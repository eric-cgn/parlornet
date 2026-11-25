"""checkers/1: the plugin's rules, a whole game over the protocol, and the web and telnet frontends
(adapters/games/checkers.py) through tests/game_checks.py."""
import importlib.util
import os
import random
import re
import subprocess
import sys
import unittest

from game_checks import LONG, ansi_screens, board_image, module_problems, plain_screen, web_page
from test_core import ROOT, Client, free_port

from adapters.games import checkers as C  # noqa: E402

_spec = importlib.util.spec_from_file_location("ck_plugin", os.path.join(ROOT, "plugins", "checkers", "plugin.py"))
ck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ck)


def position(pieces, turn=0, limit=40):
    """A state with only the pieces given ({square: 'x', 'X', 'o' or 'O'})."""
    st = ck.new_state(limit)
    cells = ["."] * 32
    for s, ch in pieces.items():
        cells[s - 1] = ch
    st["cells"], st["turn"] = "".join(cells), turn
    return st


def moves(st, seat=None):
    return ck.op_moves({"state": st, "seat": st["turn"] if seat is None else seat})["moves"]


def apply(st, seat, *move):
    return ck.op_apply({"state": st, "seat": seat, "move": list(move)})


def play(st, seat, *move):
    r = apply(st, seat, *move)
    assert "error" not in r, (move, r)
    return r


def wire(st, seat=0):
    return {l.split()[0]: l.split()[1:] for l in ck.op_view({"state": st, "seat": seat})["wire"]}


def status(st, seat):
    return ck.op_view({"state": st, "seat": seat})["status"]


class Squares(unittest.TestCase):
    def test_standard_numbering(self):
        self.assertEqual((ck.rc(1), ck.rc(4), ck.rc(5), ck.rc(29), ck.rc(32)), ((0, 1), (0, 7), (1, 0), (7, 0), (7, 6)))
        for s in range(1, 33):
            self.assertEqual(ck.sq(*ck.rc(s)), s)
            self.assertEqual(C.sq(*C.rc(s)), s)
        self.assertEqual(ck.sq(0, 0), 0)        # the top-left corner is light: each player's left corner is dark
        self.assertEqual(ck.sq(8, 1), 0)


class Rules(unittest.TestCase):
    def test_start(self):
        r = ck.op_new({"players": 2, "options": {}, "seed": 1})
        st = r["state"]
        self.assertEqual(r["events"], ["TURN 0"])
        self.assertEqual(st["cells"], "x" * 12 + "." * 8 + "o" * 12)
        self.assertEqual(moves(st), ["9-13", "9-14", "10-14", "10-15", "11-15", "11-16", "12-16"])
        self.assertEqual(moves(st, 1), [])
        v = wire(st)
        self.assertEqual((v["B"], v["C"], v["W"], v["Q"], v["L"]), (
            ["x" * 12 + "." * 8 + "o" * 12], ["12", "0", "12", "0"], ["0"], ["move"], ["0", "80"]))
        self.assertNotIn("J", v)
        self.assertEqual(ck.op_view({"state": st, "seat": 0})["geometry"], ["BRD 8"])
        self.assertEqual(status(st, 0), "Your move")
        self.assertEqual(status(st, 1), "Waiting for the other player")
        self.assertEqual(status(st, -1), "Black to move")
        self.assertIn("error", ck.op_new({"players": 3, "options": {}}))
        self.assertIn("error", ck.op_new({"players": 2, "options": {"limit": "5"}}))
        self.assertIn("error", ck.op_new({"players": 2, "options": {"limit": "x"}}))
        self.assertEqual(wire(ck.op_new({"players": 2, "options": {"limit": "0"}})["state"])["L"], ["0", "0"])
        self.assertEqual(ck.op_info({})["flags"], ["staged"])

    def test_simple_move_is_one_staged_step(self):
        st = ck.op_new({"players": 2, "options": {}})["state"]
        r = play(st, 0, "11-15")
        self.assertEqual((r["events"], r["staged"]), (["STEP 0 11 15"], "done"))
        st = r["state"]
        self.assertEqual(moves(st), [])
        self.assertEqual((wire(st)["Q"], wire(st)["J"]), (["done"], ["11", "15"]))
        self.assertEqual(status(st, 0), "Turn complete: END to finish, UND to take back")
        self.assertIn("error", apply(st, 0, "15-19"))           # one move a turn
        r = play(st, 0, "!undo")
        self.assertEqual((r["events"], r["state"]["cells"]), (["UNDO 0 11 15 x"], ck.new_state()["cells"]))
        r = play(st, 0, "!end")
        self.assertEqual(r["events"], ["TURN 1"])
        self.assertEqual(moves(r["state"]), ["21-17", "22-17", "22-18", "23-18", "23-19", "24-19", "24-20"])

    def test_errors(self):
        st = ck.op_new({"players": 2, "options": {}})["state"]
        for m in ("!undo", "!end", "nonsense", "0-4", "11-33", "11-18", "11x15x"):
            self.assertIn("error", apply(st, 0, m), m)
        self.assertEqual(apply(st, 1, "22-18")["error"], "not your turn")
        self.assertIn("error", apply(st, 0))
        self.assertEqual(apply(st, 0, "11-15")["state"]["cells"][10], ".")
        self.assertEqual(st["cells"][10], "x")                  # the state given is never changed
        self.assertEqual(ck.op_apply({"state": st, "seat": 0, "move": ["11-15"], "check": True}), {"info": "ok"})

    def test_capture_is_compulsory(self):
        st = position({14: "x", 1: "x", 18: "o", 32: "o"})
        self.assertEqual(moves(st), ["14x23"])
        self.assertEqual(status(st, 0), "Your move: you must capture")
        err = apply(st, 0, "1-6")["error"]
        self.assertIn("compulsory", err)
        self.assertIn("14x23", err)
        r = play(st, 0, "14-23")                                 # from-to works for a jump too
        self.assertEqual(r["events"], ["JUMP 0 14 23 18"])
        self.assertEqual(r["state"]["cells"][17], ".")
        # any capturing piece and any line may be chosen
        st = position({9: "x", 10: "x", 14: "o", 15: "o", 32: "o"})
        self.assertEqual(moves(st), ["9x18", "10x17", "10x19"])

    def test_multi_jump_is_staged(self):
        st = position({9: "x", 14: "o", 23: "o", 32: "o"})
        self.assertEqual(moves(st), ["9x18"])
        r = play(st, 0, "9x18")
        self.assertEqual((r["events"], r["staged"]), (["JUMP 0 9 18 14"], "more"))
        mid = r["state"]
        self.assertEqual(moves(mid), ["18x27"])
        self.assertEqual((wire(mid)["J"], wire(mid)["Q"]), (["9", "18"], ["move"]))
        self.assertEqual(status(mid, 0), "Keep jumping")
        self.assertIn("go on", apply(mid, 0, "!end")["error"])
        self.assertIn("go on", apply(mid, 0, "18-22")["error"])
        self.assertIn("finish the turn", apply(mid, 0, "draw")["error"])
        self.assertIn("wait", apply(mid, 1, "draw")["error"])
        u = play(mid, 0, "!undo")
        self.assertEqual((u["events"], u["staged"]), (["UNDO 0 9 18 x 14 o"], "more"))
        self.assertEqual(u["state"]["cells"], st["cells"])
        r = play(mid, 0, "18x27")
        self.assertEqual((r["events"], r["staged"]), (["JUMP 0 18 27 23"], "done"))
        self.assertEqual(wire(r["state"])["J"], ["9", "18", "27"])
        e = play(r["state"], 0, "!end")
        self.assertEqual(e["events"], ["TURN 1"])
        self.assertEqual(wire(e["state"])["C"], ["1", "0", "1", "0"])
        # the whole line in one token, or in several tokens of one MOV
        for move in (["9x18x27"], ["9x18", "18x27"]):
            r = play(st, 0, *move)
            self.assertEqual((r["events"], r["staged"]), (["JUMP 0 9 18 14", "JUMP 0 18 27 23"], "done"))
        self.assertIn("error", apply(st, 0, "9x18x25"))          # all or none

    def test_crowning_ends_the_turn(self):
        st = position({22: "x", 26: "o", 27: "o", 4: "o"})
        self.assertEqual(moves(st), ["22x31"])
        r = play(st, 0, "22x31")
        self.assertEqual((r["events"], r["staged"]), (["JUMP 0 22 31 26", "CROWN 0 31"], "done"))
        self.assertEqual(r["state"]["cells"][30], "X")
        self.assertEqual(moves(r["state"]), [])                  # the new king could jump 27, but not now
        u = play(r["state"], 0, "!undo")
        self.assertEqual(u["events"], ["UNDO 0 22 31 x 26 o"])
        self.assertEqual(u["state"]["cells"], st["cells"])
        e = play(r["state"], 0, "!end")
        self.assertEqual(e["events"], ["TURN 1"])
        self.assertEqual(wire(e["state"])["C"], ["0", "1", "2", "0"])
        # a white man crowned by a simple move
        st = position({5: "o", 32: "x"}, turn=1)
        r = play(st, 1, "5-1")
        self.assertEqual(r["events"], ["STEP 1 5 1", "CROWN 1 1"])

    def test_kings_move_both_ways(self):
        self.assertEqual(moves(position({18: "X", 1: "o"})), ["18-14", "18-15", "18-22", "18-23"])
        self.assertEqual(moves(position({18: "x", 1: "o"})), ["18-22", "18-23"])
        self.assertEqual(moves(position({18: "o", 32: "x"}, turn=1)), ["18-14", "18-15"])
        self.assertEqual(moves(position({18: "X", 14: "o", 1: "o"})), ["18x9"])   # a king jumps backwards

    def test_draw_by_agreement(self):
        st = ck.op_new({"players": 2, "options": {}})["state"]
        r = play(st, 0, "draw")                                  # on the offerer's own turn
        self.assertEqual(r["events"], ["DRAW 0", "TURN 1"])
        off = r["state"]
        self.assertEqual((wire(off)["D"], wire(off)["Q"], wire(off)["W"]), (["0"], ["draw"], ["1"]))
        self.assertEqual(moves(off), ["accept", "reject"])
        self.assertEqual(status(off, 1), "Your opponent offers a draw: accept or reject")
        self.assertEqual(status(off, 0), "Waiting for the answer to your draw offer")
        self.assertIn("error", apply(off, 1, "22-18"))
        self.assertIn("error", apply(off, 0, "draw"))
        acc = play(off, 1, "accept")
        self.assertEqual((acc["events"], acc["end"]), (["ACCEPT 1"], "draw agreed"))
        self.assertEqual(status(acc["state"], 0), "Game over: a draw by agreement")
        self.assertEqual(ck.op_result({"state": acc["state"]}), {"over": True, "scores": [0.5, 0.5]})
        rj = play(off, 1, "reject")
        self.assertEqual(rj["events"], ["REJECT 1", "TURN 0"])
        st = rj["state"]
        self.assertNotIn("D", wire(st))
        self.assertIn("again", apply(st, 0, "draw")["error"])  # not again before the next move
        st = play(play(st, 0, "11-15")["state"], 0, "!end")["state"]
        r = play(st, 0, "draw")                                  # on the other player's turn: they answer it
        self.assertEqual(r["events"], ["DRAW 0"])
        rj = play(r["state"], 1, "reject")
        self.assertEqual(rj["events"], ["REJECT 1"])
        self.assertEqual(moves(rj["state"])[:1], ["21-17"])
        self.assertIn("error", apply(st, -1, "draw"))

    def test_draw_by_move_limit(self):
        st = position({29: "X", 4: "O"}, limit=10)
        self.assertEqual(wire(st)["L"], ["0", "20"])
        shuffle = {0: ("29-25", "25-29"), 1: ("4-8", "8-4")}
        end = None
        for turn in range(20):
            seat = turn % 2
            st = play(st, seat, shuffle[seat][(turn // 2) % 2])["state"]
            r = play(st, seat, "!end")
            st = r["state"]
            if "end" in r:
                end = (turn, r["end"], r["events"])
                break
            self.assertEqual(wire(st)["L"], [str(turn + 1), "20"])
        self.assertEqual(end, (19, "draw limit", []))
        self.assertTrue(status(st, 0).startswith("Game over: a draw"))
        # a man moving or a capture starts the count again
        st = position({29: "X", 4: "O", 9: "x"}, limit=10)
        st["quiet"] = 7
        st = play(play(st, 0, "9-13")["state"], 0, "!end")["state"]
        self.assertEqual(st["quiet"], 0)

    def test_game_ends_when_a_side_cannot_move(self):
        st = position({14: "x", 18: "o"})
        r = play(st, 0, "14x23")
        self.assertEqual((r["events"], r["end"]), (["JUMP 0 14 23 18"], "win 0 captured"))
        self.assertNotIn("staged", r)
        done = r["state"]
        self.assertEqual((wire(done)["W"], wire(done)["Q"]), (["-"], ["over"]))
        self.assertEqual(status(done, 1), "Game over: Black wins: White has no pieces left")
        self.assertEqual(moves(done, 0), [])
        self.assertIn("error", apply(done, 1, "draw"))
        self.assertEqual(ck.op_result({"state": done}), {"over": True, "scores": [1, 0]})
        self.assertEqual(ck.op_result({"state": st}), {"over": False})
        # blocked: White's last man on 5 cannot go to 1
        r = play(position({6: "X", 5: "o"}), 0, "6-1")
        self.assertEqual(r["end"], "win 0 blocked")
        self.assertIn("cannot move", status(r["state"], 0))

    def test_random_games_finish(self):
        results = set()
        for seed in range(60):
            rnd = random.Random(seed)
            st, end = ck.op_new({"players": 2, "options": {}})["state"], None
            for _ in range(3000):
                seat = st["turn"]
                ms = moves(st)
                r = play(st, seat, rnd.choice(ms) if ms else "!end")
                before, st = st, r["state"]
                self.assertLessEqual(sum(ck.counts(st["cells"])), sum(ck.counts(before["cells"])))
                for line in ck.op_view({"state": st, "seat": seat})["wire"]:
                    self.assertLessEqual(len(line), 76)
                if "end" in r:
                    end = r["end"]
                    break
            self.assertRegex(end or "", r"^(win [01] (captured|blocked)|draw limit)$", seed)
            results.add(end.split()[0])
        self.assertIn("win", results)


class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port), "--rate", "100000",
                                      "--burst", "100000", "--auth", "none"], cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()

    async def test_a_whole_game(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO kim")
        await b.req("HELO lou")
        self.assertIn("GAM checkers/1 american Checkers 2 2 staged", await a.req("GAM"))
        self.assertIn("NORULES", (await a.req("NEW checkers standard"))[0])
        self.assertEqual((await a.req("NEW checkers american"))[0], "OK 1")
        await a.req("JON 1")
        await b.req("JON 1")
        v = await a.req("VUE 1 GMT")
        self.assertEqual(v[0].split()[:2], ["V", "checkers/1"])
        for line in ("BRD 8", "B " + "x" * 12 + "." * 8 + "o" * 12, "W 0", "Q move", "M 9-13", "M 12-16",
                     "S Your move"):
            self.assertIn(line, v)
        self.assertIn("ILLEGAL", (await b.req("MOV 1 22-18"))[0])
        # a step, taken back, played again and committed
        r = await a.req("MOV 1 11-15")
        self.assertEqual(r[-1], "OK done")
        self.assertTrue(any(l.endswith(" STEP 0 11 15") for l in r), r)
        r = await a.req("UND 1")
        self.assertTrue(any(l.endswith(" UNDO 0 11 15 x") for l in r), r)
        self.assertIn("ILLEGAL", (await a.req("END 1"))[0])
        self.assertEqual((await a.req("MOV 1 11-15"))[-1], "OK done")
        r = await a.req("END 1")
        self.assertTrue(any(l.endswith(" TURN 1") for l in r), r)
        # then both sides play on to the end, by random legal steps
        who, rnd, end, jumps = {0: a, 1: b}, random.Random(5), None, 0
        for _ in range(2000):
            v = await a.req("VUE 1")
            turn = [l.split()[1] for l in v if l.startswith("W ")][0]
            if turn == "-":
                break
            c = who[int(turn)]
            v = await c.req("VUE 1 M")
            ms = [l[2:] for l in v if l.startswith("M ")]
            if "Q done" in v:
                r = await c.req("END 1")
            else:
                m = rnd.choice(ms)
                jumps += "x" in m
                r = await c.req("MOV 1 " + m)
            self.assertTrue(r[-1].startswith("OK"), r)
            ends = [l for l in r if " END " in l]
            if ends:
                end = ends[0]
                break
        self.assertRegex(end or "", r" END (win [01] (captured|blocked)|draw limit)$")
        self.assertGreater(jumps, 0)
        self.assertIn("NOGAME", (await a.req("MOV 1 9-13"))[0])
        # a second table: a draw offered and accepted
        self.assertEqual((await b.req("NEW checkers american limit=20"))[0], "OK 2")
        await b.req("JON 2")
        await a.req("JON 2")
        r = await b.req("MOV 2 draw")                # lou (seat 0) offers on his own turn
        self.assertTrue(any(l.endswith(" DRAW 0") for l in r) and any(l.endswith(" TURN 1") for l in r), r)
        self.assertIn("M accept", await a.req("VUE 2 M"))
        r = await a.req("MOV 2 accept")
        self.assertTrue(any(l.endswith(" END draw agreed") for l in r), r)
        self.assertIn("BADOPT", (await a.req("NEW checkers american limit=3"))[0])


# ---- the frontends ----------------------------------------------------------------------------------

def view_lines(st, seat, seq=9, clock=True):
    """What VUE 3 GMT would answer for seat."""
    r = ck.op_view({"state": st, "seat": seat})
    out = ["V checkers/1 %d" % seq] + r["geometry"] + r["wire"]
    if clock and not st["over"]:
        out.append("CLK %d 250 300" % st["turn"])
    out.append("S " + r["status"])
    if seat >= 0:
        out += ["M " + m for m in moves(st, seat)]
    return out


WAITING = ["V checkers/1 1", "S Waiting for players"]
OPENING = ck.op_new({"players": 2, "options": {}})["state"]
JUMPING = play(position({9: "x", 14: "o", 23: "o", 32: "o", 1: "x"}), 0, "9x18")["state"]
DONE = play(OPENING, 0, "11-15")["state"]
OFFER = play(OPENING, 0, "draw")["state"]
OVER = play(position({14: "x", 18: "o"}), 0, "14x23")["state"]


def crowded():
    """Kings everywhere, a long count to the move limit and a draw offered on the other player's turn."""
    st = position({s: "XO"[s % 2] for s in range(1, 33) if s % 4 != 0}, turn=1, limit=200)
    st["quiet"] = 399
    st = play(st, 0, "draw")["state"]
    return st


CROWDED = crowded()


def disp_xy(s, me):
    """The screen cell of square s's piece, as the full screen draws it for seat me."""
    r, c = C.rc(s)
    if me == 0:
        r, c = 7 - r, 7 - c
    return C.BX + 1 + 5 * c + 2, C.BY + 1 + 2 * r


class Module(unittest.TestCase):
    def test_registry_and_images(self):
        self.assertEqual(module_problems(C), [])
        imgs = C.images()
        self.assertEqual(len(imgs), 1 + 64 + 12)
        self.assertTrue(all(p.startswith("/img/checkers/") for p in imgs))

    def test_parse_view(self):
        self.assertFalse(C.parse_view(WAITING)["rows"])
        v = C.parse_view(view_lines(JUMPING, 0))
        self.assertEqual((v["turn"], v["phase"], v["path"], v["moves"], v["n"]), (0, "move", [9, 18], ["18x27"], 8))
        self.assertEqual(v["rows"][0], " x . . .")
        self.assertEqual(v["rows"][4], " . x . .")
        self.assertEqual(v["clock"], (0, 250, 300))
        v = C.parse_view(view_lines(OVER, 1))
        self.assertEqual((v["turn"], v["phase"]), (-1, "over"))
        self.assertTrue(v["status"].startswith("Game over"))
        v = C.parse_view(view_lines(OFFER, 1))
        self.assertEqual((v["offer"], v["moves"]), (0, ["accept", "reject"]))

    def test_describe_event(self):
        names = ["ann", "bob"]
        for text, want in (("STEP 0 11 15", "ann moves 11-15"), ("JUMP 1 22 15 18", "bob jumps 22x15, taking 18"),
                           ("CROWN 0 30", "ann is crowned on 30"), ("UNDO 0 11 15 x", "ann takes back 11-15"),
                           ("UNDO 1 22 15 o 18 x", "bob takes back 22x15"), ("DRAW 1", "bob offers a draw"),
                           ("ACCEPT 0", "ann accepts the draw"), ("REJECT 0", "ann declines the draw"),
                           ("END win 1 captured", "Game over: bob wins (ann has no pieces left)"),
                           ("END win 0 blocked", "Game over: ann wins (bob cannot move)"),
                           ("END draw agreed", "Game over: a draw by agreement")):
            self.assertEqual(C.describe_event(text, names), want)
        self.assertIn("move limit", C.describe_event("END draw limit", names))
        for text in ("TURN 1", "END resign 0", "PUT 0 d3 FLP d4", "CHT 0 hi"):
            self.assertIsNone(C.describe_event(text, names))
        self.assertEqual(C.describe_event("STEP 1 22 18", ["ann", "-"]), "White moves 22-18")


class Web(unittest.TestCase):
    def mvs(self, html):
        return re.findall(r"mv=([^&\"]+)", html)

    def test_pick_then_target(self):
        html = web_page(self, C, view_lines(OPENING, 0), 0)
        c = board_image(self, C, view_lines(OPENING, 0), 0)
        for s in (9, 10, 11, 12):
            self.assertIn("sel=%d" % s, html)
        self.assertEqual([p for p, _, _ in c.used].count("/img/checkers/bmc.gif"), 4)
        self.assertIn("Moves: ", html)
        self.assertIn("mv=draw", html)
        self.assertIn("click a piece with a yellow rim", html)
        html = web_page(self, C, view_lines(OPENING, 0), 0, sel="11")
        used = [p for p, _, _ in board_image(self, C, view_lines(OPENING, 0), 0, sel="11").used]
        self.assertIn("/img/checkers/bms.gif", used)
        self.assertIn("/img/checkers/t15.gif", used)
        self.assertIn("/img/checkers/t16.gif", used)
        self.assertIn("/img/checkers/d20.gif", used)
        for mv in self.mvs(html):
            self.assertTrue(C.MOVE_RE.match(mv), mv)
        self.assertIn("11-15", self.mvs(html))
        # Black sees the board turned round: its own men at the bottom, square 32 top left

        def top(c, prefix):
            return min(y for p, _, y in c.used if p.startswith("/img/checkers/" + prefix))
        c = board_image(self, C, view_lines(OPENING, 0), 0)
        self.assertLess(top(c, "wm"), top(c, "bm"))
        c = board_image(self, C, view_lines(OPENING, 1), 1)
        self.assertLess(top(c, "bm"), top(c, "wm"))
        html = web_page(self, C, view_lines(OPENING, 1), 1)
        self.assertNotIn("sel=", html)
        self.assertIn("mv=draw", html)                          # the other player may offer one too
        self.assertNotIn("mv=draw", web_page(self, C, view_lines(OPENING, -1), -1))

    def test_staged_jump_undo_and_end(self):
        html = web_page(self, C, view_lines(JUMPING, 0), 0)
        self.assertIn("keep jumping", html)
        self.assertIn("mv=18x27", html)
        self.assertIn("/img/checkers/bms.gif",                  # the jumping piece stays picked
                      [p for p, _, _ in board_image(self, C, view_lines(JUMPING, 0), 0).used])
        self.assertIn("/und.htm", html)
        self.assertNotIn("/end.htm", html)
        self.assertNotIn("mv=draw", html)
        self.assertIn("Jumping: 9x18", html)
        html = web_page(self, C, view_lines(DONE, 0), 0)
        self.assertIn("/end.htm", html)
        self.assertIn("/und.htm", html)
        self.assertIn("End turn", html)
        self.assertNotIn("/und.htm", web_page(self, C, view_lines(DONE, 1), 1))

    def test_draw_offer_and_game_over(self):
        html = web_page(self, C, view_lines(OFFER, 1), 1)
        self.assertIn("mv=accept", html)
        self.assertIn("mv=reject", html)
        self.assertIn("offers a draw", html)
        self.assertNotIn("mv=accept", web_page(self, C, view_lines(OFFER, 0), 0))
        html = web_page(self, C, view_lines(OVER, 0), 0)
        self.assertEqual(self.mvs(html), [])
        self.assertNotIn("/und.htm", html)
        html = web_page(self, C, view_lines(CROWDED, 0), 0, names=(LONG, LONG.upper()))
        self.assertIn("/img/checkers/wk.gif", [p for p, _, _ in board_image(self, C, view_lines(CROWDED, 0), 0).used])
        self.assertIn("399 of 400 turns", html)


class Telnet(unittest.TestCase):
    def test_screens_for_each_seat(self):
        for me in (0, 1, -1):
            for st in (OPENING, JUMPING, DONE, OFFER, OVER, CROWDED):
                for mode, (shown, reqs) in ansi_screens(self, C, view_lines(st, me), me, keys=["LEFT", "UP"]).items():
                    self.assertIn("Checkers", shown)
                    self.assertNotIn("Waiting for an opponent", shown)
                    self.assertIn(("(█)" if mode != "ansi" else "(x)"), shown, (mode, me))
                    self.assertFalse([r for r in reqs if r.split()[0] in ("MOV", "UND", "END")], reqs)
                text = plain_screen(self, C, view_lines(st, me), me)
                self.assertIn("(x)", text)
        shown = ansi_screens(self, C, view_lines(CROWDED, 0), 0)["pc"][0]
        self.assertIn("«▒»", shown)               # a white king
        self.assertIn("No capture or man move: 399/400", shown)
        self.assertIn("to answer the draw", shown)

    def test_keys_pick_and_put_down(self):
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=["ENTER", "ENTER"])["pc"]
        self.assertIn("MOV 3 11-15", reqs)                       # the piece under the cursor, its first target
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=["RIGHT", "ENTER", "ENTER"])["ansi"]
        self.assertIn("MOV 3 10-14", reqs)                       # Black's board is turned round: right is 10
        shown, _ = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=["ENTER", "ESC"])["utf8"]
        self.assertIn("Enter plays 11-15", shown)
        self.assertIn("»11«", shown)                   # the picked piece's number
        # a cursor off the movable pieces goes to the first of them when the turn starts
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=["ENTER", "ENTER"],
                               prepare=lambda g: setattr(g, "cur", 30))["pc"]
        self.assertIn("MOV 3 9-13", reqs)

    def test_click_and_typed_moves(self):
        x0, y0 = disp_xy(11, 0)
        x1, y1 = disp_xy(15, 0)
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0,
                               keys=[("CLICK", x0, y0), ("CLICK", x1, y1)])["pc"]
        self.assertIn("MOV 3 11-15", reqs)
        x, y = disp_xy(27, 0)
        _, reqs = ansi_screens(self, C, view_lines(JUMPING, 0), 0, keys=[("CLICK", x, y)])["ansi"]
        self.assertIn("MOV 3 18x27", reqs)
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=list("11-15") + ["ENTER"])["pc"]
        self.assertIn("MOV 3 11-15", reqs)
        _, reqs = ansi_screens(self, C, view_lines(JUMPING, 0), 0, keys=list("18x27") + ["ENTER"])["pc"]
        self.assertIn("MOV 3 18x27", reqs)

    def test_hotkeys(self):
        cases = [(DONE, 0, ["e", "WAIT"], "END 3"), (DONE, 0, ["ENTER"], "END 3"), (DONE, 0, ["u", "WAIT"], "UND 3"),
                 (JUMPING, 0, ["u", "WAIT"], "UND 3"), (OFFER, 1, ["a", "WAIT"], "MOV 3 accept"),
                 (OFFER, 1, ["n", "WAIT"], "MOV 3 reject"), (OPENING, 0, ["d", "WAIT", "y"], "MOV 3 draw"),
                 (OPENING, 1, ["d", "WAIT", "y"], "MOV 3 draw"), (DONE, 0, list("end") + ["ENTER"], "END 3"),
                 (OFFER, 1, list("accept") + ["ENTER"], "MOV 3 accept")]
        for st, me, keys, want in cases:
            _, reqs = ansi_screens(self, C, view_lines(st, me), me, keys=keys)["pc"]
            self.assertIn(want, reqs, (keys, want))
        _, reqs = ansi_screens(self, C, view_lines(OPENING, 0), 0, keys=["d", "WAIT", "n", "e", "WAIT"])["pc"]
        self.assertFalse([r for r in reqs if r.startswith(("MOV", "END"))], reqs)
        _, reqs = ansi_screens(self, C, view_lines(OPENING, -1), -1, keys=["ENTER", "d", "WAIT"])["pc"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")], reqs)

    def test_hints_and_status_bar(self):
        shown = ansi_screens(self, C, view_lines(JUMPING, 0), 0)["pc"][0]
        self.assertIn("Keep jumping from 18", shown)
        self.assertIn("U undo", shown)
        shown = ansi_screens(self, C, view_lines(DONE, 0), 0)["ansi"][0]
        self.assertIn("E end turn", shown)
        shown = ansi_screens(self, C, view_lines(OFFER, 1), 1)["ansi"][0]
        self.assertIn("A accept", shown)
        self.assertIn("N decline", shown)
        shown = ansi_screens(self, C, view_lines(position({14: "x", 1: "x", 18: "o", 32: "o"}), 0), 0)["pc"][0]
        self.assertIn("you must capture", shown)
        shown = ansi_screens(self, C, view_lines(OVER, 1), 1)["pc"][0]
        self.assertIn("Game over", shown)
        self.assertIn("pieces left", shown)                     # long state texts wrap in the panel
        shown = ansi_screens(self, C, view_lines(OFFER, 1), 1)["pc"][0]
        self.assertIn("abcdefghijkl offers a draw:", shown)
        self.assertIn("  accept?", shown)

    def test_plain(self):
        text = plain_screen(self, C, view_lines(OPENING, 0), 0)
        self.assertTrue(text.endswith("Your move (like 9-13) > "))
        self.assertIn("Moves: 9-13 9-14", text)
        self.assertIn("Black: 12 men, 0 kings", text)
        self.assertLessEqual(len(text.split("\n")), 30)
        self.assertTrue(plain_screen(self, C, view_lines(JUMPING, 0), 0).endswith("Keep jumping (like 18x27) > "))
        self.assertTrue(plain_screen(self, C, view_lines(DONE, 0), 0).endswith("Turn complete: end or undo > "))
        self.assertTrue(plain_screen(self, C, view_lines(OFFER, 1), 1).endswith("Accept or reject the draw > "))
        self.assertIn("[O]", plain_screen(self, C, view_lines(CROWDED, 1), 1))
        self.assertIn("Waiting for an opponent", plain_screen(self, C, WAITING, 0))
        self.assertIn("no pieces left", plain_screen(self, C, view_lines(OVER, 0), 0))
        g = C.PlainScreen.__new__(C.PlainScreen)
        for word, want in (("11-15", "11-15"), ("15x22x31", "15x22x31"), ("undo", "undo"), ("decline", "reject"),
                           ("hello", None), ("d3", None)):
            self.assertEqual(g.move_word(word), want)


if __name__ == "__main__":
    unittest.main()
