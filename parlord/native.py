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

A dial-in gateway listed in the setting dialin.gateways may put a PROXY protocol version 2 header before the
opening (docs/protocol.md, Dial-in gateways). Lines.proxy() reads it when the first bytes are its signature
and returns it as a Proxy. From any other address, and when it is not well formed, it is BadOpening(*WRONG).
It comes within the same 20 seconds as the opening. The dial-in extension (adapters/dialin.py) reads a
gateway's calls with the same Lines: proxy() for the header, then opening_within() for its window of 5
seconds, which looks for the opening by the rules start() uses.

A client may ask for paged replies with PAG bytes (docs/protocol.md, Paged replies). page() splits a reply's
lines into the page sent now and the rest, which waits for the client's CNT. Only the public port pages. The
extension channel refuses PAG.
"""
import asyncio
import ipaddress

CR, LF = 13, 10
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
# Paged replies (PAG): the fewest and the most bytes a page may be set to. The fewest has room for six lines of
# 80 bytes and the CNT line. The reply to every request that changes something fits in it, and a move's reply
# carries only the events that fit (Server.unseen; docs/protocol.md, Paged replies). The most is the largest
# 16-bit number.
PAG_MIN = 512
PAG_MAX = 65535
CNT = "CNT"               # the line that ends a page with more to come, and the request for the next page


def page(lines, most, eol):
    """A reply's lines (str, the final line last) -> (the lines to send now, the rest or None). most: the bytes
    a page may hold (PAG), 0 for no paging. Each line counts with its ending eol. A reply that fits is sent
    whole. Otherwise the page is as many whole lines as fit with the CNT line after them, which counts too,
    and the rest waits for the client's CNT, when page() is given it again."""
    size = lambda l: len(l) + len(eol)      # noqa: E731  (the server's lines are ASCII: one byte a character)
    if not most or sum(map(size, lines)) <= most:
        return list(lines), None
    room, used, n = most - size(CNT), 0, 0
    while n < len(lines) and used + size(lines[n]) <= room:
        used += size(lines[n])
        n += 1
    n = max(n, 1)           # a page always takes a line (it has room for six of 80 bytes)
    return list(lines[:n]) + [CNT], list(lines[n:])


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


# The PROXY protocol, version 2 (the HAProxy project's proxy-protocol.txt): a 12-byte signature, a byte with the
# version (2) and the command (LOCAL 0, PROXY 1), a byte with the address family and transport, the length of
# the rest (16 bits, big-endian), the addresses, and then TLVs (type, 16-bit length, value).
PROXY_SIG = b"\r\n\r\n\x00\r\nQUIT\n"
PROXY_MOST = 512            # the longest header taken (the length after the first 16 bytes); a call's TLVs are short
PROXY_ADDRS = {0x00: 0, 0x11: 12, 0x21: 36, 0x31: 216}      # unspecified, TCP over IPv4, over IPv6, a Unix stream
# The TLVs a dial-in gateway sends, by type: what the call is. TLV_PRIOR is 32 bits, big-endian: the seconds the
# call had been up before the gateway connected. Other types are allowed and passed over.
TLV_CALL = {0xE0: "caller", 0xE1: "dialed", 0xE2: "modulation", 0xE3: "rate"}
TLV_PRIOR = 0xE4
TLV_TEXT = 64               # the most of a call's text TLV that is kept


class Proxy:
    """A PROXY v2 header: local (the gateway's own connection, a health check say, rather than a call it
    carries), src (the source address it names, or None for an unspecified or Unix one), call ({name: text}
    from TLV_CALL, printable ASCII) and prior (TLV_PRIOR's seconds, 0 when it is not there). raw is the whole
    header as it came, which Lines.proxy() sets. The dial-in extension passes it on to the core."""

    def __init__(self, local, src, call, prior):
        self.local, self.src, self.call, self.prior = local, src, call, prior
        self.raw = b""


def parse_proxy(data):
    """The bytes after the 12-byte signature: version and command, family, length, addresses and TLVs, all of
    them -> Proxy. BadOpening(*WRONG) when they are not exactly one well-formed header."""
    if len(data) < 4 or data[0] >> 4 != 2 or data[0] & 15 not in (0, 1) or data[1] not in PROXY_ADDRS:
        raise BadOpening(*WRONG)
    size = int.from_bytes(data[2:4], "big")
    body = data[4:]
    need = PROXY_ADDRS[data[1]]
    if len(body) != size or size < need:
        raise BadOpening(*WRONG)
    src = None
    if data[1] == 0x11:
        src = str(ipaddress.IPv4Address(body[:4]))
    elif data[1] == 0x21:
        src = str(ipaddress.IPv6Address(body[:16]))
    call, prior, i = {}, 0, need
    while i < size:
        if i + 3 > size:
            raise BadOpening(*WRONG)
        kind, n = body[i], int.from_bytes(body[i + 1:i + 3], "big")
        value = body[i + 3:i + 3 + n]
        if len(value) != n:
            raise BadOpening(*WRONG)
        if kind == TLV_PRIOR:
            if n != 4:
                raise BadOpening(*WRONG)
            prior = int.from_bytes(value, "big")
        elif kind in TLV_CALL:
            call[TLV_CALL[kind]] = "".join(chr(b) for b in value[:TLV_TEXT] if 32 <= b < 127)
        i += 3 + n
    return Proxy(data[0] & 15 == 0, src, call, prior)


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
        self.buf += data
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

    async def proxy(self, secs, trusted):
        """A PROXY v2 header at the very start, read whole within secs -> Proxy; None when the first bytes are
        not its signature (they stay for the opening, and a client that closes while they could still become
        it gets None too). BadOpening(*WRONG) for the signature from an address that is not trusted, and for a
        header that is too long, cut short or not well formed; TimeoutError when it has not all come in time."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + secs
        while True:
            n = min(len(self.buf), len(PROXY_SIG))
            if self.buf[:n] != PROXY_SIG[:n]:
                return None
            if n == len(PROXY_SIG) and not trusted:
                raise BadOpening(*WRONG)
            if len(self.buf) >= len(PROXY_SIG) + 4:
                size = int.from_bytes(self.buf[14:16], "big")
                if size > PROXY_MOST:
                    raise BadOpening(*WRONG)
                if len(self.buf) >= 16 + size:
                    break
            if not await self._more(deadline - loop.time()):
                if n == len(PROXY_SIG):
                    raise BadOpening(*WRONG)
                return None
        size = 16 + int.from_bytes(self.buf[14:16], "big")
        raw = bytes(self.buf[:size])
        del self.buf[:size]
        p = parse_proxy(raw[12:])
        p.raw = raw
        return p

    async def opening_within(self, secs):
        """The dial-in extension's window: wait up to secs for the opening, PARLORNET/1 and the first byte of its
        line ending, by the rules start() uses, with at most JUNK bytes of anything before it. Bytes that cannot
        become the opening do not end the wait. Once the bytes so far can never become it, what comes after
        them is read and dropped until the time is up. -> True as soon as the opening has come (the bytes stay
        in buf for start()), False when secs ran out first, None when the client closed first."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + secs
        hopeless = False
        while True:
            if not hopeless:
                try:
                    if self._opening() is not None:
                        return True
                except BadOpening:
                    hopeless = True
            if hopeless:
                self.buf.clear()
            try:
                if not await self._more(deadline - loop.time()):
                    return None
            except asyncio.TimeoutError:
                return False

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
