"""ParlorNet's title as the telnet extension's title page: PARLOR over NET in solid block letters, 80x24.

The letters are ParlorNet's title letters, the same grid of blocks as the 8-bit clients' title screen
(art/splash.txt in parlornet-8bit; tests/test_telnet_title.py checks the two agree when that checkout is
at hand). A block is two characters across and one line down. In the ANSI modes each row of blocks has a
color of its own, warm for PARLOR and cool for NET, as on the Commodore 64. `pc` draws the blocks as full
block characters, `utf8` as spaces on the row's color (a full block is 3 bytes in UTF-8), and `ansi` and
`plain` as `#`.

The page is made to be cheap on a slow line, about 1.2 to 1.3 KB in every mode: escape codes only where the
color changes, and in the ANSI modes a cursor move over a run of spaces. page() gives the text; telnet.py
sends it in the display mode's character set.
"""
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
# A color a row of blocks (ANSI foreground: 9x is the bright 3x): yellow, light red, brown, red; white, light
# green, cyan, light blue, magenta, blue.
WARM = (93, 93, 91, 91, 33, 33, 31, 31, 33)
COOL = (97, 92, 96, 96, 94, 94, 35, 35, 34)
GREY, WHITE = 37, 97


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


def sgr(color, back=False):
    """The escape for a color: reset, then the color. A bright foreground is bold and the plain color, as
    ANSI.SYS and the VT100 class know it; a background (back) is 4x, or 10x when bright."""
    if back:
        return "\x1b[0;%dm" % (color + 10)
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


def page(mode):
    """The title page for a display mode (pc, utf8, ansi or plain) as text, lines ending CR LF, the last
    ("Press Enter", on the 24th line) with none. The ANSI modes clear the screen first. A block is "##" in
    plain and ansi and two full blocks in pc; in utf8 it is two spaces in the row's color as the background
    (a full block is 3 bytes there), and the cursor moves over the gaps."""
    color = mode != "plain"
    back = mode == "utf8"
    block = {"pc": "\u2588", "utf8": "\0"}.get(mode, "#") * 2
    lines, cur = [""], None
    for word, bands in (("PARLOR", WARM), ("NET", COOL)):
        rows = word_rows(word)
        pad = " " * ((COLS - 2 * len(rows[0])) // 2)
        for r, row in enumerate(rows):
            text = (pad + "".join(block if b == "#" else "  " for b in row)).rstrip()
            if color:
                lead = len(text) - len(text.lstrip())
                ink = sgr(bands[r], back) if bands[r] != cur else ""
                cur = bands[r]
                text = squeeze(text[:lead]) + ink + squeeze(text[lead:], always=back)
            lines.append(text.replace("\0", " "))
        lines.append("")
    while len(lines) < ROWS - 3:
        lines.append("")
    for text, ink in ((CAPTION, GREY), ("", None), (PRESS, WHITE)):
        pad = " " * ((COLS - len(text)) // 2)
        lines.append((squeeze(pad) + sgr(ink) + text if color else pad + text) if text else "")
    assert len(lines) == ROWS
    out = "\r\n".join(lines)
    return "\x1b[0m\x1b[2J\x1b[H" + out + "\x1b[0m" if color else out
