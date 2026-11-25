"""Cannon Fire (cannonfire/1, docs/types/cannonfire-1.md): the web landscape and the telnet screens.

Tanks on a heightmap take turns to fire one shell each with an angle and a
power, and may drive left or right once before the shot (four times a game).
The server works out every flight and every move; this module only draws the
terrain (the Z lines), the tanks (K) with their hit points and moves left (L),
the wind (N) and the last shot (the TRJ points and H), and sends FIRE angle
power and MOVE L n or MOVE R n. It works out which columns a move can reach
(reach()) with the spec's path rule, only to offer them to the player.
"""
import math
import re

from ..telnet import (A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS, ROWS, AnsiGame, Canvas,
                      GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar, title_bar)
from ..telnet import describe_event as describe_common
from ..board import Canvas as Picture
from ..web import a, board_html, esc, hidden_fields

# ---- what the game is ------------------------------------------------------------

KEY = "cannonfire/1"
NAME = "Cannon Fire"
ORDER = 4                   # fourth in the build order (README.md)
PLAYERS = "2-8"
ABOUT = (
    "Two to eight players each command a tank on a hilly landscape. In turn, each sets the angle of the "
    "barrel and the power of the charge and fires one shell; the shell flies under gravity and the wind, "
    "blasts a crater where it lands and damages any tank nearby, and tanks settle when the ground under "
    "them is blown away. Before firing, a tank may drive a short way left or right. The last tank "
    "standing wins.",
    ["Classic rules: one shell type with a crater of radius 7; a direct hit takes 20 of a tank's 100 hit "
     "points and the blast less with distance, none from 10 units away; a tank that falls with the ground "
     "is not hurt. Tanks fire in seat order, and the server works out every flight in whole numbers, so "
     "every player sees the same shot.",
     "Moving: four moves a game, at most one a turn and before the shot. A move goes up to a tenth of the "
     "arena's width measured along the ground, so a tank climbing a slope gets less far; the arena's edge, "
     "another tank or a wreck, and a step of more than 4 units stop it.",
     "Three or more players: a player who resigns or runs out of time leaves the game and the others fire "
     "on; that tank becomes a wreck and is skipped, and the last tank standing wins.",
     "Arena width: 72 columns by default; the game type takes 40 to 200 columns (w=), a fixed landscape "
     "(seed=) and no wind (wind=0) over the native protocol.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])

LETTERS = "ABCDEFGH"
HMAX = 63
MOVES = 4                           # moves per tank per game (the spec's constants)
CLIMB = 4                           # a step up or down of more than this stops a move
STEP = [10, 14, 22, 32, 41]         # the path cost of a step by its height change, tenths of a column


def letter(seat):
    return LETTERS[seat] if 0 <= seat < len(LETTERS) else "?"


# ---- the view and the events (docs/types/cannonfire-1.md) ------------------------------

def parse_view(lines):
    """VUE lines for cannonfire/1 -> dict. rows (the terrain heights) is empty until the game starts."""
    v = {"seq": 0, "rows": [], "turn": -1, "moves": [], "status": "", "clock": None, "w": 0,
         "tanks": [], "aim": [], "scores": [], "wind": 0, "hit": None, "gone": [], "left": [], "budget": 0,
         "moved": False}
    z = {}

    def put(lst, i, val):
        while len(lst) <= i:
            lst.append(None)
        lst[i] = val
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        try:
            if tag == "V":
                v["seq"] = int(f[2])
            elif tag == "ARN":
                v["w"] = int(f[1])
            elif tag == "Z" and len(f) == 3:
                z[int(f[1])] = [max(0, min(HMAX, ord(c) - 0x30)) for c in f[2]]
            elif tag == "K":
                put(v["tanks"], int(f[1]), (int(f[2]), int(f[3])))
            elif tag == "A":
                put(v["aim"], int(f[1]), (int(f[2]), int(f[3])))
            elif tag == "L":
                put(v["left"], int(f[1]), int(f[2]))
            elif tag == "B":
                v["budget"], v["moved"] = int(f[1]), f[2] == "1"
            elif tag == "T":
                put(v["scores"], int(f[1]), int(f[2]))
            elif tag == "N":
                v["wind"] = int(f[1])
            elif tag == "H":
                v["hit"] = (int(f[1]), int(f[2]))
            elif tag == "W":
                v["turn"] = int(f[1])
            elif tag == "G":
                v["gone"] = [x == "1" for x in f[1:]]
            elif tag == "CLK":
                v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
            elif tag == "S":
                v["status"] = l[2:]
        except (ValueError, IndexError):
            continue
    for x in sorted(z):
        if x == len(v["rows"]):
            v["rows"].extend(z[x])
    if v["rows"]:
        v["w"] = len(v["rows"])
    v["tanks"] = [t or (0, 0) for t in v["tanks"]]
    v["aim"] = [t or (90, 50) for t in v["aim"]]
    v["scores"] = [t or 0 for t in v["scores"]]
    v["left"] = [t if t is not None else 0 for t in v["left"]]
    return v


# ---- moving -------------------------------------------------------------------------------

def can_move(v, seat):
    """True when it is seat's turn, its tank stands, it has moves left and has not moved this turn."""
    return (0 <= seat < len(v["tanks"]) and v["turn"] == seat and bool(v["rows"]) and v["tanks"][seat][1] > 0
            and seat < len(v["left"]) and v["left"][seat] > 0 and not v["moved"] and v["budget"] > 0
            and "Game over" not in v["status"])


def reach(v, seat):
    """The columns tank seat can drive to with one move now -> {column: path used, in tenths}, by the spec's
    rule (each step to the next column costs STEP[height change]; the edge, another tank or wreck, and a
    step of more than CLIMB stop it; at most the budget). Empty when it cannot move."""
    if not can_move(v, seat):
        return {}
    rows, w = v["rows"], len(v["rows"])
    x0 = v["tanks"][seat][0]
    taken = {x for s, (x, _) in enumerate(v["tanks"]) if s != seat}
    out = {}
    for d in (-1, 1):
        x, used = x0, 0
        while 0 <= x + d < w and x + d not in taken:
            dh = abs(rows[x + d] - rows[x])
            if dh > CLIMB or used + STEP[dh] > v["budget"]:
                break
            x, used = x + d, used + STEP[dh]
            out[x] = used
    return out


def move_to(v, seat, x):
    """The move that takes tank seat to column x (one reach() gives), as MOVE L n or MOVE R n: n is the path
    to x in whole columns, rounded up (no step costs less than a column, so it stops at x)."""
    used = reach(v, seat).get(x)
    if used is None:
        return None
    return "MOVE %s %d" % ("L" if x < v["tanks"][seat][0] else "R", (used + 9) // 10)


def full_move(v, d):
    """MOVE L n or MOVE R n for the whole budget (the tank goes as far as it can)."""
    return "MOVE %s %d" % ("L" if d < 0 else "R", max(1, (v["budget"] + 9) // 10))


def budget_text(v):
    """The path one move may use, in columns: 72 tenths -> '7.2'."""
    return "%d.%d" % divmod(v["budget"], 10)


def is_gone(v, i):
    """True for a seat that left the game (resigned or ran out of time with three or more playing)."""
    return 0 <= i < len(v.get("gone") or []) and v["gone"][i]


def _who(names, i):
    n = names[i] if 0 <= i < len(names) and names[i] != "-" else "tank %s" % letter(i)
    return n


def describe_event(text, names):
    """Cannon Fire's own events -> a line for the log ("" for those the log does not need, None for the
    shared ones)."""
    p = text.split()
    try:
        k = p[0]
        if k == "FIRE" and len(p) == 4:
            return "%s (%s) fires: angle %s, power %s" % (_who(names, int(p[1])), letter(int(p[1])), p[2], p[3])
        if k == "DMG" and len(p) == 3:
            return "%s is hit: %s hp left" % (_who(names, int(p[1])), p[2])
        if k == "FALL" and len(p) == 4:
            return "%s falls with the ground: %s hp" % (_who(names, int(p[1])), p[3])
        if k == "MOVE" and len(p) == 6:
            n = abs(int(p[3]) - int(p[2]))
            return "%s (%s) moves %s %d column%s (%s move%s left)" % (
                _who(names, int(p[1])), letter(int(p[1])), "left" if int(p[3]) < int(p[2]) else "right", n,
                "" if n == 1 else "s", p[5], "" if p[5] == "1" else "s")
        if k == "DIE" and len(p) == 2:
            return "%s (%s) is destroyed" % (_who(names, int(p[1])), letter(int(p[1])))
        if k == "OUT":
            return "The shell flies out of the arena"
        if k in ("TRJ", "HIT", "ZCH", "N"):
            return ""
    except ValueError:
        return None
    return None


def wind_text(w):
    if w == 0:
        return "no wind"
    return "wind %d to the %s" % (abs(w), "right" if w > 0 else "left")


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^([Ff][Ii][Rr][Ee] +[0-9]{1,3} +[0-9]{1,3}"            # FIRE 45 60
                     r"|[Mm][Oo][Vv][Ee] +([LlRr] +|[-+])[0-9]{1,3})\Z")        # MOVE L 7, MOVE +7
SEL_RE = re.compile(r"^[0-9]{1,3}-[0-9]{1,3}\Z")                             # the aim being set: 45-60
MOVE_FIELDS = ["angle", "power", "dir", "dist"]     # the shot form's two fields, the move form's two
NUMBER_RE = re.compile(r"^[0-9]{1,3}\Z")
DIRS = {"l": "L", "left": "L", "r": "R", "right": "R"}


def move_from_fields(values):
    """The shot form's angle and power -> 'FIRE angle power' (both whole numbers of up to three digits; the
    plugin says when one is out of range), or the move form's dir (left or right) and dist -> 'MOVE L n'.
    None for anything else, a mix of the two forms included."""
    ang, pw, dr, dist = values
    if dr or dist:
        if ang or pw or dr.lower() not in DIRS or not NUMBER_RE.match(dist):
            return None
        return "MOVE %s %d" % (DIRS[dr.lower()], int(dist))
    if not (NUMBER_RE.match(ang) and NUMBER_RE.match(pw)):
        return None
    return "FIRE %d %d" % (int(ang), int(pw))

SEAT_RGB = [(220, 40, 40), (240, 160, 0), (40, 80, 220), (200, 40, 200), (250, 250, 250), (150, 90, 30),
            (0, 170, 180), (70, 70, 70)]
SKY_BANDS = [(112, 168, 232), (132, 184, 240), (152, 198, 246), (172, 212, 250), (192, 226, 254)]   # top down
GRASS, CRUST, SOIL = (70, 150, 50), (20, 60, 10), (110, 80, 40)
FRESH, FRESH_CRUST = (196, 140, 80), (110, 60, 20)       # ground dug by the last shot
INK, WHITE, WRECK, TRAIL, BURST = (0, 0, 0), (255, 255, 255), (120, 120, 120), (40, 40, 40), (230, 40, 0)
HP_FULL, HP_LOST = (40, 200, 40), (200, 30, 30)          # a tank's hit point bar
REACH = (255, 230, 0)       # the ground a move can reach (on the player's turn, before the shot)
TOP = 12                    # rows of sky above the highest terrain (and the wind arrow)


def images():
    """None: the landscape is drawn as one image (board_image)."""
    return {}


def web_events(v, events):
    """The last shot, from the table's events (the view has only its burst point): v["trail"] its path
    (TRJ points), v["burst"] (x, y, r) from HIT, v["dug"] the columns its crater changed (ZCH)."""
    trail, burst, dug = [], None, []
    for e in events:
        f = e.split()
        if not f:
            continue
        try:
            if f[0] == "FIRE":
                trail, burst, dug = [], None, []
            elif f[0] == "TRJ":
                nums = [int(n) for n in f[1:]]
                trail += list(zip(nums[0::2], nums[1::2]))
            elif f[0] == "HIT" and len(f) == 4:
                burst = (int(f[1]), int(f[2]), int(f[3]))
            elif f[0] == "ZCH" and len(f) == 3:
                dug += range(int(f[1]), int(f[1]) + len(f[2]))
        except ValueError:
            continue
    v["trail"], v["burst"], v["dug"] = trail, burst, sorted(set(dug))


def scale(v):
    """Pixels per column and per unit of height: 3, or 2 for an arena wider than 140 columns."""
    return 3 if v["w"] <= 140 else 2


def geometry(v):
    """(pixels per unit, image width, image height)."""
    k = scale(v)
    return k, v["w"] * k, (HMAX + 1 + TOP) * k


def to_px(v, x, y):
    """A point of the arena (column, height) -> the pixel at its center."""
    k, _, h = geometry(v)
    return x * k + k / 2.0, h - 1 - y * k


def tank_at(v, s):
    """The pixel where tank s's barrel turns (on the top of its body)."""
    x, _ = v["tanks"][s]
    rows = v["rows"]
    k, _, h = geometry(v)
    cx, gy = to_px(v, x, rows[x] if 0 <= x < len(rows) else 0)
    return cx, gy - 5


def aims_shown(v, seat, sel):
    """Every tank's aim, with the player's own one being set (sel) when it is their shot."""
    aims = list(v["aim"])
    if sel and SEL_RE.match(sel) and 0 <= seat < len(aims):
        aims[seat] = aim_of(v, seat, sel)
    return aims


def board_image(v, seat, sel):
    """The landscape as one image, about 3 pixels a column: a banded sky, the ground with a dark crust
    (the ground dug by the last shot in a lighter shade), every tank with its treads, body, letter and
    barrel at its aim (wrecks gray and flattened), the last shot's path dotted with a ring at the burst,
    and the wind as an arrow with its strength. Each standing tank has a bar of its hit points above its
    letter. On the player's shot the whole picture is clickable (by ISMAP): a point aims the barrel at it,
    and before the shot the ground a move can reach is marked in yellow, where a click drives there
    (board_click)."""
    k, w, h = geometry(v)
    c = Picture(w, h)
    band = h // len(SKY_BANDS) + 1
    for i, rgb in enumerate(SKY_BANDS):
        c.fill(0, i * band, w, band, rgb)
    rows, dug = v["rows"], set(v.get("dug") or ())
    n = len(rows)
    for px in range(w):
        u = (px + 0.5) / k - 0.5
        i0 = max(0, min(n - 1, int(math.floor(u))))
        i1 = min(n - 1, i0 + 1)
        t = min(1.0, max(0.0, u - i0))
        top = int(round(h - 1 - (rows[i0] * (1 - t) + rows[i1] * t) * k))
        col = px // k
        fresh = col in dug
        c.fill(px, top, 1, h - top, FRESH if fresh else GRASS)
        c.fill(px, top + (3 * k if fresh else 2 * k), 1, h, SOIL)
        c.fill(px, top, 1, 2, FRESH_CRUST if fresh else CRUST)
    for x in reach(v, seat):
        c.fill(x * k, h - 1 - rows[x] * k - 1, k, 3, REACH)
    for (x0, y0), (x1, y1) in zip(v.get("trail") or [], (v.get("trail") or [])[1:]):
        (ax, ay), (bx, by) = to_px(v, x0, y0), to_px(v, x1, y1)
        c.line(ax, ay, bx, by, TRAIL, width=2, dash=3)
    burst = (v.get("burst") or v.get("hit") or ())[:2]        # from the events, else the view's H line
    if burst:
        px, py = to_px(v, *burst)
        c.disc(px, py, 6.5, INK, r_in=4.0)
        c.disc(px, py, 5.5, BURST, r_in=4.5)
    aims = aims_shown(v, seat, sel)
    for s, (x, hp) in enumerate(v["tanks"]):
        if 0 <= x < n:
            draw_tank(c, v, s, x, hp, aims[s] if s < len(aims) else (90, 50), s == seat)
    wind = v["wind"]
    if wind:
        c.text(3, 2, "wind %d" % abs(wind), INK)
        ax = 3 + 6 * 7
        length = 6 + 3 * abs(wind)
        x0, x1 = (ax, ax + length) if wind > 0 else (ax + length, ax)
        c.line(x0, 5, x1, 5, INK, width=2)
        d = 1 if wind > 0 else -1
        c.line(x1, 5, x1 - 4 * d, 1, INK, width=2)
        c.line(x1, 5, x1 - 4 * d, 9, INK, width=2)
    else:
        c.text(3, 2, "no wind", INK)
    if seat >= 0 and v["turn"] == seat and "Game over" not in v["status"] and 0 <= seat < len(v["tanks"]) \
            and v["tanks"][seat][1] > 0:
        c.anywhere = True
    return c


def draw_tank(c, v, s, x, hp, aim, mine):
    """One tank on its column: treads, body and turret in the seat's color with black outlines, the
    barrel at its aim, its letter above (the color is never the only cue) and its hit points as a bar
    above that, green for what is left. A wreck: gray, flat, no barrel, no bar."""
    cx, ty = tank_at(v, s)
    gy = ty + 5
    if hp <= 0:
        c.fill(cx - 6, gy - 3, 13, 3, INK)
        c.fill(cx - 5, gy - 2, 11, 2, WRECK)
        c.line(cx + 1, gy - 3, cx + 5, gy - 6, INK, width=1)
        c.text(cx + 1, gy - 14, letter(s), WRECK, align="center")
        return
    colour = SEAT_RGB[s % len(SEAT_RGB)]
    c.fill(cx - 6, gy - 2, 13, 3, INK)                 # treads
    c.fill(cx - 5, gy - 1, 11, 1, (90, 90, 90))
    c.fill(cx - 5, gy - 5, 11, 4, INK)                 # body, outlined
    c.fill(cx - 4, gy - 4, 9, 2, colour)
    c.disc(cx, ty, 3.2, INK)                           # turret
    c.disc(cx, ty, 2.2, colour)
    a_ = math.radians(aim[0])
    c.line(cx, ty, cx + 8 * math.cos(a_), ty - 8 * math.sin(a_), INK, width=2)
    ly = ty - 16
    label = letter(s)
    c.fill(cx - 3, ly - 1, 7, 9, INK if mine else WHITE)
    c.text(cx + 1, ly, label, WHITE if mine else INK, align="center")
    full = max(1, min(11, (hp * 11 + 99) // 100))
    c.fill(cx - 6, ly - 6, 13, 4, INK)
    c.fill(cx - 5, ly - 5, full, 2, HP_FULL)
    c.fill(cx - 5 + full, ly - 5, 11 - full, 2, HP_LOST)


def board_click(v, seat, sel, x, y):
    """A click on the landscape (ISMAP). On the ground a move can reach (marked in yellow, up to 3 units
    above it): drive there (/move.htm with MOVE L n or MOVE R n). Anywhere else: aim the player's barrel at
    the point, keeping the power; the page comes back with that aim (sel=angle-power), as a click on the
    sky does on telnet."""
    if not (0 <= seat < len(v["tanks"])):
        return None
    k, _, h = geometry(v)
    col = int(x) // k
    if 0 <= col < len(v["rows"]) and y >= h - 1 - (v["rows"][col] + 3) * k:
        mv = move_to(v, seat, col)
        if mv:
            return "/move.htm", {"mv": mv}
    cx, ty = tank_at(v, seat)
    dx, dy = x - cx, ty - y
    ang = int(round(math.degrees(math.atan2(dy, dx)))) if (dx or dy) else 90
    if ang < 0:
        ang = 0 if ang > -90 else 180
    _, pw = aim_of(v, seat, sel)
    return "/table.htm", {"sel": "%d-%d" % (min(180, max(0, ang)), pw)}


def aim_of(v, seat, sel):
    """The aim the page shows: sel (45-60) if given, else the seat's last aim."""
    if sel and SEL_RE.match(sel):
        a_, p_ = (int(n) for n in sel.split("-"))
        return min(180, a_), min(100, p_)
    if 0 <= seat < len(v["aim"]):
        return v["aim"][seat]
    return 45, 50


def aim_html(v, tid, link, seat, sel):
    """Links that change the angle and power (the page comes back with sel=angle-power), the Fire link, and
    a form to type a shot: angle and power in two fields (MOVE_FIELDS), sent to /move.htm."""
    ang, pw = aim_of(v, seat, sel)

    def adj(da, dp, label):
        na, np_ = min(180, max(0, ang + da)), min(100, max(0, pw + dp))
        return a(link("/table.htm", t=tid, sel="%d-%d" % (na, np_)), label)
    hidden = hidden_fields(link("/move.htm", t=tid))
    side = "right" if ang < 90 else "left" if ang > 90 else "straight up"
    fire = a(link("/move.htm", t=tid, mv="FIRE %d %d" % (ang, pw)), "<b>Fire at angle %d, power %d</b>" % (ang, pw))
    return ("<p><b>Your shot</b><br>\nAngle: %s %s <b>%d</b> %s %s (%s)<br>\nPower: %s %s <b>%d</b> %s %s<br>\n"
            "%s</p>\n<form action=\"/move.htm\" method=\"get\">\n%s\n"
            "Angle <input name=\"angle\" size=\"3\" maxlength=\"3\" value=\"%d\"> "
            "power <input name=\"power\" size=\"3\" maxlength=\"3\" value=\"%d\"> <input type=\"submit\" "
            "value=\"Fire\">\n</form>\n<p><font size=\"2\">Angle 0 points right, 90 straight up, 180 left; "
            "power 0 to 100.</font></p>"
            % (adj(10, 0, "&lt;&lt;"), adj(1, 0, "&lt;"), ang, adj(-1, 0, "&gt;"), adj(-10, 0, "&gt;&gt;"), side,
               adj(0, -10, "-10"), adj(0, -1, "-1"), pw, adj(0, 1, "+1"), adj(0, 10, "+10"),
               fire, hidden, ang, pw))


def move_html(v, tid, link, seat):
    """Before the shot: Move left and Move right (as far as the tank can go), a form for a distance, and
    the moves left; after a move, or with none left, just that."""
    left = v["left"][seat] if seat < len(v["left"]) else 0
    head = "<b>Moves left: %d of %d</b>" % (left, MOVES)
    if not can_move(v, seat):
        why = "you have moved this turn: now fire" if v["moved"] else "no moves left this game"
        return "<p>%s (%s).</p>\n" % (head, why)
    x0 = v["tanks"][seat][0]
    got = reach(v, seat)
    links = []
    for d, word in ((-1, "left"), (1, "right")):
        ends = [x for x in got if (x - x0) * d > 0]
        if ends:
            far = min(ends) if d < 0 else max(ends)
            links.append("%s (to column %d)" % (a(link("/move.htm", t=tid, mv=full_move(v, d)), "Move " + word),
                                                 far))
        else:
            links.append("Move %s: blocked" % word)
    hidden = hidden_fields(link("/move.htm", t=tid))
    return ("<p>%s, one a turn before you fire; each up to %s columns along the ground (less up or down a "
            "slope).<br>\n%s<br>\nOr click the ground marked in yellow to drive there.</p>\n"
            "<form action=\"/move.htm\" method=\"get\">\n%s\nMove <select name=\"dir\"><option value=\"left\">left"
            "<option value=\"right\">right</select> <input name=\"dist\" size=\"3\" maxlength=\"3\" value=\"%d\"> "
            "columns <input type=\"submit\" value=\"Move\">\n</form>\n"
            % (head, budget_text(v), " | ".join(links), hidden, (v["budget"] + 9) // 10))


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    over = "Game over" in v["status"]
    my_turn = seat >= 0 and v["turn"] == seat and not over
    rows = ['<tr><td></td><td><b>Tank</b></td><td><b>Player</b></td><td><b>Column</b></td><td><b>HP</b></td>'
            '<td><b>Moves</b></td><td><b>Points</b></td></tr>']
    for s, (x, hp) in enumerate(v["tanks"]):
        mark = "&gt;" if s == v["turn"] and not over else ""
        name = esc(_who(names, s)) + (" (you)" if s == seat else "")
        pts = v["scores"][s] if s < len(v["scores"]) else 0
        moves = v["left"][s] if s < len(v["left"]) else 0
        rows.append('<tr><td>%s</td><td><b>%s</b></td><td>%s</td><td align="right">%d</td><td align="right">%s</td>'
                    '<td align="right">%s</td><td align="right">%d</td></tr>'
                    % (mark, letter(s), name, x, hp if hp > 0 else "left" if is_gone(v, s) else "dead",
                       moves if hp > 0 else "", pts))
    side = '<table border="0" cellpadding="2">%s</table>\n<p>%s%s</p>' % (
        "".join(rows), esc(wind_text(v["wind"]).capitalize()),
        "; the last shell burst at column %d" % v["hit"][0] if v["hit"] else "")
    mobile = my_turn and can_move(v, seat)
    hint = ""
    if my_turn:
        hint = " - move if you like, then set the angle and power and Fire" if mobile else \
            " - set the angle and power, then Fire"
    actions = move_html(v, tid, link, seat) + aim_html(v, tid, link, seat, sel) if my_turn else ""
    board = board_html(board_image, v, tid, link, seat, sel, "Cannon Fire landscape, %s" % wind_text(v["wind"]))
    if my_turn:
        board += '<br><font size="2">Click the sky to aim there%s.</font>' % (
            ", or the yellow ground to drive there" if mobile else "")
    return board, side, hint, actions


# ---- telnet --------------------------------------------------------------------------
#
# The screen: row 1 the wind, the aim and the clock; rows 2-13 the landscape (one screen column per arena
# column up to 78, else several columns to one, showing the highest); row 14 a rule; rows 15-20 the players
# (left) and the log (right). Heights are drawn in half rows in pc and utf8 (24 levels for 0-63) and in
# whole rows plus a "_" for a half in ansi and plain.

LAND_TOP, LAND_ROWS = 2, 12
LEVELS = 2 * LAND_ROWS
SEAT_ATTR = ["0;1;31", "0;1;33", "0;1;36", "0;1;35", "0;1;37", "0;1;34", "0;1;32", "0;33"]
A_GROUND = "0;32;42"
A_HALF = "0;32"
A_ASCII_GROUND = "0;30;42"
A_TRAIL = "0;1;33"
A_BURST = "0;1;31"
A_WRECK = "0;37"
A_FIRE = "0;1;37;41"
A_REACH = "0;30;43"         # ground a move can reach


def cf_glyphs(mode):
    """Ground, trail and wind arrows per mode: CP437 shapes in pc and utf8, ASCII in ansi and plain."""
    if mode in ("pc", "utf8"):
        return {"full": "█", "half": "▄", "dot": "·", "wl": "«", "wr": "»", "deg": "°"}
    return {"full": "#", "half": "_", "dot": ".", "wl": "<", "wr": ">", "deg": ""}


def level(y):
    """A height (0-63) -> half rows (0-24)."""
    return (y * LEVELS + 32) // 64


def barrel(angle):
    """-> (dx, dy, char): where the barrel shows next to a tank, by angle."""
    if angle < 30:
        return 1, 0, "-"
    if angle < 75:
        return 1, -1, "/"
    if angle <= 105:
        return 0, -1, "|"
    if angle <= 150:
        return -1, -1, "\\"
    return -1, 0, "-"


def landscape(v, sw, glyphs, aims, trail, me=-1):
    """The landscape as LAND_ROWS rows of sw (char, attr) cells, top row first, and a function from an
    arena column to a screen column (0..sw-1)."""
    w, h = v["w"], v["rows"]
    grid = [[(" ", A_TEXT) for _ in range(sw)] for _ in range(LAND_ROWS)]

    def col(x):
        return x * sw // w

    def row_of(y):
        r = LAND_ROWS - 1 - level(y) // 2
        return r if 0 <= r < LAND_ROWS else None
    half = glyphs["half"]
    for j in range(sw):
        span = h[j * w // sw:max(j * w // sw + 1, (j + 1) * w // sw)]
        f = level(max(span))
        for k in range(LAND_ROWS):
            n = min(2, max(0, f - 2 * k))
            if n == 2:
                grid[LAND_ROWS - 1 - k][j] = (glyphs["full"], A_GROUND if half != "_" else A_ASCII_GROUND)
            elif n == 1:
                grid[LAND_ROWS - 1 - k][j] = (half, A_HALF)
    for x, y in trail:
        r = row_of(y)
        if r is not None and 0 <= x < w and grid[r][col(x)][0] == " ":
            grid[r][col(x)] = (glyphs["dot"], A_TRAIL)
    if v["hit"]:
        x, y = v["hit"]
        r = row_of(y)
        if r is not None and 0 <= x < w and grid[r][col(x)][0] in (" ", glyphs["dot"]):
            grid[r][col(x)] = ("*", A_BURST)
    for s, (x, hp) in enumerate(v["tanks"]):
        if not 0 <= x < w:
            continue
        r = max(0, LAND_ROWS - 1 - (level(h[x]) + 1) // 2)
        j = col(x)
        if hp <= 0:
            grid[r][j] = ("x", A_WRECK)
            continue
        grid[r][j] = (letter(s), SEAT_ATTR[s % 8] if s != me else SEAT_ATTR[s % 8] + ";7")
        if s < len(aims):
            dx, dy, ch = barrel(aims[s][0])
            bx, by = j + dx, r + dy
            if 0 <= bx < sw and 0 <= by < LAND_ROWS and grid[by][bx][0] in (" ", glyphs["dot"]):
                grid[by][bx] = (ch, SEAT_ATTR[s % 8])
    return grid, col


HELP = [
    "Left/Right   turn the barrel by 1 degree",
    "Up/Down      power up or down by 1",
    "< >  Home End  turn the barrel by 10 degrees",
    "+ -  PgUp PgDn power up or down by 10",
    "Enter/Space  fire",
    "[ ]          move left or right as far as you can",
    "             (once a turn, before firing)",
    "Mouse        click the sky to aim there, the ground",
    "             beside your tank to drive there",
    "T  talk      R  resign (asks first)",
    "Esc          back to the menu; the game goes on",
    "Typed: 45 60 or fire 45 60 (angle, power),",
    "move l 5, move r 5, move -5 (columns of path),",
    "say <text>, resign, redraw, menu, help.",
]


def hp_bar(hp, full="#", empty="."):
    """Hit points as five cells, one per 20 (rounded up): 100 -> '#####', 30 -> '##...'."""
    n = min(5, max(0, (hp + 19) // 20))
    return full * n + empty * (5 - n)


class CannonBase(GameBase):
    """What both Cannon Fire screens share: the view, the events, typed shots, the last shot's path."""

    parse = staticmethod(parse_view)

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.trail = []
        self.aim = [45, 50]

    def describe(self, text):
        d = describe_event(text, self.names)
        return d if d is not None else describe_common(text, self.names)

    def on_event(self, text):
        p = text.split()
        if p and p[0] == "FIRE":
            self.trail = []
        elif p and p[0] == "TRJ":
            nums = [int(n) for n in p[1:] if n.lstrip("-").isdigit()]
            self.trail += list(zip(nums[0::2], nums[1::2]))

    def move_word(self, cmd, words=()):
        w = list(words)
        if w and w[0].lower() == "move":            # move l 5, move left 5, move -5, move +5, move r
            w = [x.lower() for x in w[1:]]
            if len(w) == 1 and w[0][:1] in "-+" and w[0][1:].isdigit() and len(w[0]) <= 4:
                return "MOVE %s%d" % (w[0][0], int(w[0][1:]))
            if w and w[0] in DIRS and (len(w) == 1 or (len(w) == 2 and w[1].isdigit() and len(w[1]) <= 3)):
                if len(w) == 1:
                    return full_move(self.v, -1 if DIRS[w[0]] == "L" else 1) if self.v else None
                return "MOVE %s %d" % (DIRS[w[0]], int(w[1]))
            return None
        if w and w[0].lower() == "fire":
            w = w[1:]
        if len(w) == 2 and all(n.isdigit() and len(n) <= 3 for n in w):
            return "FIRE %s %s" % (int(w[0]), int(w[1]))
        return None

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    def mobile(self):
        """True when the player can move now (their turn, before the shot, moves left)."""
        return self.my_turn() and can_move(self.v, self.me)

    def moves_text(self):
        """The player's moves, for the info lines: 'Moves 3 (7.2 each)', 'Moved: fire', 'No moves left'."""
        v = self.v
        left = v["left"][self.me] if 0 <= self.me < len(v["left"]) else 0
        if self.my_turn() and v["moved"]:
            return "Moved: fire"
        if not left:
            return "No moves left"
        return "Moves %d (%s each)" % (left, budget_text(v))

    def seats(self):
        return len(self.v["tanks"]) if self.v else 0

    def shooter_aim(self):
        """Every seat's aim, with the player's own one being set when it is their shot."""
        aims = list(self.v["aim"])
        if self.my_turn() and self.me < len(aims):
            aims[self.me] = tuple(self.aim)
        return aims

    def player_line(self, s, wide, bar=("#", ".")):
        """(text after the letter, is it their turn). Wide: the name, a bar of the hit points (one cell for
        20) and the number, the moves left and the points; narrow: the name and the hit points."""
        x, hp = self.v["tanks"][s]
        tomove = self.playing() and self.v["turn"] == s
        name = _who(self.names, s)
        if not wide:
            return "%-12s %3s" % (name[:12], hp if hp > 0 else "lft" if is_gone(self.v, s) else "---"), tomove
        pts = self.v["scores"][s] if s < len(self.v["scores"]) else 0
        moves = self.v["left"][s] if s < len(self.v["left"]) else 0
        if hp > 0:
            state = "%s %3d hp %dmv" % (hp_bar(hp, *bar), hp, moves)
        else:
            state = "%-16s" % ("left the game" if is_gone(self.v, s) else "dead")
        return "%-11s %s %5s" % (name[:11], state, "%dp" % pts), tomove


class AnsiCannon(CannonBase, AnsiGame):
    """Cannon Fire, full screen: the landscape across the top, the aim on row 1, players and log below."""

    HOTKEYS = "rt?<>,.+=-[]"
    LOG_TOP, LOG_ROWS = 15, 6
    TITLE, HELP = "Cannon Fire", HELP

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cf = cf_glyphs(sess.mode)

    def snap(self):
        mine = self.my_turn()
        if mine and not self.was_my_turn and self.me < len(self.v["aim"]):
            self.aim = list(self.v["aim"][self.me])
        self.was_my_turn = mine

    def geometry(self):
        w = self.v["w"] if self.v else 0
        sw = min(w, COLS - 2) if w else COLS - 2
        return sw, 1 + (COLS - 2 - sw) // 2

    # ---- drawing ---------------------------------------------------------
    def compose(self):
        cv = Canvas()
        g = self.g
        right = "watching" if self.me < 0 else "you are tank %s" % letter(self.me)
        title_bar(cv, g, "Cannon Fire %s table %s" % (g["v"], self.tid), right)
        started = bool(self.v and self.v["rows"])
        sw, off = self.geometry()
        if started:
            self.draw_info(cv)
            self.draw_land(cv, sw, off)
        for y in range(LAND_TOP, LAND_TOP + LAND_ROWS):
            cv.put(off - 1, y, g["v"], A_BORDER)
            cv.put(off + sw, y, g["v"], A_BORDER)
        rule(cv, g, 0, 14, COLS, A_BORDER, "Players", tattr=A_HEAD)
        cv.put(42, 14, " Log ", A_HEAD)
        for y in range(15, 21):
            cv.put(40, y, g["v"], A_BORDER)
        self.draw_players(cv)
        self.draw_log(cv, 42, COLS - 43)
        if not started:
            self.draw_waiting(cv, 26, 5)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        tail = [("T", "talk"), ("R", "resign"), ("?", "help"), ("Esc", "menu")]
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.my_turn():
            keys = [("Arrows", "aim"), ("Enter", "fire")] + ([("[ ]", "move")] if self.mobile() else []) + tail
        elif self.me >= 0 and self.playing():
            keys = tail
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_info(self, cv):
        v, cf = self.v, self.cf
        w = v["wind"]
        arrows = (abs(w) + 2) // 3
        if w > 0:
            wind = "Wind %d %s" % (w, cf["wr"] * arrows)
        elif w < 0:
            wind = "Wind %s %d" % (cf["wl"] * arrows, -w)
        else:
            wind = "No wind"
        cv.put(1, 1, wind, A_TEXT)
        if self.my_turn():
            ang, pw = self.aim
            text = "Angle %3d%s  Power %3d" % (ang, cf["deg"], pw)
            cv.put(16, 1, text, A_BRIGHT)
            cv.put(42, 1, "[ Fire ]", A_FIRE)
            cv.hot_area(42, 1, 8, 1, ("fire",))
            if self.mobile():
                left = v["left"][self.me]
                cv.put(51, 1, "[<]", A_BRIGHT)
                cv.hot_area(51, 1, 3, 1, ("move", -1))
                cv.put(55, 1, "%d" % left, A_TEXT)
                cv.put(57, 1, "[>]", A_BRIGHT)
                cv.hot_area(57, 1, 3, 1, ("move", 1))
        elif self.over:
            cv.put(16, 1, self.over[:46], A_YELLOW)
        elif self.playing() and v["turn"] >= 0:
            s = v["turn"]
            cv.put(16, 1, ("Tank %s (%s) to fire" % (letter(s), _who(self.names, s)))[:46], A_TEXT)
        c = self.clock()
        if c:
            seat, left, _ = c
            text = "%s %s left" % (letter(seat), fmt_secs(left))
            cv.put(COLS - 2 - len(text), 1, text, A_RED if seat == self.me and left < 60 else A_DIM)

    def draw_land(self, cv, sw, off):
        grid, col = landscape(self.v, sw, self.cf, self.shooter_aim(), self.trail, self.me)
        if self.mobile():                       # the ground a move can reach: its top cell in yellow
            ground = (self.cf["full"], self.cf["half"])
            for x in reach(self.v, self.me):
                j = col(x)
                r = next((r for r in range(LAND_ROWS) if grid[r][j][0] in ground), None)
                if r is not None:
                    grid[r][j] = (grid[r][j][0], A_REACH)
        for r, cells in enumerate(grid):
            for j, (ch, attr) in enumerate(cells):
                if ch != " ":
                    cv.put(off + j, LAND_TOP + r, ch, attr)
        if self.my_turn():
            for r in range(LAND_ROWS):
                for j in range(sw):
                    cv.hot_area(off + j, LAND_TOP + r, 1, 1, ("sky", j, r))

    def draw_players(self, cv):
        v = self.v
        n = self.seats()
        if not n:
            for i, name in enumerate(self.names[:6]):
                cv.put(3, 15 + i, letter(i), SEAT_ATTR[i])
                cv.put(5, 15 + i, ("(empty seat)" if name == "-" else name)[:12], A_DIM if name == "-" else A_TEXT)
            return
        wide = n <= 6
        for s in range(n):
            x0, y = (1, 15 + s) if wide else (20 * (s // 4), 15 + s % 4)
            text, tomove = self.player_line(s, wide, (self.cf["full"], self.cf["dot"]))
            dead = v["tanks"][s][1] <= 0
            cv.put(x0, y, self.g["ptr"] if tomove else " ", A_YELLOW)
            cv.put(x0 + (2 if wide else 1), y, letter(s), SEAT_ATTR[s % 8] if not dead else A_WRECK)
            cv.put(x0 + (4 if wide else 3), y, text[:(35 if wide else 16)],
                   A_BRIGHT if tomove else A_DIM if dead else A_TEXT)

    def hint(self):
        if not (self.v and self.v["rows"]):
            return ("Table %s starts when someone joins: telnet, the web (port 6500) or the native protocol."
                    % self.tid, A_DIM)
        if self.over:
            return "%s. Esc goes back to the menu." % self.over, A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        if self.mobile():
            return ("Your shot: arrows aim, Enter fires; first [ or ] moves (%d left, %s each)"
                    % (self.v["left"][self.me], budget_text(self.v)), A_TEXT)
        if self.my_turn():
            if self.move_set():
                return ("Your shot: %s/%s angle, %s/%s power, Enter fires. Or type: 45 60"
                        % tuple(self.move_key(d) for d in ("LEFT", "RIGHT", "UP", "DOWN")), A_TEXT)
            return "Your shot: Left/Right angle, Up/Down power, Enter fires. Or type: 45 60", A_TEXT
        if self.me < len(self.v["tanks"]) and self.v["tanks"][self.me][1] <= 0:
            return "Your tank is destroyed; the game goes on. Esc goes back to the menu.", A_DIM
        if self.v["turn"] < 0:
            return "Game over. Esc goes back to the menu.", A_YELLOW
        return "Waiting for %s to fire. Leaving or logging off never resigns." % self.name(self.v["turn"]), A_DIM

    # ---- keys --------------------------------------------------------------
    def turn_by(self, da=0, dp=0):
        if not self.my_turn():
            return self.say("It is not your shot." if self.me >= 0 else "You are watching this table.")
        self.aim = [min(180, max(0, self.aim[0] + da)), min(100, max(0, self.aim[1] + dp))]
        return None

    def on_arrow(self, k):
        da, dp = {"LEFT": (1, 0), "RIGHT": (-1, 0), "UP": (0, 1), "DOWN": (0, -1)}[k]
        return self.turn_by(da, dp)

    async def on_key(self, k):
        if not self.buf and k in ("HOME", "END", "PGUP", "PGDN"):
            da, dp = {"HOME": (10, 0), "END": (-10, 0), "PGUP": (0, 10), "PGDN": (0, -10)}[k]
            return self.turn_by(da, dp)
        return await super().on_key(k)

    async def hotkey(self, k):
        steps = {"<": (10, 0), ",": (10, 0), ">": (-10, 0), ".": (-10, 0), "+": (0, 10), "=": (0, 10),
                 "-": (0, -10)}
        if k in steps:
            return self.turn_by(*steps[k])
        if k in "[]":
            return await self.drive(full_move(self.v, -1 if k == "[" else 1) if self.v else None)
        return await super().hotkey(k)

    async def on_enter(self):
        return await self.fire()

    async def on_click(self, value):
        if value[0] == "fire":
            return await self.fire()
        if value[0] == "move":
            return await self.drive(full_move(self.v, value[1]))
        if value[0] == "sky" and self.my_turn():
            x = self.ground_at(value[1], value[2])
            if x is not None:
                return await self.drive(move_to(self.v, self.me, x))
            self.aim_at(value[1], value[2])
        return None

    def ground_at(self, j, r):
        """A click on screen cell (j, r) of the landscape: on the ground (or the cell just above it) where a
        move can reach -> the arena column to drive to (the farthest such one under the cell), else None."""
        v = self.v
        got = reach(v, self.me)
        if not got:
            return None
        sw, _ = self.geometry()
        w = v["w"]
        span = range(j * w // sw, max(j * w // sw + 1, (j + 1) * w // sw))
        f = level(max(v["rows"][x] for x in span))
        top = LAND_ROWS - 1 - max(0, (f - 1) // 2)
        if r < top - 1:
            return None
        x0 = v["tanks"][self.me][0]
        near = [x for x in span if x in got]
        return max(near, key=lambda x: abs(x - x0)) if near else None

    async def drive(self, word):
        """Play a move (MOVE L n or MOVE R n): only on the player's turn, before the shot."""
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.my_turn():
            return self.say("It is not your turn." if self.playing() else (self.over or "No game is running."))
        if not self.mobile():
            return self.say("You have moved this turn: now fire." if self.v["moved"] else
                            "Your tank has no moves left: fire.")
        if word:
            err = await self.move(word)
            if err:
                self.say(err, A_RED)
        self.snap()
        return None

    def aim_at(self, j, r):
        """Point the barrel at a screen cell of the landscape (the power stays)."""
        v = self.v
        sw, _ = self.geometry()
        x, _ = v["tanks"][self.me]
        tx = x * sw / v["w"]
        ty = level(v["rows"][x] + 1) / 2.0
        dx, dy = (j + 0.5) - (tx + 0.5), (LAND_ROWS - r - 0.5) - ty
        dx *= v["w"] / sw
        dy *= 64.0 / LAND_ROWS
        ang = int(round(math.degrees(math.atan2(dy, dx)))) if (dx or dy) else 90
        if ang < 0:
            ang = 0 if ang > -90 else 180
        self.aim[0] = min(180, max(0, ang))

    async def fire(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        if not self.my_turn():
            return self.say("It is %s's shot." % self.name(self.v["turn"]))
        err = await self.move("FIRE %d %d" % tuple(self.aim))
        if err:
            self.say(err, A_RED)
        self.snap()


class PlainCannon(CannonBase, PlainGame):
    """Cannon Fire for plain terminals: the landscape in ASCII, typed shots."""

    BOARD_KINDS = ("HIT", "OUT", "MOVE", "TURN", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" Type the angle and the power to fire, like 45 60 (or fire 45 60).\n"
                 " Angle 0 points right, 90 straight up, 180 left; power 0 to 100.\n"
                 " Before firing you may move once: move l 5 or move r 5 (columns of path;\n"
                 " a slope takes more), move l or move r to go as far as you can. Four a game.\n"
                 " say <text> talks to the table; resign gives up (it asks first).\n"
                 " menu goes back to the menu; the game goes on and leaving never resigns.")
    SW = 76

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you are tank %s" % letter(me)
        out = ["", "== Cannon Fire, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet, the web" % self.tid,
                          "(port 6500) or another client. Type menu to go back; the table stays open."]
        w = v["w"]
        sw = min(w, self.SW)
        grid, col = landscape(v, sw, cf_glyphs("plain"), list(v["aim"]), self.trail)
        out.append(" +" + "-" * sw + "+")
        for cells in grid:
            out.append((" |" + "".join(ch for ch, _ in cells) + "|").rstrip())
        out.append(" +" + "-" * sw + "+")
        ruler = [" "] * (sw + 4)
        for b in range(0, w, 10 if sw == w else 20):
            label = str(b)
            j = 2 + col(b)
            if j + len(label) <= len(ruler) and ruler[j - 1] == " ":
                ruler[j:j + len(label)] = list(label)
        out.append("".join(ruler).rstrip())
        n = self.seats()
        cells = []
        for s in range(n):
            text, tomove = self.player_line(s, n <= 4)
            cells.append(("%s %s %s" % (">" if tomove else " ", letter(s), text))[:39])
        if n <= 4:
            out += [" " + c for c in cells]
        else:
            for i in range(0, n, 2):
                out.append((" " + cells[i].ljust(39) + (cells[i + 1] if i + 1 < n else "")).rstrip())
        line = wind_text(v["wind"]).capitalize() + "."
        if self.over or v["turn"] < 0:
            line += " " + (self.over or "Game over.")
        elif self.my_turn():
            ang, pw = v["aim"][me] if me < len(v["aim"]) else (45, 50)
            line += " Your shot; your last aim was angle %d, power %d." % (ang, pw)
        else:
            line += " %s to fire." % self.name(v["turn"])
        out.append(" " + line[:78])
        if self.mobile():
            ends = sorted(reach(v, me))
            x0 = v["tanks"][me][0]
            out.append((" Before firing: move l n or move r n, up to %s columns of path (%d left)."
                        % (budget_text(v), v["left"][me]))[:79])
            out.append((" This turn it can reach columns %d to %d (it stands at %d)."
                        % (min(ends + [x0]), max(ends + [x0]), x0))[:79])
        elif self.my_turn() and v["moved"]:
            out.append(" You have moved this turn: now fire.")
        if not self.over:
            out.append(" " + clock_text(v, self.names)[:78])
        for l in self.log[-6:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        if self.my_turn():
            ang, pw = self.v["aim"][self.me] if self.me < len(self.v["aim"]) else (45, 50)
            more = ", or move l/r n" if self.mobile() else ""
            return "Your shot: angle power (like %d %d)%s > " % (ang, pw, more)
        return "> "


AnsiScreen, PlainScreen = AnsiCannon, PlainCannon
