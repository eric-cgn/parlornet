"""Mancala (mancala/1, docs/types/mancala-1.md): the web board and the telnet screens.

Two rows of six pits and two stores. Each player sees their own row along the
bottom, pit 1 on the left and their store on the right; the opponent's row runs
along the top, right to left, with the opponent's store on the left. A move is
a pit number, 1 to 6. Two rulesets: kalah (store rules) and oware (abapa).
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_DIM, A_RED, A_TEXT, AnsiGame, COLS, Canvas, GameBase, PlainGame, ROWS,
                      title_bar)
from ..telnet import describe_event as describe_common
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


RULES_HELP = {         # help's page 2, the rules in use
    "kalah": ["Seeds are sown counterclockwise, one in each pit. They go through",
              "your own store but not the other player's. When your last seed",
              "lands in your store, you move again. When it lands in an empty pit",
              "of yours, it and the seeds opposite go to your store."],
    "oware": ["Seeds are sown counterclockwise, one in each pit, and the stores",
              "keep only captures. When your last seed makes 2 or 3 in a pit on",
              "the far row, you capture them and the 2s and 3s just before it.",
              "You must feed a side that is empty. More than 24 seeds wins."],
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

    HELP_MOVING = ["The arrows move the cursor along your pits, and the digits 1 to 6",
                   "go straight to that pit. Press Enter to sow it. A click on one of",
                   "your pits does the same."]
    HELP_KEYS = ["1 to 6 put the cursor on that pit."]
    HELP_COMMANDS = ["Press / to type one. /3 sows pit 3."]
    HELP_REFERENCE = ["Your pits",
                      "Your pits are the bottom row, numbered 1 to 6 from the left, and",
                      "your store is on the right. The last pit sown is blue."]

    def reference(self):
        rules = (self.v or {}).get("rules") or "kalah"
        return list(self.HELP_REFERENCE) + ["", "The rules"] + RULES_HELP.get(rules, RULES_HELP["kalah"])

    parse = staticmethod(parse_view)
    GAME = NAME
    MOVE_EXAMPLE = "/3"

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        return cmd if MOVE_RE.match(cmd) else None

    def view_last(self):
        last = self.v.get("last") if self.v else None
        return "%s sowed pit %d." % (self.actor(last[0]), last[1]) if last else ""

    def last_move(self, text):
        p = text.split()
        if p[0] == "SOW" and len(p) >= 3:
            return "%s sowed pit %s." % (self.actor(p[1]), p[2])
        return None

    def rules_name(self):
        return RULE_NAMES.get(self.v["rules"], "store rules") if self.v else "store rules"


class AnsiMancala(MancalaBase, AnsiGame):
    """Mancala, full screen: the board on the left (a cursor along the player's own pits), players,
    clock, rules and log on the right."""

    HOTKEYS = "rt?123456"         # 1 to 6 put the cursor on that pit
    LOG_TOP, LOG_ROWS = 10, 10
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
        title_bar(cv, self.g, self.title_text())
        self.draw_board(cv)
        self.draw_panel(cv)
        text, attr = self.sentence(self.hint)
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.my_turn():
            keys = self.keys("move", "sow")
        elif self.me >= 0 and self.playing():
            keys = self.keys("look")
        else:
            keys = self.keys()
        self.keys_line(cv, keys)
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
        showcur = self.me >= 0 and self.playing() and self.lit()
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

    def draw_panel(self, cv):
        v = self.v
        started = bool(v and v["rows"])
        players = []
        for i in (0, 1):
            n = store(v, i) if started else 0
            players.append((i, None, "%d in store" % n))
        self.draw_panel_parts(cv, self.PX, 2, COLS - 1 - self.PX, players, [rules_line(v) + "."] if started else [])

    def hint(self):
        if not (self.v and self.v["rows"]):
            return self.waiting_hint()
        if self.over or self.v["turn"] < 0:
            return self.over_hint(self.v["status"] or "Game over")
        if self.me < 0:
            return self.watch_hint()
        if self.my_turn():
            n = pit(self.v, self.me, self.cur)
            if str(self.cur) in self.v["moves"]:
                return "Your turn. Press Enter to sow the %s in pit %d." % (_plural(n), self.cur), A_TEXT
            if n == 0:
                return "Your turn. Pit %d is empty, so pick a pit with seeds." % self.cur, A_DIM
            return "Your turn. Pit %d doesn't reach the other side, and you must feed it." % self.cur, A_DIM
        return self.their_hint(self.v["turn"])

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            x, y = self.pit_xy(self.me, self.cur)
            return x + 2, y
        return None

    # ---- keys --------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "pit" and self.me >= 0 and self.playing():
            self.cur = value[1]
            return await self.sow()             # a click does what Enter does there
        return None

    def on_arrow(self, k):
        if self.v and self.v["rows"]:           # every arrow goes along your pits: Up and Down as Right and Left
            self.cur = (self.cur - 1 + (1 if k in ("RIGHT", "UP") else -1)) % PITS + 1

    async def on_enter(self):
        return await self.sow()

    async def hotkey(self, k):
        if k.isdigit():
            if self.v and self.v["rows"]:
                self.cur = int(k)
            return None
        return await super().hotkey(k)

    async def sow(self):
        if not self.my_turn():
            return self.say(self.not_yours())
        err = await self.move(str(self.cur))
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainMancala(MancalaBase, PlainGame):
    """Mancala for plain terminals: the board printed line by line, typed pit numbers at a prompt."""

    BOARD_KINDS = ("SOW", "SWEEP", "JOIN", "LEAVE", "START", "END")

    def lines(self):
        v, me = self.v, self.me
        out = [""] + self.plain_header()
        if not v["rows"]:
            return out + self.plain_waiting()
        bottom, top = seats_shown(me)
        legal = set(v["moves"]) if self.my_turn() else set()

        def cell(seat, p):
            n = pit(v, seat, p)
            return (" %2d*" if seat == me and str(p) in legal else " %2d ") % n
        tops = range(PITS, 0, -1)
        bots = range(1, PITS + 1)
        board = ["          " + "".join("  %d  " % p for p in tops),
                 "    +" + "----+" * 8,
                 "    |    |" + "".join(cell(top, p) + "|" for p in tops) + "    |",
                 "    | %2d +" % store(v, top) + "----+" * 5 + "----+ %2d |" % store(v, bottom),
                 "    |    |" + "".join(cell(bottom, p) + "|" for p in bots) + "    |",
                 "    +" + "----+" * 8,
                 "          " + "".join("  %d  " % p for p in bots)]
        side = self.side_text([(s, None, "%d in store" % store(v, s)) for s in (top, bottom)],
                              [rules_line(v) + ".", "Your pits are the bottom row." if me >= 0 else ""])
        return out + self.beside(board, side, 47) + self.plain_log()

    def turn_prompt(self):
        return "Your turn. Type a pit such as /%s." % min(self.v["moves"], key=int)

AnsiScreen, PlainScreen = AnsiMancala, PlainMancala
