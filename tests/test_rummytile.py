"""rummytile/1 (Rummy Tile Classic): the plugin's rules, a three-player game over the protocol, and the web
and telnet frontends (adapters/games/rummytile.py)."""
import asyncio
import importlib.util
import os
import random
import re
import unittest
from collections import Counter

from game_checks import LONG, ansi_screens, plain_screen, reads_typed, web_page
from test_core import ROOT, Client

from adapters.games import rummytile as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("rt_plugin", os.path.join(ROOT, "plugins", "rummytile", "plugin.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

TILES = Counter(P.all_tiles())


def new(players=2, seed=1):
    return P.op_new({"players": players, "options": {}, "seed": seed})["state"]


def state(racks, table=(), melded=None, pool=None, turn=0):
    """A state set up by hand: racks and table as given, the pool the rest of the tiles (or as given)."""
    n = len(racks)
    used = Counter(t for r in racks for t in r) + Counter(t for s in table for t in s)
    rest = list((TILES - used).elements())
    st = {"n": n, "pool": rest if pool is None else list(pool), "racks": [P.sort_rack(r) for r in racks],
          "table": [P.tidy(list(s)) for s in table], "melded": list(melded or [False] * n), "turn": turn,
          "over": False, "result": None, "scores": None, "passes": 0, "start": None, "steps": []}
    P.begin_turn(st, turn, [])
    return st


def play(st, seat, *tokens):
    r = P.op_apply({"state": st, "seat": seat, "move": list(tokens)})
    assert "error" not in r, (tokens, r)
    return r


def refused(tc, st, seat, *tokens):
    r = P.op_apply({"state": st, "seat": seat, "move": list(tokens)})
    tc.assertIn("error", r, tokens)
    return r["error"]


def end(st, seat):
    return P.op_apply({"state": st, "seat": seat, "move": ["!end"]})


def check_end(st, seat):
    return P.op_apply({"state": st, "seat": seat, "move": ["!end"], "check": True})


def all_tiles_of(st):
    return Counter(st["pool"]) + Counter(t for r in st["racks"] for t in r) + Counter(t for s in st["table"] for t in s)


def wire(st, seat):
    return P.op_view({"state": st, "seat": seat})["wire"]


# ---- a simple player, used for whole games -----------------------------------------------

def melds_in(rack):
    """Disjoint runs and groups (no jokers) in a rack, greedily: runs first."""
    have = Counter(t for t in rack if t != "J")
    out = []
    for c in P.COLOURS:
        run = []
        for n in range(1, 15):
            if n <= 13 and have[c + str(n)]:
                run.append(c + str(n))
                continue
            if len(run) >= 3:
                out.append(run)
                for t in run:
                    have[t] -= 1
            run = []
    for n in range(1, 14):
        cols = [c + str(n) for c in P.COLOURS if have[c + str(n)]]
        if len(cols) >= 3:
            out.append(cols)
            for t in cols:
                have[t] -= 1
    return out


def plan(rack, table, melded):
    """The steps a simple player makes: its runs and groups as new sets (once they reach 30 for a first
    meld), then, after the first meld, single tiles (jokers too) laid onto sets where they fit."""
    rack, table = list(rack), [list(s) for s in table]
    melds = melds_in(rack)
    if not melded and sum(P.value(m) for m in melds) < P.MELD:
        return []
    steps = []
    for m in melds:
        steps.append("".join(m) + "-n")
        table.append(P.tidy(m))
        for t in m:
            rack.remove(t)
    if melded:
        changed = True
        while changed and rack:
            changed = False
            for t in list(rack):
                for k, s in enumerate(table):
                    if len(s) < 13 and P.kind(s + [t]) != "x":
                        steps.append("%s-%d" % (t, k + 1))
                        table[k] = P.tidy(s + [t])
                        rack.remove(t)
                        changed = True
                        break
    return steps


def random_game(tc, players, seed, mischief=True):
    """A whole game between simple players, with some wrong steps, undos and unready ENDs thrown in.
    Checks after every request that no tile is lost or made. Returns (final state, events)."""
    rnd = random.Random(seed)
    st = new(players, seed)
    events = []
    for _ in range(3000):
        seat = st["turn"]
        if mischief and rnd.random() < 0.15 and st["table"] and st["melded"][seat]:
            # rearrange the table into something invalid, then END: undone, and a draw
            s = rnd.randrange(len(st["table"]))
            if len(st["table"][s]) > 1:
                st = play(st, seat, "%d/2" % (s + 1))["state"]
                if rnd.random() < 0.5:
                    st = play(st, seat, "!undo")["state"]
        for step in plan(st["racks"][seat], st["table"], st["melded"][seat]):
            if mischief and rnd.random() < 0.1:
                P.op_apply({"state": st, "seat": seat, "move": ["99-98"]})        # refused, nothing changes
            r = play(st, seat, step)
            st = r["state"]
            events += r["events"]
            tc.assertEqual(all_tiles_of(st), TILES)
        r = end(st, seat)
        tc.assertNotIn("error", r)
        st = r["state"]
        events += r["events"]
        tc.assertEqual(all_tiles_of(st), TILES)
        for s in st["table"]:
            tc.assertNotEqual(P.kind(s), "x", st["table"])       # a committed table is always valid
        if "end" in r:
            return st, events + ["END " + r["end"]]
    tc.fail("game %d did not end" % seed)


# ---- the rules ----------------------------------------------------------------------------

class Sets(unittest.TestCase):
    def test_runs_and_groups(self):
        for s in (["R1", "R2", "R3"], ["B11", "B12", "B13"], ["K4", "J", "K6"], ["Y1"] + ["Y%d" % n for n in range(2, 14)],
                  ["R5", "J", "J"], ["J", "R12", "R13"]):
            self.assertEqual(P.kind(s), "r", s)
        for s in (["R7", "B7", "K7"], ["R7", "B7", "K7", "Y7"], ["R7", "J", "K7"], ["R7", "B7", "J", "J"]):
            self.assertEqual(P.kind(s), "g", s)
        for s in (["R1", "R2"], ["R12", "R13", "R1"], ["R1", "R2", "B3"], ["R7", "R7", "B7"],
                  ["R7", "B7", "K7", "Y7", "J"], ["R1", "R3", "R5", "J"], ["R1", "R1", "R2", "R3"], ["J", "J"],
                  ["R%d" % n for n in range(1, 14)] + ["J"]):
            self.assertEqual(P.kind(s), "x", s)

    def test_tidy_order(self):
        self.assertEqual(P.tidy(["R7", "J", "R5"]), ["R5", "J", "R7"])            # the joker in its gap
        self.assertEqual(P.tidy(["R12", "J", "R11"]), ["R11", "R12", "J"])        # spare joker at the top
        self.assertEqual(P.tidy(["R13", "J", "R12"]), ["J", "R12", "R13"])        # ... or below 13
        self.assertEqual(P.tidy(["Y5", "J", "B5", "R5"]), ["R5", "B5", "Y5", "J"])
        self.assertEqual(P.tidy(["Y5", "R2", "J", "B9"]), ["R2", "B9", "Y5", "J"])  # not a set: color order

    def test_values_and_joker_options(self):
        self.assertEqual(P.value(["R11", "R12", "J"]), 36)          # J is 13
        self.assertEqual(P.value(["R7", "J", "K7"]), 21)
        self.assertEqual(P.joker_options(["R5", "J", "R7"]), {"R6"})
        self.assertEqual(P.joker_options(["R11", "R12", "J"]), {"R10", "R13"})
        self.assertEqual(P.joker_options(["R7", "B7", "J"]), {"K7", "Y7"})
        self.assertEqual(P.joker_options(["R7", "B7", "K7", "J"]), {"Y7"})
        self.assertEqual(P.rack_value(["R1", "J", "B13"]), 44)


class Rules(unittest.TestCase):
    def test_deal(self):
        for n in (2, 3, 4):
            r = P.op_new({"players": n, "options": {}, "seed": 7})
            st = r["state"]
            self.assertEqual([len(x) for x in st["racks"]], [14] * n)
            self.assertEqual(len(st["pool"]), 106 - 14 * n)
            self.assertEqual(all_tiles_of(st), TILES)
            self.assertEqual(r["events"], ["DEAL 14 %d" % (106 - 14 * n), "TURN 0"])
        self.assertEqual(new(3, 9), new(3, 9))                      # the seed decides everything
        self.assertNotEqual(new(3, 9)["racks"], new(3, 10)["racks"])
        for bad in (1, 5):
            self.assertIn("error", P.op_new({"players": bad, "options": {}, "seed": 1}))
        self.assertIn("error", P.op_new({"players": 2, "options": {"size": "9"}, "seed": 1}))
        self.assertIn("error", P.op_new({"players": 2, "options": {"players": "3"}, "seed": 1}))
        self.assertEqual(P.op_info({})["min"], 2)
        self.assertEqual(P.op_info({})["max"], 4)
        self.assertEqual(P.op_moves({"state": new(), "seat": 0}), {"check": True})
        self.assertNotIn("error", P.op_new({"players": 3, "rules": "standard", "options": {}, "seed": 1}))

    def test_a_seat_drops_out(self):
        """op_drop (for a core that lets the others play on after a resignation or a timeout)."""
        st = state([["R1", "R2", "R3", "B9"], ["K5", "K6"], ["Y1", "J"]], melded=[True] * 3)
        st = play(st, 0, "R1R2R3-n")["state"]
        r = P.op_drop({"state": st, "seat": 0, "why": "timeout"})
        self.assertEqual(r["events"], ["UNDONE 0 none", "GONE 0 timeout", "TURN 1"])
        st = r["state"]
        self.assertEqual(st["table"], [])                 # the staged turn is undone
        self.assertIn("G 1 0 0", wire(st, 1))
        self.assertIn("error", P.op_drop({"state": st, "seat": 0}))
        r = end(st, 1)                                     # seat 1 draws; seat 0 is skipped from now on
        self.assertEqual(r["events"][-1], "TURN 2")
        st = r["state"]
        r = end(st, 2)
        self.assertEqual(r["events"][-1], "TURN 1")
        # a seat not on turn leaves: no turn change; the last seat playing wins
        r = P.op_drop({"state": r["state"], "seat": 2})
        self.assertEqual(r["events"][0], "GONE 2 resign")
        self.assertEqual(r["state"]["over"], True)
        self.assertTrue(r["end"].startswith("win 1 "))
        scores = [int(x) for x in r["end"].split()[2:]]
        self.assertEqual(scores[0], -P.rack_value(["R1", "R2", "R3", "B9"]))     # a seat that left loses its rack
        self.assertEqual(sum(scores), 0)
        # with the pool out, only the seats still playing need to pass
        st = state([["R1"], ["K13"], ["J"]], pool=[], melded=[True] * 3)
        st = P.op_drop({"state": st, "seat": 2})["state"]
        r = end(end(st, 0)["state"], 1)
        self.assertEqual(r["end"], "win 0 42 -12 -30")
        # an old state without the gone list still plays
        st = new(2, 3)
        del st["gone"]
        self.assertNotIn("error", end(st, 0))

    def test_first_meld(self):
        st = state([["R10", "R11", "R12", "B1", "B2", "B3", "K5"], ["Y1", "Y2", "Y3"]],
                   table=[["K1", "K2", "K3"]], melded=[False, True])
        # the sets on the table cannot be touched before the first meld
        self.assertIn("first meld", refused(self, st, 0, "K5-1"))
        self.assertIn("first meld", refused(self, st, 0, "1/2"))
        self.assertIn("first meld", refused(self, st, 0, "1.K1-n"))
        # 6 points is not enough: END undoes the turn and draws
        r = play(st, 0, "B1B2B3-n")
        self.assertEqual(r["staged"], "more")
        self.assertIn("SET 2 r B1 B2 B3", r["events"])
        self.assertEqual(wire(r["state"], 0)[-1], "TP 3 6 meld")
        self.assertIn("30 needed", check_end(r["state"], 0)["error"])
        r2 = end(r["state"], 0)
        self.assertEqual(r2["events"][0], "UNDONE 0 meld")
        self.assertTrue(r2["events"][1].startswith("DRAW 0 "))
        self.assertEqual(len(r2["state"]["racks"][0]), 8)
        self.assertEqual(r2["state"]["table"], [["K1", "K2", "K3"]])
        self.assertFalse(r2["state"]["melded"][0])
        # 33 + 6 points from the rack: the meld is made, and new sets may be worked on in the same turn
        r = play(st, 0, "R10R11-n")
        r = play(r["state"], 0, "R12-2")
        self.assertEqual(r["staged"], "done")
        r = play(r["state"], 0, "B1B2B3-n")
        self.assertEqual(check_end(r["state"], 0), {"info": "ok 6 tiles meld 39"})
        r = end(r["state"], 0)
        self.assertEqual(r["events"], ["MELD 0 39", "DONE 0 6", "TURN 1"])
        self.assertTrue(r["state"]["melded"][0])
        # the next turn may change the table
        st = r["state"]
        st["turn"] = 0
        P.begin_turn(st, 0, [])
        r = play(st, 0, "K5-1")
        self.assertNotIn("error", r)
        # a joker counts as the tile it stands for in the first meld
        st = state([["R9", "J", "R11", "K2"], ["Y1"]])
        r = end(play(st, 0, "R9JR11-n")["state"], 0)
        self.assertEqual(r["events"][0], "MELD 0 30")

    def test_rearranging(self):
        st = state([["R8", "Y8", "K9"], ["B1"]],
                   table=[["R3", "R4", "R5", "R6"], ["R7", "B7", "K7", "Y7"], ["Y9", "Y10", "Y11"]],
                   melded=[True, True])
        # split a run, take a tile from a group of four, add one from the rack: R3 R4 R5 | R6 R7 R8
        r = play(st, 0, "1/4")
        self.assertEqual(r["events"], ["SPLIT 0 1 4", "SET 1 r R3 R4 R5", "SET 4 x R6"])
        r = play(r["state"], 0, "2.R7-4")
        self.assertEqual(r["events"], ["MOVE 0 2 R7 4", "SET 2 g B7 K7 Y7", "SET 4 x R6 R7"])
        self.assertIn("nothing was played", check_end(r["state"], 0)["error"])
        r = play(r["state"], 0, "R8-4")
        self.assertEqual(r["events"], ["PUT 0 R8 4", "SET 4 r R6 R7 R8"])
        self.assertEqual(r["staged"], "done")
        r = play(r["state"], 0, "Y8-3")
        self.assertEqual(r["state"]["table"][2], ["Y8", "Y9", "Y10", "Y11"])
        r = play(r["state"], 0, "3.Y11-n", "K9-5")   # two tokens, one step
        self.assertEqual(r["state"]["table"][4], ["K9", "Y11"])
        self.assertEqual(r["events"][0], "MOVE 0 3 Y11 n")
        self.assertEqual(r["staged"], "more")
        self.assertIn("set 5 is not valid", check_end(r["state"], 0)["error"])
        r = play(r["state"], 0, "!undo")
        self.assertEqual(r["events"], ["UNDO 0"])
        self.assertEqual(r["state"]["table"][2], ["Y8", "Y9", "Y10", "Y11"])
        self.assertEqual(len(r["state"]["table"]), 4)
        # a whole set joins another; an emptied set goes away and the later ones move up
        r = play(r["state"], 0, "4-1")
        self.assertEqual(r["events"], ["MERGE 0 4 1", "DEL 4", "SET 1 r R3 R4 R5 R6 R7 R8"])
        self.assertEqual(check_end(r["state"], 0), {"info": "ok 2 tiles"})
        r = end(r["state"], 0)
        self.assertEqual(r["events"], ["DONE 0 2", "TURN 1"])
        self.assertEqual(r["state"]["racks"][0], ["K9"])
        self.assertEqual(r["state"]["table"], [["R3", "R4", "R5", "R6", "R7", "R8"], ["B7", "K7", "Y7"],
                                               ["Y8", "Y9", "Y10", "Y11"]])

    def test_refused_steps(self):
        st = state([["R7", "R8"], ["B1"]], table=[["R3", "R4", "R5"], ["K1", "K2", "K3"]], melded=[True, True])
        for tok, why in (("B9-1", "no B9"), ("R7R7-1", "only 1 R7"), ("9-1", "no set 9"), ("1.5-2", "no tile 5"),
                         ("1.K2-2", "no K2"), ("1-1", "join itself"), ("1/1", "split"), ("1.1-1", "already"),
                         ("1.R3-r", "rack"), ("xyz", "not a step"), ("reset", "nothing to start over")):
            self.assertIn(why, refused(self, st, 0, tok), tok)
        self.assertIn("not your turn", refused(self, st, 1, "B1-n"))
        self.assertIn("nothing to take back", refused(self, st, 0, "!undo"))
        self.assertIn("error", P.op_apply({"state": st, "seat": 0, "move": ["R7-1", "!end"]}))
        # all or nothing: a bad second token leaves the first undone
        r = P.op_apply({"state": st, "seat": 0, "move": ["R7-n", "9-9"]})
        self.assertIn("error", r)
        # a set holds at most 13 tiles
        st = state([["R13", "B1"], ["B2"]], table=[["R%d" % n for n in range(1, 13)], ["Y1", "Y2", "Y3"]],
                   melded=[True, True])
        r = play(st, 0, "R13-1")
        self.assertIn("at most 13", refused(self, r["state"], 0, "2.Y1-1"))
        # a check changes nothing
        r = P.op_apply({"state": st, "seat": 0, "move": ["R13-1"], "check": True})
        self.assertEqual(r, {"info": "ok"})
        self.assertEqual(st["table"][0], ["R%d" % n for n in range(1, 13)])

    def test_back_to_rack_and_reset(self):
        st = state([["R6", "B9"], ["B1"]], table=[["R3", "R4", "R5"]], melded=[True, True])
        r = play(st, 0, "R6-1")
        r = play(r["state"], 0, "1.R6-r")
        self.assertEqual(r["events"], ["BACK 0 1 R6", "SET 1 r R3 R4 R5"])
        self.assertEqual(r["state"]["racks"][0], ["R6", "B9"])
        r = play(r["state"], 0, "R6-1")
        r = play(r["state"], 0, "1/3")
        self.assertIn("rack", refused(self, r["state"], 0, "1.1-r"))      # R3 was on the table already
        r = play(r["state"], 0, "reset")
        self.assertEqual(r["events"], ["RESET 0"])
        self.assertEqual(r["state"]["table"], st["table"])
        self.assertEqual(r["state"]["racks"][0], st["racks"][0])
        r = play(r["state"], 0, "!undo")              # the reset is a step too
        self.assertEqual(r["state"]["table"], [["R3", "R4"], ["R5", "R6"]])

    def test_jokers(self):
        # the usual rule: R6 from the rack frees the joker standing for R6, which is used again
        st = state([["R6", "K9", "K10", "B2"], ["B1"]], table=[["R5", "J", "R7"]], melded=[True, True])
        r = play(st, 0, "R6-1")
        self.assertEqual(r["state"]["table"][0], ["R5", "R6", "R7", "J"])
        r = play(r["state"], 0, "1.J-n", "K9K10-2")
        self.assertEqual(r["state"]["table"], [["R5", "R6", "R7"], ["K9", "K10", "J"]])
        self.assertEqual(check_end(r["state"], 0), {"info": "ok 3 tiles"})
        self.assertEqual(end(r["state"], 0)["events"][0], "DONE 0 3")
        # a joker taken out of a run of four without its tile from the rack: undone, and a draw
        st = state([["K9", "K10", "R4"], ["B1"]], table=[["R5", "R6", "R7", "J"]], melded=[True, True])
        r = play(st, 0, "1.J-n", "K9K10-2")
        self.assertEqual(wire(r["state"], 0)[-1], "TP 2 - joker")
        self.assertIn("joker", check_end(r["state"], 0)["error"])
        r2 = end(r["state"], 0)
        self.assertEqual(r2["events"][0], "UNDONE 0 joker")
        self.assertEqual(r2["state"]["table"], [["R5", "R6", "R7", "J"]])
        # ... but R4 (an end the joker could have been) frees it
        r = play(r["state"], 0, "R4-1")
        self.assertEqual(end(r["state"], 0)["events"][0], "DONE 0 3")
        # in a group of three, either missing color frees the joker
        st = state([["Y7", "R1", "R2"], ["B1"]], table=[["R7", "B7", "J"]], melded=[True, True])
        r = play(st, 0, "Y7-1", "1.J-n", "R1R2-2")
        self.assertEqual(end(r["state"], 0)["events"][0], "DONE 0 3")
        # a joker moved within the table that still stands for the same tile needs nothing
        st = state([["B9"], ["B1"]], table=[["R3", "R4", "J", "R6", "R7"]], melded=[True, True])
        r = play(st, 0, "1/3", "B9-n")
        self.assertEqual(r["state"]["table"], [["R3", "R4"], ["R6", "R7", "J"], ["B9"]])
        r = play(r["state"], 0, "2-1")
        self.assertEqual(r["state"]["table"], [["R3", "R4", "J", "R6", "R7"], ["B9"]])
        r = play(r["state"], 0, "2.B9-r")
        self.assertEqual(end(r["state"], 0)["events"][0], "UNDONE 0 none")
        # before the first meld a joker on the table cannot be taken
        st = state([["R6", "K9", "K10", "K11"], ["B1"]], table=[["R5", "J", "R7"]])
        self.assertIn("first meld", refused(self, st, 0, "R6-1"))
        self.assertEqual(P.jokers_ok([["R5", "J", "R7"]], [["R5", "R6", "R7"], ["K9", "K10", "J"]], Counter()),
                         False)

    def test_invalid_end_is_undone_with_a_draw(self):
        st = state([["R6", "B9"], ["B1"]], table=[["R3", "R4", "R5"], ["K1", "K2", "K3"]], melded=[True, True],
                   pool=["Y13", "Y12"])
        r = play(st, 0, "R6-1", "B9-2")
        self.assertIn("set 2 is not valid", check_end(r["state"], 0)["error"])
        self.assertIn("set 2 is not valid", P.op_view({"state": r["state"], "seat": 0})["status"])
        r = end(r["state"], 0)
        self.assertEqual(r["events"], ["UNDONE 0 sets", "DRAW 0 1", "TURN 1"])
        st2 = r["state"]
        self.assertEqual(st2["table"], st["table"])
        self.assertEqual(st2["racks"][0], ["R6", "B9", "Y12"])
        self.assertNotIn("Y12", " ".join(r["events"]))              # the drawn tile is never named
        # playing nothing draws; with the pool empty it is a pass
        r = end(st2, 1)
        self.assertEqual(r["events"], ["DRAW 1 0", "TURN 0"])
        r = end(r["state"], 0)
        self.assertEqual(r["events"], ["PASS 0", "TURN 1"])
        # a rearranged table with nothing from the rack is undone too
        st = state([["B9"], ["B1"]], table=[["R3", "R4", "R5", "R6"]], melded=[True, True])
        r = end(play(st, 0, "1/2")["state"], 0)
        self.assertEqual(r["events"][0], "UNDONE 0 none")
        self.assertEqual(r["state"]["table"], [["R3", "R4", "R5", "R6"]])

    def test_going_out_and_scores(self):
        st = state([["R1", "R2", "R3"], ["B5", "J"], ["K13", "K12"]], melded=[True, True, True], turn=0)
        r = end(play(st, 0, "R1R2R3-n")["state"], 0)
        self.assertEqual(r["events"], ["DONE 0 3", "OUT 0"])
        self.assertEqual(r["end"], "win 0 60 -35 -25")
        self.assertEqual(P.op_result({"state": r["state"]}), {"over": True, "scores": [60, -35, -25]})
        v = wire(r["state"], 1)
        self.assertIn("W -", v)
        self.assertIn("T 0 60", v)
        self.assertNotIn("TP", " ".join(v))
        self.assertTrue(P.op_view({"state": r["state"], "seat": 1})["status"].startswith("Game over: seat 0 wins"))
        self.assertIn("error", P.op_apply({"state": r["state"], "seat": 1, "move": ["!end"]}))

    def test_pool_out_ends_after_every_player_passes(self):
        st = state([["R1", "B4"], ["K13"], ["J"]], pool=[], melded=[True] * 3)
        r = end(st, 0)
        r = end(r["state"], 1)
        self.assertNotIn("end", r)
        r = end(r["state"], 2)
        self.assertEqual(r["events"], ["PASS 2"])
        self.assertEqual(r["end"], "win 0 33 -8 -25")         # 5 is lowest: 13-5 and 30-5
        # a play in between starts the count again
        st = state([["R1", "B4"], ["K13", "K11", "K12"], ["J"]], table=[], pool=[], melded=[True] * 3)
        r = end(st, 0)
        r = end(play(r["state"], 1, "K11K12K13-n")["state"], 1)
        self.assertEqual(r["events"][-1], "OUT 1")
        # a tie for the lowest rack is a draw, the winners sharing
        st = state([["R5"], ["B5"], ["K9"]], pool=[], melded=[True] * 3)
        for seat in (0, 1, 2):
            r = end(st, seat)
            st = r["state"]
        self.assertEqual(r["end"], "draw 2 2 -4")

    def test_hidden_racks(self):
        st = new(3, 4)
        for seat in (0, 1, 2):
            v = wire(st, seat)
            racks = [l for l in v if l.startswith("H ")]
            self.assertEqual(Counter(t for l in racks for t in l.split()[1:]), Counter(st["racks"][seat]))
        self.assertFalse([l for l in wire(st, -1) if l.startswith("H")])
        self.assertFalse([l for l in wire(st, 7) if l.startswith("H")])
        _, events = random_game(self, 3, 11)
        for e in events:
            if e.startswith("DRAW ") or e.startswith("DEAL "):
                self.assertTrue(re.match(r"^(DRAW \d \d+|DEAL 14 \d+)$", e), e)
            self.assertTrue(len(e) < 64, e)

    def test_long_lines(self):
        """A crowded table and a long rack: every view line stays under 80 bytes with its CR LF."""
        rack = [c + str(n) for c in "RB" for n in range(1, 14)] + ["J", "J", "K1", "K2", "K3"]
        table = [["Y%d" % n for n in range(1, 14)]] + [["K%d" % n, "K%d" % (n + 1), "K%d" % (n + 2)] for n in (4, 7, 10)]
        st = state([rack, ["K13"]], table=table, melded=[True, True])
        st = play(st, 0, "R10R11R12R13-n")["state"]
        st = play(st, 0, "1.Y13-5", "1.Y12-5", "1.Y11-5", "1.Y10-5", "1.Y9-5", "1.Y8-5", "1.Y7-5", "1.Y6-5",
                  "1.Y5-5")["state"]
        v = wire(st, 0)
        self.assertEqual(len([l for l in v if l.startswith("H ")]), 2)
        for l in v:
            self.assertLessEqual(len(l) + 2, 80, l)
        self.assertLess(sum(len(l) + 2 for l in v), 2000)

    def test_random_games_finish(self):
        ends = Counter()
        for seed in range(12):
            players = 2 + seed % 3
            st, events = random_game(self, players, seed)
            self.assertTrue(st["over"])
            self.assertEqual(len(st["scores"]), players)
            self.assertTrue(re.match(r"^END (win \d|draw)( -?\d+){%d}$" % players, events[-1]), events[-1])
            ends["out" if "OUT" in " ".join(events[-4:]) else "blocked"] += 1
            if "draw" in events[-1]:
                self.assertLessEqual(sum(st["scores"]), 0)
            else:
                self.assertEqual(sum(st["scores"]), 0)
        self.assertEqual(sum(ends.values()), 12)


# ---- a game over the protocol ----------------------------------------------------------------

class OverProtocol(unittest.IsolatedAsyncioTestCase):
    """Three players over the native protocol (NEW ... players=3)."""

    async def asyncSetUp(self):
        from parlord import core as C
        self.C = C
        self.saved = (C.Limits.rate_per_sec, C.Limits.rate_burst)
        C.Limits.rate_per_sec, C.Limits.rate_burst = 1e6, 10 ** 6
        self.srv = C.Server(os.path.join(ROOT, "plugins"), name="test", auth="none")      # sign-in: test_auth
        await self.srv.load_plugins()
        self.server = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0, limit=1024)
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients = []

    async def connect(self):
        c = await Client.connect(self.port)
        self.clients.append(c)
        return c

    async def asyncTearDown(self):
        for c in self.clients:
            c.w.close()
        self.server.close()
        try:
            await asyncio.wait_for(self.server.wait_closed(), 5)
        except asyncio.TimeoutError:
            pass
        for p in self.srv.plugins.values():
            p.stop()
        self.C.Limits.rate_per_sec, self.C.Limits.rate_burst = self.saved

    async def view(self, c, tid):
        lines = await c.req("VUE %s GMT" % tid)
        self.assertEqual(lines[-1], "OK")
        for l in lines:
            self.assertLessEqual(len(l) + 2, 80, l)
        return lines, G.parse_view(lines[:-1])

    async def test_three_player_game(self):
        names = ["ann", "bob", "cy"]
        cs = [await self.connect() for _ in names]
        for c, n in zip(cs, names):
            self.assertTrue((await c.req("HELO " + n))[-1].startswith("OK"))
        gam = await cs[0].req("GAM")
        self.assertIn("GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves,drop", gam)
        self.assertIn("BADOPT", (await cs[0].req("NEW rummytile standard players=5"))[-1])
        self.assertIn("BADOPT", (await cs[0].req("NEW rummytile standard size=9"))[-1])
        tid = (await cs[0].req("NEW rummytile standard players=3"))[-1].split()[1]
        self.assertIn(" 0/3 ", [l for l in await cs[0].req("LST") if l.startswith("TAB %s " % tid)][0])
        for c in cs:
            self.assertTrue((await c.req("JON %s" % tid))[-1].startswith("OK"))
        watcher = await self.connect()
        await watcher.req("HELO dee")
        self.assertEqual((await watcher.req("SEE %s" % tid))[-1], "OK")
        lines, v = await self.view(watcher, tid)
        self.assertFalse([l for l in lines if l.startswith("H ")])
        self.assertEqual(len(v["counts"]), 3)
        racks = {}
        for i, c in enumerate(cs):
            lines, v = await self.view(c, tid)
            racks[i] = v["rack"]
            self.assertEqual(len(v["rack"]), 14)
            self.assertFalse([l for l in lines if l.startswith("M ")])
        # nobody's view shows another rack
        for i, c in enumerate(cs):
            lines, _ = await self.view(c, tid)
            shown = Counter(t for l in lines if l.startswith("H ") for t in l.split()[1:])
            self.assertEqual(shown, Counter(racks[i]))
        # the staged-turn requests, before the game runs on
        turn = 0
        c = cs[turn]
        self.assertTrue((await c.req("UND %s" % tid))[0].startswith("ERR ILLEGAL"))
        self.assertTrue((await cs[1].req("MOV %s %s-n" % (tid, racks[1][0])))[0].startswith("ERR ILLEGAL"))
        self.assertIn("nothing was played", (await c.req("CHK %s !end" % tid))[0])
        r = await c.req("MOV %s %s-n" % (tid, racks[0][0]))
        self.assertEqual(r[-1], "OK more")
        self.assertTrue(any(" PUT 0 %s n" % racks[0][0] in l for l in r), r)
        self.assertTrue(any(" SET 1 x %s" % racks[0][0] in l for l in r), r)
        _, v = await self.view(cs[1], tid)
        self.assertEqual(v["sets"][0]["tiles"], [racks[0][0]])          # the staged table is public
        r = await c.req("UND %s" % tid)
        self.assertTrue(any(l.endswith(" UNDO 0") for l in r), r)
        self.assertTrue((await c.req("CHK %s 1-1" % tid))[0].startswith("ERR"))
        # now play the game out: simple players, every turn over the protocol
        done = None
        for _ in range(600):
            lines, v = await self.view(cs[turn], tid)
            if v["over"]:
                break
            self.assertEqual(v["turn"], turn)
            c = cs[turn]
            table = [s["tiles"] for s in v["sets"]]
            for step in plan(v["rack"], table, v["melded"][turn]):
                r = await c.req("MOV %s %s" % (tid, step))
                self.assertIn(r[-1], ("OK more", "OK done"), (step, r))
            if v["played"] is not None:
                chk = await c.req("CHK %s !end" % tid)
                _, after = await self.view(c, tid)
                self.assertEqual(chk[-1].startswith("OK"), after["played"][2] == "ok", (chk, after["played"]))
            r = await c.req("END %s" % tid)
            self.assertEqual(r[-1].rstrip(), "OK")
            ends = [l for l in r if " END " in l]
            if ends:
                done = ends[0].split(" ", 3)[3]
                break
            turn = (turn + 1) % 3
            self.assertTrue(any(l.endswith(" TURN %d" % turn) for l in r), r)
        self.assertIsNotNone(done)
        self.assertRegex(done, r"^END (win [012]|draw)( -?\d+){3}$")
        lines, v = await self.view(watcher, tid)
        self.assertTrue(v["over"])
        self.assertEqual(sorted(v["scores"]), [0, 1, 2])
        self.assertTrue((await cs[0].req("MOV %s reset" % tid))[0].startswith("ERR NOGAME"))
        # the log texts of the whole game
        log = await watcher.req("SIN %s 0" % tid)
        texts = [G.describe_event(l.split(" ", 3)[3], names) for l in log if l.startswith("EVT")]
        self.assertTrue(any(t and t.startswith("Game over: ") for t in texts), texts[-3:])
        for l in log:
            self.assertLessEqual(len(l) + 2, 80, l)


# ---- the frontends -------------------------------------------------------------------------------

NAMES4 = (LONG, LONG.upper(), "c" + LONG[1:], "d" + LONG[1:])


def view_lines(st, seat, seq=40, clock=True):
    v = P.op_view({"state": st, "seat": seat})
    out = ["V rummytile/1 %d" % seq] + v["geometry"] + v["wire"]
    if clock and not st["over"]:
        out.append("CLK %d 100 300" % st["turn"])
    return out + ["S " + v["status"]]


def midgame():
    """Seat 0 to play, melded, with a staged turn under way (an invalid set on the table)."""
    st = state([["R7", "B7", "Y8", "K9", "J", "R1", "B13"], ["B1", "B2"]],
               table=[["R3", "R4", "R5", "R6"], ["B7", "K7", "Y7"], ["Y9", "Y10", "Y11"]], melded=[True, False])
    return play(st, 0, "K9-n")["state"]


def crowded():
    """Four players and a table that needs more rows than the screen has: 15 long runs, one of 13 tiles,
    and a staged set at the end."""
    runs = [[c + str(n) for n in range(1, 7)] for c in "RBKY"] * 2 + [[c + str(n) for n in range(7, 14)]
                                                                     for c in "RBKY"] * 2
    table = runs[:15]
    table[0] = ["R%d" % n for n in range(1, 14)]
    table.remove(["R%d" % n for n in range(7, 14)])
    st = state([["J", "J", "Y7"], ["Y8", "Y9"], ["Y10", "Y11"], ["Y12", "Y13"]], table=table, melded=[True] * 4,
               pool=[])
    assert all_tiles_of(st) == TILES
    return play(st, 0, "JJ-n")["state"]


def bigrack():
    """A 60-tile rack: more rows than the rack area shows."""
    rack = [c + str(n) for c in "RBKY" for n in range(1, 14)] + ["R%d" % n for n in range(1, 9)]
    return state([rack, ["B1"]], table=[["Y9", "Y10", "Y11"]], melded=[True, True])


def over():
    st = state([["R1", "R2", "R3"], ["B5", "J"]], melded=[True, True])
    return end(play(st, 0, "R1R2R3-n")["state"], 0)["state"]


class Frontends(unittest.TestCase):
    def test_parse_view(self):
        v = G.parse_view(view_lines(midgame(), 0))
        self.assertTrue(v["rows"])
        self.assertEqual((v["turn"], v["pool"], v["counts"], v["melded"]), (0, v["pool"], [6, 2], [True, False]))
        self.assertEqual(v["sets"][3], {"kind": "x", "new": True, "tiles": ["K9"]})
        self.assertEqual(v["sets"][0], {"kind": "r", "new": False, "tiles": ["R3", "R4", "R5", "R6"]})
        self.assertEqual(v["played"], (1, None, "sets"))
        self.assertEqual(v["moves"], [])
        self.assertEqual(v["clock"], (0, 100, 300))
        self.assertIn("set 4 is not valid", v["status"])
        self.assertFalse(G.parse_view(["V rummytile/1 1"])["rows"])
        self.assertEqual(G.parse_view(view_lines(midgame(), 1))["rack"], ["B1", "B2"])
        self.assertEqual(G.parse_view(view_lines(over(), -1))["scores"], {0: 35, 1: -35})

    def test_describe(self):
        names = ["ann", "bob", "cy"]
        d = G.describe_event
        self.assertEqual(d("PUT 0 R7B7K7 n", names), "ann puts R7 B7 K7 into a new set")
        self.assertEqual(d("PUT 1 J 3", names), "bob puts J into set 3")
        self.assertEqual(d("MOVE 2 3 R7 5", names), "cy moves R7 from set 3 to set 5")
        self.assertEqual(d("MERGE 0 4 1", names), "ann joins set 4 onto set 1")
        self.assertEqual(d("SPLIT 0 1 4", names), "ann splits set 1 before its tile 4")
        self.assertEqual(d("BACK 0 1 R6", names), "ann takes R6 from set 1 back to the rack")
        self.assertEqual(d("UNDONE 1 sets", names), "bob's turn is undone: a set was not valid")
        self.assertEqual(d("DRAW 2 40", names), "cy draws a tile (40 left in the pool)")
        self.assertEqual(d("MELD 0 32", names), "ann makes the first meld (32 points)")
        self.assertEqual(d("SET 1 r R1 R2 R3", names), "")
        self.assertEqual(d("END win 1 -35 60 -25", names), "Game over: bob wins (ann -35, bob +60, cy -25)")
        self.assertEqual(d("END draw 2 2 -4", names), "Game over: a draw (ann +2, bob +2, cy -4)")
        self.assertIsNone(d("END resign 1", names))
        self.assertEqual(d("GONE 2 timeout", names), "cy ran out of time and leaves the game; the others play on")
        self.assertIsNone(d("JOIN 0 ann", names))

    def test_a_seat_that_left(self):
        st = state([["R1", "B4"], ["K13", "K1"], ["J", "Y2"]], melded=[True] * 3)
        st = P.op_drop({"state": st, "seat": 1})["state"]
        names = NAMES4[:3]
        self.assertEqual(G.parse_view(view_lines(st, 0))["gone"], [False, True, False])
        self.assertIn("left the game", web_page(self, G, view_lines(st, 0), 0, names=names))
        self.assertRegex(plain_screen(self, G, view_lines(st, 2), 2, names=names), names[1] + " +left")
        shown, _ = ansi_screens(self, G, view_lines(st, -1), -1, names=names)["ansi"]
        self.assertIn(" left", shown)

    def test_web_pages(self):
        for seat in (0, 1, -1):
            html = web_page(self, G, view_lines(midgame(), seat), seat)
            self.assertIn("R3", html)
            if seat == 0:
                self.assertIn("End turn", html)
                self.assertIn("/und.htm", html)
                self.assertIn('name="mv"', html)
            else:
                self.assertNotIn("/move.htm", html)
            self.assertEqual("Your rack" in html, seat >= 0)
        # every move the page links to is one /move.htm passes on
        for sel in ("", "R7B7", "J", "1.2", "4.1", "2", "B13"):
            html = web_page(self, G, view_lines(midgame(), 0), 0, sel=sel)
            moves = re.findall(r"mv=([^&\"]+)", html)
            self.assertTrue(moves or sel == "", sel)
            for m in moves:
                m = m.replace("%2F", "/")
                self.assertTrue(G.MOVE_RE.match(m), m)
            for s in re.findall(r"sel=([^&\"]+)", html):
                self.assertTrue(G.SEL_RE.match(s), s)
        html = web_page(self, G, view_lines(midgame(), 0), 0, sel="R7B7")
        self.assertIn("mv=R7B7-n", html)
        self.assertIn("mv=R7B7-1", html)
        self.assertIn("[R7]", html)
        html = web_page(self, G, view_lines(midgame(), 0), 0, sel="1.2")
        self.assertIn("mv=1.2-3", html)
        self.assertIn("mv=1%2F2", html)
        self.assertIn("mv=1.2-r", html)
        html = web_page(self, G, view_lines(midgame(), 0), 0, sel="2")
        self.assertIn("mv=2-3", html)
        html = web_page(self, G, view_lines(crowded(), 0), 0, names=NAMES4)
        self.assertIn(LONG.upper(), html)
        html = web_page(self, G, view_lines(over(), 1), 1)
        self.assertIn("+35", html)
        self.assertNotIn("/move.htm", html)
        for bad in ("R7-3\n", "R7 3", "R14-1", "1.2-x", "<b>"):
            self.assertIsNone(G.MOVE_RE.match(bad), bad)
        for good in ("R7-3", "r7b7k7-n", "3.2-5", "3.R7-n", "3.J-r", "3-5", "3/4", "reset"):
            self.assertTrue(G.MOVE_RE.match(good), good)

    def test_full_screen(self):
        two = (LONG, LONG.upper())
        for st, names in ((midgame(), two), (crowded(), NAMES4), (over(), two), (bigrack(), two)):
            for me in (0, 1, -1):
                for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, me), me, keys=["RIGHT", "DOWN", "UP"],
                                                        names=names).items():
                    self.assertIn("Rummy Tile Classic", shown)
                    self.assertFalse([r for r in reqs if r.startswith(("MOV", "END", "UND"))], (mode, me))
        shown = ansi_screens(self, G, view_lines(crowded(), 0), 0, names=NAMES4)["pc"][0]
        self.assertIn("more below", shown)
        self.assertIn("R13", shown)
        self.assertIn(NAMES4[3], shown)
        shown, _ = ansi_screens(self, G, view_lines(crowded(), 0), 0, names=NAMES4, keys=["UP"] * 2)["ansi"]
        self.assertIn("more above", shown)
        shown, _ = ansi_screens(self, G, view_lines(bigrack(), 0), 0, keys=["DOWN"] * 5)["utf8"]
        self.assertIn("rows 2-5 of 5", shown)
        shown = ansi_screens(self, G, view_lines(midgame(), 1), 1)["ansi"][0]
        self.assertIn("Waiting for", shown)
        self.assertNotIn("R7 B7", shown)

    def keys(self, keys, st=None, me=0):
        st = st or midgame()
        return ansi_screens(self, G, view_lines(st, me), me, keys=keys, prepare=reads_typed(P, st, me))

    def test_keys_send_steps(self):
        st = midgame()
        rack = G.parse_view(view_lines(st, 0))["rack"]          # R1 R7 B7 B13 Y8 J: shown by color
        self.assertEqual(rack, ["R1", "R7", "B7", "B13", "Y8", "J"])
        # pick R7 and B7 on the rack, M: a new set
        for mode, (_, reqs) in self.keys(["RIGHT", "ENTER", "RIGHT", "ENTER", "m"]).items():
            self.assertIn("MOV 3 R7B7-n", reqs, mode)
        # pick Y8, go up to the table (the [new] slot, then set 3's tiles), Enter puts it there
        _, reqs = self.keys(["RIGHT"] * 4 + ["ENTER", "UP", "LEFT", "ENTER"])["pc"]
        self.assertTrue([r for r in reqs if r.startswith("MOV 3 Y8-")], reqs)
        # a table tile picked up and put on another set; split; back to the rack; undo, reset, end, check
        _, reqs = self.keys(["UP", "ENTER"])["utf8"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")], reqs)
        _, reqs = self.keys(["b"], st=st)["ansi"]
        for k, want in (("u", "UND 3"), ("z", "MOV 3 reset"), ("c", "CHK 3 !end")):
            _, reqs = self.keys([k])["ansi"]
            self.assertIn(want, reqs, k)
        shown, reqs = self.keys(["e", "y"])["ansi"]               # the table is not ready: asks first
        self.assertIn("END 3", reqs)
        self.assertIn("End your turn? The table doesn't count yet", shown)
        _, reqs = self.keys(["e", "n"])["ansi"]
        self.assertNotIn("END 3", reqs)
        _, reqs = self.keys(["e", "ENTER"])["ansi"]                # the dialog starts on No
        self.assertNotIn("END 3", reqs)
        # typed steps, several on a line
        _, reqs = self.keys(["/"] + list("r7-1 b7-n") + ["ENTER"])["ansi"]
        self.assertIn("MOV 3 R7-1 B7-N", reqs)
        _, reqs = self.keys(["/"] + list("undo") + ["ENTER"])["ansi"]
        self.assertIn("UND 3", reqs)
        _, reqs = self.keys(["/"] + list("end") + ["ENTER", "y"])["pc"]
        self.assertIn("END 3", reqs)
        # watchers and players waiting for their turn send nothing
        _, reqs = self.keys(["ENTER", "m", "u"], me=-1)["pc"]
        self.assertFalse([r for r in reqs if r.split()[0] in ("MOV", "UND", "END", "CHK")], reqs)

    def test_pass_and_draw(self):
        """draw always means a drawn game, never ending the turn; pass ends it only when nothing is played."""
        _, reqs = self.keys(["/"] + list("draw") + ["ENTER"])["ansi"]
        self.assertNotIn("END 3", reqs)
        _, reqs = self.keys(["/"] + list("pass") + ["ENTER"])["ansi"]       # midgame has a set played this turn
        self.assertNotIn("END 3", reqs)
        st = state([["R7", "B7"], ["B1", "B2"]], melded=[True, False])
        _, reqs = self.keys(["/"] + list("pass") + ["ENTER", "y"], st=st)["ansi"]
        self.assertIn("END 3", reqs)
        shown, reqs = self.keys(["p", "n"], st=st)["ansi"]                   # P asks first
        self.assertNotIn("END 3", reqs)
        self.assertIn("Pass your turn? You play nothing this turn.", shown)

    def test_click_and_split(self):
        """A click on a table tile picks it up; a click on a tile of another set moves it there; S splits a set."""
        # find where things are drawn: set 1's tile 2 and set 3's first tile
        shown = self.keys([])["pc"][0].split("\n")
        y = next(i for i, l in enumerate(shown) if " 1:" in l)
        x1 = shown[y].index("R4")
        x3 = next(i for i, l in enumerate(shown) if " 3:" in l)
        xx = shown[x3].index("Y9")
        _, reqs = ansi_screens(self, G, view_lines(midgame(), 0), 0,
                               keys=[("CLICK", x1, y), ("CLICK", xx, x3)])["pc"]
        self.assertIn("MOV 3 1.2-3", reqs)
        _, reqs = ansi_screens(self, G, view_lines(midgame(), 0), 0,
                               keys=[("CLICK", x1, y), "b"])["pc"]
        self.assertIn("MOV 3 1/2", reqs)
        rack = next(i for i, l in enumerate(shown) if " R1 " in l and i > y)      # Enter on the rack: back to it
        _, reqs = ansi_screens(self, G, view_lines(midgame(), 0), 0,
                               keys=[("CLICK", x1, y), ("CLICK", shown[rack].index("R1"), rack)])["pc"]
        self.assertIn("MOV 3 1.2-r", reqs)

    def test_plain_screen(self):
        for st, me in ((midgame(), 0), (midgame(), 1), (midgame(), -1), (crowded(), 0), (over(), 0),
                       (bigrack(), 0)):
            names = NAMES4 if len(st["racks"]) == 4 else (LONG, LONG.upper())
            text = plain_screen(self, G, view_lines(st, me), me, names=names)
            self.assertIn("Rummy Tile Classic", text)
            self.assertEqual("Your rack" in text, me >= 0)
        text = plain_screen(self, G, view_lines(midgame(), 0), 0)
        self.assertIn(" 4! K9", text)
        self.assertIn(" 1: R3 R4 R5 R6", text)
        self.assertIn("Your turn. Type a step such as /R1-n, or /end.", text)
        self.assertIn("Set 4 is not valid yet.", text)
        self.assertIn("Waiting for a second player", plain_screen(self, G, ["V rummytile/1 1"], 0))


class TelnetTileColors(unittest.TestCase):
    """The telnet tiles: bright white (107) with a dark gold yellow (38;5;136) in utf8, for xterm-class
    terminals; light grey (47) in pc and ansi, where a yellow tile is bright yellow on black. The cursor's
    cyan (46) and a picked tile's yellow (43) still stand out, and a yellow tile's number is black on them."""

    def test_the_attributes_in_each_mode(self):
        want = {"utf8": {"R7": "0;31;107", "B7": "0;34;107", "K7": "0;30;107", "J": "0;35;107",
                         "Y7": "0;38;5;136;107"},
                "pc": {"R7": "0;31;47", "B7": "0;34;47", "K7": "0;30;47", "J": "0;35;47", "Y7": "0;1;33;40"}}
        want["ansi"] = want["pc"]
        for mode, tiles in want.items():
            for t, attr in tiles.items():
                self.assertEqual(G.tile_attr(t, mode=mode), attr, (mode, t))
                self.assertEqual(G.tile_attr(t, "46", mode), "0;%s;46" % ("30" if t[0] == "Y" else attr.split(";")[1]))
                self.assertEqual(G.tile_attr(t, "43", mode), "0;%s;43" % ("30" if t[0] == "Y" else attr.split(";")[1]))

    def test_the_terminal_shows_them(self):
        """Every cell as a terminal shows it after the painter's changes, the tiles' 256 and bright codes
        included, matches what was composed, and the tiles on the screen have their mode's colors."""
        from game_checks import AttrScreen, FakeIO, ScreenCore, session
        lines = view_lines(midgame(), 0)
        for mode in ("pc", "utf8", "ansi"):
            io = FakeIO(mode)
            s = session(io, ScreenCore(G.KEY, lines), mode)
            g = G.AnsiScreen(s, "3", G.KEY, 0)
            g.names, g.turn_secs, g.v, g.log = ["ann", "bob"], 300, g.parse(lines), []
            g.snap()
            term = AttrScreen()
            for k in [None, "RIGHT", "DOWN", "DOWN", "LEFT", "UP"]:
                if k:
                    g.on_arrow(k)
                cv, pos = g.screen()
                term.feed(s.painter.frame(cv, pos))
                self.assertEqual(term.wrong_cells(cv), [], (mode, k))
            backs = {c[4] for row in term.cells for c in row if c[0] in "RBKY" and c[4] in ("47", "107", "40")}
            self.assertEqual(backs, {"107"} if mode == "utf8" else {"47", "40"}, mode)


if __name__ == "__main__":
    unittest.main()
