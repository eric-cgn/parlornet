"""The dial-in extension takes modem calls from dial-in gateways, for ParlorNet's own clients and terminals.

A dial-in gateway answers calls from modems and carries each call here as a TCP connection, on port 6521
(docs/protocol.md, Dial-in gateways). It may start the connection with a PROXY protocol header, version 2,
that says what the call is. The extension reads the header with the core's own code (parlord/native.py) and
believes it only from the addresses in the setting dialin.gateways. The core holds that setting, so the
extension asks the core (DIA addr) as soon as a header has come. A header from any other address, or one that
is not well formed, goes to the core at once as a native call would (below), and the core refuses it with ERR
PROTOCOL and closes the line, as it does on the native port.

Then the extension waits. For WINDOW_SECS (15 seconds) from the connection it sends nothing and echoes
nothing. The window is long enough for a caller who sets up the line by hand. A ParlorNet client sends
PARLORNET/1 and its line ending as soon as the modems connect. The first CR or LF decides where the call goes.
When the line before it is PARLORNET/1, with at most 10 bytes of anything before it as on the native port, the
call goes to the native protocol at once. Any other line goes to telnet at once, and so does an empty one, so
a caller who presses Enter sees the title page right away. When no CR or LF comes within the window, the call
goes to telnet when the window ends. Once the call has gone one way, it never goes back. The bytes of the line
that decided are dropped, and so are the bytes of a window that ran out. They are line noise while the modems
settle, or keys typed before anything was shown. Every byte after the line that decided reaches telnet.

For a call to the native protocol, the extension asks the core to take the connection as one of the public
port's, from the gateway (DIA NATIVE addr). Then it passes on every byte the caller sent, the PROXY header
first, and relays both ways until either side closes. The core reads the header and the opening as it would if
the gateway had connected to 6501 itself, so a call is in the dial-in class with its limits.

For a call to telnet, the extension tells the core what the call is (DIA addr prior, SRC and the call's text)
on the connection it then gives the telnet extension's session (adapters/telnet.py), so that connection is in
the dial-in class. The session runs on the caller's line in plain mode. A modem line speaks no telnet, so none
of the session's telnet commands go out (PlainWriter) and nobody asks for a terminal type. Every byte the
caller sends is a key, with its 8th bit cleared for a terminal set to 7 bits and parity. The extension echoes
what the caller types, as a terminal program on a modem expects. Like telnet with a terminal that names no
type, it draws for 80 columns and starts with the title page. The plain menu's header shows the time left,
from TIM, as on telnet.

On a telnet call the extension asks the core for TIM every WATCH_SECS, between the session's own requests. On
the extension channel the notices at 5 minutes and at 1 minute come with TIM's reply, and the extension shows
them on the caller's screen. When the core ends the call with ERR LIMIT, the extension shows why and hangs up.

The extension logs nothing about a call. The PROXY header's caller ID and the number dialed go to the core,
which keeps them in memory only.

    python3 -m adapters.dialin --core 127.0.0.1:6590 --key dialin.key --port 6521
"""
import argparse
import asyncio
import sys

from parlord import native

from . import telnet as T
from .common import CoreClient, CoreError, clean, keepalive, peer_addr

PORT = 6521
WINDOW_SECS = 15.0      # from the connection: nothing is sent or echoed until the first CR or LF decides
WATCH_SECS = 2.0        # a telnet call's TIM, for the time allowance's notices and its end
MAX_CALLS = 60          # calls at once
MAXLINE = 80            # the protocol's longest line, its ending included
FIELDS = ("caller", "dialed", "modulation", "rate")     # the call's text that DIA passes on (native.TLV_CALL)
NO_CORE = b"The game server isn't answering. Try again soon.\r\n"
BUSY = b"Every line is busy. Try again soon.\r\n"
GONE = "The game server has closed the connection. Try again soon."
LINE_GONE = (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError)


def log(text):
    print("dialin: %s" % text, file=sys.stderr, flush=True)


def limit_words(final):
    """ERR LIMIT's line -> the sentence a caller is shown: "This call has had its 75 minutes." """
    reason = final[len("ERR LIMIT"):].strip()
    if reason.startswith("closing:"):
        reason = reason[len("closing:"):].strip()
    reason = reason or "the time allowed has run out"
    return reason[:1].upper() + reason[1:].rstrip(".") + "."


class CallIO(T.TelnetIO):
    """Telnet's keys and screens on a modem line, which speaks no telnet: what the session writes goes out
    without telnet commands (PlainWriter), and every byte that comes in is a key, its 8th bit cleared. The
    server echoes what is typed (server_echo), since a terminal program on a modem leaves that to the far
    end."""

    def __init__(self, reader, writer):
        super().__init__(reader, T.PlainWriter(writer))
        self.server_echo = True

    def _feed(self, data):
        for b in data:
            self._byte(b & 0x7F)


class CallCore(CoreClient):
    """A call's connection to the core. Its requests go one at a time, so the watch can ask TIM between the
    telnet session's own. on_notes(lines) gets the INF lines of every TIM reply, which on the extension
    channel are the time allowance's notices. on_end(final) is called once the core ends the call, with
    ERR LIMIT's line, or with None when the connection is lost."""

    def __init__(self, reader, writer):
        super().__init__(reader, writer)
        self.lock = asyncio.Lock()
        self.on_notes = self.on_end = None
        self.closed = False

    async def _req(self, text):
        if not clean(text):
            return await super()._req(text)     # refused before anything is sent
        async with self.lock:
            try:
                data, final = await super()._req(text)
            except LINE_GONE:
                if not self.closed and self.on_end is not None:
                    self.on_end(None)
                raise
        if text == "TIM" and self.on_notes is not None:
            notes = [l[4:] for l in data if l.startswith("INF ")]
            if notes:
                self.on_notes(notes)
        if final.startswith("ERR LIMIT") and self.on_end is not None:
            self.on_end(final)
        return data, final

    def close(self):
        self.closed = True
        super().close()


async def pump(src, dst):
    """Copy what comes from src to dst until src ends or either side fails."""
    try:
        while True:
            data = await src.read(4096)
            if not data:
                return
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError):
        pass


class Call:
    """One connection from a gateway, from the first byte to the hang-up."""

    def __init__(self, args, reader, writer, window=WINDOW_SECS):
        self.args, self.r, self.w, self.window = args, reader, writer, window
        self.peer = peer_addr(writer)
        self.core = None
        self.io = None
        self.ended = False

    async def run(self):
        loop = asyncio.get_running_loop()
        began = loop.time()
        until = began + self.window
        lines = native.Lines(self.r, MAXLINE)
        head = None
        try:
            head = await lines.proxy(self.window, True)
        except asyncio.TimeoutError:
            pass                # the start of the signature and no more: line noise, not a header
        except native.BadOpening:
            return await self.native(None, lines)       # not well formed: the core refuses it as on 6501
        if head is not None:
            if not await self.connect():
                return
            _, final = await self.core.req("DIA %s" % self.peer)
            if final != "OK":
                return await self.native(head, lines)   # not from a listed gateway: refused the same way
        came = await lines.opening_within(max(0.0, until - loop.time()))
        if came is None:
            return              # the caller hung up
        if came:
            await self.native(head, lines)
        else:
            await self.telnet(head, lines, loop.time() - began)

    async def connect(self):
        if self.core is None:
            try:
                self.core = await CallCore.connect(self.args.core_host, self.args.core_port)
            except (OSError, asyncio.TimeoutError, CoreError) as e:
                log("the core is not answering (%s)" % (e or type(e).__name__))
                self.w.write(NO_CORE)
                return False
        return True

    async def native(self, head, lines):
        """The core takes the call as the public port's, and the extension relays it."""
        if not await self.connect():
            return
        _, final = await self.core.req("DIA NATIVE %s" % self.peer)
        if final != "OK":
            log("the core did not take a call to the native protocol (%s)" % final[:40])
            self.w.write(NO_CORE)
            return
        self.core.w.write((head.raw if head is not None else b"") + bytes(lines.buf))
        up = asyncio.ensure_future(pump(self.r, self.core.w))
        down = asyncio.ensure_future(pump(self.core.r, self.w))
        try:
            await asyncio.wait((up, down), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (up, down):
                t.cancel()
            await asyncio.gather(up, down, return_exceptions=True)

    async def telnet(self, head, lines, secs):
        """The telnet session on the caller's line, with the call said to the core first."""
        if not await self.connect():
            return
        call = head is not None and not head.local
        core = self.core
        self.io = CallIO(self.r, self.w)
        if lines.buf:
            # the ending of the line that decided comes first: the LF of its CR LF is part of the same Enter
            self.io.cr = (lines.buf[0] & 0x7F) == native.CR
            self.io._feed(bytes(lines.buf[1:]))
        core.on_notes, core.on_end = self.show, self.end
        if call:
            # the call has been up since before the gateway connected, and for the window since
            _, final = await core.req("DIA %s %d" % (self.peer, head.prior + int(secs)))
            if final != "OK":
                log("the core did not take a call (%s)" % final[:40])
                self.w.write(NO_CORE)
                return
            if head.src:
                await core.source(head.src)     # the caller's address, which the per-address limits count
            for field in FIELDS:
                if head.call.get(field) and not self.ended:
                    await core.req(("DIA %s %s" % (field, head.call[field]))[:MAXLINE - 2])
            if not self.ended:
                await core._req("TIM")      # a pool used up already ends the call here
            if self.ended:
                return
        else:
            await core.source(self.peer)
        watch = asyncio.ensure_future(self.watch()) if call else None
        try:
            await T.main_session(self.io, self.args, telnet=False, core=core)
        except LINE_GONE:
            pass
        finally:
            if watch is not None:
                watch.cancel()
                await asyncio.gather(watch, return_exceptions=True)

    async def watch(self):
        """TIM every WATCH_SECS while the session runs, for the notices and the end of the call."""
        try:
            while not self.ended:
                await asyncio.sleep(WATCH_SECS)
                await self.core._req("TIM")
        except LINE_GONE:
            pass

    def show(self, notes):
        """The time allowance's notices, each on a line of its own."""
        if not self.ended:
            self.io.write("".join("\n%s\n" % n for n in notes))

    def end(self, final):
        """The core ended the call: why, then the hang-up. The session sees its line close and ends."""
        if self.ended:
            return
        self.ended = True
        self.io.write("\n\n%s\n" % (limit_words(final) if final else GONE))
        self.w.close()

    def close(self):
        if self.core is not None:
            self.core.close()
        try:
            self.w.close()
        except Exception:
            pass


def handler(args):
    """The connection handler for the dial-in port: one Call for each connection, MAX_CALLS at once."""
    calls = [0]

    async def handle(reader, writer):
        if calls[0] >= MAX_CALLS:
            writer.write(BUSY)
            writer.close()
            return
        calls[0] += 1
        keepalive(writer)
        call = Call(args, reader, writer, args.window)
        try:
            await call.run()
        except LINE_GONE:
            pass
        except Exception:
            T.log_error("dial-in call")
        finally:
            calls[0] -= 1
            call.close()
    return handle


async def serve(args):
    server = await asyncio.start_server(handler(args), args.host, args.port)
    print("dialin adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.dialin")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--window", type=float, default=WINDOW_SECS, help=argparse.SUPPRESS)      # tests
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    from adapters import dialin as _dialin     # the importable module, as telnet does
    try:
        asyncio.run(_dialin.serve(_dialin.parse_args()))
    except KeyboardInterrupt:
        pass
