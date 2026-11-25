"""dotsandboxes/1: the plugin's rules, a whole game over the protocol, and the web and telnet screens."""
import importlib.util
import os
import random
import re
import subprocess
import sys
import unittest
import asyncio

from game_checks import LONG, FakeIO, ScreenCore, ansi_screens, board_image, plain_screen, session, web_page
from test_core import ROOT, Client, free_port

from adapters import games  # noqa: E402
from adapters import web as W  # noqa: E402
from adapters.games import dotsandboxes as D  # noqa: E402

_spec = importlib.util.spec_from_file_location("dots_plugin", os.path.join(ROOT, "plugins", "dotsandboxes", "plugin.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

SIZES = [(w, w) for w in range(3, 10)] + [(3, 9), (9, 3), (6, 4)]


def new(size="5", players=2):
    return P.op_new({"players": players, "options": {"size": size}, "seed": 1})["state"]


def play(st, *lines):
    """Play lines in order, each by the seat to move; -> the last apply's reply."""
    r = None
    for ln in lines:
        r = P.op_apply({"state": st, "seat": st["turn"], "move": [ln]})
        assert "error" not in r, (ln, r)
        st = r["state"]
    return r


def random_state(w, h, plies, seed=0, players=2):
    st = new("%dx%d" % (w, h), players)
    rnd = random.Random(seed)
    for _ in range(plies):
        mv = P.op_moves({"state": st, "seat": st["turn"]})["moves"]
        r = P.op_apply({"state": st, "seat": st["turn"], "move": [rnd.choice(mv)]})
        st = r["state"]
        if st["over"]:
            break
    return st


def view_lines(st, seat, seq=40, clock=True):
    """The VUE GMT reply for seat, as the core builds it from the plugin."""
    r = P.op_view({"state": st, "seat": seat})
    out = ["V dotsandboxes/1 %d" % seq] + r["geometry"] + r["wire"]
    if clock and not st["over"]:
        out.append("CLK %d 3599 86400" % st["turn"])
    out.append("S " + r["status"])
    if seat >= 0:
        out += ["M " + m for m in P.op_moves({"state": st, "seat": seat})["moves"]]
    return out


class Rules(unittest.TestCase):
    def test_new_and_sizes(self):
        st = P.op_new({"players": 2, "options": {}, "seed": 0})["state"]
        self.assertEqual((st["w"], st["h"], st["turn"]), (5, 5, 0))
        self.assertEqual(len(P.op_moves({"state": st, "seat": 0})["moves"]), 60)
        self.assertEqual(P.op_moves({"state": st, "seat": 1})["moves"], [])
        self.assertEqual(P.op_new({"players": 2, "options": {}, "seed": 0})["events"], ["TURN 0"])
        for size, wh in (("3", (3, 3)), ("9", (9, 9)), ("6x4", (6, 4)), ("3X9", (3, 9))):
            st = new(size)
            self.assertEqual((st["w"], st["h"]), wh)
            self.assertEqual(len(P.open_lines(st)), wh[0] * (wh[1] + 1) + wh[1] * (wh[0] + 1))
        for bad in ("2", "10", "5x10", "x", "5x", "-5"):
            self.assertIn("error", P.op_new({"players": 2, "options": {"size": bad}, "seed": 0}), bad)
        for n in (1, 5, None):
            self.assertIn("error", P.op_new({"players": n, "options": {}, "seed": 0}))
        info = P.op_info({})
        self.assertEqual((info["type"], info["ver"], info["name"], info["min"], info["max"]),
                         ("dotsandboxes", 1, "Dots and Boxes", 2, 4))
        self.assertEqual(info["flags"], ["drop"])
        self.assertEqual(info["name"], D.NAME)

    def test_majority(self):
        """The majority ruleset: 13 by 11 for two players, and the game ends when one player holds more than
        half the boxes (72 of 143), as GameMaster's Dots&Boxes does."""
        self.assertEqual(P.op_info({})["rules"], ["standard", "majority"])
        r = P.op_new({"players": 2, "options": {}, "seed": 0, "rules": "majority"})
        st = r["state"]
        self.assertEqual((st["w"], st["h"], st["rules"], r["events"]), (13, 11, "majority", ["TURN 0"]))
        self.assertIn("BRD 13 11", P.op_view({"state": st, "seat": 0})["geometry"])
        self.assertEqual(len(P.open_lines(st)), 13 * 12 + 11 * 14)
        self.assertEqual(P.op_new({"players": 2, "options": {"size": "13x11"}, "rules": "majority"})["state"]["w"],
                         13)
        for opts, n in (({"size": "5"}, 2), ({}, 3), ({}, 4)):
            self.assertIn("error", P.op_new({"players": n, "options": opts, "rules": "majority"}))
        self.assertIn("error", P.op_new({"players": 2, "options": {}, "rules": "huge"}))
        self.assertEqual(new()["rules"], "standard")
        r = P.op_apply({"state": st, "seat": 0, "move": ["m12-n12"]})          # the bottom right corner
        self.assertEqual(r["events"], ["LINE 0 m12-n12", "TURN 1"])
        # seat 0 has 71 boxes and three sides of box n11 (the last box); its fourth side ends the game
        st = dict(st, bx="0" * 71 + "." * 72)
        for tok in ("m11-n11", "m11-m12", "m12-n12"):
            st = P.op_apply({"state": st, "seat": st["turn"], "move": [tok]})["state"]
        st = dict(st, turn=0)
        r = P.op_apply({"state": st, "seat": 0, "move": ["n11-n12"]})
        self.assertEqual(r["events"], ["LINE 0 n11-n12 BOX m11"])
        self.assertEqual((r["end"], r["state"]["over"]), ("win 0 72-0", True))
        self.assertEqual(P.op_result({"state": r["state"]}), {"over": True, "scores": [72, 0]})
        # the same position under the standard rules plays on
        r = P.op_apply({"state": dict(st, rules="standard"), "seat": 0, "move": ["n11-n12"]})
        self.assertNotIn("end", r)
        self.assertEqual(r["events"], ["LINE 0 n11-n12 BOX m11", "TURN 0"])
        # random games end at a majority or with every box taken
        for seed in range(3):
            rnd = random.Random(seed)
            st = P.op_new({"players": 2, "options": {}, "seed": 0, "rules": "majority"})["state"]
            while not st["over"]:
                mv = P.op_moves({"state": st, "seat": st["turn"]})["moves"]
                st = P.op_apply({"state": st, "seat": st["turn"], "move": [rnd.choice(mv)]})["state"]
            self.assertGreater(max(P.counts(st)), 71)
            self.assertIn(".", st["bx"])            # it ended before the board was full

    def test_line_tokens(self):
        st = new("3")
        for tok in ("a1-b1", "b1-a1", "A1B1", "a1b1"):
            r = P.op_apply({"state": st, "seat": 0, "move": [tok]})
            self.assertEqual(r["events"], ["LINE 0 a1-b1", "TURN 1"], tok)
        r = P.op_apply({"state": st, "seat": 0, "move": ["d4", "d3"]})
        self.assertEqual(r["events"][0], "LINE 0 d3-d4")
        for tok, why in (("a1-c1", "neighbors"), ("a1-b2", "neighbors"), ("a1-e1", "no such dot"),
                         ("a5-a4", "no such dot"), ("a1", "two dots"), ("pass", "two dots")):
            self.assertIn(why, P.op_apply({"state": st, "seat": 0, "move": [tok]})["error"], tok)
        self.assertIn("not your turn", P.op_apply({"state": st, "seat": 1, "move": ["a1-b1"]})["error"])
        st = play(st, "a1-b1")["state"]
        self.assertIn("drawn already", P.op_apply({"state": st, "seat": 1, "move": ["b1-a1"]})["error"])
        self.assertIn("one line", P.op_apply({"state": st, "seat": 1, "move": ["a1", "b1", "c1"]})["error"])
        self.assertNotIn("a1-b1", P.op_moves({"state": st, "seat": 1})["moves"])

    def test_box_scores_and_moves_again(self):
        st = new("3")
        st = play(st, "a1-b1", "a1-a2", "b1-b2")["state"]            # three sides of box a1
        self.assertEqual(st["turn"], 1)
        r = P.op_apply({"state": st, "seat": 1, "move": ["a2-b2"], "check": True})
        self.assertEqual(r, {"info": "boxes 1"})
        r = P.op_apply({"state": st, "seat": 1, "move": ["a2-b2"]})
        self.assertEqual(r["events"], ["LINE 1 a2-b2 BOX a1", "TURN 1"])     # the same seat again
        st = r["state"]
        self.assertEqual((st["turn"], st["again"], P.counts(st)), (1, True, [0, 1]))
        self.assertEqual(P.op_view({"state": st, "seat": 1})["status"], "Your move again: you closed a box")
        self.assertEqual(P.op_view({"state": st, "seat": 0})["status"], "Waiting for the other player")
        self.assertEqual(P.op_view({"state": st, "seat": -1})["status"], "Seat 1 to move")
        st = play(st, "c1-c2")["state"]                                   # closes nothing: the turn passes
        self.assertEqual((st["turn"], st["again"]), (0, False))

    def test_one_line_closes_two_boxes(self):
        st = new("3")
        st = play(st, "a1-b1", "b1-c1", "a2-b2", "b2-c2", "a1-a2", "c1-c2")["state"]
        r = P.op_apply({"state": st, "seat": st["turn"], "move": ["b1-b2"]})
        self.assertEqual(r["events"], ["LINE 0 b1-b2 BOX a1 b1", "TURN 0"])
        self.assertEqual(P.counts(r["state"]), [2, 0])
        wire = P.op_view({"state": r["state"], "seat": 0})["wire"]
        self.assertIn("BX 1 00.", wire)
        self.assertIn("HL 1 --.", wire)
        self.assertIn("VL 1 |||.", wire)
        self.assertIn("C 2 0", wire)
        self.assertIn("LL b1-b2", wire)
        self.assertIn("W 0", wire)

    def test_end_and_winner(self):
        st = new("3")
        lines = P.open_lines(st)
        r = None
        for ln in lines:                    # every line in order, top row first
            r = P.op_apply({"state": st, "seat": st["turn"], "move": [ln]})
            st = r["state"]
        self.assertTrue(st["over"])
        self.assertNotIn("TURN", " ".join(r["events"]))     # no TURN after the last line
        self.assertEqual(sum(P.counts(st)), 9)
        self.assertRegex(r["end"], r"^(win [01] \d-\d|draw \d-\d)$")
        self.assertEqual(r["end"], P.result_text(st))
        self.assertEqual(P.op_result({"state": st}), {"over": True, "scores": P.counts(st)})
        self.assertIn("over", P.op_apply({"state": st, "seat": 0, "move": ["a1-b1"]})["error"])
        self.assertEqual(P.op_moves({"state": st, "seat": st["turn"]})["moves"], [])
        v = P.op_view({"state": st, "seat": 0})
        self.assertIn("W -", v["wire"])
        self.assertTrue(v["status"].startswith("Game over: "))
        st["bx"], st["n"] = "000011111", 2
        self.assertEqual(P.result_text(st), "win 1 4-5")
        st["bx"], st["n"] = "000011112", 3
        self.assertEqual(P.result_text(st), "draw 4-4-1")
        self.assertEqual(P.op_result({"state": new("3")}), {"over": False})

    def test_three_and_four_players(self):
        st = new("3", players=3)
        r = play(st, "a1-b1", "b1-c1", "c1-d1")
        self.assertEqual(r["events"][-1], "TURN 0")
        st = new("4", players=4)
        seen = []
        for ln in ("a1-b1", "b1-c1", "c1-d1", "d1-e1", "a2-b2"):
            seen.append(st["turn"])
            st = play(st, ln)["state"]
        self.assertEqual(seen, [0, 1, 2, 3, 0])
        self.assertEqual(len([l for l in P.op_view({"state": st, "seat": 0})["wire"] if l.startswith("C ")][0].split()),
                         5)
        st = random_state(4, 4, 999, seed=3, players=4)
        self.assertTrue(st["over"])
        self.assertRegex(P.result_text(st), r"^(win [0-3]|draw) \d+-\d+-\d+-\d+$")

    def test_a_seat_leaves(self):
        """op_drop: with three or four playing, a seat that resigns or runs out of time leaves; its turn is
        skipped, its boxes stay its own, it cannot win, and the last seat left wins."""
        st = new("3", players=3)
        st = play(st, "a1-b1", "a1-a2", "b1-b2")["state"]             # three sides of box a1; seat 0 to move
        st = play(st, "a2-b2")["state"]                                # seat 0 takes a1 and moves again
        self.assertEqual((st["turn"], st["again"], P.counts(st)), (0, True, [1, 0, 0]))
        r = P.op_drop({"state": st, "seat": 0, "why": "timeout"})
        self.assertEqual(r["events"], ["GONE 0 timeout", "TURN 1"])
        self.assertNotIn("end", r)
        st = r["state"]
        self.assertEqual((st["turn"], st["again"], st["gone"]), (1, False, [True, False, False]))
        self.assertIn("G 1 0 0", P.op_view({"state": st, "seat": 1})["wire"])
        self.assertIn("C 1 0 0", P.op_view({"state": st, "seat": 1})["wire"])      # its box stays its own
        self.assertEqual(P.op_view({"state": st, "seat": 0})["status"], "You have left this game; seat 1 to move")
        self.assertEqual(P.op_view({"state": st, "seat": 2})["status"], "Waiting for seat 1")
        self.assertEqual(P.op_moves({"state": st, "seat": 0})["moves"], [])
        self.assertIn("error", P.op_drop({"state": st, "seat": 0}))     # already gone
        self.assertIn("error", P.op_drop({"state": st, "seat": 3}))
        st = play(st, "c1-d1")["state"]                                 # seat 1, then 2, then 1: 0 is skipped
        self.assertEqual(st["turn"], 2)
        st = play(st, "c3-d3")["state"]
        self.assertEqual(st["turn"], 1)
        # a seat not on turn leaves: no TURN; one seat left wins, whatever its boxes
        r = P.op_drop({"state": st, "seat": 2})
        self.assertEqual(r["events"], ["GONE 2 resign"])
        self.assertTrue(r["state"]["over"])
        self.assertEqual(r["end"], "win 1 1-0-0")
        self.assertIn("error", P.op_drop({"state": r["state"], "seat": 1}))
        self.assertIn("over", P.op_apply({"state": r["state"], "seat": 1, "move": ["a4-b4"]})["error"])
        # played to the end: the most boxes among the seats still playing wins
        st = new("3", players=4)
        st = P.op_drop({"state": st, "seat": 3, "why": "resign"})["state"]
        st["bx"] = "33333333."
        self.assertEqual(P.result_text(st), "draw 0-0-0-8")       # a tie of the seats playing
        st["bx"] = "33333300."
        self.assertEqual(P.result_text(st), "win 0 2-0-0-6")
        st["bx"] = "33333301."
        self.assertEqual(P.result_text(st), "draw 1-1-0-6")
        # a game with a seat gone plays to the end with TURN never going to it
        st = new("4", players=4)
        st = P.op_drop({"state": st, "seat": 0})["state"]
        self.assertEqual(st["turn"], 1)
        rnd = random.Random(7)
        while not st["over"]:
            mv = P.op_moves({"state": st, "seat": st["turn"]})["moves"]
            r = P.op_apply({"state": st, "seat": st["turn"], "move": [rnd.choice(mv)]})
            self.assertNotIn("TURN 0", r["events"])
            st = r["state"]
        self.assertFalse(r["end"].startswith("win 0 "))
        self.assertEqual(P.counts(st)[0], 0)
        # an older state without the gone list still plays
        st = new("3", players=3)
        del st["gone"]
        self.assertEqual(play(st, "a1-b1")["events"][-1], "TURN 1")
        self.assertEqual(P.op_drop({"state": st, "seat": 1})["events"], ["GONE 1 resign"])

    def test_random_games_finish(self):
        for seed in range(20):
            w, h = SIZES[seed % len(SIZES)]
            st = new("%dx%d" % (w, h))
            rnd = random.Random(seed)
            total = len(P.open_lines(st))
            for ply in range(total):
                seat = st["turn"]
                mv = P.op_moves({"state": st, "seat": seat})["moves"]
                r = P.op_apply({"state": st, "seat": seat, "move": [rnd.choice(mv)]})
                st = r["state"]
                if "BOX" in r["events"][0] and not st["over"]:
                    self.assertEqual(r["events"][1], "TURN %d" % seat)
                elif not st["over"]:
                    self.assertEqual(r["events"][1], "TURN %d" % (1 - seat))
            self.assertTrue(st["over"], seed)
            self.assertIn("end", r)
            self.assertEqual(sum(P.counts(st)), w * h)
            for l in P.op_view({"state": st, "seat": 0})["wire"]:
                self.assertLessEqual(len(l) + 2, 80)


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
        await a.req("HELO dora")
        await b.req("HELO eli")
        self.assertIn("GAM dotsandboxes/1 standard Dots_and_Boxes 2 4 drop", await a.req("GAM"))
        self.assertIn("BADOPT", (await a.req("NEW dotsandboxes standard size=10"))[0])
        self.assertEqual((await a.req("NEW dotsandboxes standard size=3 turn=5m"))[0], "OK 1")
        await a.req("JON 1")
        self.assertEqual((await b.req("JON 1"))[0], "OK 1")
        v = await a.req("VUE 1 GMT")
        self.assertEqual(v[0], "V dotsandboxes/1 4")
        for l in ("BRD 3 3", "HL 1 ...", "VL 3 ....", "BX 3 ...", "C 0 0", "W 0", "S Your move", "M a1-b1"):
            self.assertIn(l, v)
        self.assertIn("ILLEGAL", (await b.req("MOV 1 a1-b1"))[0])
        self.assertIn("ILLEGAL", (await a.req("MOV 1 a1-c1"))[0])
        self.assertEqual((await a.req("CHK 1 a1-b1"))[-1], "OK boxes 0")
        who = {0: a, 1: b}
        events, again_clock = [], None
        for _ in range(30):
            v = await a.req("VUE 1")
            w = [l for l in v if l.startswith("W ")][0].split()[1]
            if w == "-":
                break
            c = who[int(w)]
            moves = [l[2:] for l in await c.req("VUE 1 M") if l.startswith("M ")]
            self.assertTrue(moves)
            r = await c.req("MOV 1 " + moves[-1])
            self.assertTrue(r[-1].startswith("OK"), r)
            mine = [l.split(" ", 3)[3] for l in r if l.startswith("EVT 1 ")]
            events += mine
            if any(" BOX " in e for e in mine) and mine[-1].startswith("TURN"):
                self.assertEqual(mine[-1], "TURN %s" % w)            # the same player moves again
                clk = [l for l in await c.req("VUE 1") if l.startswith("CLK ")][0].split()
                again_clock = (clk[1], int(clk[2]))
        self.assertIsNotNone(again_clock)
        self.assertGreaterEqual(again_clock[1], 298)        # a fresh turn clock (5 minutes)
        ends = [e for e in events if e.startswith("END ")]
        self.assertEqual(len(ends), 1)
        self.assertRegex(ends[0], r"^END (win [01] \d-\d|draw \d-\d)$")
        self.assertEqual(len([e for e in events if e.startswith("LINE ")]), 24)
        sin = await b.req("SIN 1 0")
        self.assertTrue(any(" END " in l for l in sin))
        self.assertIn("NOGAME", (await a.req("MOV 1 a1-b1"))[0])
        # the log texts for the game's own events
        names = ["dora", "eli"]
        for e in events:
            if e.startswith("LINE"):
                self.assertTrue(W.describe(e, names, D.KEY).startswith(("dora draws", "eli draws")))


    async def test_three_players_and_one_leaves(self):
        """NEW ... players=3: turns go round three seats; one resigns and the others play on to the end."""
        names = ["fay", "gus", "hal"]
        cs = [await Client.connect(self.port) for _ in names]
        for c, n in zip(cs, names):
            self.assertTrue((await c.req("HELO " + n))[0].startswith("OK"))
        self.assertIn("BADOPT", (await cs[0].req("NEW dotsandboxes standard players=5"))[0])
        self.assertIn("BADOPT", (await cs[0].req("NEW dotsandboxes standard players=1"))[0])
        tid = (await cs[0].req("NEW dotsandboxes standard size=3 players=3"))[0].split()[1]
        self.assertIn(" 0/3 ", [l for l in await cs[0].req("LST") if l.startswith("TAB %s " % tid)][0])
        for i, c in enumerate(cs):
            self.assertEqual((await c.req("JON %s" % tid))[0], "OK %d" % i)
        v = await cs[0].req("VUE %s GMT" % tid)
        self.assertIn("C 0 0 0", v)
        self.assertIn("S Your move", v)
        self.assertNotIn("G 0 0 0", v)                               # no G line until a seat has left
        self.assertIn("S Waiting for seat 0", await cs[1].req("VUE %s T" % tid))
        r = await cs[0].req("MOV %s a1-b1" % tid)
        self.assertTrue(any(l.endswith(" TURN 1") for l in r), r)
        r = await cs[1].req("MOV %s b1-c1" % tid)
        self.assertTrue(any(l.endswith(" TURN 2") for l in r), r)
        r = await cs[2].req("MOV %s c1-d1" % tid)
        self.assertTrue(any(l.endswith(" TURN 0") for l in r), r)
        r = await cs[1].req("RES %s" % tid)                          # not on turn: it leaves, nobody else moves
        self.assertEqual(r[-1], "OK")
        r = await cs[0].req("SIN %s 0" % tid)
        evs = [l.split(" ", 3)[3] for l in r if l.startswith("EVT ")]
        self.assertIn("GONE 1 resign", evs)
        self.assertFalse(any(e.startswith("END") for e in evs), evs)
        self.assertIn("G 0 1 0", await cs[0].req("VUE %s" % tid))
        self.assertIn("ERR STATE", (await cs[1].req("RES %s" % tid))[0])
        self.assertIn("ERR STATE", (await cs[1].req("MOV %s a2-b2" % tid))[0])
        self.assertEqual(W.describe("GONE 1 resign", names, D.KEY),
                         "gus resigned and leaves the game; the others play on")
        who, ends, turns = {0: cs[0], 2: cs[2]}, [], []
        for _ in range(40):
            w = [l for l in await cs[0].req("VUE %s" % tid) if l.startswith("W ")][0].split()[1]
            if w == "-":
                break
            c = who[int(w)]
            moves = [l[2:] for l in await c.req("VUE %s M" % tid) if l.startswith("M ")]
            r = await c.req("MOV %s %s" % (tid, moves[0]))
            self.assertTrue(r[-1].startswith("OK"), r)
            turns += [l.split()[-1] for l in r if " TURN " in l]
            ends += [l.split(" ", 3)[3] for l in r if " END " in l]
        self.assertNotIn("1", turns)                                 # the seat that left is skipped
        self.assertEqual(len(ends), 1)
        self.assertRegex(ends[0], r"^END (win [02]|draw) \d+-0-\d+$")


class Frontend(unittest.TestCase):
    def test_registry_and_words(self):
        self.assertIs(games.get("dotsandboxes/1"), D)
        self.assertIn(D, games.all_games())
        self.assertIs(games.by_type()["dotsandboxes"], D)
        names = ["ann", "bob"]
        self.assertEqual(D.describe_event("LINE 0 a1-b1", names), "ann draws a1-b1")
        self.assertEqual(D.describe_event("LINE 1 b1-b2 BOX a1 b1", names), "bob draws b1-b2 and takes boxes a1 and b1")
        self.assertEqual(D.describe_event("LINE 1 a2-b2 BOX a1", names), "bob draws a2-b2 and takes box a1")
        for other in ("TURN 0", "END win 0 5-4", "PUT 0 d3 FLP d4"):
            self.assertIsNone(D.describe_event(other, names))
        self.assertEqual(W.describe("END win 1 4-5", names, D.KEY), "Game over: bob wins 4-5")
        base = D.DotsBase.__new__(D.DotsBase)
        for typed, words, tok in (("a1-b1", ["a1-b1"], "a1-b1"), ("b1-a1", ["B1-A1"], "a1-b1"),
                                  ("a2a1", ["a2a1"], "a1-a2"), ("j10", ["j10", "J9"], "j9-j10"),
                                  ("a1-c1", ["a1-c1"], "a1-c1"), ("a1", ["a1"], None), ("say", ["say", "hi"], None),
                                  ("d3", ["d3"], None), ("o1-p1", ["o1-p1"], None),
                                  ("n12-m12", ["n12-m12"], "m12-n12")):
            self.assertEqual(base.move_word(typed, words), tok, typed)
        for tok in ("a1-b1", "j9-j10", "a1b1", "B1-A1", "m12-n12", "n11-n12"):     # up to n12 (13 by 11)
            self.assertTrue(D.MOVE_RE.match(tok), tok)
        for tok in ("a1-b1\n", "a1-b1\r", "o1-p1", "a1-b1-c1", "a13-b13", "a1 b1", "d3"):
            self.assertFalse(D.MOVE_RE.match(tok), repr(tok))
        self.assertEqual(D.pos_of("b1-a1"), (0, 1))
        self.assertEqual(D.pos_of("a2-a1"), (1, 0))
        self.assertIsNone(D.pos_of("a1-b2"))
        for r in range(11):
            for c in range(11):
                tok = D.line_at(r, c)
                self.assertEqual(tok is None, r % 2 == c % 2)
                if tok:
                    self.assertEqual(D.pos_of(tok), (r, c))
        v = D.parse_view(["V dotsandboxes/1 1", "S Waiting for players"])
        self.assertFalse(v["rows"])
        self.assertEqual((v["turn"], v["moves"]), (-1, []))

    def test_parse_view(self):
        st = random_state(6, 4, 20, seed=2)
        v = D.parse_view(view_lines(st, 0))
        self.assertTrue(v["rows"])
        self.assertEqual((v["w"], v["h"], len(v["hl"]), len(v["vl"]), len(v["bx"])), (6, 4, 5, 4, 4))
        self.assertEqual(v["counts"], P.counts(st))
        self.assertEqual(v["last"], st["last"])
        self.assertEqual(v["turn"], st["turn"])
        self.assertEqual(v["clock"], (st["turn"], 3599, 86400))
        self.assertEqual(sorted(v["moves"]), sorted(P.open_lines(st)) if st["turn"] == 0 else [])
        self.assertEqual(D.boxes_left(v), st["bx"].count("."))
        for tok in P.open_lines(st):
            self.assertEqual(D.state_at(v, *D.pos_of(tok)), "open")


def screen(me=0, lines=None, mode="pc"):
    """An AnsiDots screen with a view, to work out where things are drawn."""
    s = session(FakeIO(mode), ScreenCore(D.KEY, lines or []), mode)
    g = D.AnsiScreen(s, "3", D.KEY, me)
    g.v = g.parse(lines or ["V dotsandboxes/1 1"])
    return g


class Screens(unittest.TestCase):
    def test_every_size_fits(self):
        """Every board size, at the start, in the middle and full, fits 80x24 in every mode, for each seat
        and a watcher; the board stays left of the panel and above the hint line."""
        for w, h in SIZES:
            g = screen(0, view_lines(new("%dx%d" % (w, h)), 0))
            w_, h_, sx, sy, bx, by = g.geometry()
            self.assertEqual((w_, h_), (w, h))
            self.assertGreaterEqual(bx - 3, 0)
            self.assertGreaterEqual(by - 1, 1)
            self.assertLess(bx + sx * w, g.PX - 1, (w, h))
            self.assertLessEqual(by + sy * h, 20, (w, h))
            for plies, seed in ((0, 0), (w * h, 1), (999, 2)):
                st = random_state(w, h, plies, seed)
                for me in (0, 1, -1):
                    lines = view_lines(st, me)
                    shown = ansi_screens(self, D, lines, me, keys=["RIGHT", "DOWN", "DOWN", "LEFT", "UP"])
                    for mode, (text, reqs) in shown.items():
                        self.assertIn(LONG, text, (w, h, mode))
                        self.assertFalse([q for q in reqs if q.startswith("MOV")])
                    text = plain_screen(self, D, lines, me)
                    self.assertLessEqual(len(text.split("\n")), 2 * h + 16, (w, h))    # with the log and prompt
                    web_page(self, D, lines, me)

    def test_majority_board_scrolls(self):
        """The 13 by 11 majority board on the full screen: 2 columns and 2 rows a box, 19 of its 23 rows shown,
        scrolling so that the cursor stays on the screen; the plain board and the web page draw all of it."""
        st = P.op_new({"players": 2, "options": {}, "seed": 0, "rules": "majority"})["state"]
        lines = view_lines(st, 0)
        g = screen(0, lines)
        w, h, sx, sy, bx, by = g.geometry()
        self.assertEqual((w, h, sx, sy), (13, 11, 2, 2))
        self.assertLess(bx + sx * w, g.PX - 1)
        self.assertEqual(g.shown_top(), 0)
        self.assertTrue(g.shown(18) and not g.shown(19))
        g.cur = (2 * h, 1)                           # the bottom row of dots
        self.assertEqual(g.shown_top(), h - 9)
        self.assertTrue(g.shown(2 * h) and not g.shown(3))
        self.assertLessEqual(g.xy(2 * h, 0)[1], 20)
        for mode, (text, reqs) in ansi_screens(self, D, lines, 0, keys=["UP", "ENTER"]).items():
            self.assertIn("MOV 3 a12-b12", reqs, mode)
            self.assertIn("12", text)
        down = ["DOWN"] * 20 + ["RIGHT"] * 12 + ["ENTER"]
        self.assertIn("MOV 3 m11-n11", ansi_screens(self, D, lines, 0, keys=down)["pc"][1])
        for plies, seed in ((40, 1), (200, 2)):
            st2 = P.op_new({"players": 2, "options": {}, "seed": 0, "rules": "majority"})["state"]
            rnd = random.Random(seed)
            for _ in range(plies):
                if st2["over"]:
                    break
                mv = P.op_moves({"state": st2, "seat": st2["turn"]})["moves"]
                st2 = P.op_apply({"state": st2, "seat": st2["turn"], "move": [rnd.choice(mv)]})["state"]
            for me in (0, 1, -1):
                lines = view_lines(st2, me)
                ansi_screens(self, D, lines, me, keys=["DOWN"] * 12)
                text = plain_screen(self, D, lines, me)
                self.assertIn(" 12   +", text)
                web_page(self, D, lines, me)

    def test_opening_screen(self):
        lines = view_lines(new(), 0)
        for mode, (text, reqs) in ansi_screens(self, D, lines, 0, keys=[]).items():
            self.assertIn("Dots and Boxes", text)
            self.assertIn("you are A", text)
            self.assertIn("Your move", text)
            self.assertIn("Boxes left: 25 of 25", text)
            self.assertIn("a1-b1: Enter draws this line.", text)
            self.assertIn(" A ", text)
            self.assertIn(" B ", text)
            if mode != "ansi":
                self.assertEqual(text.count("■"), 36)        # 6 by 6 dots
        text = ansi_screens(self, D, view_lines(new(), 1), 1, keys=[])["ansi"][0]
        self.assertIn("you are B", text)
        self.assertIn("Waiting for %s." % LONG, text)
        text = ansi_screens(self, D, view_lines(new(), -1), -1, keys=[])["utf8"][0]
        self.assertIn("watching", text)
        self.assertIn("Watching. T talks", text)

    def test_keys_draw_lines(self):
        lines = view_lines(new("3"), 0)
        for mode, (text, reqs) in ansi_screens(self, D, lines, 0, keys=["ENTER"]).items():
            self.assertIn("MOV 3 a1-b1", reqs, mode)
        cases = [(["RIGHT", "ENTER"], "b1-c1"), (["LEFT", "ENTER"], "c1-d1"), (["DOWN", "ENTER"], "a1-a2"),
                 (["DOWN", "RIGHT", "RIGHT", "ENTER"], "c1-c2"), (["UP", "ENTER"], "a4-b4"),
                 (["DOWN", "DOWN", "ENTER"], "a2-b2"), (["RIGHT", "DOWN", "DOWN", "ENTER"], "b2-c2"),
                 ([" "], "a1-b1")]
        for keys, tok in cases:
            reqs = ansi_screens(self, D, lines, 0, keys=keys)["pc"][1]
            self.assertEqual([q for q in reqs if q.startswith("MOV")], ["MOV 3 " + tok], keys)
        # typed: either order, with or without the dash, or as two dots
        for typed, tok in (("c2-c1", "c1-c2"), ("a1b1", "a1-b1"), ("d4 d3", "d3-d4")):
            reqs = ansi_screens(self, D, lines, 0, keys=list(typed) + ["ENTER"])["ansi"][1]
            self.assertIn("MOV 3 " + tok, reqs, typed)
        # the other seat and a watcher send nothing
        for me in (1, -1):
            reqs = ansi_screens(self, D, view_lines(new("3"), me), me, keys=["ENTER", "RIGHT", "ENTER"])["pc"][1]
            self.assertFalse([q for q in reqs if q.startswith("MOV")])

    def test_cursor_skips_drawn_lines_and_clicks(self):
        st = play(new("3"), "a1-b1", "b1-c1", "c1-d1", "a1-a2")["state"]
        me = st["turn"]
        self.assertEqual(me, 0)
        lines = view_lines(st, me)
        reqs = ansi_screens(self, D, lines, me, keys=["ENTER"])["pc"][1]
        self.assertEqual([q for q in reqs if q.startswith("MOV")], ["MOV 3 b1-b2"])     # nearest open line
        g = screen(me, lines)
        x, y = g.xy(*D.pos_of("c3-d3"))
        reqs = ansi_screens(self, D, lines, me, keys=[("CLICK", x + 1, y)])["utf8"][1]
        self.assertEqual([q for q in reqs if q.startswith("MOV")], ["MOV 3 c3-d3"])
        x, y = g.xy(*D.pos_of("b2-b3"))
        reqs = ansi_screens(self, D, lines, me, keys=[("CLICK", x, y)])["ansi"][1]
        self.assertEqual([q for q in reqs if q.startswith("MOV")], ["MOV 3 b2-b3"])
        x, y = g.xy(*D.pos_of("a1-b1"))                     # a drawn line: the cursor goes there, nothing sent
        shown, reqs = ansi_screens(self, D, lines, me, keys=[("CLICK", x, y), "ENTER"])["pc"]
        self.assertFalse([q for q in reqs if q.startswith("MOV")])
        self.assertIn("a1-b1 is drawn", shown)

    def test_arrows(self):
        g = screen(0, view_lines(new("3"), 0))
        g.snap()
        self.assertEqual(g.cur, (0, 1))
        path = []
        for k in ("RIGHT", "RIGHT", "RIGHT", "DOWN", "DOWN", "UP", "LEFT", "UP", "UP"):
            g.on_arrow(k)
            path.append(D.line_at(*g.cur))
        self.assertEqual(path, ["b1-c1", "c1-d1", "a1-b1", "a1-a2", "a2-b2", "a1-a2", "d1-d2", "c1-d1", "c4-d4"])

    def test_extra_turn_crowded_and_four_players(self):
        st = play(new("3"), "a1-b1", "a1-a2", "b1-b2", "a2-b2")["state"]
        lines = view_lines(st, 1)
        for mode, (text, reqs) in ansi_screens(self, D, lines, 1, keys=[]).items():
            self.assertIn("Your move again: box closed", text)
            self.assertIn("You closed a box: move again.", text)
        self.assertIn("Your move again (like", plain_screen(self, D, lines, 1))
        names4 = (LONG, LONG.upper(), "cy" * 6, "dee" * 4)
        st = random_state(9, 9, 120, seed=4, players=4)
        for me in (3, -1):
            lines = view_lines(st, me)
            for mode, (text, _) in ansi_screens(self, D, lines, me, keys=["DOWN"], names=names4).items():
                for n in names4:
                    self.assertIn(n, text, mode)
            text = plain_screen(self, D, lines, me, names=names4)
            for n in names4:
                self.assertIn(n, text)
            html = web_page(self, D, lines, me, names=names4)
            self.assertIn("dee" * 4, html)
            self.assertIn("<b>D</b>", html)
            self.assertIn("/img/dotsandboxes/b3.gif", [p for p, _, _ in board_image(self, D, lines, me).used])

    def test_a_seat_that_left(self):
        """Four players, seat 1 gone: every screen says so, the seat that left gets no cursor or keys, the
        others play on."""
        names4 = (LONG, LONG.upper(), "cy" * 6, "dee" * 4)
        st = random_state(5, 5, 14, seed=6, players=4)
        st = P.op_drop({"state": st, "seat": 1})["state"]
        self.assertFalse(st["over"])
        turn = st["turn"]
        self.assertNotEqual(turn, 1)
        for me in (turn, 1, -1):
            lines = view_lines(st, me)
            v = D.parse_view(lines)
            self.assertEqual(v["gone"], [False, True, False, False])
            for mode, (text, reqs) in ansi_screens(self, D, lines, me, keys=["ENTER"], names=names4).items():
                self.assertIn(" left", text, mode)
                if me == 1:
                    self.assertIn("You have left this game", text)
                    self.assertNotIn("Enter draw", text)
                    self.assertFalse([q for q in reqs if q.startswith("MOV")])
                elif me == turn:
                    self.assertEqual(len([q for q in reqs if q.startswith("MOV")]), 1)
            text = plain_screen(self, D, lines, me, names=names4)
            self.assertRegex(text, r"B %s +\d+ left" % LONG.upper())
            html = web_page(self, D, lines, me, names=names4)
            self.assertRegex(html, r"%s( \(you\))?, left the game" % LONG.upper())
            self.assertEqual("/move.htm" in html, me == turn)
        self.assertEqual(D.describe_event("GONE 1 timeout", list(names4)), None)          # a shared event
        self.assertEqual(W.describe("GONE 1 timeout", list(names4), D.KEY),
                         "%s ran out of time and leaves the game; the others play on" % LONG.upper())

    def test_game_over(self):
        st = random_state(5, 5, 999, seed=5)
        self.assertTrue(st["over"])
        result = P.result_text(st)
        for me in (0, -1):
            lines = view_lines(st, me)
            self.assertEqual(D.parse_view(lines)["turn"], -1)
            for mode, (text, reqs) in ansi_screens(self, D, lines, me, keys=["ENTER"]).items():
                self.assertIn("Game over", text)
                self.assertIn("Boxes left: 0 of 25", text)
                self.assertFalse([q for q in reqs if q.startswith("MOV")])
            over = "Game over: %s wins 13-12" % LONG

            def ended(g, over=over):
                g.over = over
            for mode, (text, _) in ansi_screens(self, D, lines, me, keys=[], prepare=ended).items():
                self.assertIn(over + ". Esc goes back", text)
            self.assertIn("Game over", plain_screen(self, D, lines, me))
            html = web_page(self, D, lines, me)
            self.assertNotIn("/move.htm", html)
            self.assertIn("Boxes left: 0 of 25", html)
        self.assertTrue(result)

    def test_plain_board(self):
        st = play(new("3"), "a1-b1", "a1-a2", "b1-b2", "a2-b2")["state"]
        text = plain_screen(self, D, view_lines(st, 1), 1)
        self.assertIn("== Dots and Boxes, table 3: you are B ==", text)
        self.assertIn("\n      a   b   c   d\n", text)
        self.assertIn("  1   +---+   +   +", text)
        self.assertIn("      | B |", text)
        self.assertIn("  2   +===+   +   +", text)
        self.assertIn("Boxes left: 8 of 9", text)
        self.assertIn("Last line: a2-b2", text)
        self.assertTrue(text.endswith("Your move again (like a2-a3) > "))
        text = plain_screen(self, D, view_lines(st, 0), 0)
        self.assertTrue(text.endswith("\n> "))
        self.assertIn("Waiting for an opponent", plain_screen(self, D, ["V dotsandboxes/1 1"], 0))

    def test_web_board(self):
        st = play(new("4"), "a1-b1", "a1-a2", "b1-b2", "a2-b2")["state"]
        lines = view_lines(st, 1)
        html = web_page(self, D, lines, 1)
        moves = P.op_moves({"state": st, "seat": 1})["moves"]
        links = re.findall(r'href="/move\.htm\?[^"]*mv=([^"&]+)', html)
        self.assertEqual(sorted(links), sorted(moves))
        self.assertTrue(all(D.MOVE_RE.match(m) for m in links))
        c = board_image(self, D, lines, 1)
        used = [p for p, _, _ in c.used]
        self.assertIn("/img/dotsandboxes/b1.gif", used)      # the closed box, with its owner's letter
        self.assertIn("/img/dotsandboxes/hl.gif", used)      # the last line drawn
        self.assertEqual(sorted(dict(a[4])["mv"] for a in c.areas), sorted(moves))
        self.assertIn('<form action="/move.htm" method="get">', html)
        self.assertIn('<input type="hidden" name="t" value="3">', html)
        self.assertIn('name="mv"', html)
        self.assertIn("Your move again", D.parse_view(lines)["status"])
        board, side, hint, actions = D.web_parts(D.parse_view(lines), "3", lambda p, **q: W.url(p, **q), 1,
                                                 ["ann", "bob"], "")
        self.assertEqual(hint, " - click an orange line")
        self.assertEqual(c.w, D.MARGIN + 4 * 32 + 5 * 8 + 4)
        self.assertLess(board_image(self, D, view_lines(new("9"), 0), 0).w, 640)
        for me in (0, -1):                                  # not your move, or watching: no links, no form
            html = web_page(self, D, view_lines(st, me), me)
            self.assertNotIn("/move.htm", html)
        images = D.images()
        self.assertEqual(len(images), 2 + 8 + 4)
        self.assertTrue(all(p.startswith("/img/dotsandboxes/") for p in images))



class Pickers(unittest.TestCase):
    """2 to 4 players: the web lobby's form has a players select and the telnet menu a players picker,
    both from the core's GAM line, with nothing in the adapters for this game."""

    GAM = ["GAM dotsandboxes/1 standard Dots_and_Boxes %d %d %s" % (P.INFO["min"], P.INFO["max"],
                                                                   ",".join(P.INFO["flags"]))]

    def test_web_lobby(self):
        from adapters.common import parse_games
        from game_checks import page_link
        out = W.lobby_page("ann", page_link, parse_games(self.GAM), []).decode("ascii")
        form = [f for f in out.split("<li><form")[1:] if "Start a game of Dots and Boxes" in f][0]
        self.assertIn('<select name="np"><option value="2" selected>2<option value="3">3<option value="4">4'
                      '</select>', form)
        self.assertNotIn('<select name="ru"', form)

    def test_telnet_menu(self):
        from test_adapters import LineIO, T
        test = self

        class Core:
            reqs = []
            auth = "password"

            async def req(self, text):
                self.reqs.append(text)
                if text == "GAM":
                    return test.GAM, "OK"
                return [], "OK 7" if text.startswith("NEW ") else "OK"

        def run(mode, args, keys):
            io = (LineIO if mode == "plain" else FakeIO)(mode, keys)
            core = Core()
            core.reqs = []
            s = T.Session(io, core, mode, "ann")
            menu = (T.PlainMenu if mode == "plain" else T.AnsiMenu)(s)
            asyncio.run(s.load())
            try:
                asyncio.run(menu.new(args))
            except EOFError:
                pass
            return [r for r in core.reqs if r.startswith("NEW ")], "\n".join("\n".join(x) for x in io.screens)

        reqs, text = run("pc", ["dotsandboxes"], ["DOWN", "DOWN", "ENTER", "ENTER"])
        self.assertIn("How many players?", text)
        self.assertIn("4 players", text)
        self.assertEqual(reqs, ["NEW dotsandboxes/1 standard turn=1d players=4"])
        self.assertEqual(run("pc", ["dotsandboxes", "3p", "1h"], [])[0],
                         ["NEW dotsandboxes/1 standard turn=1h players=3"])
        self.assertEqual(run("pc", ["dotsandboxes", "5p", "1h"], [])[0], [])
        self.assertEqual(run("plain", ["dotsandboxes"], ["3", ""])[0],
                         ["NEW dotsandboxes/1 standard turn=1d players=3"])
        s = T.Session(FakeIO("pc"), Core(), "pc", "ann")
        menu = T.AnsiMenu(s)
        asyncio.run(menu.load())
        menu.sel = [i for i, it in enumerate(menu.mine) if it[0] == "new"][0]
        self.assertEqual("Enter starts a new Dots and Boxes table; you choose players and time per turn.",
                         menu.describe())


if __name__ == "__main__":
    unittest.main()
