"""Shared pieces for server-side adapters: a client of the native protocol
over the frontend channel (docs/security-design.md), the sign-in helper for
line-based game-network adapters, and the gate that counts an adapter's
connections before and after sign-in."""
import asyncio
import ipaddress
import os
import re
import socket
import ssl
import time

from parlord.auth import NEWPASS_TEXT
from parlord.frontends import client_context, read_key


class CoreError(Exception):
    """A refusal or a failure; code is the core's ERR code (NOPLAYER, NAMEINUSE, ...) when it sent one."""

    def __init__(self, text="", code=None):
        super().__init__(text)
        self.code = code


class Refused(ValueError):
    """What a player typed or filled in makes no move: the reason, in the player's own words, to show them
    (a Grid Words word started on a played square)."""


BAD_CHARS = re.compile(r"[\x00-\x1f\x7f]")


# The message of the day as the text frontends show it: between a top and a bottom rule, 39 columns wide
# like the message itself, so it is clear where it ends and the server's own instructions begin.
MOTD_TOP = "========= Message of the Day =========="
MOTD_BOTTOM = "==== End of the Message of the Day ===="


def framed_motd(lines):
    """The message of the day between its two rules, or nothing when there is none."""
    return [MOTD_TOP] + list(lines) + [MOTD_BOTTOM] if lines else []


CLAIM_LEN = 40      # CLI PROG and CLI MACH (docs/protocol.md, Clients and machines)


def claim_text(text, most=CLAIM_LEN):
    """A client program's or a machine's name as CLI sends it: printable ASCII only, runs of spaces made
    one, trimmed, at most most characters (the core cleans it the same way)."""
    text = " ".join("".join(c if " " <= c <= "~" else " " for c in str(text or "")).split())
    return text[:most].rstrip()


def clean(value):
    """True if a value may go into a request line: no CR, LF or other control
    character (a line break inside would be a second request to the core)."""
    return not BAD_CHARS.search(value)


# The telnet extension's movement-key setting, kept with PRF (CoreClient.load_pref and save_pref):
#     keys = await core.load_pref(MOVE_KEYS_PREF, MOVE_KEYS, MOVE_KEYS[0])    # at sign-in
#     await core.save_pref(MOVE_KEYS_PREF, keys)                              # when the player changes it
MOVE_KEYS_PREF = "movekeys"
MOVE_KEYS = ("arrows", "ijkl", "esdf", "wasd", "hjkl")

# Which side a Backgammon player's home board is drawn on (points 1-6 at the bottom right, or the mirror image
# with them at the bottom left), kept with PRF by the telnet and web extensions (Play shares the web's).
# Only the drawing changes: the moves and point numbers stay the same. A missing value is "right".
BG_HOME_PREF = "bghome"
BG_HOMES = ("right", "left")


# ---- the gate: an adapter's connections, before and after sign-in -----------------------

ACT_EVERY = float(os.environ.get("PARLOR_ACT_EVERY", "30"))     # a player's typing is reported to the core
                                                                # (ACT) at most this often (tests set less)
LOGIN_SECS = 120.0          # to send the first (login) line after connecting
LOGIN_TOTAL_SECS = 600.0    # to finish signing in (reading a mailed password included) after connecting
PENDING_MAX = 20            # connections that have not signed in yet, per adapter (at least; see Gate)
PENDING_PER_ADDR = 4        # ... from one client address (IPv6: one /48)


def peer_addr(writer):
    peer = writer.get_extra_info("peername") if writer is not None else None
    return peer[0] if peer else ""


KEEPALIVE = (60, 30, 4)     # TCP keepalive on a player's connection: idle seconds, seconds between probes, probes


def keepalive(writer, idle=KEEPALIVE[0], interval=KEEPALIVE[1], count=KEEPALIVE[2]):
    """TCP keepalive on an accepted connection, where the platform has it: the kernel probes a peer that has
    been silent for idle seconds (the peer's own TCP answers; nothing reaches the program on either side), so a
    dead peer is found and a NAT on the way sees traffic. Every long-lived player connection calls this when
    it is accepted. -> True if it was set."""
    sock = writer.get_extra_info("socket") if writer is not None else None
    if sock is None:
        return False
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        idle_opt = getattr(socket, "TCP_KEEPIDLE", None) or getattr(socket, "TCP_KEEPALIVE", None)   # macOS: the latter
        for opt, value in ((idle_opt, idle), (getattr(socket, "TCP_KEEPINTVL", None), interval),
                           (getattr(socket, "TCP_KEEPCNT", None), count)):
            if opt is not None:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
        return True
    except OSError:
        return False


def proxy_list(text, key="proxies"):
    """A proxies setting (comma-separated addresses or networks) -> [ip_network]. ValueError if one is not."""
    out = []
    for x in (text or "").split(","):
        if x.strip():
            try:
                out.append(ipaddress.ip_network(x.strip(), strict=False))
            except ValueError:
                raise ValueError("%s is a comma-separated list of addresses or networks: %r" % (key, x.strip()))
    return out


def as_ip(text):
    """An address as an ip_address (an IPv4-mapped IPv6 one as IPv4), or None."""
    try:
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def trusted_proxy(ip, proxies):
    return ip is not None and any(ip.version == n.version and ip in n for n in proxies)


def forwarded_client(peer, headers, proxies):
    """(the client's address, the scheme it came over or None) for a request from peer. From one of the
    trusted proxies: the last address of X-Forwarded-For that is not a proxy (a malformed entry stops the
    walk at the one after it), and X-Forwarded-Proto; from anywhere else, the peer and None."""
    ip = as_ip(peer)
    if not trusted_proxy(ip, proxies):
        return peer, None
    addr = str(ip)
    for x in reversed(headers.get("x-forwarded-for", "").split(",")):
        hop = as_ip(x)
        if hop is None:
            break
        addr = str(hop)
        if not trusted_proxy(hop, proxies):
            break
    proto = headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
    return addr, proto if proto in ("http", "https") else None


def addr_key(addr):
    """The key for per-address limits: as the core's (IPv6 by its /48, an
    IPv4-mapped address as IPv4), or the text itself if it is not an address."""
    from parlord.accounts import source_key
    return source_key(addr) or addr or "?"


class Gate:
    """Counts an adapter's connections: those still signing in (at most
    pending_max, which grows with max_signed, and per_addr from one address,
    IPv6 by its /48) and those signed in (at most max_signed). A newcomer over
    its address's limit closes that address's least active pending
    connection. When all pending places are taken, the newest arrival at an
    address holding more than the newcomer's would is closed; if no address
    does (every one holds one, say), the newcomer waits outside. So neither a
    few addresses holding many connections nor many holding one each can
    close a player who is already signing in.
    An adapter opens its connection to the core only for a client that has
    sent a login line, so idle connects cost the core nothing, and what the
    adapter can hold open there is bounded by these counts."""

    def __init__(self, max_signed, pending_max=None, per_addr=PENDING_PER_ADDR):
        self.max_signed, self.per_addr = max_signed, per_addr
        self.pending_max = pending_max or max(PENDING_MAX, max_signed)
        self.signed = 0
        self.pending = {}           # address key -> [Ticket, ...] still signing in
        self.notice = b"\r\nServer is full, try again soon.\r\n"     # to a connection closed for a newcomer

    def admit(self, addr, writer=None):
        """A ticket for a new connection, or None: the signed-in places are
        full, or the pending ones are and no address holds more than the
        newcomer's would (then the newcomer, the newest arrival, waits outside,
        and nobody already waiting is closed for it)."""
        if self.signed >= self.max_signed:
            return None
        key = addr_key(addr)
        mine = len(self.pending.get(key, ()))
        if mine >= self.per_addr:
            self.close(min(self.pending[key], key=lambda t: t.last))       # its own least active
        elif sum(len(v) for v in self.pending.values()) >= self.pending_max:
            most = max(len(v) for v in self.pending.values())
            if most <= mine + 1:
                return None
            # the newest arrival among the addresses holding the most
            self.close(max((t for v in self.pending.values() if len(v) == most for t in v), key=lambda t: t.born))
        t = Ticket(self, key, writer)
        self.pending.setdefault(key, []).append(t)
        return t

    def close(self, t):
        t._unpend()
        t.state = "gone"
        if t.writer is not None:
            try:
                if self.notice:
                    t.writer.write(self.notice)
                t.writer.transport.abort()
            except Exception:
                pass


class Ticket:
    def __init__(self, gate, addr, writer=None):
        self.gate, self.addr, self.writer, self.state = gate, addr, writer, "pending"
        self.born = self.last = time.monotonic()

    def touch(self):
        """The client sent something during its login (it is not idle)."""
        self.last = time.monotonic()

    def _unpend(self):
        q = self.gate.pending.get(self.addr, [])
        if self in q:
            q.remove(self)
        if not q:
            self.gate.pending.pop(self.addr, None)

    def sign_in(self):
        """The connection signed in: True if there is room for it among the signed-in ones."""
        if self.state != "pending":
            return self.state == "signed"
        if self.gate.signed >= self.gate.max_signed:
            return False
        self._unpend()
        self.gate.signed += 1
        self.state = "signed"
        return True

    def release(self):
        if self.state == "pending":
            self._unpend()
        elif self.state == "signed":
            self.gate.signed -= 1
        self.state = "gone"


class NeedAuth(CoreError):
    """The server asks for a value during sign-in, sign-up or a reset (INF lines
    to show first, then ASK field label [secret] [yn] [cred] [new])."""

    def __init__(self, field, label, secret, info=(), yn=False, cred=False, new=False):
        super().__init__("needs %s" % label)
        self.field, self.label, self.secret = field, label, secret
        self.info, self.yn, self.cred, self.new = list(info), yn, cred, new


class CoreClient:
    """One connection to parlord's frontend channel speaking protocol.md: TLS
    1.3 with this frontend's pre-shared key (key_file, from --key or
    PARLOR_FRONTEND_KEY). An adapter is just a client of the core, as
    architecture.md says. After sign-in it holds the session id (sid) the
    core issued; it never keeps a credential. It signs in with the name alone
    and answers only what the core asks (docs/accounts.md)."""

    key_file = os.environ.get("PARLOR_FRONTEND_KEY")

    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.greeting = ""
        self.sid = None          # the session handle: never shown to a player, never logged
        self.name = None
        self.auth, self.label, self.signup = "password", "Password", "password"   # from the greeting
        self.input_at = self.act_at = 0.0     # the player's last input; the last ACT sent for it
        self.act_ok = True                     # False on a core without ACT

    @classmethod
    async def connect(cls, host, port, key_file=None):
        path = key_file or cls.key_file
        if not path:
            raise CoreError("this frontend has no key for the core (--key or PARLOR_FRONTEND_KEY)")
        try:
            ident, _, key = read_key(path)
        except (OSError, ValueError) as e:
            raise CoreError("cannot read the frontend key %s: %s" % (path, e))
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(
                host, port, ssl=client_context(ident, key), server_hostname="parlord", limit=4096), 10)
        except ConnectionRefusedError:
            raise
        except (ssl.SSLError, ConnectionError, asyncio.IncompleteReadError) as e:
            raise CoreError("the core refused this frontend's key (%s)" % (getattr(e, "reason", None)
                                                                         or type(e).__name__))
        so = w.get_extra_info("ssl_object")
        if so is None or so.version() != "TLSv1.3" or not so.session_reused:
            w.close()
            raise CoreError("the frontend channel is not TLS 1.3 with this frontend's key")
        c = cls(r, w)
        c.greeting = await c.readline()
        for f in c.greeting.split()[2:]:
            k, _, v = f.partition("=")
            if k == "AUTH":
                c.auth = v
                c.label = {"none": "", "password": "Password"}.get(v, v)
            elif k == "SIGNUP":
                c.signup = v
            elif k == "LABEL":
                c.label = v.replace("_", " ")
        return c

    async def readline(self):
        line = await asyncio.wait_for(self.r.readline(), 35)
        if not line:
            raise CoreError("the game server closed the connection")
        return line.decode("ascii", "replace").rstrip("\r\n")

    def user_input(self):
        """The player typed or clicked something (the adapter calls this for every command): the next
        request reports it to the core first (ACT, at most every ACT_EVERY seconds), so a player who only
        uses this frontend's own commands (a chat, who) still counts as online. What the adapter does on
        its own (its polls) never calls this."""
        self.input_at = time.monotonic()

    async def req(self, text):
        """Send one request; returns (data_lines, final_line). A request with a
        control character in it (CR or LF from a form field or a login packet,
        which would make a second request) is refused here, for every adapter.
        Input the player made since the last ACT is reported first (user_input)."""
        if (self.act_ok and self.sid and self.input_at > self.act_at and text != "ACT"
                and time.monotonic() - self.act_at >= ACT_EVERY):
            self.act_at = time.monotonic()
            _, final = await self._req("ACT")
            if final.startswith("ERR UNKNOWN"):
                self.act_ok = False
        return await self._req(text)

    async def _req(self, text):
        if not clean(text):
            raise CoreError("that has characters that cannot be used")
        self.w.write((text + "\r\n").encode("ascii", "replace"))
        await self.w.drain()
        out = []
        while True:
            line = await self.readline()
            if line.startswith("OK") or line.startswith("ERR") or line.startswith("ASK"):
                return out, line
            out.append(line)

    def _signed(self, name, final, data=()):
        if final.startswith("ASK"):
            f = final.split()
            info = [l[4:] if l.startswith("INF ") else "" for l in data if l == "INF" or l.startswith("INF ")]
            raise NeedAuth(f[1], f[2].replace("_", " ") if len(f) > 2 else f[1], "secret" in f[3:], info,
                           "yn" in f[3:], "cred" in f[3:], "new" in f[3:])
        if not final.startswith("OK"):
            if final.startswith("ERR"):
                raise CoreError(final[4:].split(" ", 1)[-1], (final.split() + [""])[1])
            raise CoreError(final)
        f = final.split()
        if len(f) < 2 and self.sid:         # the end of a flow while signed in (a password change)
            return self.sid
        if len(f) < 2:
            raise CoreError("the core sent no session")
        self.name = name
        self.sid = f[1]
        return self.sid

    async def helo(self, name):
        """Sign in with the name alone (a frontend never sends a credential the
        core has not asked for); returns the session id. Raises NeedAuth when
        the server asks for a value: answer with aut()."""
        self.name = name
        data, final = await self.req("HELO %s" % name)
        return self._signed(name, final, data)

    async def user(self, name):
        """Sign in only (USER): like helo(), but a name with no account is refused (CoreError, code
        NOPLAYER) instead of starting a sign-up."""
        self.name = name
        data, final = await self.req("USER %s" % name)
        return self._signed(name, final, data)

    async def reg(self, name):
        """Sign up only (REG): the server's sign-up steps for a new name (NeedAuth for each); a taken name
        is refused (CoreError, code NAMEINUSE), a reserved word too (RESERVED)."""
        self.name = name
        data, final = await self.req("REG %s" % name)
        return self._signed(name, final, data)

    async def resume(self, sid):
        """SES: resume a session this frontend was issued; returns the player's name."""
        data, final = await self.req("SES %s" % sid)
        if not final.startswith("OK") or len(final.split()) < 2:
            raise CoreError(final[4:].split(" ", 1)[-1] if final.startswith("ERR") else final)
        self.name, self.sid = final.split()[1], sid
        return self.name

    async def out(self, everywhere=False):
        """OUT (this session) or OUT all."""
        data, final = await self.req("OUT all" if everywhere else "OUT")
        self.sid = None
        return final.startswith("OK")

    async def finish(self):
        """End this connection's session (if any) and close: for frontends that
        hold one connection per player (telnet, IOS, FIBS, Outland)."""
        if self.sid:
            try:
                await asyncio.wait_for(self.out(), 5)
            except (CoreError, OSError, asyncio.TimeoutError):
                pass
        self.close()

    async def source(self, addr):
        """SRC: the client's address, for the core's per-address limits on
        sign-up and mail (sent once, before HELO; ignored if not an address)."""
        if addr:
            await self.req("SRC %s" % addr)

    async def claim(self, prog, mach=""):
        """CLI: tell the core which client program (and version) and which machine the player this
        connection serves is using, so their turns are recorded with them (docs/protocol.md, Clients and
        machines). Each is cleaned here as the core cleans it (claim_text); an empty one is not sent. Never
        raises for a refusal: a core older than CLI answers ERR UNKNOWN, and play goes on without it."""
        for field, text in (("PROG", claim_text(prog)), ("MACH", claim_text(mach))):
            if text:
                try:
                    await self.req("CLI %s %s" % (field, text))
                except CoreError:
                    pass

    async def motd(self):
        """The message of the day as a list of lines ([] if none)."""
        data, final = await self.req("MOTD")
        return [l[5:] for l in data if l.startswith("MOTD")] if final.startswith("OK") else []

    async def aut(self, field, value):
        data, final = await self.req("AUT %s %s" % (field, value))
        return self._signed(self.name, final, data)

    async def pwd(self):
        """Start a password change (sign-in password): raises NeedAuth for each
        step (answer with aut()); returns when it is done."""
        data, final = await self.req("PWD")
        return self._signed(self.name, final, data)

    async def can(self, kinds):
        """Tell the core which sign-in step kinds this frontend can show (text,
        secret, yn, info); it refuses a step outside them with ERR CANNOT."""
        data, final = await self.req("CAN %s" % ",".join(sorted(kinds)))
        return final.startswith("OK")

    # ---- a little data per player for this server extension (PRF; docs/protocol.md) -------------
    # The core keeps it under this extension's key name for the player signed in on this connection,
    # and only that. Keys are 1 to 12 of A-Z a-z 0-9 _ - (case-insensitive), values 1 to 56 printable
    # ASCII characters; at most 16 keys and 1024 bytes per player. Never a secret: no credential, code
    # or session id goes in here (docs/security-design.md).

    async def prefs(self):
        """Every key this extension keeps for the signed-in player: {key: value}. CoreError if refused."""
        data, final = await self.req("PRF LST")
        if not final.startswith("OK"):
            raise CoreError(err_text(final), (final.split() + [""])[1])
        return dict(l[4:].split(" ", 1) for l in data if l.startswith("PRF ") and " " in l[4:])

    async def pref_get(self, key):
        """One key's value, or None if it is not set. CoreError if refused."""
        data, final = await self.req("PRF GET %s" % key)
        if not final.startswith("OK"):
            raise CoreError(err_text(final), (final.split() + [""])[1])
        got = [l.split(" ", 2) for l in data if l.startswith("PRF ")]
        return got[0][2] if got and len(got[0]) == 3 else None

    async def pref_set(self, key, value):
        """Keep a value (CoreError with code BADKEY, BADVALUE or LIMIT if refused)."""
        data, final = await self.req("PRF SET %s %s" % (key, value))
        if not final.startswith("OK"):
            raise CoreError(err_text(final), (final.split() + [""])[1])

    async def pref_del(self, key):
        data, final = await self.req("PRF DEL %s" % key)
        if not final.startswith("OK"):
            raise CoreError(err_text(final), (final.split() + [""])[1])

    async def load_pref(self, key, choices, default):
        """For a setting read at sign-in: the kept value if it is one of choices (compared in lower
        case), else default. Never raises for a refusal or an older core without PRF, so a sign-in
        never fails over a preference."""
        try:
            value = await self.pref_get(key)
        except CoreError:
            return default
        value = (value or "").lower()
        return value if value in [c.lower() for c in choices] else default

    async def save_pref(self, key, value):
        """For a setting saved when the player changes it: True if kept, False if refused (the
        setting still applies for this connection). A lost connection is CoreError (False) as well;
        OSError and timeouts still raise."""
        try:
            await self.pref_set(key, value)
            return True
        except CoreError:
            return False

    def close(self):
        try:
            self.w.close()
        except Exception:
            pass


async def sign_in_lines(core, name, password, prompt=None, show=None, mode="any", unknown=None):
    """Sign-in, sign-up and reset for line-based game-network adapters (IOS,
    FIBS, Outland), in the client's own window before the player joins the
    lobby. The core drives the steps (docs/accounts.md):
      info  -> show(lines)             lines to display
      ask   -> prompt(label, secret)   a line typed back ("" if none came)
      yn    -> prompt("label", False)  y or n
    The name alone goes to the core. What the client already supplied (its
    password field) answers only the first question flagged cred (the
    account's password, or a password chosen at sign-up); if none comes
    (method none, an email sign-up) it is dropped, never sent, logged or kept.
    Without prompt (a client that cannot be asked anything after its login
    packet) the frontend declares only "secret", the core refuses any other
    step cleanly, and the password answers every credential question (so it
    also confirms itself at a password sign-up). A new password asked after a
    temporary one (ASK ... new, not cred) needs a prompt: without one the
    reason is NEWPASS_TEXT, to sign in on the web or by telnet first. Returns
    None when signed in, else the reason to show (without the wait= prefix).
    mode: "any" (HELO: a name with no account signs up, the convention of IOS clients), "signin" (USER:
    such a name is refused with the text unknown, or the server's; under sign-in none, where a name is
    the whole sign-in and nothing can be lost to a typing mistake, it is HELO), "signup" (REG)."""
    kinds = {"secret"}
    if prompt is not None:
        kinds |= {"text", "yn"}
    if show is not None:
        kinds.add("info")
    start = {"any": core.helo, "signin": core.user, "signup": core.reg}[mode]
    if mode == "signin" and core.auth == "none":
        start = core.helo
    try:
        await core.can(kinds)
        await start(name)
        return None
    except NeedAuth as first:
        ask = first
    except CoreError as e:
        if e.code == "NOPLAYER" and unknown:
            return unknown
        return no_wait(str(e))
    while True:
        if ask.info and show is not None:
            await show(ask.info)
        answer = None
        if ask.cred and password:
            answer = password
            if prompt is not None:
                password = None     # with prompts, the field answers the first credential question only
        elif prompt is not None:
            answer = (await prompt(ask.label, ask.secret)).strip()
        if not answer:
            if prompt is None and ask.cred:
                label = ask.label.lower()
                return "%s needs its %s%s." % (name, label, "" if label == "password" else " as the password")
            if prompt is None and ask.new:
                return NEWPASS_TEXT
            if prompt is None:
                return ("This client cannot answer the next question (%s); sign up or sign in on the web or "
                        "by telnet." % ask.label)
            return "No answer came to: %s. Log in again and type it at the prompt." % ask.label
        if len(answer) > 64:
            return "That is not a valid answer to %s." % ask.label.lower()
        try:
            await core.aut(ask.field, answer)
            return None
        except NeedAuth as again:
            ask = again
        except CoreError as e:
            return no_wait(str(e))


CHAT_WORDS = frozenset(("tell", "tellx", "say", "shout", "whisper", "kibitz", "message", "wall"))


def loggable(line, verb):
    """What of a line a signed-in player typed goes in the log: a command as
    typed, but only the verb of a chat command (tell, say, ...: what players
    say to each other is not logged), and nothing of a line that is not a
    command (it may be a password typed at the wrong moment). verb: the
    command the adapter took the line for, or None."""
    if not line.split():
        return "(empty line)"
    if verb is None:
        return "(not a command; not logged)"
    if verb in CHAT_WORDS:
        return "%s (text not logged)" % verb
    return repr(line[:80])


def no_wait(text):
    """'wait=60 Wrong code; ...' -> 'Wrong code; ...' (the wait is for programs)."""
    text = re.sub(r"^wait=\d+ ", "", text)
    return text[:1].upper() + text[1:]


def err_text(final):
    """'ERR CODE text' -> 'text'."""
    parts = final.split(" ", 2)
    return parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "error")


def more_tables(lines):
    """The n of LST's MORE n line (the tables the list left out), or 0."""
    return next((int(p[1]) for p in (l.split() for l in lines) if len(p) == 2 and p[0] == "MORE"
                 and p[1].isdigit()), 0)


def parse_games(lines):
    """GAM lines -> [{key: type/ver, rules, name, min, max}] (min and max are None if absent)."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "GAM" and len(p) >= 4:
            lo, hi = (int(p[4]), int(p[5])) if len(p) >= 6 and p[4].isdigit() and p[5].isdigit() else (None, None)
            out.append({"key": p[1], "rules": p[2], "name": p[3].replace("_", " "), "min": lo, "max": hi})
    return out


def group_games(games):
    """parse_games' list (one entry per game and ruleset) -> one entry per game, in the order first seen:
    the first ruleset's entry with "rulesets", every ruleset in GAM's order (the first is the default)."""
    out = {}
    for g in games:
        if g["key"] in out:
            out[g["key"]]["rulesets"].append(g["rules"])
        else:
            out[g["key"]] = dict(g, rulesets=[g["rules"]])
    return list(out.values())


def parse_tables(lines):
    """TAB lines -> list of dicts ("vis" is "public" or "private"). "clients": {seat: what its player plays
    with} from the CLIENT lines after a TAB line (LST clients), {} without them."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "CLIENT" and out:
            c = parse_client(l)
            if c:
                out[-1]["clients"][c[0]] = c[1]
        if p and p[0] == "TAB" and len(p) >= 7:
            filled, seats = p[4].split("/")
            seated = p[6].split(",")
            turn = next((int(f[5:]) for f in p[7:] if f.startswith("turn=") and f[5:].isdigit()), 0)
            vis = next((f[4:] for f in p[7:] if f.startswith("vis=")), "public")
            out.append({"id": p[1], "key": p[2], "rules": p[3], "filled": int(filled),
                        "seats": int(seats), "status": p[5], "seated": seated,
                        "names": [n for n in seated if n != "-"],
                        "seat_of": {n: i for i, n in enumerate(seated) if n != "-"}, "turn": turn, "vis": vis,
                        "clients": {}})
    return out


def parse_invites(lines):
    """IVT lines (the reply to IVL) -> list of dicts; "seat" is -1 when no seat is held."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "IVT" and len(p) >= 6:
            seat = next((int(f[5:]) for f in p[6:] if f.startswith("seat=") and f[5:].isdigit()), -1)
            out.append({"id": p[1], "key": p[2], "rules": p[3], "from": p[4], "status": p[5], "seat": seat})
    return out


def describe_integrity_event(text):
    """INT false why, the event of a table marked integrity=false (docs/protocol.md) -> "Integrity: why." (None
    for other events)."""
    p = text.split(" ", 2)
    if len(p) == 3 and p[0] == "INT" and p[1] == "false":
        return "Integrity: %s." % p[2].rstrip(".")
    return None


UNINVITE_TEXT = {"declined": "%s declined the invitation", "withdrawn": "%s's invitation was withdrawn",
                 "expired": "%s's invitation expired", "started": "%s's invitation ended because the game started",
                 "left": "%s's invitation ended because the player who sent it left"}


def describe_invite_event(text):
    """INVITE and UNINVITE events -> human text (None for other events). UNINVITE's last word says why
    the invitation ended (declined, withdrawn, expired, started, left); a core before that sends none."""
    p = text.split()
    if len(p) >= 3 and p[0] == "INVITE":
        return "%s is invited" % p[2]
    if len(p) >= 3 and p[0] == "UNINVITE":
        return UNINVITE_TEXT.get(p[3] if len(p) > 3 else "", "%s is no longer invited") % p[2]
    return None


def uninvite_of(text):
    """An UNINVITE event's payload -> (name, why), why "" when the core sent none; None for other events."""
    p = text.split()
    if len(p) >= 3 and p[0] == "UNINVITE":
        return p[2], p[3] if len(p) > 3 else ""
    return None


def parse_players(lines):
    """PLR lines (the reply to WHO) -> [(name, is a bot)], in the core's order."""
    return [(p[1], "bot" in p[2:]) for p in (l.split() for l in lines) if len(p) >= 2 and p[0] == "PLR"]


# ---- turns, the activity feed and profiles (TRN, FEED, PFL; docs/protocol.md) ----------------------------

def parse_turns(lines):
    """TRN lines with their TRX lines (the reply to TRN or FEED) -> [{"table", "seq", "at" (the time as sent,
    2026-11-23T14:05:09Z), "seat", "name", "key" (type/ver), "move", "prog", "mach"}], in the core's order.
    A TRX word this code does not know is skipped."""
    out = []
    for l in lines:
        p = l.split(" ", 6)
        if p[0] == "TRN" and len(p) == 7 and p[2].isdigit() and p[4].isdigit():
            out.append({"table": p[1], "seq": int(p[2]), "at": p[3], "seat": int(p[4]), "name": p[5],
                        "key": p[6].strip(), "move": "", "prog": "", "mach": ""})
        elif p[0] == "TRX" and out and len(p) >= 3 and p[1] in ("MOVE", "PROG", "MACH"):
            out[-1][p[1].lower()] = l.split(" ", 2)[2].strip()
    return out


def parse_profile(lines):
    """PFL lines (the reply to PFL name) -> {"name", "pref", "games": [(type/ver, n)], "progs": [(text, n)],
    "machs": [(text, n)], "score"}: score is parse_score's dict, or None when the server sent no score."""
    out = {"name": "", "pref": "", "games": [], "progs": [], "machs": [], "score": None}
    for l in lines:
        p = l.split(" ", 3)
        if p[0] != "PFL" or len(p) < 3:
            continue
        if p[1] == "NAME":
            out["name"] = p[2]
        elif p[1] == "PREF":
            out["pref"] = l.split(" ", 2)[2]
        elif p[1] == "GAME" and len(p) == 4 and p[3].isdigit():
            out["games"].append((p[2], int(p[3])))
        elif p[1] in ("PROG", "MACH") and len(p) == 4 and p[2].isdigit():
            out["progs" if p[1] == "PROG" else "machs"].append((p[3], int(p[2])))
        elif p[1] == "SCORE":
            out["score"] = parse_score(l.split()[2:])
    return out


# ---- scores and the leaderboard (TOP, PFL SCORE; docs/protocol.md, Scores) -------------------------------

# What every leaderboard view says, in short (docs/protocol.md, Scores).
SCORES_NOTE = ("Any score is easy to get on a public server. The leaderboard is meant for conferences, "
               "festivals and LAN parties.")
SCORE_FIELDS = ("points", "wins", "draws", "losses", "resigns")


def parse_score(words):
    """points wins draws losses resigns [rank] -> {"points", "wins", "draws", "losses", "resigns", "rank"
    (None when unranked)}, or None."""
    if len(words) < 5 or not all(w.isdigit() for w in words[:5]):
        return None
    out = dict(zip(SCORE_FIELDS, (int(w) for w in words[:5])))
    out["rank"] = int(words[5]) if len(words) > 5 and words[5].isdigit() else None
    return out


def parse_top(lines):
    """TOP lines (the reply to TOP) -> [{"rank", "name", "points", "wins", "draws", "losses", "resigns"}]."""
    out = []
    for l in lines:
        p = l.split()
        if len(p) == 8 and p[0] == "TOP" and p[1].isdigit():
            row = parse_score(p[3:])
            if row:
                row.update(rank=int(p[1]), name=p[2])
                out.append(row)
    return out


def score_text(s):
    """A score in words: "12 points: 3 wins, 1 draw, 2 losses, 1 resigned"."""
    def n(k, one, many):
        return "%d %s" % (s[k], one if s[k] == 1 else many)
    return "%s: %s, %s, %s, %d resigned" % (n("points", "point", "points"), n("wins", "win", "wins"),
                                            n("draws", "draw", "draws"), n("losses", "loss", "losses"),
                                            s["resigns"])


# Programs whose names read with "the" before them ("with the Outland Gateway"), and machine names that
# are systems rather than machines, which take no article ("on macOS", but "on a Macintosh").
THE_PROGRAMS = ("Outland Gateway", "ParlorNet bot")
SYSTEMS = ("macos", "mac os", "windows", "linux", "android", "chromeos", "ios", "ipados", "unix", "sunos", "os/2",
           "beos", "freebsd", "openbsd", "netbsd")


def with_program(prog):
    """'with the Outland Gateway', 'with Netscape Navigator 3.0'; "" for no program."""
    if not prog:
        return ""
    return "with %s%s" % ("the " if prog.startswith(THE_PROGRAMS) else "", prog)


def on_machine(mach):
    """'on a Macintosh', 'on an Apple IIe (enhanced)', 'on macOS'; "" for no machine."""
    if not mach:
        return ""
    if mach.lower().startswith(SYSTEMS):
        return "on " + mach
    return "on %s %s" % ("an" if mach[:1].lower() in "aeiou" else "a", mach)


def turn_sentence(turn, game_name):
    """A turn from parse_turns as a sentence, without markup: 'eric played FRAULEIN for 65 in Grid Words with
    BobGrid 2.3 on a Sun SPARCstation.' A turn the game says nothing about is 'played a turn'."""
    words = [turn["name"], turn["move"] or "played a turn", "in", game_name, with_program(turn["prog"]),
             on_machine(turn["mach"])]
    return " ".join(w for w in words if w) + "."


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def turn_time(at):
    """A turn's time as TRN sends it (2026-11-23T14:05:09Z) -> 'Nov 23, 14:05 UTC'; as it came if it is not
    in that form."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)", at or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        return at or ""
    return "%s %d, %s:%s UTC" % (MONTHS[int(m.group(2)) - 1], int(m.group(3)), m.group(4), m.group(5))


# ---- what each player plays with (CLIENT; docs/protocol.md, Clients and machines) -----------------------

def parse_client(text):
    """'CLIENT seat text' (an EVT payload, or a line of VUE C or LST clients) -> (seat, text), text being
    what the seat's player plays with in words ("with GameMaster 1.0.0 on a Macintosh Plus"); None for any
    other line, and for a CLIENT with nothing to say (a private table's, for someone who may not see it)."""
    p = text.split(" ", 2)
    if len(p) == 3 and p[0] == "CLIENT" and p[1].isdigit() and p[2].strip():
        return int(p[1]), p[2].strip()
    return None


def parse_clients(lines):
    """The CLIENT lines of a view (VUE C) -> {seat: text}."""
    return dict(c for c in (parse_client(l) for l in lines) if c)


def fit(text, width):
    """text cut at a word to at most width characters (cut inside a word only when one word is too long)."""
    if len(text) <= width:
        return text
    cut = text[:width + 1].rsplit(" ", 1)[0] if " " in text[:width + 1] else text[:width]
    return cut[:width].rstrip()


CUT_ENDS = ("with", "the", "on", "a", "an", "+", "-", "and", "&", "/")


def client_sentence(name, say, verb="sat down", width=0):
    """A join told with the program: 'ann sat down with GameMaster 1.0.0 on a Macintosh Plus.'; without one
    (say ""), 'ann sat down.' With width, the words of say are cut so the sentence fits."""
    head = "%s %s" % (name, verb)
    if not say:
        return head + "."
    if width and len(head) + len(say) + 2 > width:
        words = fit(say, width - len(head) - 2).split()
        while words and words[-1].lower() in CUT_ENDS:     # no "with GameMaster 1.0.0 +." or "... on a."
            words.pop()
        if len(words) < 2:              # not even "with X": leave it out
            return head + "."
        say = " ".join(words)
    return "%s %s." % (head, say)


def client_change_sentence(name, say, width=0):
    """A seat's program changed mid-game: 'ann now plays with RSCARDS 5.02 on an IBM PC.'"""
    return client_sentence(name, say, "now plays", width)


def pending_invites(events):
    """EVT payloads in order -> names invited and not yet seated or withdrawn."""
    out = []
    for text in events:
        p = text.split()
        if len(p) >= 3 and p[0] == "INVITE" and p[2] not in out:
            out.append(p[2])
        elif len(p) >= 3 and p[0] in ("UNINVITE", "JOIN") and p[2] in out:
            out.remove(p[2])
    return out


# The view parsers and event texts of each game live with the rest of its frontend code in adapters/games/
# (docs/games-howto.md). These names stay for the server extensions that speak one game's protocol (IOS,
# FIBS, Outland); they import the game's module only when first called.

def parse_reversi_view(lines):
    """VUE lines for reversi/1 -> dict (adapters/games/reversi.py)."""
    from .games.reversi import parse_view
    return parse_view(lines)


def parse_backgammon_view(lines):
    """VUE lines for backgammon/1 -> dict (adapters/games/backgammon.py)."""
    from .games.backgammon import parse_view
    return parse_view(lines)


def describe_bg_event(text, names):
    """Backgammon events -> human text, None if not a backgammon event (adapters/games/backgammon.py)."""
    from .games.backgammon import describe_event
    return describe_event(text, names)
