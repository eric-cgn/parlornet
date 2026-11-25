"""Draws the docs site's rating marks as small GIFs with a transparent background: a gold, a silver and a bronze
medal (a disc on a ribbon, with its initial, G, S or B, so a 1-bit screen tells them apart without color) and
the red "no" sign for a program that cannot play on a parlor. Pure Python, written with ParlorNet's own GIF
encoder (adapters/board.py) and a transparency extension. Every color is web-safe.

    python3 tools/medal_icons.py        # writes static/img/medal-gold.gif, medal-silver.gif,
                                        # medal-bronze.gif and unsupported.gif

The pages show each one with its word as ALT text (adapters/clients.py, MEDALS), so a text browser reads
"Gold" where a graphical one shows the medal.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters.board import encode_gif  # noqa: E402

OUT = os.path.join(ROOT, "static", "img")
SIZE = 16
SS = 4                                  # samples a side in each pixel


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


# The medals' colors: (rim and letter, body, light), all web-safe.
METALS = {"gold": ("#996600", "#FFCC00", "#FFFF99"),
          "silver": ("#666666", "#CCCCCC", "#FFFFFF"),
          "bronze": ("#663300", "#CC6633", "#FF9966")}
RIBBON = ("#003399", "#CC0000")          # the ribbon's two halves, navy and red
NO_RED, NO_WHITE = "#CC0000", "#FFFFFF"

# 3 by 5 letters, drawn in the medal's middle
LETTERS = {"gold": [".##", "#..", "#.#", "#.#", ".##"],
           "silver": [".##", "#..", ".#.", "..#", "##."],
           "bronze": ["##.", "#.#", "##.", "#.#", "##."]}


def render(shade):
    """A SIZE by SIZE picture from shade(x, y) -> palette index (0 transparent) at a point, by the most common
    index among SS by SS samples in each pixel; a pixel half or more transparent stays transparent."""
    px = []
    for py in range(SIZE):
        for pxl in range(SIZE):
            count = {}
            for sy in range(SS):
                for sx in range(SS):
                    i = shade(pxl + (sx + 0.5) / SS, py + (sy + 0.5) / SS)
                    count[i] = count.get(i, 0) + 1
            if count.get(0, 0) * 2 >= SS * SS:
                px.append(0)
            else:
                px.append(max((n, i) for i, n in count.items() if i)[1])
    return px


def medal(metal):
    rim, body, light = METALS[metal]
    palette = [(255, 255, 255), rgb(rim), rgb(body), rgb(light), rgb(RIBBON[0]), rgb(RIBBON[1])]
    cx, cy, r = 7.5, 10.4, 5.4

    def shade(x, y):
        d2 = (x - cx) ** 2 + (y - cy) ** 2
        if d2 <= r * r:
            if d2 >= (r - 1.1) ** 2:
                return 1                                    # the rim
            if (x - (cx - 1.6)) ** 2 + (y - (cy - 1.6)) ** 2 <= 2.2 ** 2:
                return 3                                    # the light, upper left
            return 2
        if y < cy - r + 1.2 and 4.5 <= x < 11.5:            # the ribbon, above the disc
            return 4 if x < 8 else 5
        return 0
    px = render(shade)
    for row, bits in enumerate(LETTERS[metal]):             # the initial, in the rim's color
        for col, b in enumerate(bits):
            if b == "#":
                px[(8 + row) * SIZE + 6 + col] = 1
    return gif(palette, px)


def no_sign():
    palette = [(255, 255, 255), rgb(NO_RED), rgb(NO_WHITE)]
    c, r, ring = 7.5, 7.6, 2.0

    def shade(x, y):
        dx, dy = x - c, y - c
        d2 = dx * dx + dy * dy
        if d2 > r * r:
            return 0
        if d2 >= (r - ring) ** 2:
            return 1                                        # the ring
        if abs(dx - dy) <= 1.4:                             # the bar, upper left to lower right
            return 1
        return 2
    return gif(palette, render(shade))


def gif(palette, px):
    """A GIF89a with palette index 0 transparent: the encoder's file with a graphic control extension."""
    data = encode_gif(SIZE, SIZE, palette, px)
    at = 13 + 3 * (1 << ((data[10] & 7) + 1))             # after the header and the color table
    assert data[at:at + 1] == b","
    gce = b"\x21\xf9\x04\x01\x00\x00\x00\x00"                 # transparent color: index 0
    return data[:at] + gce + data[at:]


ICONS = {"medal-gold.gif": lambda: medal("gold"), "medal-silver.gif": lambda: medal("silver"),
         "medal-bronze.gif": lambda: medal("bronze"), "unsupported.gif": no_sign}


if __name__ == "__main__":
    for name, draw in ICONS.items():
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(draw())
        print(path)
