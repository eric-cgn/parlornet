"""Draws the game server's own pictures into adapters/img/: an icon for each game that shows its play
(adapters/img/games/<type>.gif, 32 pixels square) and the picture of Any new computer, a laptop and a phone
(adapters/img/machines/any-new.gif). The other machines' pictures are photos from Wikimedia Commons
(adapters/img/CREDITS.tsv). Pure Python: shapes are sampled 4 by 4 times a pixel, each pixel takes its most
common color, and the GIF is written by ParlorNet's own encoder (adapters/board.py) with palette index 0
transparent. Every color is web-safe.

    python3 tools/site_icons.py         # writes the files and prints their paths
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters.board import encode_gif  # noqa: E402

OUT = os.path.join(ROOT, "adapters", "img")
SIZE = 32       # a game's icon, square
SS = 4          # samples a side in each pixel


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


# ---- shapes: each is a test of a point (x, y), in pixels from the top left ------------------------------

def rect(l, t, r, b):
    return lambda x, y: l <= x < r and t <= y < b


def rounded(l, t, r, b, rad):
    def inside(x, y):
        if not (l <= x < r and t <= y < b):
            return False
        dx = max(l + rad - x, 0, x - (r - rad))
        dy = max(t + rad - y, 0, y - (b - rad))
        return dx * dx + dy * dy <= rad * rad
    return inside


def disc(cx, cy, rad):
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= rad * rad


def polygon(pts):
    def inside(x, y):
        hit, j = False, len(pts) - 1
        for i in range(len(pts)):
            (xi, yi), (xj, yj) = pts[i], pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
        return hit
    return inside


def segment(x0, y0, x1, y1, width):
    def inside(x, y):
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / float(dx * dx + dy * dy)))
        px, py = x0 + t * dx - x, y0 + t * dy - y
        return px * px + py * py <= width * width / 4.0
    return inside


# 5 by 7 letters and digits, drawn whole pixels after the shapes
GLYPHS = {
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
}


class Picture:
    """A w by h picture: a palette (index 0 transparent), shapes painted in order, then glyphs."""

    def __init__(self, w, h, colors):
        self.w, self.h = w, h
        self.palette = [(255, 255, 255)] + [rgb(c) for c in colors]
        self.index = {c: i + 1 for i, c in enumerate(colors)}
        self.shapes, self.glyphs = [], []
        self.clip = None        # a shape outside which nothing is drawn

    def add(self, color, *shapes):
        for s in shapes:
            self.shapes.append((s, self.index[color]))
        return self

    def text(self, color, x, y, chars):
        for n, ch in enumerate(chars):
            self.glyphs.append((self.index[color], x + 6 * n, y, GLYPHS[ch]))
        return self

    def pixels(self):
        px = []
        for py in range(self.h):
            for pxl in range(self.w):
                count = {}
                for sy in range(SS):
                    for sx in range(SS):
                        x, y = pxl + (sx + 0.5) / SS, py + (sy + 0.5) / SS
                        i = 0
                        if self.clip is None or self.clip(x, y):
                            for inside, c in self.shapes:
                                if inside(x, y):
                                    i = c
                        count[i] = count.get(i, 0) + 1
                if count.get(0, 0) * 2 >= SS * SS:
                    px.append(0)
                else:
                    px.append(max((n, i) for i, n in count.items() if i)[1])
        for c, x, y, rows in self.glyphs:
            for dy, row in enumerate(rows):
                for dx, bit in enumerate(row):
                    if bit == "#":
                        px[(y + dy) * self.w + x + dx] = c
        return px

    def gif(self):
        """A GIF89a with index 0 transparent: the encoder's file with a graphic control extension."""
        data = encode_gif(self.w, self.h, self.palette, self.pixels())
        at = 13 + 3 * (1 << ((data[10] & 7) + 1))         # after the header and the color table
        assert data[at:at + 1] == b","
        return data[:at] + b"\x21\xf9\x04\x01\x00\x00\x00\x00" + data[at:]


def tile(*colors):
    """A game's icon: a 32-pixel picture whose first color fills a rounded square, the board, and nothing
    is drawn outside it."""
    p = Picture(SIZE, SIZE, list(colors)).add(colors[0], rounded(0, 0, SIZE, SIZE, 4))
    p.clip = rounded(0, 0, SIZE, SIZE, 4)
    return p


# ---- the games: each icon shows the play -------------------------------------------------------------

def reversi():
    """Black and white discs on the green board, a white one about to be flipped between two black."""
    p = tile("#006633", "#003300", "#000000", "#FFFFFF", "#999999")
    for v in (11, 21):
        p.add("#003300", rect(v - 0.5, 1, v + 0.5, 31), rect(1, v - 0.5, 31, v + 0.5))
    for cx, cy, white in ((6, 16, False), (16, 16, True), (26, 16, False), (16, 6, False), (26, 26, True),
                          (6, 26, True)):
        if white:
            p.add("#000000", disc(cx, cy, 4.3)).add("#FFFFFF", disc(cx, cy, 3.4))
        else:
            p.add("#999999", disc(cx, cy, 4.3)).add("#000000", disc(cx, cy, 3.4))
    return p


def backgammon():
    """The points on the wooden table, white checkers stacked on one and black on another."""
    p = tile("#CC9966", "#990000", "#FFCC99", "#FFFFFF", "#000000", "#666666")
    for n, l in enumerate((1, 11, 21)):
        p.add("#990000" if n % 2 == 0 else "#FFCC99", polygon([(l, 32), (l + 10, 32), (l + 5, 17)]))
        p.add("#FFCC99" if n % 2 == 0 else "#990000", polygon([(l, 0), (l + 10, 0), (l + 5, 15)]))
    for cy in (27, 19):
        p.add("#000000", disc(6, cy, 4.4)).add("#FFFFFF", disc(6, cy, 3.5))
    for cy in (5, 13):
        p.add("#666666", disc(26, cy, 4.4)).add("#000000", disc(26, cy, 3.5))
    return p


def gridwords():
    """Four letter tiles on the board, spelling GRID."""
    p = tile("#336699", "#996633", "#FFCC66", "#000000")
    for l, t in ((2, 2), (17, 2), (2, 17), (17, 17)):
        p.add("#996633", rect(l, t, l + 13, t + 13)).add("#FFCC66", rect(l, t, l + 12, t + 12))
    p.text("#000000", 5, 4, "G").text("#000000", 20, 4, "R").text("#000000", 5, 19, "I").text("#000000", 20, 19, "D")
    return p


def cannonfire():
    """A cannon on a hill, and its shell's arc through the sky toward the next hill."""
    p = tile("#99CCFF", "#339933", "#333333", "#000000", "#FF6600")
    p.add("#339933", polygon([(0, 32), (0, 24), (5, 21), (11, 22), (15, 27), (20, 25), (26, 20), (32, 22),
                              (32, 32)]))
    p.add("#333333", segment(7, 20, 12, 15, 3.2), disc(7, 20.5, 3))
    for t in range(1, 8):           # the arc: from the muzzle up over the middle and down to the right
        x = 12 + t * 2.0
        y = 15 - 1.45 * (t * 2.0) + 0.085 * (t * 2.0) ** 2
        p.add("#000000", disc(x, y, 0.75))
    p.add("#FF6600", disc(27, 11.5, 2.4)).add("#000000", disc(27, 11.5, 1.6))
    return p


def dotsandboxes():
    """Dots, the lines drawn between them, and one box closed and claimed."""
    p = tile("#CCFFFF", "#FF9999", "#003399", "#000000")
    p.add("#FF9999", rect(6, 6, 16, 16))
    for x0, y0, x1, y1 in ((6, 6, 16, 6), (6, 16, 16, 16), (6, 6, 6, 16), (16, 6, 16, 16), (16, 6, 26, 6),
                           (6, 16, 6, 26), (16, 16, 26, 16)):
        p.add("#003399", segment(x0, y0, x1, y1, 2))
    for x in (6, 16, 26):
        for y in (6, 16, 26):
            p.add("#000000", disc(x, y, 2))
    return p


def fiveinarow():
    """Five black stones in a line across the grid, with two white stones."""
    p = tile("#FFCC66", "#996633", "#000000", "#FFFFFF", "#666666")
    for v in (4, 10, 16, 22, 28):
        p.add("#996633", rect(v - 0.5, 1, v + 0.5, 31), rect(1, v - 0.5, 31, v + 0.5))
    for cx, cy in ((10, 10), (22, 22)):
        p.add("#666666", disc(cx, cy, 2.9)).add("#FFFFFF", disc(cx, cy, 2.2))
    for n in range(5):
        p.add("#000000", disc(4 + 6 * n, 28 - 6 * n, 2.9))
    return p


def mancala():
    """The wooden board: two rows of pits with seeds in them, and a store at each end."""
    p = tile("#996633", "#663300", "#FFFFCC", "#FFCC00")
    p.add("#663300", rounded(1.5, 4, 5, 28, 1.7), rounded(27, 4, 30.5, 28, 1.7))
    for cx in (9.5, 16, 22.5):
        for cy in (10.5, 21.5):
            p.add("#663300", disc(cx, cy, 3.1))
    seeds = [(9.5, 10.5), (15, 10), (17, 11), (22.5, 10.5), (8.5, 21), (10.5, 22), (16, 21.5), (21.5, 22),
             (23.5, 21), (3.25, 9), (3.25, 13), (28.75, 15), (28.75, 19), (28.75, 23)]
    for n, (x, y) in enumerate(seeds):
        p.add("#FFFFCC" if n % 2 else "#FFCC00", disc(x, y, 0.9))
    return p


def checkers():
    """A corner of the board with a black checker and a red king about to jump it."""
    p = tile("#FFCC99", "#663300", "#000000", "#999999", "#CC0000", "#FFCC00")
    for r in range(4):
        for c in range(4):
            if (r + c) % 2:
                p.add("#663300", rect(c * 8, r * 8, c * 8 + 8, r * 8 + 8))
    for cx, cy in ((12, 12), (20, 4)):
        p.add("#999999", disc(cx, cy, 3.6)).add("#000000", disc(cx, cy, 2.8))
    p.add("#000000", disc(20, 20, 3.6)).add("#CC0000", disc(20, 20, 2.8)).add("#FFCC00", disc(20, 20, 1.1))
    return p


def navalcombat():
    """A gray ship on the sea's grid with a red hit on it, and white pegs for misses."""
    p = tile("#0066CC", "#3399FF", "#999999", "#333333", "#FF0000", "#FFFFFF")
    for v in (8, 16, 24):
        p.add("#3399FF", rect(v - 0.5, 1, v + 0.5, 31), rect(1, v - 0.5, 31, v + 0.5))
    p.add("#333333", polygon([(3, 12), (24, 12), (30, 16), (24, 20), (3, 20)]))
    p.add("#999999", polygon([(4, 13), (23.5, 13), (28, 16), (23.5, 19), (4, 19)]))
    p.add("#333333", rect(9, 10, 16, 13))
    p.add("#FF0000", segment(17, 14, 21, 18, 1.6), segment(21, 14, 17, 18, 1.6))
    p.add("#FFFFFF", disc(5, 4, 1.8), disc(26, 27, 1.8), disc(12, 27, 1.8))
    return p


def rummytile():
    """Three numbered tiles laid down as a set: the same number in three colors."""
    p = tile("#339966", "#999966", "#FFFFCC", "#CC0000", "#003399", "#000000")
    for l in (2, 12, 22):
        p.add("#999966", rounded(l, 7, l + 9, 25, 1.5)).add("#FFFFCC", rounded(l, 7, l + 8, 24, 1.5))
    for l, color in ((2, "#CC0000"), (12, "#003399"), (22, "#000000")):
        p.text(color, l + 2, 12, "7")
    return p


def any_new():
    """Any new computer: a laptop with a game on its screen and a phone in front of it."""
    p = Picture(56, 40, ["#333333", "#CCCCCC", "#999999", "#006633", "#FFFFFF", "#000000", "#66CCFF"])
    p.add("#333333", rounded(6, 2, 44, 28, 2))
    p.add("#006633", rect(9, 5, 41, 25))
    for cx, cy, color in ((21, 11, "#FFFFFF"), (29, 11, "#000000"), (21, 19, "#000000"), (29, 19, "#FFFFFF")):
        p.add(color, disc(cx, cy, 3))
    p.add("#999999", polygon([(4, 28), (46, 28), (51, 35), (-1, 35)]))
    p.add("#CCCCCC", polygon([(5, 28.5), (45, 28.5), (49, 33.5), (1, 33.5)]))
    p.add("#333333", rounded(41, 14, 55, 40, 2))
    p.add("#66CCFF", rect(43, 17, 53, 36))
    p.add("#006633", rect(44, 22, 52, 30))
    p.add("#FFFFFF", disc(46, 24, 1.6)).add("#000000", disc(50, 28, 1.6))
    return p


GAMES = {"reversi": reversi, "backgammon": backgammon, "gridwords": gridwords, "cannonfire": cannonfire,
         "dotsandboxes": dotsandboxes, "fiveinarow": fiveinarow, "mancala": mancala, "checkers": checkers,
         "navalcombat": navalcombat, "rummytile": rummytile}


def drawings():
    """{path under adapters/img/: GIF bytes} of every picture drawn here."""
    out = {"games/%s.gif" % t: draw().gif() for t, draw in GAMES.items()}
    out["machines/any-new.gif"] = any_new().gif()
    return out


if __name__ == "__main__":
    for rel, data in drawings().items():
        path = os.path.join(OUT, rel)
        with open(path, "wb") as f:
            f.write(data)
        print(path)
