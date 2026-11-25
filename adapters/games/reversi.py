"""Reversi (reversi/1, docs/types/reversi-1.md): the web board and the telnet screens.

The worked example in docs/games-howto.md: each section below is one hook of
the game module interface (adapters/games/__init__.py).
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_BRIGHT, A_DIM, A_GRID, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS,
                      ROWS, SQUARE_RE, AnsiGame, Canvas, GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar,
                      title_bar, turn_text)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture
from ..web import board_html, esc, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "reversi/1"
NAME = "Reversi"
ORDER = 1                   # first in the build order (README.md)
PLAYERS = "2"
ABOUT = (
    "Two players take turns placing discs on a square board, each disc showing its owner's color. "
    "Placing a disc so that a straight line of the opponent's discs lies between it and another of the "
    "mover's own discs turns that whole line over to the mover's color; every move must turn over at "
    "least one disc. When neither player can move, the one with more discs on the board wins.",
    ["Standard rules: an 8 by 8 board with four discs in the center to start; the first seat moves first; "
     "a player with no legal move passes automatically; the game ends when neither player can move or "
     "the board is full, and more discs wins.",
     "Board size: the game type takes any even size from 4 by 4 to 10 by 10. The web site, telnet and IOS "
     "clients start 8 by 8 games; other sizes can be asked for over the native protocol.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])


# ---- the view and the events (docs/types/reversi-1.md) ------------------------------

def parse_view(lines):
    """VUE lines for reversi/1 -> dict. rows (the board rows) is empty until the game starts."""
    v = {"rows": [], "counts": (0, 0), "turn": -1, "moves": [], "status": "", "n": 8, "seq": 0, "clock": None}
    for l in lines:
        p = l.split(" ", 2)
        tag = p[0]
        if tag == "V":
            v["seq"] = int(l.split()[2])
        elif tag == "BRD":
            v["n"] = int(l.split()[1])
        elif tag == "R" and len(p) == 3:
            v["rows"].append(p[2])
        elif tag == "C":
            c = l.split()
            v["counts"] = (int(c[1]), int(c[2]))
        elif tag == "W":
            v["turn"] = int(l.split()[1])
        elif tag == "M" and len(p) > 1:
            v["moves"].append(l.split()[1])
        elif tag == "CLK":
            c = l.split()
            v["clock"] = (int(c[1]), int(c[2]), int(c[3]))
        elif tag == "S":
            v["status"] = l[2:]
    return v


def describe_event(text, names):
    """Reversi's own events -> a line for the log (None for the shared ones: PASS, END ...)."""
    p = text.split()
    if p[0] == "PUT" and len(p) >= 3:
        i = int(p[1])
        who = names[i] if 0 <= i < len(names) else "seat %d" % i
        return "%s plays %s (flips %d)" % (who, p[2], len(p) - 4)
    return None


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^[a-z][0-9]{1,2}\Z")     # a square: d3
SEL_RE = None                                  # nothing to pick up first


def _piece(kind):
    # palette: 0 green, 1 white, 2 black, 3 yellow
    size = 32
    px = []
    for y in range(size):
        for x in range(size):
            c = 0
            d2 = (x - 15.5) ** 2 + (y - 15.5) ** 2
            if kind == "b" and d2 <= 12.5 ** 2:
                c = 2
            elif kind == "w" and d2 <= 12.5 ** 2:
                c = 1
            elif kind == "h" and d2 <= 3.5 ** 2:
                c = 3
            if x == 0 or y == 0:
                c = 2 if c == 0 else c
            px.append(c)
    return make_gif(size, size, [(0, 128, 56), (255, 255, 255), (0, 0, 0), (255, 220, 0)], px)


def images():
    """Empty, black, white and hint squares. (Reversi's came first and sit at /img/*.gif; a new game puts
    its images under /img/<game>/.)"""
    return {"/img/%s.gif" % k: _piece(k) for k in "ebwh"}


SQ, MARGIN = 32, 16         # a square, and the room for the letters and numbers round the board


def board_image(v, seat, sel):
    """The whole board as one image (adapters/board.py): the squares from images(), the column letters and
    row numbers, and on the player's turn an area per legal square that plays it."""
    n = v["n"]
    legal = set(v["moves"]) if seat >= 0 and v["turn"] == seat else set()
    c = Picture(MARGIN + n * SQ + 1, MARGIN + n * SQ + 1, images=images)
    c.labels(MARGIN, MARGIN, SQ, n, n, (0, 0, 0))
    for y, row in enumerate(v["rows"]):
        for x, ch in enumerate(row):
            sq = "%s%d" % (chr(ord("a") + x), y + 1)
            x0, y0 = MARGIN + x * SQ, MARGIN + y * SQ
            kind = {"0": "b", "1": "w"}.get(ch, "h" if sq in legal else "e")
            c.tile("/img/%s.gif" % kind, x0, y0)
            if kind in "bw":            # an outline, so the discs stay apart from the board in black and white
                c.disc(x0 + 15.5, y0 + 15.5, 12.6, (0, 0, 0) if kind == "w" else (255, 255, 255),
                       r_in=11.6 if kind == "w" else 10.4)
            if kind == "h":
                c.area("rect", (x0, y0, x0 + SQ - 1, y0 + SQ - 1), sq, "/move.htm", mv=sq)
    c.fill(MARGIN + n * SQ, MARGIN, 1, n * SQ + 1, (0, 0, 0))      # the right and bottom edges
    c.fill(MARGIN, MARGIN + n * SQ, n * SQ + 1, 1, (0, 0, 0))
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat
    side = ("<table border=\"0\" cellpadding=\"3\"><tr><td><b>X</b></td><td>%s</td><td align=\"right\">%d</td></tr>"
            "<tr><td><b>O</b></td><td>%s</td><td align=\"right\">%d</td></tr></table>"
            % (esc(names[0]), v["counts"][0], esc(names[1]), v["counts"][1]))
    hint = " - click a yellow dot" if my_turn and v["moves"] else ""
    board = board_html(board_image, v, tid, link, seat, sel,
                       "Reversi board: X %d, O %d" % (v["counts"][0], v["counts"][1]))
    return board, side, hint, ""


# ---- telnet --------------------------------------------------------------------------

HELP = [
    "Arrow keys   move the cursor",
    "Enter/Space  place a disc on the cursor square",
    "Mouse        click a legal square to play it",
    "T            talk: type a line to the table",
    "R            resign (asks first)",
    "Esc          back to the menu; the game goes on",
    "Ctrl-L       redraw the screen",
    "",
    "Typed commands work too: a square like d3,",
    "say <text>, resign, redraw, menu, help.",
    "",
    "Leaving or logging off never resigns. Your",
    "turn timer runs while it is your move.",
]
GAME_HELP = HELP            # its old name in adapters/telnet.py


class ReversiBase(GameBase):
    """What both Reversi screens share: the view, the events, typed squares."""

    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        return cmd if SQUARE_RE.match(cmd) else None


class AnsiReversi(ReversiBase, AnsiGame):
    """Reversi, full screen: the board on the left (arrow-key cursor,
    legal-move hints), players, clock and log on the right."""

    HOTKEYS = "rt?"
    LOG_TOP, LOG_ROWS = 9, 11
    TITLE, HELP = "Reversi", HELP

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = (3, 2)

    # ---- geometry --------------------------------------------------------
    def geometry(self):
        """-> board size, rows per board row (2 with grid lines between
        rows, 1 when that would not fit), frame left, frame top."""
        n = self.v["n"] if self.v else 8
        return n, (2 if 2 * n + 1 <= 17 else 1), 5, 3

    def cell_xy(self, x, y):
        n, p, bx, by = self.geometry()
        return bx + 1 + 4 * x, by + 1 + p * y

    @staticmethod
    def sq(c):
        return "%s%d" % (chr(97 + c[0]), c[1] + 1)

    def snap(self):
        """When it becomes your move, put the cursor on a legal square."""
        mine = self.my_turn()
        n = self.geometry()[0]
        self.cur = (min(self.cur[0], n - 1), min(self.cur[1], n - 1))
        if mine and not self.was_my_turn and self.sq(self.cur) not in self.v["moves"]:
            m = sorted(self.v["moves"])[0]
            self.cur = (ord(m[0]) - 97, int(m[1:]) - 1)
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def disc(self, seat, bg="42"):
        if seat == 0:
            return self.g["dark"], "0;30;" + bg
        return self.g["light"], "0;1;37;" + bg

    def compose(self):
        cv = Canvas()
        right = "watching" if self.me < 0 else "you play %s" % ("black" if self.me == 0 else "white")
        title_bar(cv, self.g, "Reversi %s table %s" % (self.g["v"], self.tid), right)
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.me >= 0 and self.playing():
            keys = [("Arrows", "move"), ("Enter", "place"), ("T", "talk"), ("R", "resign"),
                    ("?", "help"), ("Esc", "menu")]
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_board(self, cv):
        g, v = self.g, self.v
        n, p, bx, by = self.geometry()
        rows = v["rows"] if v else []
        showcur = self.me >= 0 and self.playing()
        legal = set(v["moves"]) if self.my_turn() else set()
        for i in range(n):
            hi = showcur and i == self.cur[0]
            cv.put(bx + 2 + 4 * i, by - 1, chr(97 + i), A_AXIS_HI if hi else A_AXIS)
        height = 2 * n + 1 if p == 2 else n + 2
        for r in range(height):
            if r == 0:
                line = g["TL"] + g["Tt"].join([g["H"] * 3] * n) + g["TR"]
            elif r == height - 1:
                line = g["BL"] + g["Tb"].join([g["H"] * 3] * n) + g["BR"]
            elif p == 2 and r % 2 == 0:
                line = g["Tl"] + g["x"].join([g["h"] * 3] * n) + g["Tr"]
            else:
                line = g["V"] + g["v"].join(["   "] * n) + g["V"]
            cv.put(bx, by + r, line, A_GRID)
        for y in range(n):
            hi = showcur and y == self.cur[1]
            cv.put(bx - 3, by + 1 + p * y, "%2d" % (y + 1), A_AXIS_HI if hi else A_AXIS)
            for x in range(n):
                bg = "46" if showcur and (x, y) == self.cur else "42"
                c = rows[y][x] if y < len(rows) and x < len(rows[y]) else "."
                if c in "01":
                    text, attr = self.disc(int(c), bg)
                elif self.sq((x, y)) in legal:
                    text, attr = " %s " % g["hint"], "0;1;33;" + bg
                else:
                    text, attr = "   ", "0;30;" + bg
                cv.put(*self.cell_xy(x, y), text, attr)
                cx, cy = self.cell_xy(x, y)
                cv.hot_area(cx, cy, 3, 1, ("sq", x, y))
        if not rows:
            w = 27
            self.draw_waiting(cv, bx + (4 * n + 1 - w) // 2, by + (height - 5) // 2, w)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        n, p, bx, by = self.geometry()
        px = max(40, bx + 4 * n + 3)
        pw = COLS - 1 - px
        inner = pw - 4
        box(cv, g, px, 2, pw, 19, A_BORDER, "Players", tattr=A_HEAD)
        rows = v["rows"] if v else []
        for i in (0, 1):
            y = 3 + i
            tomove = self.playing() and v["turn"] == i
            empty = i >= len(self.names) or self.names[i] == "-"
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            cv.put(px + 4, y, *self.disc(i))
            label = ("(empty seat)" if empty else self.names[i]) + (" (you)" if i == self.me else "")
            count = v["counts"][i] if rows else 0
            cnt = ("%2d disc%s" % (count, "" if count == 1 else "s")).ljust(8) if inner >= 34 else "%2d" % count
            cv.put(px + 8, y, label[:inner - 6 - len(cnt)], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if rows:
                cv.put(px + pw - 2 - len(cnt), y, cnt, A_BRIGHT if tomove else A_TEXT)
        rule(cv, g, px, 5, pw, A_BORDER)
        if not rows:
            turn, tattr = "Waiting for an opponent", A_YELLOW
        elif self.over:
            turn, tattr = self.over, A_YELLOW
        elif self.my_turn():
            turn, tattr = "Your move", A_YELLOW
        elif self.me >= 0 and v["turn"] == self.me:
            turn, tattr = "Your turn: no legal move", A_TEXT
        else:
            turn, tattr = "%s to move" % self.name(v["turn"]), A_TEXT
        cv.put(px + 2, 6, turn[:inner], tattr)
        c = self.clock()
        if c:
            seat, left, limit = c
            urgent = seat == self.me and left < 60
            clock = "Clock: %s left of %s" % (fmt_secs(left), turn_text(limit))
            cv.put(px + 2, 7, clock[:inner], A_RED if urgent else A_TEXT)
        elif self.turn_secs and not self.over:
            cv.put(px + 2, 7, ("Time per turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        rule(cv, g, px, 8, pw, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, px + 2, inner)

    def hint(self):
        if not (self.v and self.v["rows"]):
            return ("Table %s starts when someone joins: telnet, the web (port 6500) or IOS (6503)."
                    % self.tid, A_DIM)
        if self.over:
            return "%s. Esc goes back to the menu." % self.over, A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        if self.my_turn():
            sq = self.sq(self.cur)
            if sq in self.v["moves"]:
                return "Your move. %s: Enter or Space places a disc here." % sq, A_TEXT
            return "Your move. %s is not a legal square; legal ones show %s." % (sq, self.g["hint"]), A_DIM
        return ("Waiting for %s. Leaving or logging off never resigns." % self.name(self.v["turn"]),
                A_DIM)

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            x, y = self.cell_xy(*self.cur)
            return x + 1, y
        return None

    # ---- keys --------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "sq" and self.me >= 0 and self.playing():
            self.cur = (value[1], value[2])
            if self.my_turn() and self.sq(self.cur) in self.v["moves"]:
                return await self.place()       # a click on a legal square plays it
        return None

    def on_arrow(self, k):
        if self.v and self.v["rows"]:
            n = self.geometry()[0]
            dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
            self.cur = ((self.cur[0] + dx) % n, (self.cur[1] + dy) % n)

    async def on_enter(self):
        return await self.place()

    async def place(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        if not self.my_turn():
            return self.say("It is %s's move." % self.name(self.v["turn"]))
        err = await self.move(self.sq(self.cur))
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainReversi(ReversiBase, PlainGame):
    """Reversi for plain terminals: the board printed line by line, typed
    moves at a prompt."""

    BOARD_KINDS = ("PUT", "PASS", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" A square like d3 plays there. say <text> talks to the table.\n"
                 " resign gives up (it asks first). menu goes back to the menu;\n"
                 " the game goes on and leaving never resigns. redraw or Enter\n"
                 " shows the board again.")

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you play %s (%s)" % ("black" if me == 0 else "white", "XO"[me])
        out = ["", "== Reversi, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet, the web" % self.tid,
                          "(port 6500) or an IOS client (port 6503). Type menu to go back;",
                          "the table stays open."]
        n = v["n"]
        legal = set(v["moves"]) if self.my_turn() else set()
        side = {}
        for i in (0, 1):
            mark = ">" if self.playing() and v["turn"] == i else " "
            side[i] = "%s %s %-12s %2d" % (mark, "XO"[i], self.name(i), v["counts"][i])
        if self.over:
            side[3] = self.over
        elif self.my_turn():
            side[3] = "Your move."
        else:
            side[3] = "%s to move." % self.name(v["turn"])
        if me >= 0 and not self.over:
            side[5], side[6] = "Type a square to play, or", "say <text>, resign, menu, help."
        else:
            side[5] = "Type say <text>, menu or help."
        out.append("      " + "".join(" %s " % chr(97 + i) for i in range(n)))
        out.append("    +" + "-" * (3 * n + 2) + "+")
        for y, row in enumerate(v["rows"]):
            cells = "".join(" %s " % ("X" if c == "0" else "O" if c == "1" else
                                      "*" if "%s%d" % (chr(97 + x), y + 1) in legal else ".")
                            for x, c in enumerate(row))
            out.append((" %2d | %s |    %s" % (y + 1, cells, side.get(y, ""))).rstrip())
        out.append("    +" + "-" * (3 * n + 2) + "+")
        if not self.over:
            out.append(" " + clock_text(v, self.names))
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.my_turn():
            return "Your move (like %s) > " % sorted(self.v["moves"])[0]
        return "> "


AnsiScreen, PlainScreen = AnsiReversi, PlainReversi
