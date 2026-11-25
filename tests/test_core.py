"""Plugin and core tests: python3 -m unittest discover -s tests"""
import asyncio
import os
import socket
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "reversi"))
import plugin as rv  # noqa: E402


def new(n=8):
    return rv.op_new({"players": 2, "options": {"size": str(n)}})["state"]


class ReversiRules(unittest.TestCase):
    def test_opening_moves(self):
        m = rv.op_moves({"state": new(), "seat": 0})["moves"]
        self.assertEqual(sorted(m), ["c4", "d3", "e6", "f5"])
        self.assertEqual(rv.op_moves({"state": new(), "seat": 1})["moves"], [])

    def test_first_move_flips(self):
        r = rv.op_apply({"state": new(), "seat": 0, "move": ["d3"]})
        self.assertEqual(r["events"][0], "PUT 0 d3 FLP d4")
        self.assertEqual(r["events"][1], "TURN 1")
        self.assertEqual(rv.counts(r["state"]), (4, 1))

    def test_rejects(self):
        st = new()
        self.assertIn("error", rv.op_apply({"state": st, "seat": 1, "move": ["d3"]}))
        self.assertIn("error", rv.op_apply({"state": st, "seat": 0, "move": ["a1"]}))
        self.assertIn("error", rv.op_apply({"state": st, "seat": 0, "move": ["z9"]}))
        self.assertIn("error", rv.op_apply({"state": st, "seat": 0, "move": ["d3", "d4"]}))

    def test_sizes(self):
        self.assertIn("error", rv.op_new({"players": 2, "options": {"size": "7"}}))
        self.assertEqual(rv.counts(new(4)), (2, 2))

    def test_full_random_games_terminate(self):
        import random
        for seed in range(30):
            rnd = random.Random(seed)
            st, seat, plies = new(), 0, 0
            while not st["over"]:
                mv = rv.op_moves({"state": st, "seat": st["turn"]})["moves"]
                r = rv.op_apply({"state": st, "seat": st["turn"], "move": [rnd.choice(mv)]})
                st = r["state"]
                plies += 1
                self.assertLess(plies, 70)
            self.assertIn("end", r)


def make_keys(*names, role="frontend"):
    """A temporary frontend key directory with a key for each name."""
    import atexit
    import shutil
    import tempfile
    sys.path.insert(0, ROOT)
    from parlord import frontends
    d = tempfile.mkdtemp(prefix="pnkeys")
    atexit.register(shutil.rmtree, d, True)
    for n in names:
        frontends.write_key(d, n, role)
    return d


def stack_args(core, fe, keys):
    """parlord's arguments for a core with a frontend channel."""
    return ["parlord", "--port", str(core), "--frontend-port", str(fe), "--keys", keys,
            "--rate", "1000", "--burst", "1000"]


def adapter_args(module, port, fe, keys, name):
    return [module, "--port", str(port), "--core", "127.0.0.1:%d" % fe, "--key", os.path.join(keys, name + ".key")]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    def __init__(self, r, w):
        self.r, self.w = r, w

    @classmethod
    async def connect(cls, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"PARLORNET/1\r\n")       # the opening first (CR LF), then the greeting
        c = cls(r, w)
        c.greeting = await c.line()
        return c

    async def line(self):
        return (await asyncio.wait_for(self.r.readline(), 5)).decode().rstrip("\r\n")

    async def req(self, text):
        self.w.write((text + "\r\n").encode())
        out = []
        while True:
            l = await self.line()
            out.append(l)
            if l.startswith("OK") or l.startswith("ERR") or l.startswith("ASK"):
                return out


class Integration(unittest.IsolatedAsyncioTestCase):
    extra = ["--rate", "1000", "--burst", "1000", "--auth", "none"]      # sign-in is tested in test_auth

    async def asyncSetUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port)] + self.extra,
                                     cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()  # banner: server is listening

    async def asyncTearDown(self):
        self.proc.kill()
        self.proc.wait()

    async def test_game_to_the_end(self):
        a = await Client.connect(self.port)
        b = await Client.connect(self.port)
        self.assertTrue(a.greeting.startswith("PARLORNET/1 "))
        self.assertEqual((await a.req("VUE 1"))[0][:3], "ERR")  # before HELO
        self.assertIn("GAM reversi/1 standard Reversi 2 2", await a.req("GAM"))  # allowed before HELO
        ra = (await a.req("HELO alice"))[0]
        rb = (await b.req("HELO bob"))[0]
        self.assertTrue(ra.startswith("OK ") and rb.startswith("OK "))
        self.assertEqual((await a.req("HELO alice"))[0][:3], "ERR")
        c = await Client.connect(self.port)
        self.assertEqual((await c.req("HELO alice"))[0], "OK -")      # public port: no session id
        gam = await a.req("GAM")
        self.assertIn("GAM reversi/1 standard Reversi 2 2", gam)
        self.assertEqual((await a.req("NEW reversi standard"))[0], "OK 1")
        self.assertIn("SYNTAX", (await a.req("JON 1 7"))[0])
        self.assertEqual((await a.req("JON 1 0"))[0], "OK 0")
        lst = await b.req("LST")
        self.assertIn("TAB 1 reversi/1 standard 1/2 open alice,- turn=86400", lst[0])
        self.assertEqual((await b.req("JON 1"))[0], "OK 1")
        v = await a.req("VUE 1 GMT")
        self.assertEqual(v[0], "V reversi/1 4")
        self.assertIn("BRD 8 8", v)
        self.assertIn("M d3", v)
        self.assertIn("S Your move", v)
        self.assertEqual((await b.req("MOV 1 d3"))[0][:3], "ERR")  # not bob's turn
        ended = False
        for _ in range(80):
            played = False
            for cl in (a, b):
                v = await cl.req("VUE 1 M")
                moves = [l[2:] for l in v if l.startswith("M ")]
                if not moves:
                    continue
                r = await cl.req("MOV 1 " + moves[0])
                self.assertTrue(r[-1].startswith("OK"), r)
                played = True
                ends = [l for l in r if " END " in l]
                if ends:
                    self.assertRegex(ends[0], r"END (win [01] \d+-\d+|draw \d+-\d+)")
                    ended = True
                break
            if ended or not played:
                break
        self.assertTrue(ended)
        sin = await a.req("SIN 1 0")
        self.assertTrue(any(" END " in l for l in sin))
        self.assertIn("NOGAME", (await b.req("MOV 1 d3"))[0])

    async def test_limits_and_errors(self):
        a = await Client.connect(self.port)
        await a.req("HELO zed")
        self.assertIn("UNKNOWN", (await a.req("FROB"))[0])
        self.assertIn("NOTABLE", (await a.req("VUE 99"))[0])
        c = await Client.connect(self.port)
        self.assertIn("NOTFRONTEND", (await (await Client.connect(self.port)).req("SES " + "A" * 22))[0])
        self.assertIn("NOGAME", (await a.req("NEW chess standard"))[0])
        self.assertIn("BADOPT", (await a.req("NEW reversi standard size=7"))[0])
        self.assertIn("BADOPT", (await a.req("NEW reversi standard players=3"))[0])   # reversi is 2 to 2
        a.w.write(b"X" * 200 + b"\r\n")
        self.assertIn("TOOLONG", await a.line())

    async def test_private_tables_and_invitations(self):
        a, b, c = [await Client.connect(self.port) for _ in range(3)]
        await a.req("HELO amy")
        await b.req("HELO bo")
        await c.req("HELO cy")
        self.assertIn("BADOPT", (await a.req("NEW reversi standard vis=secret"))[0])
        self.assertEqual((await a.req("NEW reversi standard vis=private"))[0], "OK 1")
        self.assertIn("NOTSEATED", (await a.req("INV 1 bo"))[0])     # invite from a seat
        self.assertEqual((await a.req("JON 1"))[0], "OK 0")
        # outsiders do not see a private table, cannot sit or watch
        self.assertEqual(await b.req("LST"), ["OK"])
        self.assertIn("ERR PRIVATE", (await b.req("JON 1"))[0])
        self.assertIn("ERR PRIVATE", (await b.req("SEE 1"))[0])
        lst = await a.req("LST")
        self.assertEqual(lst[0], "TAB 1 reversi/1 standard 1/2 open amy,- turn=86400 vis=private")
        self.assertEqual(await b.req("IVL"), ["OK"])
        # errors
        self.assertIn("SYNTAX", (await a.req("INV 1"))[0])
        self.assertIn("SYNTAX", (await a.req("INV 1 bo 2"))[0])
        self.assertIn("NOPLAYER", (await a.req("INV 1 nobody"))[0])
        self.assertIn("STATE", (await a.req("INV 1 amy"))[0])
        self.assertIn("TAKEN", (await a.req("INV 1 bo 0"))[0])
        self.assertIn("NOTABLE", (await a.req("INV 9 bo"))[0])
        self.assertIn("NOINVITE", (await a.req("UNV 1 cy"))[0])
        # invite, see it, withdraw, invite again
        self.assertEqual((await a.req("INV 1 BO"))[0], "OK")
        self.assertEqual(await b.req("IVL"), ["IVT 1 reversi/1 standard amy open", "OK"])
        self.assertEqual((await b.req("LST"))[0], "TAB 1 reversi/1 standard 1/2 open amy,- turn=86400 vis=private")
        self.assertEqual(await c.req("LST"), ["OK"])
        self.assertIn("NOTSEATED", (await c.req("UNV 1 bo"))[0])     # only seated players withdraw
        self.assertEqual((await a.req("UNV 1 bo"))[0], "OK")
        self.assertEqual(await b.req("IVL"), ["OK"])
        self.assertIn("ERR PRIVATE", (await b.req("JON 1"))[0])
        self.assertEqual((await a.req("INV 1 bo 1"))[0], "OK")
        self.assertEqual((await a.req("INV 1 cy"))[0], "OK")
        self.assertEqual(await b.req("IVL"), ["IVT 1 reversi/1 standard amy open seat=1", "OK"])
        self.assertIn("TAKEN", (await c.req("JON 1"))[0])            # the only free seat is held for bo
        self.assertEqual((await c.req("UNV 1 cy"))[0], "OK")         # cy declines
        self.assertEqual(await c.req("LST"), ["OK"])
        sin = await a.req("SIN 1 0")
        kinds = [l.split(" ", 3)[3] for l in sin if l.startswith("EVT")]
        self.assertEqual(kinds, ["JOIN 0 amy", "INVITE - bo", "UNINVITE - bo withdrawn", "INVITE 1 bo", "INVITE - cy",
                                 "UNINVITE - cy declined"])
        self.assertEqual((await b.req("JON 1"))[0], "OK 1")          # used up
        self.assertEqual(await b.req("IVL"), ["OK"])
        self.assertIn("CLOSED", (await a.req("INV 1 cy"))[0])        # the game has started

    async def test_public_table_held_seats(self):
        a, b, c = [await Client.connect(self.port) for _ in range(3)]
        await a.req("HELO ida")
        await b.req("HELO jo")
        await c.req("HELO kim")
        await a.req("NEW reversi standard")
        self.assertEqual((await a.req("JON 1 1"))[0], "OK 1")
        self.assertEqual((await a.req("INV 1 jo 0"))[0], "OK")
        lst = await c.req("LST")
        self.assertEqual(lst[0], "TAB 1 reversi/1 standard 1/2 open -,ida turn=86400")   # public: no vis field
        self.assertEqual((await c.req("SEE 1"))[0], "OK")
        self.assertIn("TAKEN", (await c.req("JON 1"))[0])
        self.assertIn("TAKEN", (await c.req("JON 1 0"))[0])
        self.assertEqual((await a.req("INV 1 kim"))[0], "OK")       # no seat held for kim
        self.assertIn("TAKEN", (await c.req("JON 1"))[0])            # still only seat 0, held for jo
        self.assertEqual((await b.req("JON 1"))[0], "OK 0")          # jo gets the held seat
        sin = await a.req("SIN 1 0")
        kinds = [l.split(" ", 3)[3] for l in sin if l.startswith("EVT")]
        self.assertEqual(kinds[:5], ["JOIN 1 ida", "INVITE 0 jo", "INVITE - kim", "JOIN 0 jo", "UNINVITE - kim started"])
        self.assertEqual(kinds[5], "START")
        self.assertEqual(await c.req("IVL"), ["OK"])


class Persistence(unittest.IsolatedAsyncioTestCase):
    def start(self):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.port), "--db", self.db,
                                      "--rate", "1000", "--burst", "1000", "--turn-min", "1", "--sweep", "0.5",
                                      "--frontend-port", "0", "--auth", "none"],
                                     cwd=ROOT, stdout=subprocess.PIPE)
        self.proc.stdout.readline()

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        self.proc.stdout.close()

    async def asyncSetUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "parlord.db")
        self.start()

    async def asyncTearDown(self):
        self.stop()
        self.dir.cleanup()

    async def test_leave_keeps_game_restart_keeps_everything(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        ta = (await a.req("HELO ann"))[0].split()[1]
        tb = (await b.req("HELO ben"))[0].split()[1]
        await a.req("NEW reversi standard turn=1h")
        await a.req("JON 1")
        await b.req("JON 1")
        await a.req("MOV 1 d3")
        self.assertEqual((await b.req("LVE 1"))[0], "OK")      # leaving does not resign
        lst = await a.req("LST")
        self.assertIn("playing ann,ben turn=3600", lst[0])
        self.stop()
        self.start()                                            # restart: all comes back from SQLite
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        self.assertEqual((await a.req("HELO ann"))[0], "OK -")
        self.assertEqual((await b.req("HELO ben"))[0], "OK -")
        v = await b.req("VUE 1 M")
        self.assertIn("R 3 ...0....", v)
        self.assertTrue(any(l.startswith("CLK 1 ") and l.endswith(" 3600") for l in v), v)
        self.assertIn("M c3", v)
        sin = await a.req("SIN 1 0")
        self.assertTrue(any(" PUT 0 d3 FLP d4" in l for l in sin))
        r = await b.req("MOV 1 c3")
        self.assertTrue(r[-1].startswith("OK"), r)
        self.assertEqual((await a.req("NEW reversi standard"))[0], "OK 2")   # ids continue
        r = await b.req("RES 1")
        self.assertTrue(any("END resign 1" in l for l in r), r)
        self.assertIn("NOGAME", (await b.req("RES 1"))[0])

    async def test_invitations_survive_restart(self):
        a, b, c = [await Client.connect(self.port) for _ in range(3)]
        ta = (await a.req("HELO lou"))[0].split()[1]
        tb = (await b.req("HELO mae"))[0].split()[1]
        tc = (await c.req("HELO ned"))[0].split()[1]
        await a.req("NEW reversi standard vis=private")
        await a.req("JON 1")
        await a.req("INV 1 mae 1")
        await a.req("NEW reversi standard")
        await a.req("JON 2 1")
        await a.req("INV 2 ned 0")
        self.stop()
        self.start()
        a, b, c = [await Client.connect(self.port) for _ in range(3)]
        await a.req("HELO lou " + ta)
        await b.req("HELO mae " + tb)
        await c.req("HELO ned " + tc)
        self.assertEqual(await b.req("IVL"), ["IVT 1 reversi/1 standard lou open seat=1", "OK"])
        self.assertEqual(await c.req("IVL"), ["IVT 2 reversi/1 standard lou open seat=0", "OK"])
        self.assertEqual([l[:5] for l in await c.req("LST")], ["TAB 2", "OK"])   # private table 1 still hidden
        self.assertIn("ERR PRIVATE", (await c.req("JON 1"))[0])
        self.assertIn("TAKEN", (await b.req("JON 2"))[0])            # seat 0 is still held for ned
        self.assertEqual((await b.req("JON 1"))[0], "OK 1")
        self.assertEqual((await c.req("JON 2"))[0], "OK 0")
        self.assertEqual(await b.req("IVL"), ["OK"])
        self.stop()
        self.start()
        c = await Client.connect(self.port)
        await c.req("HELO ned " + tc)
        self.assertEqual(await c.req("IVL"), ["OK"])                   # used up, also after a restart

    async def test_turn_timer_forfeits(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO cat")
        await b.req("HELO dan")
        self.assertIn("BADOPT", (await a.req("NEW reversi standard turn=soon"))[0])
        await a.req("NEW reversi standard turn=2s")
        await a.req("JON 1")
        await b.req("JON 1")
        r = await a.req("WAT 1 0 1")
        r = await a.req("WAT 1 %s 6" % r[-1].split()[1])
        self.assertTrue(any("END timeout 0" in l for l in r), r)


    async def events(self, c, tid, since=0, secs=0):
        """The event payloads of a table after since (waiting up to secs for something new) -> (list, seq)."""
        r = await c.req("WAT %s %d %s" % (tid, since, secs) if secs else "SIN %s %d" % (tid, since))
        return [l.split(" ", 3)[3] for l in r if l.startswith("EVT ")], int(r[-1].split()[1])

    async def three_seats(self, names, turn):
        cs = [await Client.connect(self.port) for _ in names]
        toks = [(await c.req("HELO " + n))[0].split()[1] for c, n in zip(cs, names)]
        self.assertEqual((await cs[0].req("NEW rummytile standard players=3 turn=%s" % turn))[0], "OK 1")
        for i, c in enumerate(cs):
            self.assertEqual((await c.req("JON 1"))[0], "OK %d" % i)
        return cs, toks

    async def test_one_of_three_resigns_and_the_others_play_on(self):
        """A seat leaving a game of three (flag drop): GONE, the table keeps running; with two seats left,
        a resignation ends the table as before. Who has left survives a restart."""
        (a, b, c), toks = await self.three_seats(["ova", "pim", "quin"], "1h")
        r = await b.req("RES 1")
        self.assertEqual(r[-1], "OK")
        evs, seq = await self.events(a, 1)
        self.assertIn("GONE 1 resign", evs)
        self.assertFalse(any(e.startswith("END") for e in evs), evs)
        self.assertIn(" playing ova,pim,quin ", (await a.req("LST"))[0])
        self.assertIn("ERR STATE", (await b.req("RES 1"))[0])         # already gone
        self.assertIn("ERR STATE", (await b.req("MOV 1 reset"))[0])
        self.assertEqual((await a.req("END 1"))[-1], "OK")             # ova draws; the turn skips pim
        evs, seq = await self.events(a, 1, seq)
        self.assertIn("TURN 2", evs)
        self.stop()
        self.start()                                                   # the gone seat is kept
        c = await Client.connect(self.port)
        await c.req("HELO quin " + toks[2])
        r = await c.req("RES 1")
        self.assertTrue(any(l.endswith(" END resign 2") for l in r), r)
        self.assertIn("NOGAME", (await c.req("RES 1"))[0])

    async def test_one_of_three_times_out_and_the_others_play_on(self):
        (a, b, c), _ = await self.three_seats(["rae", "sol", "tam"], "2s")
        evs, seq = await self.events(a, 1)
        got = list(evs)
        while not any(e.startswith("GONE") for e in got):
            evs, seq = await self.events(a, 1, seq, 6)
            self.assertTrue(evs, got)
            got += evs
        self.assertIn("GONE 0 timeout", got)
        self.assertIn("TURN 1", got[got.index("GONE 0 timeout"):])
        self.assertFalse(any(e.startswith("END") for e in got), got)
        while not any(e.startswith("END") for e in got):               # two left: a timeout ends the table
            evs, seq = await self.events(a, 1, seq, 6)
            self.assertTrue(evs, got)
            got += evs
        self.assertIn("END timeout 1", got)

    async def test_two_seats_resign_ends_the_table(self):
        a, b = await Client.connect(self.port), await Client.connect(self.port)
        await a.req("HELO uma")
        await b.req("HELO vic")
        await a.req("NEW rummytile standard")
        await a.req("JON 1")
        await b.req("JON 1")
        r = await b.req("RES 1")
        self.assertTrue(any(l.endswith(" END resign 1") for l in r), r)
        self.assertFalse(any(" GONE " in l for l in r), r)


class OkLine(unittest.TestCase):
    def test_ok_has_no_trailing_space(self):
        from parlord.core import ok
        self.assertEqual(ok(), "OK")
        self.assertEqual(ok(""), "OK")               # MOV with no staged state, CHK with no info
        self.assertEqual(ok("more"), "OK more")
        self.assertEqual(ok(0), "OK 0")


class RateLimit(Integration):
    extra = ["--rate", "5", "--burst", "10"]

    async def test_rate(self):
        a = await Client.connect(self.port)
        await a.req("HELO fast")
        seen = [(await a.req("GAM"))[-1] for _ in range(30)]
        self.assertTrue(any(x.startswith("ERR RATE") for x in seen))

    test_game_to_the_end = None
    test_limits_and_errors = None
    test_private_tables_and_invitations = None
    test_public_table_held_seats = None


if __name__ == "__main__":
    unittest.main()
