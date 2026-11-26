"""Naval Combat (navalcombat/1, docs/types/navalcombat-1.md): the web grids and the telnet screens.

Two phases: each player places a fleet on their own grid (a ship at a time, from one end to the other,
or the rest at random), says ready, then the two fire one shot a turn at the other's grid. The view a
frontend gets already has the other fleet removed (the plugin shows only shots and sunk ships), so
everything here draws exactly what the view says and nothing more.
"""
import re

from ..telnet import (A_AXIS, A_AXIS_HI, A_BORDER, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, AnsiGame, COLS, Canvas,
                      GameBase, PlainGame, ROWS, and_list, title_bar, wrap)
from ..telnet import describe_event as describe_common
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
WORDS = ("random", "undo", "reset", "ready")      # /reset sends the plugin's clear


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



class NavalBase(GameBase):
    """What both Naval Combat screens share: the view, the events, typed moves."""

    HELP_MOVING = ["While you place ships, the arrows move over your grid. Enter picks",
                   "where a ship starts, Right or Down point it, and Enter places it.",
                   "Once your fleet is placed, Enter says you are ready. Then the",
                   "arrows move over the other grid, and Enter fires there."]
    HELP_KEYS = ["G places the rest of your fleet at random.",
                 "U takes back the last ship, and Z takes back every ship."]
    HELP_COMMANDS = ["Press / to type one. /a1 across places a ship, and /e5 fires at e5.",
                     "/random, /undo, /reset and /ready do what their keys do."]
    HELP_REFERENCE = []

    def reference(self):
        v = self.v or {}
        gl = nc_glyphs(self.s.mode)
        out = []
        if v.get("fleet"):
            out += ["The fleet", "Your fleet has ships of %s squares." % and_list(str(n) for n in v["fleet"])]
            out.append("")
        return out + ["The marks", "%s is sea, %s is a miss, %s is a hit, %s is a sunk ship and %s is" % tuple(
            gl[c].strip() for c in ".ox#s"), "one of your ships. The last square fired at shows in magenta."]

    parse = staticmethod(parse_view)
    GAME = NAME
    MOVE_EXAMPLE = "/e5"
    COMMANDS = {"random": "/random places the rest of your fleet at random.",
                "undo": "/undo takes back the last ship you placed.",
                "reset": "/reset takes back every ship you placed.",
                "ready": "/ready starts the battle once your fleet is placed."}
    across = True       # the direction of the ship being placed

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def placing(self):
        return self.playing() and placing(self.v, self.me)

    def view_last(self):
        last = self.v.get("last") if self.v else None
        if not last:
            return ""
        what = shot_result(self.v, last)
        return "%s fired at %s and %s." % (self.actor(last[0]), last[1], "missed" if what == "miss" else
                                          "sank a ship" if what == "sunk" else "hit")

    def last_move(self, text):
        p = text.split()
        if p[0] == "SHOT" and len(p) >= 4 and p[1].isdigit():
            seat, where, what = int(p[1]), p[2], p[3]
            other = "your" if 1 - seat == self.me else "%s's" % self.who(self.name(1 - seat))
            if what == "sunk" and len(p) >= 5:
                return "%s sank %s %s-square ship." % (self.actor(seat), other, p[4])
            if what == "hit":
                return "%s fired at %s and hit." % (self.actor(seat), where)
            return "%s fired at %s and missed." % (self.actor(seat), where)
        return None

    def turn_sentence(self):
        if self.placing():
            return "Place your fleet."
        if self.v and self.v.get("rows") and self.v["phase"] == "place" and self.playing():
            return "The fleets are being placed."
        if self.my_turn():
            return "Your shot."
        return super().turn_sentence()

    def move_word(self, cmd, words=()):
        if cmd in WORDS:
            return "clear" if cmd == "reset" else cmd
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
            if d in ("across", "a"):
                across = True
            elif d in ("down", "d"):
                across = False
            else:
                return None
        x, y = xy(cmd)
        return token_at(x, y, across, self.v["next"], self.v["n"])


class AnsiNaval(NavalBase, AnsiGame):
    """Naval Combat, full screen: your grid on the left, the other's on the right (for watchers, seat 0's
    and seat 1's), the panel on the right; the cursor places ships on the left grid and fires on the right."""

    HOTKEYS = "rt?guz"
    LOG_TOP, LOG_ROWS = 11, 9

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = (0, 0)
        self.across = True
        self.pick = None            # where the ship being placed starts, once Enter has picked it
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
        """The squares of the ship being placed from the square picked, if it may go there; else []."""
        if not self.placing() or not self.v["next"] or not self.pick:
            return []
        t = token_at(self.pick[0], self.pick[1], self.across, self.v["next"], self.v["n"])
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
        title_bar(cv, self.g, self.title_text())
        self.draw_grids(cv)
        self.draw_panel(cv)
        text, attr = self.sentence(self.hint)
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.placing():
            placed = bool(self.v["mine"])
            undo = [("u", "undo", "undo"), ("z", "reset", "reset")] if placed else []
            if not self.v["next"]:
                keys = self.keys(None, "ready", undo)
            elif self.pick:
                keys = self.keys("point", "place", undo + [("g", "random", "random")], cancel=True)
            else:
                keys = self.keys("move", "pick", undo + [("g", "random", "random")])
        elif self.my_turn():
            keys = self.keys("aim", "fire")
        else:
            keys = self.keys()
        self.keys_line(cv, keys)
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
            line = "Ship %d of %d is %d square%s long." % (k + 1, len(v["fleet"]), v["next"],
                                                          "" if v["next"] == 1 else "s")
            if self.pick:
                line += " The %s squares show where it goes." % self.nc["p"].strip()
            cv.put(1, 20, line[:COLS - 2], A_YELLOW)
        else:
            cv.put(1, 20, "Your fleet is placed.", A_YELLOW)

    def draw_panel(self, cv):
        v = self.v
        started = bool(v and v["rows"])
        players = []
        for i in (0, 1):
            if not started:
                state = ""
            elif v["phase"] == "place":
                state = "ready" if v["ready"][i] else "placing"
            else:
                state = "%d afloat" % len(afloat_sizes(v, i))
            players.append((i, None, state))
        self.draw_panel_parts(cv, PX, 1, PW, players)

    def hint(self):
        v = self.v
        if not (v and v["rows"]):
            return self.waiting_hint()
        if self.over:
            return self.over_hint()
        if self.me < 0:
            return self.watch_hint()
        if self.placing():
            if not v["next"]:
                return "Your fleet is placed. Press Enter when you are ready, or U to undo.", A_TEXT
            if self.pick:
                where = "Ship %d starts at %s." % (len(v["mine"]) + 1, sq(*self.pick))
                if self.preview():
                    return where + " Press Right or Down to point it, then Enter to place it.", A_TEXT
                return where + " It doesn't fit %s, so press %s." % (
                    "across" if self.across else "down", "Down" if self.across else "Right"), A_DIM
            return "Place your %d-square ship. Press Enter where it starts, or G for random." % v["next"], A_TEXT
        if v["phase"] == "place":
            return "Your fleet is ready. Waiting for %s to place a fleet." % self.who(self.name(1 - self.me)), A_DIM
        if self.my_turn():
            if sq(*self.cur) in v["moves"]:
                return "Your shot. Pick a square on %s's grid and press Enter to fire." % self.who(
                    self.name(1 - self.me)), A_TEXT
            return "Your shot. You fired at %s already, so pick another square." % sq(*self.cur), A_DIM
        return self.their_hint(v["turn"])

    def cursor_at(self):
        side = self.side()
        if side is None:
            return None
        x, y = self.cell_xy(side, *self.cur)
        return x + 1, y

    # ---- keys --------------------------------------------------------------
    def on_arrow(self, k):
        if self.pick and self.placing():        # a start is picked: Right and Down point the ship
            if k in ("RIGHT", "DOWN"):
                self.across = k == "RIGHT"
            return None
        if self.v and self.v["rows"]:
            n = self.v["n"]
            dx, dy = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}[k]
            self.cur = ((self.cur[0] + dx) % n, (self.cur[1] + dy) % n)
        return None

    def on_escape(self):
        if self.pick:
            self.pick = None
            return None
        return "menu"

    async def on_click(self, value):
        if value[0] == "sq" and value[1] == self.side():
            self.cur = (value[2], value[3])
            if self.placing() and self.pick and self.cur != self.pick:     # a click on the other end points it
                if self.cur[1] == self.pick[1] or self.cur[0] == self.pick[0]:
                    self.across = self.cur[1] == self.pick[1]
            return await self.act()
        return None

    async def on_enter(self):
        return await self.act()

    async def act(self):
        """Enter, Space or a click: place the ship, say ready, or fire."""
        if self.me < 0 or not self.playing():
            return self.say(self.not_yours())
        v = self.v
        if self.placing():
            if not v["next"]:
                return await self.play("ready")
            if not self.pick:
                self.pick, self.across = self.cur, True
                if not self.preview():
                    self.across = False
                return None
            t = token_at(self.pick[0], self.pick[1], self.across, v["next"], v["n"])
            if not t or t not in v["moves"]:
                return self.say("The %d-square ship doesn't fit there %s." % (
                    v["next"], "across" if self.across else "down"), A_RED)
            self.pick = None
            return await self.play(t)
        if v["phase"] == "place":
            return self.say("Waiting for %s to place a fleet." % self.who(self.name(1 - self.me)))
        if not self.my_turn():
            return self.say(self.not_yours())
        return await self.play(sq(*self.cur))

    async def play(self, token):
        err = await self.move(token)
        if err:
            self.say(err, A_RED)
        self.snap()

    async def hotkey(self, k):
        if k in "guz":
            if not self.placing():
                return self.say("%s works only while you place your fleet." % k.upper())
            word = {"g": "random", "u": "undo", "z": "clear"}[k]
            if word not in self.v["moves"]:
                return self.say("You haven't placed a ship yet." if k != "g" else "Your fleet is placed already.")
            self.pick = None
            return await self.play(word)
        return await super().hotkey(k)


class PlainNaval(NavalBase, PlainGame):
    """Naval Combat for plain terminals: the two grids printed side by side, typed moves at a prompt."""

    BOARD_KINDS = ("SHOT", "READY", "JOIN", "LEAVE", "START", "END")

    def lines(self):
        v, me = self.v, self.me
        out = [""] + self.plain_header()
        if not v["rows"]:
            return out + self.plain_waiting()
        gl = nc_glyphs("plain")
        n = v["n"]
        left, right = sides(me)
        heads = [("Your fleet" if side == 0 else "%s's fleet" % self.who(self.name(seat))) if me >= 0
                 else "%s's fleet" % self.who(self.name(seat)) for side, seat in ((0, left), (1, right))]
        board = ["    %-22s    %s" % (heads[0][:22], heads[1][:22])]
        letters = "    " + "".join(" %s" % chr(97 + i) for i in range(n)) + "  "
        board.append(letters + "  " + letters.rstrip())
        frame = "   +" + "-" * (2 * n + 1) + "+"
        board.append(frame + "  " + frame)
        for y in range(n):
            cells = ["".join(gl.get(c, " .") for c in v["boards"][seat][y]) for seat in (left, right)]
            board.append("%2d |%s |  %2d |%s |" % (y + 1, cells[0], y + 1, cells[1]))
        board.append(frame + "  " + frame)
        players = [(i, None, ("ready" if v["ready"][i] else "placing") if v["phase"] == "place" else
                    "%d afloat" % len(afloat_sizes(v, i))) for i in (0, 1)]
        out += self.beside(board, self.side_text(players, width=25), 54)
        stats = [shot_counts(v, seat) for seat in (left, right)]
        out.append(("   %d hits and %d misses." % stats[0]).ljust(31) + "%d hits and %d misses." % stats[1])
        out.append(" %s is sea, %s a miss, %s a hit and %s a sunk ship.%s" % (
            gl["."].strip(), gl["o"].strip(), gl["x"].strip(), gl["#"].strip(),
            " %s is your ship." % gl["s"].strip() if me >= 0 else ""))
        return out + self.plain_log()

    def turn_prompt(self):
        if self.placing():
            if self.v["next"]:
                return "Place your %d-square ship. Type /a1 across or /a1 down, or /random." % self.v["next"]
            return "Your fleet is placed. Type /ready, or /undo."
        return "Your shot. Type a square such as /%s." % next((m for m in self.v["moves"] if SQ_RE.match(m)), "e5")

    def prompt(self):
        if self.placing():
            return self.turn_prompt() + " > "
        return super().prompt()

AnsiScreen, PlainScreen = AnsiNaval, PlainNaval
