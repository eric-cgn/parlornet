"""One image per board, for the web site's game pages: a GIF with an image map.

A game module's board_image(v, seat, sel) draws its board on a Canvas: tiles from its own images()
(composited, palettes merged), lines, discs and text in a small bitmap font. It marks the places a
player can click with area(): a rectangle, circle or polygon and the link it stands for (a page and its
query). web.py turns the canvas into one GIF (gif(), LZW-compressed, up to 256 colors), an <img> with
USEMAP (a client-side map of those areas) and ISMAP (the same areas looked up on the server by /click.htm,
for browsers without client-side maps), and keeps the result in a small cache (BoardCache).

Pure Python, no dependencies. Nothing here knows any game.
"""
import hashlib
import time
from collections import OrderedDict

# ---- GIF: encode with LZW, decode (for tiles and tests) ---------------------------------------------


def encode_gif(w, h, palette, pixels):
    """A GIF89a of w x h pixels (bytes or a list of palette indexes, row by row), LZW-compressed, with
    the palette padded to a power of two (2 to 256 colors)."""
    palette = list(palette) or [(0, 0, 0)]
    depth = max(1, (len(palette) - 1).bit_length())
    palette += [(0, 0, 0)] * ((1 << depth) - len(palette))
    out = bytearray(b"GIF89a")
    out += bytes([w & 255, w >> 8, h & 255, h >> 8, 0x80 | 0x70 | (depth - 1), 0, 0])
    for r, g, b in palette:
        out += bytes([r, g, b])
    out += b"," + bytes([0, 0, 0, 0, w & 255, w >> 8, h & 255, h >> 8, 0])
    min_size = max(2, depth)
    out.append(min_size)
    data = _lzw(bytes(pixels), min_size)
    for j in range(0, len(data), 255):
        chunk = data[j:j + 255]
        out.append(len(chunk))
        out += chunk
    out += b"\x00;"
    return bytes(out)


def _lzw(pixels, min_size):
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    data = bytearray()
    bits = nbits = 0
    size = min_size + 1
    table = {}
    nxt = eoi + 1

    bits |= clear << nbits
    nbits += size
    if not pixels:
        pixels = b"\x00"
    prefix = pixels[0]
    for p in pixels[1:]:
        k = (prefix << 8) | p
        c = table.get(k)
        if c is not None:
            prefix = c
            continue
        bits |= prefix << nbits
        nbits += size
        while nbits >= 8:
            data.append(bits & 255)
            bits >>= 8
            nbits -= 8
        table[k] = nxt
        if nxt == (1 << size) and size < 12:
            size += 1
        nxt += 1
        if nxt == 4096:                 # the table is full: start again
            bits |= clear << nbits
            nbits += size
            table = {}
            nxt = eoi + 1
            size = min_size + 1
        prefix = p
    for code in (prefix, eoi):
        bits |= code << nbits
        nbits += size
        while nbits >= 8:
            data.append(bits & 255)
            bits >>= 8
            nbits -= 8
        if code == prefix and nxt < 4096:      # the decoder adds an entry on this code too
            if nxt == (1 << size) and size < 12:
                size += 1
            nxt += 1
    if nbits:
        data.append(bits & 255)
    return bytes(data)


class Image:
    """A decoded GIF: w, h, palette [(r, g, b)], pixels (bytes, row by row)."""
    __slots__ = ("w", "h", "palette", "pixels")

    def __init__(self, w, h, palette, pixels):
        self.w, self.h, self.palette, self.pixels = w, h, palette, bytes(pixels)


def decode_gif(data):
    """The first image of a GIF (global palette, no interlace) -> Image. Raises ValueError on anything
    else, or on a short or broken file."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise ValueError("not a GIF")
    w, h, flags = data[6] | data[7] << 8, data[8] | data[9] << 8, data[10]
    pos = 13
    palette = []
    if flags & 0x80:
        n = 2 << (flags & 7)
        palette = [tuple(data[pos + 3 * i:pos + 3 * i + 3]) for i in range(n)]
        pos += 3 * n
    while pos < len(data) and data[pos] == 0x21:            # extensions: skip
        pos += 2
        while data[pos]:
            pos += data[pos] + 1
        pos += 1
    if pos >= len(data) or data[pos] != 0x2C:
        raise ValueError("no image")
    iw, ih, iflags = data[pos + 5] | data[pos + 6] << 8, data[pos + 7] | data[pos + 8] << 8, data[pos + 9]
    if (iw, ih) != (w, h) or iflags & 0xC0:
        raise ValueError("only whole, non-interlaced images with the global palette")
    pos += 10
    min_size = data[pos]
    pos += 1
    raw = bytearray()
    while data[pos]:
        raw += data[pos + 1:pos + 1 + data[pos]]
        pos += data[pos] + 1
    if data[pos + 1:pos + 2] != b";":
        raise ValueError("no trailer")
    px = _unlzw(bytes(raw), min_size, w * h)
    if len(px) != w * h:
        raise ValueError("%d pixels, not %d" % (len(px), w * h))
    return Image(w, h, palette, px)


def _unlzw(raw, min_size, want):
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    out = bytearray()
    size = min_size + 1
    table = [bytes([i]) for i in range(clear)] + [b"", b""]
    prev = None
    bits = nbits = 0
    pos = 0
    while True:
        while nbits < size:
            if pos >= len(raw):
                return bytes(out)
            bits |= raw[pos] << nbits
            pos += 1
            nbits += 8
        code = bits & ((1 << size) - 1)
        bits >>= size
        nbits -= size
        if code == clear:
            size = min_size + 1
            table = table[:eoi + 1]
            prev = None
            continue
        if code == eoi:
            return bytes(out)
        if code < len(table):
            entry = table[code]
            if prev is not None:
                table.append(prev + entry[:1])
        elif code == len(table) and prev is not None:
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ValueError("bad LZW code")
        out += entry
        if len(out) > want:
            raise ValueError("too many pixels")
        prev = entry
        if len(table) == (1 << size) and size < 12:
            size += 1


# ---- a bitmap font ------------------------------------------------------------------------------------

# 5x7 glyphs, rows top to bottom; letters, digits and a little punctuation
FONT = {k: v.split() for k, v in {
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    "a": "00000 00000 01110 00001 01111 10001 01111", "b": "10000 10000 10110 11001 10001 10001 11110",
    "c": "00000 00000 01110 10000 10000 10001 01110", "d": "00001 00001 01101 10011 10001 10001 01111",
    "e": "00000 00000 01110 10001 11111 10000 01110", "f": "00110 01001 01000 11100 01000 01000 01000",
    "g": "00000 00000 01111 10001 01111 00001 01110", "h": "10000 10000 10110 11001 10001 10001 10001",
    "i": "00100 00000 01100 00100 00100 00100 01110", "j": "00010 00000 00110 00010 00010 10010 01100",
    "k": "10000 10000 10010 10100 11000 10100 10010", "l": "01100 00100 00100 00100 00100 00100 01110",
    "m": "00000 00000 11010 10101 10101 10001 10001", "n": "00000 00000 10110 11001 10001 10001 10001",
    "o": "00000 00000 01110 10001 10001 10001 01110", "p": "00000 00000 11110 10001 11110 10000 10000",
    "q": "00000 00000 01111 10001 01111 00001 00001", "r": "00000 00000 10110 11001 10000 10000 10000",
    "s": "00000 00000 01110 10000 01110 00001 11110", "t": "01000 01000 11100 01000 01000 01001 00110",
    "u": "00000 00000 10001 10001 10001 10011 01101", "v": "00000 00000 10001 10001 10001 01010 00100",
    "w": "00000 00000 10001 10001 10101 10101 01010", "x": "00000 00000 10001 01010 00100 01010 10001",
    "y": "00000 00000 10001 10001 01111 00001 01110", "z": "00000 00000 11111 00010 00100 01000 11111",
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    " ": "00000 00000 00000 00000 00000 00000 00000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "_": "00000 00000 00000 00000 00000 00000 11111", "+": "00000 00100 00100 11111 00100 00100 00000",
    ":": "00000 01100 01100 00000 01100 01100 00000", ".": "00000 00000 00000 00000 00000 01100 01100",
    "<": "00010 00100 01000 10000 01000 00100 00010", ">": "01000 00100 00010 00001 00010 00100 01000",
    "/": "00001 00010 00010 00100 01000 01000 10000", "?": "01110 10001 00001 00010 00100 00000 00100",
    ",": "00000 00000 00000 00000 01100 00100 01000", "(": "00010 00100 01000 01000 01000 00100 00010",
    ")": "01000 00100 00010 00010 00010 00100 01000", "'": "00100 00100 01000 00000 00000 00000 00000",
    "!": "00100 00100 00100 00100 00100 00000 00100", "=": "00000 00000 11111 00000 11111 00000 00000",
}.items()}
# 3x5 digits, for counts on small things
SMALL = {k: v.split() for k, v in {
    "0": "111 101 101 101 111", "1": "010 110 010 010 111", "2": "111 001 111 100 111",
    "3": "111 001 011 001 111", "4": "101 101 111 001 001", "5": "111 100 111 001 111",
    "6": "111 100 111 101 111", "7": "111 001 001 001 001", "8": "111 101 111 101 111",
    "9": "111 101 111 001 111", " ": "000 000 000 000 000", "-": "000 000 111 000 000",
    "x": "000 101 010 101 000", "+": "000 010 111 010 000",
}.items()}


def text_size(s, font=FONT, scale=1):
    """(width, height) of s in pixels, one blank column between characters."""
    if not s:
        return 0, 0
    g = font.get("0") or next(iter(font.values()))
    return (len(s) * (len(g[0]) + 1) - 1) * scale, len(g) * scale


# ---- the canvas ----------------------------------------------------------------------------------------

class Canvas:
    """A board being drawn: w x h pixels of up to 256 colors (added as they are used), and the areas a
    player can click (area()). anywhere: the module's board_click(v, seat, sel, x, y) answers a click
    on any point not in an area (Cannon Fire's sky)."""

    def __init__(self, w, h, bg=(255, 255, 255), images=None):
        self.w, self.h = int(w), int(h)
        self.palette, self._ink = [], {}
        self.px = bytearray([self.ink(bg)]) * (self.w * self.h)
        self.areas = []
        self.anywhere = False
        self.images = images        # the module's images function, for tile()
        self.used, self.words = [], []      # (tile, x, y) and (text, x, y) drawn: for tests

    def tile(self, path, x, y, clear=None):
        """Draw one of the module's own images (its images() path) with its top left at x, y."""
        img = tiles(self.images)[path]
        self.blit(img, x, y, clear)
        self.used.append((path, x, y))
        return img

    def ink(self, rgb):
        """The palette index of a color, added if new (ValueError past 256 colors)."""
        rgb = tuple(rgb)
        i = self._ink.get(rgb)
        if i is None:
            if len(self.palette) >= 256:
                raise ValueError("more than 256 colors")
            i = self._ink[rgb] = len(self.palette)
            self.palette.append(rgb)
        return i

    def fill(self, x, y, w, h, rgb):
        x0, y0, x1, y1 = max(0, int(x)), max(0, int(y)), min(self.w, int(x + w)), min(self.h, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes([self.ink(rgb)]) * (x1 - x0)
        for yy in range(y0, y1):
            o = yy * self.w
            self.px[o + x0:o + x1] = row

    def frame(self, x, y, w, h, rgb, t=1):
        """A rectangle's outline, t pixels thick, inside x, y, w, h."""
        self.fill(x, y, w, t, rgb)
        self.fill(x, y + h - t, w, t, rgb)
        self.fill(x, y, t, h, rgb)
        self.fill(x + w - t, y, t, h, rgb)

    def put(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[int(y) * self.w + int(x)] = self.ink(rgb)

    def get(self, x, y):
        return self.palette[self.px[y * self.w + x]]

    def blit(self, img, x, y, clear=None):
        """Draw a decoded image (Image) with its top left at x, y; clear: a palette index of img that is
        left out (see-through)."""
        table = bytes(self.ink(c) for c in img.palette) + bytes(256 - len(img.palette))
        src = img.pixels.translate(table)
        for r in range(img.h):
            yy = y + r
            if not 0 <= yy < self.h:
                continue
            x0, x1 = max(0, x), min(self.w, x + img.w)
            if x1 <= x0:
                return
            line = src[r * img.w + (x0 - x):r * img.w + (x1 - x)]
            o = yy * self.w
            if clear is None:
                self.px[o + x0:o + x1] = line
            else:
                raw = img.pixels[r * img.w + (x0 - x):r * img.w + (x1 - x)]
                for i, c in enumerate(raw):
                    if c != clear:
                        self.px[o + x0 + i] = line[i]

    def text(self, x, y, s, rgb, font=FONT, scale=1, align="left"):
        """Text with its top left at x, y (align "center": x is the middle, "right": the right edge)."""
        x, y = int(round(x)), int(round(y))
        self.words.append((s, x, y))
        w, _ = text_size(s, font, scale)
        if align == "center":
            x -= w // 2
        elif align == "right":
            x -= w
        ink = self.ink(rgb)
        for ch in s:
            g = font.get(ch) or font.get(ch.upper()) or font.get(ch.lower())
            if g is None:
                g = font.get("?") or font["0"]
            for r, bits in enumerate(g):
                for c, b in enumerate(bits):
                    if b == "1":
                        for sy in range(scale):
                            yy = y + r * scale + sy
                            for sx in range(scale):
                                xx = x + c * scale + sx
                                if 0 <= xx < self.w and 0 <= yy < self.h:
                                    self.px[yy * self.w + xx] = ink
            x += (len(g[0]) + 1) * scale

    def disc(self, cx, cy, r, rgb, r_in=-1.0):
        """A filled disc (or a ring from r_in to r) round cx, cy (pixel centers at .5 are not assumed)."""
        ink = self.ink(rgb)
        r2, ri2 = r * r, (r_in * r_in if r_in >= 0 else -1)
        for yy in range(max(0, int(cy - r - 1)), min(self.h, int(cy + r + 2))):
            dy2 = (yy - cy) ** 2
            for xx in range(max(0, int(cx - r - 1)), min(self.w, int(cx + r + 2))):
                d2 = (xx - cx) ** 2 + dy2
                if d2 <= r2 and d2 > ri2:
                    self.px[yy * self.w + xx] = ink

    def line(self, x0, y0, x1, y1, rgb, width=1, dash=0):
        """A straight line from x0, y0 to x1, y1, width pixels wide; dash > 0: every other dash pixels."""
        ink = self.ink(rgb)
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        half = (width - 1) / 2.0
        for i in range(n):
            if dash and (i // dash) % 2:
                continue
            t = i / max(1, n - 1)
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            for yy in range(int(round(y - half)), int(round(y - half)) + width):
                for xx in range(int(round(x - half)), int(round(x - half)) + width):
                    if 0 <= xx < self.w and 0 <= yy < self.h:
                        self.px[yy * self.w + xx] = ink

    def polygon(self, points, rgb):
        """A filled polygon (even-odd rule, pixel centers)."""
        ink = self.ink(rgb)
        ys = [p[1] for p in points]
        for yy in range(max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)):
            yc = yy + 0.5
            xs = []
            for i in range(len(points)):
                (ax, ay), (bx, by) = points[i], points[i - 1]
                if (ay <= yc < by) or (by <= yc < ay):
                    xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                x0, x1 = max(0, int(round(a))), min(self.w, int(round(b)))
                if x1 > x0:
                    o = yy * self.w
                    self.px[o + x0:o + x1] = bytes([ink]) * (x1 - x0)

    def labels(self, x0, y0, cell, cols, rows, rgb, gap=3, font=FONT):
        """Column letters (a, b, ...) above and row numbers (1, 2, ...) left of a grid of cols x rows cells of
        cell pixels whose top left is x0, y0."""
        _, th = text_size("0", font)
        for i in range(cols):
            self.text(x0 + i * cell + cell // 2 + 1, y0 - gap - th, chr(97 + i), rgb, font, align="center")
        for j in range(rows):
            self.text(x0 - gap, y0 + j * cell + (cell - th) // 2, str(j + 1), rgb, font, align="right")

    def area(self, shape, coords, alt, path, **params):
        """A place to click: shape "rect" (x1, y1, x2, y2, inclusive), "circle" (x, y, r) or "poly" (x1, y1,
        x2, y2, ...), standing for the link path?t=<table>&params, with alt as its text."""
        self.areas.append((shape, tuple(int(c) for c in coords), str(alt), path,
                           tuple(sorted((k, str(v)) for k, v in params.items() if v is not None))))

    def gif(self):
        return encode_gif(self.w, self.h, self.palette, self.px)


def hit(areas, x, y):
    """The first area holding the point x, y (as browsers pick among overlapping areas), or None."""
    for a in areas:
        shape, c = a[0], a[1]
        if shape == "rect" and c[0] <= x <= c[2] and c[1] <= y <= c[3]:
            return a
        if shape == "circle" and (x - c[0]) ** 2 + (y - c[1]) ** 2 <= c[2] ** 2:
            return a
        if shape == "poly" and _inside(c, x, y):
            return a
    return None


def _inside(c, x, y):
    pts = list(zip(c[0::2], c[1::2]))
    inside = False
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i], pts[i - 1]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


# ---- the tiles a module already has -------------------------------------------------------------------

_tiles = {}


def tiles(images):
    """A module's images() decoded once: {path: Image}. images is the module's images function, or one with a
    cache_key of its own (a game's images in a player's colors: the most recent 32 of those are kept)."""
    key = getattr(images, "cache_key", None) or images.__module__
    if key not in _tiles:
        own = [k for k in _tiles if ":" in k]
        if ":" in key and len(own) >= 32:
            del _tiles[own[0]]
        _tiles[key] = {p: decode_gif(g) for p, g in images().items()}
    return _tiles[key]


# ---- rendered boards, kept for a while --------------------------------------------------------------------

def view_digest(v):
    """A short digest of what a board image is drawn from: the parsed view, less what changes without the
    board changing (the clock) or is not the board's (a /check.htm answer)."""
    items = sorted((k, repr(x)) for k, x in v.items() if k not in ("clock", "check"))
    return hashlib.sha1(repr(items).encode("utf-8", "replace")).hexdigest()[:12]


class Board:
    """A rendered board: the GIF, its size, its areas, whether any point is clickable, and when it was made."""
    __slots__ = ("gif", "w", "h", "areas", "anywhere", "when", "cost", "ms")

    def __init__(self, canvas, ms=0.0):
        self.gif = canvas.gif()
        self.w, self.h, self.areas, self.anywhere = canvas.w, canvas.h, canvas.areas, canvas.anywhere
        self.when = int(time.time())
        self.ms = ms
        self.cost = len(self.gif) + 64 * len(self.areas) + 200


class BoardCache:
    """The last few boards per table, least recently used out first: at most per_table a table and
    max_bytes in all (GIF bytes plus an allowance for the areas)."""

    def __init__(self, max_bytes=1500000, per_table=4):
        self.max_bytes, self.per_table = max_bytes, per_table
        self.items = OrderedDict()      # (game, table, seat, sel, digest) -> Board
        self.bytes = 0

    def get(self, key):
        b = self.items.get(key)
        if b is not None:
            self.items.move_to_end(key)
        return b

    def put(self, key, board):
        old = self.items.pop(key, None)
        if old is not None:
            self.bytes -= old.cost
        self.items[key] = board
        self.bytes += board.cost
        same = [k for k in self.items if k[:2] == key[:2]]
        for k in same[:max(0, len(same) - self.per_table)]:
            self.bytes -= self.items.pop(k).cost
        while self.bytes > self.max_bytes and len(self.items) > 1:
            _, b = self.items.popitem(last=False)
            self.bytes -= b.cost

    def render(self, key, draw):
        """The cached board for key, or draw() -> Canvas rendered and kept."""
        b = self.get(key)
        if b is None:
            t0 = time.perf_counter()
            canvas = draw()
            b = Board(canvas)
            b.ms = (time.perf_counter() - t0) * 1000
            self.put(key, b)
        return b
