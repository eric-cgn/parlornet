"""Naval Combat (navalcombat/1, docs/types/navalcombat-1.md): the web grids and the telnet screens.

Two phases: each player places a fleet on their own grid (a ship at a time, from one end to the other,
or the rest at random), says ready, then the two fire one shot a turn at the other's grid. The view a
frontend gets already has the other fleet removed (the plugin shows only shots and sunk ships), so
everything here draws exactly what the view says and nothing more.
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS,
                      ROWS, AnsiGame, Canvas, GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar,
                      title_bar, turn_text, wrap)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture
from ..web import a, board_html, esc, hidden_fields, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "navalcombat/1"
NAME = "Naval Combat"
ORDER = 9
PLAYERS = "2"
ABOUT = (
    "Each player secretly places a fleet of ships on their own 10 by 10 grid. Then the two take turns "
    "firing a shot at a square of the other's grid and are told whether it was a hit or a miss, and when "
    "a ship goes down. The first to sink the whole of the other fleet wins.",
    ["Classic rules: a fleet of five ships, of 5, 4, 3, 3 and 2 squares, each placed straight across or "
     "down; ships may touch but not overlap. The first seat fires first, one shot a turn, hit or miss.",
     "Batnav rules (the fleet of the 1990s batnav network game): ten ships, four of 1 square, three of 2, "
     "two of 3 and one of 4, which may not touch, not even at a corner; played the same way.",
     "Placing the fleet: ships go on one at a time, largest first, from one end to the other; the server "
     "can place the rest at random. Each player then says ready, and shooting starts when both are.",
     "Hidden fleets: the server never shows a ship to the other player or to watchers, not even when the "
     "game ends; a ship is shown to everyone only once every square of it has been hit.",
     "Salvo rules (GameMaster's BattleShips on the Mac): the classic fleet, and each turn is a salvo of six "
     "shots, however many ships are left. GameMaster works out the hits on the Mac, so each player's program "
     "is sent the other fleet when shooting starts. The web and telnet never show it, but a program could.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet). "
     "While the fleets are placed, the clock runs for the first player still placing."])

RULE_NAMES = {"classic": "Classic: five ships", "batnav": "Batnav: ten ships, not touching",
              "salvo": "Salvo: six shots a turn, fleets sent to programs"}
N = 10
SQ_RE = re.compile(r"^[a-j](10|[1-9])\Z")
PLACE_RE = re.compile(r"^[a-j](10|[1-9])-[a-j](10|[1-9])\Z")
WORDS = ("random", "undo", "clear", "ready")


def sq(x, y):
    return "%s%d" % (chr(97 + x), y + 1)


def xy(name):
    return ord(name[0]) - 97, int(name[1:]) - 1


def squares(token):
    """'a1-e1' -> [(0, 0) .. (4, 0)]."""
    (x0, y0), (x1, y1) = (xy(p) for p in token.split("-"))
    if y0 == y1:
        return [(x, y0) for x in range(min(x0, x1), max(x0, x1) + 1)]
    return [(x0, y) for y in range(min(y0, y1), max(y0, y1) + 1)]


def token_at(x, y, across, length, n=N):
    """The placement token for a ship of length with its top or left end at x, y; None off the grid."""
    ex, ey = (x + length - 1, y) if across or length == 1 else (x, y + length - 1)
    if ex >= n or ey >= n:
        return None
    return "%s-%s" % (sq(x, y), sq(ex, ey))


# ---- the view and the events (docs/types/navalcombat-1.md) ----------------------------

def parse_view(lines):
    """VUE lines for navalcombat/1 -> dict. rows is True once there is a game (a Q line)."""
    v = {"seq": 0, "rows": False, "turn": -1, "moves": [], "status": "", "clock": None, "n": N,
         "fleet": [], "phase": "", "ready": [False, False], "mine": [], "next": 0,
         "sunk": [[], []], "afloat": [0, 0], "last": None, "salvo": 0}
    rows = {0: {}, 1: {}}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) >= 3:
            v["seq"] = int(f[2])
        elif tag == "BRD" and len(f) >= 2:
            v["n"] = int(f[1])
        elif tag == "FL":
            v["fleet"] = [int(x) for x in f[1:]]
        elif tag == "Q" and len(f) == 2:
            v["phase"], v["rows"] = f[1], True
        elif tag == "W" and len(f) == 2:
            v["turn"] = -1 if f[1] == "-" else int(f[1])
        elif tag == "RD" and len(f) == 3:
            v["ready"] = [f[1] == "1", f[2] == "1"]
        elif tag == "B" and len(f) == 4 and f[1] in ("0", "1"):
            rows[int(f[1])][int(f[2]) - 1] = f[3]
        elif tag == "Y" and len(f) == 3:
            v["mine"].append((int(f[1]), f[2]))
        elif tag == "N" and len(f) == 2:
            v["next"] = int(f[1])
        elif tag == "K" and len(f) == 4 and f[1] in ("0", "1"):
            v["sunk"][int(f[1])].append((int(f[2]), f[3]))
        elif tag == "L" and len(f) == 3:
            v["afloat"] = [int(f[1]), int(f[2])]
        elif tag == "LS" and len(f) == 3:
            v["last"] = (int(f[1]), f[2])
        elif tag == "SL" and len(f) == 2 and f[1].isdigit():
            v["salvo"] = int(f[1])          # salvo rules: shots left in this turn
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) == 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    n = v["n"]
    v["boards"] = [[(rows[s].get(y, "") + "." * n)[:n] for y in range(n)] for s in (0, 1)]
    return v


def afloat_sizes(v, seat):
    """The lengths of seat's ships still afloat (the fleet less the sunk ones)."""
    left = list(v["fleet"])
    for length, _ in v["sunk"][seat]:
        if length in left:
            left.remove(length)
    return left


def shot_counts(v, seat):
    """(hits, misses) of the shots fired at seat's waters."""
    cells = "".join(v["boards"][seat])
    return cells.count("x") + cells.count("#"), cells.count("o")


def shot_result(v, last):
    """'miss', 'hit' or 'sunk' for the last shot (seat, square), from the board it landed on."""
    seat, name = last
    x, y = xy(name)
    c = v["boards"][1 - seat][y][x] if y < v["n"] and x < v["n"] else "."
    return {"o": "miss", "x": "hit", "#": "sunk"}.get(c, "")


def describe_event(text, names):
    """Naval Combat's own events -> a line for the log (None for the shared ones)."""
    p = text.split()

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) else "seat %d" % i
    k = p[0]
    if k == "READY" and len(p) == 2:
        return "%s's fleet is ready" % nm(p[1])
    if k == "SHOT" and len(p) >= 4:
        if p[3] == "sunk" and len(p) >= 5:
            return "%s fires at %s: hit, and sinks %s's %s-square ship" % (nm(p[1]), p[2], nm(1 - int(p[1])), p[4])
        return "%s fires at %s: %s" % (nm(p[1]), p[2], p[3])
    if k == "END" and len(p) >= 3 and p[1] == "win":
        score = " (hits %s)" % p[3] if len(p) > 3 else ""
        return "Game over: %s sinks %s's whole fleet%s" % (nm(p[2]), nm(1 - int(p[2])), score)
    return None


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^([a-j](10|[1-9])(-[a-j](10|[1-9]))?|random|undo|clear|ready)\Z")
SEL_RE = re.compile(r"^[a-j](10|[1-9])\Z")      # the start square of the ship being placed

CELL = 20
PAL = [(0, 80, 160), (0, 48, 112), (255, 255, 255), (160, 160, 160), (220, 0, 0), (255, 220, 0),
       (0, 0, 0), (80, 80, 80)]
IMG = "/img/navalcombat/%s.gif"
ALT = {".": ".", "o": "o", "x": "X", "#": "#", "s": "S"}
KIND = {".": "sea", "o": "miss", "x": "hit", "#": "sunk", "s": "ship"}


def _cell(kind):
    """One 20x20 square: sea (blue, darker grid lines at the top and left) with what is on it."""
    px = []
    for y in range(CELL):
        for x in range(CELL):
            c = 1 if x == 0 or y == 0 else 0
            d2 = (x - 10) ** 2 + (y - 10) ** 2
            inside = 3 <= x <= 17 and 3 <= y <= 17
            cross = inside and (abs(x - y) <= 1 or abs(x + y - 20) <= 1)
            if kind == "aim" and d2 <= 2.3 ** 2:
                c = 5
            elif kind == "start" and d2 <= 4.2 ** 2:
                c = 5
            elif kind == "end" and 3.2 ** 2 <= d2 <= 6.2 ** 2:
                c = 5
            elif kind == "miss" and 3.0 ** 2 <= d2 <= 5.5 ** 2:
                c = 2
            elif kind in ("ship", "hit", "sel") and 2 <= x <= 18 and 2 <= y <= 18:
                c = 3
                if kind == "hit" and cross:
                    c = 4
                if kind == "sel" and (x <= 3 or x >= 17 or y <= 3 or y >= 17):
                    c = 5
            elif kind == "sunk" and 1 <= x <= 19 and 1 <= y <= 19:
                c = 4 if cross else 7
            px.append(c)
    return make_gif(CELL, CELL, PAL, px)


def images():
    """The grid squares, under /img/navalcombat/."""
    return {IMG % k: _cell(k) for k in ("sea", "aim", "miss", "hit", "sunk", "ship", "start", "sel", "end")}


def sides(seat):
    """(left grid's seat, right grid's seat): your own fleet on the left; watchers see seat 0 on the left."""
    return (seat, 1 - seat) if seat in (0, 1) else (0, 1)


def placing(v, seat):
    return seat in (0, 1) and v["phase"] == "place" and v["turn"] == seat and not v["ready"][seat]


def shooting(v, seat):
    return seat in (0, 1) and v["phase"] == "shoot" and v["turn"] == seat and bool(v["moves"])


def click_map(v, seat, sel):
    """The squares to click, per grid (0 left, 1 right): {(x, y): (page, {query}, image kind, alt)}, and
    whether a start square is picked (sel) with its ends offered."""
    links = {0: {}, 1: {}}
    starts = {}
    picked = False
    if placing(v, seat):
        for m in v["moves"]:
            if "-" in m:
                starts.setdefault(m.split("-")[0], []).append(m)
        if sel in starts and v["next"] > 1:
            picked = True
            links[0][xy(sel)] = ("/table.htm", {}, "sel", "cancel %s" % sel)
            for m in starts[sel]:
                end = m.split("-")[1]
                links[0][xy(end)] = ("/move.htm", {"mv": m}, "end", m)
        else:
            for s, ms in starts.items():
                if v["next"] == 1:
                    links[0][xy(s)] = ("/move.htm", {"mv": ms[0]}, "start", s)
                else:
                    links[0][xy(s)] = ("/table.htm", {"sel": s}, "start", s)
    elif shooting(v, seat):
        for m in v["moves"]:
            if SQ_RE.match(m):
                links[1][xy(m)] = ("/move.htm", {"mv": m}, "aim", m)
    return links, starts, picked


MARGIN, GAP = 16, 24


def grid_x(i, n):
    """The left edge of grid i's squares (0 left, 1 right)."""
    return MARGIN + i * (n * CELL + 1 + GAP + MARGIN)


def board_image(v, seat, sel):
    """Both grids in one image: your fleet on the left and the other's waters on the right (seat 0's and
    seat 1's for watchers), each with its letters and numbers. Each grid is drawn from this seat's view,
    which holds no ship of the other fleet until it is hit. The squares to click are areas: start and end
    squares while placing, every square not yet fired at while shooting."""
    n = v["n"]
    left, right = sides(seat)
    links, _, _ = click_map(v, seat, sel)
    c = Picture(grid_x(1, n) + n * CELL + 2, MARGIN + n * CELL + 2, images=images)
    for i, s in enumerate((left, right)):
        x0 = grid_x(i, n)
        c.labels(x0, MARGIN, CELL, n, n, (0, 0, 0))
        board = v["boards"][s]
        for y in range(n):
            for x in range(n):
                px, py = x0 + x * CELL, MARGIN + y * CELL
                if (x, y) in links[i]:
                    path, params, kind, alt = links[i][(x, y)]
                    c.tile(IMG % kind, px, py)
                    c.area("rect", (px, py, px + CELL - 1, py + CELL - 1), alt, path, **params)
                else:
                    c.tile(IMG % KIND.get(board[y][x], "sea"), px, py)
        c.frame(x0 - 1, MARGIN - 1, n * CELL + 2, n * CELL + 2, (0, 0, 0))
    return c


def shot_word(v):
    """'Your shot', or under the salvo rules the shots left: 'Your salvo: 4 shots left'."""
    n = v.get("salvo") or 0
    return "Your salvo: %d shot%s left" % (n, "" if n == 1 else "s") if n else "Your shot"


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    left, right = sides(seat)
    links, starts, picked = click_map(v, seat, sel)
    actions, hint = [], ""
    if placing(v, seat):
        if picked:
            y = xy(sel)[1]
            for m in starts[sel]:
                across = xy(m.split("-")[1])[1] == y
                actions.append(a(link("/move.htm", t=tid, mv=m), "<b>%s</b> (%s)" % (
                    "Across" if across else "Down", esc(m))))
            actions.append(a(link("/table.htm", t=tid), "Cancel"))
            hint = " - click a yellow ring, or choose Across or Down"
        elif v["next"]:
            hint = (" - click a yellow dot: the %d-square ship starts there (then choose across or down), "
                    "or let the server place the rest" % v["next"])
        else:
            hint = " - Ready when you are"
        labels = [("random", "Random fleet" if not v["mine"] or not v["next"] else "Place the rest at random"),
                  ("undo", "Undo the last ship"), ("clear", "Clear"), ("ready", "<b>Ready</b>")]
        actions += [a(link("/move.htm", t=tid, mv=m), label) for m, label in labels if m in v["moves"]]
    elif shooting(v, seat):
        hint = " - click a square of %s's grid to fire" % names[right]

    def title(s, side):
        if seat in (0, 1):
            return "Your fleet" if side == 0 else "%s&#39;s waters" % esc(names[s])
        return "%s&#39;s fleet" % esc(names[s])
    alt = "%s (left) and %s (right)" % (title(left, 0), title(right, 1))
    board = "<p><b>%s</b> (left) and <b>%s</b> (right)</p>\n%s" % (
        title(left, 0), title(right, 1), board_html(board_image, v, tid, link, seat, sel, alt.replace("&#39;", "'"),
                                                    text=not shooting(v, seat)))
    if shooting(v, seat):
        board += ('\n<form action="/move.htm" method="get">\n%s\nOr type a square to fire at (like %s): '
                  '<input name="mv" size="4" maxlength="3"> <input type="submit" value="Fire">\n</form>'
                  % (hidden_fields(link("/move.htm", t=tid)), esc(sorted(m for m in v["moves"] if SQ_RE.match(m))[0])))
    rows = []
    for s in (0, 1):
        if v["phase"] == "place":
            state = "fleet ready" if v["ready"][s] else "placing the fleet"
        else:
            state = "afloat: %s" % (" ".join(str(x) for x in afloat_sizes(v, s)) or "none")
        mark = "&gt;" if v["turn"] == s and v["phase"] != "over" else "&nbsp;"
        rows.append("<tr><td>%s</td><td><b>%s</b></td><td>%s</td></tr>" % (mark, esc(names[s]), state))
    side = '<table border="0" cellpadding="3">%s</table>' % "".join(rows)
    if v["mine"]:
        side += "<p><font size=\"2\">Your ships: %s</font></p>" % ", ".join(esc(t) for _, t in v["mine"])
    if v["last"]:
        s, where = v["last"]
        side += "<p>Last shot: %s at %s, %s</p>" % (esc(names[s]), esc(where), shot_result(v, v["last"]))
    side += ("<p><font size=\"2\">Squares: . sea, o miss, X hit, # sunk%s</font></p>"
             % (", S your ship" if seat in (0, 1) else ""))
    return board, side, hint, " | ".join(actions)


# ---- telnet --------------------------------------------------------------------------
#
# Two 10 by 10 grids side by side, 2 columns a square, then the panel:
#
#   x  0-1    row numbers          26-27  row numbers          y  1     grid titles
#      2      frame                28     frame                   2     column letters
#      3-22   the left grid        29-48  the right grid          3     frame
#      23-24  pad, frame           49-50  pad, frame              4-13  the grid rows
#      52-78  the panel: players, turn, clock, last shot, log     14    frame
#                                                                 15-17 hits and ships afloat
#                                                                 18    the key; 19-20 placing

GX = (3, 29)        # first column of the left and right grids' squares
GY = 4              # first row of the grids
PX, PW = 52, 27     # the panel


def nc_glyphs(mode):
    """Two characters a square. Each differs in shape, not only in color: sea, miss, hit, sunk, ship, and
    the ship being placed. pc and utf8 use CP437 shapes; ansi and plain ASCII."""
    if mode in ("pc", "utf8"):
        return {".": " ·", "o": " o", "x": " X", "#": "▒▒", "s": "██", "p": "░░"}
    return {".": " .", "o": " o", "x": " X", "#": "##", "s": "[]", "p": "++"}


# foreground, background per square
COLOURS = {".": ("36", "44"), "o": ("1;37", "44"), "x": ("1;37", "41"), "#": ("31", "40"), "s": ("37", "44"),
           "p": ("1;33", "44")}
LEGEND = [(".", "sea"), ("o", "miss"), ("x", "hit"), ("#", "sunk"), ("s", "ship")]

HELP = [
    "Arrow keys   move the cursor",
    "Enter/Space  place the ship there, or fire there",
    "D            turn the ship: across or down",
    "A            place the rest of the fleet at random",
    "U  C         take back the last ship; clear all",
    "Mouse        click a square to place or to fire",
    "T  R         talk to the table; resign (asks)",
    "Esc          back to the menu; the game goes on",
    "",
    "Typed: a1 across, a1 down, a1-e1, random, undo,",
    "clear, ready; a square like e5 fires there.",
    "Also say <text>, resign, redraw, menu, help.",
    "Leaving or logging off never resigns.",
]


class NavalBase(GameBase):
    """What both Naval Combat screens share: the view, the events, typed moves."""

    parse = staticmethod(parse_view)
    across = True       # the direction of the ship being placed

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def placing(self):
        return self.playing() and placing(self.v, self.me)

    def move_word(self, cmd, words=()):
        if cmd in WORDS:
            return cmd
        if PLACE_RE.match(cmd):
            return cmd
        if not SQ_RE.match(cmd) or not self.v:
            return None
        if self.v["phase"] != "place":
            return cmd
        if not self.v["next"]:
            return None
        across = self.across
        if len(words) > 1:
            d = words[1].lower()
            if d in ("across", "a", "right", "h", "horizontal"):
                across = True
            elif d in ("down", "d", "v", "vertical"):
                across = False
            else:
                return None
        x, y = xy(cmd)
        return token_at(x, y, across, self.v["next"], self.v["n"])


class AnsiNaval(NavalBase, AnsiGame):
    """Naval Combat, full screen: your grid on the left, the other's on the right (for watchers, seat 0's
    and seat 1's), the panel on the right; the cursor places ships on the left grid and fires on the right."""

    HOTKEYS = "rt?dauc"
    LOG_TOP, LOG_ROWS = 11, 9
    TITLE, HELP = "Naval Combat", HELP

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = (0, 0)
        self.across = True
        self.nc = nc_glyphs(sess.mode)

    # ---- where things are ------------------------------------------------
    def side(self):
        """The grid the cursor is on: 0 (left) while placing, 1 (right) while shooting, None otherwise."""
        if self.me < 0 or not self.playing():
            return None
        if self.v["phase"] == "place" and not self.v["ready"][self.me]:
            return 0
        if self.v["phase"] == "shoot":
            return 1
        return None

    def cell_xy(self, side, x, y):
        return GX[side] + 2 * x, GY + y

    def preview(self):
        """The squares of the ship being placed at the cursor, if it may go there; else []."""
        if not self.placing() or not self.v["next"]:
            return []
        t = token_at(self.cur[0], self.cur[1], self.across, self.v["next"], self.v["n"])
        return squares(t) if t and t in self.v["moves"] else []

    def snap(self):
        """When it becomes your shot, put the cursor on a square not fired at yet."""
        mine = self.my_turn()
        n = self.v["n"] if self.v else N
        self.cur = (min(self.cur[0], n - 1), min(self.cur[1], n - 1))
        if mine and self.v["phase"] == "shoot" and not self.was_my_turn and sq(*self.cur) not in self.v["moves"]:
            free = [m for m in self.v["moves"] if SQ_RE.match(m)]
            if free:
                start = self.cur[1] * n + self.cur[0]
                self.cur = min((xy(m) for m in free), key=lambda p: (p[1] * n + p[0] - start) % (n * n))
        self.was_my_turn = mine

    # ---- drawing ---------------------------------------------------------
    def compose(self):
        cv = Canvas()
        right = "watching" if self.me < 0 else "your fleet is on the left"
        title_bar(cv, self.g, "Naval Combat %s table %s" % (self.g["v"], self.tid), right)
        self.draw_grids(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.placing():
            keys = [("Arrows", "move"), ("Enter", "place" if self.v["next"] else "ready"), ("D", "turn"),
                    ("A", "random"), ("U", "undo"), ("C", "clear"), ("?", "help"), ("Esc", "menu")]
        elif self.me >= 0 and self.playing():
            keys = [("Arrows", "aim"), ("Enter", "fire"), ("T", "talk"), ("R", "resign"), ("?", "help"),
                    ("Esc", "menu")]
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_grids(self, cv):
        g, v = self.g, self.v
        started = bool(v and v["rows"])
        n = v["n"] if v else N
        left, right = sides(self.me)
        cside = self.side()
        prev = set(self.preview())
        last = v["last"] if started else None
        for side, seat in ((0, left), (1, right)):
            gx = GX[side]
            if self.me >= 0:
                head = "Your fleet" if side == 0 else "Enemy: %s" % self.name(seat)
            else:
                head = "%s's fleet" % self.name(seat)
            cv.put(gx - 1, 1, head[:23], A_HEAD)
            for i in range(n):
                hi = cside == side and i == self.cur[0]
                cv.put(gx + 2 * i + 1, 2, chr(97 + i), A_AXIS_HI if hi else A_AXIS)
            cv.put(gx - 1, GY - 1, g["tl"] + g["h"] * (2 * n + 1) + g["tr"], A_BORDER)
            cv.put(gx - 1, GY + n, g["bl"] + g["h"] * (2 * n + 1) + g["br"], A_BORDER)
            for y in range(n):
                hi = cside == side and y == self.cur[1]
                cv.put(gx - 3, GY + y, "%2d" % (y + 1), A_AXIS_HI if hi else A_AXIS)
                cv.put(gx - 1, GY + y, g["v"], A_BORDER)
                cv.put(gx + 2 * n, GY + y, " ", "0;44")
                cv.put(gx + 2 * n + 1, GY + y, g["v"], A_BORDER)
                for x in range(n):
                    c = v["boards"][seat][y][x] if started else "."
                    if side == 0 and (x, y) in prev:
                        c = "p"
                    fg, bg = COLOURS.get(c, COLOURS["."])
                    if last and last[0] != seat and last[1] == sq(x, y):
                        bg = "45"           # the last shot fired at this grid
                    attr = "0;%s;%s" % (fg, bg)
                    if cside == side and (x, y) == self.cur:
                        attr = "0;30;46"
                    cv.put(gx + 2 * x, GY + y, self.nc.get(c, self.nc["."]), attr)
                    cv.hot_area(gx + 2 * x, GY + y, 2, 1, ("sq", side, x, y))
            if started:
                h, m = shot_counts(v, seat)
                cv.put(gx - 2, GY + n + 1, ("Hits %d, misses %d" % (h, m))[:24], A_TEXT)
                sizes = " ".join(str(s) for s in afloat_sizes(v, seat)) or "none"
                for i, l in enumerate(wrap("Afloat: " + sizes, 24)[:2]):
                    cv.put(gx - 2, GY + n + 2 + i, l, A_DIM)
        if started:
            x = 1
            for c, label in LEGEND:
                if c == "s" and self.me < 0:
                    continue
                fg, bg = COLOURS[c]
                cv.put(x, 18, self.nc[c], "0;%s;%s" % (fg, bg))
                cv.put(x + 3, 18, label, A_DIM)
                x += 5 + len(label)
            self.draw_orders(cv)
        else:
            self.draw_waiting(cv, 12, 6)

    def draw_orders(self, cv):
        """Rows 19 and 20 while placing: which ship is next and the keys."""
        if not self.placing():
            return
        v = self.v
        if v["next"]:
            k = len(v["mine"])
            cv.put(1, 19, ("Ship %d of %d: %d square%s, %s" % (k + 1, len(v["fleet"]), v["next"],
                                                              "" if v["next"] == 1 else "s",
                                                              "across" if self.across else "down"))[:50], A_YELLOW)
            cv.put(1, 20, "Enter place  %s turn  %s random  U undo  C clear" % (self.kn("D"), self.kn("A")), A_DIM)
        else:
            cv.put(1, 19, "Fleet placed.", A_YELLOW)
            cv.put(1, 20, "Enter ready  U undo  C clear  %s a new fleet" % self.kn("A"), A_DIM)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        inner = PW - 4
        box(cv, g, PX, 1, PW, 20, A_BORDER, "Players", tattr=A_HEAD)
        started = bool(v and v["rows"])
        for i in (0, 1):
            y = 2 + 2 * i
            tomove = started and self.playing() and v["turn"] == i and v["phase"] != "over"
            empty = i >= len(self.names) or self.names[i] == "-"
            cv.put(PX + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            label = ("(empty seat)" if empty else self.names[i]) + (" (you)" if i == self.me else "")
            cv.put(PX + 4, y, label[:inner - 2], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if started:
                if v["phase"] == "place":
                    state = "fleet ready" if v["ready"][i] else "placing ships"
                else:
                    state = "%d of %d afloat" % (len(afloat_sizes(v, i)), len(v["fleet"]))
                cv.put(PX + 4, y + 1, state[:inner - 2], A_DIM)
        rule(cv, g, PX, 6, PW, A_BORDER)
        if not started:
            turn, tattr = "Waiting for an opponent", A_YELLOW
        elif self.over:
            turn, tattr = self.over, A_YELLOW
        elif self.placing():
            turn, tattr = "Place your fleet", A_YELLOW
        elif v["phase"] == "place":
            turn, tattr = "Fleets being placed", A_TEXT
        elif self.my_turn():
            turn, tattr = shot_word(v), A_YELLOW
        else:
            turn, tattr = "%s to shoot" % self.name(v["turn"]), A_TEXT
        cv.put(PX + 2, 7, turn[:inner], tattr)
        c = self.clock()
        if c:
            seat, left, limit = c
            urgent = seat == self.me and left < 60
            cv.put(PX + 2, 8, ("Clock %s of %s" % (fmt_secs(left), turn_text(limit)))[:inner],
                   A_RED if urgent else A_TEXT)
        elif self.turn_secs and not self.over:
            cv.put(PX + 2, 8, ("Per turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        if started and v["last"]:
            cv.put(PX + 2, 9, ("Last shot: %s %s" % (v["last"][1], shot_result(v, v["last"])))[:inner], A_TEXT)
        rule(cv, g, PX, 10, PW, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, PX + 2, inner)

    def hint(self):
        v = self.v
        if not (v and v["rows"]):
            return ("Table %s starts when someone joins: telnet or the web (port 6500)." % self.tid, A_DIM)
        if self.over:
            return "%s. Esc goes back to the menu." % self.over, A_YELLOW
        if self.me < 0:
            return "Watching. Ships show only once sunk. T talks, Esc goes back to the menu.", A_DIM
        if self.placing():
            if not v["next"]:
                return ("Fleet placed. Enter: ready to fight (U undo, C clear, %s a new random fleet)."
                        % self.kn("A"), A_TEXT)
            where = "%s %s" % (sq(*self.cur), "across" if self.across else "down")
            if self.preview():
                return "%s: Enter places the %d-square ship here (%s turns it)." % (where, v["next"], self.kn("D")), \
                    A_TEXT
            return ("The %d-square ship does not fit at %s; %s turns it, %s places the rest." % (
                v["next"], where, self.kn("D"), self.kn("A")), A_DIM)
        if v["phase"] == "place":
            return "Your fleet is ready. Waiting for %s's fleet." % self.name(1 - self.me), A_DIM
        if self.my_turn():
            if sq(*self.cur) in v["moves"]:
                return "%s. %s: Enter fires here." % (shot_word(v), sq(*self.cur)), A_TEXT
            return "%s. You have fired at %s already; pick another square." % (shot_word(v), sq(*self.cur)), A_DIM
        return "Waiting for %s's shot. Leaving or logging off never resigns." % self.name(v["turn"]), A_DIM

    def cursor_at(self):
        side = self.side()
        if side is None:
            return None
        x, y = self.cell_xy(side, *self.cur)
        return x + 1, y

    # ---- keys --------------------------------------------------------------
    def on_arrow(self, k):
        if self.v and self.v["rows"]:
            n = self.v["n"]
            dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
            self.cur = ((self.cur[0] + dx) % n, (self.cur[1] + dy) % n)

    async def on_click(self, value):
        if value[0] == "sq" and value[1] == self.side():
            self.cur = (value[2], value[3])
            return await self.act()
        return None

    async def on_enter(self):
        return await self.act()

    async def act(self):
        """Enter, Space or a click: place the ship, say ready, or fire."""
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        v = self.v
        if self.placing():
            if not v["next"]:
                return await self.play("ready")
            t = token_at(self.cur[0], self.cur[1], self.across, v["next"], v["n"])
            if not t or t not in v["moves"]:
                return self.say("The %d-square ship does not fit there %s." % (
                    v["next"], "across" if self.across else "down"), A_RED)
            return await self.play(t)
        if v["phase"] == "place":
            return self.say("Waiting for %s to place their fleet." % self.name(1 - self.me))
        if not self.my_turn():
            return self.say("It is %s's shot." % self.name(v["turn"]))
        return await self.play(sq(*self.cur))

    async def play(self, token):
        err = await self.move(token)
        if err:
            self.say(err, A_RED)
        self.snap()

    async def hotkey(self, k):
        if k in "dauc":
            if not self.placing():
                return self.say("That key is for placing your fleet.")
            if k == "d":
                self.across = not self.across
                return None
            word = {"a": "random", "u": "undo", "c": "clear"}[k]
            if word not in self.v["moves"]:
                return self.say("No ship placed yet.")
            return await self.play(word)
        return await super().hotkey(k)


class PlainNaval(NavalBase, PlainGame):
    """Naval Combat for plain terminals: the two grids printed side by side, typed moves at a prompt."""

    BOARD_KINDS = ("SHOT", "READY", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" Placing: a1 across or a1 down puts the next ship there (a1-e1 works too);\n"
                 " random places the rest, undo takes back the last ship, clear removes all,\n"
                 " ready starts the battle once every ship is placed.\n"
                 " Shooting: a square like e5 fires there. say <text> talks to the table.\n"
                 " resign gives up (it asks first). menu goes back to the menu; the game\n"
                 " goes on and leaving never resigns. redraw or Enter shows the grids again.")

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "your fleet is on the left"
        out = ["", "== Naval Combat, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet or the web" % self.tid,
                          "(port 6500). Type menu to go back; the table stays open."]
        gl = nc_glyphs("plain")
        n = v["n"]
        left, right = sides(me)
        heads = []
        for side, seat in ((0, left), (1, right)):
            heads.append(("Your fleet" if side == 0 else "Enemy: %s" % self.name(seat)) if me >= 0
                         else "%s's fleet" % self.name(seat))
        side = {}
        for i in (0, 1):
            mark = ">" if self.playing() and v["turn"] == i else " "
            side[2 * i] = "%s %s%s" % (mark, self.name(i), " (you)" if i == me else "")
            if v["phase"] == "place":
                side[2 * i + 1] = "  fleet ready" if v["ready"][i] else "  placing ships"
            else:
                side[2 * i + 1] = "  %d of %d afloat" % (len(afloat_sizes(v, i)), len(v["fleet"]))
        if self.over:
            side[5] = self.over
        elif self.placing():
            side[5] = ("Next: the %d-square ship" % v["next"]) if v["next"] else "Fleet placed: type ready"
        elif v["phase"] == "place":
            side[5] = "Fleets being placed."
        elif self.my_turn():
            side[5] = shot_word(v) + "."
        else:
            side[5] = "%s to shoot." % self.name(v["turn"])
        if v["last"]:
            side[6] = "Last shot: %s %s" % (v["last"][1], shot_result(v, v["last"]))
        side[8] = "Type help for commands."
        out.append("    %-22s    %s" % (heads[0][:22], heads[1][:22]))
        letters = "    " + "".join(" %s" % chr(97 + i) for i in range(n)) + "  "
        out.append(letters + "  " + letters.rstrip())
        frame = "   +" + "-" * (2 * n + 1) + "+"
        out.append(frame + "  " + frame)
        for y in range(n):
            cells = ["".join(gl.get(c, " .") for c in v["boards"][seat][y]) for seat in (left, right)]
            line = "%2d |%s |  %2d |%s |" % (y + 1, cells[0], y + 1, cells[1])
            out.append((line + "  " + side.get(y, "")[:24]).rstrip())
        out.append(frame + "  " + frame)
        stats = [shot_counts(v, seat) for seat in (left, right)]
        out.append(("   Hits %-2d misses %-2d" % stats[0]).ljust(31) + "Hits %-2d misses %d" % stats[1])
        afl = ["Afloat: " + (" ".join(str(s) for s in afloat_sizes(v, seat)) or "none") for seat in (left, right)]
        out.append(("   " + afl[0]).ljust(31) + afl[1])
        out.append(" Key: . sea  o miss  X hit  # sunk%s" % ("  [] your ship" if me >= 0 else ""))
        if not self.over:
            out.append(" " + clock_text(v, self.names)[:78])
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.placing():
            if self.v["next"]:
                return "Place the %d-square ship (like a1 across, a1 down, random) > " % self.v["next"]
            return "Type ready (or undo, clear, random) > "
        if self.my_turn():
            return "Your shot (like %s) > " % next((m for m in self.v["moves"] if SQ_RE.match(m)), "e5")
        return "> "


AnsiScreen, PlainScreen = AnsiNaval, PlainNaval
