"""Five in a Row (fiveinarow/1, docs/types/fiveinarow-1.md): the web board and the telnet screens.

Laid out as Reversi's module (docs/games-howto.md): what the game is, the view
and the events, the web page, then the telnet screens.
"""
import re
import textwrap

from ..telnet import (A_AXIS, A_AXIS_HI, A_DIM, A_RED, A_TEXT, AnsiGame, COLS, Canvas, GameBase, PlainGame, ROWS,
                      title_bar)
from ..telnet import describe_event as describe_common
from ..board import Canvas as Picture
from ..web import board_html, esc, hidden_fields, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "fiveinarow/1"
NAME = "Five in a Row"
ORDER = 6                   # sixth in the build order (README.md)
PLAYERS = "2"
ABOUT = (
    "Two players take turns placing stones of their own color on the empty points of a square grid, one "
    "stone a turn; a stone once placed never moves and is never taken. The first player to make an unbroken "
    "line of five of their own stones, across, down or diagonally, wins. The game is also known as Gomoku.",
    ["Freestyle rules: a line of five or more stones wins. The first seat plays black and moves first; "
     "there is no swap and no opening restriction, and the game is a draw if the board fills up with no "
     "winning line.",
     "Standard rules: as freestyle, but only a line of exactly five wins; a line of six or more does not "
     "count, and play goes on.",
     "Board size: 15 by 15 points; the game type takes any size from 9 by 9 to 20 by 20 (size=). The web "
     "site and telnet start 15 by 15 games, with the rules chosen there; other sizes can be asked for over "
     "the native protocol. NetLuff, a Mac program, plays freestyle on 20 by 20 boards.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])


# ---- the view and the events (docs/types/fiveinarow-1.md) -----------------------------

def sq(x, y):
    """(column, row) from 0 -> a point's name: (7, 7) is h8."""
    return "%s%d" % (chr(97 + x), y + 1)


def pt(tok):
    """A point's name -> (column, row), or None."""
    tok = tok.lower()
    if len(tok) < 2 or not "a" <= tok[0] <= "t" or not tok[1:].isdigit():
        return None
    return ord(tok[0]) - 97, int(tok[1:]) - 1


def parse_view(lines):
    """VUE lines for fiveinarow/1 -> dict. rows is False until the game starts (the first W line); stones maps
    (column, row) to the seat; order lists each seat's stones in the order played."""
    v = {"rows": False, "n": 15, "rules": "", "stones": {}, "order": ([], []), "counts": (0, 0), "last": None,
         "five": [], "turn": -1, "moves": [], "status": "", "seq": 0, "clock": None}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) >= 3:
            v["seq"] = int(f[2])
        elif tag == "BRD" and len(f) >= 2:
            v["n"] = int(f[1])
        elif tag == "RUL" and len(f) >= 2:
            v["rules"] = f[1]
        elif tag == "P" and len(f) >= 2 and f[1] in ("0", "1"):
            s = int(f[1])
            for t in f[2:]:
                p = pt(t)
                if p:
                    v["stones"][p] = s
                    v["order"][s].append(p)
        elif tag == "C" and len(f) >= 3:
            v["counts"] = (int(f[1]), int(f[2]))
        elif tag == "L" and len(f) >= 3:
            v["last"] = pt(f[2])
        elif tag == "F":
            v["five"] = [p for p in (pt(t) for t in f[1:]) if p]
        elif tag == "W" and len(f) >= 2:
            v["turn"], v["rows"] = int(f[1]), True
        elif tag == "M" and len(f) >= 2:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) >= 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    return v


COUNT_WORDS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def describe_event(text, names):
    """Five in a Row's own events -> a line for the log (None for the shared ones)."""
    p = text.split()

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) else "seat %d" % i
    if p[0] == "PUT" and len(p) >= 3:
        return "%s plays %s" % (nm(p[1]), p[2])
    if p[0] == "END" and len(p) >= 4 and p[1] == "win" and "-" in p[3]:
        a, b = (pt(t) for t in p[3].split("-", 1))
        if a and b:
            k = max(abs(a[0] - b[0]), abs(a[1] - b[1])) + 1
            return "Game over: %s wins with %s in a row, %s" % (nm(p[2]), COUNT_WORDS.get(k, str(k)),
                                                                  p[3].replace("-", " to "))
    if p[0] == "END" and len(p) >= 2 and p[1] == "draw":
        return "Game over: a draw, the board is full"
    return None


def star_points(n):
    """The marked points of an n by n board: four on the fourth line (third on small boards), the center on
    odd sizes, and the side midpoints from 19 by 19."""
    k = 3 if n >= 13 else 2
    lo, hi, mid = k, n - 1 - k, n // 2
    out = {(lo, lo), (hi, lo), (lo, hi), (hi, hi)}
    if n % 2:
        out.add((mid, mid))
    if n >= 19:
        out |= {(mid, lo), (lo, mid), (hi, mid), (mid, hi)}
    return out


def nearest_empty(v, start):
    """The empty point nearest to start (ring by ring), or start itself on a full board."""
    n, stones = v["n"], v["stones"]
    x0, y0 = start
    for d in range(n):
        ring = [(x0 + dx, y0 + dy) for dy in range(-d, d + 1) for dx in range(-d, d + 1)
                if max(abs(dx), abs(dy)) == d]
        for p in ring:
            if 0 <= p[0] < n and 0 <= p[1] < n and p not in stones:
                return p
    return start


def marked(v):
    """The stones drawn with a mark: the last one and the winning line."""
    return set(v["five"]) | ({v["last"]} if v["last"] else set())


RULE_NAMES = {"freestyle": "Freestyle: five or more wins", "standard": "Standard: exactly five wins"}
RULES_TEXT = {"freestyle": "Five or more in a row wins", "standard": "Exactly five in a row wins"}
RULES_SHORT = {"freestyle": "Five or more wins", "standard": "Exactly five wins"}


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^[a-t](?:[1-9]|1[0-9]|20)\Z")   # a point: h8 (\Z: no newline after it)
SEL_RE = None                                       # nothing to pick up first

CELL = 25                                           # pixels per point
POSITIONS = ("nw", "n", "ne", "w", "c", "e", "sw", "s", "se")
STATES = "ebwxy"            # empty, black, white, black marked, white marked
PALETTE = [(222, 176, 98), (0, 0, 0), (255, 255, 255), (208, 0, 0)]     # wood, black, white, red


def position(x, y, n):
    """Where a point sits on the board: a corner (nw ...), an edge (n, w, e, s) or inside (c)."""
    ns = "n" if y == 0 else "s" if y == n - 1 else ""
    we = "w" if x == 0 else "e" if x == n - 1 else ""
    return (ns + we) or "c"


def _point(pos, state, star=False):
    c, r = CELL // 2, CELL / 2 - 1
    px = []
    for y in range(CELL):
        for x in range(CELL):
            on_h = y == c and (x >= c or "w" not in pos) and (x <= c or "e" not in pos)
            on_v = x == c and (y >= c or "n" not in pos) and (y <= c or "s" not in pos)
            col = 1 if on_h or on_v else 0
            d2 = (x - c) ** 2 + (y - c) ** 2
            if star and d2 <= 2.5 ** 2:
                col = 1
            if state in "bwxy" and d2 <= r ** 2:
                col = 1 if state in "bx" or d2 > (r - 1.2) ** 2 else 2
            if state in "xy" and d2 <= 3.3 ** 2:
                col = 3
            px.append(col)
    return make_gif(CELL, CELL, PALETTE, px)


def images():
    """Every intersection: nine places on the board (corners, edges, inside) by empty, black, white and
    the two marked stones; and the inside star point."""
    out = {"/img/fiveinarow/%s-%s.gif" % (p, s): _point(p, s) for p in POSITIONS for s in STATES}
    out["/img/fiveinarow/star.gif"] = _point("c", "e", star=True)
    return out


MARGIN = 16


def board_image(v, seat, sel):
    """The board as one image: the points of images(), letters and numbers round it; on the player's
    turn every empty point is an area that plays there."""
    n, stones, mark, stars = v["n"], v["stones"], marked(v), star_points(v["n"])
    my_turn = seat >= 0 and v["turn"] == seat and "Game over" not in v["status"]
    c = Picture(MARGIN + n * CELL + 4, MARGIN + n * CELL + 4, bg=PALETTE[0], images=images)
    c.labels(MARGIN, MARGIN, CELL, n, n, (0, 0, 0), gap=2)
    for y in range(n):
        for x in range(n):
            x0, y0 = MARGIN + x * CELL, MARGIN + y * CELL
            pos, s = position(x, y, n), stones.get((x, y))
            if s is not None:
                c.tile("/img/fiveinarow/%s-%s.gif" % (pos, ("xy" if (x, y) in mark else "bw")[s]), x0, y0)
                continue
            c.tile("/img/fiveinarow/%s.gif" % ("star" if (x, y) in stars else pos + "-e"), x0, y0)
            if my_turn:
                c.area("rect", (x0, y0, x0 + CELL - 1, y0 + CELL - 1), sq(x, y), "/move.htm", mv=sq(x, y))
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat and "Game over" not in v["status"]
    rows = []
    for i, colour in ((0, "black"), (1, "white")):
        rows.append('<tr><td><b>%s</b></td><td>%s%s</td><td>%s</td><td align="right">%d</td></tr>' % (
            "XO"[i], esc(names[i]), " (you)" if i == seat else "", colour, v["counts"][i]))
    side = '<table border="0" cellpadding="2">%s</table>\n' % "".join(rows)
    notes = []
    moves = len(v["order"][0]) + len(v["order"][1])
    if v["last"]:
        notes.append("Move %d: %s" % (moves, esc(sq(*v["last"]))))
    if v["five"]:
        notes.append("Winning line: %s" % esc(" ".join(sq(*p) for p in v["five"])))
    if v["rules"] in RULES_TEXT:
        notes.append(RULES_TEXT[v["rules"]] + ".")
    if notes:
        side += "<p>%s</p>" % "<br>\n".join(notes)
    if my_turn:
        side += ('\n<form action="/move.htm" method="get">\n%s\nOr type a point (like %s): '
                 '<input name="mv" size="4" maxlength="3"> <input type="submit" value="Play">\n</form>'
                 % (hidden_fields(link("/move.htm", t=tid)), sq(*nearest_empty(v, (v["n"] // 2, v["n"] // 2)))))
    hint = " - click an empty point" if my_turn else ""
    return board_html(board_image, v, tid, link, seat, sel, "Five in a Row board"), side, hint, ""


# ---- telnet --------------------------------------------------------------------------

def five_glyphs(mode):
    """The board's lines and the stones for a mode. The stones differ in shape (a solid and a shaded
    block, or X and O), not only in color."""
    if mode in ("pc", "utf8"):
        return {"nw": "┌", "n": "┬", "ne": "┐", "w": "├", "c": "┼", "e": "┤", "sw": "└", "s": "┴", "se": "┘",
                "h": "─", "v": "│", "stone": ("█", "▒")}
    return {"nw": "+", "n": "+", "ne": "+", "w": "+", "c": "+", "e": "+", "sw": "+", "s": "+", "se": "+",
            "h": "-", "v": "|", "stone": ("X", "O")}


BOARD_BG, CURSOR_BG, FIVE_BG = "43", "46", "42"
A_LINE = "0;30;" + BOARD_BG
A_MARK = "0;1;31;" + BOARD_BG
STONE_FG = ("0;30;", "0;1;37;")         # black, white


def with_bg(attr, bg):
    """The same attribute on another background color."""
    parts = [p for p in attr.split(";") if not (len(p) == 2 and p[0] == "4")]
    return ";".join(parts + [bg])




class FiveBase(GameBase):
    """What both Five in a Row screens share: the view, the events, typed points. Every empty point is a
    legal move (the game has no move list), so it is your turn whenever W says so."""

    HELP_MOVING = ["The arrows move the cursor over the points. Press Enter to place a",
                   "stone there. A click on a point does the same."]
    HELP_KEYS = []
    HELP_COMMANDS = ["Press / to type one. /h8 places a stone on h8."]
    HELP_REFERENCE = ["The board",
                      "The letters name the columns from the left, and the numbers name",
                      "the rows from the top. Brackets mark the last stone placed."]

    def reference(self):
        rules = self.rules_text()
        return list(self.HELP_REFERENCE) + (["", "The rules"] + textwrap.wrap(rules, 68) if rules else [])

    parse = staticmethod(parse_view)
    GAME = NAME
    MOVE_EXAMPLE = "/h8"

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        return cmd if MOVE_RE.match(cmd) else None

    def view_last(self):
        v = self.v
        if v and v.get("last") in (v.get("stones") or {}):
            return "%s placed a stone on %s." % (self.actor(v["stones"][v["last"]]), sq(*v["last"]))
        return ""

    def last_move(self, text):
        p = text.split()
        if p[0] == "PUT" and len(p) >= 3:
            return "%s placed a stone on %s." % (self.actor(p[1]), p[2])
        return None

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    def colour(self, seat):
        return "black" if seat == 0 else "white"

    def rules_text(self):
        return RULES_TEXT.get(self.v["rules"], "") if self.v else ""


class AnsiFive(FiveBase, AnsiGame):
    """Five in a Row, full screen: the board on the left (arrow-key cursor, the last stone in brackets),
    players, clock and log on the right."""

    HOTKEYS = "rt?"
    LOG_TOP, LOG_ROWS = 11, 9

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.G = five_glyphs(sess.mode)
        self.cur = None

    # ---- geometry --------------------------------------------------------
    def geometry(self):
        """-> board size, columns per point, rows per point, left point's column, top point's row.
        4 columns and 2 rows a point up to 10 by 10, then 3 columns up to 15 by 15, then 2: every size
        fits beside a panel of at least 28 columns. A 20 by 20 board starts on row 2 and ends on the hint's row,
        so its hint goes on the line below (compose)."""
        n = self.v["n"] if self.v else 15
        p, s = (4, 2) if n <= 10 else (3, 1) if n <= 15 else (2, 1)
        height = s * (n - 1) + 1
        return n, p, s, 5, max(2, 2 + (19 - height) // 2)

    def point_xy(self, x, y):
        n, p, s, bx, by = self.geometry()
        return bx + p * x, by + s * y

    def panel_x(self):
        n, p, s, bx, by = self.geometry()
        return max(40, bx + p * (n - 1) + 4)

    def snap(self):
        """Keep the cursor on the board; when it becomes your move, on an empty point."""
        n = self.geometry()[0]
        if self.cur is None:
            self.cur = (n // 2, n // 2)
        self.cur = (min(self.cur[0], n - 1), min(self.cur[1], n - 1))
        mine = self.my_turn()
        if mine and not self.was_my_turn and self.cur in self.v["stones"]:
            self.cur = nearest_empty(self.v, self.cur)
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def stone_attr(self, seat, bg=BOARD_BG):
        return STONE_FG[seat] + bg

    def compose(self):
        cv = Canvas()
        title_bar(cv, self.g, self.title_text())
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = self.sentence(self.hint)
        tall = self.geometry()[0] > 19              # the board's last row is the hint's: the hint goes below
        if not (tall and self.buf):
            cv.put(1, ROWS - 2 if tall else ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.my_turn():
            keys = self.keys("move", "place")
        elif self.me >= 0 and self.playing():
            keys = self.keys("look")
        else:
            keys = self.keys()
        self.keys_line(cv, keys)
        return cv

    def draw_board(self, cv):
        G, v = self.G, self.v
        n, p, s, bx, by = self.geometry()
        height = s * (n - 1) + 1
        started = bool(v and v["rows"])
        stones = v["stones"] if v else {}
        showcur = self.me >= 0 and self.playing() and self.lit() and self.cur is not None
        cur = self.cur if showcur else None
        for i in range(n):
            cv.put(bx + p * i, by - 1, chr(97 + i), A_AXIS_HI if cur and i == cur[0] else A_AXIS)
        right = bx + p * (n - 1)
        for r in range(height):
            cv.put(bx - 2, by + r, "  ", A_LINE)
            cv.put(right + 1, by + r, "  ", A_LINE)
            if r % s:                                   # between two rows of points
                cv.put(bx, by + r, (G["v"] + " " * (p - 1)) * (n - 1) + G["v"], A_LINE)
                continue
            y = r // s
            cv.put(bx - 4, by + r, "%2d" % (y + 1), A_AXIS_HI if cur and y == cur[1] else A_AXIS)
            cv.put(bx, by + r, (G["h"] * (p - 1)).join(G[position(x, y, n)] for x in range(n)), A_LINE)
            for x in range(n):
                cx, cy = self.point_xy(x, y)
                cv.hot_area(cx - p // 2, cy, p, 1, ("pt", x, y))
        mark, five = marked(v) if v else set(), set(v["five"]) if v else set()
        for (x, y), seat in stones.items():
            if not (0 <= x < n and 0 <= y < n):
                continue
            cx, cy = self.point_xy(x, y)
            bg = FIVE_BG if (x, y) in five else BOARD_BG
            glyph = G["stone"][seat]
            if p >= 3:
                ends = "[]" if (x, y) in mark else "()"
                cv.put(cx - 1, cy, ends[0], A_MARK if (x, y) in mark else self.stone_attr(seat, bg))
                cv.put(cx, cy, glyph, self.stone_attr(seat, bg))
                cv.put(cx + 1, cy, ends[1], A_MARK if (x, y) in mark else self.stone_attr(seat, bg))
            else:
                cv.put(cx, cy, glyph, self.stone_attr(seat, bg))
        if p == 2 and v and v["last"] in stones:       # narrow cells: the brackets go on the lines either side
            cx, cy = self.point_xy(*v["last"])
            cv.put(cx - 1, cy, "[", A_MARK)
            cv.put(cx + 1, cy, "]", A_MARK)
        if cur:
            cx, cy = self.point_xy(*cur)
            for x in range(cx - 1, cx + 2) if p >= 3 else (cx,):
                cv.at[cy][x] = with_bg(cv.at[cy][x], CURSOR_BG)
        if not started:
            w = 27
            self.draw_waiting(cv, bx + (p * (n - 1) + 1 - w) // 2, by + (height - 5) // 2, w)

    def draw_panel(self, cv):
        v = self.v
        px = self.panel_x()
        started = bool(v and v["rows"])
        players = []
        for i in (0, 1):
            c = v["counts"][i] if started else 0
            players.append((i, ("(%s)" % self.G["stone"][i], self.stone_attr(i)), "%d stone%s" % (c, "" if c == 1 else "s")))
        facts = []
        if started and v["five"]:
            facts.append("The line is %s." % " ".join(sq(*p) for p in v["five"]))
        if started:
            facts.append(self.rules_text() if len(self.rules_text()) <= COLS - 5 - px else
                         RULES_SHORT.get(v["rules"], ""))
        self.draw_panel_parts(cv, px, 2, COLS - 1 - px, players, facts)

    def hint(self):
        if not (self.v and self.v["rows"]):
            return self.waiting_hint()
        if self.over:
            text, attr = self.over_hint()
            return (text if len(text) <= COLS - 2 else self.over_sentence()), attr
        if self.me < 0:
            return self.watch_hint()
        if self.my_turn():
            p = sq(*self.cur)
            if self.cur in self.v["stones"]:
                return "Your turn. A stone is on %s already, so pick an empty point." % p, A_DIM
            return "Your turn. Press Enter to place a stone on %s." % p, A_TEXT
        return self.their_hint(self.v["turn"])

    def cursor_at(self):
        if self.me >= 0 and self.playing() and self.cur is not None:
            return self.point_xy(*self.cur)
        return None

    # ---- keys --------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "pt" and self.me >= 0 and self.playing():
            self.cur = (value[1], value[2])
            return await self.place()             # a click does what Enter does there
        return None

    def on_arrow(self, k):
        if self.v and self.v["rows"] and self.cur is not None:
            n = self.geometry()[0]
            dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
            self.cur = ((self.cur[0] + dx) % n, (self.cur[1] + dy) % n)

    async def on_enter(self):
        return await self.place()

    async def place(self):
        if not self.my_turn():
            return self.say(self.not_yours())
        if self.cur in self.v["stones"]:
            return self.say("A stone is on %s already, so pick an empty point." % sq(*self.cur), A_RED)
        err = await self.move(sq(*self.cur))
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainFive(FiveBase, PlainGame):
    """Five in a Row for plain terminals: the board printed line by line, typed points at a prompt."""

    BOARD_KINDS = ("PUT", "JOIN", "LEAVE", "START", "END")

    def lines(self):
        v = self.v
        out = [""] + self.plain_header()
        if not v["rows"]:
            return out + self.plain_waiting()
        n, stones = v["n"], v["stones"]
        board = ["     " + " ".join(chr(97 + i) for i in range(n))]
        for y in range(n):
            cells = [" "] * (2 * n + 1)
            for x in range(n):
                s = stones.get((x, y))
                cells[2 * x + 1] = "." if s is None else "XO"[s]
            if v["last"] and v["last"][1] == y and v["last"] in stones:
                cells[2 * v["last"][0]], cells[2 * v["last"][0] + 2] = "[", "]"
            board.append(" %2d %s" % (y + 1, "".join(cells)))
        facts = ["The line is %s." % " ".join(sq(*p) for p in v["five"])] if v["five"] else []
        side = self.side_text([(i, "XO"[i], "%d" % v["counts"][i]) for i in (0, 1)], facts + [self.rules_text()],
                              min(32, 80 - (2 * n + 8)))
        return out + self.beside(board, side, 2 * n + 8) + self.plain_log()

    def turn_prompt(self):
        v = self.v
        n = v["n"]
        return "Your turn. Type a point such as /%s." % sq(*nearest_empty(v, v["last"] or (n // 2, n // 2)))

AnsiScreen, PlainScreen = AnsiFive, PlainFive
