"""The game matrix (tests/test_matrix.py) over the period clients' protocols, for the games they play:
IOS (Reversi), FIBS and Outland (Backgammon). Each starts a table from its own client, the other player
joins, both make at least two real moves (from the core's legal-move list, or the backgammon/1 rules the
real clients work out themselves), a native watcher looks on and leaves (not at FIBS, whose invitations make private tables), and a
resignation ends it, seen
by both clients and by the core."""
import time
import unittest

import test_fibs as TF
import test_ios as TI
import test_outland as TO
from test_adapters import spawn
from test_core import adapter_args, free_port, make_keys, stack_args
from test_matrix import Conn, table, until

from adapters import outland as O  # noqa: E402
from adapters.games import backgammon as BG  # noqa: E402
from adapters.games import reversi as RV  # noqa: E402
from speed import slow  # noqa: E402


def view(conn, module, tid):
    lines = conn.req("VUE %s GMT" % tid)[:-1]
    return lines, module.parse_view(lines)


def watch_and_leave(tc, watcher, tid):
    t = table(watcher, tid)
    tc.assertEqual(t["status"], "playing")
    tc.assertTrue(watcher.req("SEE %s" % tid)[-1].startswith("OK"))
    tc.assertTrue(watcher.req("VUE %s" % tid)[-1].startswith("OK"))
    return lambda: tc.assertTrue(watcher.req("LVE %s" % tid)[-1].startswith("OK"))


def ended(conn, tid):
    return [l.split(" ", 3)[3] for l in conn.req("SIN %s 0" % tid) if l.startswith("EVT ")][-1]


@slow
class Ios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.ios = free_port(), free_port(), free_port()
        cls.keys = make_keys("ios")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "none"),
                     spawn(*adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios"), "--prompt-wait", "2")]

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()

    def test_reversi(self):
        clients = {}
        for name in ("mxpia", "mxquin"):
            c = TI.Eclipse(self.ios)
            self.addCleanup(c.close)
            self.assertTrue(c.login(name)[0])
            c.who()
            clients[name] = c
        pia, quin = clients["mxpia"], clients["mxquin"]
        pia.send("match mxquin 5 0 0 white")                 # a new table, from the IOS client
        quin.wait(lambda l: l.startswith("<ios> MATCH"))
        quin.send("accept mxpia")                            # the other player joins it
        oracles = {n: Conn(self.core, n) for n in clients}
        for o in oracles.values():
            self.addCleanup(o.close)
        t = until(lambda: next((t for t in TI_tables(oracles["mxpia"]) if t["status"] == "playing"), None),
                  "the IOS match's table")
        tid, seat_of = t["id"], t["seat_of"]
        watcher = Conn(self.core, "mxwatch")
        self.addCleanup(watcher.close)
        leave = watch_and_leave(self, watcher, tid)
        moves = {0: 0, 1: 0}
        k = 0
        while min(moves.values()) < 2:
            mover = next(n for n in clients if view(oracles[n], RV, tid)[1]["turn"] == seat_of[n])
            _, v = view(oracles[mover], RV, tid)
            clients[mover].send("PM %s +0.00     1.50" % v["moves"][0].upper())     # a legal square
            k += 1
            for c in clients.values():                       # both boards show it: the move arrived
                c.pb(lambda p, k=k: p["move_no"] == k)
            moves[seat_of[mover]] += 1
        leave()
        pia.send("resign")
        for c in clients.values():
            c.game(True)
        self.assertTrue(ended(oracles["mxquin"], tid).startswith("END resign"))
        self.assertIn("S Game over", "\n".join(oracles["mxpia"].req("VUE %s T" % tid)))


def TI_tables(conn):
    from adapters.common import parse_tables
    return parse_tables(conn.req("LST"))


def full_move(v, seat):
    """A whole backgammon move from the view, by the backgammon/1 rules: [(from, to)] in the core's numbering."""
    bg = TF.bg
    st = {"pts": list(v["pts"]), "bar": list(v["bar"]), "off": list(v["off"]), "turn": seat,
          "dice": list(v["dice"]), "steps": []}
    out = []
    while True:
        legal = bg.legal_steps(st)
        if not legal:
            return out
        (f, t), d = sorted(legal.items(), key=lambda e: str(e[0]))[0]
        nb, _ = bg.apply_step(bg.board_of(st), seat, f, t)
        st["pts"], st["bar"], st["off"] = nb["pts"], nb["bar"], nb["off"]
        st["dice"].remove(d)
        st["steps"].append(1)
        out.append((f, t))


@slow
class Fibs(TF.Stack):
    def test_backgammon(self):
        x, w = self.client(TF.FibsW), self.client(TF.FibsW)
        x.login("mxxav", self.password_for("mxxav"))
        w.login("mxwil", self.password_for("mxwil"))
        x.send("invite mxwil 1")                             # a new table, from a FIBS client
        w.wait("mxxav wants")
        w.send("join mxxav")                                 # the other player joins it
        x.wait("** Player mxwil has joined you")
        oracles = {"mxxav": TF.Native(self.core, "mxxav", "mxxav-Pass-word"),
                   "mxwil": TF.Native(self.core, "mxwil", "mxwil-Pass-word")}
        for o in oracles.values():
            self.addCleanup(o.close)
        t = until(lambda: next((t for t in TI_tables(oracles["mxxav"]) if t["status"] == "playing"), None),
                  "the FIBS match's table")
        tid, seat_of = t["id"], t["seat_of"]
        clients = {"mxxav": x, "mxwil": w}
        self.assertEqual(t["vis"], "private")               # an invitation's table: nobody else may watch
        turns = {0: 0, 1: 0}
        end = time.time() + 60
        while min(turns.values()) < 2:
            self.assertLess(time.time(), end, turns)
            for c in clients.values():
                c.queue += c.lines(0.02)                     # read what the clients were sent
            _, v = view(oracles["mxxav"], BG, tid)
            mover = next((n for n in clients if v["turn"] == seat_of[n]), None)
            if mover is None:
                continue
            seat = seat_of[mover]
            _, v = view(oracles[mover], BG, tid)
            if v["phase"] == "roll":
                clients[mover].send("roll")
            elif v["phase"] == "move":
                plan = full_move(v, seat)
                if not plan:
                    continue                                 # the server passes a player who cannot move
                clients[mover].send("move " + " ".join("%s-%s" % s for s in plan))
            else:
                self.fail("unexpected phase %r" % v["phase"])
            until(lambda: view(oracles[mover], BG, tid)[1]["seq"] != v["seq"], "%s's %s" % (mover, v["phase"]))
            if view(oracles[mover], BG, tid)[1]["turn"] != seat:
                turns[seat] += 1
        x.send("resign n")
        w.wait("mxxav wants to resign")
        w.send("accept")
        w.wait("You accept and win")
        until(lambda: ended(oracles["mxxav"], tid).startswith("END"), "the end")


@slow
class Outland(TO.LiveBase):
    def test_backgammon(self):
        req = self.native("mxwes")
        tid = req("NEW backgammon standard turn=1h")[-1].split()[1]
        self.assertEqual(req("JON %s" % tid)[-1], "OK 0")
        g = self.gateway("mxgus", "anything")
        g.open_backgammon()
        players = g.lobby()
        ch = O.record(O.C_CHALLENGE)                         # the Outland player sits at the open table
        ch[2] = players["mxwes"][0]
        TO.struct.pack_into(">H", ch, 4, 1)
        g.send_rec(ch)
        resp = g.rec(O.S_RESPONSE)
        team = resp[7]
        g.send_rec(O.record(O.C_START, team))
        opening = g.rec(O.S_ROLL)
        d1, d2 = opening[O.O_DIE1], opening[O.O_DIE2]
        watcher = Conn(self.core, "mxwatch")
        self.addCleanup(watcher.close)
        leave = watch_and_leave(self, watcher, tid)
        board = TO.img_state(O.board_image(O.Board(), 0))
        mine = d1 > d2
        turns = {0: 0, 1: 0}
        while min(turns.values()) < 2:
            if mine:
                new, _ = self.client_turn(g, team, board, d1, d2)
                until(lambda: self.view(req, tid)["turn"] != team, "the Outland player's turn")
                v = self.view(req, tid)
                self.assertEqual((v["pts"], list(v["bar"]), list(v["off"])), (new["pts"], new["bar"], new["off"]))
                turns[team] += 1
            else:
                self.web_turn_native(req, tid, 1 - team)
                go = g.rec(O.S_GO)                          # the native player's move arrives
                board = TO.img_state(go[O.O_IMG:O.O_IMG + 162])
                d1, d2 = go[O.O_DIE1], go[O.O_DIE2]
                turns[1 - team] += 1
            mine = not mine
        leave()
        res = O.record(O.C_RESIGN)
        TO.struct.pack_into(">H", res, 2, 1)
        g.send_rec(res)
        self.assertEqual(TO.struct.unpack_from(">H", g.rec(O.S_RESIGNRET), 2)[0], 1)
        self.assertEqual(ended(Conn(self.core, "mxwes"), tid), "END resign %d" % team)

    def web_turn_native(self, req, tid, seat):
        """The native player's whole turn from the core's move list: roll, steps, END."""
        for _ in range(12):
            v = self.view(req, tid)
            if v["turn"] != seat:
                return
            if v["phase"] == "roll":
                r = req("MOV %s roll" % tid)
            elif v["moves"]:
                r = req("MOV %s %s" % (tid, v["moves"][0]))
            else:
                r = req("END %s" % tid)
            self.assertTrue(r[-1].startswith("OK"), r)
        raise AssertionError("the native player's turn did not end")
