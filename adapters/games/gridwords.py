"""Grid Words (gridwords/1, docs/types/gridwords-1.md): the web board and the telnet screens.

A crossword tile game for 2-4 players with a hidden rack each. There is no
legal-move list (flag nomoves): a play is typed or built as one move,
PLAY x y H|V letters, and CHK previews it. The board geometry (premium
squares, tile values) comes from the view's G lines; the standard layout
below is only the fallback.
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_BRIGHT, A_DIM, A_GRID, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS,
                      ROWS, SQUARE_RE, AnsiGame, Canvas, GameBase, PlainGame, clock_text, err_text, fmt_secs, rule,
                      status_bar, title_bar, turn_text)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture, decode_gif, encode_gif
from .. import palette as PAL
from ..common import Refused
from ..web import a, board_html, esc, hidden_fields

# ---- what the game is ------------------------------------------------------------

KEY = "gridwords/1"
NAME = "Grid Words"
ORDER = 3
PLAYERS = "2-4"
ABOUT = (
    "Two to four players build interlocking words on a square board from letter tiles drawn from a bag, "
    "the way a crossword fills in. Each tile is worth some points, and colored squares double or triple "
    "a letter or a whole word; when the tiles run out, or nobody can play, the highest score wins.",
    ["English rules: a 15 by 15 board with the usual pattern of premium squares, 100 tiles (two of them "
     "blanks that stand for any letter, worth nothing) and the usual letter values. Each player holds "
     "seven tiles, hidden from the others, and draws back up to seven after each turn.",
     "A turn places tiles in one row or column to make a word that joins those already on the board (the "
     "first word covers the center square), exchanges tiles with the bag (while it holds at least seven), "
     "or passes. Every word a play makes is scored, and using all seven tiles at once earns 50 more.",
     "Words are checked against the server's word list as they are played, so a wrong word is simply "
     "refused and there is no challenge round. The web site and telnet can preview a play's score first.",
     "The game ends when a player uses their last tile with the bag empty, or after two full rounds in "
     "which nobody plays a word. Each player then loses the points still on their rack, and a player who "
     "went out gains the others' total.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])

SIZE = 15
LETTERS = "ABCDEFGHIJKLMNO"


def _std_premiums():
    eighth = {"TW": [(1, 1), (8, 1)], "DW": [(2, 2), (3, 3), (4, 4), (5, 5), (8, 8)],
              "TL": [(6, 2), (6, 6)], "DL": [(4, 1), (7, 3), (8, 4), (7, 7)]}
    out = {}
    for kind, squares in eighth.items():
        for x, y in squares:
            for p, q in ((x, y), (y, x)):
                for px in (p, SIZE + 1 - p):
                    for py in (q, SIZE + 1 - q):
                        out[(px, py)] = kind
    return out


STD_PRM = _std_premiums()
STD_TIL = {c: (v, n) for c, v, n in (
    ("A", 1, 9), ("B", 3, 2), ("C", 3, 2), ("D", 2, 4), ("E", 1, 12), ("F", 4, 2), ("G", 2, 3), ("H", 4, 2),
    ("I", 1, 9), ("J", 8, 1), ("K", 5, 1), ("L", 1, 4), ("M", 3, 2), ("N", 1, 6), ("O", 1, 8), ("P", 3, 2),
    ("Q", 10, 1), ("R", 1, 6), ("S", 1, 4), ("T", 1, 6), ("U", 1, 4), ("V", 4, 2), ("W", 4, 2), ("X", 8, 1),
    ("Y", 4, 2), ("Z", 10, 1), ("_", 0, 2))}


def sq_name(x, y):
    """1-based x, y -> 'h8'."""
    return "%s%d" % (chr(96 + x), y)


def dir_word(d):
    return "across" if d == "H" else "down"


# ---- typed letters -> a play (telnet and the web) ------------------------------------------
# A player types only the letters of their own rack, from an empty start square. The letters fill the
# empty squares in order, and the tiles already on the board between, just before and just after them join
# the word: `x` typed at i4 beside HE plays HEX. The core's move (PLAY x y H|V letters) writes the whole word
# from its first square, board letters included; lay_out makes it, typed_from goes back.

def letter_tokens(typed):
    """Typed letters -> one token per tile: 'A', or '_A' for a blank's letter."""
    out, blank = [], False
    for c in typed.upper():
        if c == "_":
            blank = True
            continue
        out.append(("_" if blank else "") + c)
        blank = False
    return out


def lay_out(tiles, x, y, d, typed, w=SIZE, h=SIZE):
    """Where a player's typed letters go from the start square x, y (across or down): they fill the empty
    squares in order, and the tiles on the board between, just before and just after them join the word.
    tiles is the view's {(x, y): letter}. -> (sx, sy, word, {(x, y): letter placed}), the whole word from its
    first square as the core's PLAY takes it. Raises Refused, in the player's words, when the start square
    already has a tile, no letter is typed, or the letters run off the board."""
    if (x, y) in tiles:
        raise Refused("There is already a tile on %s: start your word on an empty square" % sq_name(x, y))
    toks = letter_tokens(typed)
    if not toks or typed.endswith("_"):
        raise Refused("Type the letters from your rack, a blank as _ before its letter")
    dx, dy = (1, 0) if d == "H" else (0, 1)
    sx, sy = x, y
    while (sx - dx, sy - dy) in tiles:
        sx, sy = sx - dx, sy - dy
    word, placed, cx, cy = [], {}, sx, sy
    while (cx, cy) != (x, y):
        word.append(tiles[(cx, cy)].upper())
        cx, cy = cx + dx, cy + dy
    i = 0
    while i < len(toks):
        if not (1 <= cx <= w and 1 <= cy <= h):
            raise Refused("From %s %s, those letters run off the board" % (sq_name(x, y), dir_word(d)))
        if (cx, cy) in tiles:
            word.append(tiles[(cx, cy)].upper())
        else:
            word.append(toks[i])
            placed[(cx, cy)] = toks[i][-1]
            i += 1
        cx, cy = cx + dx, cy + dy
    while (cx, cy) in tiles:
        word.append(tiles[(cx, cy)].upper())
        cx, cy = cx + dx, cy + dy
    return sx, sy, "".join(word), placed


def typed_from(tiles, x, y, d, word):
    """A whole-word play (PLAY x y d word) as a player types it: (x, y of its first empty square, the letters
    that go on the empty squares), or None if it places no tile."""
    dx, dy = (1, 0) if d == "H" else (0, 1)
    start, letters = None, []
    for i, tok in enumerate(letter_tokens(word)):
        pos = (x + dx * i, y + dy * i)
        if pos not in tiles:
            start = start or pos
            letters.append(tok)
    return (start[0], start[1], "".join(letters)) if start else None


def described(move):
    """A move as the player sees it: 'HEX across from g4', 'an exchange of Q, V and a blank', 'a pass'."""
    f = move.split()
    if f[0] == "PLAY" and len(f) == 5:
        return "%s %s from %s" % (f[4].replace("_", ""), dir_word(f[3]), sq_name(int(f[1]), int(f[2])))
    if f[0] == "EXCH" and len(f) == 2:
        letters, blanks = [c for c in f[1] if c != "_"], f[1].count("_")
        parts = letters + ["a blank"] * blanks
        return "an exchange of " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])
    return "a pass" if f[0] == "PASS" else move


def exchange_refused(bag=None):
    """Why an exchange can't be made with too few tiles in the bag, in plain words."""
    if bag is None:
        return "You can't exchange now because the bag holds fewer than seven tiles."
    return "You can't exchange now. The bag holds %d tile%s, and an exchange needs at least seven." % (
        bag, "" if bag == 1 else "s")


def refusal(move, reason):
    """The core's reason for refusing a move -> what the player reads: 'Can't play HEX across from g4: ...'.
    The play is named as the board makes it, so the letters on the board that joined it show."""
    if move.startswith("EXCH") and reason.startswith("exchanging needs"):
        return exchange_refused()
    return "Can't play %s: %s." % (described(move), reason.rstrip("."))


# ---- the view and the events (docs/types/gridwords-1.md) ------------------------------

def parse_view(lines):
    """VUE lines for gridwords/1 -> dict. rows is True once the game has started (there is a W line)."""
    v = {"seq": 0, "rows": False, "turn": -1, "moves": [], "status": "", "clock": None,
         "w": SIZE, "h": SIZE, "ctr": (8, 8), "prm": dict(STD_PRM), "til": dict(STD_TIL),
         "tiles": {}, "rack": None, "counts": [], "bag": 0, "scores": [], "last": None}
    prm_seen = til_seen = False

    def put(lst, i, val):
        while len(lst) <= i:
            lst.append(0)
        lst[i] = val
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        try:
            if tag == "V" and len(f) > 2:
                v["seq"] = int(f[2])
            elif tag == "BRD":
                v["w"], v["h"] = int(f[1]), int(f[2])
            elif tag == "CTR":
                v["ctr"] = (int(f[1]), int(f[2]))
            elif tag == "PRM":
                if not prm_seen:
                    v["prm"], prm_seen = {}, True
                for i in range(2, len(f) - 1, 2):
                    v["prm"][(int(f[i]), int(f[i + 1]))] = f[1]
            elif tag == "TIL":
                if not til_seen:
                    v["til"], til_seen = {}, True
                v["til"][f[1]] = (int(f[2]), int(f[3]))
            elif tag == "X":
                for i in range(1, len(f) - 2, 3):
                    v["tiles"][(int(f[i]), int(f[i + 1]))] = f[i + 2]
            elif tag == "R":
                v["rack"] = f[1] if len(f) > 1 and f[1] != "-" else ""
            elif tag == "K":
                put(v["counts"], int(f[1]), int(f[2]))
            elif tag == "BAG":
                v["bag"] = int(f[1])
            elif tag == "T":
                put(v["scores"], int(f[1]), int(f[2]))
            elif tag == "W":
                v["turn"], v["rows"] = int(f[1]), True
            elif tag == "LAST" and len(f) == 7:
                v["last"] = (int(f[1]), int(f[2]), int(f[3]), f[4], f[5], int(f[6]))
            elif tag == "CLK":
                v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
            elif tag == "S":
                v["status"] = l[2:]
        except (ValueError, IndexError):
            continue
    return v


def _who(names, i):
    i = int(i)
    n = names[i] if 0 <= i < len(names) else "-"
    return n if n != "-" else "seat %d" % (i + 1)


def describe_event(text, names):
    """Grid Words' own events -> a line for the log ("" for those the log does not show)."""
    p = text.split()
    if not p:
        return None
    try:
        if p[0] == "PLAY" and len(p) == 7:
            return "%s plays %s at %s %s for %s" % (_who(names, p[1]), p[5], sq_name(int(p[2]), int(p[3])),
                                                    dir_word(p[4]), p[6])
        if p[0] in ("X", "DRAW", "TURN"):
            return ""
        if p[0] == "EXCH" and len(p) == 3:
            return "%s exchanges %s tile%s" % (_who(names, p[1]), p[2], "" if p[2] == "1" else "s")
        if p[0] == "PASS" and len(p) == 2:
            return "%s passes" % _who(names, p[1])
        if p[0] == "ADJ" and len(p) == 4:
            if p[3] == "-":
                return "%s goes out: %s for the tiles left on the other racks" % (_who(names, p[1]), p[2])
            return "%s: %s for %s left on the rack" % (_who(names, p[1]), p[2], p[3])
    except ValueError:
        return None
    return None


# ---- scoring a play locally (the web page's preview) -----------------------------------

def preview(v, rack, x, y, d, text):
    """Score a play on the view's board as the server would, except that the words are not looked up.
    -> (score, [words]) or (None, reason)."""
    letters, blank = [], False
    for c in text.upper():
        if c == "_" and not blank:
            blank = True
        elif "A" <= c <= "Z":
            letters.append((c, blank))
            blank = False
        else:
            return None, "letters are A-Z, with _ before a blank's letter"
    if not letters or blank:
        return None, "type the letters of the word"
    w, h, tiles = v["w"], v["h"], v["tiles"]

    def inside(px, py):
        return 1 <= px <= w and 1 <= py <= h
    dx, dy = (1, 0) if d == "H" else (0, 1)
    n = len(letters)
    if not inside(x, y) or not inside(x + dx * (n - 1), y + dy * (n - 1)):
        return None, "the word runs off the board"
    left = list(rack or "")
    placed = {}
    for i, (ch, bl) in enumerate(letters):
        pos = (x + dx * i, y + dy * i)
        if pos in tiles:
            if tiles[pos].upper() != ch:
                return None, "%s already holds %s" % (sq_name(*pos), tiles[pos].upper())
            continue
        need = "_" if bl else ch
        if need not in left:
            return None, "your rack has no %s" % ("blank" if bl else ch)
        left.remove(need)
        placed[pos] = ch.lower() if bl else ch
    if not placed:
        return None, "a play needs at least one tile from your rack"
    if (x - dx, y - dy) in tiles:
        return None, "the tiles just before %s are part of the word too" % sq_name(x, y)
    if (x + dx * n, y + dy * n) in tiles:
        return None, ("the tiles just after %s are part of the word too"
                      % sq_name(x + dx * (n - 1), y + dy * (n - 1)))
    if not tiles:
        if v["ctr"] not in placed or n < 2:
            return None, "the first word covers the center square, two letters or more"
    elif len(placed) == n and not any((px + ax, py + ay) in tiles
                                      for px, py in placed for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1))):
        return None, "the word must join the words already on the board"
    board = dict(tiles)
    board.update(placed)
    runs = [(x, y, dx, dy)] if n >= 2 else []
    for px, py in placed:
        sx, sy = px, py
        while (sx - dy, sy - dx) in board:
            sx, sy = sx - dy, sy - dx
        if (sx + dy, sy + dx) in board or (sx, sy) != (px, py):
            runs.append((sx, sy, dy, dx))
    total, words = 0, []
    for sx, sy, wdx, wdy in runs:
        points, mult, word = 0, 1, ""
        cx, cy = sx, sy
        while (cx, cy) in board:
            c = board[(cx, cy)]
            word += c
            val = 0 if c.islower() else v["til"].get(c, (0, 0))[0]
            if (cx, cy) in placed:
                kind = v["prm"].get((cx, cy))
                val *= {"DL": 2, "TL": 3}.get(kind, 1)
                mult *= {"DW": 2, "TW": 3}.get(kind, 1)
            points += val
            cx, cy = cx + wdx, cy + wdy
        total += points * mult
        words.append(word)
    if not words:
        return None, "a play must make a word of two letters or more"
    if len(placed) == 7:
        total += 50
    return total, words


# ---- the web page (HTML 3.2) -----------------------------------------------------------

PLAY_RE = r"PLAY [0-9]{1,2} [0-9]{1,2} [HV] [A-Z_]{1,30}"
MOVE_RE = re.compile(r"^(?:%s|EXCH [A-Z_]{1,7}|PASS)\Z" % PLAY_RE, re.I)
SEL_RE = re.compile(r"^(?:[0-9]{1,2}\.[0-9]{1,2}\.[HV]|%s)\Z" % PLAY_RE, re.I)   # a start square, or a play to check
MOVE_FIELDS = ["sq", "dir", "letters"]     # the play form: start square (h8), direction (H or V), the letters
MOVE_FIELDS_VIEW = True                    # move_from_fields gets the table's view: the board's tiles join the word
WEB_CHECK = True                           # web_parts shows /check.htm's answer (v["check"])
START_RE = re.compile(r"^([a-o])(1[0-5]|[1-9])\Z", re.I)
WORD_LETTERS_RE = re.compile(r"^[A-Za-z_]{1,30}\Z")


def move_from_fields(values, v=None):
    """The play form's [start square, direction, letters] on the table's view v -> 'PLAY x y H|V WORD', the
    whole word as lay_out makes it: the letters are the player's own, from an empty start square, and the
    tiles already on the board join the word. None unless the square is a1 to o15, the direction H or V, and
    the letters 1 to 30 of A-Z and _; raises Refused (lay_out) for a start on a played square or letters
    that run off the board. Without a view, the board is taken as empty."""
    sq, d, letters = values
    m = START_RE.match(sq)
    if not m or d.upper() not in ("H", "V") or not WORD_LETTERS_RE.match(letters):
        return None
    x, y, d = ord(m.group(1).lower()) - 96, int(m.group(2)), d.upper()
    v = v or {}
    sx, sy, word, _ = lay_out(v.get("tiles") or {}, x, y, d, letters, v.get("w", SIZE), v.get("h", SIZE))
    return "PLAY %d %d %s %s" % (sx, sy, d, word)


CELL = 24
IMG = "/img/gridwords/"
# Every square and tile image is drawn with one palette of roles (adapters/palette.py works out each role's
# color from the player's seven): the images are made once in the default colors, and a player's own
# colors are the same images with another color table (images_for).
ROLES = ("grid", "board", "tw", "dw", "tl", "dl", "tile", "letter", "label_tw", "label_dw", "label_tl", "label_dl",
         "blank", "arrow", "points")
R = {r: i for i, r in enumerate(ROLES)}


def colour_table(pal):
    """A palette (adapters/palette.py) -> the images' color table, in ROLES order."""
    d = PAL.derived(pal)
    return [d[r] for r in ROLES]


PALETTE = colour_table(PAL.PRESETS[PAL.DEFAULT])
SQ_COLOUR = {"": R["board"], "TW": R["tw"], "DW": R["dw"], "TL": R["tl"], "DL": R["dl"], "ctr": R["dw"]}
SQ_INK = {"TW": R["label_tw"], "DW": R["label_dw"], "TL": R["label_tl"], "DL": R["label_dl"], "ctr": R["label_dw"]}
SQ_TEXT = {"TW": "3W", "DW": "2W", "TL": "3L", "DL": "2L"}

FONT = {  # 5x7
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
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
}
SMALL = {  # 3x5, for the values on the tiles
    "0": "111 101 101 101 111", "1": "010 110 010 010 111", "2": "111 001 111 100 111",
    "3": "111 001 011 001 111", "4": "101 101 111 001 001", "5": "111 100 111 001 111",
    "6": "111 100 111 101 111", "7": "111 001 001 001 001", "8": "111 101 111 101 111",
    "9": "111 101 111 001 111",
}


def _canvas(colour):
    px = [[colour] * CELL for _ in range(CELL)]
    for i in range(CELL):
        px[0][i] = px[i][0] = 0            # the grid line on the top and left
    return px


def _text(px, text, font, x0, y0, colour, scale=1):
    for ch in text:
        rows = font[ch].split()
        for r, bits in enumerate(rows):
            for c, b in enumerate(bits):
                if b == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            px[y0 + r * scale + sy][x0 + c * scale + sx] = colour
        x0 += (len(rows[0]) + 1) * scale


def _gif(px):
    return encode_gif(CELL, CELL, PALETTE, [c for row in px for c in row])


def _square(kind):
    """A square: its color, and a premium's name (3W, 2W, 3L, 2L) in the ink that reads on it; a word
    premium has a frame inside its edge as well, so the kinds differ in shape and not by color alone."""
    px = _canvas(SQ_COLOUR[kind])
    ink = SQ_INK.get(kind)
    if kind in ("TW", "DW", "ctr"):
        for i in range(3, CELL - 2):
            px[3][i] = px[CELL - 3][i] = px[i][3] = px[i][CELL - 3] = ink
    if kind in SQ_TEXT:
        _text(px, SQ_TEXT[kind], FONT, 7, 9, ink)
    elif kind == "ctr":                     # a star: the center square
        for i in range(-5, 6):
            px[12 + i][12] = px[12][12 + i] = ink
            if abs(i) <= 3:
                px[12 + i][12 + i] = px[12 + i][12 - i] = ink
    return _gif(px)


def _tile(letter, blank=False, value=None):
    """A tile: its letter (a blank's in the blank ink, with no value) and its value."""
    px = _canvas(R["tile"])
    if letter:
        _text(px, letter, FONT, 5, 4, R["blank"] if blank else R["letter"], scale=2)
    if value is not None:
        digits = str(value)
        _text(px, digits, SMALL, CELL - 2 - 4 * len(digits), CELL - 7, R["points"])
    return _gif(px)


def _arrow(d):
    px = _canvas(R["board"])
    ink = R["arrow"]
    for i in range(5, 19):
        for t in (11, 12):
            if d == "H":
                px[t][i] = ink
            else:
                px[i][t] = ink
    for k in range(1, 5):
        for t in (k, k + 1):
            if d == "H":
                px[12 - t][18 - k] = px[11 + t][18 - k] = ink
            else:
                px[18 - k][12 - t] = px[18 - k][11 + t] = ink
    return _gif(px)


def _swatch(pal):
    """A palette's preview for the colors page: an empty square, the four premiums and a tile, 6 squares."""
    table = colour_table(pal)
    squares = [_square(""), _square("TW"), _square("DW"), _square("TL"), _square("DL"), _tile("A", value=1)]
    imgs = [decode_gif(g) for g in squares]
    w = CELL * len(imgs) + 1
    rows = []
    for y in range(CELL + 1):
        row = []
        for img in imgs:
            row += list(img.pixels[min(y, CELL - 1) * CELL:(min(y, CELL - 1) + 1) * CELL]) if y < CELL \
                else [R["grid"]] * CELL
        row.append(R["grid"])
        rows += row
    return encode_gif(w, CELL + 1, table, rows)


def images():
    """Squares (plain, premiums, the center), the start arrows, the tiles A-Z, blanks a-z, a rack blank, and a
    preview of each preset's colors (pal-<preset>.gif, for the colors page)."""
    out = {IMG + "s%s.gif" % k.lower(): _square(k) for k in SQ_COLOUR}
    out[IMG + "goh.gif"], out[IMG + "gov.gif"] = _arrow("H"), _arrow("V")
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        out[IMG + "t%s.gif" % c.lower()] = _tile(c, value=STD_TIL[c][0])
        out[IMG + "b%s.gif" % c.lower()] = _tile(c, blank=True)
    out[IMG + "blank.gif"] = _tile("", value=0)
    for name, pal in PAL.PRESETS.items():
        out[IMG + "pal-%s.gif" % name] = _swatch(pal)
    return out


def recolour(gif, table):
    """A GIF of these images with another color table (the same size of table: ROLES, padded)."""
    n = 2 << (gif[10] & 7)
    flat = bytearray()
    for rgb in list(table) + [(0, 0, 0)] * (n - len(table)):
        flat += bytes(int(c) for c in rgb)
    return gif[:13] + bytes(flat[:3 * n]) + gif[13 + 3 * n:]


def swatch(value):
    """The preview GIF of a kept palette value (adapters/palette.py), or None when it is not one."""
    pal = PAL.decode(value)
    return _swatch(pal) if pal else None


_by_palette = {}        # PRF value -> its images function (images_for); the most recent few


def images_for(value):
    """The images function a board is drawn with in a player's colors (value: their kept gwcolors, or None):
    images itself for the default, else the same images with that palette's color table. Each has a
    cache_key, so adapters/board.py decodes it once."""
    pal = PAL.palette(value)
    code = PAL.encode(pal)
    if code == PAL.DEFAULT:
        return images
    fn = _by_palette.pop(code, None)
    if fn is None:
        table = colour_table(pal)

        def fn():
            return {p: recolour(g, table) for p, g in images().items() if "/pal-" not in p}
        fn.cache_key = "gridwords:" + code
    _by_palette[code] = fn
    while len(_by_palette) > 16:
        _by_palette.pop(next(iter(_by_palette)))
    return fn


def tile_name(c):
    """The image of a tile on the board or the rack: t (a letter), b (a blank played as a letter), blank."""
    if c == "_":
        return IMG + "blank.gif"
    return IMG + ("b" if c.islower() else "t") + c.lower() + ".gif"


def square_name(v, x, y):
    kind = "ctr" if (x, y) == v["ctr"] else v["prm"].get((x, y), "")
    return IMG + "s%s.gif" % kind.lower()


def parse_sel(sel):
    """sel -> (x, y, d, letters or None), or None."""
    if not sel:
        return None
    f = sel.replace(".", " ").split()
    if f[0].upper() == "PLAY":
        return int(f[1]), int(f[2]), f[3].upper(), f[4]
    return int(f[0]), int(f[1]), f[2].upper(), None


def start_of(v, sel):
    start = parse_sel(sel) if sel else None
    if start and not (1 <= start[0] <= v["w"] and 1 <= start[1] <= v["h"]):
        return None
    return start


def typed_start(v, start):
    """A start (start_of) as the player sees it: (x, y, d, the letters they type or None) from the first empty
    square, for a whole-word play picked by sel= (PLAY ..., after Check score); None for no start, or a bare
    start on a played square (a word starts on an empty square)."""
    if not start:
        return None
    x, y, d, word = start
    if word:
        t = typed_from(v["tiles"], x, y, d, word)
        return (t[0], t[1], d, t[2]) if t else None
    return None if (x, y) in v["tiles"] else start


def played_start(v, start):
    """The square name when a bare start (a click) is on a played square, else None."""
    if start and not start[3] and (start[0], start[1]) in v["tiles"]:
        return sq_name(start[0], start[1])
    return None


MARGIN, RACK_GAP = 16, 26


def board_image(v, seat, sel):
    """The board as one image: squares and tiles of images(), letters and numbers round it, the start
    arrow of the word being placed (sel), and under it the seat's own rack (in this seat's view only: a
    watcher's or another seat's view has no rack). On the player's turn every square is an area that
    starts a word there (again on the start square: the other direction); the page refuses a start on a
    played square, in words (play_forms). The arrow is on the first empty square of a picked play."""
    w, h = min(v["w"], SIZE), min(v["h"], SIZE)
    my_turn = seat >= 0 and v["turn"] == seat and "Game over" not in v["status"]
    start = typed_start(v, start_of(v, sel))
    rack = v["rack"] if seat >= 0 and v["rack"] is not None else None
    height = MARGIN + h * CELL + 1 + (RACK_GAP + CELL + 1 if rack is not None else 0) + 2
    grid = colour_table(PAL.palette(v.get("palette")))[R["grid"]]
    c = Picture(max(MARGIN + w * CELL + 2, 8 * CELL + 60), height, images=images_for(v.get("palette")))
    c.labels(MARGIN, MARGIN, CELL, w, h, (0, 0, 0))
    for y in range(1, h + 1):
        for x in range(1, w + 1):
            px, py = MARGIN + (x - 1) * CELL, MARGIN + (y - 1) * CELL
            t = v["tiles"].get((x, y))
            here = start and start[:2] == (x, y)
            if here and t is None:
                c.tile(IMG + "go%s.gif" % start[2].lower(), px, py)
            else:
                c.tile(tile_name(t) if t else square_name(v, x, y), px, py)
            if my_turn:
                d = ("V" if start[2] == "H" else "H") if here else "H"
                c.area("rect", (px, py, px + CELL - 1, py + CELL - 1), sq_name(x, y), "/table.htm",
                       sel="%d.%d.%s" % (x, y, d))
    c.fill(MARGIN + w * CELL, MARGIN, 1, h * CELL + 1, grid)
    c.fill(MARGIN, MARGIN + h * CELL, w * CELL + 1, 1, grid)
    if rack is not None:
        y0 = MARGIN + h * CELL + RACK_GAP
        c.text(2, y0 - 11, "Your rack", (0, 0, 0))
        for i, t in enumerate(rack):
            c.tile(tile_name(t), MARGIN + i * CELL, y0)
        if rack:
            c.fill(MARGIN + len(rack) * CELL, y0, 1, CELL + 1, grid)
            c.fill(MARGIN, y0 + CELL, len(rack) * CELL + 1, 1, grid)
    return c


def _hidden(link, path, tid):
    """The form's hidden fields: the table and what link() carries (the session for browsers without
    cookies, the action check x=, the display choices)."""
    return hidden_fields(link(path, t=tid))


def play_fields(sq, d, letters):
    """The play form's fields (MOVE_FIELDS): the start square, a direction select and the letters."""
    return ('Start <input name="sq" size="3" maxlength="3" value="%s"> <select name="dir">'
            '<option value="H"%s>across<option value="V"%s>down</select> letters '
            '<input name="letters" size="16" maxlength="30" value="%s">'
            % (esc(sq), " selected" if d != "V" else "", " selected" if d == "V" else "", esc(letters)))


def play_forms(v, tid, link, seat, start):
    """The forms under the board: play a word, check its score first (/check.htm), exchange tiles. The
    letters are the player's own, typed from an empty start square; the tiles on the board join the word
    when it is played (move_from_fields)."""
    played = played_start(v, start)
    start = typed_start(v, start)
    x, y, d = (start[0], start[1], start[2]) if start else ((v["ctr"][0], v["ctr"][1], "H") if not v["tiles"]
                                                            else (None, None, "H"))
    sq = sq_name(x, y) if x else ""
    letters = (start[3] or "") if start else ""
    if played:
        where = ("<b>There is already a tile on %s.</b> Click the empty square where your word starts "
                 "(click it again to go down), or type it (like h8)." % played)
    elif x:
        where = "Starting at <b>%s</b>, %s." % (sq_name(x, y), dir_word(d))
    else:
        where = "Click the empty square where your word starts (click it again to go down), or type it (like h8)."
    out = ["<p>%s Type only the letters from your rack, in order: the letters already on the board join your "
           "word by themselves. A blank is <tt>_</tt> before the letter it stands for (<tt>_E</tt>).</p>" % where]
    out.append('<form action="/move.htm" method="get">%s%s <input type="submit" value="Play"></form>'
               % (_hidden(link, "/move.htm", tid), play_fields(sq, d, letters)))
    out.append('<form action="/check.htm" method="get">%s%s <input type="submit" value="Check score"></form>'
               % (_hidden(link, "/check.htm", tid), play_fields(sq, d, letters)))
    out.append("<p><font size=\"2\">Columns are a to o, rows 1 to 15. Check score asks the server, which "
               "also looks the words up; nothing is played.</font></p>")
    if v["bag"] >= 7:
        out.append('<form action="/move.htm" method="get">%s<input name="mv" size="14" maxlength="12" '
                   'value="EXCH "> <input type="submit" value="Exchange"> (the letters to put back)</form>'
                   % _hidden(link, "/move.htm", tid))
    return "\n".join(out)


def check_html(v, tid, link, start, my_turn):
    """What Check score found, as paragraphs for the side panel. The server's answer when the page is
    /check.htm's (v["check"]: the move and the core's reply, OK points words... or ERR ILLEGAL reason); the
    page's own estimate (preview: the words are not looked up) when the server gave none, or for a play
    picked with sel= on /table.htm."""
    mv, reply = v.get("check") or (None, None)
    if mv is None and start and start[3]:
        mv = "PLAY %d %d %s %s" % start
    play_it = " " + a(link("/move.htm", t=tid, mv=mv), "<b>Play it</b>") if mv and my_turn else ""
    if reply and reply.startswith("OK"):
        f = reply.split()
        points = f[1] if len(f) > 1 and f[1].isdigit() else "?"
        words = ", ".join(w.upper() for w in f[2:]) or "The play"
        return ["<p><b>Check:</b> %s scores %s points.%s</p>" % (esc(words), points, play_it)]
    if reply:
        return ["<p><b>Check:</b> %s.</p>" % esc(err_text(reply))]
    if not (start and start[3]):
        return []
    score, words = preview(v, v["rack"], *start)
    if score is None:
        return ["<p><b>Check:</b> %s.</p>" % esc(words)]
    return ["<p><b>Check:</b> %s scores %d points (an estimate: the word list is looked at when you play).%s</p>"
            % (esc(", ".join(w.upper() for w in words)), score, play_it)]


def web_parts(v, tid, link, seat, names, sel):
    """-> (board and the play forms, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat and "Game over" not in v["status"]
    start = start_of(v, sel)
    seats = max(len(v["counts"]), len(v["scores"]), 2)
    rows = []
    for i in range(seats):
        name = names[i] if i < len(names) else "-"
        mark = "&gt;" if v["turn"] == i else "&nbsp;"
        rows.append("<tr><td>%s</td><td>%s%s</td><td align=\"right\">%d</td><td align=\"right\">%s</td></tr>"
                    % (mark, esc(name), " (you)" if i == seat else "",
                       v["scores"][i] if i < len(v["scores"]) else 0,
                       "%d tiles" % v["counts"][i] if i < len(v["counts"]) else ""))
    side = ['<table border="0" cellpadding="2"><tr><th></th><th align="left">Player</th><th>Score</th>'
            "<th>Rack</th></tr>%s</table>" % "".join(rows),
            "<p>Tiles in the bag: %d</p>" % v["bag"]]
    if seat >= 0 and v["rack"] is not None:
        side.append("<p>Your rack: <tt>%s</tt></p>" % (esc(" ".join(v["rack"])) or "(empty)"))
    if v["last"]:
        s, x, y, d, word, score = v["last"]
        side.append("<p>Last play: %s, %s at %s %s, %d points</p>"
                    % (esc(_who(names, s)), esc(word), sq_name(x, y), dir_word(d), score))
    if seat >= 0:
        side += check_html(v, tid, link, start, my_turn)
    side.append("<p>%s</p>" % a(link("/colours.htm", t=tid), "Board colors"))
    board = board_html(board_image, v, tid, link, seat, sel, "Grid Words board", text=False)
    if my_turn:
        board += "\n" + play_forms(v, tid, link, seat, start)
    actions = a(link("/move.htm", t=tid, mv="PASS"), "Pass") if my_turn else ""
    hint = (" - click the empty square where your word starts, then type your letters"
            if my_turn and not typed_start(v, start) else "")
    return board, "\n".join(side), hint, actions


# ---- telnet --------------------------------------------------------------------------

HELP = [
    "The arrow keys move to the next empty square. Click a square,",
    "or press Enter on it, to start a word across from there.",
    "Press Enter again to go down, and once more to clear the start.",
    "Then type the letters from your rack and press Enter to play.",
    "Letters already on the board join your word. Type _ and a",
    "letter to play a blank as that letter.",
    "You can also type a square and a word, like i4 a XY for",
    "across or i4 d XY for down.",
    "Type check and a play, like check i4 a XY, to see its score.",
    "Type pass to skip your turn.",
    "Type exchange and the letters, like exchange QXZ, to swap them",
    "for new tiles. You can exchange only while the bag holds at",
    "least seven tiles.",
    "Type say and your text to talk, or resign to give up.",
    "Type colors to change the board's colors.",
    "A word like pass needs its square in front, like h8 a pass.",
    "Esc clears the start or goes back to the menu. Leaving never resigns.",
]
# On your turn, with no word started: how to pass or exchange (the hint line, and once a turn in plain mode).
PASS_HINT = "Type pass to skip your turn, or type exchange and the letters to swap them."
RACK = 7                    # tiles on a rack, and the fewest in the bag that an exchange needs


def points_lines(til):
    """The second page of help: every letter's points and the blank's, seven letters to a row."""
    cells = ["%s %-2d" % (c, til[c][0]) for c in sorted(til) if c != "_"]
    rows = ["   ".join(cells[i:i + 7]).rstrip() for i in range(0, len(cells), 7)]
    blank = til.get("_", (0, 0))[0]
    return (["Each letter is worth the points shown beside it.", ""] + rows +
            ["", "A blank is worth %d point%s and can stand for any letter." % (blank, "" if blank == 1 else "s")])


PLAY_WORDS = ("a", "across", "h", "d", "down", "v")


class Refusal(str):
    """move_word's answer for typed letters that make no play: the reason, which play_word says instead of
    sending a move."""
WORD_RE = re.compile(r"^[A-Za-z_]{1,30}\Z")


def gw_glyphs(mode):
    """The board's cells (3 columns each) for a display mode: pc and utf8 use CP437 characters, ansi and
    plain ASCII. Premium squares carry their names, so they differ in shape as well as color."""
    fancy = mode in ("pc", "utf8")
    return {"empty": " · " if fancy else " . ", "ctr": " * ", "TW": "3W ", "DW": "2W ", "TL": "3L ",
            "DL": "2L ", "H": "»" if fancy else ">", "V": "v"}


# The telnet board's colors: the player's palette mapped to the nearest ANSI colors (adapters/palette.py),
# kept with PRF in telnet's own namespace; these are the default's.
ATTRS = PAL.ansi_attrs(PAL.PRESETS[PAL.DEFAULT])
CELL_ATTR = {k: ATTRS[k] for k in ("", "ctr", "TW", "DW", "TL", "DL")}
A_TILE, A_BLANK, A_LASTW, A_PEND, A_MARK = ATTRS["tile"], ATTRS["blank"], ATTRS["last"], ATTRS["pend"], ATTRS["mark"]


def cursor_attr(attr):
    """A square's attribute with the cursor on it: the same colors in reverse video."""
    return "0;7;" + attr[2:] if attr.startswith("0;") else "7"


async def load_colours(core):
    """A telnet player's kept Grid Words colors, as the PRF value (the default's name when none is kept, or
    for a stand-in core without PRF)."""
    get = getattr(core, "pref_get", None)
    value = None
    if get:
        try:
            value = await get(PAL.PREF_KEY)
        except Exception:
            value = None
    return value.lower() if PAL.decode(value) else PAL.DEFAULT


def colour_attrs(value):
    """The telnet attributes for a kept value: a preset is mapped with every kind of square kept apart, a
    player's own picks as they are."""
    pal = PAL.palette(value)
    return PAL.ansi_attrs(pal, distinct=(value or PAL.DEFAULT) in PAL.PRESETS)


class GridBase(GameBase):
    """What both Grid Words screens share: the view, the events, typed plays and checks."""

    parse = staticmethod(parse_view)

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.start = None           # (x, y, "H" or "V"), 1-based: where a typed word starts

    def describe(self, text):
        d = describe_event(text, self.names)
        return d if d is not None else describe_common(text, self.names)

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    @staticmethod
    def square(word):
        """'h8' -> (8, 8), or None."""
        if not SQUARE_RE.match(word) or word[0] > "o" or not 1 <= int(word[1:]) <= SIZE:
            return None
        return ord(word[0]) - 96, int(word[1:])

    tokens = staticmethod(letter_tokens)

    def lay_out(self, x, y, d, typed):
        """lay_out on this table's board: (sx, sy, word, {(x, y): letter}); raises Refused."""
        return lay_out((self.v or {}).get("tiles", {}), x, y, d, typed)

    def laid_out(self, x, y, d, typed):
        """lay_out on this table's board, or None where it refuses (a start on a played square, the letters
        off the board)."""
        try:
            return self.lay_out(x, y, d, typed)
        except Refused:
            return None

    def play_from(self, x, y, d, typed):
        laid = self.laid_out(x, y, d, typed)
        return "PLAY %d %d %s %s" % (laid[0], laid[1], d, laid[2]) if laid else None

    def play_text(self, words):
        """The words of a typed play (without check) -> 'PLAY x y D LETTERS', a Refusal (the reason those
        letters make no play), or None if the words are not a play. From a square (typed, or the start set
        with Enter), the letters typed are the player's own: they fill the empty squares and the tiles
        already on the board join the word (lay_out), so `i4 x` beside HE plays HEX."""
        low = [w.lower() for w in words]
        if len(words) == 5 and low[0] == "play" and words[1].isdigit() and words[2].isdigit() \
                and low[3] in ("h", "v") and WORD_RE.match(words[4]):
            return "PLAY %s %s %s %s" % (words[1], words[2], low[3].upper(), words[4].upper())
        sq = self.square(low[0]) if words else None
        start = None
        if sq and len(words) in (2, 3) and WORD_RE.match(words[-1]) and (len(words) == 2 or low[1] in PLAY_WORDS):
            start = (sq[0], sq[1], "V" if len(words) == 3 and low[1] in ("d", "down", "v") else "H")
        elif len(words) == 1 and self.start and WORD_RE.match(words[0]):
            start = self.start
        if start is None:
            return None
        try:
            laid = self.lay_out(start[0], start[1], start[2], words[-1])
        except Refused as e:
            return Refusal(str(e))
        return "PLAY %d %d %s %s" % (laid[0], laid[1], start[2], laid[2])

    def move_word(self, cmd, words=()):
        words = list(words)
        if cmd == "pass" and len(words) == 1:
            return "PASS"
        if cmd in ("exchange", "exch", "swap") and len(words) == 2 and re.match(r"^[A-Za-z_]{1,7}\Z", words[1]):
            return "EXCH " + words[1].upper()
        if cmd in ("check", "chk"):
            p = self.play_text(words[1:]) if len(words) > 1 else None
            return p if isinstance(p, Refusal) else "CHK " + p if p else None
        return self.play_text(words)

    def described(self, word):
        return described(word)

    async def move(self, word):
        """A play, exchange or pass; on a refusal, the reason in the player's own terms (not the command)."""
        bag = (self.v or {}).get("bag")
        if word.startswith("EXCH ") and isinstance(bag, int) and bag < RACK:
            return exchange_refused(bag)
        _, final = await self.core.req("MOV %s %s" % (self.tid, word))
        if not final.startswith("OK"):
            return refusal(word, err_text(final))
        await self.pull()
        await self.refresh()
        return None

    async def play_word(self, word):
        if isinstance(word, Refusal):
            return "%s." % word
        if word.startswith("CHK "):
            _, final = await self.core.req("CHK %s %s" % (self.tid, word[4:]))
            if not final.startswith("OK"):
                return "Check: %s" % err_text(final)
            f = final.split()
            return "Check: %s scores %s" % (", ".join(f[2:]) or word.split()[-1], f[1] if len(f) > 1 else "?")
        err = await self.move(word)
        if not err:
            self.start = None
        return err

    def seats(self):
        return max(2, len(self.v["counts"]) if self.v else 0, min(len(self.names), 4))

    def cell_text(self, x, y, g):
        """-> (text, kind) for a square: kind is 'tile', 'blank' or the square's premium kind."""
        v = self.v
        c = v["tiles"].get((x, y))
        if c:
            return " %s " % c, ("blank" if c.islower() else "tile")
        kind = "ctr" if (x, y) == v["ctr"] else v["prm"].get((x, y), "")
        return g["ctr" if kind == "ctr" else kind or "empty"], kind

    def last_cells(self):
        last = self.v["last"] if self.v else None
        if not last:
            return set()
        _, x, y, d, word, _ = last
        dx, dy = (1, 0) if d == "H" else (0, 1)
        return {(x + dx * i, y + dy * i) for i in range(len(word))}

    def rack_text(self):
        """The rack's letters, without their points (help lists them)."""
        return " ".join(self.v["rack"] or "")

    def turn_line(self):
        v = self.v
        if not (v and v["rows"]):
            return "Waiting for an opponent"
        if self.over:
            return self.over
        if self.my_turn():
            return "Your move"
        return "%s to move" % self.name(v["turn"])

    def last_line(self):
        last = self.v["last"] if self.v else None
        if not last:
            return ""
        _, x, y, d, word, score = last
        return "Last: %s %s %s, %d" % (word, sq_name(x, y), dir_word(d), score)


class AnsiGrid(GridBase, AnsiGame):
    """Grid Words, full screen: the 15x15 board on the left (a cursor, the start of the word being
    typed, the letters typed so far), players, bag, rack, clock and log on the right."""

    HOTKEYS = "?"               # letters are for words: talk and resign are typed (say, resign)
    LOG_TOP, LOG_ROWS = 14, 6
    TITLE, HELP = "Grid Words", HELP
    BX, BY, PX = 2, 2, 50       # board frame left and top, panel left

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = (8, 8)

    def glyphs(self):
        """pc and utf8 draw their frames with box characters, ansi with ASCII."""
        return gw_glyphs("ansi" if self.g["H"] == "-" else "pc")

    def cell_xy(self, x, y):
        return self.BX + 1 + 3 * (x - 1), self.BY + y

    def snap(self):
        """When it becomes your move on an empty board, put the cursor on the center square."""
        mine = self.my_turn()
        self.cur = (min(max(self.cur[0], 1), SIZE), min(max(self.cur[1], 1), SIZE))
        if mine and not self.was_my_turn and self.v and not self.v["tiles"]:
            self.cur = self.v["ctr"]
        tiles = (self.v or {}).get("tiles") or {}
        if self.cur in tiles and len(tiles) < SIZE * SIZE:     # a play covered it: on to the next empty square
            x, y = self.cur
            while (x, y) in tiles:
                x, y = (x % SIZE) + 1, y if x < SIZE else y % SIZE + 1
            self.cur = (x, y)
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def compose(self):
        cv = Canvas()
        right = "watching" if self.me < 0 else "you are seat %d" % (self.me + 1)
        title_bar(cv, self.g, "Grid Words %s table %s" % (self.g["v"], self.tid), right)
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.me >= 0 and self.playing():
            # while a start is set, every letter types: only the arrow keys move (also with movement letters)
            keys = [("Arrow keys" if self.typing() and self.move_set() else "Arrows", "move"), ("Enter", "start"),
                    ("letters", "word"), ("?", "help"), ("Esc", "menu")]
        else:
            keys = [("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def pending(self):
        """The letters being typed, by square, while a start is set: {(x, y): letter}."""
        typed = self.typed_text().strip()
        if not (self.start and typed and WORD_RE.match(typed)):
            return {}
        laid = self.laid_out(self.start[0], self.start[1], self.start[2], typed)
        return laid[3] if laid else {}

    def attrs(self):
        """The board's colors for this player (colour_attrs of the kept value, loaded with the first view)."""
        value = getattr(self.s, "gwcolors", None) or PAL.DEFAULT
        if getattr(self, "_attrs_for", None) != value:
            self._attrs, self._attrs_for = colour_attrs(value), value
        return self._attrs

    async def refresh(self):
        if getattr(self.s, "gwcolors", None) is None:
            self.s.gwcolors = await load_colours(self.core)
        return await super().refresh()

    async def choose_colours(self):
        """The colors command: a preset, or the player's own pick for each element from the ANSI colors,
        kept for the next visit (PRF gwcolors, telnet's own)."""
        names = list(PAL.PRESETS)
        cur = names.index(self.s.gwcolors) if getattr(self.s, "gwcolors", None) in names else len(names)
        i = await self.choose("Board colors", ["The colors of the Grid Words board.",
                                                "Every square keeps its name (3W, 2W, 3L,",
                                                "2L), whatever the colors."],
                              [PAL.PRESET_NAMES[n] for n in names] + ["Your own: pick each color"], cur)
        if i is None:
            return None
        if i < len(names):
            value = names[i]
        else:
            picks = {}
            for k in PAL.ELEMENTS:
                choices = PAL.ANSI_CHOICES + (tuple(n for n, _ in PAL.ANSI_BRIGHT) if k == "letter" else ())
                j = await self.choose(PAL.ELEMENT_NAMES[k], ["The color of: %s." % PAL.ELEMENT_NAMES[k].lower()],
                                      [c[:1].upper() + c[1:] for c in choices], 0)
                if j is None:
                    return None
                picks[k] = choices[j]
            value = PAL.encode(PAL.ansi_custom(picks))
        self.s.gwcolors = value
        kept = await self.core.save_pref(PAL.PREF_KEY, value) if hasattr(self.core, "save_pref") else False
        return self.say("Board colors: %s%s." % (PAL.PRESET_NAMES.get(value, "your own"),
                                                  ", kept for next time" if kept else ""), A_YELLOW)

    async def command(self, line):
        if line.split() and line.split()[0].lower() in ("colours", "colors") and len(line.split()) == 1:
            await self.choose_colours()
            return None
        return await super().command(line)

    def draw_board(self, cv):
        g, gg, v = self.g, self.glyphs(), self.v
        at = self.attrs()
        bx, by = self.BX, self.BY
        started = bool(v and v["rows"])
        showcur = self.me >= 0 and self.playing()
        for i in range(SIZE):
            cv.put(bx + 2 + 3 * i, by - 1, LETTERS[i].lower(), A_AXIS_HI if showcur and i + 1 == self.cur[0]
                   else A_AXIS)
        cv.put(bx, by, g["TL"] + g["H"] * (3 * SIZE) + g["TR"], A_GRID)
        cv.put(bx, by + SIZE + 1, g["BL"] + g["H"] * (3 * SIZE) + g["BR"], A_GRID)
        last, pend = self.last_cells(), self.pending()
        for y in range(1, SIZE + 1):
            cv.put(bx - 2, by + y, "%2d" % y, A_AXIS_HI if showcur and y == self.cur[1] else A_AXIS)
            cv.put(bx, by + y, g["V"], A_GRID)
            cv.put(bx + 1 + 3 * SIZE, by + y, g["V"], A_GRID)
            for x in range(1, SIZE + 1):
                if v:
                    text, kind = self.cell_text(x, y, gg)
                else:
                    text, kind = gg["empty"], ""
                attr = (at["blank"] if kind == "blank" else at["last"] if (x, y) in last else at["tile"]) \
                    if kind in ("tile", "blank") else at.get(kind, at[""])
                if self.start and self.start[:2] == (x, y):
                    mark = gg[self.start[2]]
                    text = (" %s " % mark) if kind not in ("tile", "blank") else text[:2] + mark
                    attr = at["mark"] if kind not in ("tile", "blank") else attr
                if (x, y) in pend and kind not in ("tile", "blank"):
                    text, attr = "[%s]" % pend[(x, y)], at["pend"]
                if showcur and (x, y) == self.cur:
                    attr = cursor_attr(attr)
                cx, cy = self.cell_xy(x, y)
                cv.put(cx, cy, text, attr)
                cv.hot_area(cx, cy, 3, 1, ("sq", x, y))
        legend = [("3W", at["TW"]), (" ", ""), ("2W", at["DW"]), (": word x3 x2  ", A_DIM),
                  ("3L", at["TL"]), (" ", ""), ("2L", at["DL"]), (": letter x3 x2", A_DIM)]
        x = bx
        for text, attr in legend:
            cv.put(x, by + SIZE + 2, text, attr)
            x += len(text)
        if self.start and self.me >= 0 and self.playing():
            line = "Your word starts at %s and goes %s." % (sq_name(*self.start[:2]), dir_word(self.start[2]))
        else:
            line = self.last_line()
        cv.put(bx, by + SIZE + 3, line[:3 * SIZE + 2], A_TEXT)
        if not started:
            self.draw_waiting(cv, bx + 10, by + 6, 27)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        px, pw = self.PX, COLS - 1 - self.PX
        inner = pw - 4
        box(cv, g, px, 1, pw, 20, A_BORDER, "Players", tattr=A_HEAD)
        started = bool(v and v["rows"])
        for i in range(min(self.seats(), 4)):
            y = 2 + i
            tomove = self.playing() and v["turn"] == i
            empty = i >= len(self.names) or self.names[i] == "-"
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            cv.put(px + 4, y, ("(empty seat)" if empty else self.names[i])[:12],
                   A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if started:
                score = v["scores"][i] if i < len(v["scores"]) else 0
                count = v["counts"][i] if i < len(v["counts"]) else 0
                cv.put(px + 17, y, "%4d" % score, A_BRIGHT if tomove else A_TEXT)
                cv.put(px + 22, y, "(%d)" % count, A_DIM)
            if i == self.me:
                cv.put(px + 26, y, "*", A_YELLOW)
        rule(cv, g, px, 6, pw, A_BORDER)
        turn = self.turn_line()
        cv.put(px + 2, 7, turn[:inner], A_YELLOW if (not started or self.over or self.my_turn()) else A_TEXT)
        c = self.clock()
        if c:
            seat, left, limit = c
            clock = "Clock: %s left of %s" % (fmt_secs(left), turn_text(limit))
            cv.put(px + 2, 8, clock[:inner], A_RED if seat == self.me and left < 60 else A_TEXT)
        elif self.turn_secs and not self.over:
            cv.put(px + 2, 8, ("Time per turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        if started:
            cv.put(px + 2, 9, ("Tiles in the bag: %d" % v["bag"])[:inner], A_TEXT)
        rule(cv, g, px, 10, pw, A_BORDER, "Your rack" if self.me >= 0 else "Racks", tattr=A_HEAD)
        if self.me >= 0 and started and v["rack"] is not None:
            for i, c in enumerate(v["rack"][:RACK]):      # the letters as tiles; help lists their points
                cv.put(px + 2 + 3 * i, 11, " %s " % c, self.attrs()["tile"])
            cv.put(px + 2, 12, "Type _E for a blank E."[:inner], A_DIM)
        elif self.me < 0:
            cv.put(px + 2, 11, "Hidden from watchers."[:inner], A_DIM)
        rule(cv, g, px, 13, pw, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, px + 2, inner)

    def hint(self):
        v = self.v
        if not (v and v["rows"]):
            return ("Table %s starts when someone joins from telnet or the web (port 6500)." % self.tid, A_DIM)
        if self.over:
            return "%s. Esc goes back to the menu." % self.over, A_YELLOW
        if self.me < 0:
            return "You are watching. Type say and your text to talk, or press Esc for the menu.", A_DIM
        if self.my_turn():
            if self.start:
                return "Type your letters from %s and press Enter to play them." % sq_name(*self.start[:2]), A_TEXT
            return "Your move. Press Enter on a square to start a word there.", A_TEXT
        return "Waiting for %s. Leaving or logging off never resigns." % self.name(v["turn"]), A_DIM

    def input_line(self, cv):
        """Line 23: what is being typed, or on your turn with no word started, how to pass or exchange."""
        if self.buf:
            return super().input_line(cv)
        if self.my_turn() and not self.start and not self.msg:
            cv.put(1, ROWS - 2, self.pass_hint()[:COLS - 2], A_DIM)

    def pass_hint(self):
        """PASS_HINT, with : before a word whose first letter moves the cursor (exchange under ESDF)."""
        words = {w: (":" if self.move_of(w[0]) else "") + w for w in ("pass", "exchange")}
        return PASS_HINT.replace("type pass", "type " + words["pass"]).replace(
            "type exchange", "type " + words["exchange"])

    async def help(self):
        """Two pages: how to play, then every letter's points (the rack shows only the letters)."""
        await self.help_box(self.TITLE, self.HELP, foot="Press any key to see the letter points.")
        await self.message("Letter points", points_lines((self.v or {}).get("til") or STD_TIL))

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            x, y = self.cell_xy(*self.cur)
            return x + 1, y
        return None

    # ---- keys --------------------------------------------------------------
    def set_start(self):
        x, y = self.cur
        if (x, y) in ((self.v or {}).get("tiles") or {}):
            self.say("Start on an empty square: type only the letters from your rack.")
            return
        if self.start and self.start[:2] == (x, y):
            self.start = (x, y, "V") if self.start[2] == "H" else None
        else:
            self.start = (x, y, "H")

    async def on_click(self, value):
        if value[0] == "sq" and self.me >= 0 and self.playing():
            if (value[1], value[2]) in (self.v.get("tiles") or {}):
                self.say("Start on an empty square: type only the letters from your rack.")
                return None
            self.cur = (value[1], value[2])
            self.set_start()
        return None

    def on_arrow(self, k):
        if self.v and self.v["rows"]:
            dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
            tiles = self.v.get("tiles") or {}
            x, y = self.cur
            for _ in range(SIZE):                       # skip played squares: land on the next open one
                x, y = (x - 1 + dx) % SIZE + 1, (y - 1 + dy) % SIZE + 1
                if (x, y) not in tiles:
                    self.cur = (x, y)
                    break

    def typing(self):
        """Once Enter has set a word's start, every letter types the word (movement letters too)."""
        return bool(self.start) and self.me >= 0

    def keys_help(self):
        out = super().keys_help()
        return out + ["Once Enter sets a start, every letter types."] if out else out

    def on_escape(self):
        if self.start:
            self.start = None
            return None
        return "menu"

    async def on_enter(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        self.set_start()
        return None

    async def typed_move(self, word):
        text = await self.play_word(word)
        if text:
            self.say(text, A_YELLOW if text.startswith("Check:") and " scores " in text else A_RED)
        self.snap()


class PlainGrid(GridBase, PlainGame):
    """Grid Words for plain terminals: the board printed line by line, plays typed at a prompt."""

    BOARD_KINDS = ("PLAY", "EXCH", "PASS", "JOIN", "LEAVE", "START", "END")
    HELP = (" Type i4 a XY to play your X and Y across from i4, or i4 d XY for down.\n"
            " Start on an empty square and type only your rack's letters. Letters already\n"
            " on the board join the word. Type _E to play a blank as E.\n"
            " Type check and a play, like check h8 a WORD, to see its score.\n"
            " Type pass to skip your turn.\n"
            " Type exchange and the letters, like exchange QXZ, to swap them for new tiles.\n"
            " You can exchange only while the bag holds at least seven tiles.\n"
            " Type say and your text to talk to the table, or resign to give up.\n"
            " Type menu to go back to the menu. The game goes on while you are away.\n"
            " Type redraw, or press Enter, to see the board again.")

    @property
    def HELP_TEXT(self):
        """The help, then every letter's points (the rack shows only the letters)."""
        return "\n".join([self.HELP, ""] + [" " + l if l else "" for l in
                                            points_lines((self.v or {}).get("til") or STD_TIL)])

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you are seat %d" % (me + 1)
        out = ["", "== Grid Words, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet or the web" % self.tid,
                          "(port 6500). Type menu to go back; the table stays open."]
        g = gw_glyphs("plain")
        side = {}
        for i in range(min(self.seats(), 4)):
            mark = ">" if self.playing() and v["turn"] == i else " "
            side[i] = "%s %-12s %4d (%d)%s" % (mark, self.name(i)[:12], v["scores"][i] if i < len(v["scores"]) else 0,
                                               v["counts"][i] if i < len(v["counts"]) else 0,
                                               "*" if i == me else "")
        side[5] = "Tiles in the bag: %d" % v["bag"]
        if me >= 0 and v["rack"] is not None:
            side[6] = "Rack: " + self.rack_text()
        side[8] = self.turn_line()[:27] + ("." if not self.over else "")
        side[9] = self.last_line()[:28]
        side[11] = "3W 2W: word x3 x2"
        side[12] = "3L 2L: letter x3 x2"
        if me >= 0 and not self.over:
            side[13] = "Type h8 a WORD to play."
            side[14] = "Type help to learn more."
        else:
            side[13] = "Type help for the commands."
        out.append("     " + "".join(" %s " % LETTERS[i].lower() for i in range(SIZE)))
        out.append("    +" + "-" * (3 * SIZE) + "+")
        for y in range(1, SIZE + 1):
            cells = "".join(self.cell_text(x, y, g)[0] for x in range(1, SIZE + 1))
            out.append(("%3d |%s| %s" % (y, cells, side.get(y - 1, ""))).rstrip()[:80])
        out.append("    +" + "-" * (3 * SIZE) + "+")
        out.append(" " + (self.over or clock_text(v, self.names))[:78])
        if self.my_turn():
            out.append(" " + PASS_HINT)         # with the board, so once a turn
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.my_turn():
            return "Your play (like h8 a WORD) > "
        return "> "


AnsiScreen, PlainScreen = AnsiGrid, PlainGrid
