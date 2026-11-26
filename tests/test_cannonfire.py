"""cannonfire/1: the plugin (terrain, ballistics, craters, falls, damage, the end), a whole game over the
protocol, and the web and telnet screens (tests/game_checks.py)."""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

from game_checks import LONG, ansi_screens, board_image, plain_screen, web_page
from test_adapters import Native, Stack
from test_core import ROOT, Client, free_port

from adapters.games import cannonfire as G  # noqa: E402
from speed import slow  # noqa: E402

_spec = importlib.util.spec_from_file_location("cf_plugin", os.path.join(ROOT, "plugins", "cannonfire", "plugin.py"))
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)

EVT_ROOM = 80 - 2 - len("EVT 9999 99999 ")     # what an event may take, with CR LF and a long prefix

# seed=2026, 72 columns, two players: the recorded opening, then moves and shots (the conformance vector, recorded
# again for the rules with moves: damage by distance only, none from falls)
VECTOR_VIEW = ["Z 0 QNOMONKMMKKKKLLKLLKJJHFHGFIIIKLMOLKKKJHFFFFEFCCDCCAA?<877878", "Z 60 666556675566",
               "K 0 11 100", "K 1 61 100", "A 0 45 50", "A 1 135 50", "L 0 4", "L 1 4", "N -4", "W 0", "B 72 0",
               "T 0 0", "T 1 0"]
VECTOR_TURNS = [
    ((0, "MOVE L 7"), ["MOVE 0 11 7 52 3"]),
    ((0, "FIRE 45 60"), ["FIRE 0 45 60", "TRJ 10 34 14 36 17 38 21 40 24 41 27 42",
                         "TRJ 31 41 34 41 37 40 40 38 43 36 46 33", "TRJ 49 30 52 26 55 21 58 16 61 11 64 4",
                         "HIT 64 5 7", "DMG 1 86", "ZCH 57 721000000000125", "FALL 1 61 86", "N -2", "TURN 1"]),
    ((1, "MOVE R 7"), ["MOVE 1 61 68 70 3"]),
    ((1, "FIRE 120 55"), ["FIRE 1 120 55", "TRJ 67 3 66 4 65 6 65 7 64 8 63 9", "TRJ 62 10 61 11 60 12 59 13 58 13 57 14",
                          "TRJ 56 14 55 15 54 15 53 15 53 15 51 15", "HIT 51 17 7", "ZCH 44 E>=<;;;:;;", "N -3",
                          "TURN 0"]),
    ((0, "FIRE 50 66"), ["FIRE 0 50 66", "TRJ 11 35 15 38 18 41 22 44 26 45 30 46",
                         "TRJ 33 46 37 46 41 45 44 43 47 41 51 37", "TRJ 54 33 58 29 61 24 64 18 68 11 71 4",
                         "HIT 71 5 7", "DMG 1 76", "ZCH 69 000", "N -5", "TURN 1"]),
    ((1, "MOVE -3"), ["MOVE 1 68 65 30 2"]),
    ((1, "FIRE 135 45"), ["FIRE 1 135 45", "TRJ 65 2 64 2 63 3 63 4 62 4 62 4", "TRJ 61 5 60 5 60 5 59 6 59 6 58 6",
                          "TRJ 58 7 57 7 56 7 56 7 55 7 54 7", "HIT 54 8 7", "ZCH 47 ;5432221222", "N -6", "TURN 0"]),
]
VECTOR_END = ["Z 0 QNOMONKMMKKKKLLKLLKJJHFHGFIIIKLMOLKKKJHFFFFEE>=;543222122221", "Z 60 000000000000",
              "K 0 7 100", "K 1 65 76", "A 0 50 66", "A 1 135 45", "L 0 3", "L 1 2", "N -6", "H 54 8", "W 0", "B 72 0",
              "T 0 24", "T 1 0"]
# the same opening's first shot without a move (the web and protocol tests fire it)
FIRST_SHOT = ["FIRE 0 45 60", "TRJ 14 32 18 34 21 36 24 38 27 39 31 40", "TRJ 34 40 37 39 40 38 43 36 46 35 49 32",
              "TRJ 52 29 55 25 58 21 61 16 63 11 66 5", "HIT 66 6 7", "DMG 1 90", "ZCH 59 7321000000012",
              "FALL 1 61 90", "N -2", "TURN 1"]


def new(n=2, seed=1, **opts):
    r = cf.op_new({"players": n, "options": {k: str(v) for k, v in opts.items()}, "seed": seed})
    assert "error" not in r, r
    return r["state"]


def flat(n=2, xs=(10, 40), h=20, w=72, wind=0):
    """A hand-made state: flat ground, tanks at the columns given, no wind."""
    st = new(n, 1, w=w, wind=0)
    st["h"] = [h] * w
    st["tanks"] = [[x, 100] for x in xs]
    st["aim"] = [[45, 50] for _ in xs]
    st["score"] = [0] * n
    st["wind"] = wind
    return st


def fire(st, seat, angle, power):
    r = cf.op_apply({"state": st, "seat": seat, "move": ["FIRE", str(angle), str(power)]})
    assert "error" not in r, r
    return r


def play(st, seat, move, check=False):
    """op_apply with a move as typed ('MOVE L 7') -> the reply."""
    return cf.op_apply({"state": st, "seat": seat, "move": move.split(), "check": check})


def slope(st, x0, rise, w=72, base=20):
    """Ground flat at base up to column x0, then rising by rise a column to the right (capped at 63)."""
    st["h"] = [base if x <= x0 else min(63, base + rise * (x - x0)) for x in range(w)]
    return st


def aim_at(st, seat, target_x, angles=(45, 60, 30, 75)):
    """The test's own gunner: the angle and power whose burst lands nearest the target column."""
    x = st["tanks"][seat][0]
    best = None
    for base in angles:
        ang = base if target_x > x else 180 - base
        for p in range(0, 101):
            _, impact, _ = cf.fly(st, seat, ang, p)
            if impact is None:
                continue
            miss = abs(impact[0] - target_x)
            if best is None or miss < best[0]:
                best = (miss, ang, p)
        if best and best[0] == 0:
            break
    return best[1], best[2]


def play_out(st, limit=200):
    """Each living tank shoots at the next living one until the game ends -> (state, end, every event)."""
    events = []
    for _ in range(limit):
        seat = st["turn"]
        target = cf.next_seat(st, seat)
        a, p = aim_at(st, seat, st["tanks"][target][0])
        r = fire(st, seat, a, p)
        events += r["events"]
        st = r["state"]
        if "end" in r:
            return st, r["end"], events
    return st, None, events


def view_lines(st, seat=0, seq=40, clock=True, status="x"):
    lines = ["V cannonfire/1 %d" % seq, "ARN %d 63" % st["w"]] + cf.op_view({"state": st, "seat": seat})["wire"]
    if clock and not st["over"]:
        lines.append("CLK %d 200 300" % st["turn"])
    return lines + ["S " + status]


class Terrain(unittest.TestCase):
    def test_new_game(self):
        for n in range(2, 9):
            for seed in (1, 2, 99):
                st = new(n, seed)
                self.assertEqual(len(st["h"]), 72)
                self.assertTrue(all(1 <= h <= 50 for h in st["h"]))
                xs = sorted(x for x, _ in st["tanks"])
                self.assertEqual(len(set(xs)), n)
                self.assertTrue(all(2 <= x <= 69 for x in xs))
                for x, hp in st["tanks"]:
                    self.assertEqual(hp, 100)
                    self.assertEqual(st["h"][x - 1], st["h"][x])       # a flat pad under each tank
                    self.assertEqual(st["h"][x + 1], st["h"][x])
                self.assertTrue(-5 <= st["wind"] <= 5)
                self.assertEqual((st["turn"], st["score"]), (0, [0] * n))
        self.assertEqual(len(new(2, 1, w=200)["h"]), 200)
        self.assertEqual(new(2, 5, wind=0)["wind"], 0)

    def test_options(self):
        for opts, players in (({"w": "39"}, 2), ({"w": "201"}, 2), ({"w": "x"}, 2), ({"wind": "2"}, 2),
                              ({"seed": "-1"}, 2), ({"size": "8"}, 2), ({}, 1), ({}, 9), ({"w": "60"}, 8)):
            self.assertIn("error", cf.op_new({"players": players, "options": opts, "seed": 1}), opts)
        self.assertNotIn("error", cf.op_new({"players": 8, "options": {"w": "64"}, "seed": 1}))
        # seed= replaces the table's seed: the same landscape whatever the table drew
        a = cf.op_new({"players": 2, "options": {"seed": "77"}, "seed": 1})["state"]
        b = cf.op_new({"players": 2, "options": {"seed": "77"}, "seed": 2})["state"]
        self.assertEqual(a, b)
        self.assertNotEqual(new(2, 1)["h"], new(2, 2)["h"])

    def test_info(self):
        i = cf.op_info({})
        self.assertEqual((i["type"], i["ver"], i["name"], i["min"], i["max"]), ("cannonfire", 1, G.NAME, 2, 8))
        self.assertEqual(i["flags"], ["nomoves", "drop"])
        self.assertEqual(cf.op_moves({"state": new(), "seat": 0}), {"check": True})

    def test_view(self):
        st = new(3, 4, w=150)
        r = cf.op_view({"state": st, "seat": 1})
        self.assertEqual(r["geometry"], ["ARN 150 63"])
        z = [l for l in r["wire"] if l.startswith("Z ")]
        self.assertEqual([l.split()[1] for l in z], ["0", "60", "120"])
        self.assertEqual("".join(l.split()[2] for l in z), cf.enc(st["h"]))
        self.assertTrue(all(len(l) <= 78 for l in r["wire"]))
        v = G.parse_view(view_lines(st, 1))
        self.assertEqual(v["rows"], st["h"])
        self.assertEqual(v["tanks"], [tuple(t) for t in st["tanks"]])
        self.assertEqual((v["turn"], v["wind"], v["w"], v["hit"]), (0, st["wind"], 150, None))
        self.assertEqual([l for l in r["wire"] if l[:2] in ("L ", "B ")], ["L 0 4", "L 1 4", "L 2 4", "B 150 0"])
        self.assertEqual((v["left"], v["budget"], v["moved"]), ([4, 4, 4], 150, False))
        self.assertEqual(r["status"], "Waiting for tank A to fire")
        self.assertEqual(cf.op_view({"state": st, "seat": 0})["status"], "Your shot")


class Physics(unittest.TestCase):
    def test_recorded_game(self):
        """The conformance vector: seed=2026 gives this landscape and these moves and shots, on every machine."""
        st = cf.op_new({"players": 2, "options": {"seed": "2026"}, "seed": 5})["state"]
        self.assertEqual(cf.op_view({"state": st, "seat": 0})["wire"], VECTOR_VIEW)
        self.assertEqual(fire(st, 0, 45, 60)["events"], FIRST_SHOT)
        for (seat, move), events in VECTOR_TURNS:
            r = play(st, seat, move)
            self.assertEqual(r["events"], events, move)
            st = r["state"]
        self.assertEqual(cf.op_view({"state": st, "seat": 0})["wire"], VECTOR_END)

    MOVES = ["MOVE R 5", "FIRE 45 60", "FIRE 130 55", "MOVE -9", "FIRE 60 70", "FIRE 120 40", "MOVE L 2",
             "FIRE 80 30", "MOVE +4", "FIRE 100 90", "FIRE 45 60", "FIRE 130 55"]

    def test_determinism(self):
        """The same seed, moves and shots give the same game, in this process and in the plugin process."""
        def run():
            st, out = new(3, 31337), []
            for mv in self.MOVES:
                r = play(st, st["turn"], mv)
                out.append((r.get("events"), r.get("end"), r.get("error")))
                st = r.get("state", st)
                if "end" in r:
                    break
            return st, out
        self.assertEqual(run(), run())
        st0, mine = run()
        self.assertTrue(any(e and e[0].startswith("MOVE ") for e, _, _ in mine))
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "plugins", "cannonfire", "plugin.py")],
                              input="\n".join(json.dumps(q) for q in self._requests()) + "\n",
                              capture_output=True, text=True, timeout=30)
        replies = [json.loads(l) for l in proc.stdout.splitlines()]
        got = [(r.get("events"), r.get("end"), r.get("error")) for r in replies[1:]][:len(mine)]
        self.assertEqual(got, mine)

    def _requests(self):
        """The plugin protocol requests for run(): new, then each move on the state it returned."""
        st = new(3, 31337)
        reqs = [{"op": "new", "players": 3, "options": {}, "seed": 31337}]
        for mv in self.MOVES:
            reqs.append({"op": "apply", "state": st, "seat": st["turn"], "move": mv.split()})
            r = cf.op_apply(reqs[-1])
            st = r.get("state", st)
            if "end" in r:
                break
        return reqs

    def test_launch(self):
        self.assertEqual(cf.launch(90, 100), (0, 51200))
        self.assertEqual(cf.launch(0, 100), (51200, 0))
        self.assertEqual(cf.launch(180, 100), (-51200, 0))
        for a in range(0, 91):
            vx, vy = cf.launch(a, 77)
            self.assertEqual(cf.launch(180 - a, 77), (-vx, vy))
        self.assertEqual(cf.launch(45, 0), (0, 0))

    def test_wind_pushes(self):
        st = flat(xs=(10, 60))
        still = cf.fly(st, 0, 60, 50)[1][0]
        st["wind"] = 9
        self.assertGreater(cf.fly(st, 0, 60, 50)[1][0], still)
        st["wind"] = -9
        self.assertLess(cf.fly(st, 0, 60, 50)[1][0], still)

    def test_straight_up_comes_down_on_yourself(self):
        st = flat(xs=(10, 60))
        r = fire(st, 0, 90, 40)
        # the burst at the tank's feet, 1 from its center: 18; the ground drops 7 and the tank with it, unharmed
        bowl = cf.enc([17, 16, 15, 14, 14, 14, 13, 14, 14, 14, 15, 16, 17])
        self.assertEqual(r["events"][-5:-1], ["HIT 10 20 7", "DMG 0 82", "ZCH 4 " + bowl, "FALL 0 10 82"])
        self.assertEqual(r["state"]["score"], [0, 0])            # hurting yourself scores nothing

    def test_out_of_the_arena(self):
        st = flat(xs=(10, 68))
        st["turn"] = 1
        r = fire(st, 1, 10, 100)
        self.assertIn("OUT 71", r["events"])
        self.assertFalse(any(e.startswith(("HIT", "DMG", "ZCH")) for e in r["events"]))
        self.assertEqual(r["state"]["h"], st["h"])
        self.assertNotIn("H", [l.split()[0] for l in cf.op_view({"state": r["state"], "seat": 0})["wire"]])

    def test_errors(self):
        st = new()
        for move, why in ((["FIRE", "181", "50"], "angle"), (["FIRE", "45", "101"], "power"),
                          (["FIRE", "45"], "FIRE angle power"), (["SHOOT", "1", "2"], "FIRE angle power"),
                          (["FIRE", "-1", "50"], "whole numbers"), (["FIRE", "4.5", "50"], "whole numbers")):
            self.assertIn(why, cf.op_apply({"state": st, "seat": 0, "move": move})["error"])
        self.assertIn("not your turn", cf.op_apply({"state": st, "seat": 1, "move": ["FIRE", "45", "50"]})["error"])
        self.assertEqual(cf.op_apply({"state": st, "seat": 0, "move": ["fire", "45", "50"], "check": True}),
                         {"info": "angle 45 power 50"})
        st["over"] = True
        self.assertIn("over", cf.op_apply({"state": st, "seat": 0, "move": ["FIRE", "45", "50"]})["error"])


class Craters(unittest.TestCase):
    def test_surface_crater(self):
        h = [20] * 30
        changed = cf.crater(h, 15, 20, 4)
        self.assertEqual(changed, list(range(12, 19)))
        self.assertEqual(h[11:20], [20, 18, 17, 17, 16, 17, 17, 18, 20])   # a bowl, 4 deep in the middle
        self.assertEqual(h[:11] + h[20:], [20] * 21)

    def test_collapse_into_a_buried_hole(self):
        h = [30] * 20
        cf.crater(h, 10, 20, 4)                  # 10 below the surface: the dirt above falls in
        self.assertEqual(h[10], 30 - 9)
        self.assertEqual(h[6], 30 - 1)
        self.assertEqual(h[14], 30 - 1)

    def test_edges_and_bedrock(self):
        h = [2] * 10
        changed = cf.crater(h, 0, 2, 4)
        self.assertEqual(changed[0], 0)
        self.assertEqual(h[0], 0)
        self.assertTrue(all(x >= 0 for x in h))

    def test_zch_reports_the_new_ground(self):
        st = flat(xs=(10, 60))
        a, p = aim_at(st, 0, 35)
        r = fire(st, 0, a, p)
        hit = next(e for e in r["events"] if e.startswith("HIT ")).split()
        self.assertEqual(hit[3], "7")
        zch = [e.split() for e in r["events"] if e.startswith("ZCH ")]
        self.assertTrue(zch)
        new_h = list(st["h"])
        for _, x, chars in zch:
            for i, c in enumerate(chars):
                new_h[int(x) + i] = ord(c) - 0x30
        self.assertEqual(new_h, r["state"]["h"])
        self.assertEqual(r["state"]["hit"], [int(hit[1]), int(hit[2])])


class Damage(unittest.TestCase):
    def direct_hit(self, st, seat, target):
        a, p = aim_at(st, seat, st["tanks"][target][0])
        r = fire(st, seat, a, p)
        x = st["tanks"][target][0]
        self.assertIn("HIT %d %d %d" % (x, st["h"][x] + 1, cf.RADIUS), r["events"])
        return r

    def test_direct_hit_and_fall(self):
        st = flat(n=3, xs=(10, 40, 46))
        r = self.direct_hit(st, 0, 1)
        ev = r["events"]
        # 20 for the direct hit (the most a shot does); six columns away, 8. The ground drops 6 under the first
        # tank and 2 under the second: both fall with it, unharmed.
        self.assertEqual([e for e in ev if e.startswith(("DMG", "FALL", "DIE"))],
                         ["DMG 1 80", "DMG 2 92", "FALL 1 40 80", "FALL 2 46 92"])
        order = [e.split()[0] for e in ev]
        self.assertLess(order.index("HIT"), order.index("DMG"))
        self.assertLess(order.index("DMG"), order.index("ZCH"))
        self.assertLess(order.index("ZCH"), order.index("FALL"))
        self.assertEqual(ev[-1], "TURN 1")
        self.assertEqual(r["state"]["tanks"][1], [40, 80])
        self.assertEqual(r["state"]["h"][40], 14)
        self.assertEqual(r["state"]["h"][46], 18)
        self.assertEqual(r["state"]["score"], [28, 0, 0])

    def test_blast_falls_off_with_distance(self):
        """20 at the burst, 2 less for each unit of distance (floor square root) from the tank's center, none
        from 10 on: DMG_MAX * (REACH - d) // REACH. The crater lowers the nearer tanks too: no more damage."""
        self.assertEqual((cf.DMG_MAX, cf.REACH), (20, 10))
        # the burst on the ground, the tank's center 1 above it: d = isqrt(gap^2 + 1)
        for gap, hp in ((1, 82), (2, 84), (3, 86), (5, 90), (7, 94), (9, 98), (10, 100), (12, 100)):
            st = flat(xs=(10, 40))
            st["tanks"][1][0] = 40 + gap
            st2, ev, _ = cf.shoot(st, 0, *aim_at(st, 0, 40))
            self.assertIn("HIT 40 20 7", ev)
            self.assertEqual(st2["tanks"][1][1], hp, (gap, ev))

    def test_no_fall_damage_from_any_height(self):
        """A shell bursts in a deep gully beside a tall pillar: the dirt under the tank on the pillar falls into
        the hole and the tank drops 12 units with it, out of the blast's reach and unharmed."""
        st = flat(xs=(10, 41))
        st["h"] = [20] * 36 + [5] * 5 + [40] * 3 + [20] * 28
        a, p = aim_at(st, 0, 39)
        st2, ev, _ = cf.shoot(st, 0, a, p)
        hit = next(e for e in ev if e.startswith("HIT")).split()
        self.assertTrue(36 <= int(hit[1]) <= 40 and int(hit[2]) == 5, ev)
        self.assertGreaterEqual(40 - st2["h"][41], 10)
        self.assertIn("FALL 1 41 100", ev)
        self.assertFalse([e for e in ev if e.startswith(("DMG 1", "DIE"))])
        self.assertEqual(st2["tanks"][1], [41, 100])
        self.assertEqual(st2["score"], [0, 0])

    def test_falls_never_hurt(self):
        """Over whole games: a FALL leaves the tank with the hit points it had after the shot's blast."""
        for n, seed in ((2, 1), (3, 11), (4, 5)):
            st, end, events = play_out(new(n, seed))
            hp = {}
            for e in events:
                f = e.split()
                if f[0] == "FIRE":
                    hp = {}
                elif f[0] == "DMG":
                    hp[f[1]] = f[2]
                elif f[0] == "FALL":
                    self.assertNotEqual(f[3], "0", e)
                    if f[1] in hp:
                        self.assertEqual(f[3], hp[f[1]], e)

    def test_five_direct_hits_destroy_a_tank(self):
        """A direct hit takes 20 (the most): a tank stands after four and the fifth destroys it."""
        st = flat(xs=(10, 40))
        for want in (80, 60, 40, 20, 0):
            st["turn"] = 0
            r = self.direct_hit(st, 0, 1)
            self.assertIn("DMG 1 %d" % want, r["events"])
            st = r["state"]
            self.assertEqual(st["tanks"][1][1], want)
            self.assertEqual("end" in r, want == 0)
        self.assertEqual(r["end"], "win 0 125-0")                 # 100 hp taken and 25 for the kill

    def test_kill_ends_the_game(self):
        st = flat(xs=(10, 40))
        st["tanks"][1][1] = 20
        r = self.direct_hit(st, 0, 1)
        self.assertIn("DIE 1", r["events"])
        self.assertNotIn("TURN 1", r["events"])
        self.assertEqual(r["end"], "win 0 45-0")                  # 20 hp taken and 25 for the kill
        self.assertTrue(r["state"]["over"])
        self.assertEqual(cf.op_result({"state": r["state"]}), {"over": True, "scores": [45, 0]})
        self.assertEqual(cf.op_view({"state": r["state"], "seat": 0})["status"], "Game over: tank A wins")
        self.assertIn("W -1", cf.op_view({"state": r["state"], "seat": 0})["wire"])

    def test_both_destroyed_is_a_draw(self):
        st = flat(xs=(10, 12))
        st["tanks"] = [[10, 10], [12, 10]]
        r = fire(st, 0, *aim_at(st, 0, 12))
        self.assertIn("DIE 0", r["events"])
        self.assertIn("DIE 1", r["events"])
        self.assertTrue(r["end"].startswith("draw "))
        self.assertIn("Game over", cf.op_view({"state": r["state"], "seat": -1})["status"])

    def test_turns_skip_the_dead(self):
        st = flat(n=3, xs=(10, 35, 60))
        st["tanks"][1][1] = 5
        r = self.direct_hit(st, 0, 1)
        self.assertIn("DIE 1", r["events"])
        self.assertEqual(r["events"][-1], "TURN 2")
        self.assertNotIn("end", r)
        st = r["state"]
        st["turn"] = 2
        r = fire(st, 2, 45, 0)
        self.assertEqual(r["events"][-1], "TURN 0")

    def test_wrecks_do_not_stop_shells(self):
        st = flat(n=3, xs=(10, 35, 60))
        st["tanks"][1][1] = 0
        path, impact, _ = cf.fly(st, 0, *aim_at(st, 0, 35))
        self.assertEqual(impact, (35, 20))                    # the ground under the wreck, not the wreck

    def test_whole_games_end(self):
        for n, seed in ((2, 1), (2, 7), (3, 11), (4, 5), (8, 3)):
            st, end, events = play_out(new(n, seed))
            self.assertTrue(end, (n, seed))
            self.assertRegex(end, r"^(win [0-7]|draw) \d+(-\d+)*$")
            self.assertEqual(len(end.split()[-1].split("-")), n)
            alive = [s for s, (_, hp) in enumerate(st["tanks"]) if hp > 0]
            self.assertLessEqual(len(alive), 1)
            if end.startswith("win"):
                self.assertEqual(alive, [int(end.split()[1])])
            for e in events:
                self.assertLessEqual(len(e), EVT_ROOM, e)
                e.encode("ascii")
            self.assertTrue(all(0 <= h <= 63 for h in st["h"]))


class Leaving(unittest.TestCase):
    """op_drop: a seat that resigns or runs out of time with three or more playing leaves; its tank becomes a
    wreck and is skipped; with one tank left, that seat wins."""

    def test_a_tank_leaves_and_the_others_fire_on(self):
        st = flat(n=4, xs=(10, 28, 46, 64))
        st["score"] = [0, 40, 0, 0]
        r = cf.op_drop({"state": st, "seat": 0, "why": "timeout"})        # on turn
        self.assertEqual(r["events"], ["GONE 0 timeout", "DIE 0", "TURN 1"])
        self.assertNotIn("end", r)
        st = r["state"]
        self.assertEqual((st["tanks"][0][1], st["turn"], st["gone"]), (0, 1, [True, False, False, False]))
        wire = cf.op_view({"state": st, "seat": 2})["wire"]
        self.assertIn("G 1 0 0 0", wire)
        self.assertIn("K 0 10 0", wire)
        self.assertFalse([l for l in cf.op_view({"state": flat(n=4, xs=(10, 28, 46, 64)), "seat": 2})["wire"]
                          if l.startswith("G ")])
        self.assertIn("error", cf.op_drop({"state": st, "seat": 0}))     # already gone
        self.assertIn("error", cf.op_drop({"state": st, "seat": 4}))
        self.assertIn("not your turn", cf.op_apply({"state": st, "seat": 0, "move": ["FIRE", "45", "50"]})["error"])
        r = fire(st, 1, 45, 0)                                         # seat 1 fires; turns skip the wreck
        self.assertEqual(r["events"][-1], "TURN 2")
        st = r["state"]
        st["turn"] = 3
        self.assertEqual(fire(st, 3, 135, 0)["events"][-1], "TURN 1")
        # a seat not on turn leaves: GONE and DIE, no TURN; its points stay
        r = cf.op_drop({"state": st, "seat": 1})
        self.assertEqual(r["events"], ["GONE 1 resign", "DIE 1"])
        st = r["state"]
        self.assertEqual((st["turn"], st["score"][1]), (3, 40))
        # one tank left: that seat wins
        r = cf.op_drop({"state": st, "seat": 3, "why": "resign"})
        self.assertEqual(r["events"], ["GONE 3 resign", "DIE 3"])
        self.assertTrue(r["state"]["over"])
        self.assertEqual(r["end"], "win 2 0-40-0-0")
        self.assertIn("error", cf.op_drop({"state": r["state"], "seat": 2}))
        self.assertEqual(cf.op_view({"state": r["state"], "seat": 2})["status"], "Game over: tank C wins")

    def test_a_destroyed_tank_leaves(self):
        st = flat(n=3, xs=(10, 35, 60))
        st["tanks"][1][1] = 0
        r = cf.op_drop({"state": st, "seat": 1})
        self.assertEqual(r["events"], ["GONE 1 resign"])               # already a wreck: nothing else changes
        self.assertNotIn("end", r)
        r = cf.op_drop({"state": r["state"], "seat": 0})             # on turn, and only tank C is left
        self.assertEqual(r["events"], ["GONE 0 resign", "DIE 0"])
        self.assertEqual(r["end"], "win 2 0-0-0")
        st = flat(n=3, xs=(10, 35, 60))                                # an older state without the gone list
        st.pop("gone", None)
        self.assertEqual(cf.op_drop({"state": st, "seat": 2})["events"], ["GONE 2 resign", "DIE 2"])

    def test_whole_games_with_a_seat_gone(self):
        for n, seed in ((3, 11), (4, 5), (8, 3)):
            st = cf.op_drop({"state": new(n, seed), "seat": 1})["state"]
            st, end, events = play_out(st)
            self.assertTrue(end)
            self.assertNotIn("TURN 1", events)
            self.assertFalse(end.startswith("win 1 "))


class Moving(unittest.TestCase):
    """MOVE L n / MOVE R n: at most one a turn, before the shot, four a game, up to w tenths of a column of
    path, each step to the next column costing STEP[height change]."""

    def test_a_move_keeps_the_turn(self):
        st = flat(xs=(10, 40))
        r = play(st, 0, "MOVE R 5")
        self.assertEqual(r["events"], ["MOVE 0 10 15 50 3"])             # no TURN: the same seat fires next
        self.assertNotIn("end", r)
        st = r["state"]
        self.assertEqual((st["turn"], st["moved"], st["moves"], st["tanks"][0]), (0, True, [3, 4], [15, 100]))
        wire = cf.op_view({"state": st, "seat": 0})["wire"]
        self.assertIn("B 72 1", wire)
        self.assertIn("L 0 3", wire)
        self.assertIn("K 0 15 100", wire)
        self.assertEqual(cf.op_view({"state": st, "seat": 0})["status"], "Your shot")
        for again in ("MOVE R 2", "MOVE L 2"):                            # a second move this turn
            self.assertEqual(play(st, 0, again)["error"], "one move a turn, and you have moved: now fire")
            self.assertIn("error", play(st, 0, again, check=True))
        self.assertEqual(play(st, 1, "MOVE L 2")["error"], "not your turn")
        r = fire(st, 0, 175, 100)                                         # out of the arena, to the left
        self.assertEqual(r["events"][-1], "TURN 1")                       # the shot ends the turn
        st = r["state"]
        self.assertFalse(st["moved"])
        self.assertIn("B 72 0", cf.op_view({"state": st, "seat": 1})["wire"])
        self.assertEqual(play(st, 1, "MOVE -2")["events"], ["MOVE 1 40 38 20 3"])
        self.assertEqual(play(st, 0, "MOVE R 2")["error"], "not your turn")   # after the shot: the turn has passed

    def test_four_moves_a_game(self):
        st = flat(xs=(10, 60))
        for left in (3, 2, 1, 0):
            r = play(st, 0, "MOVE R 1")
            self.assertEqual(r["events"][0].split()[-1], str(left))
            st = fire(r["state"], 0, 175, 100)["state"]
            st = fire(st, 1, 5, 100)["state"]
        self.assertEqual(st["moves"], [0, 4])
        self.assertEqual(st["tanks"][0][0], 14)
        self.assertEqual(play(st, 0, "MOVE R 1")["error"], "no moves left this game: fire")
        self.assertIn("L 0 0", cf.op_view({"state": st, "seat": 0})["wire"])
        self.assertEqual(fire(st, 0, 175, 100)["events"][-1], "TURN 1")

    def test_path_on_a_slope(self):
        """The same budget (72 tenths) goes 7 columns on the flat, 5 up a slope of 1 (14 a step) and 3 up a slope
        of 2 (22 a step): a slope travels less horizontal distance. Down a slope costs the same as up."""
        self.assertEqual(cf.STEP, [10, 14, 22, 32, 41])
        for rise, x, used in ((1, 35, 70), (2, 33, 66), (3, 32, 64), (4, 31, 41)):
            st = slope(flat(xs=(30, 70)), 30, rise)
            self.assertEqual(play(st, 0, "MOVE L 9")["events"], ["MOVE 0 30 23 70 3"])    # flat to the left
            self.assertEqual(play(st, 0, "MOVE R 9")["events"], ["MOVE 0 30 %d %d 3" % (x, used)], rise)
            if rise < 4:                                  # back down the same slope: the same path to column 30
                st = slope(flat(xs=(40, 70)), 30, rise)
                st["tanks"][0][0] = x
                self.assertEqual(play(st, 0, "MOVE L 9")["events"], ["MOVE 0 %d 30 %d 3" % (x, used)])
        st = slope(flat(xs=(30, 70)), 30, 1)
        self.assertEqual(play(st, 0, "MOVE R 3")["events"], ["MOVE 0 30 32 28 3"])   # 30 tenths: 2 steps of 14
        self.assertEqual(play(st, 0, "MOVE R 999")["events"], ["MOVE 0 30 35 70 3"])  # at most the budget
        st = flat(xs=(30, 140), w=150)
        self.assertEqual(play(st, 0, "MOVE R 99")["events"], ["MOVE 0 30 45 150 3"])  # 10% of 150 columns

    def test_what_stops_a_move(self):
        st = flat(xs=(2, 40))
        self.assertEqual(play(st, 0, "MOVE L 7")["events"], ["MOVE 0 2 0 20 3"])       # the arena edge
        st["tanks"][0][0] = 0
        self.assertEqual(play(st, 0, "MOVE L 7")["error"], "the tank cannot move left: the arena edge is there")
        st = flat(xs=(10, 13))
        self.assertEqual(play(st, 0, "MOVE R 7")["events"], ["MOVE 0 10 12 20 3"])     # another tank
        st["tanks"][0][0] = 12
        self.assertEqual(play(st, 0, "MOVE R 7")["error"], "the tank cannot move right: another tank is in the way")
        st = flat(n=3, xs=(10, 13, 60))
        st["tanks"][1][1] = 0
        self.assertEqual(play(st, 0, "MOVE R 7")["events"], ["MOVE 0 10 12 20 3"])     # a wreck too
        st = flat(xs=(10, 40))
        st["h"][13] = 25                                                              # a step of 5: too steep
        self.assertEqual(play(st, 0, "MOVE R 7")["events"], ["MOVE 0 10 12 20 3"])
        st["h"][11] = 25
        self.assertEqual(play(st, 0, "MOVE R 7")["error"], "the tank cannot move right: the ground is too steep")
        st["h"][11] = 24                                                              # a step of 4 drives
        self.assertEqual(play(st, 0, "MOVE R 1")["error"],
                         "the tank cannot move right: that is not enough path for one column")
        self.assertEqual(play(st, 0, "MOVE R 9")["events"], ["MOVE 0 10 11 41 3"])     # 41 up; 41 down is too far
        # into a crater and out: a tank drives onto a crater's edge and settles on the ground
        st = flat(xs=(10, 40))
        cf.crater(st["h"], 16, 20, 7)
        r = play(st, 0, "MOVE R 7")
        self.assertEqual(r["events"][0].split()[:3], ["MOVE", "0", "10"])
        self.assertGreater(r["state"]["tanks"][0][0], 10)
        self.assertEqual(st["tanks"][0], [10, 100])                                    # the state given is unchanged

    def test_tokens_and_check(self):
        st = flat(xs=(10, 40))
        for mv, to in (("MOVE R 3", 13), ("move r 3", 13), ("MOVE L 3", 7), ("Move l 3", 7), ("MOVE +3", 13),
                       ("MOVE -3", 7), ("MOVE R 007", 17)):
            self.assertEqual(play(st, 0, mv)["state"]["tanks"][0][0], to, mv)
        for bad in ("MOVE R 0", "MOVE X 3", "MOVE 3", "MOVE L", "MOVE L -3", "MOVE R 1000", "MOVE R 3 4",
                    "MOVE +", "MOVE -0", "MOVE", "MOVE R x"):
            self.assertIn("error", play(st, 0, bad), bad)
        self.assertIn("MOVE L n", play(st, 0, "MOVE 3")["error"])
        self.assertEqual(play(st, 0, "MOVE R 9", check=True), {"info": "from 10 to 17 used 70 of 72"})
        self.assertFalse(st.get("moved"))
        self.assertIn("MOVE L n", cf.op_apply({"state": st, "seat": 0, "move": ["SHOOT", "1", "2"]})["error"])

    def test_old_states_and_leaving(self):
        """A state saved before tanks could move has 4 moves each; a seat that leaves on its turn after moving
        passes a fresh turn on."""
        st = flat(n=3, xs=(10, 35, 60))
        st.pop("moves")
        st.pop("moved")
        self.assertIn("L 2 4", cf.op_view({"state": st, "seat": 0})["wire"])
        st = play(st, 0, "MOVE R 2")["state"]
        self.assertEqual(st["moves"], [3, 4, 4])
        r = cf.op_drop({"state": st, "seat": 0, "why": "timeout"})
        self.assertEqual(r["events"], ["GONE 0 timeout", "DIE 0", "TURN 1"])
        self.assertFalse(r["state"]["moved"])
        self.assertEqual(play(r["state"], 1, "MOVE L 2")["events"], ["MOVE 1 35 33 20 3"])


def harmless(st, seat):
    """A shot (angle, power) that leaves the arena or bursts well away from every tank."""
    for p in range(100, 0, -5):
        for ang in (5, 175, 20, 160, 35, 145):
            _, impact, _ = cf.fly(st, seat, ang, p)
            if impact is None or all(abs(impact[0] - x) >= 9 for x, _ in st["tanks"]):
                return ang, p
    raise AssertionError("no harmless shot")


@slow
class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port), "--rate", "1000",
                                      "--burst", "1000", "--turn-min", "1", "--sweep", "0.5",
                                      "--auth", "none"],          # sign-in is tested in test_auth and below
                                     cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()

    @staticmethod
    def state_from(view):
        """The test's gunner rebuilds the state it needs from a VUE reply (a real client never simulates)."""
        st = {"w": 0, "h": [], "tanks": [], "wind": 0}
        for l in view:
            f = l.split()
            if f[0] == "ARN":
                st["w"] = int(f[1])
            elif f[0] == "Z":
                st["h"] += [ord(c) - 0x30 for c in f[2]]
            elif f[0] == "K":
                st["tanks"].append([int(f[2]), int(f[3])])
            elif f[0] == "N":
                st["wind"] = int(f[1])
            elif f[0] == "W":
                st["turn"] = int(f[1])
        return st

    async def test_a_whole_game(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO gus")
        await b.req("HELO hal")
        self.assertIn("GAM cannonfire/1 classic Cannon_Fire 2 8 nomoves,drop", await a.req("GAM"))
        self.assertIn("BADOPT", (await a.req("NEW cannonfire classic w=12"))[-1])
        r = await a.req("NEW cannonfire classic seed=2026")
        tid = r[-1].split()[1]
        await a.req("JON %s" % tid)
        await b.req("JON %s" % tid)
        v = await a.req("VUE %s GMT" % tid)
        self.assertEqual(v[1:3], ["ARN 72 63"] + VECTOR_VIEW[:1])
        self.assertIn("S Your shot", v)
        self.assertFalse(any(l.startswith("M ") for l in v))      # nomoves: no move list
        self.assertTrue(all(len(l) <= 78 for l in v))
        self.assertEqual((await a.req("CHK %s FIRE 45 60" % tid))[-1], "OK angle 45 power 60")
        self.assertIn("ILLEGAL", (await a.req("MOV %s FIRE 45 101" % tid))[-1])
        self.assertIn("ILLEGAL", (await b.req("MOV %s FIRE 45 60" % tid))[-1])
        # the recorded first shot
        r = await a.req("MOV %s fire 45 60" % tid)
        self.assertEqual(r[-1].rstrip(), "OK")
        got = [l.split(" ", 3)[3] for l in r if l.startswith("EVT ")]
        self.assertEqual(got[-len(FIRST_SHOT):], FIRST_SHOT)
        # a move: CHK previews it; MOV keeps the turn (no TURN event); a second move is refused; the shot ends it
        # up the crater's side: 14 for a step of 1, then a step of 4 (41 more) is past the 30 asked for
        self.assertEqual((await b.req("CHK %s MOVE L 3" % tid))[-1], "OK from 61 to 60 used 14 of 72")
        self.assertIn("ILLEGAL", (await a.req("MOV %s MOVE L 3" % tid))[-1])
        r = await b.req("MOV %s MOVE L 3" % tid)
        self.assertEqual(r[-1].rstrip(), "OK")
        self.assertEqual([l.split(" ", 3)[3] for l in r if l.startswith("EVT ")][-2:], ["TURN 1", "MOVE 1 61 60 14 3"])
        v = await b.req("VUE %s T" % tid)
        self.assertIn("W 1", v)
        self.assertIn("B 72 1", v)
        self.assertIn("K 1 60 90", v)
        self.assertIn("one move a turn", (await b.req("MOV %s MOVE R 3" % tid))[-1])
        r = await b.req("MOV %s FIRE 5 100" % tid)
        self.assertTrue(r[-2].endswith(" TURN 0"), r)
        # the gunners play it out
        who = {0: a, 1: b}
        end = None
        for _ in range(100):
            v = await a.req("VUE %s G" % tid)
            st = self.state_from(v)
            if st["turn"] < 0:
                break
            seat = st["turn"]
            ang, pw = aim_at(st, seat, st["tanks"][1 - seat][0])
            r = await who[seat].req("MOV %s FIRE %d %d" % (tid, ang, pw))
            self.assertEqual(r[-1].rstrip(), "OK", r)
            for l in r:
                self.assertLessEqual(len(l), 78, l)
                if " END " in l:
                    end = l.split(" ", 3)[3]
            if end:
                break
        self.assertRegex(end or "", r"^END (win [01] \d+-\d+|draw \d+-\d+)$")
        self.assertIn("NOGAME", (await a.req("MOV %s FIRE 45 50" % tid))[-1])
        v = await b.req("VUE %s T" % tid)
        self.assertIn("W -1", v)
        self.assertTrue(any(l.startswith("S Game over") for l in v))

    async def three(self, names, turn="1h"):
        cs = [await Client.connect(self.port) for _ in names]
        for c, n in zip(cs, names):
            self.assertTrue((await c.req("HELO " + n))[-1].startswith("OK"))
        tid = (await cs[0].req("NEW cannonfire classic players=3 seed=31 wind=0 turn=%s" % turn))[-1].split()[1]
        for i, c in enumerate(cs):
            self.assertEqual((await c.req("JON %s" % tid))[-1], "OK %d" % i)
        return cs, tid

    async def events(self, c, tid, since=0, secs=0):
        r = await c.req("WAT %s %d %s" % (tid, since, secs) if secs else "SIN %s %d" % (tid, since))
        return [l.split(" ", 3)[3] for l in r if l.startswith("EVT ")], int(r[-1].split()[1])

    async def test_three_players_one_resigns(self):
        """Three tanks; B resigns on its turn: its tank is a wreck, the turn goes to C, and A and C fire on to
        the end without B."""
        names = ["ida", "jon", "kat"]
        cs, tid = await self.three(names)
        st = self.state_from(await cs[0].req("VUE %s G" % tid))
        r = await cs[0].req("MOV %s FIRE %d %d" % ((tid,) + harmless(st, 0)))
        self.assertEqual(r[-1].rstrip(), "OK", r)
        self.assertTrue(r[-2].endswith(" TURN 1"), r)
        r = await cs[1].req("RES %s" % tid)
        self.assertEqual(r[-1], "OK")
        evs, seq = await self.events(cs[0], tid)
        self.assertEqual(evs[-3:], ["GONE 1 resign", "DIE 1", "TURN 2"])
        self.assertFalse(any(e.startswith("END") for e in evs))
        v = await cs[2].req("VUE %s T" % tid)
        self.assertIn("G 0 1 0", v)
        self.assertIn("S Your shot", v)
        self.assertTrue(any(l.startswith("K 1 ") and l.endswith(" 0") for l in v), v)
        self.assertIn("ERR STATE", (await cs[1].req("MOV %s FIRE 45 50" % tid))[-1])
        self.assertIn("ERR STATE", (await cs[1].req("RES %s" % tid))[-1])
        self.assertEqual(G.describe_event("GONE 1 resign", names), None)
        from adapters import web as W
        self.assertEqual(W.describe("GONE 1 resign", names, G.KEY),
                         "jon resigned and leaves the game; the others play on")
        end = None
        for _ in range(100):
            st = self.state_from(await cs[0].req("VUE %s G" % tid))
            if st["turn"] < 0:
                break
            seat = st["turn"]
            self.assertIn(seat, (0, 2))
            ang, pw = aim_at(st, seat, st["tanks"][2 - seat][0])
            r = await cs[seat].req("MOV %s FIRE %d %d" % (tid, ang, pw))
            self.assertEqual(r[-1].rstrip(), "OK", r)
            self.assertFalse(any(l.endswith(" TURN 1") for l in r), r)
            end = next((l.split(" ", 3)[3] for l in r if " END " in l), None)
            if end:
                break
        self.assertRegex(end or "", r"^END (win [02]|draw) \d+-\d+-\d+$")

    async def test_three_players_one_times_out(self):
        """Three tanks with 2 seconds a turn: A runs out of time and leaves (GONE 0 timeout, its tank a
        wreck); B fires; with two left, C's timeout ends the table."""
        cs, tid = await self.three(["lee", "max", "ned"], "2s")
        got, seq = await self.events(cs[1], tid)
        while not any(e.startswith("GONE") for e in got):
            evs, seq = await self.events(cs[1], tid, seq, 6)
            self.assertTrue(evs, got)
            got += evs
        i = got.index("GONE 0 timeout")
        self.assertEqual(got[i:i + 3], ["GONE 0 timeout", "DIE 0", "TURN 1"])
        self.assertFalse(any(e.startswith("END") for e in got), got)
        st = self.state_from(await cs[1].req("VUE %s G" % tid))
        self.assertEqual(st["tanks"][0][1], 0)
        r = await cs[1].req("MOV %s FIRE %d %d" % ((tid,) + harmless(st, 1)))
        self.assertEqual(r[-1].rstrip(), "OK", r)
        self.assertTrue(any(l.endswith(" TURN 2") for l in r), r)    # the wreck is skipped
        got = []
        while not any(e.startswith("END") for e in got):
            evs, seq = await self.events(cs[1], tid, seq, 6)
            self.assertTrue(evs, got)
            got += evs
        self.assertIn("END timeout 2", got)


class WebShots(Stack):
    """Cannon Fire on the web, signed in with sessions (a password, then the cookie): /move.htm builds FIRE
    angle power from the shot form's two fields (MOVE_FIELDS), each checked strictly; mv= still works;
    Cannon Fire has no /check.htm page; a whole game is played through the web."""

    def setUp(self):
        self.ola, self.pia = self.browser(), self.browser()
        self.ola.login("ola", "ola-Pass-word")
        self.pia.login("pia", "pia-Pass-word")
        self.a = Native(self.core, "ola", "ola-Pass-word")        # the core's own view, for the gunners
        self.addCleanup(self.a.close)

    def table(self, seed):
        """A table made over the protocol (the seed makes the game the recorded one); ola sits down there,
        pia from her browser."""
        tid = self.a.req("NEW cannonfire classic seed=%d" % seed)[-1].split()[1]
        self.assertEqual(self.a.req("JON %s" % tid)[-1], "OK 0")
        self.assertEqual(self.pia.act("/join.htm?t=%s" % tid)[0], 302)
        return tid

    def test_shot_from_two_fields(self):
        tid = self.table(2026)
        seq = self.a.req("SIN %s 999" % tid)
        page = self.ola.get("/table.htm?t=%s" % tid)[3]
        self.assertIn('<input name="angle"', page)
        form = page[page.index('<form action="/move.htm"'):]
        form = form[:form.index("</form>")]
        self.assertIn('<input type="hidden" name="x" value="', form)      # the form carries the action check
        keep = self.ola.keep()
        for bad in ("angle=45&power=1000", "angle=45", "angle=45%0A&power=60", "angle=45&power=6%00",
                    "angle=45&angle=50&power=60", "angle=%E9&power=60", "angle=45&power=" + "6" * 33,
                    "angle=fire&power=60", "angle=45&power=60&dir=left&dist=3", "dir=up&dist=3", "dir=left",
                    "dist=3", "dir=left&dist=-3"):
            st, _, _, body = self.ola.get("/move.htm?t=%s&%s&%s" % (tid, bad, keep))
            self.assertEqual(st, 200, bad)
            self.assertIn("That isn&#39;t a move this page can send.", body, bad)
        self.assertEqual(self.a.req("SIN %s 999" % tid), seq)          # nothing was sent
        body = self.ola.get("/move.htm?t=%s&angle=45&power=181&%s" % (tid, keep))[3]   # the plugin's own limits
        self.assertIn("the power is 0 to 100", body)
        body = self.ola.get("/move.htm?t=%s&angle=45&power=60" % tid)[3]                # no x=: refused
        self.assertIn("That link is out of date.", body)
        self.assertEqual(self.a.req("SIN %s 999" % tid), seq)
        st, _, _, _ = self.ola.get("/move.htm?t=%s&angle=%%2045&power=60&%s" % (tid, keep))  # spaces are trimmed
        self.assertEqual(st, 302)
        evs = [l.split(" ", 3)[3] for l in self.a.req("SIN %s %s" % (tid, seq[-1].split()[1]))
               if l.startswith("EVT ")]
        self.assertEqual(evs[:len(FIRST_SHOT)], FIRST_SHOT)                         # FIRE 0 45 60, as recorded
        body = self.ola.get("/check.htm?t=%s&angle=45&power=60&%s" % (tid, keep))[3]
        self.assertIn("This game has no check on the web.", body)
        body = self.browser().get("/move.htm?t=%s&angle=45&power=60&%s" % (tid, keep))[3]  # x= without the session
        self.assertIn("Sign in to play.", body)
        # pia's turn: the page offers moves before the shot; the move form's two fields make MOVE R 3
        page = self.pia.get("/table.htm?t=%s" % tid)[3]
        self.assertIn("Moves left: 4 of 4", page)
        self.assertIn('<select name="dir">', page)
        self.assertEqual(self.pia.act("/move.htm?t=%s&dir=right&dist=3" % tid)[0], 302)
        evs = [l.split(" ", 3)[3] for l in self.a.req("SIN %s 0" % tid) if l.startswith("EVT ")]
        self.assertTrue(evs[-1].startswith("MOVE 1 61 "), evs[-1])
        page = self.pia.get("/table.htm?t=%s" % tid)[3]                  # still her shot, no more moves this turn
        self.assertIn("you have moved this turn: now fire", page)
        self.assertNotIn('<select name="dir">', page)
        self.assertIn('<input name="angle"', page)
        self.assertEqual(self.pia.act("/move.htm?t=%s&mv=FIRE%%20120%%2055" % tid)[0], 302)     # mv= still works
        evs = [l.split(" ", 3)[3] for l in self.a.req("SIN %s 0" % tid) if l.startswith("EVT ")]
        self.assertIn("FIRE 1 120 55", evs)

    def test_a_whole_game_on_the_web(self):
        tid = self.table(2027)
        browsers = {0: self.ola, 1: self.pia}
        end = None
        for _ in range(100):
            st = OverProtocol.state_from(self.a.req("VUE %s G" % tid))
            if st["turn"] < 0:
                break
            seat = st["turn"]
            ang, pw = aim_at(st, seat, st["tanks"][1 - seat][0])
            b = browsers[seat]
            body = b.get("/table.htm?t=%s&r=0" % tid)[3]
            self.assertIn('<input name="angle"', body)
            st_, loc, _, _ = b.act("/move.htm?t=%s&angle=%d&power=%d" % (tid, ang, pw))
            self.assertEqual(st_, 302)
            end = next((e for e in (l.split(" ", 3)[3] for l in self.a.req("SIN %s 0" % tid)
                                    if l.startswith("EVT ")) if e.startswith("END ")), None)
            if end:
                break
        self.assertRegex(end or "", r"^END (win [01] \d+-\d+|draw \d+-\d+)$")
        for b in browsers.values():
            body = b.get("/table.htm?t=%s" % tid)[3]
            self.assertIn("Game over", body)
            self.assertNotIn("Pass-word", "".join(b.urls))


# ---- the frontends -----------------------------------------------------------------------

def midgame(n=2, seed=12345, shots=1, w=72):
    st = new(n, seed, w=w)
    events = []
    for _ in range(shots):
        seat = st["turn"]
        target = (st["tanks"][seat][0] + st["tanks"][cf.next_seat(st, seat)][0]) // 2    # between: the game goes on
        r = fire(st, seat, *aim_at(st, seat, target))
        events += r["events"]
        st = r["state"]
    return st, events


def crowded():
    """Eight tanks, 200 columns, some destroyed, long names, big scores."""
    st = new(8, 4, w=200)
    st["tanks"][2][1] = 0
    st["tanks"][5][1] = 0
    st["tanks"][6][1] = 3
    st["score"] = [875, 10, 0, 99, 125, 0, 600, 3]
    st["wind"] = -9
    st["hit"] = [st["tanks"][2][0], st["h"][st["tanks"][2][0]]]
    st["turn"] = 7
    return st


NAMES8 = tuple(LONG[:12 - i] + str(i) if i else LONG for i in range(8))


class Web(unittest.TestCase):
    def test_move_words(self):
        for ok in ("FIRE 45 60", "fire 0 100", "Fire 180 0", "FIRE  45 60", "MOVE L 7", "move r 12", "MOVE -3",
                   "MOVE +3"):
            self.assertTrue(G.MOVE_RE.match(ok), ok)
        for bad in ("FIRE 45 60\n", "FIRE 45", "d3", "FIRE 1234 5", "FIRE 45 60 7", "FIRE\r45 60", "45 60",
                    "MOVE L", "MOVE X 3", "MOVE L 1234", "MOVE 3", "MOVE L 3\n", "MOVE - 3", "MOVE L 3 4"):
            self.assertFalse(G.MOVE_RE.match(bad), bad)
        self.assertTrue(G.SEL_RE.match("45-60"))
        self.assertFalse(G.SEL_RE.match("45-60\n"))

    def test_move_from_fields(self):
        from adapters import games
        self.assertEqual(games.problems(G), [])
        self.assertEqual(G.MOVE_FIELDS, ["angle", "power", "dir", "dist"])
        for vals, mv in ((["45", "60"], "FIRE 45 60"), (["007", "100"], "FIRE 7 100"), (["180", "0"], "FIRE 180 0"),
                         (["999", "999"], "FIRE 999 999"),           # out of range: the plugin says so
                         (["", "", "left", "3"], "MOVE L 3"), (["", "", "Right", "07"], "MOVE R 7"),
                         (["", "", "r", "999"], "MOVE R 999")):
            self.assertEqual(G.move_from_fields(vals + [""] * (4 - len(vals))), mv)
            self.assertTrue(G.MOVE_RE.match(mv))
        for vals in (["", "60"], ["45", ""], ["-5", "60"], ["4.5", "60"], ["1000", "5"], ["45 60", "1"],
                     ["45", "6O"], ["４５", "60"], ["45", "60", "left", "3"], ["", "", "up", "3"], ["", "", "left", ""],
                     ["", "", "", "3"], ["", "", "left", "-3"], ["", "", "left", "1000"], ["45", "", "left", "3"]):
            self.assertIsNone(G.move_from_fields(vals + [""] * (4 - len(vals))), vals)

    def test_my_shot(self):
        st, _ = midgame()
        seat = st["turn"]
        html = web_page(self, G, view_lines(st, seat, status="Your shot"), seat)
        ang, pw = st["aim"][seat]
        self.assertIn("mv=FIRE%%20%d%%20%d" % (ang, pw), html)
        self.assertIn('<form action="/move.htm" method="get">', html)
        self.assertIn('Angle <input name="angle" size="3" maxlength="3" value="%d"> power <input name="power" '
                      'size="3" maxlength="3" value="%d">' % (ang, pw), html)
        self.assertNotIn('name="mv"', html)
        self.assertIn('<input type="hidden" name="t" value="3">', html)
        self.assertIn("sel=%d-%d" % (min(180, ang + 10), pw), html)
        self.assertIn("sel=%d-%d" % (ang, min(100, pw + 1)), html)
        self.assertIn(" ismap></a>", html)                    # the landscape: one image, clickable anywhere
        self.assertNotIn("usemap", html)
        c = board_image(self, G, view_lines(st, seat, status="Your shot"), seat)
        self.assertTrue(c.anywhere)
        self.assertEqual((c.w, c.h), (72 * 3, (G.HMAX + 1 + G.TOP) * 3))
        self.assertEqual(sorted(w for w, _, _ in c.words if len(w) == 1), ["A", "B"])
        # a click aims the barrel at the point, keeping the power (sel=angle-power)
        v = G.parse_view(view_lines(st, seat))
        cx, ty = G.tank_at(v, seat)
        for dx, dy, want in ((0, -40, 90), (60, 0, 0), (-60, 0, 180), (40, -40, 45), (-40, -40, 135),
                             (60, 30, 0), (-60, 30, 180)):
            self.assertEqual(G.board_click(v, seat, "", cx + dx, ty + dy), ("/table.htm", {"sel": "%d-%d" % (want, pw)}))
        self.assertEqual(G.board_click(v, seat, "30-77", cx, ty - 40), ("/table.htm", {"sel": "90-77"}))
        self.assertIn("set the angle and power", html)
        # every move the page links to is one /move.htm passes on
        import re
        for mv in re.findall(r"mv=([^&\"]+)", html):
            self.assertTrue(G.MOVE_RE.match(mv.replace("%20", " ")), mv)

    def test_moves_on_the_web(self):
        """Before the shot: Move left and Move right links (the whole budget), the move form, the moves left,
        and the ground within reach marked; a click there drives there, a click elsewhere aims. After the move,
        or with no moves left: none of that, and aiming still open."""
        st = flat(xs=(30, 60))
        lines = view_lines(st, 0, status="Your shot")
        html = web_page(self, G, lines, 0)
        self.assertIn("mv=MOVE%20L%208", html)
        self.assertIn("mv=MOVE%20R%208", html)
        self.assertIn("Move left</a> (to column 23)", html)
        self.assertIn("Move right</a> (to column 37)", html)
        self.assertIn("Moves left: 4 of 4", html)
        self.assertIn("each up to 7.2 columns along the ground", html)
        self.assertIn('<select name="dir">', html)
        self.assertIn('<input name="dist" size="3" maxlength="3" value="8">', html)
        self.assertIn("move if you like", html)
        self.assertIn("the yellow ground to drive there", html)
        self.assertIn("<td><b>Moves</b></td>", html)
        c = board_image(self, G, lines, 0)
        self.assertIn(G.REACH, c.palette)
        self.assertIn(G.HP_FULL, c.palette)                    # the hit point bars
        v = G.parse_view(lines)
        self.assertEqual(sorted(G.reach(v, 0)), [23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 34, 35, 36, 37])
        k, _, h = G.geometry(v)
        ground = h - 1 - 20 * k                                # the top of the ground, in pixels
        for col, mv in ((33, "MOVE R 3"), (37, "MOVE R 7"), (23, "MOVE L 7"), (29, "MOVE L 1")):
            self.assertEqual(G.board_click(v, 0, "", col * k + 1, ground + 2), ("/move.htm", {"mv": mv}))
            self.assertEqual(G.board_click(v, 0, "", col * k + 1, ground - 2 * k), ("/move.htm", {"mv": mv}))
            self.assertTrue(G.MOVE_RE.match(mv))
        for col, y in ((38, ground + 2), (22, ground + 2), (33, ground - 20 * k), (30, ground + 2)):
            self.assertEqual(G.board_click(v, 0, "", col * k + 1, y)[0], "/table.htm", (col, y))   # aims
        # on a slope the click sends the path to the column: 2 steps of 14 are 28 tenths, MOVE R 3
        sv = G.parse_view(view_lines(slope(flat(xs=(30, 60)), 30, 1), 0))
        self.assertEqual(G.move_to(sv, 0, 32), "MOVE R 3")
        self.assertEqual(sorted(G.reach(sv, 0))[-1], 35)
        # the adapter's reach agrees with the plugin on every column of a seeded landscape
        for seed in (1, 2, 3, 2026):
            st2 = new(2, seed)
            v2 = G.parse_view(view_lines(st2, 0))
            for x, used in G.reach(v2, 0).items():
                mv = G.move_to(v2, 0, x)
                self.assertEqual(play(st2, 0, mv)["events"][0].split()[3:5], [str(x), str(used)], (seed, mv))
        # after the move: the same seat's shot, no moves offered
        st = play(st, 0, "MOVE R 3")["state"]
        lines = view_lines(st, 0, status="Your shot")
        html = web_page(self, G, lines, 0)
        self.assertNotIn("mv=MOVE", html)
        self.assertNotIn('<select name="dir">', html)
        self.assertIn("Moves left: 3 of 4</b> (you have moved this turn: now fire)", html)
        self.assertIn('<input name="angle"', html)
        self.assertNotIn(G.REACH, board_image(self, G, lines, 0).palette)
        v = G.parse_view(lines)
        self.assertEqual(G.board_click(v, 0, "", 36 * k + 1, ground + 2)[0], "/table.htm")
        # no moves left
        st = fire(st, 0, 175, 100)["state"]
        st["moves"], st["turn"] = [0, 4], 0
        html = web_page(self, G, view_lines(st, 0, status="Your shot"), 0)
        self.assertIn("no moves left this game", html)
        self.assertNotIn("mv=MOVE", html)
        # the other seat and a watcher see no moves
        for seat in (1, -1):
            html = web_page(self, G, view_lines(flat(xs=(30, 60)), seat), seat)
            self.assertNotIn("mv=MOVE", html)
            self.assertNotIn(G.REACH, board_image(self, G, view_lines(flat(xs=(30, 60)), seat), seat).palette)

    def test_sel_sets_the_aim(self):
        st, _ = midgame()
        seat = st["turn"]
        html = web_page(self, G, view_lines(st, seat), seat, sel="170-95")
        self.assertIn("mv=FIRE%20170%2095", html)
        self.assertIn("sel=180-95", html)                     # 170 + 10, not past 180
        self.assertIn("sel=170-100", html)

    def test_other_seat_and_watcher(self):
        st, _ = midgame()
        for seat in (1 - st["turn"], -1):
            html = web_page(self, G, view_lines(st, seat), seat)
            self.assertNotIn("/move.htm", html)
            self.assertNotIn("<form", html)
            self.assertIn("Wind", html)

    def test_crowded_and_over(self):
        st = crowded()
        html = web_page(self, G, view_lines(st, 7), 7, names=NAMES8)
        self.assertIn("dead", html)
        c = board_image(self, G, view_lines(st, 7), 7)
        self.assertEqual(c.w, 400)                             # 200 columns at 2 pixels
        self.assertEqual(sorted(w for w, _, _ in c.words if len(w) == 1), list("ABCDEFGH"))    # every tank's letter
        self.assertIn(G.WRECK, c.palette)                      # the wrecks
        for s in range(8):
            if st["tanks"][s][1] > 0:
                self.assertIn(G.SEAT_RGB[s], c.palette, s)
        st, end, _ = play_out(new(2, 3))
        html = web_page(self, G, view_lines(st, 0, status="Game over: x"), 0)
        self.assertNotIn("/move.htm", html)

    def test_a_seat_that_left(self):
        st = cf.op_drop({"state": new(3, 8), "seat": 1})["state"]
        v = G.parse_view(view_lines(st, 0))
        self.assertEqual(v["gone"], [False, True, False])
        names = (LONG, "bob", "cy")
        html = web_page(self, G, view_lines(st, 0), 0, names=names)
        self.assertIn('<td align="right">left</td>', html)
        c = board_image(self, G, view_lines(st, 0), 0)
        self.assertIn(G.WRECK, c.palette)                      # its wreck
        self.assertIn(("B", ), [(w,) for w, _, _ in c.words])
        for mode, (text, _) in ansi_screens(self, G, view_lines(st, 0), 0, names=names).items():
            self.assertIn("left", text, mode)
        self.assertIn("left", plain_screen(self, G, view_lines(st, 0), 0, names=names))

    def test_images(self):
        self.assertEqual(G.images(), {})                       # the landscape is drawn whole (board_image)
        st, events = midgame()
        c = board_image(self, G, view_lines(st, 1), 1, events=events)
        self.assertLess(len(c.gif()), 6000)


class Events(unittest.TestCase):
    def test_describe(self):
        names = ["ann", "bob", "-"]
        self.assertEqual(G.describe_event("FIRE 1 45 60", names), "bob (B) fires: angle 45, power 60")
        self.assertEqual(G.describe_event("DMG 0 25", names), "ann is hit: 25 hp left")
        self.assertEqual(G.describe_event("FALL 1 40 7", names), "bob falls with the ground: 7 hp")
        self.assertEqual(G.describe_event("MOVE 0 11 7 52 3", names), "ann (A) moves left 4 columns (3 moves left)")
        self.assertEqual(G.describe_event("MOVE 1 11 12 10 1", names), "bob (B) moves right 1 column (1 move left)")
        self.assertEqual(G.describe_event("DIE 2", names), "tank C (C) is destroyed")
        self.assertEqual(G.describe_event("OUT 71", names), "The shell flies out of the arena")
        for quiet in ("TRJ 1 2 3 4", "HIT 4 5 4", "ZCH 3 AB", "N 4"):
            self.assertEqual(G.describe_event(quiet, names), "")
        for other in ("PASS 0", "END win 0 55-0", "CHT 0 hi", "TURN 1"):
            self.assertIsNone(G.describe_event(other, names))
        from adapters import web as W
        self.assertEqual(W.describe("END win 0 55-0", names, G.KEY), "Game over: ann wins 55-0")


class Screens(unittest.TestCase):
    def test_full_screen_each_seat(self):
        st, events = midgame()
        lines = view_lines(st, st["turn"])

        def prep(g):
            for e in events:
                g.on_event(e)
        for me in (0, 1, -1):
            for mode, (shown, reqs) in ansi_screens(self, G, lines, me, prepare=prep).items():
                self.assertIn("Cannon Fire", shown)
                self.assertIn("Wind", shown)
                self.assertTrue(G.cf_glyphs(mode)["dot"] in shown, mode)       # the last shot's path
                if me == st["turn"]:
                    self.assertIn("[ Fire ]", shown)
                    self.assertIn("Your shot", shown)
                elif me >= 0:
                    self.assertIn("Waiting for", shown)
                else:
                    self.assertIn("You are watching", shown)
                self.assertFalse([r for r in reqs if r.startswith("MOV")])

    def test_keys_aim_and_fire(self):
        st, _ = midgame()
        seat = st["turn"]
        ang, pw = st["aim"][seat]
        lines = view_lines(st, seat)
        cases = [
            (["LEFT", "LEFT", "UP", "ENTER"], (ang + 2, pw + 1)),
            (["RIGHT", "DOWN", "DOWN", " "], (ang - 1, pw - 2)),
            (["<", "WAIT", "+", "WAIT", "ENTER"], (ang + 10, pw + 10)),
            ([">", "WAIT", "-", "WAIT", "ENTER"], (ang - 10, pw - 10)),
            (["HOME", "PGDN", "ENTER"], (ang + 10, pw - 10)),
            (["/"] + list("30 70") + ["ENTER"], (30, 70)),
            (["/"] + list("fire 120 99") + ["ENTER"], (120, 99)),
            ([("CLICK", 43, 1)], (ang, pw)),                    # the [ Fire ] button
            ([("CLICK", 1, 23)], None),                         # the status bar's Arrows item: nothing
        ]
        for keys, shot in cases:
            typed = "".join(k for k in keys[1:-1]) if keys[0] == "/" else None
            for mode, (shown, reqs) in ansi_screens(self, G, lines, seat, keys=keys).items():
                if typed:                               # a typed shot runs on the server (CMD)
                    self.assertIn("CMD 3 /" + typed, reqs, (keys, mode))
                    continue
                movs = [r for r in reqs if r.startswith("MOV")]
                want = [] if shot is None else ["MOV 3 FIRE %d %d" % (min(180, max(0, shot[0])),
                                                                      min(100, max(0, shot[1])))]
                self.assertEqual(movs, want, (keys, mode))

    def test_click_the_sky_aims(self):
        st = flat(xs=(10, 60))
        lines = view_lines(st, 0)
        # 72 columns from screen column 4: tank A is at screen column 14, row 9 (ground 20 is 8 half rows)
        for target, lo, hi in (((14, 4), 90, 90), ((15, 3), 80, 89), ((40, 9), 0, 10), ((4, 9), 160, 180)):
            keys = [("CLICK", target[0], target[1]), "ENTER"]
            for mode, (_, reqs) in ansi_screens(self, G, lines, 0, keys=keys).items():
                movs = [r for r in reqs if r.startswith("MOV")]
                self.assertEqual(len(movs), 1)
                ang, pw = int(movs[0].split()[3]), int(movs[0].split()[4])
                self.assertTrue(lo <= ang <= hi, (target, movs))
                self.assertEqual(pw, 50)                     # a click aims; the power stays

    def test_keys_move(self):
        """[ and ] drive as far as the tank can; a typed /drive left 5 or /drive right; the [<] and [>] buttons; a click
        on the ground within reach (or the cell above it) drives there. Only before the shot, on your turn."""
        st = flat(xs=(10, 60))
        lines = view_lines(st, 0)
        cases = [
            (["[", "WAIT"], "MOVE L 8"),
            (["]", "WAIT"], "MOVE R 8"),
            (["/"] + list("drive left 5") + ["ENTER"], "MOVE L 5"),
            (["/"] + list("drive left 2") + ["ENTER"], "MOVE L 2"),
            (["/"] + list("drive right") + ["ENTER"], "MOVE R 8"),
            ([("CLICK", 52, 1)], "MOVE L 8"),                    # [<]
            ([("CLICK", 58, 1)], "MOVE R 8"),                    # [>]
            ([("CLICK", 4 + 15, 10)], "MOVE R 5"),               # the ground at column 15
            ([("CLICK", 4 + 15, 9)], "MOVE R 5"),                # the cell above it
            ([("CLICK", 4 + 6, 10)], "MOVE L 4"),
        ]
        for keys, mv in cases:
            typed = "".join(k for k in keys[1:-1]) if keys[0] == "/" else None
            for mode, (shown, reqs) in ansi_screens(self, G, lines, 0, keys=keys).items():
                if typed:                               # a typed drive runs on the server (CMD)
                    self.assertIn("CMD 3 /" + typed, reqs, (keys, mode))
                else:
                    self.assertEqual([r for r in reqs if r.startswith("MOV")], ["MOV 3 " + mv], (keys, mode))
                self.assertIn("[ and ] drive first.", shown)
                self.assertIn("[<] 4 [>]", shown)
        # out of reach on the ground, or in the sky: a click aims (and Enter fires)
        for target in ((4 + 30, 10), (4 + 15, 4)):
            for mode, (_, reqs) in ansi_screens(self, G, lines, 0, keys=[("CLICK",) + target, "ENTER"]).items():
                movs = [r for r in reqs if r.startswith("MOV")]
                self.assertEqual(len(movs), 1)
                self.assertTrue(movs[0].startswith("MOV 3 FIRE "), movs)
        # after a move this turn, and on another seat's turn: no move is sent
        moved = view_lines(play(st, 0, "MOVE R 2")["state"], 0)
        for lines_, me in ((moved, 0), (view_lines(st, 1), 1), (view_lines(st, -1), -1)):
            for mode, (shown, reqs) in ansi_screens(self, G, lines_, me, keys=["[", "WAIT", "]", "WAIT",
                                                                                ("CLICK", 4 + 15, 10)]).items():
                self.assertFalse([r for r in reqs if r.startswith("MOV") and "MOVE" in r], (me, mode))
                self.assertNotIn("[<]", shown)
        for mode, (shown, _) in ansi_screens(self, G, moved, 0, keys=["[", "WAIT"]).items():
            self.assertIn("You have driven this turn, so now you fire.", shown)

    def test_hit_points_and_moves_shown(self):
        st = flat(n=3, xs=(10, 35, 60))
        st["tanks"][1][1] = 30
        st["tanks"][2][1] = 0
        st["moves"] = [4, 1, 2]
        lines = view_lines(st, 0)
        self.assertEqual((G.hp_bar(100), G.hp_bar(30), G.hp_bar(1), G.hp_bar(0)), ("#####", "##...", "#....", "....."))
        for mode, (shown, _) in ansi_screens(self, G, lines, 0).items():
            full, dot = G.cf_glyphs(mode)["full"], G.cf_glyphs(mode)["dot"]
            self.assertIn(full * 5 + " 100 hp 4mv", shown, mode)
            self.assertIn(full * 2 + dot * 3 + "  30 hp 1mv", shown, mode)
            self.assertIn("dead", shown)
        text = plain_screen(self, G, lines, 0)
        self.assertIn("##### 100 hp 4mv", text)
        self.assertIn("##...  30 hp 1mv", text)
        flat_text = " ".join(text.split())
        self.assertIn("You may drive first with /drive left or /drive right, up to 7.2 columns of path.", flat_text)
        self.assertIn("You can reach columns 3 to 17, and you stand at 10.", flat_text)
        self.assertIn(", or /drive left or /drive right. >", text)
        moved = plain_screen(self, G, view_lines(play(st, 0, "MOVE R 2")["state"], 0), 0)
        self.assertIn("You have driven this turn, so now you fire.", " ".join(moved.split()))
        self.assertNotIn("/drive", moved)
        self.assertIn("4mv", plain_screen(self, G, lines, 1))

    def test_burst_and_path(self):
        st = flat(xs=(10, 60))
        r = fire(st, 0, *aim_at(st, 0, 35))
        st = r["state"]
        self.assertEqual(st["hit"][0], 35)
        lines = view_lines(st, 1)

        def prep(g):
            for e in r["events"]:
                g.on_event(e)
        for mode, (shown, _) in ansi_screens(self, G, lines, 1, prepare=prep).items():
            land = shown.split("\n")[2:14]
            self.assertEqual(land[11 - G.level(st["hit"][1]) // 2][4 + 35], "*", mode)
            self.assertGreater(sum(l.count(G.cf_glyphs(mode)["dot"]) for l in land), 6)
        web_page(self, G, lines, 1, events=r["events"])
        c = board_image(self, G, lines, 1, events=r["events"])
        self.assertIn(G.BURST, c.palette)                      # the ring at the burst
        self.assertIn(G.TRAIL, c.palette)                      # the dotted path
        self.assertIn(G.FRESH, c.palette)                      # the ground the crater dug
        px, py = G.to_px(G.parse_view(lines), *st["hit"])
        self.assertIn(c.get(int(px) + 5, int(py)), (G.BURST, (0, 0, 0)))
        self.assertIn("*", plain_screen(self, G, lines, 1))

    def test_not_my_turn_sends_nothing(self):
        st, _ = midgame()
        other = 1 - st["turn"]
        for me in (other, -1):
            for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, me), me,
                                                     keys=["LEFT", "ENTER", ("CLICK", 43, 1)]).items():
                self.assertFalse([r for r in reqs if r.startswith("MOV")])

    def test_crowded_screen(self):
        st = crowded()
        lines = view_lines(st, 7)
        for me in (7, 2, -1):
            for mode, (shown, _) in ansi_screens(self, G, lines, me, names=NAMES8, keys=["?", "WAIT", "ESC"]).items():
                for s in range(8):
                    self.assertIn(G.letter(s), shown)
                self.assertIn(NAMES8[7], shown)
                self.assertIn("Wind", shown)
        text = plain_screen(self, G, lines, 7, names=NAMES8)
        self.assertIn("The wind is 9 to the left.", text)
        self.assertIn("---", text)                             # the destroyed tanks

    def test_narrow_arena(self):
        st = new(2, 8, w=40)
        for mode, (shown, _) in ansi_screens(self, G, view_lines(st, 0), 0).items():
            self.assertIn("A", shown)

    def test_game_over(self):
        st, end, events = play_out(new(2, 3))
        lines = view_lines(st, 0, status="Game over")

        def prep(g):
            for e in events[-12:]:
                g.on_event(e)
            g.over = "Game over: %s wins" % LONG
        for me in (0, 1, -1):
            for mode, (shown, reqs) in ansi_screens(self, G, lines, me, keys=["ENTER"], prepare=prep).items():
                self.assertIn("Game over", shown)
                self.assertNotIn("[ Fire ]", shown)
                self.assertFalse([r for r in reqs if r.startswith("MOV")])
            text = plain_screen(self, G, lines, me, prepare=prep)
            self.assertIn("Game over", text)

    def test_plain(self):
        st, events = midgame()
        seat = st["turn"]
        text = plain_screen(self, G, view_lines(st, seat), seat)
        self.assertIn("Your shot. Type /fire", text)
        self.assertIn("You are abcdefghijkl.", text)
        self.assertIn(" 0         10        20", text)
        self.assertEqual(text.count("\n |"), 12)
        watch = plain_screen(self, G, view_lines(st, -1), -1)
        self.assertIn("You are watching.", watch)
        self.assertTrue(watch.endswith("> "))
        g = G.PlainScreen.__new__(G.PlainScreen)
        self.assertEqual(G.CannonBase.move_word(g, "45", ["45", "60"]), "FIRE 45 60")
        self.assertEqual(G.CannonBase.move_word(g, "fire", ["FIRE", "7", "100"]), "FIRE 7 100")
        self.assertIsNone(G.CannonBase.move_word(g, "d3", ["d3"]))
        self.assertIsNone(G.CannonBase.move_word(g, "45", ["45", "x"]))

    def test_waiting(self):
        lines = ["V cannonfire/1 1", "S Waiting for players"]
        self.assertFalse(G.parse_view(lines)["rows"])
        for mode, (shown, _) in ansi_screens(self, G, lines, 0).items():
            self.assertIn("Waiting for a second player", shown)
        self.assertIn("Waiting for a second player", plain_screen(self, G, lines, 0))


if __name__ == "__main__":
    unittest.main()
