"""Mancala (mancala/1, docs/types/mancala-1.md): the web board and the telnet screens.

Two rows of six pits and two stores. Each player sees their own row along the
bottom, pit 1 on the left and their store on the right; the opponent's row runs
along the top, right to left, with the opponent's store on the left. A move is
a pit number, 1 to 6. Two rulesets: kalah (store rules) and oware (abapa).
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS, ROWS,
                      AnsiGame, Canvas, GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar, title_bar,
                      turn_text)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture
from ..web import board_html, esc, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "mancala/1"
NAME = "Mancala"
ORDER = 7                   # seventh in the build order (README.md)
PLAYERS = "2"
ABOUT = (
    "Two players face each other across a board of two rows of six pits, with a store at each end; each "
    "player owns the row nearer to them and the store on their right. A turn picks up every seed in one of "
    "the player's own pits and sows them one by one into the pits that follow, counterclockwise, and where the "
    "last seed lands decides whether anything is captured. The player who gathers more seeds wins.",
    ["Store rules (the default): four seeds in each pit to start (three to six can be asked for over the "
     "native protocol); sowing passes through the mover's own store but not the opponent's. A last seed in "
     "the mover's own store earns another turn; a last seed in an empty pit of the mover's own row captures "
     "it and the seeds in the pit opposite, if there are any. When either row is empty, each player adds the "
     "seeds left on their side to their store, and the bigger store wins.",
     "Oware rules (abapa): four seeds in each pit, and the stores only keep captures. A sowing of twelve or "
     "more seeds skips the pit it started from. When the last seed makes a pit on the opponent's row hold "
     "two or three, those seeds are captured, with each pit just before it on that row that also holds two "
     "or three; a move that would capture every seed on the opponent's row (a grand slam) captures nothing.",
     "Oware rules, the end: a player must leave the opponent seeds to play whenever a move can; more than "
     "24 seeds wins at once and 24 each is a draw. A player who cannot move takes the seeds on their own "
     "side and the game ends; so does an endless cycle (a repeated position, or 200 moves without a "
     "capture), with each player taking the seeds on their side.",
     "The web site and telnet ask which rules a new table plays (store rules first); over the native "
     "protocol the ruleset is the word after the game: NEW mancala oware.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])

PITS = 6
RULE_NAMES = {"kalah": "store rules", "oware": "Oware rules"}


def rules_line(v):
    """'Store rules, 4 seeds a pit' or 'Oware rules (abapa)'."""
    if v["rules"] == "oware":
        return "Oware rules (abapa)"
    return "%s, %d seeds a pit" % (RULE_NAMES.get(v["rules"], v["rules"]).capitalize(), v["seeds"])


# ---- the view and the events (docs/types/mancala-1.md) ------------------------------

def parse_view(lines):
    """VUE lines for mancala/1 -> dict. rows is True once there is a board (the game has started);
    b is the 14 counts in sowing order (seat 0's pits 1-6, its store, seat 1's pits 1-6, its store)."""
    v = {"rows": False, "b": [0] * (2 * PITS + 2), "turn": -1, "moves": [], "status": "", "seq": 0,
         "clock": None, "rules": "kalah", "seeds": 4, "last": None}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) >= 3:
            v["seq"] = int(f[2])
        elif tag == "B" and len(f) == 2 * PITS + 3:
            v["b"] = [int(x) for x in f[1:]]
            v["rows"] = True
        elif tag == "RU" and len(f) >= 3:
            v["rules"], v["seeds"] = f[1], int(f[2])
        elif tag == "LM" and len(f) == 3:
            v["last"] = (int(f[1]), int(f[2]))
        elif tag == "W" and len(f) == 2:
            v["turn"] = int(f[1])
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) == 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    return v


def pit(v, seat, p):
    return v["b"][seat * (PITS + 1) + p - 1]


def store(v, seat):
    return v["b"][seat * (PITS + 1) + PITS]


def seats_shown(me):
    """(bottom seat, top seat): the viewer's own row at the bottom; a watcher sees seat 0 there."""
    bottom = me if me in (0, 1) else 0
    return bottom, 1 - bottom


def _plural(n, word="seed"):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def describe_event(text, names):
    """Mancala's own events -> a line for the log (None for the shared ones: TURN, END ...)."""
    p = text.split()

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) else "seat %d" % i
    k = p[0]
    if k == "SOW" and len(p) >= 4:
        return "%s sows pit %s (%s)" % (nm(p[1]), p[2], _plural(int(p[3])))
    if k == "CAP" and len(p) == 3:
        return "%s captures %s" % (nm(p[1]), _plural(int(p[2])))
    if k == "GRAND" and len(p) == 2:
        return "Grand slam: %s captures nothing" % nm(p[1])
    if k == "AGAIN" and len(p) == 2:
        return "%s ends in the store and moves again" % nm(p[1])
    if k == "SWEEP" and len(p) >= 3:
        why = {"empty": "a row is empty", "nofeed": "no move can feed the other side",
               "cycle": "an endless cycle"}.get(p[3] if len(p) > 3 else "", "")
        return "%s takes the %s left on their row%s" % (nm(p[1]), _plural(int(p[2])),
                                                         " (%s)" % why if why else "")
    return None


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^[1-6]\Z")              # a pit: 3
SEL_RE = None                                  # nothing to pick up first
PIT_W, PIT_H, STORE_H = 40, 40, 88
MAX_DOTS = 12                                  # a pit image shows up to 12 seeds; the count is text
_PALETTE = [(153, 102, 51), (64, 38, 16), (240, 228, 190), (255, 220, 0), (255, 255, 255)]


def _spots():
    """Where seeds sit in a pit image, nearest the middle first."""
    offs = (-11.25, -3.75, 3.75, 11.25)
    pts = [(19.5 + dx, 19.5 + dy) for dy in offs for dx in offs if abs(dx) + abs(dy) < 20]   # no corners
    return sorted(pts, key=lambda p: ((p[0] - 19.5) ** 2 + (p[1] - 19.5) ** 2, p[1], p[0]))


def _pit_gif(n, rim):
    """A round pit holding n seeds (as dots); rim 3 is yellow (a legal pit), 4 white (the last move)."""
    spots = _spots()[:n]
    px = []
    for y in range(PIT_H):
        for x in range(PIT_W):
            d = ((x - 19.5) / 17.0) ** 2 + ((y - 19.5) / 17.0) ** 2
            c = 0
            if d <= 1.0:
                c = 1
                if any((x - sx) ** 2 + (y - sy) ** 2 <= 2.4 ** 2 for sx, sy in spots):
                    c = 2
            elif d <= 1.3 and rim:
                c = rim
            px.append(c)
    return make_gif(PIT_W, PIT_H, _PALETTE, px)


def _store_gif():
    px = []
    for y in range(STORE_H):
        for x in range(PIT_W):
            d = ((x - 19.5) / 17.0) ** 2 + ((y - 43.5) / 41.0) ** 2
            px.append(1 if d <= 1.0 else 0)
    return make_gif(PIT_W, STORE_H, _PALETTE, px)


def images():
    """Pits with 0 to 12 seeds, plain (p), legal (h, a yellow rim) and last moved (l, a white rim), and the
    store, under /img/mancala/."""
    out = {"/img/mancala/store.gif": _store_gif()}
    for kind, rim in (("p", 0), ("h", 3), ("l", 4)):
        for n in range(MAX_DOTS + 1):
            out["/img/mancala/%s%d.gif" % (kind, n)] = _pit_gif(n, rim)
    return out


PAD, COL = 6, 48            # the board's edge; a pit's column (the pit and the gap after it)
WOODEN = _PALETTE[0]


def board_image(v, seat, sel):
    """The board as one image: the opponent's pits along the top (6 to 1, left to right) with their store
    on the left, yours along the bottom (1 to 6) with your store on the right; each pit's count under
    it and the pit numbers outside. On your turn a legal pit is an area that sows it."""
    legal = set(v["moves"]) if seat >= 0 and v["turn"] == seat else set()
    bottom, top = seats_shown(seat)
    w = 2 * PAD + 2 * PIT_W + 2 * 8 + PITS * COL - 8
    h = 2 * PAD + 2 * 10 + 2 * (PIT_H + 12)
    c = Picture(w, h, bg=WOODEN, images=images)
    ink, light = _PALETTE[1], _PALETTE[4]
    c.frame(0, 0, w, h, ink)
    x_pits = PAD + PIT_W + 8
    y_top, y_bot = PAD + 10, PAD + 10 + PIT_H + 12

    def pit_at(s, p, x, y, clickable):
        n = pit(v, s, p)
        kind = "h" if clickable and str(p) in legal else "l" if v["last"] == (s, p) else "p"
        c.tile("/img/mancala/%s%d.gif" % (kind, min(n, MAX_DOTS)), x, y)
        c.text(x + PIT_W // 2, y + PIT_H + 2, str(n), light, align="center")
        if kind == "h":
            c.area("rect", (x, y, x + PIT_W - 1, y + PIT_H + 10), "pit %d, %d seed%s" % (p, n, "" if n == 1 else "s"),
                   "/move.htm", mv=str(p))
    for i, p in enumerate(range(PITS, 0, -1)):
        x = x_pits + i * COL
        c.text(x + PIT_W // 2, 3, str(p), light, align="center")
        pit_at(top, p, x, y_top, False)
    for i, p in enumerate(range(1, PITS + 1)):
        x = x_pits + i * COL
        c.text(x + PIT_W // 2, h - 10, str(p), light, align="center")
        pit_at(bottom, p, x, y_bot, True)
    y_store = PAD + 10 + (2 * (PIT_H + 12) - STORE_H - 10) // 2
    for s, x in ((top, PAD), (bottom, w - PAD - PIT_W)):
        c.tile("/img/mancala/store.gif", x, y_store)
        c.text(x + PIT_W // 2, y_store + STORE_H + 2, str(store(v, s)), light, align="center")
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat
    bottom, top = seats_shown(seat)
    rows = []
    for where, s in (("Top row, left store", top), ("Bottom row, right store", bottom)):
        rows.append('<tr><td>%s%s</td><td><b>%s</b></td><td align="right">%d</td></tr>' % (
            "&gt; " if v["turn"] == s else "", esc(names[s]) + (" (you)" if s == seat else ""), where,
            store(v, s)))
    side = ('<table border="0" cellpadding="3">%s</table>\n<p><font size="2">%s. Seeds are sown '
            "counterclockwise: left to right along the bottom row.</font></p>" % ("".join(rows), esc(rules_line(v))))
    hint = " - click one of your pits with a yellow rim" if my_turn and v["moves"] else ""
    return board_html(board_image, v, tid, link, seat, sel, "Mancala board"), side, hint, ""


# ---- telnet --------------------------------------------------------------------------

HELP = [
    "Left/Right   move the cursor along your pits",
    "Enter/Space  sow the seeds of the cursor pit",
    "Mouse        click one of your pits to sow it",
    "1 to 6       typed, then Enter: sow that pit",
    "T            talk: type a line to the table",
    "R            resign (asks first)",
    "Esc          back to the menu; the game goes on",
    "Ctrl-L       redraw the screen",
    "",
    "Your pits are the bottom row, 1 to 6 from the",
    "left, and your store is on the right. Typed",
    "commands: say <text>, resign, redraw, menu, help.",
    "Leaving or logging off never resigns.",
]

LEGEND = {
    "kalah": ["Sown counterclockwise, one seed a pit,",
              "through the mover's store, not the other.",
              "Last seed in the mover's store: move again.",
              "Last seed in an empty own pit: it and the",
              "seeds opposite go to the mover's store."],
    "oware": ["Sown counterclockwise, one seed a pit;",
              "stores only keep captures. A last seed that",
              "makes 2 or 3 on the far row captures them,",
              "and the 2s and 3s just before. Feed an",
              "empty side. More than 24 seeds wins."],
}


def mancala_glyphs(mode):
    """The board frame's extra joints, the seed dot and the cursor marks for a mode (CP437 shapes in pc
    and utf8, ASCII in ansi)."""
    if mode in ("pc", "utf8"):
        return {"Td": "╦", "Bd": "╩", "seed": "∙", "cl": "»", "cr": "«"}
    return {"Td": "+", "Bd": "+", "seed": "o", "cl": ">", "cr": "<"}


A_FRAME = "0;30;43"         # the board: black lines on brown
A_PIT = "0;1;37;40"         # a pit's or a store's inside


class MancalaBase(GameBase):
    """What both Mancala screens share: the view, the events, typed pits."""

    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        if MOVE_RE.match(cmd):
            return cmd
        if cmd in ("pit", "sow") and len(words) > 1 and MOVE_RE.match(words[1]):
            return words[1]
        return None

    def rules_name(self):
        return RULE_NAMES.get(self.v["rules"], "store rules") if self.v else "store rules"


class AnsiMancala(MancalaBase, AnsiGame):
    """Mancala, full screen: the board on the left (a cursor along the player's own pits), players,
    clock, rules and log on the right."""

    HOTKEYS = "rt?"
    LOG_TOP, LOG_ROWS = 10, 10
    TITLE, HELP = "Mancala", HELP
    BX, BY = 2, 4               # the board frame's top left corner
    PX = 46                     # the panel's left edge

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = 1
        self.mg = mancala_glyphs(sess.mode)

    # ---- geometry --------------------------------------------------------
    def pit_x(self, col):
        """The left edge of display column col's pit (0-5, left to right): 4 cells wide."""
        return self.BX + 7 + 5 * col

    def pit_xy(self, seat, p):
        """Where seat's pit p is drawn: (left x, middle row)."""
        bottom, _ = seats_shown(self.me)
        if seat == bottom:
            return self.pit_x(p - 1), self.BY + 6
        return self.pit_x(PITS - p), self.BY + 2

    def legal(self):
        return set(self.v["moves"]) if self.my_turn() else set()

    def snap(self):
        """When it becomes your move, put the cursor on a pit you can sow."""
        mine = self.my_turn()
        if mine and not self.was_my_turn and str(self.cur) not in self.v["moves"]:
            self.cur = min(int(m) for m in self.v["moves"])
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def compose(self):
        cv = Canvas()
        rn = self.rules_name()
        right = "watching" if self.me < 0 else "you play the bottom row"
        title_bar(cv, self.g, "Mancala %s table %s (%s)" % (self.g["v"], self.tid, rn), right)
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.me >= 0 and self.playing():
            keys = [("Arrows", "move"), ("Enter", "sow"), ("T", "talk"), ("R", "resign"),
                    ("?", "help"), ("Esc", "menu")]
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_frame(self, cv):
        g, mg, bx, by = self.g, self.mg, self.BX, self.BY
        cells = [g["H"] * 4] * PITS
        cv.put(bx, by, g["TL"] + g["H"] * 5 + mg["Td"] + g["Tt"].join(cells) + mg["Td"] + g["H"] * 5 + g["TR"],
               A_FRAME)
        cv.put(bx, by + 4, g["V"] + " " * 5 + g["Tl"] + g["x"].join([g["h"] * 4] * PITS) + g["Tr"] + " " * 5
               + g["V"], A_FRAME)
        cv.put(bx, by + 8, g["BL"] + g["H"] * 5 + mg["Bd"] + g["Tb"].join(cells) + mg["Bd"] + g["H"] * 5 + g["BR"],
               A_FRAME)
        for y in list(range(by + 1, by + 4)) + list(range(by + 5, by + 8)):
            cv.put(bx, y, g["V"] + " " * 5 + g["V"] + g["v"].join(["    "] * PITS) + g["V"] + " " * 5 + g["V"],
                   A_FRAME)
        for y in range(by + 1, by + 8):         # the stores' and the pits' insides are dark
            cv.put(bx + 1, y, " " * 5, A_PIT)
            cv.put(bx + 37, y, " " * 5, A_PIT)
            if y != by + 4:
                for c in range(PITS):
                    cv.put(self.pit_x(c), y, "    ", A_PIT)

    def draw_pit(self, cv, seat, p, legal, showcur):
        x, y = self.pit_xy(seat, p)
        n = pit(self.v, seat, p)
        mine = seat == self.me
        cur = showcur and mine and p == self.cur
        last = self.v["last"] == (seat, p)
        bg = "46" if cur else "44" if last else "40"
        dots = self.mg["seed"] * min(n, 4), self.mg["seed"] * min(max(n - 4, 0), 4)
        seed_attr = "0;30;46" if cur else "0;33;" + bg
        cv.put(x, y - 1, dots[0].ljust(4), seed_attr)
        cv.put(x, y + 1, dots[1].ljust(4), seed_attr)
        count = "%2d" % n
        if cur:
            text, attr = self.mg["cl"] + count + self.mg["cr"], "0;1;30;46"
        elif str(p) in legal and mine:
            text, attr = self.g["hint"] + count + self.g["hint"], "0;1;33;" + bg
        else:
            text, attr = " %s " % count, "0;1;37;" + bg
        cv.put(x, y, text, attr)
        if mine:
            cv.hot_area(x, y - 1, 4, 3, ("pit", p))

    def draw_board(self, cv):
        v, bx, by = self.v, self.BX, self.BY
        bottom, top = seats_shown(self.me)
        started = bool(v and v["rows"])
        showcur = self.me >= 0 and self.playing()
        self.draw_frame(cv)
        for c in range(PITS):
            hi = showcur and c + 1 == self.cur
            cv.put(self.pit_x(c) + 2, by - 1, str(PITS - c), A_AXIS)
            cv.put(self.pit_x(c) + 2, by + 9, str(c + 1), A_AXIS_HI if hi else A_AXIS)
        cv.put(bx, by - 2, ("%s: top row, store left" % self.name(top))[:43], A_TEXT)
        you = " (you)" if self.me == bottom else ""
        cv.put(bx, by + 10, ("%s%s: bottom row, store right" % (self.name(bottom), you))[:43], A_TEXT)
        if not started:
            self.draw_waiting(cv, bx + 8, by + 2, 27)
            return
        legal = self.legal()
        for s in (0, 1):
            for p in range(1, PITS + 1):
                self.draw_pit(cv, s, p, legal, showcur)
        for s, x in ((top, bx + 1), (bottom, bx + 37)):
            cv.put(x, by + 3, "store", "0;36;40")
            cv.put(x, by + 4, ("%d" % store(v, s)).center(5), A_PIT)
        for i, line in enumerate(LEGEND.get(v["rules"], LEGEND["kalah"])):
            cv.put(bx, by + 12 + i, line[:43], A_DIM)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        px = self.PX
        pw = COLS - 1 - px
        inner = pw - 4
        box(cv, g, px, 2, pw, 19, A_BORDER, "Players", tattr=A_HEAD)
        started = bool(v and v["rows"])
        for i in (0, 1):
            y = 3 + i
            tomove = self.playing() and v["turn"] == i
            empty = i >= len(self.names) or self.names[i] == "-"
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            label = ("(empty seat)" if empty else self.names[i]) + (" (you)" if i == self.me else "")
            cnt = "store %2d" % store(v, i) if started else ""
            cv.put(px + 4, y, label[:pw - 7 - len(cnt)], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if cnt:
                cv.put(px + pw - 2 - len(cnt), y, cnt, A_BRIGHT if tomove else A_TEXT)
        rule(cv, g, px, 5, pw, A_BORDER)
        if not started:
            turn, tattr = "Waiting for an opponent", A_YELLOW
        elif self.over:
            turn, tattr = self.over, A_YELLOW
        elif v["turn"] < 0:
            turn, tattr = v["status"] or "Game over", A_YELLOW
        elif self.my_turn():
            turn, tattr = "Your move", A_YELLOW
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
        if started:
            cv.put(px + 2, 8, rules_line(v)[:inner], A_DIM)
        rule(cv, g, px, 9, pw, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, px + 2, inner)

    def hint(self):
        if not (self.v and self.v["rows"]):
            return ("Table %s starts when someone joins: telnet, the web (port 6500) or the native protocol."
                    % self.tid, A_DIM)
        if self.over or self.v["turn"] < 0:
            return "%s. Esc goes back to the menu." % (self.over or self.v["status"] or "Game over"), A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        if self.my_turn():
            n = pit(self.v, self.me, self.cur)
            if str(self.cur) in self.v["moves"]:
                return "Your move. Pit %d: Enter or Space sows its %s." % (self.cur, _plural(n)), A_TEXT
            if n == 0:
                return "Your move. Pit %d is empty; pick a pit with seeds." % self.cur, A_DIM
            return "Your move. Pit %d does not reach the other side, which you must feed." % self.cur, A_DIM
        return ("Waiting for %s. Leaving or logging off never resigns." % self.name(self.v["turn"]), A_DIM)

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            x, y = self.pit_xy(self.me, self.cur)
            return x + 2, y
        return None

    # ---- keys --------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "pit" and self.me >= 0 and self.playing():
            self.cur = value[1]
            if self.my_turn() and str(self.cur) in self.v["moves"]:
                return await self.sow()         # a click on a legal pit sows it
        return None

    def on_arrow(self, k):
        if self.v and self.v["rows"] and k in ("LEFT", "RIGHT"):
            self.cur = (self.cur - 1 + (1 if k == "RIGHT" else -1)) % PITS + 1

    async def on_enter(self):
        return await self.sow()

    async def sow(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        if not self.my_turn():
            return self.say("It is %s's move." % self.name(self.v["turn"]))
        err = await self.move(str(self.cur))
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainMancala(MancalaBase, PlainGame):
    """Mancala for plain terminals: the board printed line by line, typed pit numbers at a prompt."""

    BOARD_KINDS = ("SOW", "SWEEP", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" A pit number, 1 to 6, sows that pit of yours (the bottom row, counted\n"
                 " from the left; your store is on the right). say <text> talks to the\n"
                 " table. resign gives up (it asks first). menu goes back to the menu;\n"
                 " the game goes on and leaving never resigns. redraw or Enter shows\n"
                 " the board again.")

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you play the bottom row"
        out = ["", "== Mancala (%s), table %s: %s ==" % (self.rules_name(), self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet or the web" % self.tid,
                          "(port 6500). Type menu to go back; the table stays open."]
        bottom, top = seats_shown(me)
        legal = set(v["moves"]) if self.my_turn() else set()

        def cell(seat, p):
            n = pit(v, seat, p)
            return (" %2d*" if seat == me and str(p) in legal else " %2d ") % n
        tops = range(PITS, 0, -1)
        bots = range(1, PITS + 1)
        side = {}
        for i, s in enumerate((top, bottom)):
            mark = ">" if self.playing() and v["turn"] == s else " "
            where = "top" if s == top else "bottom"
            side[1 + i] = "%s %-12s %-6s store %2d" % (mark, self.name(s), where, store(v, s))
        if self.over or v["turn"] < 0:
            side[3] = (self.over or v["status"] or "Game over")[:31]
        elif self.my_turn():
            side[3] = "Your move: a pit with a *."
        else:
            side[3] = "%s to move." % self.name(v["turn"])
        if me >= 0 and not self.over:
            side[4], side[5] = "Type a pit (1-6) to sow it, or", "say <text>, resign, menu, help."
        else:
            side[4] = "Type say <text>, menu or help."
        board = ["          " + "".join("  %d  " % p for p in tops),
                 "    +" + "----+" * 8,
                 "    |    |" + "".join(cell(top, p) + "|" for p in tops) + "    |",
                 "    | %2d +" % store(v, top) + "----+" * 5 + "----+ %2d |" % store(v, bottom),
                 "    |    |" + "".join(cell(bottom, p) + "|" for p in bots) + "    |",
                 "    +" + "----+" * 8,
                 "          " + "".join("  %d  " % p for p in bots)]
        for i, row in enumerate(board):
            out.append((row.ljust(47) + side.get(i, "")).rstrip())
        out.append(" %s. Sown counterclockwise (bottom row: left to right)." % rules_line(v))
        if not self.over:
            out.append(" " + clock_text(v, self.names))
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.my_turn():
            return "Your move (a pit, like %s) > " % min(self.v["moves"], key=int)
        return "> "


AnsiScreen, PlainScreen = AnsiMancala, PlainMancala
