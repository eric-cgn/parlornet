"""Draws the docs' diagrams: each docs/diagrams/NAME.svg becomes static/img/diagram-NAME.gif.

    python3 tools/diagrams.py           # every diagram
    python3 tools/diagrams.py serial    # docs/diagrams/serial.svg only

The SVG is the source, edited as text and committed beside the docs. The GIF is what the docs show, on GitHub
and on the docs site alike, because every browser the docs site serves shows a GIF, back to Mac Mosaic and
Netscape 1.1, and none of the period ones shows an SVG. Both are committed: the docs site's build only copies
the GIFs, so it needs neither of the programs below.

Each diagram is rendered at its own size by rsvg-convert (librsvg) and mapped by ImageMagick, without dithering,
to PALETTE: 16 web-safe colors (docs/site.md, Palette), so it shows the same on a 256-color screen and stays
small. Keep a diagram at most 480 pixels wide, so it fits a period screen, with its text at 11 pixels or more,
in black on white, ivory or light felt. Its <title> says what it shows. The tests check the GIFs against these
rules (tests/test_docs_site.py, Diagrams).

The fonts come from the machine that renders: the SVGs ask for Helvetica, then Arial, then any sans-serif, so a
GIF drawn on another machine may differ by a pixel here and there. Look at each GIF before committing it.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "diagrams")
OUT = os.path.join(ROOT, "static", "img")
MAX_WIDTH = 480

# White and black, the grays, ivory and light felt (the fills) and the steps between them and black (the edges
# of text on them), and felt green: all web-safe.
PALETTE = ["#FFFFFF", "#000000", "#333333", "#666666", "#999999", "#CCCCCC",
           "#FFFFCC", "#CCCC99", "#999966", "#666633", "#333300",
           "#99CC99", "#669966", "#336633", "#003300", "#006633"]


def gif_name(name):
    return "diagram-%s.gif" % name


def sources():
    """The diagram names, from docs/diagrams/*.svg."""
    return sorted(f[:-4] for f in os.listdir(SRC) if f.endswith(".svg"))


def svg_size(path):
    """(width, height) from the SVG's root element."""
    with open(path, encoding="utf-8") as f:
        root = re.search(r"<svg\b[^>]*>", f.read()).group(0)
    return tuple(int(re.search(r'\b%s="(\d+)"' % k, root).group(1)) for k in ("width", "height"))


def magick():
    for cmd in (["magick"], ["convert"]):
        if shutil.which(cmd[0]):
            return cmd
    raise SystemExit("ImageMagick (magick or convert) is needed")


def draw(name, tmp):
    svg = os.path.join(SRC, name + ".svg")
    w, _ = svg_size(svg)
    if w > MAX_WIDTH:
        raise SystemExit("%s is %d pixels wide; keep it to %d" % (svg, w, MAX_WIDTH))
    if not shutil.which("rsvg-convert"):
        raise SystemExit("rsvg-convert (librsvg) is needed")
    png = os.path.join(tmp, name + ".png")
    palette = os.path.join(tmp, "palette.gif")
    subprocess.run(["rsvg-convert", "--background-color", "#FFFFFF", "-o", png, svg], check=True)
    subprocess.run(magick() + ["-size", "1x1"] + ["xc:" + c for c in PALETTE] + ["+append", palette], check=True)
    out = os.path.join(OUT, gif_name(name))
    subprocess.run(magick() + [png, "+dither", "-remap", palette, "-strip", out], check=True)
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or sources()
    with tempfile.TemporaryDirectory() as tmp:
        for n in names:
            print(draw(n, tmp))
