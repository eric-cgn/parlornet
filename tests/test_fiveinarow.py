"""fiveinarow/1: the plugin's rules, a whole game over the protocol, and the web and telnet frontends."""
import asyncio
import importlib.util
import os
import re
import subprocess
import sys
import unittest

from game_checks import FakeIO, ScreenCore, ansi_screens, board_image, plain_screen, session, web_page
from test_core import ROOT, Client, free_port

from adapters.games import fiveinarow as F  # noqa: E402

_spec = importlib.util.spec_from_file_location("five_plugin", os.path.join(ROOT, "plugins", "fiveinarow", "plugin.py"))
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)


def new(rules=None, size=None):
    req = {"op": "new", "players": 2, "options": {} if size is None else {"size": str(size)}, "seed": 1}
    if rules:
        req["rules"] = rules
    r = fp.op_new(req)
    assert "error" not in r, r
    return r["state"]


def play(st, seat, tok):
    r = fp.op_apply({"state": st, "seat": seat, "move": [tok]})
    assert "error" not in r, (tok, r)
    return r


def play_all(st, black, white):
    """Black plays black[i], white plays white[i], alternately; -> the last reply."""
    r = None
    for i, b in enumerate(black):
        r = play(st, 0, b)
        st = r["state"]
        if i < len(white):
            r = play(st, 1, white[i])
            st = r["state"]
    return r


def wire(st, seat=0):
    return fp.op_view({"state": st, "seat": seat})["wire"]


def no_five_board(n):
    """A full n by n board with no five in a row for either seat: (x + 2y) mod 4 < 2 is seat 0. Seats are
    swapped if needed so seat 0 has as many stones as seat 1, or one more."""
    cells = ["0" if (x + 2 * y) % 4 < 2 else "1" for y in range(n) for x in range(n)]
    if cells.count("0") < cells.count("1"):
        cells = ["1" if c == "0" else "0" for c in cells]
    return cells


class Rules(unittest.TestCase):
    def test_info_and_new(self):
        self.assertEqual(fp.op_info({})["rules"], ["freestyle", "standard"])
        self.assertEqual(fp.op_info({})["flags"], ["nomoves"])
        r = fp.op_new({"players": 2, "options": {}, "seed": 0})
        self.assertEqual(r["events"], ["TURN 0"])
        self.assertEqual((r["state"]["n"], r["state"]["rules"]), (15, "freestyle"))   # no ruleset given
        self.assertEqual(new("standard")["rules"], "standard")
        for size in (9, 12, 19, 20):
            self.assertEqual(new(size=size)["n"], size)
        for bad in ("8", "21", "x"):
            self.assertIn("error", fp.op_new({"players": 2, "options": {"size": bad}, "seed": 0}))
        self.assertIn("error", fp.op_new({"players": 2, "options": {}, "seed": 0, "rules": "renju"}))
        self.assertIn("error", fp.op_new({"players": 3, "options": {}, "seed": 0}))
        self.assertEqual(fp.op_moves({"state": new(), "seat": 0}), {"check": True})   # every empty point

    def test_wins_in_all_four_directions(self):
        lines = {"across": ["d4", "e4", "f4", "g4", "h4"], "down": ["d4", "d5", "d6", "d7", "d8"],
                 "diagonal": ["d4", "e5", "f6", "g7", "h8"], "other diagonal": ["h4", "g5", "f6", "e7", "d8"]}
        white = ["a15", "c15", "e15", "g15"]
        for name, five in lines.items():
            for order in (five, five[:2] + five[3:] + five[2:3]):       # the last stone at an end, in the middle
                for rules in ("freestyle", "standard"):
                    r = play_all(new(rules), order, white)
                    self.assertEqual(r["events"], ["PUT 0 %s" % order[-1]], (name, rules))   # no TURN: it ended
                    ends = sorted([five[0], five[-1]])
                    self.assertIn(r["end"], ("win 0 %s-%s" % tuple(ends), "win 0 %s-%s" % tuple(ends[::-1])))
                    st = r["state"]
                    self.assertTrue(st["over"])
                    w = wire(st)
                    f = [l for l in w if l.startswith("F ")][0].split()[1:]
                    self.assertEqual(sorted(f), sorted(five), name)
                    self.assertIn("W -1", w)
                    self.assertTrue(fp.op_view({"state": st, "seat": 0})["status"].startswith("Game over: win 0"))
                    self.assertEqual(fp.op_result({"state": st}), {"over": True, "scores": [1, 0]})
                    self.assertIn("error", fp.op_apply({"state": st, "seat": 1, "move": ["o1"]}))

    def test_white_wins_and_four_is_not_five(self):
        r = play_all(new(), ["a1", "a3", "a5", "a7"], ["h3", "h4", "h5", "h6"])
        self.assertEqual(r["events"], ["PUT 1 h6", "TURN 0"])           # four in a row: play goes on
        r = play(r["state"], 0, "o15")
        r = play(r["state"], 1, "h7")
        self.assertEqual(r["end"], "win 1 h3-h7")
        self.assertEqual(fp.op_result({"state": r["state"]})["scores"], [0, 1])

    def test_overlines(self):
        """Six in a row (a gap filled between three and two): freestyle wins, standard plays on."""
        black, white = ["b2", "c2", "d2", "f2", "g2", "e2"], ["b9", "d9", "f9", "h9", "j9"]
        r = play_all(new("freestyle"), black, white)
        self.assertEqual(r["end"], "win 0 b2-g2")
        self.assertEqual([l for l in wire(r["state"]) if l.startswith("F ")], ["F b2 c2 d2 e2 f2 g2"])
        r = play_all(new("standard"), black, white)
        self.assertNotIn("end", r)
        self.assertEqual(r["events"], ["PUT 0 e2", "TURN 1"])
        self.assertFalse([l for l in wire(r["state"]) if l.startswith("F ")])
        # four and four joined: nine in a row
        black, white = ["a5", "b5", "c5", "d5", "f5", "g5", "h5", "i5", "e5"], ["a9", "c9", "e9", "g9", "a11",
                                                                                  "c11", "e11", "g11"]
        self.assertEqual(play_all(new("freestyle"), black, white)["end"], "win 0 a5-i5")
        self.assertNotIn("end", play_all(new("standard"), black, white))
        describe = F.describe_event("END win 0 a5-i5", ["ann", "bob"])
        self.assertEqual(describe, "Game over: ann wins with nine in a row, a5 to i5")
        # standard: an overline across and exactly five down at once still wins
        black = ["b2", "c2", "d2", "f2", "g2", "e3", "e4", "e5", "e6", "e2"]
        white = ["b9", "d9", "f9", "h9", "j9", "b11", "d11", "f11", "h11"]
        r = play_all(new("standard"), black, white)
        self.assertEqual(r["end"], "win 0 e2-e6")
        # standard: a five that a later stone would extend still won when it was made
        r = play_all(new("standard"), ["c3", "d3", "e3", "f3", "g3"], white[:4])
        self.assertEqual(r["end"], "win 0 c3-g3")

    def test_draw_when_the_board_is_full(self):
        for n in (9, 10, 15):
            cells = no_five_board(n)
            last = n * n - 1
            mover = last % 2                              # who places the last stone
            spot = next(i for i in range(n * n) if cells[i] == str(mover))
            mine = [i for i in range(n * n) if cells[i] == "0" and i != spot]
            theirs = [i for i in range(n * n) if cells[i] == "1" and i != spot]
            hist = [x for pair in zip(mine, theirs) for x in pair] + (mine[len(theirs):] if mover else [])
            hist += theirs[len(mine):] if not mover else []
            self.assertEqual(len(hist), last)
            board = list(cells)
            board[spot] = "."
            st = dict(new(size=n), cells="".join(board), hist=hist, turn=mover)
            r = play(st, mover, fp.sq_name(n, spot))
            c0, c1 = cells.count("0"), cells.count("1")
            self.assertEqual(r["end"], "draw %d-%d" % (c0, c1), n)
            self.assertEqual(r["events"], ["PUT %d %s" % (mover, fp.sq_name(n, spot))])
            self.assertEqual(fp.op_result({"state": r["state"]}), {"over": True, "scores": [0, 0]})
            self.assertEqual(F.describe_event("END " + r["end"], ["a", "b"]), "Game over: a draw, the board is full")
            # the full board, in P lines of at most 78 characters, comes back whole through the frontend
            w = wire(r["state"])
            self.assertTrue(all(len(l) <= 78 for l in w))
            v = F.parse_view(["V fiveinarow/1 99", "BRD %d %d" % (n, n)] + w)
            self.assertEqual(len(v["stones"]), n * n)
            self.assertEqual(v["counts"], (c0, c1))
            self.assertEqual(v["turn"], -1)

    def test_errors_and_check(self):
        st = play(new(size=9), 0, "e5")["state"]
        for tok, why in (("e5", "taken"), ("j1", "no such point"), ("a10", "no such point"), ("a0", "no such"),
                         ("zz", "no such point")):
            self.assertIn(why, fp.op_apply({"state": st, "seat": 1, "move": [tok]})["error"], tok)
        self.assertIn("not your turn", fp.op_apply({"state": st, "seat": 0, "move": ["a1"]})["error"])
        self.assertIn("one point", fp.op_apply({"state": st, "seat": 1, "move": ["a1", "a2"]})["error"])
        self.assertEqual(play(st, 1, "A1")["events"][0], "PUT 1 a1")                 # either case
        r = play_all(new(), ["a1", "a2", "a3", "a4"], ["o1", "o2", "o3", "o4"])
        st = r["state"]
        self.assertEqual(fp.op_apply({"state": st, "seat": 0, "move": ["a5"], "check": True}), {"info": "wins"})
        self.assertEqual(fp.op_apply({"state": st, "seat": 0, "move": ["b5"], "check": True}), {"info": ""})
        self.assertEqual(st["cells"].count("."), 15 * 15 - 8)                         # check changed nothing

    def test_view(self):
        st = new()
        self.assertEqual(wire(st), ["C 0 0", "W 0"])
        v = fp.op_view({"state": st, "seat": 0})
        self.assertEqual(v["geometry"], ["BRD 15 15", "RUL freestyle"])
        self.assertEqual(v["status"], "Your move")
        self.assertEqual(fp.op_view({"state": st, "seat": 1})["status"], "Waiting for the other player")
        self.assertEqual(fp.op_view({"state": st, "seat": -1})["status"], "Seat 0 to move")
        r = play_all(st, ["h8", "j10"], ["i9"])
        self.assertEqual(wire(r["state"]), ["P 0 h8 j10", "P 1 i9", "C 2 1", "L 0 j10", "W 1"])
        self.assertEqual(fp.op_result({"state": r["state"]}), {"over": False})


class Frontend(unittest.TestCase):
    """The parser, the event texts and the move words."""

    def test_parse_view(self):
        self.assertFalse(F.parse_view(["V fiveinarow/1 1", "S Waiting for players"])["rows"])
        v = F.parse_view(["V fiveinarow/1 7", "BRD 19 19", "RUL standard", "P 0 h8 s19", "P 0 a1", "P 1 j10",
                          "C 3 1", "L 0 a1", "W 1", "CLK 1 50 300", "S Waiting for the other player"])
        self.assertTrue(v["rows"])
        self.assertEqual((v["seq"], v["n"], v["rules"], v["turn"], v["counts"]), (7, 19, "standard", 1, (3, 1)))
        self.assertEqual(v["order"], ([(7, 7), (18, 18), (0, 0)], [(9, 9)]))
        self.assertEqual(v["stones"][(9, 9)], 1)
        self.assertEqual((v["last"], v["clock"], v["moves"]), ((0, 0), (1, 50, 300), []))

    def test_describe(self):
        names = ["ann", "bob"]
        self.assertEqual(F.describe_event("PUT 1 j10", names), "bob plays j10")
        self.assertEqual(F.describe_event("END win 0 h4-h8", names), "Game over: ann wins with five in a row, h4 to h8")
        self.assertEqual(F.describe_event("END win 1 o1-k5", names), "Game over: bob wins with five in a row, o1 to k5")
        self.assertIsNone(F.describe_event("END resign 0", names))
        self.assertIsNone(F.describe_event("TURN 1", names))
        from adapters import web as W
        self.assertEqual(W.describe("END resign 0", names, F.KEY), "ann resigned")
        self.assertEqual(W.describe("PUT 0 h8", names, F.KEY), "ann plays h8")

    def test_move_re(self):
        for ok in ("a1", "s19", "j10", "h8", "o15", "t20", "a20"):
            self.assertTrue(F.MOVE_RE.match(ok), ok)
        for bad in ("u1", "a21", "a0", "a01", "h8\n", "h8 ", "H8", "a", "1a", "a100"):
            self.assertFalse(F.MOVE_RE.match(bad), bad)

    def test_images(self):
        imgs = F.images()
        self.assertEqual(len(imgs), 9 * 5 + 1)
        self.assertTrue(all(p.startswith("/img/fiveinarow/") for p in imgs))
        self.assertTrue(all(d[6:8] == bytes([F.CELL, 0]) for d in imgs.values()))      # 25 pixels wide
        self.assertEqual(sorted(F.star_points(15)), [(3, 3), (3, 11), (7, 7), (11, 3), (11, 11)])
        self.assertEqual(len(F.star_points(19)), 9)
        self.assertEqual(len(F.star_points(10)), 4)


def view(n=15, black=(), white=(), last=None, turn=0, five=(), rules="freestyle", status=None):
    lines = ["V fiveinarow/1 40", "BRD %d %d" % (n, n), "RUL %s" % rules]
    lines += fp.wrapped("P 0", list(black)) + fp.wrapped("P 1", list(white))
    lines.append("C %d %d" % (len(black), len(white)))
    if last:
        lines.append("L %d %s" % (0 if last in black else 1, last))
    if five:
        lines.append("F " + " ".join(five))
    lines.append("W %d" % turn)
    if turn >= 0:
        lines.append("CLK %d 250 300" % turn)
    lines.append("S " + (status or ("Game over: win 0 %s-%s" % (five[0], five[-1]) if five else "Your move")))
    return lines


def crowded(n):
    """All but a few points taken (a board near the end), the last stone in the bottom-right corner."""
    cells = no_five_board(n)
    pts = [F.sq(i % n, i // n) for i in range(n * n)]
    black = [p for p, c in zip(pts, cells) if c == "0"][2:]
    white = [p for p, c in zip(pts, cells) if c == "1"][2:]
    corner = F.sq(n - 1, n - 1)
    for side in (black, white):
        if corner in side:
            side.remove(corner)
            side.append(corner)
    return view(n, black, white, last=corner, turn=0)


OPENING = view(15, ["h8"], [], last="h8", turn=1)
MIDGAME = view(15, ["h8", "i9", "j10"], ["h9", "g8"], last="g8", turn=0)
FINISHED = view(15, ["h4", "h5", "h6", "h7", "h8"], ["a1", "a2", "a3", "a4"], last="h8", turn=-1,
                five=["h4", "h5", "h6", "h7", "h8"])


class Screens(unittest.TestCase):
    def test_opening_for_each_seat_and_a_watcher(self):
        empty = view(15, turn=0)
        for me in (0, 1, -1):
            shown = ansi_screens(self, F, empty, me, keys=["ENTER"])
            for mode, (text, reqs) in shown.items():
                self.assertIn("Five in a Row", text)
                self.assertIn(" 15 ", text)
                if me == 0:
                    self.assertIn("MOV 3 h8", reqs, mode)               # the cursor starts on the center
                    self.assertIn("Your move", text)
                else:
                    self.assertFalse([r for r in reqs if r.startswith("MOV")], (mode, me))
            text = plain_screen(self, F, empty, me)
            self.assertIn("  8  . . . . . . . . . . . . . . .", text)
            self.assertIn(("Your move (like h8) > " if me == 0 else "> "), text)

    def test_twenty_by_twenty(self):
        """NetLuff's board: rows 1 to 20 on screen rows 2 to 21, so the hint moves to the line below; a click
        on t20 plays it."""
        lines = view(20, ["j10"], ["k11"], last="k11", turn=0)
        shown = ansi_screens(self, F, lines, 0, log=False, keys=[("CLICK", 5 + 2 * 19, 2 + 19)])
        for mode, (text, reqs) in shown.items():
            rows = text.split("\n")[:24]
            self.assertTrue(rows[21].startswith(" 20 "), (mode, rows[21]))
            self.assertIn("Your move", rows[22], mode)
            self.assertIn("MOV 3 t20", reqs, mode)
        self.assertEqual(F.pt("t20"), (19, 19))
        self.assertEqual(fp.parse_sq("t20", 20), 399)

    def test_every_size_fits(self):
        """9 by 9 to 20 by 20, near full, as each seat and a watcher: the whole board on the screen, in every
        mode; the plain board and the web page too."""
        for n in range(9, 21):
            lines = crowded(n)
            for me in (0, 1, -1):
                for mode, (text, _) in ansi_screens(self, F, lines, me, keys=["UP", "LEFT"]).items():
                    rows = text.split("\n")[:24]
                    p = 4 if n <= 10 else 3 if n <= 15 else 2         # columns per point
                    self.assertTrue(any(r.startswith(" %2d " % n) for r in rows), (n, mode))
                    self.assertTrue(any(r[5:6] == "a" and r[5 + p * (n - 1)] == chr(96 + n) for r in rows),
                                    (n, mode))
                    marks = ("[X]", "[O]") if mode == "ansi" else ("[█]", "[▒]")
                    self.assertTrue(marks[0] in text or marks[1] in text, (n, mode))
                plain = plain_screen(self, F, lines, me)
                self.assertIn(" %2d " % n, plain)
                # title, letters, the board (side text below a small one), clock, log, prompt: board and side
                # text in at most 20 lines
                self.assertLessEqual(len(plain.split("\n")), 2 + 1 + max(n, 10) + 1 + 4 + 1)
                web_page(self, F, lines, me)

    def test_stones_differ_in_shape(self):
        for mode in ("pc", "utf8", "ansi", "plain"):
            g = F.five_glyphs(mode)
            self.assertNotEqual(g["stone"][0], g["stone"][1])
            "".join([g[k] for k in F.POSITIONS + ("h", "v")] + list(g["stone"])).encode(
                "ascii" if mode in ("ansi", "plain") else "cp437")

    def test_arrows_enter_click_and_typing(self):
        # MIDGAME: black to move, the cursor on h8 (taken) moves to the nearest empty point first
        shown = ansi_screens(self, F, MIDGAME, 0, keys=["ENTER"])
        for mode, (text, reqs) in shown.items():
            self.assertEqual([r for r in reqs if r.startswith("MOV")], ["MOV 3 g7"], mode)
        shown = ansi_screens(self, F, MIDGAME, 0, keys=["RIGHT", "RIGHT", "DOWN", "ENTER"])
        for mode, (text, reqs) in shown.items():
            self.assertEqual([r for r in reqs if r.startswith("MOV")], ["MOV 3 i8"], mode)
        # Enter on a stone says so and sends nothing
        shown = ansi_screens(self, F, MIDGAME, 0, keys=["RIGHT", "DOWN", "ENTER"])
        for mode, (text, reqs) in shown.items():
            self.assertFalse([r for r in reqs if r.startswith("MOV")], mode)
            self.assertIn("h8 is taken", text)
        # a click on c2: 15 by 15 points sit 3 columns apart from column 5, rows from row 4
        shown = ansi_screens(self, F, MIDGAME, 0, keys=[("CLICK", 5 + 3 * 2, 4 + 1)])
        for mode, (text, reqs) in shown.items():
            self.assertIn("MOV 3 c2", reqs, mode)
        # a click on the narrow 19 by 19 board: 2 columns apart, from row 2
        shown = ansi_screens(self, F, view(19, ["j10"], [], last="j10"), 0, keys=[("CLICK", 5 + 2 * 18, 2 + 18)])
        for mode, (text, reqs) in shown.items():
            self.assertIn("MOV 3 s19", reqs, mode)
        # a typed point, in either case
        shown = ansi_screens(self, F, MIDGAME, 0, keys=list("K11") + ["ENTER"])
        for mode, (text, reqs) in shown.items():
            self.assertIn("MOV 3 k11", reqs, mode)
        # not your turn, or watching: nothing is sent
        for me in (1, -1):
            for mode, (text, reqs) in ansi_screens(self, F, MIDGAME, me, keys=["ENTER", ("CLICK", 11, 5)]).items():
                self.assertFalse([r for r in reqs if r.startswith("MOV")], (mode, me))

    def test_last_move_and_the_end(self):
        def over(g):
            g.over = F.describe_event("END win 0 h4-h8", g.names)
        for me in (0, 1, -1):
            for mode, (text, reqs) in ansi_screens(self, F, FINISHED, me, keys=["ENTER"], prepare=over).items():
                self.assertIn("Line h4 h5 h6 h7 h8", text)
                self.assertIn("wins with five in a row", text)
                self.assertFalse([r for r in reqs if r.startswith("MOV")])
                self.assertIn("[X]" if mode == "ansi" else "[█]", text)
            self.assertIn("Esc: menu", ansi_screens(self, F, FINISHED, me, prepare=over)["ansi"][0])  # fits row 21
            text = plain_screen(self, F, FINISHED, me, prepare=over)
            self.assertIn("  8  . . . . . . .[X]. . . . . . .", text)
            self.assertIn("Line h4 h5 h6 h7 h8", text)
            self.assertTrue(text.endswith("> "))

    def test_plain_typed_move(self):
        io = FakeIO("plain")
        core = ScreenCore(F.KEY, MIDGAME)
        s = session(io, core, "plain")
        g = F.PlainScreen(s, "3", F.KEY, 0)
        g.names, g.v = ["ann", "bob"], g.parse(MIDGAME)
        self.assertIn("Your move (like f7) > ", plain_screen(self, F, MIDGAME, 0))     # next to the last stone
        asyncio.run(g.command("J9"))
        self.assertIn("MOV 3 j9", core.reqs)
        g2 = F.PlainScreen(s, "3", F.KEY, -1)
        g2.names, g2.v = ["ann", "bob"], g2.parse(MIDGAME)
        core.reqs.clear()
        asyncio.run(g2.command("j9"))                  # a watcher cannot play
        self.assertNotIn("MOV 3 j9", core.reqs)


class WebPage(unittest.TestCase):
    def test_links_on_your_turn_only(self):
        html = web_page(self, F, MIDGAME, 0)
        mvs = re.findall(r"mv=([^&\"]+)", html)
        self.assertEqual(len(mvs), 15 * 15 - 5)          # every empty point
        self.assertTrue(all(F.MOVE_RE.match(m) for m in mvs))
        self.assertNotIn("h8", mvs)
        self.assertIn("g7", mvs)
        self.assertIn("click an empty point", html)
        self.assertIn('<input name="mv" size="4" maxlength="3">', html)      # or type the point
        used = [p for p, _, _ in board_image(self, F, MIDGAME, 0).used]
        self.assertEqual(sum(p.endswith("-y.gif") for p in used), 1)         # the last stone, marked
        self.assertIn("Move 5: g8", html)
        self.assertIn("Five or more in a row wins", html)
        for seat in (1, -1):
            html = web_page(self, F, MIDGAME, seat)
            self.assertNotIn("/move.htm", html)
            self.assertNotIn("click", html)

    def test_finished_and_extremes(self):
        html = web_page(self, F, FINISHED, 0)
        self.assertNotIn("/move.htm", html)
        used = [p for p, _, _ in board_image(self, F, FINISHED, 0).used]
        self.assertEqual(sum(p.endswith("-x.gif") for p in used), 5)         # the winning line is marked
        self.assertIn("Winning line: h4 h5 h6 h7 h8", html)
        html = web_page(self, F, view(19, turn=0), 0)
        self.assertEqual(html.count("<area "), 361)
        c = board_image(self, F, view(19, turn=0), 0)
        self.assertLessEqual(c.w, 640)                  # the widest board with its labels
        self.assertIn("/img/fiveinarow/star.gif", [p for p, _, _ in c.used])


class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port), "--rate", "1000",
                                      "--burst", "1000", "--auth", "none"], cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()

    async def test_a_whole_game(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO ann")
        await b.req("HELO bob")
        gam = await a.req("GAM")
        self.assertIn("GAM fiveinarow/1 freestyle Five_in_a_Row 2 2 nomoves", gam)
        self.assertIn("GAM fiveinarow/1 standard Five_in_a_Row 2 2 nomoves", gam)
        self.assertIn("BADOPT", (await a.req("NEW fiveinarow freestyle size=21"))[0])
        self.assertIn("NORULES", (await a.req("NEW fiveinarow renju"))[0])
        self.assertEqual((await a.req("NEW fiveinarow freestyle"))[0], "OK 1")
        self.assertEqual((await a.req("JON 1"))[0], "OK 0")
        self.assertEqual((await b.req("JON 1"))[0], "OK 1")
        v = await a.req("VUE 1 GMT")
        self.assertEqual(v[0].split()[:2], ["V", "fiveinarow/1"])
        for line in ("BRD 15 15", "RUL freestyle", "C 0 0", "W 0", "S Your move"):
            self.assertIn(line, v)
        self.assertFalse([l for l in v if l.startswith("M ")])           # nomoves: no move list
        self.assertEqual([l.rstrip() for l in await a.req("CHK 1 h8")], ["OK"])
        self.assertIn("ILLEGAL", (await b.req("MOV 1 h8"))[0])          # not bob's turn
        black, white = ["h4", "h5", "h6", "h7", "h8"], ["a1", "a2", "a3", "a4"]
        for i, pt in enumerate(black):
            r = await a.req("MOV 1 " + pt)
            self.assertEqual(r[-1].rstrip(), "OK", r)
            self.assertTrue(any(l.endswith("PUT 0 %s" % pt) for l in r), r)
            if i < len(white):
                self.assertTrue(any(l.endswith("TURN 1") for l in r), r)
                self.assertIn("taken", (await b.req("MOV 1 " + pt))[0])
                r = await b.req("MOV 1 " + white[i])
                self.assertEqual(r[-1].rstrip(), "OK", r)
        self.assertTrue(any(l.endswith("END win 0 h4-h8") for l in r), r)
        v = await b.req("VUE 1 GMT")
        self.assertIn("P 0 h4 h5 h6 h7 h8", v)
        self.assertIn("P 1 a1 a2 a3 a4", v)
        self.assertIn("F h4 h5 h6 h7 h8", v)
        self.assertIn("L 0 h8", v)
        self.assertIn("W -1", v)
        self.assertIn("S Game over: win 0 h4-h8", v)
        sin = await b.req("SIN 1 0")
        self.assertTrue(any(l.endswith("END win 0 h4-h8") for l in sin))
        self.assertIn("NOGAME", (await b.req("MOV 1 o15"))[0])
        # a small board
        self.assertEqual((await a.req("NEW fiveinarow standard size=9"))[0], "OK 2")
        await a.req("JON 2")
        await b.req("JON 2")
        v = await a.req("VUE 2 G")
        self.assertIn("BRD 9 9", v)
        self.assertIn("ILLEGAL", (await a.req("MOV 2 j1"))[0])
        self.assertEqual((await a.req("MOV 2 e5"))[-1].rstrip(), "OK")


if __name__ == "__main__":
    unittest.main()
