"""mancala/1: the plugin's two rulesets (kalah, oware), whole games over the protocol, and the web and telnet
screens (docs/types/mancala-1.md, docs/games-howto.md)."""
import importlib.util
import os
import random
import re
import subprocess
import sys
import unittest

from game_checks import LONG, ansi_screens, board_image, plain_screen, web_page
from test_core import ROOT, Client, free_port

from adapters.games import mancala as M  # noqa: E402

_spec = importlib.util.spec_from_file_location("mancala_plugin", os.path.join(ROOT, "plugins", "mancala", "plugin.py"))
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def new(rules="kalah", **opts):
    r = mc.op_new({"players": 2, "rules": rules, "options": {k: str(v) for k, v in opts.items()}, "seed": 1})
    assert "error" not in r, r
    return r["state"]


def state(rules, b, turn=0):
    st = new(rules)
    st["b"], st["turn"] = list(b), turn
    st["seen"] = []
    return st


def board(stores=(0, 0), **pits):
    """A board from the stores and the pits named a1..a6 (seat 0's) and b1..b6 (seat 1's); the rest empty."""
    b = [0] * 14
    b[6], b[13] = stores
    for k, n in pits.items():
        b[(0 if k[0] == "a" else 7) + int(k[1]) - 1] = n
    return b


def play(st, seat, p):
    r = mc.op_apply({"state": st, "seat": seat, "move": [str(p)]})
    assert "error" not in r, r
    return r


def moves(st, seat):
    return mc.op_moves({"state": st, "seat": seat})["moves"]


def sow_board(ev):
    """The board in a SOW event."""
    return [int(x) for x in ev.split()[4:]]


def random_game(rules, seed, **opts):
    rnd = random.Random(seed)
    st = new(rules, **opts)
    total = sum(st["b"])
    for plies in range(2000):
        seat = st["turn"]
        ms = moves(st, seat)
        assert ms, (rules, seed, st)
        r = play(st, seat, rnd.choice(ms))
        st = r["state"]
        assert sum(st["b"]) == total and min(st["b"]) >= 0
        if "end" in r:
            return r, st, plies
    raise AssertionError("no end")


class Setup(unittest.TestCase):
    def test_info_and_new(self):
        self.assertEqual(mc.op_info({})["rules"], ["kalah", "oware"])
        r = mc.op_new({"players": 2, "options": {}, "seed": 1})
        self.assertEqual(r["events"], ["TURN 0"])
        st = r["state"]
        self.assertEqual((st["rules"], st["b"], st["turn"]), ("kalah", [4] * 6 + [0] + [4] * 6 + [0], 0))
        self.assertEqual(moves(st, 0), ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(moves(st, 1), [])
        self.assertEqual(new(seeds=6)["b"][:7], [6] * 6 + [0])
        self.assertEqual(new(seeds=3)["b"][7:], [3] * 6 + [0])

    def test_bad_options(self):
        for opts in ({"seeds": "2"}, {"seeds": "7"}, {"seeds": "x"}, {"rules": "kalahx"}):
            self.assertIn("error", mc.op_new({"players": 2, "options": opts, "seed": 1}), opts)
        self.assertIn("error", mc.op_new({"players": 2, "rules": "oware", "options": {"seeds": "5"}, "seed": 1}))
        self.assertIn("error", mc.op_new({"players": 3, "options": {}, "seed": 1}))

    def test_choosing_the_ruleset(self):
        """The rules field of new (the table's ruleset) or, from a core that does not pass it, the option
        rules=; when both are there the table's ruleset wins and the option is ignored."""
        self.assertEqual(new("oware")["rules"], "oware")
        r = mc.op_new({"players": 2, "options": {"rules": "oware"}, "seed": 1})
        self.assertEqual(r["state"]["rules"], "oware")
        self.assertEqual(mc.op_new({"players": 2, "rules": "oware", "options": {"rules": "OWARE"},
                                    "seed": 1})["state"]["rules"], "oware")
        for opt in ("oware", "kalahx"):
            self.assertEqual(mc.op_new({"players": 2, "rules": "kalah", "options": {"rules": opt},
                                        "seed": 1})["state"]["rules"], "kalah")
        self.assertIn("error", mc.op_new({"players": 2, "rules": "nope", "options": {}, "seed": 1}))

    def test_view(self):
        st = new("oware")
        v = mc.op_view({"state": st, "seat": 0})
        self.assertEqual(v["wire"], ["RU oware 4", "B 4 4 4 4 4 4 0 4 4 4 4 4 4 0", "W 0"])
        self.assertEqual(v["geometry"], ["BRD 6"])
        self.assertEqual(v["status"], "Your move")
        st = play(st, 0, 2)["state"]
        self.assertIn("LM 0 2", mc.op_view({"state": st, "seat": -1})["wire"])
        self.assertEqual(mc.op_result({"state": st}), {"over": False})

    def test_errors(self):
        st = new()
        for seat, mv in ((1, ["1"]), (0, ["0"]), (0, ["7"]), (0, ["x"]), (0, ["1", "2"]), (0, [])):
            self.assertIn("error", mc.op_apply({"state": st, "seat": seat, "move": mv}), (seat, mv))
        st = state("kalah", board(a1=1, a3=0, b1=4))
        self.assertIn("empty", mc.op_apply({"state": st, "seat": 0, "move": ["3"]})["error"])
        self.assertEqual(mc.op_apply({"state": new(), "seat": 0, "move": ["3"], "check": True}),
                         {"info": "captures 0"})


class Kalah(unittest.TestCase):
    def test_sowing_into_the_store_moves_again(self):
        r = play(new(), 0, 3)
        self.assertEqual(r["events"], ["SOW 0 3 4 4 4 0 5 5 5 1 4 4 4 4 4 4 0", "AGAIN 0", "TURN 0"])
        self.assertEqual(r["state"]["turn"], 0)
        r = play(r["state"], 0, 1)          # 4 seeds from pit 1: the last lands in pit 5, no extra turn
        self.assertEqual(r["events"][-1], "TURN 1")
        self.assertEqual(sow_board(r["events"][0]), [0, 5, 1, 6, 6, 5, 1, 4, 4, 4, 4, 4, 4, 0])

    def test_seat_1_sows_its_own_row(self):
        st = play(new(), 0, 1)["state"]
        r = play(st, 1, 4)                  # seat 1's pit 4: 4 seeds into pits 5, 6, its store and seat 0's pit 1
        self.assertEqual(sow_board(r["events"][0]), [1, 5, 5, 5, 5, 4, 0, 4, 4, 4, 0, 5, 5, 1])

    def test_long_sowing_skips_the_opponents_store_and_captures_on_the_lap(self):
        st = state("kalah", board(a1=1, a6=13, b1=2, b2=2, b3=2, b4=2, b5=2, b6=2))
        r = play(st, 0, 6)
        # the store, seat 1's pits, not seat 1's store, then round to pit 6 again: empty, so it captures
        self.assertEqual(r["events"], ["SOW 0 6 13 2 1 1 1 1 0 5 0 3 3 3 3 3 0", "CAP 0 4", "TURN 1"])
        self.assertEqual(r["state"]["b"][13], 0)

    def test_capture_from_an_empty_own_pit(self):
        st = state("kalah", board(a1=3, a4=5, b1=2, b2=5, b6=1), turn=1)
        r = play(st, 1, 1)                  # seat 1's pit 1: 2 seeds, the last into its empty pit 3,
        self.assertEqual(r["events"], ["SOW 1 1 2 3 0 0 0 0 0 0 0 6 0 0 0 1 6", "CAP 1 6", "TURN 0"])
        # which takes seat 0's pit 4 opposite with it

    def test_no_capture_when_the_pit_opposite_is_empty(self):
        st = state("kalah", board(a1=1, a4=2, b1=3, b2=1, b3=1, b4=1))
        r = play(st, 0, 1)                  # into empty pit 2; seat 1's pit 5 opposite is empty
        self.assertEqual(r["events"], ["SOW 0 1 1 0 1 0 2 0 0 0 3 1 1 1 0 0 0", "TURN 1"])

    def test_end_when_a_row_is_empty(self):
        st = state("kalah", board((22, 20), a6=1, b2=3, b5=2))
        r = play(st, 0, 6)                  # the last seed of seat 0 goes into its store: no extra turn, the end
        self.assertEqual(r["events"], ["SOW 0 6 1 0 0 0 0 0 0 23 0 3 0 0 2 0 20", "SWEEP 1 5 empty"])
        self.assertEqual(r["end"], "win 1 23-25")
        self.assertTrue(r["state"]["over"])
        self.assertEqual(moves(r["state"], 1), [])
        self.assertEqual(mc.op_result({"state": r["state"]}), {"over": True, "scores": [23, 25]})
        self.assertIn("error", mc.op_apply({"state": r["state"], "seat": 1, "move": ["2"]}))
        self.assertEqual(mc.op_view({"state": r["state"], "seat": 0})["status"], "Game over: win 1 23-25")

    def test_end_on_a_capture_that_empties_the_other_row(self):
        st = state("kalah", board((31, 10), a1=1, a3=2, b5=4))
        r = play(st, 0, 1)                  # into empty pit 2, opposite seat 1's pit 5: seat 1's row is empty
        self.assertEqual(r["events"], ["SOW 0 1 1 0 0 2 0 0 0 36 0 0 0 0 0 0 10", "CAP 0 5", "SWEEP 0 2 empty"])
        self.assertEqual(r["end"], "win 0 38-10")

    def test_random_games_finish(self):
        for seed in range(40):
            r, st, _ = random_game("kalah", seed, seeds=3 + seed % 4)
            self.assertRegex(r["end"], r"^(win [01]|draw) \d+-\d+$")
            self.assertEqual(sum(st["b"][i] for i in list(range(6)) + list(range(7, 13))), 0)


class Oware(unittest.TestCase):
    def test_sowing_skips_the_stores(self):
        r = play(new("oware"), 0, 6)
        self.assertEqual(r["events"], ["SOW 0 6 4 4 4 4 4 4 0 0 5 5 5 5 4 4 0", "TURN 1"])
        r = play(r["state"], 1, 6)          # seat 1's pit 6: skips its store, on into seat 0's pits
        self.assertEqual(sow_board(r["events"][0]), [5, 5, 5, 5, 4, 0, 0, 5, 5, 5, 5, 4, 0, 0])

    def test_a_lap_skips_the_pit_it_started_from(self):
        st = state("oware", board(a1=12, a2=1, b1=1, b2=1, b3=1))
        r = play(st, 0, 1)
        self.assertEqual(sow_board(r["events"][0]), [0, 3, 1, 1, 1, 1, 0, 2, 2, 2, 1, 1, 1, 0])

    def test_captures_2s_and_3s_backwards(self):
        st = state("oware", board(a6=3, b1=1, b2=2, b3=1, b4=4))
        r = play(st, 0, 6)                  # 2, 3, 2 on seat 1's pits 1-3: all captured, back to the edge
        self.assertEqual(r["events"], ["SOW 0 6 3 0 0 0 0 0 0 7 0 0 0 4 0 0 0", "CAP 0 7", "TURN 1"])

    def test_the_chain_stops_at_a_pit_without_2_or_3(self):
        st = state("oware", board(a5=4, b1=4, b2=1, b3=2, b4=1))
        r = play(st, 0, 5)                  # pit 5: own pit 6, then seat 1's 1 (5), 2 (2) and 3 (3)
        self.assertEqual(r["events"][1], "CAP 0 5")
        self.assertEqual(r["state"]["b"][7:10], [5, 0, 0])

    def test_no_capture_on_the_own_row_or_without_2_or_3(self):
        st = state("oware", board(a1=2, a2=1, a3=2, b1=3))
        self.assertEqual(play(st, 0, 1)["events"][1], "TURN 1")       # 2 and 3 on its own row
        st = state("oware", board(a1=1, a6=1, b1=3, b2=1))
        self.assertEqual(play(st, 0, 6)["events"][1], "TURN 1")       # makes a 4

    def test_grand_slam_captures_nothing(self):
        st = state("oware", board(a1=3, a6=2, b1=1, b2=1))
        r = play(st, 0, 6)                  # would take both of seat 1's pits, all it has
        self.assertEqual(r["events"], ["SOW 0 6 2 3 0 0 0 0 0 0 2 2 0 0 0 0 0", "GRAND 0", "TURN 1"])
        self.assertEqual(r["state"]["b"][6], 0)

    def test_must_feed(self):
        st = state("oware", board((0, 10), a1=2, a4=1, a6=1))
        self.assertEqual(moves(st, 0), ["6"])                       # only pit 6 reaches seat 1's row
        self.assertIn("feed", mc.op_apply({"state": st, "seat": 0, "move": ["1"]})["error"])
        st = state("oware", board(a1=2, a3=4, a6=1))
        self.assertEqual(moves(st, 0), ["3", "6"])

    def test_cannot_feed_ends_the_game(self):
        st = state("oware", board((24, 22), a6=1, b1=1))
        r = play(st, 0, 6)                  # a grand slam: nothing captured, seat 0's row is empty, and
        self.assertEqual(r["events"], ["SOW 0 6 1 0 0 0 0 0 0 24 2 0 0 0 0 0 22", "GRAND 0",
                                       "SWEEP 1 2 nofeed"])   # seat 1's pit 1 (2 seeds) cannot reach it
        self.assertEqual(r["end"], "draw 24-24")

    def test_more_than_24_wins(self):
        st = state("oware", board((23, 14), a1=4, a6=1, b1=1, b3=5))
        r = play(st, 0, 6)
        self.assertEqual(r["events"], ["SOW 0 6 1 4 0 0 0 0 0 25 0 0 5 0 0 0 14", "CAP 0 2"])
        self.assertEqual(r["end"], "win 0 25-14")

    def test_an_endless_cycle_ends_the_game(self):
        st = state("oware", board((23, 23), a1=1, b1=1))
        after = mc.position(board(a2=1, b1=1), 1)
        st["seen"] = [after]
        r = play(st, 0, 1)                  # the position after the move has been seen: each takes its side
        self.assertEqual(r["events"][1:], ["SWEEP 0 1 cycle", "SWEEP 1 1 cycle"])
        self.assertEqual(r["end"], "draw 24-24")
        st["seen"] = ["x"] * mc.NO_CAPTURE_LIMIT
        self.assertEqual(play(st, 0, 1)["end"], "draw 24-24")       # or 200 moves without a capture
        st["seen"] = []
        r = play(st, 0, 1)
        self.assertNotIn("end", r)
        self.assertEqual(r["state"]["seen"], [after])

    def test_captures_clear_the_cycle_record(self):
        st = state("oware", board(a6=1, b1=1, b4=3))
        st["seen"] = ["a", "b"]
        r = play(st, 0, 6)
        self.assertEqual(r["events"][1], "CAP 0 2")
        self.assertEqual(len(r["state"]["seen"]), 1)

    def test_random_games_finish(self):
        ends = set()
        for seed in range(40):
            r, st, _ = random_game("oware", seed)
            self.assertRegex(r["end"], r"^(win [01]|draw) \d+-\d+$")
            s0, s1 = st["b"][6], st["b"][13]
            self.assertTrue(max(s0, s1) >= 25 or sum(st["b"]) == s0 + s1, (seed, st["b"]))
            ends.add(next((e.split()[-1] for e in r["events"] if e.startswith("SWEEP")), "25"))
        self.assertIn("25", ends)


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

    async def whole_game(self, new_line, rules, tid):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO gus%s" % tid)
        await b.req("HELO hal%s" % tid)
        gam = await a.req("GAM")
        self.assertIn("GAM mancala/1 kalah Mancala 2 2", gam)
        self.assertIn("GAM mancala/1 oware Mancala 2 2", gam)
        self.assertIn("BADOPT", (await a.req("NEW mancala kalah seeds=9"))[0])
        self.assertEqual(await a.req(new_line), ["OK %s" % tid])
        await a.req("JON %s" % tid)
        await b.req("JON %s" % tid)
        who = {0: a, 1: b}
        v = await a.req("VUE %s G" % tid)
        self.assertIn("BRD 6", v)
        self.assertIn("RU %s 4" % rules, v)
        self.assertIn("B 4 4 4 4 4 4 0 4 4 4 4 4 4 0", v)
        self.assertIn("ILLEGAL", (await b.req("MOV %s 1" % tid))[0])      # not seat 1's turn
        self.assertIn("ILLEGAL", (await a.req("MOV %s 9" % tid))[0])
        rnd, events = random.Random(7), []
        for _ in range(500):
            v = await a.req("VUE %s" % tid)
            seat = int(next(l for l in v if l.startswith("W ")).split()[1])
            if seat < 0:
                break
            ms = [l.split()[1] for l in await who[seat].req("VUE %s M" % tid) if l.startswith("M ")]
            self.assertTrue(ms)
            r = await who[seat].req("MOV %s %s" % (tid, rnd.choice(ms)))
            self.assertTrue(r[-1].startswith("OK"), r)
            events += [l.split(" ", 3)[3] for l in r if l.startswith("EVT ")]
        end = [e for e in events if e.startswith("END ")]
        self.assertEqual(len(end), 1, events[-5:])
        self.assertRegex(end[0], r"^END (win [01]|draw) \d+-\d+$")
        v = await b.req("VUE %s T" % tid)
        self.assertIn("W -1", v)
        self.assertTrue(any(l.startswith("S Game over: ") for l in v), v)
        self.assertTrue(any(e.startswith("SOW ") for e in events))
        for e in events:                # every event of the game is one a frontend can describe
            kind = e.split()[0]
            if kind not in ("TURN", "END", "START", "JOIN"):
                self.assertTrue(M.describe_event(e, ["gus", "hal"]), e)
        for l in await a.req("SIN %s 0" % tid):
            self.assertLessEqual(len(l) + 2, 80, l)
        return events

    async def test_store_rules_game(self):
        events = await self.whole_game("NEW mancala kalah", "kalah", "1")
        self.assertFalse(any(e.startswith("GRAND") for e in events))

    async def test_oware_game(self):
        events = await self.whole_game("NEW mancala oware", "oware", "1")
        self.assertFalse(any(e.startswith("AGAIN") for e in events))

    async def test_table_ruleset_beats_the_option(self):
        events = await self.whole_game("NEW mancala oware rules=kalah", "oware", "1")
        self.assertFalse(any(e.startswith("AGAIN") for e in events))


# ---- the frontends -----------------------------------------------------------------------

OPEN = ["V mancala/1 4", "RU kalah 4", "B 4 4 4 4 4 4 0 4 4 4 4 4 4 0", "W 0", "CLK 0 290 300",
        "M 1", "M 2", "M 3", "M 4", "M 5", "M 6", "S Your move"]
SEAT1 = ["V mancala/1 7", "RU kalah 4", "B 0 5 5 5 5 5 1 4 4 4 4 4 4 0", "LM 0 1", "W 1", "CLK 1 100 300",
         "M 1", "M 2", "M 3", "M 4", "M 5", "M 6", "S Your move"]
CROWDED = ["V mancala/1 99", "RU kalah 6", "B 0 12 0 0 1 2 30 1 0 0 0 0 26 0", "LM 1 2", "W 0", "CLK 0 5 300",
           "M 2", "M 5", "M 6", "S Your move"]
OWARE_FEED = ["V mancala/1 50", "RU oware 4", "B 2 0 0 1 0 1 20 0 0 0 0 0 0 24", "LM 1 6", "W 0", "M 6",
              "S Your move"]
OVER = ["V mancala/1 80", "RU oware 4", "B 0 0 0 0 0 0 25 0 0 0 3 0 0 20", "LM 0 6", "W -1",
        "S Game over: win 0 25-20"]
WAITING = ["V mancala/1 1", "S Waiting for players"]


def legal_links(html):
    """The pits the board's map sows (the same links are listed as text under the image too)."""
    areas = re.findall(r"<area [^>]*mv=([^\"&]+)", html)
    self_check = re.findall(r"mv=([^\"&]+)", html)
    assert sorted(self_check) == sorted(areas * 2), (areas, self_check)
    return areas


class Views(unittest.TestCase):
    def test_parse(self):
        v = M.parse_view(CROWDED)
        self.assertTrue(v["rows"])
        self.assertEqual((v["rules"], v["seeds"], v["turn"], v["last"], v["seq"]), ("kalah", 6, 0, (1, 2), 99))
        self.assertEqual((M.pit(v, 0, 2), M.store(v, 0), M.pit(v, 1, 6), M.store(v, 1)), (12, 30, 26, 0))
        self.assertEqual(v["moves"], ["2", "5", "6"])
        self.assertEqual(v["clock"], (0, 5, 300))
        self.assertFalse(M.parse_view(WAITING)["rows"])

    def test_events(self):
        names = [LONG, "bob"]
        self.assertEqual(M.describe_event("SOW 1 4 5 1 1 1 1 1 1 0 0 0 0 0 0 0 1", names), "bob sows pit 4 (5 seeds)")
        self.assertEqual(M.describe_event("SOW 0 1 1 " + "0 " * 13 + "1", names),
                         "%s sows pit 1 (1 seed)" % LONG)
        self.assertEqual(M.describe_event("CAP 0 7", names), "%s captures 7 seeds" % LONG)
        self.assertEqual(M.describe_event("AGAIN 1", names), "bob ends in the store and moves again")
        self.assertEqual(M.describe_event("GRAND 1", names), "Grand slam: bob captures nothing")
        self.assertEqual(M.describe_event("SWEEP 1 5 nofeed", names),
                         "bob takes the 5 seeds left on their row (no move can feed the other side)")
        self.assertIsNone(M.describe_event("TURN 1", names))
        self.assertIsNone(M.describe_event("END win 0 25-20", names))
        from adapters import web as W
        self.assertEqual(W.describe("CAP 1 3", ["ann", "bob"], "mancala/1"), "bob captures 3 seeds")


def tiles(tc, lines, seat):
    return [p for p, _, _ in board_image(tc, M, lines, seat).used]


class WebBoard(unittest.TestCase):
    def test_opening_for_each_seat_and_a_watcher(self):
        html = web_page(self, M, OPEN, 0)
        self.assertEqual(legal_links(html), ["1", "2", "3", "4", "5", "6"])
        self.assertIn("click one of your pits", html)
        self.assertIn("/img/mancala/h4.gif", tiles(self, OPEN, 0))
        self.assertIn("Store rules, 4 seeds a pit", html)
        for seat in (1, -1):
            html = web_page(self, M, OPEN, seat)
            self.assertEqual(legal_links(html), [])
            self.assertFalse([p for p in tiles(self, OPEN, seat) if p.startswith("/img/mancala/h")])
        html = web_page(self, M, SEAT1, 1, names=("ann", LONG))
        self.assertEqual(legal_links(html), ["1", "2", "3", "4", "5", "6"])
        c = board_image(self, M, SEAT1, 1)
        last = [y for p, _, y in c.used if p == "/img/mancala/l0.gif"]
        legal = [y for p, _, y in c.used if p.startswith("/img/mancala/h")]
        self.assertTrue(last and legal and max(last) < min(legal))  # seat 0's last move, on the top row
        self.assertIn("%s (you)" % LONG, html)
        for mv in legal_links(html):
            self.assertTrue(M.MOVE_RE.match(mv))

    def test_crowded_and_must_feed_and_over(self):
        html = web_page(self, M, CROWDED, 0)
        self.assertEqual(sorted(legal_links(html)), ["2", "5", "6"])
        c = board_image(self, M, CROWDED, 0)
        words = [w for w, _, _ in c.words]
        self.assertIn("30", words)                                  # every count is written under its pit
        self.assertIn("26", words)
        self.assertIn("/img/mancala/p12.gif", [p for p, _, _ in c.used])    # 26 seeds: the fullest picture
        self.assertIn("Store rules, 6 seeds a pit", html)
        html = web_page(self, M, OWARE_FEED, 0)
        self.assertEqual(legal_links(html), ["6"])
        self.assertIn("Oware rules (abapa)", html)
        html = web_page(self, M, OVER, 0)
        self.assertEqual(legal_links(html), [])
        self.assertIn("25", [w for w, _, _ in board_image(self, M, OVER, 0).words])

    def test_images(self):
        im = M.images()
        self.assertEqual(len(im), 1 + 3 * 13)
        self.assertTrue(all(p.startswith("/img/mancala/") for p in im))
        self.assertTrue(all(M.MOVE_RE.match(t) for t in "123456"))
        self.assertFalse(any(M.MOVE_RE.match(t) for t in ("0", "7", "12", "1\r", "a1", "")))


class Screens(unittest.TestCase):
    def test_full_screen_every_seat(self):
        for me in (0, 1, -1):
            lines = OPEN if me != 1 else SEAT1
            for mode, (shown, reqs) in ansi_screens(self, M, lines, me, keys=["RIGHT", "ENTER"]).items():
                self.assertIn("Mancala", shown)
                self.assertIn("store rules", shown)
                self.assertIn(LONG.upper(), shown)
                if me < 0:
                    self.assertIn("Watching", shown)
                    self.assertFalse([r for r in reqs if r.startswith("MOV")])
                else:
                    self.assertIn("bottom row, store right", shown)
                    self.assertIn("MOV 3 2", reqs, mode)            # cursor on pit 1, right to 2, Enter
        shown = ansi_screens(self, M, OPEN, 0)["pc"][0]
        self.assertIn("╔═════╦════╤", shown)
        self.assertIn("» 4«", shown)
        shown = ansi_screens(self, M, OPEN, 0)["ansi"][0]
        self.assertIn("> 4<", shown)
        self.assertIn(". 4.", shown)

    def test_left_wraps_and_typed_and_clicked_moves(self):
        _, reqs = ansi_screens(self, M, OPEN, 0, keys=["LEFT", "ENTER"])["utf8"]
        self.assertIn("MOV 3 6", reqs)
        _, reqs = ansi_screens(self, M, OPEN, 0, keys=["4", "ENTER"])["pc"]
        self.assertIn("MOV 3 4", reqs)
        _, reqs = ansi_screens(self, M, OPEN, 0, keys=list("pit 5") + ["ENTER"])["ansi"]
        self.assertIn("MOV 3 5", reqs)
        # seat 0's pit 4 is drawn at x 24-27, rows 9-11
        _, reqs = ansi_screens(self, M, OPEN, 0, keys=[("CLICK", 25, 10)])["pc"]
        self.assertIn("MOV 3 4", reqs)
        _, reqs = ansi_screens(self, M, OPEN, 1, keys=[("CLICK", 25, 10), "ENTER"])["pc"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")])  # not seat 1's move

    def test_crowded_must_feed_and_over(self):
        for mode, (shown, reqs) in ansi_screens(self, M, CROWDED, 0, keys=["ENTER"]).items():
            self.assertIn("30", shown)
            self.assertIn("26", shown)
            self.assertIn("Store rules, 6 seeds a pit", shown)
            self.assertIn("MOV 3 2", reqs)                        # the cursor starts on the first legal pit
        for mode, (shown, reqs) in ansi_screens(self, M, OWARE_FEED, 0, keys=["LEFT", "LEFT"]).items():
            self.assertIn("Oware rules", shown)
            self.assertIn("feed", shown)
            self.assertIn("More than 24 seeds wins.", shown)
        for me in (0, 1, -1):
            for mode, (shown, reqs) in ansi_screens(self, M, OVER, me, keys=["ENTER"]).items():
                self.assertFalse([r for r in reqs if r.startswith("MOV")])

    def test_plain(self):
        text = plain_screen(self, M, OPEN, 0)
        self.assertIn("|  4*|  4*|", text)
        self.assertIn("Your move (a pit, like 1) >", text)
        self.assertIn("bottom store  0", text)
        text = plain_screen(self, M, CROWDED, 0)
        self.assertIn("+ 30 |", text)
        self.assertIn(" 12*|", text)
        text = plain_screen(self, M, CROWDED, -1)
        self.assertNotIn("*", text)
        self.assertIn("you are watching", text)
        text = plain_screen(self, M, OVER, 1)
        self.assertIn("Oware rules", text)
        self.assertTrue(text.rstrip().endswith(">"))
        self.assertIn("Waiting for an opponent", plain_screen(self, M, WAITING, 0))

    def test_typed_words(self):
        g = M.PlainScreen.__new__(M.PlainScreen)
        self.assertEqual(g.move_word("3", ["3"]), "3")
        self.assertEqual(g.move_word("sow", ["sow", "6"]), "6")
        self.assertIsNone(g.move_word("7", ["7"]))
        self.assertIsNone(g.move_word("d3", ["d3"]))


if __name__ == "__main__":
    unittest.main()
