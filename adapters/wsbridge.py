"""PARLORNET/1 over a WebSocket, for the modern client (/play/, docs/modern-client.md).

A browser cannot open a TCP connection, so the Play extension (adapters/playserver.py) takes a WebSocket at
/play/ws and carries PARLORNET/1 lines over it: each text message from the browser is one request line, and
each line the core sends is one text message to the browser. Behind each WebSocket is one connection of the
Play extension to the core's extension channel, so every rule, the sign-in included, stays in the core, and
the modern client is a PARLORNET/1 client like any other (docs/protocol.md, WebSocket).

What the bridge adds or holds back, and nothing else:

- It tells the core the browser's address (SRC) before anything else, for the per-address limits.
- The extension channel's own requests (SRC, PRO, PRD, a table's data TDL, TDS and TDD, the notices NFY, INT,
  and SES with a session id) are refused here: ERR NOTFRONTEND, as on the public port.
- A sign-in's OK carries the session id on the extension channel; the browser gets OK - instead, as on
  the public port, and before it an HND line: a one-time handle (60 seconds) that the page can trade for
  the Play extension's session cookie (POST /play/session). The session id itself never reaches a script.
- SES alone (no id) resumes the session in the cookie that came with the WebSocket's handshake, if any.
- Stay signed in (docs/accounts.md): the key KEY NEW gives never reaches a script either. Its OK carries
  OK - and before it an HND line, a one-time handle the page trades for the key cookie (pnplaykey). When SES
  alone finds no session to resume and that cookie came with the handshake, the bridge signs in with it
  itself (USER name, then AUT key) and answers the SES as a resume would, with an HND for the new session;
  a key the core refuses is answered ERR NOKEY, so the page clears the cookie.

The handshake (RFC 6455) and the framing are written here, with limits: one message is at most 125 bytes
(a line is at most 78 characters), printable ASCII; frames from the browser must be masked; fragments,
binary frames, extensions and oversized frames close the connection with the code the RFC gives. The
handshake's Origin must be one the server accepts (origin_ok). A ping goes to the browser every PING_SECS,
and a browser silent for DEAD_SECS is closed; one that has not signed in after SIGNIN_SECS is closed too.
The number of WebSockets is bounded (a Gate, adapters/common.py): those still signing in, per address and
in all, and those signed in.

Pure Python, no dependencies."""
import asyncio
import base64
import hashlib
import re
import secrets
import time

from .common import CoreError, Gate

WS_PATH = "/play/ws"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SUBPROTOCOL = "parlornet1"

MAX_MESSAGE = 125           # bytes in one message from the browser (one frame's short length)
MAX_LINE = 78               # characters in one request line (80 bytes with its CR LF)
PING_SECS = 30.0            # a ping to the browser this often
DEAD_SECS = 75.0            # nothing at all from the browser this long: closed
SIGNIN_SECS = 600.0         # to sign in after connecting (reading a mailed password included)
FRAMES_PER_SEC, FRAME_BURST = 20.0, 60      # frames from the browser, pings included
HANDOFF_SECS = 60
WS_SIGNED = 50              # WebSockets signed in, in all
WS_PENDING = 20             # ... still signing in, in all
WS_PER_ADDR = 4             # ... still signing in, from one address

# close codes (RFC 6455, 7.4.1)
NORMAL, GOING, PROTOCOL, UNSUPPORTED, BAD_DATA, POLICY, TOO_BIG, ERROR = 1000, 1001, 1002, 1003, 1007, 1008, 1009, 1011

KEY_RE = re.compile(r"^[A-Za-z0-9+/]{22}==\Z")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{22}\Z")
LINE_RE = re.compile(r"^[\x20-\x7e]*\Z")
EXT_ONLY = ("SRC", "PRO", "PRD", "TDL", "TDS", "TDD", "NFY", "INT")
SIGNIN_VERBS = ("HELO", "USER", "REG", "AUT")


class Refuse(Exception):
    """The handshake is refused: an HTTP status, its text, and extra header lines."""

    def __init__(self, status, text, extra=""):
        super().__init__(text)
        self.status, self.text, self.extra = status, text, extra


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")


def is_upgrade(headers):
    return "websocket" in headers.get("upgrade", "").lower()


def handshake(method, version, path, headers, origin_ok):
    """The response to a WebSocket handshake (bytes), or Refuse. origin_ok(origin, host) says whether the
    Origin header (lower case) names this site, given the Host header (adapters/playserver.py)."""
    if path != WS_PATH:
        raise Refuse(404, "No WebSocket here.")
    if method != "GET" or version != "HTTP/1.1":
        raise Refuse(400, "A WebSocket handshake is GET with HTTP/1.1.")
    tokens = [t.strip().lower() for t in headers.get("connection", "").split(",")]
    if not is_upgrade(headers) or "upgrade" not in tokens:
        raise Refuse(400, "Upgrade: websocket and Connection: Upgrade are needed.")
    if headers.get("sec-websocket-version", "").strip() != "13":
        raise Refuse(426, "WebSocket version 13 only.", "Sec-WebSocket-Version: 13\r\n")
    key = headers.get("sec-websocket-key", "").strip()
    if not KEY_RE.match(key) or len(base64.b64decode(key)) != 16:
        raise Refuse(400, "Sec-WebSocket-Key is not 16 bytes in base64.")
    host = headers.get("host", "").strip().lower()
    origin = headers.get("origin", "").strip().lower()
    if not origin or not origin_ok(origin, host):
        raise Refuse(403, "This WebSocket is for pages of this site only.")
    offered = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    proto = "Sec-WebSocket-Protocol: %s\r\n" % SUBPROTOCOL if SUBPROTOCOL in offered else ""
    return ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n%s\r\n" % (accept_key(key), proto)).encode("ascii")


def frame(opcode, payload=b""):
    """A frame from the server: final, unmasked."""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 127]) + n.to_bytes(8, "big")
    return head + payload


def close_frame(code, reason=""):
    return frame(8, code.to_bytes(2, "big") + reason.encode("ascii")[:120])


class Closed(Exception):
    """The WebSocket is over: the close code to send (None: none, the browser closed or went away)."""

    def __init__(self, code=None, reason=""):
        super().__init__(reason)
        self.code, self.reason = code, reason


class FrameReader:
    """Messages from the browser, one at a time, with the limits above."""

    def __init__(self, reader):
        self.r = reader
        self.tokens, self.stamp = FRAME_BURST, time.monotonic()
        self.last = time.monotonic()        # when the browser last sent anything

    async def exact(self, n):
        try:
            return await self.r.readexactly(n)
        except (asyncio.IncompleteReadError, ConnectionError):
            raise Closed(None)

    def allow(self):
        now = time.monotonic()
        self.tokens = min(FRAME_BURST, self.tokens + (now - self.stamp) * FRAMES_PER_SEC)
        self.stamp = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    async def frame(self):
        """-> (fin, opcode, payload)."""
        b0, b1 = await self.exact(2)
        self.last = time.monotonic()
        if not self.allow():
            raise Closed(POLICY, "too many frames")
        if b0 & 0x70:
            raise Closed(PROTOCOL, "no extensions")
        if not b1 & 0x80:
            raise Closed(PROTOCOL, "frames from a client are masked")
        n = b1 & 0x7F
        if n > MAX_MESSAGE:                 # 126 and 127: a longer frame than any line
            raise Closed(TOO_BIG, "a message is at most %d bytes" % MAX_MESSAGE)
        mask = await self.exact(4)
        data = await self.exact(n)
        return bool(b0 & 0x80), b0 & 0x0F, bytes(c ^ mask[i & 3] for i, c in enumerate(data))

    async def message(self, pong):
        """The next text message, as a str; pings are answered with pong(payload) on the way."""
        parts, size = [], 0
        while True:
            fin, op, data = await self.frame()
            if op >= 8:                     # control frames: whole, short, and they may come between fragments
                if not fin:
                    raise Closed(PROTOCOL, "control frames are not fragmented")
                if op == 8:                 # the browser closes: answered with a close of our own
                    raise Closed(NORMAL, "")
                if op == 9:
                    await pong(data)
                elif op != 10:
                    raise Closed(PROTOCOL, "unknown control frame")
                continue
            if op == 2:
                raise Closed(UNSUPPORTED, "text only")
            if op not in (0, 1) or (op == 0) != bool(parts):
                raise Closed(PROTOCOL, "unexpected continuation")
            parts.append(data)
            size += len(data)
            if size > MAX_MESSAGE:
                raise Closed(TOO_BIG, "a message is at most %d bytes" % MAX_MESSAGE)
            if fin:
                break
        raw = b"".join(parts)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Closed(BAD_DATA, "not UTF-8")
        return text


class Bridge:
    """The WebSockets of one Play extension. server: the Play object (its core connections and the
    sessions it has seen resume, adapters/playserver.py)."""

    def __init__(self, server):
        self.server = server
        self.gate = Gate(WS_SIGNED, WS_PENDING, WS_PER_ADDR)
        self.gate.notice = b""
        self.handoffs = {}          # one-time handle -> (kind, value, until): kind "sid" or "key" (name.key)
        self.conns = set()          # the open WebSockets (Conn)

    def in_use(self, name, secs):
        """Whether a WebSocket signed in as name sent a line in the last secs seconds: the player has Play
        open and in view (the page asks every second at a table, every few in the lobby, only while it is
        visible), so a push notification would only repeat what the page shows."""
        now, key = time.monotonic(), name.lower()
        return any(c.name == key and now - c.last < secs for c in self.conns)

    def handoff(self, value, kind="sid"):
        now = time.time()
        for h, (_, _, until) in list(self.handoffs.items()):
            if until < now:
                del self.handoffs[h]
        h = secrets.token_urlsafe(16)
        self.handoffs[h] = (kind, value, now + HANDOFF_SECS)
        return h

    def take(self, h):
        """(kind, value) for a handle, once, or None: ("sid", a session id) or ("key", name.key)."""
        entry = self.handoffs.pop(h, None)
        return entry[:2] if entry and entry[2] >= time.time() else None

    async def serve(self, reader, writer, req):
        """A WebSocket after its 101: req is the handshake (headers, peer, src, the cookie's session id)."""
        ticket = self.gate.admit(req["peer"], writer)
        out = Sender(writer)
        if ticket is None:
            await out.text("ERR BUSY server is full")
            await out.close(POLICY, "busy")
            return
        core = None
        tasks = []
        try:
            try:
                core = await self.server.connect(req)
            except (OSError, asyncio.TimeoutError, CoreError):
                await out.text("ERR BUSY the game server isn't answering")
                await out.close(ERROR, "no core")
                return
            await out.text(core.greeting)
            conn = Conn(self, core, out, req, ticket)
            self.conns.add(conn)
            frames = FrameReader(reader)
            tasks = [asyncio.ensure_future(conn.from_browser(frames)), asyncio.ensure_future(conn.from_core()),
                     asyncio.ensure_future(conn.keepalive(frames))]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            code, reason = NORMAL, ""
            for t in done:
                e = t.exception() if not t.cancelled() else None
                if isinstance(e, Closed):
                    code, reason = e.code, e.reason
                elif e is not None and not isinstance(e, (ConnectionError, OSError, CoreError, asyncio.TimeoutError)):
                    code, reason = ERROR, "error"
            if code is not None:
                await out.close(code, reason)
        finally:
            self.conns = {c for c in self.conns if c.core is not core}
            for t in tasks:
                t.cancel()
            ticket.release()
            if core is not None:
                core.close()
            try:
                writer.close()
            except Exception:
                pass


class Sender:
    def __init__(self, writer):
        self.w = writer
        self.closed = False

    async def send(self, data):
        if self.closed:
            return
        self.w.write(data)
        await asyncio.wait_for(self.w.drain(), 10)

    async def text(self, line):
        await self.send(frame(1, line.encode("ascii", "replace")))

    async def close(self, code, reason=""):
        if not self.closed:
            try:
                await self.send(close_frame(code, reason))
            except Exception:
                pass
            self.closed = True


class Conn:
    """One WebSocket and its core connection: requests one way, replies the other, in order."""

    def __init__(self, bridge, core, out, req, ticket):
        self.bridge, self.core, self.out, self.req, self.ticket = bridge, core, out, req, ticket
        self.pending = []           # the verbs of the requests sent and not yet answered, in order
        self.born = time.monotonic()
        self.signed = False
        self.name = None            # the player signed in here (lower case), for Bridge.in_use
        self.asked = None           # the name in the last USER, HELO or REG
        self.asked_as = None        # the same name as typed, for the key cookie
        self.key_tried = False      # SES alone tried the Stay signed in cookie's key already
        self.last = 0.0             # when the browser last sent a line
        self.lock = asyncio.Lock()  # writes to the core: one line at a time

    async def to_core(self, line):
        async with self.lock:
            self.core.w.write((line + "\r\n").encode("ascii"))
            await self.core.w.drain()

    async def from_browser(self, frames):
        while True:
            line = await frames.message(lambda data: self.out.send(frame(10, data)))
            if not LINE_RE.match(line):
                raise Closed(BAD_DATA, "lines are printable ASCII")
            if len(line) > MAX_LINE:
                await self.out.text("ERR TOOLONG lines are 80 bytes at most")
                raise Closed(TOO_BIG, "line too long")
            if not line.strip():
                continue
            self.ticket.touch()
            self.last = time.monotonic()
            words = line.split()
            verb = words[0].upper()
            if verb in ("USER", "HELO", "REG") and len(words) > 1:
                self.asked = words[1].lower()
                self.asked_as = words[1]
            if verb in EXT_ONLY or (verb == "SES" and len(words) > 1):
                await self.out.text("ERR NOTFRONTEND %s is not for this connection" % verb)
                continue
            if verb == "SES":
                await self.resume()
                continue
            if verb == "KEY" and len(words) > 1 and words[1].upper() == "NEW":
                verb = "KEYNEW"             # its key goes to the cookie by a handle, never to the page
            self.pending.append(verb)
            await self.to_core(line)

    async def resume(self):
        """SES alone: resume the session of the cookie the handshake carried (the Play extension's own), or,
        without one, sign in with the Stay signed in cookie's key."""
        sid, addr = self.req.get("sid"), self.req["src"]
        if not sid or not self.bridge.server.resume_allowed(addr, sid):
            if not await self.key_signin():
                await self.out.text("ERR NOSESSION no session to resume")
            return
        self.pending.append("SES")
        await self.to_core("SES %s" % sid)

    async def key_signin(self):
        """Sign in with the key cookie (once a WebSocket): USER name now, AUT key when the core asks for the
        password (from_core). False when there is no key cookie or it was tried."""
        got = self.req.get("key")
        if not got or self.key_tried:
            return False
        self.key_tried = True
        self.asked, self.asked_as = got[0].lower(), got[0]
        self.pending.append("KEYUSER")
        await self.to_core("USER %s" % got[0])
        return True

    async def from_core(self):
        while True:
            raw = await self.core.r.readline()
            if not raw:
                raise Closed(GOING, "the game server closed the connection")
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            final = line.startswith(("OK", "ERR", "ASK"))
            if self.pending and self.pending[0] in ("KEYUSER", "KEYAUT"):
                if not final:
                    continue                # the sign-in with the key is the bridge's own: the page sees none of it
                await self.key_step(self.pending.pop(0), line)
                continue
            verb = self.pending.pop(0) if final and self.pending else None
            if final and verb == "KEYNEW" and line.startswith("OK"):
                f = line.split()
                if len(f) == 2 and SID_RE.match(f[1]) and self.name:
                    name = self.asked_as or self.name
                    await self.out.text("HND %s" % self.bridge.handoff("%s.%s" % (name, f[1]), "key"))
                    line = "OK -"
            if final and verb == "SES" and line.startswith("ERR") and await self.key_signin():
                self.bridge.server.note_failed_resume(self.req["src"], self.req.get("sid"))
                continue
            if final and line.startswith("OK"):
                f = line.split()
                if verb in SIGNIN_VERBS and len(f) == 2 and SID_RE.match(f[1]):
                    self.name = self.asked
                    await self.signed_in(f[1])
                    line = "OK -"
                elif verb == "SES" and len(f) == 2:
                    self.name = f[1].lower()
                    self.bridge.server.note_resumed(self.req.get("sid"))
                    if not self.admit():
                        await self.out.text("ERR BUSY server is full")
                        raise Closed(POLICY, "busy")
                elif verb == "OUT":
                    self.signed, self.name = False, None
            elif final and verb == "SES" and line.startswith("ERR"):
                self.bridge.server.note_failed_resume(self.req["src"], self.req.get("sid"))
            await self.out.text(line)

    async def key_step(self, verb, line):
        """The bridge's own sign-in with the key cookie, answering the page's SES: the core's question for the
        password gets the key (AUT key). Its OK becomes a resume's OK name, with the new session's HND first. A
        refusal becomes the page's ERR, which is NOKEY for the key itself, so the page clears the cookie."""
        if verb == "KEYUSER" and line.startswith("ASK password"):
            self.pending.append("KEYAUT")
            await self.to_core("AUT key %s" % self.req["key"][1])
            return
        f = line.split()
        if line.startswith("OK") and len(f) == 2 and SID_RE.match(f[1]):
            self.name = self.asked
            await self.signed_in(f[1])
            self.bridge.server.note_resumed(f[1])
            await self.out.text("OK %s" % self.asked_as)
            return
        if line.startswith("ERR NOKEY"):
            await self.out.text(line)
            return
        await self.out.text("ERR NOSESSION no session to resume")

    async def signed_in(self, sid):
        if not self.admit():
            await self.out.text("ERR BUSY server is full")
            raise Closed(POLICY, "busy")
        await self.out.text("HND %s" % self.bridge.handoff(sid))

    def admit(self):
        self.signed = True
        if self.ticket.sign_in():
            return True
        return False

    async def keepalive(self, frames, tick=5.0):
        pinged = time.monotonic()
        while True:
            await asyncio.sleep(tick)
            now = time.monotonic()
            if now - frames.last > DEAD_SECS:
                raise Closed(GOING, "no answer")
            if not self.signed and now - self.born > SIGNIN_SECS:
                await self.out.text("ERR IDLE sign in first")
                raise Closed(POLICY, "sign in first")
            if now - pinged >= PING_SECS:
                pinged = now
                await self.out.send(frame(9, b"pn"))
