"""Backgammon (backgammon/1, docs/types/backgammon-1.md): the web board and the telnet screens.

A staged game: a turn is several steps (13-9, bar-22, 6-off), each sent with
MOV, which UND takes back and END finishes. The game words roll, double,
take, drop, accept and reject are moves too.
"""
import re
import textwrap
import time

from ..telnet import (A_AXIS, A_BRIGHT, A_DIM, A_RED, A_SEL, A_TEXT, A_YELLOW, AnsiGame, COLS, Canvas, GameBase,
                      PlainGame, ROWS, and_list, or_list, title_bar)
from ..telnet import box, describe_event as describe_common
from ..board import SMALL, Canvas as Picture, text_size
from ..web import BG_HOME_PAGE, a, board_html, esc, hidden_fields, make_gif
from ..common import BG_HOME_PREF, BG_HOMES

# ---- what the game is ------------------------------------------------------------

KEY = "backgammon/1"
NAME = "Backgammon"
ORDER = 2
PLAYERS = "2"
ABOUT = (
    "Two players race fifteen checkers each around a board of 24 points, in opposite directions, moving "
    "by the throw of two dice. A lone checker can be hit and sent back to the start, and a player who "
    "brings all fifteen home can bear them off; the first to bear off every checker wins. The doubling "
    "cube lets a player raise the stakes, and the other player must accept or give up the game.",
    ["Standard rules, with the server rolling all dice: an opening roll of one die each decides who "
     "starts; the full use of the dice is enforced (as many dice as possible, the larger die when only "
     "one can be played).",
     "Doubling cube: on by default (a table can be started without it). Doubles are offered before the "
     "roll, up to 64; beavers, the Jacoby rule and automatic doubles are not offered.",
     "Match play: matches to 1 to 99 points, with several games at one table, the score kept, and the "
     "Crawford rule (no doubling in the game after a player first reaches one point short of the match). "
     "Gammons and backgammons count in every game. FIBS clients start matches with invite; the web site "
     "and telnet start single games.",
     "Resigning: a player may offer to resign a single game, a gammon or a backgammon, and the other "
     "player accepts or rejects the offer.",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])


# ---- the view and the events (docs/types/backgammon-1.md) ----------------------------

def parse_view(lines):
    """VUE lines for backgammon/1 -> dict. rows is True once there is a position (the game has started)."""
    v = {"pts": [0] * 24, "bar": (0, 0), "off": (0, 0), "dice": [], "cube": 1, "cube_owner": -1, "turn": -1,
         "phase": "", "moves": [], "status": "", "seq": 0, "clock": None, "rows": False,
         "need": 0, "rolled": None, "cube_ok": None, "match": 0, "score": [0, 0], "game": 1, "crawford": False,
         "offer": None}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "N" and len(f) == 2:
            v["need"] = int(f[1])
        elif tag == "DR" and len(f) == 3:
            v["rolled"] = (int(f[1]), int(f[2]))
        elif tag == "CA" and len(f) == 3:
            v["cube_ok"] = (f[1] == "1", f[2] == "1")
        elif tag == "L" and len(f) == 2:
            v["match"] = int(f[1])
        elif tag == "T" and len(f) == 3 and f[1] in ("0", "1"):
            v["score"][int(f[1])] = int(f[2])
        elif tag == "G" and len(f) >= 2:
            v["game"], v["crawford"] = int(f[1]), "crawford" in f[2:]
        elif tag == "RS" and len(f) == 4:
            v["offer"] = (int(f[1]), int(f[2]), f[3])
        elif tag == "V":
            v["seq"] = int(f[2])
        elif tag == "P" and len(f) == 14:
            start = int(f[1])
            for i, n in enumerate(f[2:]):
                v["pts"][start - 1 + i] = int(n)
        elif tag == "B":
            v["bar"] = (int(f[1]), int(f[2]))
        elif tag == "O":
            v["off"] = (int(f[1]), int(f[2]))
        elif tag == "D":
            v["dice"] = [int(x) for x in f[1:]]
        elif tag == "C":
            v["cube"] = int(f[1])
            v["cube_owner"] = -1 if f[2] == "-" else int(f[2])
        elif tag == "W":
            v["turn"] = -1 if f[1] == "-" else int(f[1])
        elif tag == "Q":
            v["phase"] = f[1]
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK":
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    v["rows"] = bool(any(v["pts"]) or any(v["off"]) or any(v["bar"]))     # no checkers yet: waiting
    return v


def describe_event(text, names):
    """Backgammon events -> human text (None if not a backgammon event)."""
    p = text.split()
    k = p[0]

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) else "seat %d" % i
    if k == "ROLL":
        return "%s rolls %s-%s" % (nm(p[1]), p[2], p[3])
    if k == "STEP":
        return "%s moves %s to %s%s" % (nm(p[1]), p[2], p[3], ", hits" if len(p) > 4 else "")
    if k == "UNDO":
        return "%s takes back %s-%s" % (nm(p[1]), p[2], p[3])
    if k == "DBL":
        return "%s doubles to %s" % (nm(p[1]), p[2])
    if k == "TAKE":
        return "%s takes; the cube is at %s" % (nm(p[1]), p[2])
    if k == "DROP":
        return "%s drops" % nm(p[1])
    if k == "OPEN":
        return "Opening roll: %s %s, %s %s" % (nm(0), p[1], nm(1), p[2])
    if k == "OFFER":
        return "%s offers to resign (%s, %s point%s)" % (nm(p[1]), p[3], p[2], "" if p[2] == "1" else "s")
    if k in ("ACCEPT", "REJECT"):
        return "%s %ss" % (nm(p[1]), k.lower())
    if k == "WON":
        return "%s wins the game, %s point%s" % (nm(p[1]), p[2], "" if p[2] == "1" else "s")
    if k == "SCORE":
        return "Match score: %s %s, %s %s" % (nm(0), p[1], nm(1), p[2])
    if k == "GAME":
        return "Game %s of the match%s" % (p[1], " (Crawford: no doubling)" if "crawford" in p[2:] else "")
    if k == "STEPS":
        return ""
    if k == "END" and len(p) >= 5 and p[1] == "win":
        n = int(p[3])
        return "Game over: %s wins %d point%s%s" % (nm(p[2]), n, "" if n == 1 else "s",
                                                   "" if p[4] == "single" else " (%s)" % p[4])
    return None


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^((bar|[0-9]{1,2})-(off|[0-9]{1,2})|roll|double|take|drop|accept|reject)\Z")
SEL_RE = re.compile(r"^(bar|[0-9]{1,2})\Z")     # the point of a picked-up checker

BG_W, BG_H, BG_SLICES = 24, 18, 5
BG_PAL = [(0, 96, 56), (176, 48, 40), (226, 206, 160), (246, 246, 240), (30, 30, 30), (110, 110, 110),
          (255, 214, 0), (80, 60, 40)]


def _bg_slice(top, colour, checker, i):
    """One slice of a point. top: base at the top edge; color 'r' or 'c' (red,
    cream); checker 'n' none, 'w' white, 'k' black, 'h' hint (a move target),
    's' selected (a white or black checker is drawn by the caller as 'w'/'k')."""
    px = []
    depth_total = BG_H * BG_SLICES
    for r in range(BG_H):
        depth = i * BG_H + (r if top else BG_H - 1 - r)
        half = 12.0 * (1 - depth / depth_total)
        for x in range(BG_W):
            c = 0
            if abs(x - 11.5) <= half:
                c = 1 if colour == "r" else 2
            d2 = (x - 11.5) ** 2 + (r - 8.5) ** 2
            if checker in ("w", "k") and d2 <= 8.6 ** 2:
                c = 5 if d2 > 7.6 ** 2 else (3 if checker == "w" else 4)
            elif checker == "h" and d2 <= 4.2 ** 2:
                c = 6
            elif checker in ("sw", "sk") and d2 <= 8.6 ** 2:
                c = 6 if d2 > 7.0 ** 2 else (3 if checker == "sw" else 4)
            px.append(c)
    return make_gif(BG_W, BG_H, BG_PAL, px)


def _die(n):
    pips = {1: [(1, 1)], 2: [(0, 0), (2, 2)], 3: [(0, 0), (1, 1), (2, 2)], 4: [(0, 0), (2, 0), (0, 2), (2, 2)],
            5: [(0, 0), (2, 0), (1, 1), (0, 2), (2, 2)], 6: [(0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)]}[n]
    px = []
    for y in range(22):
        for x in range(22):
            c = 3 if 1 <= x <= 20 and 1 <= y <= 20 else 5
            for gx, gy in pips:
                if (x - (5 + 6 * gx)) ** 2 + (y - (5 + 6 * gy)) ** 2 <= 4.5:
                    c = 4
            px.append(c)
    return make_gif(22, 22, BG_PAL, px)


def images():
    """Point slices and dice, under /img/bg/."""
    out = {}
    for top in (True, False):
        for colour in "rc":
            for checker in ("n", "w", "k", "h", "sw", "sk"):
                for i in range(BG_SLICES):
                    out["/img/bg/%s%s%s%d.gif" % ("t" if top else "b", colour, checker, i)] = \
                        _bg_slice(top, colour, checker, i)
    for n in range(1, 7):
        out["/img/bg/d%d.gif" % n] = _die(n)
    return out


BG_FRAME, BG_LABEL, BG_MID, BG_TRAY = 4, 12, 30, 30    # frame, point numbers, middle strip, bear-off tray
WOOD, FELT, INK, WHITE, YELLOW = BG_PAL[7], BG_PAL[0], (0, 0, 0), (255, 255, 255), BG_PAL[6]


def bg_layout(seat):
    """The points along the top and the bottom, left to right, numbered from the viewer's side (your home
    board at the bottom right; seat 0's for watchers). board_image draws them mirrored when the viewer keeps
    the home board on the left."""
    if seat == 1:
        return list(range(12, 0, -1)), list(range(13, 25))
    return list(range(13, 25)), list(range(12, 0, -1))


def pips(v, s):
    """The pip count of seat s: seat 0 moves down to point 1, seat 1 up to 24; the bar is 25 away."""
    if s == 0:
        return sum(p * n for p, n in enumerate(v["pts"], 1) if n > 0) + 25 * v["bar"][0]
    return sum((25 - p) * -n for p, n in enumerate(v["pts"], 1) if n < 0) + 25 * v["bar"][1]


def board_image(v, seat, sel):
    """Backgammon as one image: the 24 points (five slices of images() each; on a taller stack the fifth checker
    shows how many checkers it stands for, docs/design.md, Stacks of pieces), the point numbers, the bar with its checkers, a bear-off tray per side with
    the checkers borne off, the dice, the doubling cube and both pip counts. Your side is at the bottom
    with your home board on the right (seat 1's board is the mirror of seat 0's). On your turn a point or
    the bar you can move from picks a checker up (sel=), drawn yellow; with one picked, its targets show a
    yellow dot and move it there, and your tray bears it off. v["home"] "left" (the viewer's kept bghome) draws
    the mirror image: your home board and the trays on the left; the points keep their numbers."""
    my_turn = seat >= 0 and v["turn"] == seat and v["phase"] == "move"
    moves = [m.split("-") for m in v["moves"]] if my_turn else []
    froms = {m[0] for m in moves}
    targets = {m[1] for m in moves if m[0] == sel}
    top, bottom = bg_layout(seat)
    me = seat if seat in (0, 1) else 0          # whose side is at the bottom
    half_h = BG_H * BG_SLICES
    x_tray = BG_FRAME + 13 * BG_W + BG_FRAME
    w = x_tray + BG_TRAY + BG_FRAME
    flip = v.get("home") == "left"

    def mx(x, width=1):
        """Where something drawn at x, width wide, goes: the same place, or its mirror image."""
        return w - x - width if flip else x
    x_tray = mx(x_tray, BG_TRAY)
    x_felt = mx(BG_FRAME, 13 * BG_W)
    y_top = BG_LABEL + BG_FRAME
    y_mid = y_top + half_h
    y_bot = y_mid + BG_MID
    h = y_bot + half_h + BG_FRAME + BG_LABEL
    c = Picture(w, h, bg=WHITE, images=images)
    c.fill(0, BG_LABEL, w, h - 2 * BG_LABEL, WOOD)
    c.fill(x_felt, y_top, 13 * BG_W, y_bot + half_h - y_top, FELT)

    def col_x(i):
        return mx(BG_FRAME + (i + (1 if i >= 6 else 0)) * BG_W, BG_W)

    def checker(cx, cy, who, rim=BG_PAL[5]):
        c.disc(cx, cy, 8.6, rim)
        c.disc(cx, cy, 7.6, BG_PAL[3] if who == 0 else BG_PAL[4])

    def stands_for(cx, cy, who, n):
        """The number on the last checker drawn of a tall stack, centered on it in the checker's contrasting
        color: how many checkers that one stands for, itself and the ones not drawn."""
        t = str(n)
        tw, th = text_size(t)
        c.text(cx - tw // 2, cy - th // 2, t, INK if who in (0, "w") else WHITE)

    def point(p, i, is_top):
        x = col_x(i)
        n = v["pts"][p - 1]
        who, cnt = ("w" if n > 0 else "k"), abs(n)
        colour = "r" if i % 2 == 0 else "c"
        shown = min(cnt, BG_SLICES)
        for slot in range(BG_SLICES):
            if slot < shown:
                kind = ("s" + who) if str(p) == sel and slot == shown - 1 else who
            elif str(p) in targets and slot == min(cnt, BG_SLICES - 1) and cnt < BG_SLICES:
                kind = "h"
            else:
                kind = "n"
            y = y_top + slot * BG_H if is_top else y_bot + half_h - (slot + 1) * BG_H
            c.tile("/img/bg/%s%s%s%d.gif" % ("t" if is_top else "b", colour, kind, slot), x, y)
        if cnt > BG_SLICES:             # the last checker drawn stands for itself and the ones not drawn
            y = y_top + (BG_SLICES - 1) * BG_H if is_top else y_bot + half_h - BG_SLICES * BG_H
            stands_for(x + 12, y + 9, who, cnt - BG_SLICES + 1)
        if str(p) in targets and cnt >= BG_SLICES:      # a full point: the dot goes on the outermost checker
            y = y_top + (shown - 1) * BG_H if is_top else y_bot + half_h - shown * BG_H
            c.disc(x + 11.5, y + 8.5, 3.0, YELLOW)
        c.text(x + BG_W // 2, 3 if is_top else h - BG_LABEL + 3, str(p), INK, SMALL, align="center")
        box = (x, y_top, x + BG_W - 1, y_mid - 1) if is_top else (x, y_bot, x + BG_W - 1, y_bot + half_h - 1)
        if str(p) in targets:
            c.area("rect", box, "%s to %d" % (sel, p), "/move.htm", mv="%s-%d" % (sel, p))
        elif my_turn and str(p) in froms and str(p) != sel:
            c.area("rect", box, "from %d" % p, "/table.htm", sel=p)
    for i, p in enumerate(top):
        point(p, i, True)
    for i, p in enumerate(bottom):
        point(p, i, False)

    # the bar: each side's checkers on its own half, nearest the middle; three places, and past three the third
    # checker shows how many it stands for
    bar_x = mx(BG_FRAME + 6 * BG_W, BG_W)
    c.fill(bar_x, BG_LABEL, BG_W, h - 2 * BG_LABEL, WOOD)
    c.fill(x_felt, y_mid, 13 * BG_W, BG_MID, WOOD)
    for s in (0, 1):
        n = v["bar"][s]
        mine = s == me
        shown = min(n, 3)
        for k in range(shown):
            cy = (y_bot + 10 + 19 * k) if mine else (y_mid - 10 - 19 * k)
            picked = mine and sel == "bar" and k == shown - 1
            checker(bar_x + 11.5, cy, s, YELLOW if picked else BG_PAL[5])
        if n > 3:
            stands_for(bar_x + 12, (y_bot + 10 + 19 * 2) if mine else (y_mid - 10 - 19 * 2), s, n - 2)
    if my_turn and "bar" in froms and sel != "bar":
        c.area("rect", (bar_x, y_top, bar_x + BG_W - 1, y_bot + half_h - 1), "enter from the bar", "/table.htm",
               sel="bar")

    # the trays: the other side's at the top, yours at the bottom, by your home board; a slab per checker off
    c.fill(x_tray, y_top, BG_TRAY, half_h, (60, 44, 28))
    c.fill(x_tray, y_bot, BG_TRAY, half_h, (60, 44, 28))
    for s in (0, 1):
        n = v["off"][s]
        mine = s == me
        for k in range(min(n, 15)):
            y = (y_bot + half_h - 5 - 4 * k) if mine else (y_top + 1 + 4 * k)
            c.fill(x_tray + 3, y, BG_TRAY - 6, 4, BG_PAL[5])
            c.fill(x_tray + 4, y + 1, BG_TRAY - 8, 2, BG_PAL[3] if s == 0 else BG_PAL[4])
        c.text(x_tray + BG_TRAY // 2, h - BG_LABEL + 3 if mine else 3, str(n), INK, SMALL, align="center")
    if "off" in targets:
        c.frame(x_tray, y_bot, BG_TRAY, half_h, YELLOW, 2)
        c.area("rect", (x_tray, y_bot, x_tray + BG_TRAY - 1, y_bot + half_h - 1), "%s off" % sel, "/move.htm",
               mv="%s-off" % sel)

    # the middle strip: pip counts, the dice, the cube (by its owner's tray, or in the middle)
    c.text(mx(BG_FRAME + 4), y_mid + 11, "pips: white %d, black %d" % (pips(v, 0), pips(v, 1)), WHITE,
           align="right" if flip else "left")
    dice = [d for d in v["dice"][:4] if 1 <= d <= 6]
    x_dice = mx(BG_FRAME + 13 * BG_W - 4 - len(dice) * 26, len(dice) * 26 - 4)     # the dice read left to right
    for k, d in enumerate(dice):
        c.tile("/img/bg/d%d.gif" % d, x_dice + k * 26, y_mid + 4)
    cube = v["cube"]
    owner = v["cube_owner"]
    # in the middle, or at the inner end of its owner's tray (the checkers off fill the outer 60 pixels)
    cy = y_mid + 4 if owner < 0 else (y_bot + 4 if owner == me else y_mid - 26)
    cx = x_tray + (BG_TRAY - 22) // 2
    c.fill(cx, cy, 22, 22, INK)
    c.fill(cx + 1, cy + 1, 20, 20, WHITE)
    c.text(cx + 11, cy + 8, str(cube if owner >= 0 or cube > 1 else 64), INK, align="center")
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    my_turn = seat >= 0 and v["turn"] == seat
    dice = " ".join(str(d) for d in v["dice"])
    owner = "in the middle" if v["cube_owner"] < 0 else "with " + esc(names[v["cube_owner"]])
    side = ("<table border=\"0\" cellpadding=\"3\">"
            "<tr><td><b>White</b></td><td>%s</td><td>%d off</td></tr>"
            "<tr><td><b>Black</b></td><td>%s</td><td>%d off</td></tr></table>"
            "<p>Dice: %s<br>Cube: %d, %s<br>Pips: white %d, black %d</p>"
            % (esc(names[0]), v["off"][0], esc(names[1]), v["off"][1], dice or "-", v["cube"], owner,
               pips(v, 0), pips(v, 1)))
    if v.get("match"):
        side += "<p>Match to %d: %d - %d, game %d%s</p>" % (v["match"], v["score"][0], v["score"][1], v["game"],
                                                           " (Crawford)" if v["crawford"] else "")
    if v["rows"]:           # with the board: the viewer's kept side (PRF bghome), and the switch
        home = "left" if v.get("home") == "left" else "right"
        other = "right" if home == "left" else "left"
        side += '<p><font size="2">Your home board is on the %s: %s</font></p>' % (
            home, a(link(BG_HOME_PAGE, t=tid, h=other), "put it on the %s" % other))
    actions = []
    if my_turn:
        for m in v["moves"]:
            if m in ("roll", "double", "take", "drop", "accept", "reject"):
                actions.append(a(link("/move.htm", t=tid, mv=m), "<b>%s</b>" % m.capitalize()))
        if v["phase"] == "move":
            offs = [m for m in v["moves"] if m.endswith("-off") and m.split("-")[0] == sel]
            for m in offs:
                actions.append(a(link("/move.htm", t=tid, mv=m), "<b>Bear off from %s</b>" % sel))
            actions.append(a(link("/und.htm", t=tid), "Undo"))
            if not v["moves"]:
                actions.append(a(link("/end.htm", t=tid), "<b>End turn</b>"))
            steps = [m for m in v["moves"] if "-" in m]
            if steps:
                side += "<p><font size=\"2\">Moves: %s</font></p>" % " ".join(
                    a(link("/move.htm", t=tid, mv=m), esc(m)) for m in steps)
                side += ('\n<form action="/move.htm" method="get">\n%s\nOr type a move (like %s): '
                         '<input name="mv" size="7" maxlength="7"> <input type="submit" value="Move">\n</form>'
                         % (hidden_fields(link("/move.htm", t=tid)), esc(steps[0])))
    hint = ""
    if my_turn and v["phase"] == "move" and v["moves"]:
        hint = " - click a checker, then where it goes" if not sel else " - click a yellow dot (or another checker)"
    board = board_html(board_image, v, tid, link, seat, sel, "Backgammon board", text=False)
    return board, side, hint, " | ".join(actions)


# ---- telnet --------------------------------------------------------------------------
#
# The board, 47 columns by 19 rows, drawn the same way in every mode:
#
#   x  1        frame                y  1      point numbers (top row)
#      2-19     left quadrant           2      frame
#      20, 24   bar frame               3-9    top points, 7 checker slots
#      21-23    the bar                 10     the middle: dice, BAR, the cube
#      25-42    right quadrant          11-17  bottom points
#      43, 47   frame                   18     frame
#      44-46    the tray (borne off)    19     point numbers (bottom row)
#
# A cursor position is (row, column): row 0 top, 1 bottom; columns 0-5 and
# 7-12 are points, 6 the bar and 13 the tray. The player's own side is at
# the bottom with the home board on the right (seat 0's side for watchers).
# A player who keeps the home board on the left (PRF bghome, the session's
# bghome) sees the mirror image: x becomes 49 - x - width, so the tray is
# at 2-4 and the bar at 25-27. The cursor's columns stay the same and the
# left and right arrows swap, so they still move the way they point.

BG_SLOTS = 7
BG_NAMES = ("white", "black")           # seat 0, seat 1: as on the web page
A_WOOD = "0;30;43"
A_DIE = "0;30;47"
A_FELT = "0;30;42"
A_PT = ("0;31;", "0;37;")               # point colors; the felt (or cursor) background is added
A_CHK = ("0;1;37;", "0;30;")            # checkers, per seat
A_STANDS = ("0;30;47", "0;1;37;40")     # the number on a tall stack's last checker: the checker's color reversed
A_MARK = "0;1;33;"
BG_STEP_RE = re.compile(r"^(bar|b|\d{1,2})[-/](off|o|\d{1,2})\Z")
BG_WORDS = {"roll": "roll", "double": "double", "undo": "undo", "end": "end"}


def bg_glyphs(mode):
    """Points, checkers and frame for a mode. The two sides' checkers differ
    in shape, not only in color; in ASCII the two point colors differ in
    their fill too."""
    if mode in ("pc", "utf8"):
        top = ["███", "███", "▐█▌", "▐█▌", " █ ", " █ ", " ▀ "]
        bot = top[:6] + [" ▄ "]
        return {"top": (top, top), "bot": (bot, bot), "chk": ("(█)", "(▒)"), "sel": ("»█«", "»▒«"),
                "mark": "(·)", "slab": ("■", "■"), "TL": "╔", "TR": "╗", "BL": "╚", "BR": "╝", "H": "═",
                "V": "║", "T": "╦", "B": "╩"}

    def tri(f, tip, left, right):
        return [f * 3, f * 3, left + f + right, left + f + right, " %s " % f, " %s " % f, " %s " % tip]
    return {"top": (tri("#", "v", "\\", "/"), tri(":", "v", "\\", "/")),
            "bot": (tri("#", "^", "/", "\\"), tri(":", "^", "/", "\\")),
            "chk": ("(O)", "(X)"), "sel": (">O<", ">X<"), "mark": "(*)", "slab": ("o", "x"),
            "TL": "+", "TR": "+", "BL": "+", "BR": "+", "H": "=", "V": "|", "T": "+", "B": "+"}


def bg_lists(bottom):
    """The points of the top and the bottom row, left to right."""
    if bottom == 1:
        return list(range(12, 0, -1)), list(range(13, 25))
    return list(range(13, 25)), list(range(12, 0, -1))


def bg_x(c):
    return 2 + 3 * c if c < 6 else 21 if c == 6 else 25 + 3 * (c - 7) if c < 13 else 44


async def load_home(core):
    """A telnet player's kept home board side (PRF bghome, telnet's own): "right" (the default) or "left"."""
    load = getattr(core, "load_pref", None)
    return await load(BG_HOME_PREF, BG_HOMES, BG_HOMES[0]) if load else BG_HOMES[0]


AUTO_SECS = 5       # the countdowns: the roll, and sending a finished move, come this many seconds later


def seconds(n):
    return "1 second" if n == 1 else "%d seconds" % n


class BgBase(GameBase):
    """Backgammon, shared by the full-screen and the plain screen.

    Two countdowns save keys, on this side only (the server's turn clock is not touched). At the start of
    your turn the dice are rolled for you after AUTO_SECS, and once nothing is left to play (every die
    used, or no legal step left) your move is sent after AUTO_SECS. Any key stops a countdown, and Esc
    (Enter in plain mode, whose lines come in at Enter) does nothing else, so the turn waits as before.
    A countdown that was stopped, or that ran, does not start again until the turn moves on."""

    HELP_MOVING = ["Press Enter to roll the dice. The arrows move over the points, the",
                   "bar and the tray. Enter picks up a checker, Enter on a marked point",
                   "moves it there and Esc puts it back. Enter ends a finished turn."]
    HELP_KEYS = ["D offers to double the stakes, before you roll.",
                 "H moves your home board to the other side.",
                 "U takes back your last step, and E ends your turn."]
    HELP_COMMANDS = ["Press / to type one. /13-9 moves a checker from 13 to 9. /roll,",
                     "/double, /undo, /end and /home left or /home right do what they say."]
    HELP_REFERENCE = ["The points",
                      "Your points count from 1 in your home board up to 24. The bar is",
                      "in the middle, and the tray holds the checkers you bear off once",
                      "all of yours are home. The pip count says how far they still go.",
                      "A * beside a point's number marks where the other checkers went.",
                      "",
                      "The cube",
                      "Doubling offers to play on for twice the stakes. The other player",
                      "accepts and takes the cube, or declines and loses the game. You",
                      "may double before you roll, while the cube is yours or free."]

    BACKLOG = 24
    GAME = NAME
    MOVE_EXAMPLE = "/13-9"
    COMMANDS = {"roll": "/roll rolls the dice.", "double": "/double offers to double the stakes.",
                "undo": "/undo takes back your last step.", "end": "/end ends your turn.",
                "home": "/home left or /home right draws your home board on that side."}
    now = staticmethod(time.monotonic)      # the countdowns' clock (tests set their own)

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.rolled = None          # (seat, die, die) from the latest ROLL event
        self.bg = bg_glyphs(sess.mode)
        self.auto = None            # a running countdown: ("roll" or "end", when it acts)
        self.auto_held = None       # the countdown stopped (or run) for this part of the turn

    # ---- the countdowns -----------------------------------------------------------
    def auto_kind(self):
        """What a countdown would do now: "roll" at the start of your turn, "end" once nothing is left to
        play, or None (not your turn, or the game is over)."""
        if self.over or not (self.v and self.v.get("rows")):
            return None
        ph = self.phase()
        if ph == "roll" and "roll" in self.v["moves"]:
            return "roll"
        if ph == "move" and not self.steps():
            return "end"
        return None

    def paused(self):
        """No countdown runs while something else is open (the full screen's typed line or a box)."""
        return False

    def auto_secs(self):
        """The whole seconds the countdown still shows."""
        return max(0, int(-(-(self.auto[1] - self.now()) // 1)))

    def tick_in(self):
        if not self.auto:
            return None
        left = self.auto[1] - self.now()
        return max(0.05, left % 1 or 1.0) if left > 0 else 0.05

    async def tick(self):
        kind = self.auto_kind()
        if kind is None:
            self.auto = self.auto_held = None
            return
        if kind == self.auto_held or self.paused():
            self.auto = None
            return
        if not self.auto or self.auto[0] != kind:
            self.auto = (kind, self.now() + AUTO_SECS)
            return await self.auto_started()
        if self.now() >= self.auto[1]:
            self.auto, self.auto_held = None, kind
            await self.auto_run(kind)

    async def auto_started(self):
        return None

    async def auto_run(self, kind):
        raise NotImplementedError

    async def key_seen(self, k):
        """A key stops a running countdown. Esc only stops it (in plain mode Enter, as lines come at Enter)."""
        if not self.auto:
            return False
        kind, self.auto = self.auto[0], None
        self.auto_held = kind
        if k in self.STOP_KEYS:
            await self.auto_stopped(kind)
            return True
        return False

    async def auto_stopped(self, kind):
        return None

    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    def on_event(self, text):
        p = text.split()
        if p[0] == "ROLL" and len(p) == 4:
            self.rolled = (int(p[1]), int(p[2]), int(p[3]))

    moved_to = ()           # the points the other player's checkers moved to in their last turn
    turn_says = None        # [seat, the roll's words, the steps] of the turn being told

    def last_move(self, text):
        p = text.split()
        k = p[0]
        if k == "ROLL" and len(p) == 4:
            self.turn_says = [int(p[1]), "rolled %s and %s" % (p[2], p[3]), []]
            if int(p[1]) != self.me:
                self.moved_to = set()
        elif k == "STEP" and len(p) >= 4 and self.turn_says and self.turn_says[0] == int(p[1]):
            self.turn_says[2].append("%s-%s" % (p[2], p[3]))
            if int(p[1]) != self.me:
                self.moved_to = set(self.moved_to) | {p[3]}
        elif k == "UNDO" and len(p) >= 4 and self.turn_says and "%s-%s" % (p[2], p[3]) in self.turn_says[2]:
            self.turn_says[2].remove("%s-%s" % (p[2], p[3]))
        elif k == "DBL" and len(p) >= 3:
            return "%s doubled to %s." % (self.actor(p[1]), p[2])
        elif k == "TAKE" and len(p) >= 2:
            return "%s accepted the double." % self.actor(p[1])
        elif k == "DROP" and len(p) >= 2:
            return "%s declined the double." % self.actor(p[1])
        else:
            return None
        if not self.turn_says:
            return None
        seat, roll, steps = self.turn_says
        moved = " and moved %s" % and_list(steps) if steps else ""
        return "%s %s%s." % (self.actor(seat), roll, moved)

    async def refresh(self):
        if getattr(self.s, "bghome", None) is None:
            self.s.bghome = await load_home(self.core)
        return await super().refresh()

    # ---- the home board's side ----------------------------------------------
    def flipped(self):
        """True when the player keeps the home board on the left: the board is drawn mirrored."""
        return getattr(self.s, "bghome", None) == "left"

    def mx(self, x, width=3):
        """The column something width wide drawn at x goes to: the same, or its mirror image."""
        return 49 - x - width if self.flipped() else x

    async def set_home(self, side):
        """Draw the home board on side ("right" or "left") from now on, and keep it for the next visit: True if
        kept (False if the core refused; it still applies now)."""
        self.s.bghome = side
        save = getattr(self.core, "save_pref", None)
        return bool(save) and await save(BG_HOME_PREF, side)

    def home_text(self, kept=False):
        side = "left" if self.flipped() else "right"
        return "Your home board is on the %s%s." % (side, ", kept for next time" if kept else "")

    async def home(self, side):
        """The home command: side "right", "left", or "" (the full screen asks; plain says how)."""
        raise NotImplementedError

    async def game_command(self, cmd, args):
        if cmd == "home" and len(args) <= 1 and (not args or args[0].lower() in BG_HOMES):
            await self.home(args[0].lower() if args else "")
            return True
        return False

    # ---- state ------------------------------------------------------------
    def playing(self):
        return bool(self.v and self.v["rows"]) and not self.over and self.v["phase"] not in ("over", "")

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    def phase(self):
        """The phase when it is this player's turn, else ''."""
        return self.v["phase"] if self.my_turn() else ""

    def steps(self):
        return [m for m in self.v["moves"] if "-" in m] if self.phase() == "move" else []

    def stepped(self):
        """True once a step of this turn is played, so that U has one to take back."""
        if not self.rolled or self.rolled[0] != self.me:
            return bool(self.v["dice"]) is False and self.phase() == "move"
        d1, d2 = self.rolled[1:]
        return len(self.v["dice"]) < (4 if d1 == d2 else 2)

    def froms(self):
        out = []
        for m in self.steps():
            f = m.split("-")[0]
            if f not in out:
                out.append(f)
        return out

    def targets(self, frm):
        """Where a checker from frm can go, nearest first."""
        start = (25 if self.me == 0 else 0) if frm == "bar" else int(frm)
        t = [m.split("-")[1] for m in self.steps() if m.split("-")[0] == frm]
        return sorted(t, key=lambda x: 99 if x == "off" else abs(int(x) - start))

    def bottom(self):
        return 1 if self.me == 1 else 0

    def pips(self, seat):
        pts = self.v["pts"]
        if seat == 0:
            return sum(p * n for p, n in enumerate(pts, 1) if n > 0) + 25 * self.v["bar"][0]
        return sum((25 - p) * -n for p, n in enumerate(pts, 1) if n < 0) + 25 * self.v["bar"][1]

    def tok_at(self, rc):
        r, c = rc
        if c == 6:
            return "bar"
        if c == 13:
            return "off"
        return str(bg_lists(self.bottom())[r][c if c < 6 else c - 1])

    def pos_of(self, tok):
        if tok == "bar":
            return 1, 6
        if tok == "off":
            return 1, 13
        top, bot = bg_lists(self.bottom())
        p = int(tok)
        r = 0 if p in top else 1
        i = (top, bot)[r].index(p)
        return r, i if i < 6 else i + 1

    # ---- the board ----------------------------------------------------------
    def stands_for(self, cv, x, y, side, n):
        """A tall stack's last checker drawn carries how many checkers it stands for, itself and the ones not
        drawn (docs/design.md, Stacks of pieces): the number in the checker's middle, in its color reversed.
        The middle holds one digit: + stands for 10 or more, which 15 checkers in BG_SLOTS places never need."""
        cv.put(x + 1, y, str(n) if n < 10 else "+", A_STANDS[side])

    def draw_bg(self, cv, cur=None, sel=None):
        """The board into cv; with cur (a cursor position) and sel (the
        point of a picked-up checker), the player's highlights too."""
        G, v = self.bg, self.v
        bottom = self.bottom()
        topseat = 1 - bottom
        curtok = self.tok_at(cur) if cur else None
        froms = self.froms() if cur else []
        marks = self.targets(sel) if sel else []
        mx = self.mx
        seg, bar = G["H"] * 18, G["H"] * 3

        def edge(left, joint, right):
            inner = [seg, joint, bar, joint, seg, joint, bar]
            return left + "".join(inner[::-1] if self.flipped() else inner) + right
        cv.put(1, 2, edge(G["TL"], G["T"], G["TR"]), A_WOOD)
        cv.put(1, 18, edge(G["BL"], G["B"], G["BR"]), A_WOOD)
        bar_x, off_x = mx(21), mx(44)
        for y in range(3, 18):
            for x in (1, 20, 24, 43, 47):
                cv.put(mx(x, 1), y, G["V"], A_WOOD)
            cv.put(mx(2, 18), y, " " * 18, A_FELT)
            cv.put(mx(25, 18), y, " " * 18, A_FELT)
        for r, points in enumerate(bg_lists(bottom)):
            for i, p in enumerate(points):
                c = i if i < 6 else i + 1
                x, tok = mx(bg_x(c)), str(p)
                bg = "46" if tok == curtok else "42"
                n = v["pts"][p - 1]
                cnt, side = abs(n), (0 if n > 0 else 1)
                shown = min(cnt, BG_SLOTS)
                kind = (i + r) % 2
                tri = G["top" if r == 0 else "bot"][kind]
                for s in range(BG_SLOTS):
                    y = 3 + s if r == 0 else 17 - s
                    if s < shown:
                        picked = tok == sel and s == shown - 1
                        cv.put(x, y, G["sel" if picked else "chk"][side], A_CHK[side] + ("43" if picked else bg))
                        if s == shown - 1 and cnt > BG_SLOTS:
                            self.stands_for(cv, x, y, side, cnt - BG_SLOTS + 1)
                    elif s == cnt and tok in marks:
                        cv.put(x, y, G["mark"], A_MARK + bg)
                    else:
                        cv.put(x, y, tri[s], A_PT[kind] + bg)
                moved = tok in self.moved_to         # where the other player's checkers went last turn
                lattr = A_SEL if tok == curtok else A_YELLOW if tok in marks or moved else \
                    A_BRIGHT if tok in froms else A_AXIS
                cv.put(x, 1 if r == 0 else 19, (tok.rjust(2) + "*" if moved else tok.rjust(2).ljust(3)), lattr)
                cv.hot_area(x, 1 if r == 0 else 11, 3, 9, ("pt", r, c))
        for side in (topseat, bottom):
            r = 0 if side == topseat else 1
            here = cur is not None and cur[0] == r
            bar_bg = "46" if here and curtok == "bar" else "43"
            off_bg = "46" if here and curtok == "off" else "43"
            for y in (range(3, 10) if r == 0 else range(11, 18)):
                cv.put(bar_x, y, "   ", "0;30;" + bar_bg)
                cv.put(off_x, y, "   ", "0;30;" + off_bg)
            n = v["bar"][side]
            shown = min(n, BG_SLOTS)
            for s in range(shown):
                y = 9 - s if r == 0 else 11 + s
                picked = sel == "bar" and side == self.me and s == shown - 1
                cv.put(bar_x, y, G["sel" if picked else "chk"][side], A_CHK[side] + bar_bg)
                if s == shown - 1 and n > BG_SLOTS:
                    self.stands_for(cv, bar_x, y, side, n - BG_SLOTS + 1)
            n = v["off"][side]
            rows = (n + 2) // 3
            for i in range(rows):
                cv.put(off_x, 3 + i if r == 0 else 17 - i, (G["slab"][side] * min(3, n - 3 * i)).ljust(3),
                       A_CHK[side] + off_bg)
            target = "off" in marks and side == self.me
            if target:
                cv.put(off_x, 3 + rows if r == 0 else 17 - rows, G["mark"], A_MARK + off_bg)
            lattr = A_SEL if here and curtok == "off" else A_YELLOW if target else A_AXIS
            cv.put(off_x, 1 if r == 0 else 19, "off", lattr)
            cv.hot_area(bar_x, 1 if r == 0 else 11, 3, 9, ("pt", r, 6))
            cv.hot_area(off_x, 1 if r == 0 else 11, 3, 9, ("pt", r, 13))
        cv.put(bar_x, 10, "BAR", A_SEL if curtok == "bar" else A_WOOD)
        cv.put(off_x, 10, "   ", A_WOOD)
        if v["rows"]:
            owner = v["cube_owner"]
            cv.put(off_x, 10 if owner < 0 else 9 if owner == topseat else 11, str(v["cube"]).center(3), A_DIE)
        qx = mx(25 if v["turn"] == bottom else 2, 18)   # the dice lie on the mover's right (mirrored: left)
        if v["dice"] and v["phase"] == "move" and not self.over:
            x0 = qx + (18 - (4 * len(v["dice"]) - 1)) // 2
            for j, d in enumerate(v["dice"]):
                cv.put(x0 + 4 * j, 10, "[%d]" % d, A_DIE)
        elif v["phase"] == "take" and not self.over:
            cv.put(qx + 2, 10, ("Doubled to %d" % (v["cube"] * 2)).center(14), A_MARK + "42")

    # ---- game words ---------------------------------------------------------
    async def act(self, word):
        """roll, double, take, drop, undo, end or a step -> an error text or None."""
        if not self.my_turn():
            return self.not_yours()
        if word == "undo":
            return await self.staged("UND", "take that back")
        if word == "end":
            return await self.staged("END", "end the turn")
        if word == "drop" and self.v["phase"] == "take" and not await self.ask_drop():
            return None
        return await self.move(word)

    def offer(self):
        """A double or an offer to resign, waiting for this player's answer (docs/design.md, Dialogs)."""
        v = self.v
        ph = self.phase() if v and v.get("rows") else ""
        if ph == "take":
            cube = v["cube"]
            by = self.who(self.name(1 - self.me))
            return (("take", cube), "Double", [
                "%s doubles to %d." % (by, cube * 2),
                "If you accept, the game goes on for %d points." % (cube * 2),
                "If you decline, you lose %d point%s and the game." % (cube, "" if cube == 1 else "s")],
                True, "take", "drop", "%s doubles to %d. Press Enter to answer." % (by, cube * 2))
        if ph == "resign" and v.get("offer"):
            s, pts, kind = v["offer"]
            by = self.who(self.name(s))
            what = "%s offers to resign%s." % (by, "" if kind in ("single", "") else " a " + kind)
            return (("resign", s, pts, kind), "Offer to resign", [
                what, "If you accept, you win %d point%s and the game." % (pts, "" if pts == 1 else "s"),
                "If you decline, the game goes on."], False, "accept", "reject", what[:-1] + ". Press Enter to answer.")
        return None

    async def ask_drop(self):
        """Every way of dropping a double asks first: True when the player says yes."""
        cube = self.v["cube"]
        return await self.ask_yes("Drop", ["Drop the double? You lose %d point%s and the game." % (
            cube, "" if cube == 1 else "s")], "drop it", "Drop")

    @staticmethod
    def game_word(cmd):
        """A typed word -> the move or staged word it stands for, or None."""
        if cmd in BG_WORDS:
            return BG_WORDS[cmd]
        m = BG_STEP_RE.match(cmd)
        if m:
            a, b = m.groups()
            return "%s-%s" % ("bar" if a == "b" else a, "off" if b == "o" else b)
        return None

    def move_word(self, cmd, words=()):
        return self.game_word(cmd)

    async def play_word(self, word):
        return await self.act(word)


class AnsiBackgammon(BgBase, AnsiGame):
    """Backgammon, full screen: the board on the left (the cursor, a
    picked-up checker and where it can go highlighted), players, dice,
    cube, clock and log on the right."""

    HOTKEYS = "udehrt?"
    LOG_TOP, LOG_ROWS = 12, 7

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = (1, 12)
        self.sel = None

    def snap(self, stepped=False):
        """When the move phase starts (or after a step), put the cursor on a
        checker that can move; forget a pick that can no longer move."""
        froms = self.froms()
        if self.sel and self.sel not in froms:
            self.sel = None
        if froms and (not self.was_my_turn or stepped) and self.tok_at(self.cur) not in froms:
            self.sel = None
            back = sorted(froms, key=lambda f: -999 if f == "bar" else -int(f) if self.me == 0 else int(f))
            self.cur = self.pos_of(back[0])         # the back checkers first
        self.was_my_turn = bool(froms)

    # ---- drawing ------------------------------------------------------------
    def compose(self):
        cv = Canvas()
        g = self.g
        title_bar(cv, g, self.title_text())
        showcur = self.me >= 0 and self.playing() and self.lit()
        self.draw_bg(cv, self.cur if showcur else None, self.sel)
        if not self.v["rows"]:
            self.draw_waiting(cv, 11, 7)
        self.draw_panel(cv)
        steps = self.steps()
        if steps:
            line = "You can play %s." % ", ".join(steps)
            cv.put(1, 20, line if len(line) <= COLS - 2 else line[:COLS - 5] + "...", A_DIM)
        text, attr = (self.auto_text(), A_YELLOW) if self.auto and not self.buf else self.sentence(self.hint)
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        ph = self.phase()
        home = [("h", "home", "home")] if self.me >= 0 else []
        if ph == "roll":
            keys = self.keys("look", "roll", ([("d", "double", "double")] if "double" in self.v["moves"] else [])
                             + home)
        elif ph in ("take", "resign"):
            keys = self.keys("look", "answer")
        elif ph == "move" and steps:
            undo = [("u", "undo", "undo")] if self.stepped() else []
            keys = self.keys("move", "place" if self.sel else "pick", undo + home, cancel=bool(self.sel))
        elif ph == "move":
            keys = self.keys("look", "end turn", [("u", "undo", "undo"), ("e", "end turn", "end")] + home)
        elif self.me >= 0 and self.playing():
            keys = self.keys("look", None, home)
        else:
            keys = self.keys()
        self.keys_line(cv, keys)
        return cv

    def draw_panel(self, cv):
        v = self.v
        players = [(seat, (self.bg["chk"][seat], A_CHK[seat] + "42"), "pips %d" % self.pips(seat))
                   for seat in (1 - self.bottom(), self.bottom())]
        facts = []
        if v["rows"] and not self.over:
            if v["dice"]:
                facts.append("The dice to play are %s." % and_list(str(d) for d in v["dice"]))
            if v["cube_owner"] < 0:
                facts.append("The cube is at %d, in the middle." % v["cube"])
            else:
                facts.append("The cube is at %d, and %s." % (
                    v["cube"], "it is yours" if v["cube_owner"] == self.me else
                    "%s has it" % self.who(self.name(v["cube_owner"]))))
            for seat in (self.bottom(), 1 - self.bottom()):
                bar, off = v["bar"][seat], v["off"][seat]
                if bar or off:
                    facts.append("%s %s %s." % (self.actor(seat), "have" if seat == self.me else "has", and_list(
                        (["%d on the bar" % bar] if bar else []) + (["%d borne off" % off] if off else []))))
        self.draw_panel_parts(cv, 49, 1, COLS - 50, players, facts)

    def hint(self):
        v = self.v
        if not v["rows"]:
            return self.waiting_hint()
        if self.over or v["phase"] == "over":
            return self.over_hint()
        if self.me < 0:
            return self.watch_hint()
        ph = self.phase()
        if not ph:
            if v["phase"] == "take" and v["turn"] != self.me:
                return "You offered to double to %d. Waiting for %s." % (
                    v["cube"] * 2, self.who(self.name(v["turn"]))), A_DIM
            return self.their_hint(v["turn"])
        if ph == "roll":
            if "double" in v["moves"]:
                return "Your turn. Press Enter to roll the dice, or %s to double to %d." % (
                    self.kn("d", "double"), v["cube"] * 2), A_TEXT
            return "Your turn. Press Enter to roll the dice.", A_TEXT
        if ph in ("take", "resign"):
            o = self.offer()
            return (o[6] if o else "Your turn."), A_YELLOW
        if not self.steps():
            return "Your dice are played. Press Enter to end your turn, or %s to undo a step." % self.kn("u"), A_TEXT
        tok = self.tok_at(self.cur)
        if self.sel:
            return "Press Enter on a marked point to move there, or Esc to put it back.", A_TEXT
        rolled = "You rolled %d and %d. " % self.rolled[1:] if self.rolled and self.rolled[0] == self.me else ""
        if tok in self.froms():
            return "%sPress Enter to pick up the checker on %s." % (rolled or "Your turn. ", tok), A_TEXT
        return "%sYou can move a checker from %s." % (rolled or "Your turn. ", or_list(self.froms())), A_DIM

    # ---- the countdowns (BgBase) ----------------------------------------------------
    STOP_KEYS = ("ESC",)

    def auto_text(self):
        n = seconds(self.auto_secs())
        if self.auto[0] == "end":
            return "Sending your move in %s. Press Esc to change it." % n
        if "double" in self.v["moves"]:
            return "Rolling in %s. Press Esc to double or wait." % n
        return "Rolling in %s. Press Esc to wait." % n

    def paused(self):
        return bool(self.buf) or self.chatting or self.overlay is not None

    async def auto_run(self, kind):
        self.sel = None
        await self.play(kind)

    async def auto_stopped(self, kind):
        if kind == "roll":
            return self.say("The dice wait for you. Press Enter when you want to roll.", A_TEXT)
        return self.say("Your move waits. Press Enter to send it, or %s to undo a step." % self.kn("u"), A_TEXT)

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            r, c = self.cur
            return self.mx(bg_x(c)) + 1, 1 if r == 0 else 19
        return None

    # ---- keys ---------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "pt" and self.me >= 0 and self.playing():
            self.cur = (value[1], value[2])
            if self.steps():
                return await self.pick()
        return None

    def on_arrow(self, k):
        if self.v["rows"]:
            r, c = self.cur
            if k in ("UP", "DOWN"):
                r = 1 - r
            else:
                c = (c + (1 if (k == "RIGHT") != self.flipped() else -1)) % 14
            self.cur = (r, c)

    def on_escape(self):
        if self.sel:
            self.sel = None
            return None
        return "menu"

    async def on_enter(self):
        ph = self.phase()
        if not ph:
            return self.say(self.not_yours())
        if ph == "roll":
            return await self.play("roll")
        if not self.steps():
            return await self.play("end")
        return await self.pick()

    async def pick(self):
        """Enter (or a click) on the cursor point: pick up a checker there,
        or put the picked-up one down on it."""
        tok = self.tok_at(self.cur)
        if self.sel and tok in self.targets(self.sel):
            step, self.sel = "%s-%s" % (self.sel, tok), None
            return await self.play(step)
        if tok == self.sel:
            self.sel = None
            return None
        if tok in self.froms():
            self.sel = tok
            self.cur = self.pos_of(self.targets(tok)[0])     # on to the nearest target
            return None
        if self.sel:
            return self.say("The checker from %s can't go to %s." % (self.sel, tok), A_RED)
        return self.say("No checker of yours can move from %s now." % tok, A_RED)

    async def play(self, word):
        err = await self.act(word)
        if err:
            self.say(err, A_RED)
        self.snap(stepped=True)

    def typed_reset(self):
        self.sel = None

    async def home(self, side):
        """H or the home command: with no side, ask which; then draw it so and keep it."""
        if not side:
            cur = 1 if self.flipped() else 0
            i = await self.choose("Home board", ["Which side your home board (points 1-6)",
                                                 "is drawn on. Points keep their numbers",
                                                 "and moves are typed the same either way."],
                                  ["On the right (points 1-6 bottom right)", "On the left (the mirror image)"], cur)
            if i is None:
                return None
            side = BG_HOMES[i]
        return self.say(self.home_text(await self.set_home(side)), A_YELLOW)

    async def hotkey(self, k):
        ph = self.phase()
        if k == "h":
            return await self.home("left" if not self.flipped() else "right")
        if k in ("r", "t", "?"):
            return await super().hotkey(k)
        if k == "u":
            if ph != "move":
                return self.say("There is no step of yours to take back now.")
            self.sel = None
            return await self.play("undo")
        if k == "e":
            if ph != "move":
                return self.say(self.not_yours() if not self.my_turn() else "You haven't rolled yet.")
            if self.steps():
                return self.say("You still have steps to play, such as %s." % self.steps()[0])
            return await self.play("end")
        if k == "d":
            if ph != "roll" or "double" not in self.v["moves"]:
                return self.say("You can double at the start of your turn, before you roll, while the cube is "
                                "yours or in the middle.")
            return await self.play("double")
        return None


class PlainBackgammon(BgBase, PlainGame):
    """Backgammon for plain terminals: the board in ASCII, typed moves."""

    BOARD_KINDS = ("ROLL", "STEP", "UNDO", "DBL", "TAKE", "DROP", "PASS", "TURN", "JOIN", "LEAVE", "START", "END")

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.bg = bg_glyphs("plain")

    async def home(self, side):
        """/home left or /home right draws the board so and keeps it; /home alone says which."""
        if not side:
            return self.tell(" %s Type /home left or /home right to change it." % self.home_text())
        kept = await self.set_home(side)
        self.io.write(" %s\n" % self.home_text(kept))
        return await self.draw()

    def lines(self):
        v, G = self.v, self.bg
        out = [""] + self.plain_header()
        if not v["rows"]:
            return out + self.plain_waiting()
        cv = Canvas()
        self.draw_bg(cv)
        facts = []
        if not self.over:
            if v["dice"]:
                facts.append("The dice to play are %s." % and_list(str(d) for d in v["dice"]))
            facts.append("The cube is at %d, %s." % (v["cube"], "in the middle" if v["cube_owner"] < 0 else
                                                       "and it is yours" if v["cube_owner"] == self.me else
                                                       "and %s has it" % self.who(self.name(v["cube_owner"]))))
        side = self.side_text([(seat, G["chk"][seat], "pips %d" % self.pips(seat))
                               for seat in (1 - self.bottom(), self.bottom())], facts, COLS - 50)
        for i, l in enumerate(side):
            cv.put(49, 2 + i, l[:COLS - 50])
        out += cv.lines()[1:20]
        if self.steps():
            out += [" " + l for l in textwrap.wrap("You can play %s." % ", ".join(self.steps()), 78)]
        return out + self.plain_log()

    # ---- the countdowns (BgBase): one line, no redraw ---------------------------------
    STOP_KEYS = ("ENTER", "ESC")

    async def auto_started(self):
        if self.auto[0] == "end":
            text = "Sending your move in %s. Press Enter to change it." % seconds(AUTO_SECS)
        elif "double" in self.v["moves"]:
            text = "Rolling in %s. Type /double, or press Enter to wait." % seconds(AUTO_SECS)
        else:
            text = "Rolling in %s. Press Enter to wait." % seconds(AUTO_SECS)
        await self.notice(" " + text)

    async def auto_run(self, kind):
        err = await self.act(kind)
        await (self.notice(err) if err else self.draw())

    async def auto_stopped(self, kind):
        await self.notice(" Type /roll when you want to roll." if kind == "roll" else
                          " Type /end to send your move, or /undo to take a step back.")

    def turn_prompt(self):
        ph = self.phase()
        if ph == "roll":
            return "Your turn. Type /roll%s." % (", or /double to double to %d" % (self.v["cube"] * 2)
                                                 if "double" in self.v["moves"] else "")
        if ph == "move" and self.steps():
            return "Your turn. Type a step such as /%s." % self.steps()[0]
        if ph == "move":
            return "Your dice are played. Type /end, or /undo."
        return "Your turn."

AnsiScreen, PlainScreen = AnsiBackgammon, PlainBackgammon
