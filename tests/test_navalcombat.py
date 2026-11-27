"""navalcombat/1: the plugin's rules, hidden fleets in every view and event, a whole game over the protocol,
and the web and telnet frontends (adapters/games/navalcombat.py)."""
import importlib.util
import json
import os
import re
import unittest

from game_checks import LONG, ansi_screens, board_image, plain_screen, web_page
from test_adapters import Native, Stack
from test_adapters import query_of  # noqa: E402
import procs
from test_core import ROOT, Client, free_port

from adapters.games import navalcombat as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("nc_plugin", os.path.join(ROOT, "plugins", "navalcombat", "plugin.py"))
nc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nc)

CLASSIC_FLEET = ["a1-e1", "a2-d2", "a3-c3", "e3-g3", "j9-j10"]       # touching: fine in classic
BATNAV_FLEET = ["a1-d1", "a3-c3", "e3-g3", "a5-b5", "d5-e5", "g5-h5", "a7-a7", "c7-c7", "e7-e7", "g7-g7"]


def new(rules="classic", seed=7):
    return nc.op_new({"players": 2, "options": {}, "seed": seed, "rules": rules})["state"]


def play(st, seat, move, ok=True):
    r = nc.op_apply({"state": st, "seat": seat, "move": [move]})
    if ok:
        assert "error" not in r, (move, r)
    return r


def place_all(st, seat, tokens, ready=True):
    for t in tokens:
        st = play(st, seat, t)["state"]
    if ready:
        st = play(st, seat, "ready")["state"]
    return st


def started(rules="classic", seed=7):
    """Both fleets placed: seat 0 by hand, seat 1 at random."""
    st = new(rules, seed)
    st = place_all(st, 0, CLASSIC_FLEET if rules == "classic" else BATNAV_FLEET)
    st = play(st, 1, "random")["state"]
    return play(st, 1, "ready")["state"]


def fleet_squares(st, seat):
    return {nc.sq_name(x, y) for s in st["ships"][seat] for x, y in nc.ship_cells(s)}


def vue(st, seat, moves=True, seq=40, clock=True):
    """The lines VUE GMT returns (before OK), as the core builds them."""
    r = nc.op_view({"state": st, "seat": seat})
    out = ["V navalcombat/1 %d" % seq] + r["geometry"] + r["wire"]
    if clock and st["phase"] != "over":
        out.append("CLK %d 250 300" % (st["turn"] if st["phase"] == "shoot" else nc.clock_seat(st)))
    out.append("S " + r["status"])
    if moves and seat >= 0:
        out += ["M " + m for m in nc.op_moves({"state": st, "seat": seat})["moves"]]
    return out


def board(lines, seat):
    rows = {int(l.split()[2]): l.split()[3] for l in lines if l.startswith("B %d " % seat)}
    return [rows.get(y, "." * 10) for y in range(1, 11)]


def assert_hidden(tc, st, viewer):
    """Nothing in viewer's view, moves or data shows a square of the other seat's fleet that is not hit
    (both fleets for a watcher), except sunk ships, all of whose squares are hit."""
    lines = vue(st, viewer)
    r = nc.op_view({"state": st, "seat": viewer})
    for seat in (0, 1):
        if seat == viewer:
            continue
        shots = st["shots"][seat]
        rows = board(lines, seat)
        for y in range(10):
            for x in range(10):
                c = rows[y][x]
                tc.assertNotEqual(c, "s", (viewer, seat, x, y))
                if c != ".":
                    tc.assertIn(shots[y * 10 + x], "ox", (viewer, seat, x, y))
        tc.assertEqual(r["data"]["boards"][seat], rows)
        for l in lines:
            if l.startswith("K %d " % seat):
                for x, y in G.squares(l.split()[3]):
                    tc.assertEqual(shots[y * 10 + x], "x")
    ys = [l for l in lines if l.startswith("Y ")]
    if viewer < 0:
        tc.assertEqual(ys, [])
        tc.assertNotIn("mine", r["data"])
    else:
        tc.assertEqual(ys, ["Y %d %s" % (s[3], nc.ship_token(s)) for s in st["ships"][viewer]])
    tc.assertNotIn("ships", json.dumps(r["data"]))
    tc.assertNotIn("seed", json.dumps(r))


# ---- the rules ---------------------------------------------------------------------

class Rules(unittest.TestCase):
    def test_new(self):
        r = nc.op_new({"players": 2, "options": {}, "seed": 1, "rules": "batnav"})
        self.assertEqual(r["events"], ["TURN 0"])
        self.assertEqual(r["state"]["fleet"], [4, 3, 3, 2, 2, 2, 1, 1, 1, 1])
        self.assertEqual(nc.op_new({"players": 2, "options": {}, "seed": 1})["state"]["fleet"], [5, 4, 3, 3, 2])
        self.assertIn("error", nc.op_new({"players": 2, "options": {}, "rules": "huge"}))
        self.assertIn("error", nc.op_new({"players": 2, "options": {"size": "8"}}))
        self.assertIn("error", nc.op_new({"players": 3, "options": {}}))
        self.assertEqual(nc.INFO["rules"], ["classic", "batnav", "salvo"])
        v = vue(new(), 0)
        self.assertIn("FL 5 4 3 3 2", v)
        self.assertIn("Q place", v)
        self.assertIn("N 5", v)
        self.assertEqual(v[v.index("Q place") + 1], "W 0")
        self.assertEqual(vue(new(), 1)[vue(new(), 1).index("Q place") + 1], "W 1")   # both place at once
        self.assertEqual(vue(new(), -1)[vue(new(), -1).index("Q place") + 1], "W 0")  # the clock's seat

    def test_place_classic(self):
        st = new()
        moves = nc.op_moves({"state": st, "seat": 0})["moves"]
        self.assertIn("a1-e1", moves)
        self.assertIn("a1-a5", moves)
        self.assertNotIn("g1-k1", moves)
        self.assertEqual(moves[-1], "random")
        self.assertEqual(len(moves), 2 * 10 * 6 + 1)
        for bad, why in (("a1-d1", "5 squares"), ("a1-b2", "end to the other"), ("g1-k1", "end to the other"),
                         ("a1", "end to the other"), ("ready", "place every ship"), ("undo", "no ship"),
                         ("e5", "end to the other")):
            r = play(st, 0, bad, ok=False)
            self.assertIn(why, r["error"], bad)
        st = play(st, 0, "e1-a1")["state"]                        # either end first
        self.assertEqual(st["ships"][0], [[0, 0, 1, 5]])
        self.assertIn("overlaps", play(st, 0, "c1-c4", ok=False)["error"])
        st = play(st, 0, "a2-d2")["state"]                        # touching is fine in classic
        st = place_all(st, 0, CLASSIC_FLEET[2:], ready=False)
        moves = nc.op_moves({"state": st, "seat": 0})["moves"]
        self.assertEqual(moves, ["random", "undo", "clear", "ready"])
        self.assertIn("every ship is placed", play(st, 0, "a9-b9", ok=False)["error"])
        self.assertEqual(play(st, 0, "undo")["state"]["ships"][0], st["ships"][0][:4])
        self.assertEqual(play(st, 0, "clear")["state"]["ships"][0], [])
        r = play(st, 0, "ready")
        self.assertEqual(r["events"], ["READY 0", "TURN 1"])
        st = r["state"]
        self.assertIn("ready", play(st, 0, "undo", ok=False)["error"])
        self.assertEqual(nc.op_moves({"state": st, "seat": 0})["moves"], [])
        self.assertIn("W 1", vue(st, 0))                         # seat 1 is still placing
        self.assertIn("waiting for the other fleet", play(st, 0, "e5", ok=False)["error"])
        st = place_all(st, 1, CLASSIC_FLEET, ready=False)
        r = play(st, 1, "ready")
        self.assertEqual(r["events"], ["READY 1", "TURN 0"])
        self.assertEqual(r["state"]["phase"], "shoot")

    def test_place_batnav(self):
        st = new("batnav")
        self.assertIn("4 squares", play(st, 0, "a1-e1", ok=False)["error"])
        st = play(st, 0, "a1-d1")["state"]
        for touching in ("a2-c2", "e1-e3", "e2-g2", "b2-b4"):       # beside, at the end, at a corner
            self.assertIn("touch", play(st, 0, touching, ok=False)["error"], touching)
        st = play(st, 0, "a3-c3")["state"]
        st = place_all(st, 0, BATNAV_FLEET[2:6], ready=False)
        self.assertIn("N 1", vue(st, 0))
        self.assertIn("i10-i10", nc.op_moves({"state": st, "seat": 0})["moves"])
        self.assertIn("1 square long", play(st, 0, "i9-i10", ok=False)["error"])
        st = place_all(st, 0, BATNAV_FLEET[6:])
        self.assertTrue(st["ready"][0])

    def test_random_fleets_are_legal(self):
        for rules in ("classic", "batnav"):
            for seed in range(40):
                st = new(rules, seed)
                st = play(st, 0, "random")["state"]
                ships = st["ships"][0]
                self.assertEqual([s[3] for s in ships], st["fleet"])
                for i, s in enumerate(ships):
                    self.assertTrue(nc.fits(ships[:i] + ships[i + 1:], s, 10, rules == "batnav"), (rules, seed))
        # the rest of the fleet goes around the ships already placed; the same seed gives the same fleet
        st = place_all(new(), 0, CLASSIC_FLEET[:2], ready=False)
        r1, r2 = play(st, 0, "random")["state"], play(st, 0, "random")["state"]
        self.assertEqual(r1["ships"][0][:2], st["ships"][0])
        self.assertEqual(r1["ships"][0], r2["ships"][0])
        # with the fleet complete, random places it all again (another fleet each time)
        r3 = play(r1, 0, "random")["state"]
        self.assertEqual(len(r3["ships"][0]), 5)
        self.assertNotEqual(r3["ships"][0], r1["ships"][0])
        self.assertEqual(play(r1, 0, "random")["events"], [])

    def test_shots(self):
        st = started()
        b1 = fleet_squares(st, 1)
        water = sorted(set(nc.sq_name(x, y) for x in range(10) for y in range(10)) - b1 - fleet_squares(st, 0))
        self.assertEqual(len(nc.op_moves({"state": st, "seat": 0})["moves"]), 100)
        self.assertEqual(nc.op_moves({"state": st, "seat": 1})["moves"], [])
        self.assertEqual(nc.op_apply({"state": st, "seat": 0, "move": [sorted(b1)[0]], "check": True}),
                         {"info": ""})                         # CHK never says hit or miss
        r = play(st, 0, water[0])
        self.assertEqual(r["events"], ["SHOT 0 %s miss" % water[0], "TURN 1"])
        st = r["state"]
        self.assertIn("not your turn", play(st, 0, water[1], ok=False)["error"])
        r = play(st, 1, "a1")                                  # seat 0's 5-square ship
        self.assertEqual(r["events"], ["SHOT 1 a1 hit", "TURN 0"])
        st = r["state"]
        self.assertIn("already fired at %s" % water[0], play(st, 0, water[0], ok=False)["error"])
        self.assertIn("one square", play(st, 0, "a1-a2", ok=False)["error"])
        self.assertIn("LS 1 a1", vue(st, -1))
        # sinking the 2-square ship at j9-j10
        st = play(st, 0, water[1])["state"]
        st = play(st, 1, "j9")["state"]
        st = play(st, 0, water[2])["state"]
        r = play(st, 1, "j10")
        self.assertEqual(r["events"], ["SHOT 1 j10 sunk 2 j9-j10", "TURN 0"])
        v = vue(r["state"], 1)
        self.assertIn("K 0 2 j9-j10", v)
        self.assertEqual(board(v, 0)[8][9] + board(v, 0)[9][9], "##")
        self.assertIn("L 4 5", v)

    def test_game_end(self):
        st = started()
        targets = sorted(fleet_squares(st, 1))
        misses = [nc.sq_name(x, y) for y in range(10) for x in range(10)
                  if nc.sq_name(x, y) not in fleet_squares(st, 0)]
        for i, t in enumerate(targets):
            r = play(st, 0, t)
            st = r["state"]
            if i < len(targets) - 1:
                self.assertNotIn("end", r)
                st = play(st, 1, misses[i])["state"]
        self.assertEqual(r["end"], "win 0 17-0")
        self.assertTrue(r["events"][0].startswith("SHOT 0 %s sunk " % targets[-1]))
        self.assertEqual(len(r["events"]), 1)
        self.assertEqual(st["phase"], "over")
        self.assertIn("error", play(st, 1, misses[-1], ok=False))
        self.assertEqual(nc.op_moves({"state": st, "seat": 0})["moves"], [])
        self.assertEqual(nc.op_result({"state": st}), {"over": True, "scores": [1, 0]})
        v = vue(st, 1)
        self.assertIn("W -", v)
        self.assertIn("S Game over: your fleet is sunk", v)
        self.assertIn("S Game over: you sank the whole fleet", vue(st, 0))
        self.assertIn("S Game over: seat 0 sank the whole fleet", vue(st, -1))
        # the winner's fleet stays hidden from the loser and from watchers, even now
        assert_hidden(self, st, 1)
        assert_hidden(self, st, -1)
        self.assertEqual(len([l for l in vue(st, 1) if l.startswith("K 1 ")]), 5)
        self.assertEqual([l for l in vue(st, -1) if l.startswith("K 0 ")], [])


    def test_salvo(self):
        """The salvo ruleset (GameMaster's BattleShips): the classic fleet, six shots a turn with TURN for
        the same seat after each of the first five, SL for the shots left, and each seated player's view
        carries the other fleet (OF) once both are ready, never a watcher's view or the data."""
        st = new("salvo")
        self.assertEqual((st["fleet"], st["fired"]), ([5, 4, 3, 3, 2], 0))
        self.assertEqual(new()["fleet"], st["fleet"])
        self.assertNotIn("fired", new())
        st = place_all(st, 0, CLASSIC_FLEET)
        self.assertEqual([l for l in vue(st, 1) if l.startswith("OF ")], [])     # not before both are ready
        st = play(play(st, 1, "random")["state"], 1, "ready")["state"]
        self.assertEqual([l for l in vue(st, 1) if l.startswith("OF ")],
                         ["OF 5 a1-e1", "OF 4 a2-d2", "OF 3 a3-c3", "OF 3 e3-g3", "OF 2 j9-j10"])
        self.assertEqual(len([l for l in vue(st, 0) if l.startswith("OF ")]), 5)
        self.assertEqual([l for l in vue(st, -1) if l.startswith("OF ")], [])
        self.assertNotIn("ships", json.dumps(nc.op_view({"state": st, "seat": 1})["data"]))
        self.assertIn("SL 6", vue(st, 0))
        self.assertIn("S Your salvo: 6 shots left of 6", vue(st, 0))
        self.assertIn("S Waiting for the other player's salvo", vue(st, 1))
        water = sorted(set(nc.sq_name(x, y) for x in range(10) for y in range(10)) - fleet_squares(st, 1))
        for i in range(5):
            r = play(st, 0, water[i])
            self.assertEqual(r["events"], ["SHOT 0 %s miss" % water[i], "TURN 0"])
            st = r["state"]
            self.assertIn("SL %d" % (5 - i), vue(st, 0))
        self.assertIn("S Your salvo: 1 shot left of 6", vue(st, 0))
        self.assertIn("not your turn", play(st, 1, "a1", ok=False)["error"])
        r = play(st, 0, water[5])
        self.assertEqual(r["events"], ["SHOT 0 %s miss" % water[5], "TURN 1"])
        st = r["state"]
        self.assertEqual((st["turn"], st["fired"]), (1, 0))
        self.assertIn("SL 6", vue(st, 1))
        # a whole game, at random: six shots a turn until a fleet is sunk
        import random
        for seed in range(4):
            rnd = random.Random(seed)
            st = new("salvo", seed)
            for seat in (0, 1):
                st = play(play(st, seat, "random")["state"], seat, "ready")["state"]
            runs, last, run = [], None, 0
            while st["phase"] == "shoot":
                seat = st["turn"]
                run = run + 1 if seat == last else 1
                last = seat
                r = play(st, seat, rnd.choice(nc.op_moves({"state": st, "seat": seat})["moves"]))
                st = r["state"]
                if st["phase"] == "shoot" and st["turn"] != seat:
                    runs.append(run)
            self.assertEqual(set(runs), {6})
            self.assertTrue(r["end"].startswith("win %d " % st["winner"]))


class Hidden(unittest.TestCase):
    """The other fleet is never in a view, a move list, the view's data or an event."""

    def test_views_while_placing(self):
        st = place_all(new(), 0, CLASSIC_FLEET[:3], ready=False)
        for viewer in (1, -1):
            v = vue(st, viewer)
            self.assertEqual([l for l in v if l.startswith("B 0 ")], [])
            self.assertIn("L 5 5", v)                         # not how many seat 0 has placed
            assert_hidden(self, st, viewer)
        self.assertEqual(len([l for l in vue(st, 0) if l.startswith("B 0 ")]), 3)
        self.assertIn("B 0 1 sssss.....", vue(st, 0))

    def test_whole_games_never_leak(self):
        for rules in ("classic", "batnav"):
            for seed in range(6):
                import random
                rnd = random.Random(seed)
                st = new(rules, seed)
                events = []
                for seat in (0, 1):
                    for m in ("random", "ready"):
                        r = play(st, seat, m)
                        st = r["state"]
                        events += r["events"]
                hidden = {0: fleet_squares(st, 0), 1: fleet_squares(st, 1)}
                while st["phase"] == "shoot":
                    seat = st["turn"]
                    r = play(st, seat, rnd.choice(nc.op_moves({"state": st, "seat": seat})["moves"]))
                    st = r["state"]
                    events += r["events"]
                    for viewer in (0, 1, -1):
                        assert_hidden(self, st, viewer)
                self.assertTrue(r["end"].startswith("win %d " % st["winner"]))
                # every event: no placement squares, and a square named in a SHOT on seat's waters was shot
                fired = {0: set(), 1: set()}
                for e in events:
                    p = e.split()
                    self.assertIn(p[0], ("READY", "TURN", "SHOT"), e)
                    if p[0] == "SHOT":
                        target = 1 - int(p[1])
                        fired[target].add(p[2])
                        if p[3] == "sunk":
                            sunk = {nc.sq_name(x, y) for x, y in G.squares(p[5])}
                            self.assertLessEqual(sunk, fired[target], e)
                            self.assertLessEqual(sunk, hidden[target], e)
                loser = 1 - st["winner"]
                self.assertLessEqual(hidden[loser], fired[loser])


# ---- the protocol ---------------------------------------------------------------------

class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = procs.spawn("parlord", "--port", str(self.port), "--rate", "1000",
                                "--burst", "1000", "--auth", "none", stderr=None)
        self.addCleanup(procs.stop, self.proc)

    async def test_whole_game(self):
        a, b, w = await Client.connect(self.port), await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO ann")
        await b.req("HELO bob")
        await w.req("HELO wes")
        gam = await a.req("GAM")
        self.assertIn("GAM navalcombat/1 classic Naval_Combat 2 2", gam)
        self.assertIn("GAM navalcombat/1 batnav Naval_Combat 2 2", gam)
        self.assertEqual((await a.req("NEW navalcombat classic"))[-1], "OK 1")
        await a.req("JON 1")
        r = await b.req("JON 1")
        self.assertEqual(r[-1], "OK 1")
        self.assertEqual((await w.req("SEE 1"))[-1], "OK")
        seen = {"bob": [], "wes": []}

        async def req(c, who, text):
            out = await c.req(text)
            if who in seen:
                seen[who] += out
            return out
        v = await req(w, "wes", "VUE 1 GMT")
        self.assertIn("FL 5 4 3 3 2", v)
        self.assertIn("Q place", v)
        # ann places by hand, bob at random
        for t in CLASSIC_FLEET:
            r = await a.req("MOV 1 " + t)
            self.assertEqual(r[-1].rstrip(), "OK", (t, r))
        self.assertIn("ILLEGAL", (await a.req("MOV 1 a1-e1"))[-1])
        self.assertEqual((await req(b, "bob", "MOV 1 random"))[-1].rstrip(), "OK")
        v = await req(b, "bob", "VUE 1 GMT")
        bob_ships = [l.split()[2] for l in v if l.startswith("Y ")]
        self.assertEqual(len(bob_ships), 5)
        r = await req(b, "bob", "MOV 1 ready")
        self.assertTrue(any(l.endswith(" READY 1") for l in r), r)
        r = await req(w, "wes", "VUE 1 GMT")
        self.assertIn("W 0", r)                                 # ann is placing, and on the clock
        self.assertTrue(any(l.startswith("CLK 0 ") for l in r))
        r = await a.req("MOV 1 ready")
        self.assertTrue(any(l.endswith(" TURN 0") for l in r), r)
        ann = {nc.sq_name(x, y) for t in CLASSIC_FLEET for x, y in G.squares(t)}
        targets = sorted({nc.sq_name(x, y) for t in bob_ships for x, y in G.squares(t)})
        misses = sorted({nc.sq_name(x, y) for x in range(10) for y in range(10)} - ann)
        self.assertEqual((await a.req("CHK 1 " + targets[0]))[-1].rstrip(), "OK")
        for i, t in enumerate(targets):
            r = await a.req("MOV 1 " + t)
            self.assertEqual(r[-1].rstrip(), "OK", r)
            if i < len(targets) - 1:
                m = [l[2:] for l in await req(b, "bob", "VUE 1 GMT") if l.startswith("M ")]
                self.assertEqual(len(m), 100 - i)
                await req(b, "bob", "MOV 1 " + misses[i])
                await req(w, "wes", "VUE 1 GMT")
        self.assertTrue(any(l.endswith(" END win 0 17-0") for l in r), r)
        for who, c in (("bob", b), ("wes", w)):
            await req(c, who, "VUE 1 GMT")
            await req(c, who, "SIN 1 0")
        # what bob and the watcher received: ann's fleet appears nowhere
        for who, lines in seen.items():
            for l in lines:
                f = l.split()
                if f[0] == "B" and f[1] == "0":
                    self.assertNotIn("s", f[3], (who, l))
                    for x, c in enumerate(f[3]):
                        if c != ".":
                            self.assertIn(nc.sq_name(x, int(f[2]) - 1), misses[:len(targets)], (who, l))
                self.assertFalse(f[0] == "K" and f[1] == "0", (who, l))
                if who == "wes":
                    self.assertNotEqual(f[0], "Y", l)
                    self.assertFalse(f[0] == "B" and "s" in f[3], l)
                if f[0] == "EVT":
                    self.assertIn(f[3], ("JOIN", "START", "TURN", "READY", "SHOT", "END", "CHT"), l)
                    if f[3] == "SHOT" and f[4] == "1":
                        self.assertNotIn(f[5], ann, l)
        v = await w.req("VUE 1 GMT")
        self.assertIn("S Game over: seat 0 sank the whole fleet", v)

    async def test_batnav_table(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO ann")
        await b.req("HELO bob")
        self.assertEqual((await a.req("NEW navalcombat batnav"))[-1], "OK 1")
        await a.req("JON 1")
        await b.req("JON 1")
        v = await a.req("VUE 1 GMT")
        self.assertIn("FL 4 3 3 2 2 2 1 1 1 1", v)
        self.assertIn("N 4", v)
        for t in BATNAV_FLEET:
            self.assertEqual((await a.req("MOV 1 " + t))[-1].rstrip(), "OK", t)
        await b.req("MOV 1 a1-d1")
        self.assertIn("ILLEGAL", (await b.req("MOV 1 a2-c2"))[-1])


class WebGame(Stack):
    """A whole Naval Combat game on the web, both players signed in with sessions (a password, then the
    cookie): the fleets placed at random and readied from the page, then every shot from a /move.htm
    link with the session's x=. The protocol connections only read each player's own fleet."""

    def test_a_whole_game_on_the_web(self):
        ann, bob = self.browser(), self.browser()
        for name, b in (("ann", ann), ("bob", bob)):
            self.assertIn("You are <b>%s</b>" % name, b.login(name, name + "-Pass-word")[3])
        st, loc, _, _ = ann.act("/new.htm?g=navalcombat%2F1&turn=1h")
        self.assertEqual(st, 302)
        tid = query_of(loc)["t"][0]
        self.assertEqual(bob.act("/join.htm?t=%s" % tid)[0], 302)
        natives = {"ann": Native(self.core, "ann", "ann-Pass-word"), "bob": Native(self.core, "bob", "bob-Pass-word")}
        for n in natives.values():
            self.addCleanup(n.close)
        for b in (ann, bob):
            body = b.get("/table.htm?t=%s&r=0" % tid)[3]
            for mv in ("random", "ready"):
                link = b.link(r"^/move\.htm\?.*mv=%s" % mv, body)
                self.assertIn("x=", link)
                st, loc, _, _ = b.get(link)
                self.assertEqual(st, 302)
                body = b.go(loc)[3]
        fleets = {}
        for name, n in natives.items():
            ships = [l.split()[2] for l in n.req("VUE %s GMT" % tid) if l.startswith("Y ")]
            self.assertEqual(len(ships), 5)
            fleets[name] = sorted({nc.sq_name(x, y) for t in ships for x, y in G.squares(t)})
        misses = sorted({nc.sq_name(x, y) for x in range(10) for y in range(10)} - set(fleets["ann"]))
        for i, target in enumerate(fleets["bob"]):
            ann.get("/table.htm?t=%s&r=0" % tid)
            self.assertEqual(ann.act("/move.htm?t=%s&mv=%s" % (tid, target))[0], 302, target)
            if i < len(fleets["bob"]) - 1:
                bob.get("/table.htm?t=%s&r=0" % tid)
                self.assertEqual(bob.act("/move.htm?t=%s&mv=%s" % (tid, misses[i]))[0], 302)
        events = [l.split(" ", 3)[3] for l in natives["bob"].req("SIN %s 0" % tid) if l.startswith("EVT ")]
        self.assertIn("END win 0 17-0", events)
        for b in (ann, bob):
            self.assertIn("Game over", b.get("/table.htm?t=%s" % tid)[3])
            self.assertNotIn("Pass-word", "".join(b.urls))


# ---- the frontends ----------------------------------------------------------------------

def placing_view(seat=0, done=2, rules="classic"):
    st = new(rules)
    fleet = CLASSIC_FLEET if rules == "classic" else BATNAV_FLEET
    st = place_all(st, seat, fleet[:done], ready=False)
    return st


def late_game(rules="batnav", seed=3, shots=70):
    """Many shots fired, some ships sunk; seat 0 to shoot."""
    import random
    rnd = random.Random(seed)
    st = started(rules, seed)
    while st["phase"] == "shoot" and shots:
        seat = st["turn"]
        r = play(st, seat, rnd.choice(nc.op_moves({"state": st, "seat": seat})["moves"]))
        if "end" in r:
            break
        st = r["state"]
        shots -= 1
    if st["turn"] != 0:
        st = play(st, 1, nc.op_moves({"state": st, "seat": 1})["moves"][0])["state"]
    return st


def finished():
    st = started()
    targets = sorted(fleet_squares(st, 1))
    misses = [nc.sq_name(x, y) for y in range(10) for x in range(10) if nc.sq_name(x, y) not in fleet_squares(st, 0)]
    for i, t in enumerate(targets):
        r = play(st, 0, t)
        st = r["state"]
        if i < len(targets) - 1:
            st = play(st, 1, misses[i])["state"]
    return st


def screens(text):
    """The text of ansi_screens -> a list of 24-line screens."""
    lines = text.split("\n")
    return [lines[i:i + 24] for i in range(0, len(lines), 24)]


def region(scr, side):
    """The squares of the left (0) or right (1) grid on a full screen."""
    x0 = G.GX[side]
    return [scr[G.GY + y][x0:x0 + 20] if len(scr) > G.GY + y else "" for y in range(10)]


SHIP_SHAPES = ("█", "[]")


class Parse(unittest.TestCase):
    def test_parse(self):
        v = G.parse_view(["V navalcombat/1 1", "S Waiting for players"])
        self.assertFalse(v["rows"])
        st = late_game()
        v = G.parse_view(vue(st, 0))
        self.assertTrue(v["rows"])
        self.assertEqual((v["phase"], v["turn"], v["n"]), ("shoot", 0, 10))
        self.assertEqual(v["fleet"], [4, 3, 3, 2, 2, 2, 1, 1, 1, 1])
        self.assertEqual(len(v["mine"]), 10)
        self.assertEqual(v["boards"][0], board(vue(st, 0), 0))
        self.assertEqual(len(G.afloat_sizes(v, 1)), v["afloat"][1])
        self.assertTrue(v["clock"])

    def test_describe(self):
        names = ["ann", "bob"]
        self.assertEqual(G.describe_event("READY 1", names), "bob's fleet is ready")
        self.assertEqual(G.describe_event("SHOT 0 e5 miss", names), "ann fires at e5: miss")
        self.assertEqual(G.describe_event("SHOT 1 e5 hit", names), "bob fires at e5: hit")
        self.assertEqual(G.describe_event("SHOT 0 e5 sunk 3 c5-e5", names),
                         "ann fires at e5: hit, and sinks bob's 3-square ship")
        self.assertEqual(G.describe_event("END win 1 9-17", names),
                         "Game over: bob sinks ann's whole fleet (hits 9-17)")
        self.assertIsNone(G.describe_event("END resign 0", names))
        self.assertIsNone(G.describe_event("JOIN 0 ann", names))
        from adapters import web as W
        self.assertEqual(W.describe("SHOT 1 a1 hit", names, "navalcombat/1"), "bob fires at a1: hit")


def tiles(tc, lines, seat, sel=""):
    """The squares drawn on the board image, as image paths."""
    return [p for p, _, _ in board_image(tc, G, lines, seat, sel).used]


def ships(tc, lines, seat):
    return tiles(tc, lines, seat).count(G.IMG % "ship")


class Web(unittest.TestCase):
    def links(self, html):
        return re.findall(r'href="([^"]+)"', html)

    def mvs(self, html):
        return [re.search(r"mv=([^&;]+)", h).group(1) for h in self.links(html) if "mv=" in h]

    def test_placing(self):
        st = placing_view()
        html = web_page(self, G, vue(st, 0), 0)
        mvs = self.mvs(html)
        self.assertEqual(sorted(set(mvs)), ["clear", "random", "undo"])
        self.assertIn("sel=a5", html)
        self.assertEqual(ships(self, vue(st, 0), 0), 9)             # the two ships placed: 5 + 4 squares
        self.assertIn(G.IMG % "start", tiles(self, vue(st, 0), 0))
        self.assertIn("Place the rest at random", html)
        # a start square picked: across and down, and the yellow rings
        html = web_page(self, G, vue(st, 0), 0, sel="a5")
        # each end three times: a map area, the same link as text under the image, and Across or Down
        self.assertEqual(sorted(self.mvs(html)), sorted(["a5-c5", "a5-a7"] * 3 + ["random", "undo", "clear"]))
        used = tiles(self, vue(st, 0), 0, sel="a5")
        self.assertIn(G.IMG % "end", used)
        self.assertIn(G.IMG % "sel", used)
        # a start square that is not one: no direction links
        html = web_page(self, G, vue(st, 0), 0, sel="a1")
        self.assertNotIn("Across", html)
        for mv in self.mvs(html):
            self.assertTrue(G.MOVE_RE.match(mv), mv)
        # the other seat and a watcher see nothing of seat 0's ships
        for viewer in (1, -1):
            html = web_page(self, G, vue(st, viewer), viewer)
            self.assertEqual(ships(self, vue(st, viewer), viewer), 0)
            self.assertNotIn("a1-e1", html)
        # fleet complete: ready
        st = place_all(new(), 0, CLASSIC_FLEET, ready=False)
        html = web_page(self, G, vue(st, 0), 0)
        self.assertEqual(sorted(set(self.mvs(html))), ["clear", "random", "ready", "undo"])
        self.assertIn("<b>Ready</b>", html)

    def test_batnav_one_square_ships(self):
        st = placing_view(done=6, rules="batnav")
        html = web_page(self, G, vue(st, 0), 0)
        self.assertIn("mv=j10-j10", html)                         # no direction to choose
        self.assertNotIn("sel=", html)

    def test_shooting(self):
        st = late_game()
        html = web_page(self, G, vue(st, 0), 0, names=(LONG, LONG.upper()))
        mvs = self.mvs(html)
        self.assertEqual(sorted(mvs), sorted(nc.op_moves({"state": st, "seat": 0})["moves"]))
        for mv in mvs:
            self.assertTrue(G.MOVE_RE.match(mv), mv)
        self.assertEqual(ships(self, vue(st, 0), 0), sum(r.count("s") for r in board(vue(st, 0), 0)))
        self.assertIn("Last shot", html)
        html1 = web_page(self, G, vue(st, 1), 1)
        self.assertEqual(self.mvs(html1), [])
        self.assertEqual(ships(self, vue(st, 1), 1), sum(r.count("s") for r in board(vue(st, 1), 1)))
        watch = web_page(self, G, vue(st, -1), -1)
        self.assertEqual(ships(self, vue(st, -1), -1), 0)
        self.assertNotIn("Your ships", watch)
        # the page source names none of the other fleet's squares: no ship token, no hidden square in an alt
        own1 = {nc.ship_token(s) for s in st["ships"][1]}
        for viewer, html in ((1, html1), (-1, watch)):
            for s in st["ships"][0]:
                if not nc.is_sunk(st, 0, s) and nc.ship_token(s) not in own1:
                    self.assertNotIn(nc.ship_token(s), html)

    def test_end(self):
        st = finished()
        for seat in (0, 1, -1):
            html = web_page(self, G, vue(st, seat), seat)
            self.assertEqual(self.mvs(html), [])
        self.assertGreater(ships(self, vue(st, 0), 0), 0)                   # the winner's own ships
        self.assertEqual(ships(self, vue(st, 1), 1), 0)                     # all sunk
        self.assertEqual(ships(self, vue(st, -1), -1), 0)
        self.assertIn("Game over", G.parse_view(vue(st, 1))["status"])


class Screens(unittest.TestCase):
    def test_waiting(self):
        lines = ["V navalcombat/1 1", "S Waiting for players"]
        for me in (0, 1, -1):
            for mode, (shown, _) in ansi_screens(self, G, lines, me).items():
                self.assertIn("Waiting for a second player", shown)
            self.assertIn("Waiting for a second player", plain_screen(self, G, lines, me))

    def test_placing_keys(self):
        """Enter picks where the ship starts, Right or Down point it with a preview, and Enter places it. Esc
        drops the pick. G places the rest at random, U takes back a ship and Z clears them all."""
        st = placing_view()                         # the 3-square ship next
        for mode, (shown, reqs) in ansi_screens(self, G, vue(st, 0), 0,
                                                keys=["DOWN", "DOWN", "DOWN", "ENTER", "ENTER"]).items():
            self.assertIn("MOV 3 a4-c4", reqs, mode)
            self.assertIn("Ship 3 of 5 is 3 squares long.", shown)
            self.assertIn("Ship 3 starts at a4. Press Right or Down to point it, then Enter to place it.", shown)
        for mode, (shown, reqs) in ansi_screens(self, G, vue(st, 0), 0,
                                                keys=["RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "ENTER", "DOWN",
                                                      "ENTER"]).items():
            self.assertIn("MOV 3 f1-f3", reqs, mode)
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["DOWN", "ENTER", "ESC", "ESC"])["pc"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")])          # Esc drops the pick
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["g"])["pc"]
        self.assertIn("MOV 3 random", reqs)
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["u", "z"])["ansi"]
        self.assertIn("MOV 3 undo", reqs)
        self.assertIn("MOV 3 clear", reqs)
        shown, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["ENTER", "ENTER"])["utf8"]   # a1 is taken
        self.assertFalse([r for r in reqs if r.startswith("MOV")])
        self.assertIn("doesn't fit", shown)
        for typed in ("h8 down", "b9-d9", "reset"):           # typed, each runs on the server (CMD)
            _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["/"] + list(typed) + ["ENTER"])["pc"]
            self.assertIn("CMD 3 /" + typed, reqs)
        cell = (G.GX[0] + 2 * 4, G.GY + 6)
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=[("CLICK",) + cell, ("CLICK",) + cell])["pc"]
        self.assertIn("MOV 3 e7-g7", reqs)
        # the preview of the ship picked, in its own shape
        shown, _ = ansi_screens(self, G, vue(st, 0), 0, keys=["DOWN", "DOWN", "DOWN", "ENTER"])["pc"]
        self.assertIn("░░" * 3, screens(shown)[-1][G.GY + 3])
        st = place_all(new(), 0, CLASSIC_FLEET, ready=False)
        for mode, (shown, reqs) in ansi_screens(self, G, vue(st, 0), 0, keys=["ENTER"]).items():
            self.assertIn("MOV 3 ready", reqs)
            self.assertIn("Your fleet is placed.", shown)

    def test_shooting_keys(self):
        st = late_game()
        free = nc.op_moves({"state": st, "seat": 0})["moves"]
        for mode, (shown, reqs) in ansi_screens(self, G, vue(st, 0), 0, keys=["ENTER"]).items():
            mov = [r for r in reqs if r.startswith("MOV")]
            self.assertEqual(len(mov), 1)
            self.assertIn(mov[0].split()[2], free)
            self.assertIn("Your shot", shown)
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=["/"] + list(free[-1]) + ["ENTER"])["ansi"]
        self.assertIn("CMD 3 /%s" % free[-1], reqs)
        x, y = G.xy(free[0])
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=[("CLICK", G.GX[1] + 2 * x + 1, G.GY + y)])["pc"]
        self.assertIn("MOV 3 %s" % free[0], reqs)
        # a click on your own grid fires nothing; neither does the other seat
        _, reqs = ansi_screens(self, G, vue(st, 0), 0, keys=[("CLICK", G.GX[0] + 1, G.GY)])["pc"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")])
        shown, reqs = ansi_screens(self, G, vue(st, 1), 1, keys=["ENTER"])["pc"]
        self.assertFalse([r for r in reqs if r.startswith("MOV")])
        self.assertIn("It is %s's turn." % LONG, shown)

    def test_hidden_on_screen(self):
        """The other fleet's ships never show: seat 1 and a watcher see no ship shape on seat 0's grid."""
        for st in (placing_view(done=5), late_game(), late_game("classic", 5, 40), finished()):
            for me in (0, 1, -1):
                for mode, (shown, _) in ansi_screens(self, G, vue(st, me), me).items():
                    for scr in screens(shown):
                        for side, seat in enumerate(G.sides(me)):
                            grid = "".join(region(scr, side))
                            own = sum(r.count("s") for r in board(vue(st, me), seat)) if seat == me else 0
                            shape = "██" if mode != "ansi" else "[]"
                            self.assertEqual(grid.count(shape), own, (mode, me, side))
                text = plain_screen(self, G, vue(st, me), me)
                grids = [l for l in text.split("\n") if re.match(r"^ ?\d+ \|", l)]
                self.assertEqual(len(grids), 10)
                for side, seat in enumerate(G.sides(me)):
                    cells = "".join(l[4 + 28 * side:24 + 28 * side] for l in grids)
                    own = sum(r.count("s") for r in board(vue(st, me), seat)) if seat == me else 0
                    self.assertEqual(cells.count("[]"), own, (me, side))

    def test_crowded_and_end(self):
        for st in (late_game(), late_game("classic", 9, 90), finished()):
            for me in (0, 1, -1):
                for mode, (shown, _) in ansi_screens(self, G, vue(st, me), me, keys=["?", "WAIT", "x"]).items():
                    self.assertIn(LONG[:12], shown)
                text = plain_screen(self, G, vue(st, me), me)
                self.assertIn("is sea,", text)
        st = finished()
        shown, _ = ansi_screens(self, G, vue(st, 1), 1, prepare=lambda g: setattr(g, "over", "Game over: x"))["pc"]
        self.assertIn("Game over", shown)

    def test_plain(self):
        st = placing_view()
        text = plain_screen(self, G, vue(st, 0), 0)
        self.assertIn("Place your 3-square ship. Type /a1 across or /a1 down, or /random. > ", text)
        self.assertIn("[][][][][]", text)
        text = plain_screen(self, G, vue(late_game(), 0), 0)
        self.assertIn("Your shot. Type a square such as /", text)
        self.assertIn("%s's fleet" % LONG.upper(), text)
        text = plain_screen(self, G, vue(late_game(), -1), -1)
        self.assertIn("%s's fleet" % LONG, text)
        self.assertNotIn("[]", text)

    def test_plain_typed(self):
        st = placing_view()
        g = G.PlainScreen.__new__(G.PlainScreen)
        g.v, g.me, g.over, g.names = G.parse_view(vue(st, 0)), 0, "", ["ann", "bob"]
        self.assertEqual(g.move_word("a4", ["a4"]), "a4-c4")
        self.assertEqual(g.move_word("a4", ["a4", "down"]), "a4-a6")
        self.assertEqual(g.move_word("a4", ["a4", "sideways"]), None)
        self.assertEqual(g.move_word("random", ["random"]), "random")
        self.assertEqual(g.move_word("j9", ["j9", "across"]), None)        # off the grid
        self.assertEqual(g.move_word("a1-c1", ["a1-c1"]), "a1-c1")
        g.v = G.parse_view(vue(late_game(), 0))
        self.assertEqual(g.move_word("e5", ["e5"]), "e5")
        self.assertIsNone(g.move_word("k5", ["k5"]))


if __name__ == "__main__":
    unittest.main()
