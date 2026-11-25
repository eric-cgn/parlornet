"""RSCARDS packets: the wire format of GEnie's RSCARDS graphics front ends, as read from the front ends' code.

A packet is one command byte (A-Z or 1), fixed-width arguments and a two-byte checksum, all printable 7-bit
ASCII. The argument encodings:

    b   one byte, 0x20 + v (v 0..63)
    w   two bytes, 0x20 + (v >> 6), 0x20 + (v & 63) (v 0..4095)
    t   three bytes, 6 bits each, high first (v 0..262143)
    c   one raw printable byte
    s   printable bytes without ~, then ~ (at most 64 before it)

The checksum is the sum of the command byte and every argument byte, masked with 0xFFF, as a w. The front end
answers each packet with ACK (06 0D) or NAK (15 0D), ignores a packet identical to the last one it
acknowledged (it acknowledges it again but does not draw it), and skips CR and LF anywhere.

The virtual screen is 640 by 200 with an 8 by 8 font (80 columns, 25 rows). Colors: 0 black, 1 white, 2 red,
3 green, 4 blue, 5 yellow, 6 magenta, 7 cyan; a mono front end draws 0 black and every other color white.

This module builds packets (one function per command, returning bytes with the checksum) and parses them
(parse, for the tests' scripted front end and for measuring). It knows nothing of games or the core."""

BLACK, WHITE, RED, GREEN, BLUE, YELLOW, MAGENTA, CYAN = range(8)
ACK, NAK = 0x06, 0x15
WIDTH, HEIGHT = 640, 200
CELL_W, CELL_H = 8, 8

# T fonts, as the IBM and Mac front ends draw them: the normal 8 by 8 font left-aligned, centered in the width or
# right-aligned (24, 26, 28), and a small font the same ways (25, 27, 29)
LEFT, CENTER, RIGHT = 24, 26, 28
SMALL_LEFT, SMALL_CENTER, SMALL_RIGHT = 25, 27, 29
MSG_LINE, MSG_APPEND = 200, 201         # T at y=200 writes the message area's last line; 201 scrolls it first

ARGS = {            # wire order of each command's arguments
    "A": "rb", "B": "rc", "C": "wwb", "D": "b", "E": "wwwwb", "F": "rww", "G": "rr", "H": "rc",
    "I": "wwbcs", "J": "r", "K": "c", "L": "bbb", "M": "b", "N": "bwwbbbt", "O": "rbb", "P": "b", "Q": "c",
    "R": "r", "S": "rr", "T": "bwwws", "U": "bwS", "V": "wwwwww", "W": "wwwwb", "X": "wwbb", "Y": "bww",
    "Z": "c", "1": "tw",
}
SIZES = {"b": 1, "c": 1, "w": 2, "t": 3, "r": 8}


class WireError(ValueError):
    pass


# ---- encoding ---------------------------------------------------------------------------------------------

def b(v):
    if not 0 <= v <= 63:
        raise WireError("b out of range: %r" % v)
    return bytes([0x20 + v])


def w(v):
    if not 0 <= v <= 4095:
        raise WireError("w out of range: %r" % v)
    return bytes([0x20 + (v >> 6), 0x20 + (v & 63)])


def t(v):
    if not 0 <= v <= 262143:
        raise WireError("t out of range: %r" % v)
    return bytes([0x20 + (v >> 12), 0x20 + ((v >> 6) & 63), 0x20 + (v & 63)])


def rect(x, y, wd, ht):
    return w(x) + w(y) + w(wd) + w(ht)


def c(ch):
    v = ch if isinstance(ch, int) else ord(ch)
    if not 0x20 <= v <= 0x7D:
        raise WireError("not a printable code: %r" % ch)
    return bytes([v])


def clean_text(text, most=64):
    """Text the front end can show: printable ASCII, no ~, at most `most` characters."""
    out = "".join(ch if 0x20 <= ord(ch) <= 0x7D else "?" for ch in str(text))
    return out[:most]


def s(text):
    data = clean_text(text).encode("ascii")
    return data + b"~"


def checksum(body):
    return sum(x for x in body if x not in (0x0D, 0x0A)) & 0xFFF


def packet(cmd, args=b""):
    body = cmd.encode("ascii") + args
    return body + w(checksum(body))


def body(cmd, args=b""):
    """A command without its checksum, as a macro (U) stores it."""
    return cmd.encode("ascii") + args


# one function per command; each returns a whole packet (or, with raw=True, the body for a macro)

def fill(x, y, wd, ht, color, raw=False):
    """A: fill a rectangle (a zero rectangle is the whole screen)."""
    return (body if raw else packet)("A", rect(x, y, wd, ht) + b(color))


def area(x, y, wd, ht, code):
    """B: a click area; a click sends the code three times and CR. A zero rectangle clears every area."""
    return packet("B", rect(x, y, wd, ht) + c(code))


def card(x, y, n):
    return packet("C", w(x) + w(y) + b(n))


def entry_line(on):
    """D: the entry line at the bottom (on: shown and enabled)."""
    return packet("D", b(0 if on else 1))


def disc(x, y, rx, ry, color, raw=False):
    """E: a filled oval centered at x, y with radii rx, ry."""
    return (body if raw else packet)("E", w(x) + w(y) + w(rx) + w(ry) + b(color))


def flash(x, y, wd, ht, on, off, frame=0):
    return packet("F", rect(frame * 1000 + x, y, wd, ht) + w(on) + w(off))


def hot(x, y, wd, ht, code, frame=0):
    """H: a click area that is inverted while the pointer is in it (frame: a frame of that many pixels)."""
    return packet("H", rect(frame * 1000 + x, y, wd, ht) + c(code))


def field(x, y, maxlen, text="", fg=WHITE, bg=BLACK, digits=False):
    """I: an input field; RETURN sends four spaces, the text and CR."""
    return packet("I", w(x) + w(y) + b(maxlen) + c("9" if digits else "X") + s("%d%d%s" % (fg, bg, text)))


def scroll_area(first, rows, color):
    """L: the message area, rows first .. first+rows-1 of the 25; L 0 0 0 for none."""
    return packet("L", b(first) + b(rows) + b(color))


def mouse(on):
    return packet("M", b(8 if on else 9))


def panel(x, y, wd, ht, outer, inner, raw=False):
    """O: fill with outer, then an inset rectangle with inner."""
    return (body if raw else packet)("O", rect(x, y, wd, ht) + b(outer) + b(inner))


def play(slot):
    return packet("P", b(slot))


def leave():
    """Q: back to terminal mode."""
    return packet("Q", c("Q"))


def invert(x, y, wd, ht, frame=0, raw=False):
    return (body if raw else packet)("R", rect(frame * 1000 + x, y, wd, ht))


def text(x, y, txt, fg=WHITE, bg=BLACK, font=LEFT, width=0, raw=False):
    """T: text at x, y (font LEFT, CENTER or RIGHT in width; SMALL_* for the small font)."""
    return (body if raw else packet)("T", b(font) + w(x) + w(y) + w(width) + s("%d%d%s" % (fg, bg, txt)))


def macro(slot, data):
    """U: store data (command bodies without checksums, at most 127 bytes) as macro slot."""
    if not 1 <= len(data) <= 127:
        raise WireError("a macro is 1 to 127 bytes, not %d" % len(data))
    return packet("U", b(slot) + w(len(data)) + data)


def line(x1, y1, x2, y2, color, raw=False):
    """W: a line."""
    return (body if raw else packet)("W", w(x1) + w(y1) + w(x2) + w(y2) + b(color))


def die(x, y, color, value, raw=False):
    """X: a 48 by 24 die face at x, y showing value pips (1-9)."""
    return (body if raw else packet)("X", w(x) + w(y) + b(color) + b(value))


def run(slot, dx, dy):
    """Y: play macro slot with the origin moved by dx, dy."""
    return packet("Y", b(slot) + w(dx) + w(dy))


def reset():
    """Z: clear the screen, the click areas and the flashers; mouse off; the beacon stops."""
    return packet("Z", c("Z"))


def tone(value, duration):
    return packet("1", t(value) + w(duration))


# ---- decoding (the tests' front end and measurements) -----------------------------------------------------

def parse(data):
    """Bytes from the host -> [(cmd, [args], ok)], one per packet: ok is False when the checksum is wrong.
    Bytes between packets that are no command are skipped, as the front end skips them. A packet cut short
    raises WireError."""
    data = bytes(x for x in data if x not in (0x0D, 0x0A))
    out, i = [], 0
    while i < len(data):
        cmd = chr(data[i])
        if cmd not in ARGS:
            i += 1
            continue
        start = i
        i += 1
        args = []
        for kind in ARGS[cmd]:
            if kind == "s":
                end = data.find(b"~", i)
                if end < 0 or end - i > 64:
                    raise WireError("an unterminated string in %r" % data[start:start + 80])
                args.append(data[i:end].decode("ascii"))
                i = end + 1
            elif kind == "S":
                n = args[-1]
                args.append(data[i:i + n])
                i += n
            else:
                n = SIZES[kind]
                if i + n > len(data):
                    raise WireError("a packet cut short: %r" % data[start:])
                chunk = data[i:i + n]
                i += n
                args.append(decode(kind, chunk))
        if i + 2 > len(data):
            raise WireError("a packet without its checksum: %r" % data[start:])
        got = decode("w", data[i:i + 2])
        i += 2
        out.append((cmd, args, got == checksum(data[start:i - 2])))
    return out


def split(data):
    """Packets written one after another (with their checksums) -> the list of packets."""
    out, i = [], 0
    while i < len(data):
        cmd = chr(data[i])
        if cmd not in ARGS:
            raise WireError("not a packet: %r" % data[i:i + 20])
        j = i + 1
        for kind in ARGS[cmd]:
            if kind == "s":
                j = data.index(b"~", j) + 1
            elif kind == "S":
                j += decode("w", data[j - 2:j])
            else:
                j += SIZES[kind]
        j += 2
        out.append(data[i:j])
        i = j
    return out


def decode(kind, chunk):
    if kind == "c":
        return chr(chunk[0])
    vals = [x - 0x20 for x in chunk]
    if any(not 0 <= v <= 63 for v in vals):
        raise WireError("a byte out of range in %r" % chunk)
    if kind == "b":
        return vals[0]
    if kind == "w":
        return vals[0] * 64 + vals[1]
    if kind == "t":
        return (vals[0] * 64 + vals[1]) * 64 + vals[2]
    if kind == "r":
        return tuple(vals[k] * 64 + vals[k + 1] for k in (0, 2, 4, 6))
    raise WireError(kind)


def parse_macro(data):
    """A macro's bytes (command bodies without checksums) -> [(cmd, [args])]."""
    out, i = [], 0
    while i < len(data):
        cmd = chr(data[i])
        i += 1
        if cmd not in ARGS:
            continue
        args = []
        for kind in ARGS[cmd]:
            if kind == "s":
                end = data.find(b"~", i)
                args.append(data[i:end].decode("ascii"))
                i = end + 1
            else:
                n = SIZES[kind]
                args.append(decode(kind, data[i:i + n]))
                i += n
        out.append((cmd, args))
    return out
