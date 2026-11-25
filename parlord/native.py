"""Lines on the public native port (6501), docs/protocol.md, Connecting.

The client speaks first. Its first line is the protocol's name and version,
PARLORNET/1, in any case, and the byte or two after the 1 set the connection's
line ending, which the server then uses for every line it sends and expects on
every line it reads:

    CR, then LF within 250 ms           CR LF
    CR, then nothing within 250 ms      CR
    LF, then CR within 250 ms           LF CR
    LF, then nothing within 250 ms      LF

(250 ms: ENDING_SECS, core.Limits.ending_secs.) Up to 10 bytes of anything may come before
PARLORNET/1 and are ignored. When the bytes cannot become the opening, the
start fails with BadOpening: ERR VERSION for PARLORNET/ with another version,
ERR PROTOCOL for anything else (more than 10 bytes before it, or a byte after
the 1 that is not CR or LF), and so does an opening that has not come within
20 seconds (OPENING_SECS); opening_error() makes the line, with where the
protocol is documented, and the connection is closed. After the start a line ends only with the
connection's ending; a CR or LF used any other way ends a line that is refused
(ERR NEWLINE) and not run. The extension channel does not use this: it keeps
CR LF lines and the server's greeting first. The GameMaster extension's
tunnel (adapters/gamemaster.py) starts the same way.
"""
import asyncio

CR, LF = 13, 10
HANG_UP = 3               # Control-C: the parlor closes the connection, so a bridge (pndial's nc) ends
NAMES = {b"\r\n": "CR LF", b"\r": "CR", b"\n": "LF", b"\n\r": "LF CR"}
NAME = b"PARLORNET/"      # the opening is NAME and VERSION, then the line ending
VERSION = b"1"
JUNK = 10                 # bytes of anything allowed before the opening
WRONG = ("PROTOCOL", "wrong protocol")
WRONG_VERSION = ("VERSION", "wrong version")
OPENING_SECS = 20.0       # from connecting to the opening
ENDING_SECS = 0.25        # after the first CR (LF), for the LF (CR) of a two-byte ending
PAGE = "docs/protocol.html"     # the protocol's reference on the docs site (site.docs_url)
MAXLINE = 80


def opening_error(code, text, docs_url=""):
    """The ERR line for a start that failed, ending CR LF (no ending is set yet): ERR code text, and
    "; see" the protocol's page on the docs site when docs_url (site.docs_url, ending /) is given and the
    line still fits 80 bytes."""
    line = "ERR %s %s" % (code, text)
    see = "; see " + docs_url + PAGE
    if docs_url and len(line) + len(see) <= MAXLINE - 2:
        line += see
    return line.encode("ascii", "replace") + b"\r\n"


class BadOpening(Exception):
    """The client's first bytes cannot become the opening: code and text of the ERR line."""

    def __init__(self, code, text):
        super().__init__(code, text)
        self.code, self.text = code, text


class TooLong(Exception):
    """A line longer than the protocol allows."""


class Lines:
    def __init__(self, reader, maxline=80):
        self.r = reader
        self.maxline = maxline
        self.buf = bytearray()
        self.eol = None

    async def _more(self, timeout):
        """Read what has come into buf; False at end of input. TimeoutError past timeout."""
        if timeout is not None and timeout <= 0:
            raise asyncio.TimeoutError
        data = await asyncio.wait_for(self.r.read(1024), timeout)
        if not data:
            return False
        if self.eol is not None and HANG_UP in data:
            self.buf.clear()                        # Control-C in a session hangs up, the line it ends unrun:
            return False                            # the protocol is printable text (before the opening a
        self.buf += data                            # telnet client's negotiation can hold the byte)
        return True

    def _opening(self):
        """Where the line ending starts in buf, once the opening up to it has come (None while more is
        needed). Raises BadOpening when the bytes so far cannot become the opening."""
        buf = bytes(self.buf).upper()
        for p in range(min(JUNK, len(buf)) + 1):
            head = buf[p:p + len(NAME)]
            if not NAME.startswith(head):
                continue
            if len(head) < len(NAME):
                return None                         # it can still become the opening
            i = j = p + len(NAME)
            while j < len(buf) and buf[j] in b"0123456789":
                j += 1
            if not VERSION.startswith(buf[i:j]):
                raise BadOpening(*WRONG_VERSION)
            if j == len(buf):
                return None
            if buf[i:j] == VERSION and buf[j] in (CR, LF):
                return j
            raise BadOpening(*WRONG)
        raise BadOpening(*WRONG)                    # more than JUNK bytes, and none of them start it

    async def start(self, secs, pair_secs):
        """Wait up to secs for the client's opening (PARLORNET/1 and the first byte of its ending) and
        set the ending from it (and the byte after it, within pair_secs). Returns the ending, or None
        if the client closed first. Raises TimeoutError when the opening did not come in time and
        BadOpening when the bytes cannot become it. Bytes after the ending are the next line."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + secs
        while True:
            at = self._opening()
            if at is not None:
                break
            if not await self._more(deadline - loop.time()):
                return None
        first = self.buf[at]
        del self.buf[:at + 1]
        second = LF if first == CR else CR
        if not self.buf:
            try:
                await self._more(pair_secs)
            except asyncio.TimeoutError:
                pass
        if self.buf[:1] == bytes([second]):
            del self.buf[0]
            self.eol = bytes([first, second])
        else:
            self.eol = bytes([first])
        return self.eol

    async def line(self, timeout):
        """The next line as (bytes, good): good is False when it ended with a CR or LF that is not the
        connection's ending (the line is refused, not run). None at end of input (a last line without
        its ending is returned as a good line, as a stream reader's readline would). TimeoutError when
        no whole line came within timeout; TooLong when the line passes the length limit."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        eol = self.eol
        while True:
            i = next((k for k, b in enumerate(self.buf) if b in (CR, LF)), -1)
            if i > self.maxline or (i < 0 and len(self.buf) > self.maxline):
                raise TooLong
            if i >= 0:
                if self.buf[i] != eol[0]:
                    good, used = False, 1           # the other byte: not this connection's ending
                elif len(eol) == 1:
                    good, used = True, 1
                elif i + 1 < len(self.buf):
                    good = self.buf[i + 1] == eol[1]
                    used = 2 if good else 1
                else:
                    good = None                     # the first byte of a two-byte ending: wait for the second
                if good is not None:
                    text = bytes(self.buf[:i])
                    del self.buf[:i + used]
                    return text, good
            if not await self._more(deadline - loop.time()):
                if self.buf:
                    text = bytes(self.buf)
                    self.buf.clear()
                    return text, not any(b in (CR, LF) for b in text)
                return None
