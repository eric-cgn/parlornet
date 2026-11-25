"""Draws ParlorNet Play's PNG icons from the same shapes as adapters/play/icon.svg: 180 (the Home Screen icon
of iPhone and iPad), 192 and 512 (the web app manifest). Pure Python: rounded squares and discs, with 4 by 4
samples a pixel for smooth edges, written as PNG with zlib.

    python3 tools/play_icons.py         # writes adapters/play/icon-180.png, icon-192.png, icon-512.png
"""
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "adapters", "play")
SIZES = (180, 192, 512)
SS = 4


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


# icon.svg, in its 32 by 32 units: (kind, shape, color), drawn in order
SHAPES = [("rect", (0, 0, 32, 32, 7), rgb("#1f5c46")), ("rect", (3, 3, 26, 26, 4), rgb("#277255")),
          ("disc", (11, 11, 5), rgb("#f4efe4")), ("disc", (21, 21, 5), rgb("#f4efe4")),
          ("disc", (21, 11, 5), rgb("#191c20")), ("disc", (11, 21, 5), rgb("#191c20"))]


def inside(kind, s, x, y):
    if kind == "disc":
        cx, cy, r = s
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    l, t, w, h, r = s
    if not (l <= x <= l + w and t <= y <= t + h):
        return False
    dx = max(l + r - x, 0, x - (l + w - r))
    dy = max(t + r - y, 0, y - (t + h - r))
    return dx * dx + dy * dy <= r * r


def draw(size):
    """The icon at size pixels; at 180, the Home Screen's, a full square (iOS rounds the corners itself)."""
    shapes = SHAPES if size != 180 else [("rect", (0, 0, 32, 32, 0), SHAPES[0][2])] + SHAPES[1:]
    k = 32.0 / size
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    x, y = (px + (sx + 0.5) / SS) * k, (py + (sy + 0.5) / SS) * k
                    col = None
                    for kind, s, c in shapes:
                        if inside(kind, s, x, y):
                            col = c
                    if col:
                        acc[0] += col[0]
                        acc[1] += col[1]
                        acc[2] += col[2]
                        acc[3] += 255
            n = SS * SS
            a = acc[3] // n
            row += bytes([acc[0] * 255 // acc[3] if acc[3] else 0, acc[1] * 255 // acc[3] if acc[3] else 0,
                          acc[2] * 255 // acc[3] if acc[3] else 0, a])
        rows.append(bytes(row))
    return png(size, size, b"".join(rows))


def png(w, h, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    for size in SIZES:
        path = os.path.join(OUT, "icon-%d.png" % size)
        with open(path, "wb") as f:
            f.write(draw(size))
        print(path)
