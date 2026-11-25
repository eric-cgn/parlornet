"""Checkers (checkers/1, docs/types/checkers-1.md): the web board and the telnet screens.

A staged game: a turn is one step (11-15) or a line of jumps (15x22, then
22x31), each sent with MOV; UND takes a step back and END finishes the turn.
draw offers a draw, and accept or reject answers one. The 32 dark squares
are numbered 1-32 in the standard way; each player sees the board from
their own side (Black, seat 0, has it turned round), the numbers stay.
"""
import re

from ..telnet import (A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_TEXT, A_YELLOW, COLS, ROWS, AnsiGame, Canvas,
                      GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar, title_bar, turn_text, wrap)
from ..telnet import box, describe_event as describe_common
from ..board import Canvas as Picture
from ..web import a, board_html, esc, make_gif

# ---- what the game is ------------------------------------------------------------

KEY = "checkers/1"
NAME = "Checkers"
ORDER = 8
PLAYERS = "2"
ABOUT = (
    "Two players move twelve pieces each diagonally across the dark squares of an 8 by 8 board, capturing "
    "by jumping over an opponent's piece. A piece that reaches the far side is crowned king and may then "
    "move backwards too. A player who cannot move, or has no pieces left, loses.",
    ["American rules (English draughts): Black moves first; men move one square diagonally forward and "
     "kings one square in any diagonal direction. Captures are compulsory, and a piece that can jump again "
     "must go on jumping, though a player may choose which line of captures to take. A man that reaches "
     "the far row is crowned and its turn ends there.",
     "A turn is played a step at a time, and each step can be taken back until the turn is ended. The dark "
     "squares are numbered 1 to 32 in the standard way, so moves read 11-15 or 15x22.",
     "Draws: by agreement (either player may offer one), or when 40 moves each go by with no capture and "
     "no man moving (a table can set another limit over the native protocol).",
     "Time per turn: chosen when a table is started (from 5 minutes to a week on the web and telnet)."])

SIDES = ("Black", "White")              # seat 0 moves first


# ---- squares (standard numbering: 1-4 on the top row, seen from White's side) --------------

def rc(s, n=8):
    """Square number -> (row, column), row 0 at the top."""
    per = n // 2
    r = (s - 1) // per
    return r, 2 * ((s - 1) % per) + (1 if r % 2 == 0 else 0)


def sq(r, c, n=8):
    """(row, column) -> square number, 0 for a light square or off the board."""
    if 0 <= r < n and 0 <= c < n and (r + c) % 2 == 1:
        return r * (n // 2) + c // 2 + 1
    return 0


def owner(ch):
    return 0 if ch in "xX" else 1 if ch in "oO" else -1


def steps_of(moves):
    """M tokens -> [(from, to, token)] for the steps among them (not accept, reject)."""
    out = []
    for m in moves:
        p = re.split(r"[-x]", m)
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            out.append((int(p[0]), int(p[1]), m))
    return out


# ---- the view and the events ---------------------------------------------------------

def parse_view(lines):
    """VUE lines for checkers/1 -> dict. rows (the board as n strings, a space on light squares) is empty
    until the game starts."""
    v = {"seq": 0, "n": 8, "cells": "", "rows": [], "turn": -1, "moves": [], "status": "", "clock": None,
         "phase": "", "path": [], "offer": None, "counts": (0, 0, 0, 0), "quiet": 0, "limit": 0}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) > 2:
            v["seq"] = int(f[2])
        elif tag == "BRD" and len(f) > 1:
            v["n"] = int(f[1])
        elif tag == "B" and len(f) == 2:
            v["cells"] = f[1]
        elif tag == "C" and len(f) == 5:
            v["counts"] = tuple(int(x) for x in f[1:])
        elif tag == "W" and len(f) == 2:
            v["turn"] = -1 if f[1] == "-" else int(f[1])
        elif tag == "Q" and len(f) == 2:
            v["phase"] = f[1]
        elif tag == "J":
            v["path"] = [int(x) for x in f[1:]]
        elif tag == "D" and len(f) == 2:
            v["offer"] = int(f[1])
        elif tag == "L" and len(f) == 3:
            v["quiet"], v["limit"] = int(f[1]), int(f[2])
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) == 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    cells = v["cells"]
    if cells:
        n = v["n"] if v["n"] * v["n"] // 2 == len(cells) else int(round((2 * len(cells)) ** 0.5))
        v["n"] = n
        v["rows"] = ["".join(cells[sq(r, c, n) - 1] if sq(r, c, n) else " " for c in range(n)) for r in range(n)]
    return v


def describe_event(text, names):
    """Checkers events -> a line for the log (None for the shared ones)."""
    p = text.split()

    def nm(i):
        i = int(i)
        return names[i] if 0 <= i < len(names) and names[i] != "-" else SIDES[i] if i in (0, 1) else "seat %d" % i
    k = p[0]
    if k == "STEP" and len(p) == 4:
        return "%s moves %s-%s" % (nm(p[1]), p[2], p[3])
    if k == "JUMP" and len(p) == 5:
        return "%s jumps %sx%s, taking %s" % (nm(p[1]), p[2], p[3], p[4])
    if k == "CROWN" and len(p) == 3:
        return "%s is crowned on %s" % (nm(p[1]), p[2])
    if k == "UNDO" and len(p) >= 5:
        return "%s takes back %s%s%s" % (nm(p[1]), p[2], "x" if len(p) > 5 else "-", p[3])
    if k == "DRAW" and len(p) == 2:
        return "%s offers a draw" % nm(p[1])
    if k == "ACCEPT" and len(p) == 2:
        return "%s accepts the draw" % nm(p[1])
    if k == "REJECT" and len(p) == 2:
        return "%s declines the draw" % nm(p[1])
    if k == "END" and len(p) == 4 and p[1] == "win" and p[3] in ("captured", "blocked"):
        loser = 1 - int(p[2])
        return "Game over: %s wins (%s %s)" % (nm(p[2]), nm(loser), "has no pieces left" if p[3] == "captured"
                                               else "cannot move")
    if k == "END" and len(p) == 3 and p[1] == "draw":
        if p[2] == "agreed":
            return "Game over: a draw by agreement"
        if p[2] == "limit":
            return "Game over: a draw (no capture or man move within the move limit)"
    return None


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^([0-9]{1,2}[-x][0-9]{1,2}|draw|accept|reject)\Z")
SEL_RE = re.compile(r"^[0-9]{1,2}\Z")       # the square of a picked piece

SQ = 32
PAL = [(226, 204, 158), (52, 112, 64), (24, 24, 24), (244, 240, 226), (128, 128, 120), (255, 214, 0),
       (180, 220, 186), (96, 64, 40)]
# 0 light square, 1 dark square, 2 black piece, 3 white piece, 4 rim, 5 yellow, 6 square numbers, 7 crown edge

DIGITS = {"0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
          "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
          "8": "111101111101111", "9": "111101111001111"}
CROWN = ["..#....#....#..", "..##..###..##..", "..###########..", "...#########...", "...#########..."]


def _square(number=0, piece="", mark=""):
    """A 32x32 square. number: drawn small in the corner of an empty dark square (0 for none); piece: 'bm',
    'bk', 'wm' or 'wk' (black or white, man or king); mark: 't' a target dot, 'c' a piece that can move
    (a yellow rim), 's' the picked piece (on yellow), or ''. Light squares are _square(-1)."""
    px = [0 if number < 0 else 5 if mark == "s" else 1] * (SQ * SQ)

    def dot(x, y, c):
        px[y * SQ + x] = c
    if number > 0:
        for i, ch in enumerate(str(number)):
            for j, bit in enumerate(DIGITS[ch]):
                if bit == "1":
                    dot(2 + 4 * i + j % 3, 2 + j // 3, 6)
    for y in range(SQ):
        for x in range(SQ):
            d2 = (x - 15.5) ** 2 + (y - 15.5) ** 2
            if piece:
                fill = 2 if piece[0] == "b" else 3
                if d2 <= 11.5 ** 2:
                    dot(x, y, 4 if 7.5 ** 2 <= d2 <= 8.5 ** 2 and piece[1] == "m" else fill)
                elif d2 <= 12.8 ** 2:
                    dot(x, y, 4)
                elif mark == "c" and d2 <= 14.6 ** 2:
                    dot(x, y, 5)
            elif mark == "t" and d2 <= 5.0 ** 2:
                dot(x, y, 5)
    if piece and piece[1] == "k":
        for j, row in enumerate(CROWN):
            for i, ch in enumerate(row):
                if ch == "#":
                    dot(8 + i, 13 + j, 5 if piece[0] == "b" else 7)
        for i in range(15):
            dot(8 + i, 18, 7)
    return make_gif(SQ, SQ, PAL, px)


def images():
    """Under /img/checkers/: the light square, the 32 numbered dark squares (d1-d32) and the same with a
    target dot (t1-t32), and each piece plain, can-move (c) and picked (s)."""
    out = {"/img/checkers/l.gif": _square(-1)}
    for s in range(1, 33):
        out["/img/checkers/d%d.gif" % s] = _square(s)
        out["/img/checkers/t%d.gif" % s] = _square(s, mark="t")
    for piece in ("bm", "bk", "wm", "wk"):
        for mark in ("", "c", "s"):
            out["/img/checkers/%s%s.gif" % (piece, mark)] = _square(piece=piece, mark=mark)
    return out


def web_moves(v, seat, sel):
    """For the viewer: (steps [(from, to, token)], squares that can move, the picked square or 0, its
    targets {to: token})."""
    my = seat >= 0 and v["turn"] == seat and v["phase"] == "move"
    steps = steps_of(v["moves"]) if my else []
    froms = sorted({f for f, _, _ in steps})
    pick = int(sel) if sel and sel.isdigit() and int(sel) in froms else 0
    if v["path"] and froms:
        pick = v["path"][-1]            # a jump that goes on: the jumping piece
    elif not pick and len(froms) == 1:
        pick = froms[0]
    return steps, froms, pick, {t: m for f, t, m in steps if f == pick}


FRAME = 4


def board_image(v, seat, sel):
    """The board as one image: the squares of images() in a wooden frame, Black's side at the bottom for
    Black. On the player's turn a piece that can move picks it up (sel=), and a target dot moves the
    picked piece there."""
    n, cells = v["n"], v["cells"]
    _, froms, pick, targets = web_moves(v, seat, sel)
    flip = seat == 0                    # Black sees the board from its own side
    side = 2 * FRAME + n * SQ
    c = Picture(side, side, bg=PAL[7], images=images)
    for dr in range(n):
        for dc in range(n):
            r, col = (n - 1 - dr, n - 1 - dc) if flip else (dr, dc)
            s = sq(r, col, n)
            x0, y0 = FRAME + dc * SQ, FRAME + dr * SQ
            box = (x0, y0, x0 + SQ - 1, y0 + SQ - 1)
            if not s:
                c.tile("/img/checkers/l.gif", x0, y0)
                continue
            ch = cells[s - 1]
            if ch in "xXoO":
                kind = ("b" if owner(ch) == 0 else "w") + ("k" if ch.isupper() else "m")
                mark = "s" if s == pick else "c" if s in froms else ""
                c.tile("/img/checkers/%s%s.gif" % (kind, mark), x0, y0)
                if s in froms and s != pick:
                    c.area("rect", box, "piece on %d" % s, "/table.htm", sel=s)
            elif s in targets:
                c.tile("/img/checkers/t%d.gif" % s, x0, y0)
                c.area("rect", box, targets[s], "/move.htm", mv=targets[s])
            else:
                c.tile("/img/checkers/d%d.gif" % s, x0, y0)
    c.frame(FRAME - 1, FRAME - 1, n * SQ + 2, n * SQ + 2, (0, 0, 0))
    return c


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    steps, froms, pick, targets = web_moves(v, seat, sel)
    playing = v["phase"] not in ("over", "") and "Game over" not in v["status"]
    m0, k0, m1, k1 = v["counts"]
    rows = []
    for i, (m, k) in enumerate(((m0, k0), (m1, k1))):
        tomove = playing and v["turn"] == i
        rows.append("<tr><td>%s</td><td><b>%s</b></td><td>%s%s</td><td>%d men, %d king%s</td></tr>" % (
            "&gt;" if tomove else "&nbsp;", SIDES[i], esc(names[i]), " (you)" if i == seat else "", m, k,
            "" if k == 1 else "s"))
    side = '<table border="0" cellpadding="3">%s</table>' % "".join(rows)
    if playing and v["offer"] is not None:
        side += "<p>%s offers a draw.</p>" % esc(names[v["offer"]])
    if playing and v["path"]:
        side += "<p>Jumping: %s</p>" % "x".join(str(s) for s in v["path"])
    if playing and v["limit"] and v["quiet"] >= 10:
        side += "<p><font size=\"2\">%d of %d turns with no capture and no man moving; a draw at %d.</font></p>" % (
            v["quiet"], v["limit"], v["limit"])
    if steps:
        side += "<p><font size=\"2\">Moves: %s</font></p>" % " ".join(
            a(link("/move.htm", t=tid, mv=m), esc(m)) for _, _, m in steps)
    actions = []
    my_turn = playing and seat >= 0 and v["turn"] == seat
    if my_turn and v["phase"] == "draw":
        actions += [a(link("/move.htm", t=tid, mv="accept"), "<b>Accept the draw</b>"),
                    a(link("/move.htm", t=tid, mv="reject"), "Decline")]
    if my_turn and v["phase"] == "done":
        actions.append(a(link("/end.htm", t=tid), "<b>End turn</b>"))
    if my_turn and v["path"]:
        actions.append(a(link("/und.htm", t=tid), "Undo"))
    if playing and seat >= 0 and v["phase"] in ("move", "done") and not v["path"] and v["offer"] is None:
        actions.append(a(link("/move.htm", t=tid, mv="draw"), "Offer a draw"))
    hint = ""
    if my_turn and v["phase"] == "move" and steps:
        if v["path"]:
            hint = " - keep jumping: click a yellow dot"
        elif pick:
            hint = " - click a yellow dot%s" % (", or another piece with a yellow rim" if len(froms) > 1 else "")
        else:
            hint = " - click a piece with a yellow rim, then where it goes"
    elif my_turn and v["phase"] == "done":
        hint = " - click End turn, or Undo"
    board = board_html(board_image, v, tid, link, seat, sel, "Checkers board", text=False)
    return board, side, hint, " | ".join(actions)


# ---- telnet --------------------------------------------------------------------------
#
# The board, 42 columns by 18 rows from (1, 2): a frame round 8 by 8 cells of 5 columns and 2 rows. A dark
# square shows its piece (or a target mark) on its first row and its number on the second. The player's
# own side is at the bottom (White's for watchers, as in the usual diagrams).

BX, BY = 1, 2                           # the frame's top left
A_LIGHT = "0;30;47"
A_PIECE = ("0;30;", "0;1;37;")          # per seat; the square's background is added
A_MARK = "0;1;33;"
A_NUM = "0;30;"
CK_HELP = [
    "Arrow keys   move the cursor over the dark squares",
    "Enter/Space  pick up a piece that can move, then",
    "             put it down on a marked square",
    "Mouse        click a piece, then where it goes",
    "U  undo a step            E  end the turn",
    "D  offer a draw           A/N  accept/decline one",
    "T  talk                   R  resign (asks first)",
    "Esc  back to the menu (the game goes on)",
    "",
    "Captures are compulsory, and a jump goes on while",
    "the piece can jump again. Typed: 11-15, 15x22,",
    "undo, end, draw, accept, reject, say <text>.",
]
STEP_RE = re.compile(r"^\d{1,2}([-x]\d{1,2})+\Z")
WORDS = {"undo": "undo", "und": "undo", "end": "end", "done": "end", "draw": "draw", "accept": "accept",
         "reject": "reject", "decline": "reject"}


def ck_glyphs(mode):
    """Pieces, marks and the frame for a mode. Men are round and kings pointed, and the two sides differ
    in their fill (pc, utf8) or their letter (ansi, plain), so color is never needed."""
    if mode in ("pc", "utf8"):
        return {"man": ("(█)", "(▒)"), "king": ("«█»", "«▒»"), "mark": "(·)", "sel": ("»", "«"),
                "TL": "╔", "TR": "╗", "BL": "╚", "BR": "╝", "H": "═", "V": "║"}
    return {"man": ("(x)", "(o)"), "king": ("[X]", "[O]"), "mark": "(*)", "sel": (">", "<"),
            "TL": "+", "TR": "+", "BL": "+", "BR": "+", "H": "-", "V": "|"}


class CkBase(GameBase):
    """Checkers, shared by the full screen and the plain screen."""

    BACKLOG = 16

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.ck = ck_glyphs(sess.mode)

    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or describe_common(text, self.names)

    # ---- state ------------------------------------------------------------
    def playing(self):
        return bool(self.v and self.v["rows"]) and not self.over and self.v["phase"] != "over"

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    def phase(self):
        """The phase when it is this player's turn (move, done, draw), else ''."""
        return self.v["phase"] if self.my_turn() else ""

    def steps(self):
        return steps_of(self.v["moves"]) if self.phase() == "move" else []

    def froms(self):
        out = []
        for f, _, _ in self.steps():
            if f not in out:
                out.append(f)
        return out

    def targets(self, frm):
        return [(t, m) for f, t, m in self.steps() if f == frm]

    def can_offer(self):
        v = self.v
        return (self.me >= 0 and self.playing() and v["phase"] in ("move", "done") and not v["path"]
                and v["offer"] is None)

    def top_seat(self):
        return 1 if self.me == 0 else 0

    def disp(self, s):
        """Square -> (display row, display column)."""
        r, c = rc(s, self.v["n"])
        n = self.v["n"]
        return (n - 1 - r, n - 1 - c) if self.me == 0 else (r, c)

    def at(self, dr, dc):
        n = self.v["n"]
        return sq(n - 1 - dr, n - 1 - dc, n) if self.me == 0 else sq(dr, dc, n)

    def counts_text(self, seat):
        m, k = self.v["counts"][2 * seat], self.v["counts"][2 * seat + 1]
        return "%s: %d m%sn, %d king%s" % (SIDES[seat], m, "a" if m == 1 else "e", k, "" if k == 1 else "s")

    def seat_label(self, seat):
        empty = seat >= len(self.names) or self.names[seat] == "-"
        return ("(empty seat)" if empty else self.names[seat]) + (" (you)" if seat == self.me else ""), empty

    def state_text(self):
        """-> (whose turn and what to do, attribute)."""
        v = self.v
        if not v["rows"]:
            return "Waiting for an opponent", A_YELLOW
        if self.over or v["phase"] == "over":
            return self.over or v["status"] or "Game over", A_YELLOW
        t, mine = v["turn"], self.my_turn()
        if v["phase"] == "draw":
            if mine:
                return "%s offers a draw: accept?" % self.name(v["offer"]), A_YELLOW
            return "%s to answer the draw offer" % self.name(t), A_TEXT
        if mine:
            if v["phase"] == "done":
                return "Your move: turn complete", A_YELLOW
            if v["path"]:
                return "Keep jumping from %d" % v["path"][-1], A_YELLOW
            if any("x" in m for m in v["moves"]):
                return "Your move: you must capture", A_YELLOW
            return "Your move", A_YELLOW
        if v["path"]:
            return "%s is jumping" % self.name(t), A_TEXT
        return "%s to move" % self.name(t), A_TEXT

    def side_note(self):
        """A draw offer, or how near the move limit is ('' if neither)."""
        v = self.v
        if not v["rows"] or not self.playing():
            return ""
        if v["offer"] is not None and v["phase"] != "draw":
            return "%s offered a draw" % self.name(v["offer"])
        if v["limit"] and v["quiet"] >= 10:
            return "No capture or man move: %d/%d" % (v["quiet"], v["limit"])
        return ""

    # ---- the board ----------------------------------------------------------
    def draw_board(self, cv, cur=0, sel=0):
        """The board into cv; with cur (the cursor's square) and sel (a picked piece), the player's
        highlights too: pieces that can move and where the picked one can go."""
        G, v = self.ck, self.v
        n = v["n"]
        w = 5 * n
        cv.put(BX, BY, G["TL"] + G["H"] * w + G["TR"], A_BORDER)
        cv.put(BX, BY + 2 * n + 1, G["BL"] + G["H"] * w + G["BR"], A_BORDER)
        for y in range(BY + 1, BY + 2 * n + 1):
            cv.put(BX, y, G["V"], A_BORDER)
            cv.put(BX + w + 1, y, G["V"], A_BORDER)
        froms = set(self.froms()) if cur else set()
        marks = {t for t, _ in self.targets(sel)} if sel else set()
        cells = v["cells"]
        for dr in range(n):
            for dc in range(n):
                x, y = BX + 1 + 5 * dc, BY + 1 + 2 * dr
                s = self.at(dr, dc)
                if not s:
                    cv.put(x, y, "     ", A_LIGHT)
                    cv.put(x, y + 1, "     ", A_LIGHT)
                    continue
                bg = "46" if s == cur else "43" if s == sel else "42"
                ch = cells[s - 1] if s <= len(cells) else "."
                if ch in "xXoO":
                    seat = owner(ch)
                    cv.put(x, y, " %s " % G["king" if ch.isupper() else "man"][seat], A_PIECE[seat] + bg)
                elif s in marks:
                    cv.put(x, y, " %s " % G["mark"], A_MARK + bg)
                else:
                    cv.put(x, y, "     ", A_NUM + bg)
                num = "%s%d%s" % (G["sel"][0], s, G["sel"][1]) if s == sel else str(s)
                cv.put(x, y + 1, num.center(5), (A_MARK if s in froms or s in marks else A_NUM) + bg)
                cv.hot_area(x, y, 5, 2, ("sq", s))

    # ---- game words ---------------------------------------------------------
    async def act(self, word):
        """A step, undo, end, draw, accept or reject -> an error text or None."""
        if self.me < 0:
            return "You are watching this table."
        if not self.playing():
            return self.over or "The game has not started yet."
        if word == "draw":
            if not self.can_offer():
                return "You can offer a draw when no turn is half played and no offer is waiting."
            return await self.move("draw")
        if not self.my_turn():
            return "It is %s's turn." % self.name(self.v["turn"])
        if word == "undo":
            return await self.staged("UND", "take that back")
        if word == "end":
            return await self.staged("END", "end the turn")
        return await self.move(word)

    def move_word(self, cmd, words=()):
        if cmd in WORDS:
            return WORDS[cmd]
        return cmd if STEP_RE.match(cmd) else None

    async def play_word(self, word):
        return await self.act(word)


class AnsiCheckers(CkBase, AnsiGame):
    """Checkers, full screen: the board on the left (a cursor, the pieces that can move, a picked piece
    and where it can go), players, whose turn, the clock and the log on the right."""

    HOTKEYS = "uedanrt?"
    LOG_TOP, LOG_ROWS = 12, 7
    TITLE, HELP = "Checkers", CK_HELP

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = 11 if me == 0 else 22
        self.sel = 0
        self.zig = 1

    def snap(self, stepped=False):
        """When the move starts (or after a step), put the cursor on a piece that can move; a jump that
        goes on keeps its piece picked; forget a pick that can no longer move."""
        froms = self.froms()
        path = self.v["path"] if self.v else []
        if path and froms:
            self.sel = path[-1]
            if self.cur not in [t for t, _ in self.targets(self.sel)]:
                self.cur = self.targets(self.sel)[0][0]
        elif self.sel not in froms:
            self.sel = 0
        if froms and (not self.was_my_turn or stepped) and not self.sel and self.cur not in froms:
            self.cur = froms[0]
        self.was_my_turn = bool(froms)

    # ---- drawing ------------------------------------------------------------
    def compose(self):
        cv = Canvas()
        g, v = self.g, self.v
        right = "watching" if self.me < 0 else "you play %s %s" % (SIDES[self.me].lower(), self.ck["man"][self.me])
        title_bar(cv, g, "Checkers %s table %s" % (g["v"], self.tid), right)
        showcur = self.me >= 0 and self.playing()
        if v["rows"]:
            self.draw_board(cv, self.cur if showcur else 0, self.sel if showcur else 0)
        else:
            self.draw_empty(cv)
            self.draw_waiting(cv, BX + 8, BY + 6)
        self.draw_panel(cv)
        steps = self.steps()
        if steps:
            line = "Moves: " + " ".join(m for _, _, m in steps)
            cv.put(1, 20, line if len(line) <= COLS - 2 else line[:COLS - 5] + "...", A_DIM)
        elif v["path"] and self.playing():
            cv.put(1, 20, ("Jumped: " + "x".join(str(s) for s in v["path"]))[:COLS - 2], A_DIM)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        ph = self.phase()
        tail = [("T", "talk"), ("R", "resign"), ("?", "help"), ("Esc", "menu")]
        draw = [("D", "draw")] if self.can_offer() else []
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif ph == "draw":
            keys = [("A", "accept"), ("N", "decline")] + tail
        elif ph == "done":
            keys = [("E", "end turn"), ("U", "undo")] + tail
        elif ph == "move":
            keys = [("Arrows", "move"), ("Enter", "pick")] + ([("U", "undo")] if v["path"] else draw) + tail
        elif self.me >= 0 and self.playing():
            keys = [("Arrows", "look")] + draw + tail
        else:
            keys = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        status_bar(cv, keys)
        return cv

    def draw_empty(self, cv):
        """The frame and the squares, with no pieces, before the game starts."""
        n = 8
        cv.put(BX, BY, self.ck["TL"] + self.ck["H"] * 5 * n + self.ck["TR"], A_BORDER)
        cv.put(BX, BY + 2 * n + 1, self.ck["BL"] + self.ck["H"] * 5 * n + self.ck["BR"], A_BORDER)
        for y in range(2 * n):
            cv.put(BX, BY + 1 + y, self.ck["V"], A_BORDER)
            cv.put(BX + 5 * n + 1, BY + 1 + y, self.ck["V"], A_BORDER)
            for c in range(n):
                cv.put(BX + 1 + 5 * c, BY + 1 + y, "     ", "0;30;42" if (y // 2 + c) % 2 else A_LIGHT)

    def draw_panel(self, cv):
        g, v = self.g, self.v
        px = BX + 5 * 8 + 3
        pw = COLS - 1 - px
        inner = pw - 4
        box(cv, g, px, 1, pw, 19, A_BORDER, "Players", tattr=A_HEAD)
        for i, seat in enumerate((self.top_seat(), 1 - self.top_seat())):
            y = 2 + 2 * i
            tomove = self.playing() and v["turn"] == seat
            label, empty = self.seat_label(seat)
            cv.put(px + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            cv.put(px + 4, y, self.ck["man"][seat], A_PIECE[seat] + "42")
            cv.put(px + 8, y, label[:inner - 6], A_BRIGHT if tomove else A_DIM if empty else A_TEXT)
            if v["rows"]:
                cv.put(px + 4, y + 1, self.counts_text(seat)[:inner - 2], A_DIM)
        rule(cv, g, px, 6, pw, A_BORDER)
        text, attr = self.state_text()
        for i, line in enumerate(wrap(text, inner)[:2]):
            cv.put(px + 2, 7 + i, line, attr)
        note = self.side_note()
        if note:
            cv.put(px + 2, 9, note[:inner], A_TEXT)
        c = self.clock()
        if c:
            seat, left, limit = c
            clock = "Clock: %s left of %s" % (fmt_secs(left), turn_text(limit))
            if len(clock) > inner:
                clock = "Clock: %s left" % fmt_secs(left)
            cv.put(px + 2, 10, clock[:inner], A_RED if seat == self.me and left < 60 else A_TEXT)
        elif self.turn_secs and not self.over:
            cv.put(px + 2, 10, ("Time per turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        rule(cv, g, px, 11, pw, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, px + 2, inner)

    def hint(self):
        v = self.v
        if not v["rows"]:
            return ("Table %s starts when someone joins: telnet, the web (port 6500) or another client."
                    % self.tid, A_DIM)
        if self.over or v["phase"] == "over":
            return "%s. Esc goes back to the menu." % (self.over or "Game over"), A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        ph = self.phase()
        if not ph:
            if v["phase"] == "draw" and v["offer"] == self.me:
                return "Waiting for %s to answer your draw offer." % self.name(v["turn"]), A_DIM
            return "Waiting for %s to move. Leaving never resigns." % self.name(v["turn"]), A_DIM
        if ph == "draw":
            return "%s offers a draw: %s accepts (the game ends drawn), N declines." % (
                self.name(v["offer"]), self.kn("A")), A_TEXT
        if ph == "done":
            return "Turn complete: %s (or Enter) ends it, U takes back the last step." % self.kn("E"), A_TEXT
        s = self.cur
        if self.sel:
            t = self.targets(self.sel)
            if s in [x for x, _ in t]:
                tok = dict(t)[s]
                return "Enter plays %s.%s" % (tok, "" if v["path"] else " Esc puts the piece back."), A_TEXT
            return "The piece on %d can go to %s: put the cursor there, press Enter." % (
                self.sel, ", ".join(str(x) for x, _ in t)), A_TEXT
        if s in self.froms():
            return "%d: Enter picks up this piece; it can go to %s." % (
                s, ", ".join(str(x) for x, _ in self.targets(s))), A_TEXT
        must = " (a capture is compulsory)" if any("x" in m for _, _, m in self.steps()) else ""
        return "Pieces that can move%s: %s." % (must, ", ".join(str(f) for f in self.froms())), A_DIM

    def cursor_at(self):
        if self.me >= 0 and self.playing():
            dr, dc = self.disp(self.cur)
            return BX + 1 + 5 * dc + 2, BY + 1 + 2 * dr
        return None

    # ---- keys ---------------------------------------------------------------
    async def on_click(self, value):
        if value[0] == "sq" and self.me >= 0 and self.playing():
            self.cur = value[1]
            if self.steps():
                return await self.pick()
        return None

    def on_arrow(self, k):
        if not self.v["rows"]:
            return
        n = self.v["n"]
        dr, dc = self.disp(self.cur)
        if k in ("LEFT", "RIGHT"):
            dc = (dc + (2 if k == "RIGHT" else -2)) % n
        else:
            dr = (dr + (1 if k == "DOWN" else -1)) % n
            dc = dc + self.zig if 0 <= dc + self.zig < n else dc - self.zig
            self.zig = -self.zig
        s = self.at(dr, dc)
        if s:
            self.cur = s

    def on_escape(self):
        if self.sel and not self.v["path"]:
            self.sel = 0
            return None
        return "menu"

    async def on_enter(self):
        if self.me < 0:
            return self.say("You are watching this table.")
        if not self.playing():
            return self.say(self.over or "The game has not started yet.")
        ph = self.phase()
        if not ph:
            return self.say("It is %s's turn." % self.name(self.v["turn"]))
        if ph == "draw":
            return self.say("A draw is offered: %s accepts, N declines." % self.kn("A"))
        if ph == "done":
            return await self.play("end")
        return await self.pick()

    async def pick(self):
        """Enter (or a click) on the cursor square: pick up a piece there, or put the picked one down."""
        s = self.cur
        if self.sel and s in [t for t, _ in self.targets(self.sel)]:
            step = dict(self.targets(self.sel))[s]
            if not self.v["path"]:
                self.sel = 0
            return await self.play(step)
        if s == self.sel and not self.v["path"]:
            self.sel = 0
            return None
        if s in self.froms() and not self.v["path"]:
            self.sel = s
            self.cur = self.targets(s)[0][0]       # on to its first target
            return None
        if self.sel:
            return self.say("The piece on %d can't go to %d." % (self.sel, s), A_RED)
        if any("x" in m for _, _, m in self.steps()):
            return self.say("A capture is compulsory: pieces that can jump: %s." % ", ".join(
                str(f) for f in self.froms()), A_RED)
        return self.say("No piece of yours can move from %d now." % s, A_RED)

    async def play(self, word):
        err = await self.act(word)
        if err:
            self.say(err, A_RED)
        self.snap(stepped=True)

    async def typed_move(self, word):
        self.sel = 0
        await self.play(word)

    async def hotkey(self, k):
        ph = self.phase()
        if k in ("r", "t", "?"):
            return await super().hotkey(k)
        if k == "u":
            if ph not in ("move", "done") or not self.v["path"]:
                return self.say("There is no step of yours to take back now.")
            return await self.play("undo")
        if k == "e":
            if ph != "done":
                return self.say("Finish the move first." if ph == "move" else "It is not your move.")
            return await self.play("end")
        if k == "d":
            if not self.can_offer():
                return self.say("You can offer a draw when no turn is half played and no offer is waiting.")
            if await self.confirm("Draw", ["Offer your opponent a draw? If they accept,",
                                           "the game ends drawn."]):
                return await self.play("draw")
            return None
        if k == "a":
            if ph != "draw":
                return self.say("There is no draw offer to accept.")
            return await self.play("accept")
        if k == "n":
            if ph != "draw":
                return self.say("There is no draw offer to decline.")
            return await self.play("reject")
        return None


class PlainCheckers(CkBase, PlainGame):
    """Checkers for plain terminals: the board in ASCII, typed moves."""

    BOARD_KINDS = ("STEP", "JUMP", "CROWN", "UNDO", "DRAW", "ACCEPT", "REJECT", "TURN", "JOIN", "LEAVE", "START",
                   "END")
    HELP_TEXT = (" A step like 11-15 moves a piece; a jump is like 15x22 (or 15x22x31 for\n"
                 " several). Captures are compulsory and a jump goes on while it can.\n"
                 " undo takes a step back, end finishes the turn. draw offers a draw;\n"
                 " accept or reject answers one. say <text> talks to the table; resign\n"
                 " gives up (it asks first). menu goes back; leaving never resigns.")

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.ck = ck_glyphs("plain")

    def lines(self):
        v, me, G = self.v, self.me, self.ck
        you = "you are watching" if me < 0 else "you play %s %s" % (SIDES[me].lower(), G["man"][me])
        out = ["", "== Checkers, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when someone joins table %s from telnet, the web" % self.tid,
                          "(port 6500) or another client. Type menu to go back; the table stays open."]
        cv = Canvas()
        self.draw_board(cv)
        side = []
        for seat in (self.top_seat(), 1 - self.top_seat()):
            label, _ = self.seat_label(seat)
            mark = ">" if self.playing() and v["turn"] == seat else " "
            side += ["%s %s %s" % (mark, G["man"][seat], label), "    " + self.counts_text(seat)]
        x = BX + 5 * 8 + 4
        side += [""] + wrap(self.state_text()[0], COLS - 1 - x)
        note = self.side_note()
        if note:
            side.append(note)
        side += ["", "Men %s %s, kings %s %s." % (G["man"][0], G["man"][1], G["king"][0], G["king"][1])]
        ph = self.phase()
        if ph == "draw":
            side.append("Type accept or reject.")
        elif ph == "done":
            side += ["Type end to finish the turn,", "or undo."]
        elif ph == "move" and self.steps():
            side.append("Type a step like %s%s" % (self.steps()[0][2], "," if self.v["path"] else "."))
            if self.v["path"]:
                side.append("or undo.")
        if self.can_offer():
            side.append("draw offers a draw.")
        side.append("say <text>, resign, menu, help")
        for i, l in enumerate(side):
            cv.put(x, BY + i, l[:COLS - 1 - x])
        out += cv.lines()[BY:BY + 18]
        if self.steps():
            out += [" " + l for l in wrap("Moves: " + " ".join(m for _, _, m in self.steps()), 78)]
        if not self.over:
            out.append(" " + clock_text(v, self.names))
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return out

    def prompt(self):
        ph = self.phase()
        if ph == "draw":
            return "Accept or reject the draw > "
        if ph == "done":
            return "Turn complete: end or undo > "
        if ph == "move" and self.steps():
            return "%s (like %s) > " % ("Keep jumping" if self.v["path"] else "Your move", self.steps()[0][2])
        return "> "


AnsiScreen, PlainScreen = AnsiCheckers, PlainCheckers
