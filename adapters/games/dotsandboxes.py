"""Dots and Boxes (dotsandboxes/1, docs/types/dotsandboxes-1.md): the web board and the telnet screens.

A move draws one line between two neighboring dots, named by its dots: a1-b1
(across) or a1-a2 (down). Closing a box takes it and the same player moves
again. Seats are shown as the letters A to D, in every mode. With three or
four seats, a player who resigns or runs out of time leaves the game and the
others play on (G in the view, GONE in the events).

Positions on the board are worked out on a lattice of (2h+1) rows by (2w+1)
columns: (even, even) is a dot, (even, odd) a line across, (odd, even) a line
down and (odd, odd) a box. The telnet cursor sits on a line of the lattice.
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS, ROWS,
                      AnsiGame, Canvas, GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar, title_bar,
                      turn_text)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture
from ..web import board_html, esc, hidden_fields, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "dotsandboxes/1"
NAME = "Dots and Boxes"
ORDER = 5
PLAYERS = "2-4"
ABOUT = (
    "Two to four players take turns drawing a line between two neighboring dots on a grid. A player who "
    "closes the fourth side of a box claims it and draws again; when every box is closed, the player with "
    "the most boxes wins.",
    ["Standard rules: a grid of 5 by 5 boxes with every line open; the first seat moves first and turns go "
     "round in seat order; a move draws one line, and a player whose line closes one or two boxes takes "
     "them and moves again; when every box is taken the player with the most boxes wins, and a tie for the "
     "most is a draw.",
     "Three or four players: a player who resigns or runs out of time leaves the game and the others play "
     "on. That player's turn is skipped and its boxes stay taken, but it cannot win; the last player left "
     "wins.",
     "Board size: the game type takes 3 to 9 boxes each way, square or not. The web site and telnet start "
     "5 by 5 games; other sizes can be asked for over the native protocol.",
     "Majority rules: two players on a board of 13 boxes across and 11 down, and the game ends as soon as one "
     "player holds more than half the boxes (72 of 143). These are the rules of GameMaster's Dots&Boxes on "
     "the Mac, so GameMaster players can join these tables.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet). A "
     "player who closes a box and moves again gets a fresh turn clock."])

LETTERS = "ABCD"            # seats 0-3 as shown
RULE_NAMES = {"standard": "Standard: every box is played", "majority": "Majority: 13 by 11, first past half"}


# ---- the view and the events (docs/types/dotsandboxes-1.md) ----------------------------

def parse_view(lines):
    """VUE lines for dotsandboxes/1 -> dict. rows is True once there is a board (the game has started);
    hl, vl and bx are the HL, VL and BX rows, top first."""
    v = {"rows": False, "w": 5, "h": 5, "hl": [], "vl": [], "bx": [], "counts": [0, 0], "last": "",
         "turn": -1, "moves": [], "status": "", "seq": 0, "clock": None, "gone": []}
    brd = False
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) >= 3:
            v["seq"] = int(f[2])
        elif tag == "BRD" and len(f) == 3:
            v["w"], v["h"], brd = int(f[1]), int(f[2]), True
        elif tag == "HL" and len(f) == 3:
            v["hl"].append(f[2])
        elif tag == "VL" and len(f) == 3:
            v["vl"].append(f[2])
        elif tag == "BX" and len(f) == 3:
            v["bx"].append(f[2])
        elif tag == "C" and len(f) > 1:
            v["counts"] = [int(x) for x in f[1:]]
        elif tag == "G" and len(f) > 1:
            v["gone"] = [x == "1" for x in f[1:]]
        elif tag == "LL" and len(f) == 2:
            v["last"] = f[1]
        elif tag == "W" and len(f) == 2:
            v["turn"] = -1 if f[1] == "-" else int(f[1])
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) == 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    if v["hl"] and not brd:
        v["w"], v["h"] = len(v["hl"][0]), len(v["hl"]) - 1
    v["rows"] = bool(v["hl"])
    return v


def is_gone(v, i):
    """True for a seat that has left the game (resigned or ran out of time with three or more playing)."""
    return 0 <= i < len(v.get("gone") or []) and v["gone"][i]


def seat_name(names, i):
    return names[i] if 0 <= i < len(names) and names[i] != "-" else "seat %d" % (i + 1)


def describe_event(text, names):
    """Dots and Boxes' own events -> a line for the log (None for the shared ones: TURN, END ...)."""
    p = text.split()
    if p and p[0] == "LINE" and len(p) >= 3 and p[1].isdigit():
        out = "%s draws %s" % (seat_name(names, int(p[1])), p[2])
        boxes = p[4:] if len(p) > 4 and p[3] == "BOX" else []
        if boxes:
            out += " and takes box%s %s" % ("es" if len(boxes) > 1 else "", " and ".join(boxes))
        return out
    return None


# ---- the lattice and the coordinates ------------------------------------------------------

def dot(x, y):
    return "%s%d" % (chr(97 + x), y + 1)


def line_at(r, c):
    """The line at a lattice position -> its token (a1-b1), or None for a dot or a box."""
    if r % 2 == 0 and c % 2 == 1:
        x, y = c // 2, r // 2
        return "%s-%s" % (dot(x, y), dot(x + 1, y))
    if r % 2 == 1 and c % 2 == 0:
        x, y = c // 2, r // 2
        return "%s-%s" % (dot(x, y), dot(x, y + 1))
    return None


DOT_RE = re.compile(r"^([a-n])(1[0-2]|[1-9])\Z")       # up to n12, the corner of the 13 by 11 board
LINE_RE = re.compile(r"^([a-n](?:1[0-2]|[1-9]))-?([a-n](?:1[0-2]|[1-9]))\Z")


def pos_of(token):
    """A line token -> its lattice position (r, c), or None if it is not two neighboring dots."""
    m = LINE_RE.match(token.lower())
    if not m:
        return None
    (x1, y1), (x2, y2) = sorted(((ord(d[0]) - 97, int(d[1:]) - 1) for d in m.groups()), key=lambda d: (d[1], d[0]))
    if y1 == y2 and x2 == x1 + 1:
        return 2 * y1, 2 * x1 + 1
    if x1 == x2 and y2 == y1 + 1:
        return 2 * y1 + 1, 2 * x1
    return None


def canonical(d1, d2):
    """Two dots as typed -> the line's token (left or upper dot first); two dots that are not neighbors
    as they are, so the server says what is wrong."""
    p = pos_of(d1 + "-" + d2)
    return line_at(*p) if p else "%s-%s" % (d1.lower(), d2.lower())


def cell(rows, y, x):
    """One character of the HL, VL or BX rows ('.' where the view has none)."""
    return rows[y][x] if 0 <= y < len(rows) and 0 <= x < len(rows[y]) else "."


def state_at(v, r, c):
    """What is at a lattice position: 'dot', 'open', 'drawn', 'last', or a box owner seat (-1 open)."""
    if r % 2 == 0 and c % 2 == 0:
        return "dot"
    if r % 2 == 1 and c % 2 == 1:
        o = cell(v["bx"], r // 2, c // 2)
        return int(o) if o.isdigit() else -1
    drawn = cell(v["hl"], r // 2, c // 2) == "-" if r % 2 == 0 else cell(v["vl"], r // 2, c // 2) == "|"
    if not drawn:
        return "open"
    return "last" if line_at(r, c) == v["last"] else "drawn"


def boxes_left(v):
    return sum(row.count(".") for row in v["bx"])


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^[a-nA-N](1[0-2]|[1-9])-?[a-nA-N](1[0-2]|[1-9])\Z")    # a line: a1-b1
SEL_RE = None                                                       # nothing to pick up first

CELL, THIN = 32, 8          # a box's side and a line's thickness, in pixels
IMG = "/img/dotsandboxes/"
CREAM = (250, 246, 230)
OWNER_RGB = [(40, 80, 200), (200, 40, 40), (30, 140, 60), (130, 50, 160)]
FONT = {   # 5 by 7 letters for the boxes
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
}


def _line_gif(kind, across):
    """A line image, across (32x8) or down (8x32). kind: n open, o open and playable, d drawn, l the last."""
    pal = [CREAM, (0, 0, 0), (170, 170, 170), (220, 0, 0), (240, 150, 0)]
    w, h = (CELL, THIN) if across else (THIN, CELL)
    px = []
    for y in range(h):
        for x in range(w):
            along, side = (x, y) if across else (y, x)
            c = 0
            if kind in "dl" and 2 <= side <= 5:
                c = 1 if kind == "d" else 3
            elif kind == "o" and 3 <= side <= 4 and along % 6 < 4:
                c = 4
            elif kind == "n" and side in (3, 4) and along % 4 == 1:
                c = 2
            px.append(c)
    return make_gif(w, h, pal, px)


def _dot_gif():
    px = [1 if (x - 3.5) ** 2 + (y - 3.5) ** 2 <= 3.6 ** 2 else 0 for y in range(THIN) for x in range(THIN)]
    return make_gif(THIN, THIN, [CREAM, (0, 0, 0)], px)


def _box_gif(seat):
    """An open box (seat -1), or a closed one in its owner's color with the owner's letter."""
    if seat < 0:
        return make_gif(CELL, CELL, [CREAM, (0, 0, 0)], [0] * (CELL * CELL))
    glyph = FONT[LETTERS[seat]]
    ox, oy = (CELL - 15) // 2, (CELL - 21) // 2
    px = []
    for y in range(CELL):
        for x in range(CELL):
            gx, gy = (x - ox) // 3, (y - oy) // 3
            on = 0 <= x - ox < 15 and 0 <= y - oy < 21 and glyph[gy][gx] == "#"
            px.append(2 if on else 1)
    return make_gif(CELL, CELL, [CREAM, OWNER_RGB[seat], (255, 255, 255), (0, 0, 0)], px)


def images():
    """Dots, lines across and down (open, playable, drawn, the last drawn) and boxes (open, and one per
    seat), under /img/dotsandboxes/."""
    out = {IMG + "dot.gif": _dot_gif(), IMG + "b.gif": _box_gif(-1)}
    for k in "nodl":
        out[IMG + "h%s.gif" % k] = _line_gif(k, True)
        out[IMG + "v%s.gif" % k] = _line_gif(k, False)
    for s in range(4):
        out[IMG + "b%d.gif" % s] = _box_gif(s)
    return out


MARGIN = 16
STEP = CELL + THIN          # from one dot to the next


def lattice_xy(r, c):
    """The top left pixel of a lattice position (a dot, a line or a box)."""
    return MARGIN + (c // 2) * STEP + (c % 2) * THIN, MARGIN + (r // 2) * STEP + (r % 2) * THIN


def board_image(v, seat, sel):
    """The lattice as one image: dots, lines and boxes of images(), letters and numbers round it. On the
    player's turn every open line is an area, a diamond reaching to the middle of the boxes on each side,
    that draws it."""
    w, h = v["w"], v["h"]
    legal = set(v["moves"]) if seat >= 0 and v["turn"] == seat else set()
    c = Picture(MARGIN + w * STEP + THIN + 4, MARGIN + h * STEP + THIN + 4, bg=CREAM, images=images)
    for i in range(w + 1):
        c.text(MARGIN + i * STEP + THIN // 2 + 1, MARGIN - 10, chr(97 + i), (0, 0, 0), align="center")
    for j in range(h + 1):
        c.text(MARGIN - 4, MARGIN + j * STEP, str(j + 1), (0, 0, 0), align="right")
    for r in range(2 * h + 1):
        for col in range(2 * w + 1):
            x0, y0 = lattice_xy(r, col)
            st = state_at(v, r, col)
            if st == "dot":
                c.tile(IMG + "dot.gif", x0, y0)
                continue
            if isinstance(st, int):
                c.tile(IMG + ("b%d.gif" % st if st >= 0 else "b.gif"), x0, y0)
                continue
            hv = "h" if r % 2 == 0 else "v"
            tok = line_at(r, col)
            kind = {"open": "o" if tok in legal else "n", "drawn": "d", "last": "l"}[st]
            c.tile(IMG + "%s%s.gif" % (hv, kind), x0, y0)
            if kind == "o":
                if hv == "h":       # the dots' centers, and half a box above and below
                    cx, cy = x0 + CELL // 2, y0 + THIN // 2
                    pts = (x0 - THIN // 2, cy, cx, cy - STEP // 2, x0 + CELL + THIN // 2, cy, cx, cy + STEP // 2)
                else:
                    cx, cy = x0 + THIN // 2, y0 + CELL // 2
                    pts = (cx, y0 - THIN // 2, cx + STEP // 2, cy, cx, y0 + CELL + THIN // 2, cx - STEP // 2, cy)
                c.area("poly", pts, tok, "/move.htm", mv=tok)
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat and bool(v["moves"])
    rows = []
    for i in range(max(2, len(v["counts"]))):
        n = v["counts"][i] if i < len(v["counts"]) else 0
        who = esc(seat_name(names, i)) + (" (you)" if i == seat else "") + (", left the game" if is_gone(v, i)
                                                                          else "")
        if i == v["turn"]:
            who = "<b>%s</b>" % who
        rows.append('<tr><td>%s</td><td><b>%s</b></td><td>%s</td><td align="right">%d</td></tr>'
                    % ('<font color="#%02X%02X%02X"><b>%s</b></font>' % (OWNER_RGB[i] + (LETTERS[i],)), LETTERS[i],
                       who, n))
    side = '<table border="0" cellpadding="3">%s</table>' % "".join(rows)
    info = ["Boxes left: %d of %d" % (boxes_left(v), v["w"] * v["h"])]
    if v["last"]:
        info.append("Last line: %s" % esc(v["last"]))
    side += "\n<p>%s</p>" % "<br>".join(info)
    hint = ""
    if my_turn:
        hint = " - click an orange line"
        hidden = hidden_fields(link("/move.htm", t=tid))
        side += ('\n<form action="/move.htm" method="get">\n%s\nOr type a line (like %s): '
                 '<input name="mv" size="7" maxlength="7"> <input type="submit" value="Draw">\n</form>'
                 % (hidden, esc(sorted(v["moves"])[0])))
    return board_html(board_image, v, tid, link, seat, sel, "Dots and Boxes board", text=False), side, hint, ""


# ---- telnet --------------------------------------------------------------------------

HELP = [
    "Arrow keys   move the cursor from line to line",
    "Enter/Space  draw the line under the cursor",
    "Mouse        click an open line to draw it",
    "T            talk: type a line to the table",
    "R            resign (asks first)",
    "Esc          back to the menu; the game goes on",
    "",
    "Closing a box takes it (your letter fills it)",
    "and you move again. Most boxes wins.",
    "Typed commands work too: a line like a1-b1,",
    "say <text>, resign, redraw, menu, help.",
    "Leaving or logging off never resigns.",
]
OWNER_BG = ("44", "41", "42", "45")         # seats A-D: blue, red, green, magenta (the letter says it too)
A_LINE = "0;1;37"
A_LAST = "0;1;33"
A_DOT = "0;37"
A_OPEN, A_LEGAL = "0;34", "0;33"
A_CUR = "0;30;46"


def dots_glyphs(mode):
    """Dots and lines for a mode: pc and utf8 the same CP437 shapes, ansi and plain ASCII. The last line
    drawn differs in shape (double), not only in color."""
    if mode in ("pc", "utf8"):
        return {"dot": "■", "h": "─", "hl": "═", "v": "│", "vl": "║", "open": "·"}
    return {"dot": "+", "h": "-", "hl": "=", "v": "|", "vl": "#", "open": "."}


class DotsBase(GameBase):
    """What both Dots and Boxes screens share: the view, the events, typed lines."""

    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        """a1-b1, a1b1 or a1 b1 (either dot first) -> the line's token."""
        m = LINE_RE.match(cmd)
        if m:
            return canonical(*m.groups())
        if DOT_RE.match(cmd) and len(words) > 1 and DOT_RE.match(words[1].lower()):
            return canonical(cmd, words[1])
        return None

    def seats(self):
        return max(2, len(self.v["counts"]) if self.v else 0, len(self.names))

    def playing(self):
        """Started, and nobody has seen the end yet (W - says the game is over before its END is read)."""
        return bool(self.v and self.v["rows"]) and not self.over and self.v["turn"] >= 0

    def again(self):
        return "again" in self.v["status"] if self.v else False

    def end_text(self):
        return self.over or (self.v["status"] if "Game over" in self.v["status"] else "Game over")

    def turn_line(self):
        v = self.v
        if not (v and v["rows"]):
            return "Waiting for an opponent"
        if not self.playing():
            return self.end_text()
        if self.my_turn():
            return "Your move again: box closed" if self.again() else "Your move"
        if is_gone(v, self.me):
            return "You left; %s to move" % self.name(v["turn"])
        return "%s to move" % self.name(v["turn"])


class AnsiDots(DotsBase, AnsiGame):
    """Dots and Boxes, full screen: the board on the left (a cursor on the lines), players, boxes, clock
    and log on the right."""

    HOTKEYS = "rt?"
    LOG_TOP, LOG_ROWS = 9, 11
    TITLE, HELP = "Dots and Boxes", HELP
    PX = 43                 # the panel's left edge

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.dg = dots_glyphs(sess.mode)
        self.cur = (0, 1)   # lattice row, column: a line
        self.gx = 1         # the column the cursor keeps to when it moves up and down
        self.top = 0        # the first dot row shown, on a board too tall for the screen (13 by 11)

    # ---- geometry --------------------------------------------------------
    def geometry(self):
        """-> boxes across and down, columns and rows per box, the top left dot's x and y. Small boards get
        6 columns and 3 rows a box; up to 9 by 9 fits at 4 and 2 (37 columns, 19 rows). The 13 by 11
        majority board gets 2 and 2: 27 columns, and 23 rows, of which 19 are shown (see shown_top)."""
        v = self.v
        w, h = (v["w"], v["h"]) if v else (5, 5)
        sx = 6 if 6 * w + 1 <= 37 else 4 if 4 * w + 1 <= 37 else 2
        sy = 3 if 3 * h + 1 <= 19 else 2
        return w, h, sx, sy, 4 + (37 - sx * w - 1) // 2, 2 + (19 - min(sy * h + 1, 19)) // 2

    def shown_top(self):
        """The first dot row shown. A board taller than the 19 rows it has (13 by 11) shows 10 rows of dots
        and scrolls so that the cursor stays on the screen."""
        w, h, sx, sy = self.geometry()[:4]
        if sy * h + 1 <= 19:
            self.top = 0
        else:
            r = self.cur[0]
            self.top = min(max(self.top, (r - 17) // 2, 0), r // 2, h - 9)
        return self.top

    def xy(self, r, c):
        """The screen cell where a lattice position starts."""
        w, h, sx, sy, bx, by = self.geometry()
        return bx + sx * (c // 2) + c % 2, by + sy * (r // 2 - self.top) + r % 2

    def shown(self, r):
        """True for a lattice row on the screen."""
        y = self.xy(r, 0)[1]
        return self.geometry()[5] <= y < 21

    def nearest_open(self):
        r0, c0 = self.cur
        best = min(self.v["moves"], key=lambda t: (abs(pos_of(t)[0] - r0) + abs(pos_of(t)[1] - c0), pos_of(t)))
        return pos_of(best)

    def snap(self):
        """Keep the cursor on a line of the board; on the player's move, on an open one."""
        w, h = self.geometry()[:2]
        r, c = self.cur
        if not (0 <= r <= 2 * h and 0 <= c <= 2 * w and line_at(r, c)):
            self.cur = (0, 1)
        mine = self.my_turn()
        if mine and line_at(*self.cur) not in self.v["moves"] and all(pos_of(t) for t in self.v["moves"]):
            self.cur = self.nearest_open()
        self.gx = self.cur[1]
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def compose(self):
        cv = Canvas()
        right = "watching" if self.me < 0 else "you are %s" % LETTERS[self.me] if self.me < 4 else ""
        title_bar(cv, self.g, "Dots and Boxes %s table %s" % (self.g["v"], self.tid), right)
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.me >= 0 and self.playing() and not is_gone(self.v, self.me):
            keys = [("Arrows", "move"), ("Enter", "draw"), ("T", "talk"), ("R", "resign"),
                    ("?", "help"), ("Esc", "menu")]
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_board(self, cv):
        v, G = self.v, self.dg
        w, h, sx, sy, bx, by = self.geometry()
        self.shown_top()
        showcur = self.me >= 0 and self.playing() and not is_gone(v, self.me)
        cur = self.cur if showcur else None
        ends = set()
        if cur:
            r, c = cur
            ends = {(r // 2, c // 2), ((r + 1) // 2, (c + 1) // 2)}     # the line's two dots (row, column)
        for i in range(w + 1):
            hi = any(e[1] == i for e in ends)
            cv.put(bx + sx * i, by - 1, chr(97 + i), A_AXIS_HI if hi else A_AXIS)
        for j in range(h + 1):
            if not self.shown(2 * j):
                continue
            hi = any(e[0] == j for e in ends)
            cv.put(bx - 3, self.xy(2 * j, 0)[1], "%2d" % (j + 1), A_AXIS_HI if hi else A_AXIS)
        started = bool(v and v["rows"])
        legal = set(v["moves"]) if self.my_turn() else set()
        for r in range(2 * h + 1):
            if not self.shown(r):
                continue
            for c in range(2 * w + 1):
                x, y = self.xy(r, c)
                st = state_at(v, r, c) if started else ("dot" if r % 2 == 0 and c % 2 == 0 else
                                                        -1 if r % 2 and c % 2 else "open")
                if st == "dot":
                    cv.put(x, y, G["dot"], A_DOT)
                    continue
                if isinstance(st, int):
                    if st >= 0:
                        letter = LETTERS[st] if st < 4 else "?"
                        for k in range(sy - 1):
                            cv.put(x, y + k, letter.center(sx - 1), "0;1;37;" + OWNER_BG[st % 4])
                    continue
                on = (r, c) == cur
                if st == "open":
                    attr = A_CUR if on else A_LEGAL if line_at(r, c) in legal else A_OPEN
                    glyph = G["open"]
                elif st == "last":
                    attr, glyph = (A_CUR if on else A_LAST), None
                else:
                    attr, glyph = (A_CUR if on else A_LINE), None
                if r % 2 == 0:
                    if glyph is None:
                        text = (G["hl"] if st == "last" else G["h"]) * (sx - 1)
                    elif sx == 2:
                        text = glyph
                    else:
                        text = "".join(glyph if k % 2 else " " for k in range(sx - 1))
                    cv.put(x, y, text, attr)
                    cv.hot_area(x, y, sx - 1, 1, ("ln", r, c))
                else:
                    ch = glyph or (G["vl"] if st == "last" else G["v"])
                    for k in range(sy - 1):
                        cv.put(x, y + k, ch, attr)
                    cv.hot_area(x - 1, y, 3, sy - 1, ("ln", r, c))
        if not started:
            bw = 27
            self.draw_waiting(cv, bx + max(0, sx * w + 1 - bw) // 2, by + (min(sy * h + 1, 19) - 5) // 2, bw)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        px = self.PX
        pw = COLS - 1 - px
        inner = pw - 4
        box(cv, g, px, 1, pw, 20, A_BORDER, "Players", tattr=A_HEAD)
        started = bool(v and v["rows"])
        n = self.seats()
        for i in range(n):
            y = 2 + i
            tomove = self.playing() and v["turn"] == i
            empty = i >= len(self.names) or self.names[i] == "-"
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            cv.put(px + 4, y, " %s " % LETTERS[i % 4], "0;1;37;" + OWNER_BG[i % 4])
            label = ("(empty seat)" if empty else self.names[i]) + (" (you)" if i == self.me else "")
            count = v["counts"][i] if started and i < len(v["counts"]) else 0
            cnt = "%2d box%s" % (count, "" if count == 1 else "es")
            if started and is_gone(v, i):
                cnt = "%2d left" % count
            cv.put(px + 8, y, label[:inner - 6 - 8], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if started:
                cv.put(px + pw - 2 - 8, y, cnt.ljust(8), A_BRIGHT if tomove else A_TEXT)
        y = 2 + n
        rule(cv, g, px, y, pw, A_BORDER)
        turn = self.turn_line()
        cv.put(px + 2, y + 1, turn[:inner], A_YELLOW if (not self.playing() or self.my_turn()) else A_TEXT)
        c = self.clock()
        if c:
            seat, left, limit = c
            clock = "Clock: %s left of %s" % (fmt_secs(left), turn_text(limit))
            cv.put(px + 2, y + 2, clock[:inner], A_RED if seat == self.me and left < 60 else A_TEXT)
        elif self.turn_secs and self.playing():
            cv.put(px + 2, y + 2, ("Time per turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        if started:
            info = "Boxes left: %d of %d" % (boxes_left(v), v["w"] * v["h"])
            if v["last"]:
                info += "  last %s" % v["last"]
            cv.put(px + 2, y + 3, info[:inner], A_DIM)
        rule(cv, g, px, y + 4, pw, A_BORDER, "Log", tattr=A_HEAD)
        self.LOG_TOP, self.LOG_ROWS = y + 5, 20 - (y + 5)
        self.draw_log(cv, px + 2, inner)

    def hint(self):
        v = self.v
        if not (v and v["rows"]):
            return ("Table %s starts when someone joins: from telnet or the web (port 6500)."
                    % self.tid, A_DIM)
        if not self.playing():
            return "%s. Esc goes back to the menu." % self.end_text(), A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        if is_gone(v, self.me):
            return "You have left this game; the others play on. Esc goes back to the menu.", A_DIM
        if self.my_turn():
            tok = line_at(*self.cur)
            again = "You closed a box: move again. " if self.again() else "Your move. "
            if tok in v["moves"]:
                return "%s%s: Enter draws this line." % (again, tok), A_TEXT
            return "%s%s is drawn; open lines show %s." % (again, tok, self.dg["open"]), A_DIM
        return "Waiting for %s. Leaving or logging off never resigns." % self.name(v["turn"]), A_DIM

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            r, c = self.cur
            sx, sy = self.geometry()[2:4]
            self.shown_top()
            x, y = self.xy(r, c)
            return (x + (sx - 2) // 2, y) if r % 2 == 0 else (x, y + (sy - 2) // 2)
        return None

    # ---- keys --------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "ln" and self.me >= 0 and self.playing():
            self.cur, self.gx = (value[1], value[2]), value[2]
            if self.my_turn() and line_at(*self.cur) in self.v["moves"]:
                return await self.draw()       # a click on an open line draws it
        return None

    def on_arrow(self, k):
        """Left and right go along the row of the screen (lines across in a row of dots, lines down in a row
        of boxes); up and down go to the next row, to the line nearest the column the cursor keeps to."""
        if not (self.v and self.v["rows"]):
            return
        w, h = self.geometry()[:2]
        r, c = self.cur
        if k in ("LEFT", "RIGHT"):
            lo, hi = (1, 2 * w - 1) if r % 2 == 0 else (0, 2 * w)
            c += 2 if k == "RIGHT" else -2
            c = lo if c > hi else hi if c < lo else c
            self.cur, self.gx = (r, c), c
        else:
            r = (r + (1 if k == "DOWN" else -1)) % (2 * h + 1)
            cols = range(1, 2 * w, 2) if r % 2 == 0 else range(0, 2 * w + 1, 2)
            self.cur = (r, min(cols, key=lambda x: (abs(x - self.gx), x)))

    async def on_enter(self):
        return await self.draw()

    async def draw(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.end_text() if self.v["rows"] else "The game has not started yet.")
        if not self.my_turn():
            return self.say("It is %s's move." % self.name(self.v["turn"]))
        tok = line_at(*self.cur)
        if tok not in self.v["moves"]:
            return self.say("%s is drawn already: pick an open line." % tok)
        err = await self.move(tok)
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainDots(DotsBase, PlainGame):
    """Dots and Boxes for plain terminals: the board printed line by line, typed lines at a prompt."""

    BOARD_KINDS = ("LINE", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" A line like a1-b1 (across) or a1-a2 (down) draws it; closing a box\n"
                 " takes it, and you move again. say <text> talks to the table.\n"
                 " resign gives up (it asks first). menu goes back to the menu;\n"
                 " the game goes on and leaving never resigns. redraw or Enter\n"
                 " shows the board again.")

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you are %s" % LETTERS[me % 4]
        out = ["", "== Dots and Boxes, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet, the web" % self.tid,
                          "(port 6500) or another client. Type menu to go back;",
                          "the table stays open."]
        G = dots_glyphs("plain")
        w, h = v["w"], v["h"]
        side = []
        for i in range(self.seats()):
            mark = ">" if self.playing() and v["turn"] == i else " "
            n = v["counts"][i] if i < len(v["counts"]) else 0
            side.append("%s %s %-12s %2d%s" % (mark, LETTERS[i % 4], self.name(i)[:12], n,
                                               " left" if is_gone(v, i) else ""))
        side += ["", "Boxes left: %d of %d" % (boxes_left(v), w * h)]
        if v["last"]:
            side.append("Last line: %s" % v["last"])
        side += ["", self.turn_line() + "."]
        if me >= 0 and self.playing() and not is_gone(v, me):
            side += ["Type a line like a1-b1 or a1-a2,", "say <text>, resign, menu, help."]
        else:
            side.append("Type say <text>, menu or help.")
        board = ["      " + "".join(chr(97 + i).ljust(4) for i in range(w + 1))]
        for r in range(2 * h + 1):
            row = " %2d   " % (r // 2 + 1) if r % 2 == 0 else "      "
            for c in range(2 * w + 1):
                st = state_at(v, r, c)
                if st == "dot":
                    row += G["dot"]
                elif isinstance(st, int):
                    row += (" %s " % LETTERS[st % 4]) if st >= 0 else "   "
                elif r % 2 == 0:
                    row += {"open": "   ", "drawn": G["h"] * 3, "last": G["hl"] * 3}[st]
                else:
                    row += {"open": " ", "drawn": G["v"], "last": G["vl"]}[st]
            board.append(row)
        if max(len(row) for row in board) > 46:     # the 13 by 11 board (59 columns): the rest goes below
            out += [row.rstrip() for row in board] + [" " + text for text in side if text]
            side = []
        for i, row in enumerate(board if side else ()):
            text = side[i - 1] if 1 <= i <= len(side) else ""
            out.append((row.ljust(46) + text)[:80].rstrip())
        for text in side[len(board) - 1:]:
            out.append((" " * 46 + text)[:80])
        if self.playing():
            out.append(" " + clock_text(v, self.names))
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.my_turn():
            return "Your move%s (like %s) > " % (" again" if self.again() else "", sorted(self.v["moves"])[0])
        return "> "


AnsiScreen, PlainScreen = AnsiDots, PlainDots
