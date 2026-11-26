"""ParlorNet's title as the telnet extension's title page: PARLOR over NET in block letters, 80x24.

The letters are ParlorNet's title letters, the same grid of blocks as the 8-bit clients' title screen
(art/splash.txt in parlornet-8bit; tests/test_telnet_title.py checks the two agree when that checkout is
at hand). A block is two characters across and one line down, which is about square on a terminal. In the
ANSI modes each row of blocks has a color of its own, warm for PARLOR and cool for NET, as on the
Commodore 64, and the blocks are drawn as the C64 draws them where the mode allows: a square with a thin
dark gap along its right and bottom edges, so that blocks side by side read as separate squares.

- `utf8` draws a block as a space and U+2595 (right one eighth block) in reverse video and underlined, with
  the row's color from the 256 colors as the foreground. Reverse video makes the cell the row's color and
  the eighth block and the underline the screen's background color: those are the gaps on the right and the
  bottom. The last block of a run is two spaces, since the empty cell beside it is a gap already. The
  colors are the nearest of the 256 to the C64's.
- `pc` draws a block as a full block and a left half block (CP437 0xDB 0xDD), a gap on the right only,
  since CP437 has no thin line along a cell's bottom.
- `ansi` draws a block as `##`, and `plain` as `##` with no color.

`pc` and `ansi` color with the 16 ANSI foreground colors only, since most terminals there have no bright
backgrounds. The page is made to be cheap on a slow line, from about 1.2 KB to 1.7 KB: escape codes only
where the color changes, and a cursor move over a run of spaces. page() gives the text; telnet.py sends it
in the display mode's character set.
"""
import re

from .webstyle import TAGLINE

# The title's letters, as in art/splash.txt: rows of blocks, '#' filled.
LETTERS = """
letter P
####.
#####
##.##
##.##
#####
####.
##...
##...
##...

letter A
.###.
#####
##.##
##.##
#####
#####
##.##
##.##
##.##

letter R
####.
#####
##.##
##.##
####.
#####
##.##
##.##
##.##

letter L
##...
##...
##...
##...
##...
##...
##...
#####
.####

letter O
.###.
#####
##.##
##.##
##.##
##.##
##.##
#####
.###.

letter N
##..##
###.##
######
######
##.###
##..##
##..##
##..##
##..##

letter E
#####
#####
##...
##...
####.
####.
##...
#####
#####

letter T
######
######
..##..
..##..
..##..
..##..
..##..
..##..
..##..
"""

COLS, ROWS = 80, 24
GAP = 1             # blocks between letters
CAPTION = TAGLINE
PRESS = "Press Enter"
# A color a row of blocks, as the C64 has them: yellow, yellow, light red, light red, orange, orange, red,
# red, brown; white, light green, cyan, cyan, light blue, light blue, purple, purple, blue. WARM and COOL are
# the 16 ANSI foreground colors (9x is the bright 3x; orange and brown are both 33). WARM256 and COOL256 are
# the 256 colors nearest the C64's by CIEDE2000, with purple the violet of nearly equal distance (165) rather
# than magenta (164), for the C64's hue.
WARM = (93, 93, 91, 91, 33, 33, 31, 31, 33)
COOL = (97, 92, 96, 96, 94, 94, 35, 35, 34)
WARM256 = (227, 227, 210, 210, 166, 166, 131, 131, 94)
COOL256 = (231, 120, 87, 87, 105, 105, 165, 165, 21)
GREY, WHITE = 37, 97
# A block in each mode, two characters; in utf8 "\0" is a space that paints (reverse video), kept apart
# from the spaces that a cursor move skips.
BLOCK = {"plain": "##", "ansi": "##", "pc": "\u2588\u258c", "utf8": "\0\u2595"}


def parse(text):
    """art/splash.txt's format -> {letter: [row of blocks, ...]}, each row a string of '#' and '.'."""
    out, lines, i = {}, [ln.strip() for ln in text.splitlines()], 0
    while i < len(lines):
        if not lines[i].startswith("letter "):
            i += 1
            continue
        name, rows = lines[i].split()[1], []
        i += 1
        while i < len(lines) and lines[i]:
            rows.append(lines[i])
            i += 1
        out[name] = rows
    return out


GRID = parse(LETTERS)


def word_rows(word):
    """A word's rows of blocks, the letters GAP blocks apart: each row a string of '#' and '.'."""
    return [("." * GAP).join(GRID[c][r] for c in word) for r in range(len(GRID[word[0]]))]


def sgr(color):
    """The escape for a foreground color: reset, then the color. A bright one is bold and the plain color, as
    ANSI.SYS and the VT100 class know it."""
    return "\x1b[0;1;%dm" % (color - 60) if color >= 90 else "\x1b[0;%dm" % color


def squeeze(line, always=False):
    """A line for an ANSI screen: each run of spaces longer than its cursor move becomes the move (CUF), or
    every run (always), where a space would paint."""
    out, i = [], 0
    while i < len(line):
        if line[i] != " ":
            out.append(line[i])
            i += 1
            continue
        j = i
        while j < len(line) and line[j] == " ":
            j += 1
        move = "\x1b[%dC" % (j - i)
        out.append(move if always or len(move) < j - i else line[i:j])
        i = j
    return "".join(out)


def ink(mode, color, first):
    """The escape that turns a row of blocks to its color: the 16-color foreground in pc and ansi; in utf8
    the 256-color foreground, after reverse video and underline (first) or keeping them."""
    if mode != "utf8":
        return sgr(color)
    return "\x1b[%s38;5;%dm" % ("0;4;7;" if first else "", color)


def page(mode):
    """The title page for a display mode (pc, utf8, ansi or plain) as text, lines ending CR LF, the last
    ("Press Enter", on the 24th line) with none. The ANSI modes clear the screen first, and a row's color
    holds from row to row until it changes. A block is BLOCK[mode]; the cursor moves over the gaps, which in
    utf8 must not paint, since reverse video is on."""
    color = mode != "plain"
    utf8 = mode == "utf8"
    lines, cur = [""], None
    for word, bands in (("PARLOR", WARM256 if utf8 else WARM), ("NET", COOL256 if utf8 else COOL)):
        rows = word_rows(word)
        pad = " " * ((COLS - 2 * len(rows[0])) // 2)
        for r, row in enumerate(rows):
            text = (pad + "".join(BLOCK[mode] if b == "#" else "  " for b in row)).rstrip()
            if utf8:                # the last block of a run has the gap beside it already
                text = re.sub("\u2595(?!\0)", "\0", text)
            if color:
                lead = len(text) - len(text.lstrip())
                change = ink(mode, bands[r], cur is None) if bands[r] != cur else ""
                cur = bands[r]
                text = squeeze(text[:lead], always=utf8) + change + squeeze(text[lead:], always=utf8)
            lines.append(text.replace("\0", " "))
        lines.append("")
    while len(lines) < ROWS - 3:
        lines.append("")
    for text, tint in ((CAPTION, GREY), ("", None), (PRESS, WHITE)):
        pad = " " * ((COLS - len(text)) // 2)
        lines.append((squeeze(pad) + sgr(tint) + text if color else pad + text) if text else "")
    assert len(lines) == ROWS
    out = "\r\n".join(lines)
    return "\x1b[0m\x1b[2J\x1b[H" + out + "\x1b[0m" if color else out
