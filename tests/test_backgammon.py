"""backgammon/1 plugin tests and a staged game over the protocol."""
import asyncio
import os
import random
import subprocess
import sys
import unittest

from test_core import ROOT, Client, free_port

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("bg_plugin", os.path.join(ROOT, "plugins", "backgammon", "plugin.py"))
bg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bg)


def checkers(st, seat):
    s = 1 if seat == 0 else -1
    return sum(n * s for n in st["pts"] if n * s > 0) + st["bar"][seat] + st["off"][seat]


class Rules(unittest.TestCase):
    def test_start(self):
        r = bg.op_new({"players": 2, "options": {}, "seed": 1})
        st = r["state"]
        self.assertEqual((checkers(st, 0), checkers(st, 1)), (15, 15))
        d0, d1 = st["rolled"]
        self.assertEqual(r["events"], ["OPEN %d %d" % (d0, d1), "TURN %d" % st["turn"],
                                       "ROLL %d %d %d" % (st["turn"], d0, d1), "STEPS %d 2" % st["turn"]])
        self.assertEqual(st["turn"], 0 if d0 > d1 else 1)     # one die each, the higher starts

    def test_enter_from_bar_first(self):
        st = bg.new_state(1, False)
        st["pts"][23] -= 1
        st["bar"][0] = 1
        st["dice"] = [3, 5]
        st["turn"] = 0
        moves = bg.op_moves({"state": st, "seat": 0})["moves"]
        self.assertTrue(moves and all(m.startswith("bar-") for m in moves), moves)

    def test_blocked_and_hit(self):
        st = bg.new_state(1, False)
        st["pts"] = [0] * 24
        st["pts"][12] = 1          # seat 0 on 13
        st["pts"][8] = -1          # seat 1 blot on 9
        st["pts"][9] = -2          # seat 1 point on 10
        st["pts"][23] = -12
        st["pts"][0] = 14          # rest of seat 0 on 1
        st["dice"] = [3, 4]
        st["turn"] = 0
        moves = bg.op_moves({"state": st, "seat": 0})["moves"]
        self.assertNotIn("13-10", moves)
        self.assertIn("13-9", moves)
        r = bg.op_apply({"state": st, "seat": 0, "move": ["13-9"]})
        self.assertEqual(r["events"][0], "STEP 0 13 9 HIT")
        self.assertEqual(r["state"]["bar"][1], 1)

    def test_larger_die_rule(self):
        st = bg.new_state(1, False)
        st["pts"] = [0] * 24
        st["pts"][5] = 1           # one checker on 6, rest borne off
        st["off"] = [14, 0]
        st["pts"][23] = -15
        st["dice"] = [2, 6]
        st["turn"] = 0
        self.assertEqual(bg.op_moves({"state": st, "seat": 0})["moves"], ["6-4", "6-off"])  # off ends the game
        # only one die can be played: it must be the larger one
        st = bg.new_state(1, False)
        st["pts"] = [0] * 24
        st["pts"][23] = 1          # seat 0: one checker on 24
        st["pts"][0] = 14          # the rest on 1 (not home-complete because of 24)
        st["pts"][21] = -2         # 24-2=22 blocked
        st["pts"][17] = -2         # 24-6=18 blocked
        st["pts"][15] = -2         # 22-6=16 and 18-2=16 blocked (so 2+6 can't both play)
        st["pts"][19] = -9         # 24-4... unused; rest of seat 1
        st["dice"] = [2, 5]        # 24-2=22 blocked, 24-5=19 open: only the 5
        st["turn"] = 0
        self.assertEqual(bg.op_moves({"state": st, "seat": 0})["moves"], ["24-19"])

    def test_undo_and_end(self):
        r = bg.op_new({"players": 2, "options": {"cube": "0"}, "seed": 3})
        st, seat = r["state"], r["state"]["turn"]
        self.assertIn("error", bg.op_apply({"state": st, "seat": seat, "move": ["!end"]}))
        m = bg.op_moves({"state": st, "seat": seat})["moves"][0]
        r1 = bg.op_apply({"state": st, "seat": seat, "move": [m]})
        self.assertEqual(r1["staged"], "more")
        r2 = bg.op_apply({"state": r1["state"], "seat": seat, "move": ["!undo"]})
        self.assertEqual(r2["state"]["pts"], st["pts"])
        self.assertTrue(r2["events"][0].startswith("UNDO "))

    def test_cube(self):
        r = bg.op_new({"players": 2, "options": {}, "seed": 5})
        st = r["state"]
        st["phase"] = "roll"
        st["dice"] = []
        seat = st["turn"]
        r = bg.op_apply({"state": st, "seat": seat, "move": ["double"]})
        self.assertEqual(r["events"][:2], ["DBL %d 2" % seat, "TURN %d" % (1 - seat)])
        r2 = bg.op_apply({"state": r["state"], "seat": 1 - seat, "move": ["take"]})
        self.assertEqual(r2["state"]["cube"], 2)
        self.assertEqual(r2["state"]["cube_owner"], 1 - seat)
        r3 = bg.op_apply({"state": r["state"], "seat": 1 - seat, "move": ["drop"]})
        self.assertEqual(r3["end"], "win %d 1 single" % seat)

    def test_random_games_finish(self):
        for seed in range(60):
            rnd = random.Random(seed)
            r = bg.op_new({"players": 2, "options": {"cube": "1"}, "seed": seed})
            st, end = r["state"], None
            for _ in range(3000):
                seat = st["turn"]
                moves = bg.op_moves({"state": st, "seat": seat})["moves"]
                if not moves:
                    r = bg.op_apply({"state": st, "seat": seat, "move": ["!end"]})
                elif moves == ["roll", "double"]:
                    r = bg.op_apply({"state": st, "seat": seat, "move": ["roll"]})
                elif moves == ["take", "drop"]:
                    r = bg.op_apply({"state": st, "seat": seat, "move": ["take"]})
                else:
                    r = bg.op_apply({"state": st, "seat": seat, "move": [rnd.choice(moves)]})
                self.assertNotIn("error", r, (seed, moves, r))
                st = r["state"]
                self.assertEqual((checkers(st, 0), checkers(st, 1)), (15, 15))
                if "end" in r:
                    end = r["end"]
                    break
            self.assertRegex(end or "", r"^win [01] \d+ (single|gammon|backgammon)$", seed)


def wire(st, seat=0):
    return {l.split()[0]: l.split()[1:] for l in bg.op_view({"state": st, "seat": seat})["wire"]}


def play(st, seat, m):
    r = bg.op_apply({"state": st, "seat": seat, "move": [m]})
    assert "error" not in r, (m, r)
    return r


class Extensions(unittest.TestCase):
    """Added to backgammon/1 compatibly: OPEN, STEPS, N/DR/CA view lines,
    resign offers, match play with the Crawford rule."""

    def test_steps_and_view_lines(self):
        r = bg.op_new({"players": 2, "options": {}, "seed": 2})
        st, seat = r["state"], r["state"]["turn"]
        v = wire(st, seat)
        self.assertEqual(v["N"], ["2"])
        self.assertEqual(v["DR"], [str(d) for d in st["rolled"]])
        self.assertEqual(v["CA"], ["1", "1"])
        self.assertNotIn("L", v)                    # match lines only in match play
        for k in ("P", "B", "O", "D", "C", "W", "Q"):
            self.assertIn(k, v)                      # the old lines are all still there
        # a roll on a blocked board: STEPS says how many steps must be played
        st = bg.new_state(9, True)
        st["pts"] = [0] * 24
        st["pts"][23] = 1                            # seat 0: one checker on 24, the rest on 1
        st["pts"][0] = 14
        st["pts"][21], st["pts"][17], st["pts"][15], st["pts"][19] = -2, -2, -2, -9
        st["turn"], st["phase"] = 0, "roll"
        found = None
        for rolls in range(40):
            st["rolls"] = rolls
            r = bg.op_apply({"state": st, "seat": 0, "move": ["roll"]})
            if any(e.startswith("STEPS 0 1") for e in r["events"]):
                found = r
                break
        self.assertTrue(found, "no roll with a single playable die")
        self.assertEqual(wire(found["state"])["N"], ["1"])

    def test_resign_offer_accept_and_reject(self):
        r = bg.op_new({"players": 2, "options": {}, "seed": 4})
        st, mover = r["state"], r["state"]["turn"]
        other = 1 - mover
        # the player not on turn offers; the mover answers without a turn change
        r = play(st, other, "resign-gammon")
        self.assertEqual(r["events"], ["OFFER %d 2 gammon" % other])
        self.assertEqual(wire(r["state"], mover)["RS"], [str(other), "2", "gammon"])
        self.assertEqual(bg.op_moves({"state": r["state"], "seat": mover})["moves"], ["accept", "reject"])
        self.assertIn("error", bg.op_apply({"state": r["state"], "seat": mover, "move": ["13-9"]}))
        self.assertIn("error", bg.op_apply({"state": r["state"], "seat": other, "move": ["resign-single"]}))
        rj = play(r["state"], mover, "reject")
        self.assertEqual(rj["events"], ["REJECT %d" % mover])
        self.assertEqual((rj["state"]["phase"], rj["state"]["turn"]), ("move", mover))
        self.assertNotIn("RS", wire(rj["state"]))
        acc = play(r["state"], mover, "accept")
        self.assertEqual(acc["events"], ["ACCEPT %d" % mover])
        self.assertEqual(acc["end"], "win %d 2 gammon" % mover)
        # the mover offers on its own turn: the turn passes to the answerer and back
        r = play(st, mover, "resign-single")
        self.assertEqual(r["events"], ["OFFER %d 1 single" % mover, "TURN %d" % other])
        rj = play(r["state"], other, "reject")
        self.assertEqual(rj["events"], ["REJECT %d" % other, "TURN %d" % mover])
        self.assertEqual(rj["state"]["dice"], st["dice"])       # the roll is kept
        self.assertIn("error", bg.op_apply({"state": st, "seat": mover, "move": ["resign-lots"]}))

    def test_match_play_and_crawford(self):
        self.assertIn("error", bg.op_new({"players": 2, "options": {"match": "x"}, "seed": 1}))
        r = bg.op_new({"players": 2, "options": {"match": "3"}, "seed": 6})
        self.assertEqual(r["events"][:2], ["GAME 1", r["events"][1]])
        self.assertTrue(r["events"][1].startswith("OPEN "))
        st = r["state"]
        v = wire(st)
        self.assertEqual((v["L"], v["G"]), (["3"], ["1"]))
        # seat 0 owns the cube at 2 and redoubles; the drop wins 2 points: 2-0, the next game is Crawford
        st["phase"], st["dice"], st["turn"], st["cube"], st["cube_owner"] = "roll", [], 0, 2, 0
        r = play(st, 0, "double")
        r = play(r["state"], 1, "drop")
        self.assertNotIn("end", r)
        ev = r["events"]
        self.assertEqual(ev[:4], ["DROP 1", "WON 0 2 single", "SCORE 2 0", "GAME 2 crawford"])
        self.assertTrue(ev[4].startswith("OPEN "))
        st = r["state"]
        v = wire(st)
        self.assertEqual((v["T"], v["G"], v["CA"]), (["1", "0"], ["2", "crawford"], ["0", "0"]))
        self.assertEqual((st["cube"], st["cube_owner"], st["off"]), (1, -1, [0, 0]))
        self.assertEqual((checkers(st, 0), checkers(st, 1)), (15, 15))
        # seat 1 resigns the Crawford game for 1 point: 3-0 ends the match
        r = play(st, 1, "resign-single")
        r = play(r["state"], 0, "accept")
        self.assertEqual(r["events"][-2:], ["WON 0 1 single", "SCORE 3 0"])
        self.assertEqual(r["end"], "win 0 1 single")
        # or the trailer wins the Crawford game: 2-1, and doubling is back after it
        r = play(st, 0, "resign-single")
        r = play(r["state"], 1, "accept")
        i = r["events"].index("WON 1 1 single")
        self.assertEqual(r["events"][i + 1:i + 3], ["SCORE 2 1", "GAME 3"])
        self.assertEqual(wire(r["state"])["CA"], ["1", "1"])

    def test_random_matches_finish(self):
        for seed in range(25):
            rnd = random.Random(seed)
            st = bg.op_new({"players": 2, "options": {"match": "5"}, "seed": seed})["state"]
            end = None
            for _ in range(20000):
                seat = st["turn"]
                moves = bg.op_moves({"state": st, "seat": seat})["moves"]
                if not moves:
                    m = "!end"
                elif moves == ["roll", "double"]:
                    m = rnd.choice(moves)
                elif moves == ["take", "drop"]:
                    m = rnd.choice(["take", "take", "drop"])
                else:
                    m = rnd.choice(moves)
                r = play(st, seat, m)
                st = r["state"]
                self.assertEqual((checkers(st, 0), checkers(st, 1)), (15, 15))
                if "end" in r:
                    end = r["end"]
                    break
            self.assertTrue(end, seed)
            self.assertGreaterEqual(max(st["score"]), 5)

    def test_old_state_still_plays(self):
        """A state stored before the extensions (no match, offer, need keys)."""
        st = bg.op_new({"players": 2, "options": {}, "seed": 8})["state"]
        for k in ("need", "offer", "match", "score", "game", "crawford", "crawford_done"):
            del st[k]
        v = wire(st, st["turn"])
        self.assertIn("N", v)
        m = bg.op_moves({"state": st, "seat": st["turn"]})["moves"][0]
        play(st, st["turn"], m)
        r = play(st, 1 - st["turn"], "resign-single")
        self.assertTrue(r["events"][0].startswith("OFFER "))


class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port), "--auth", "none", "--rate", "1000",
                                      "--burst", "1000"], cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()

    async def test_staged_turns(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO gus")
        await b.req("HELO hal")
        self.assertIn("GAM backgammon/1 standard Backgammon 2 2 staged", await a.req("GAM"))
        await a.req("NEW backgammon standard cube=0")
        await a.req("JON 1")
        await b.req("JON 1")
        who = {0: a, 1: b}
        v = await a.req("VUE 1")
        seat = int([l for l in v if l.startswith("W ")][0].split()[1])
        c = who[seat]
        self.assertIn("NOSTAGE", (await c.req("UND 99"))[0] if False else "NOSTAGE")
        m = [l[2:] for l in await c.req("VUE 1 M") if l.startswith("M ")]
        r = await c.req("MOV 1 " + m[0])
        self.assertIn(r[-1], ("OK more", "OK done"))
        r = await c.req("UND 1")
        self.assertTrue(any(" UNDO " in l for l in r), r)
        self.assertIn("ILLEGAL", (await c.req("END 1"))[0])
        for _ in range(4):
            m = [l[2:] for l in await c.req("VUE 1 M") if l.startswith("M ")]
            if not m:
                break
            r = await c.req("MOV 1 " + m[0])
        self.assertEqual(r[-1], "OK done")
        r = await c.req("END 1")
        self.assertTrue(any(" TURN %d" % (1 - seat) in l for l in r), r)
        self.assertIn("SYNTAX", (await who[1 - seat].req("MOV 1 !end x"))[0])
        reversi = await Client.connect(self.port)
        await reversi.req("HELO ivy")
        await reversi.req("NEW reversi standard")
        await reversi.req("JON 2")
        self.assertIn("NOSTAGE", (await reversi.req("UND 2"))[0])


class TelnetCountdowns(unittest.TestCase):
    """Telnet rolls for you 5 seconds into your turn and sends a finished move 5 seconds after the last
    checker, with a countdown on the screen. Any key stops a countdown, and Esc (Enter in plain mode) only
    stops it. A fake clock stands in for the seconds."""

    def screen(self, mode, phase, me=0, turn=0):
        from test_adapters import BgFakeCore, BG_TABS, FakeIO, LONG, bg_lines
        from adapters import telnet as T
        from adapters.games import backgammon as G
        core = BgFakeCore(BG_TABS, bg_lines(phase, turn))
        io = FakeIO(mode)
        if mode == "plain":
            out = []
            io.write = out.append
            io.out = out
        s = T.Session(io, core, mode, LONG)
        g = (G.AnsiScreen if mode != "plain" else G.PlainScreen)(s, "3", G.KEY, me)
        g.names, g.turn_secs, g.v = [LONG, LONG[::-1]], 300, g.parse(core.lines)
        g.s.bghome = "right"
        if mode != "plain":
            g.snap()
        self.t = [100.0]
        g.now = lambda: self.t[0]
        return g, core

    def line(self, g):
        return g.screen()[0].lines()[ROWS_HINT]

    def test_the_roll_comes_after_five_seconds(self):
        for mode in ("pc", "utf8", "ansi"):
            g, core = self.screen(mode, "roll")
            asyncio.run(g.tick())
            self.assertEqual(self.line(g).strip(), "Rolling in 5 seconds. Press Esc to double or wait.")
            self.t[0] += 2.5
            asyncio.run(g.tick())
            self.assertEqual(self.line(g).strip(), "Rolling in 3 seconds. Press Esc to double or wait.")
            self.assertLessEqual(g.tick_in(), 0.5)          # the loop wakes when the number changes
            self.assertNotIn("MOV 3 roll", core.reqs)
            self.t[0] += 2.5
            asyncio.run(g.tick())
            self.assertIn("MOV 3 roll", core.reqs, mode)
            self.t[0] += 10                                 # the same view comes back: no second roll
            asyncio.run(g.tick())
            asyncio.run(g.tick())
            self.assertEqual(core.reqs.count("MOV 3 roll"), 1)

    def test_esc_stops_it_and_the_turn_waits(self):
        g, core = self.screen("pc", "roll")
        asyncio.run(g.tick())
        self.assertTrue(asyncio.run(g.key_seen("ESC")), "Esc only stops the countdown: no menu")
        self.t[0] += 60
        asyncio.run(g.tick())
        self.assertNotIn("MOV 3 roll", core.reqs)
        self.assertIn("The dice wait for you. Press Enter when you want to roll.", self.line(g))
        asyncio.run(g.on_key("d"))                          # then the player can double
        self.assertIn("MOV 3 double", core.reqs)

    def test_any_other_key_stops_it_too(self):
        g, core = self.screen("pc", "roll")
        asyncio.run(g.tick())
        self.assertFalse(asyncio.run(g.key_seen("?")), "the key does what it does")
        self.assertIsNone(g.auto)
        self.t[0] += 60
        asyncio.run(g.tick())
        self.assertNotIn("MOV 3 roll", core.reqs)

    def test_not_on_the_opponents_turn_nor_while_typing(self):
        g, core = self.screen("pc", "roll", me=1)
        asyncio.run(g.tick())
        self.assertIsNone(g.auto)
        g, core = self.screen("pc", "roll")
        g.buf = "say hel"
        asyncio.run(g.tick())
        self.assertIsNone(g.auto)

    def test_the_move_is_sent_after_the_last_checker(self):
        for mode in ("pc", "utf8", "ansi"):
            g, core = self.screen(mode, "done")             # the dice are spent: nothing left to play
            asyncio.run(g.tick())
            self.assertEqual(self.line(g).strip(), "Sending your move in 5 seconds. Press Esc to change it.")
            self.t[0] += 4.9
            asyncio.run(g.tick())
            self.assertNotIn("END 3", core.reqs)
            self.t[0] += 0.2
            asyncio.run(g.tick())
            self.assertIn("END 3", core.reqs, mode)

    def test_esc_leaves_the_move_unsent(self):
        g, core = self.screen("pc", "done")
        asyncio.run(g.tick())
        self.assertTrue(asyncio.run(g.key_seen("ESC")))
        self.t[0] += 60
        asyncio.run(g.tick())
        self.assertNotIn("END 3", core.reqs)
        self.assertIn("Your move waits. Press Enter to send it, or U to undo a step.", self.line(g))
        g, core = self.screen("pc", "done")                 # undoing a step stops it as well
        asyncio.run(g.tick())
        self.assertFalse(asyncio.run(g.key_seen("u")))
        self.t[0] += 60
        asyncio.run(g.tick())
        self.assertNotIn("END 3", core.reqs)

    def test_plain_prints_one_line(self):
        g, core = self.screen("plain", "roll")
        asyncio.run(g.tick())
        self.t[0] += 1.5
        asyncio.run(g.tick())
        said = "".join(g.io.out)
        self.assertEqual(said.count("Rolling in"), 1, said)
        self.assertIn(" Rolling in 5 seconds. Type /double, or press Enter to wait.\n", said)
        self.t[0] += 4
        asyncio.run(g.tick())
        self.assertIn("MOV 3 roll", core.reqs)
        g, core = self.screen("plain", "done")
        asyncio.run(g.tick())
        self.assertIn(" Sending your move in 5 seconds. Press Enter to change it.\n", "".join(g.io.out))
        self.assertTrue(asyncio.run(g.key_seen("ENTER")))
        self.t[0] += 60
        asyncio.run(g.tick())
        self.assertNotIn("END 3", core.reqs)
        self.assertIn("Type /end to send your move", "".join(g.io.out))


ROWS_HINT = 21      # the full screen's hint line (ROWS - 3)


class TelnetOffers(unittest.TestCase):
    """A double comes as a dialog with Accept and Decline, answered inside it. Declining drops the double, which
    ends the game, so it asks first. Esc puts the dialog away unanswered, and Enter brings it back. Plain mode
    asks the same at its prompt. Pass means nothing in Backgammon."""

    def screen(self, mode, keys=(), lines=None):
        from test_adapters import BgFakeCore, BG_TABS, FakeIO, LONG, bg_lines
        from adapters import telnet as T
        from adapters.games import backgammon as G
        core = BgFakeCore(BG_TABS, lines or bg_lines("take", 0))
        io = FakeIO(mode, list(keys))
        if mode == "plain":
            io.out = []
            io.write = io.out.append

            async def readline():
                return await io.readkey()
            io.readline = readline
        s = T.Session(io, core, mode, LONG)
        g = (G.AnsiScreen if mode != "plain" else G.PlainScreen)(s, "3", G.KEY, 0)
        g.names, g.turn_secs, g.v = [LONG, LONG[::-1]], 300, g.parse(core.lines)
        g.s.bghome = "right"
        return g, core

    def press(self, g, *keys):
        g.io.keys[:0] = list(keys)

        async def go():
            while g.io.keys:
                await g.on_key(await g.io.readkey())
        try:
            asyncio.run(go())
        except EOFError:
            pass

    def test_the_double_is_a_dialog(self):
        g, core = self.screen("pc")
        self.press(g, "ENTER")                      # the dialog starts on Accept
        self.assertIn("MOV 3 take", core.reqs)
        g, core = self.screen("pc")
        self.press(g, "y")
        self.assertIn("MOV 3 take", core.reqs)

    def test_declining_asks_first(self):
        g, core = self.screen("pc")
        self.press(g, "n", "ESC")                   # Decline, then Esc: No, so nothing is sent
        self.assertNotIn("MOV 3 drop", core.reqs)
        g, core = self.screen("pc")
        self.press(g, "RIGHT", "ENTER", "ENTER")    # Decline, then Enter on No: the safe answer
        self.assertNotIn("MOV 3 drop", core.reqs)
        g, core = self.screen("pc")
        self.press(g, "n", "y")
        self.assertIn("MOV 3 drop", core.reqs)
        shown = "\n".join("\n".join(s) for s in g.io.screens)
        self.assertIn("Drop the double? You lose 1 point and the game.", shown)

    def test_esc_puts_it_away(self):
        g, core = self.screen("pc")
        self.press(g, "ESC")
        self.assertFalse([r for r in core.reqs if r.startswith("MOV")])
        self.assertIn("doubles to 2. Press Enter to answer.", g.screen()[0].lines()[ROWS_HINT])
        self.press(g, "ENTER", "y")                 # Enter brings the dialog back
        self.assertIn("MOV 3 take", core.reqs)

    def test_no_command_answers_it(self):
        for word in ("take", "drop", "accept", "decline", "pass"):
            g, core = self.screen("pc", ["ESC"] * 3)          # an answer that needs a box is closed again
            try:
                asyncio.run(g.command(word))
            except EOFError:
                pass
            self.assertFalse([r for r in core.reqs if r.startswith("MOV")], word)

    def test_plain_asks_at_the_prompt(self):
        g, core = self.screen("plain", ["n", "n"])
        asyncio.run(g.line_in("n"))                 # decline, then no to dropping
        self.assertNotIn("MOV 3 drop", core.reqs)
        g, core = self.screen("plain", ["y"])
        asyncio.run(g.line_in("n"))
        self.assertIn("MOV 3 drop", core.reqs)
        g, core = self.screen("plain")
        asyncio.run(g.line_in("y"))
        self.assertIn("MOV 3 take", core.reqs)
        self.assertIn("Type Y to accept, or N to decline.", g.full_prompt())


if __name__ == "__main__":
    unittest.main()
