"""The trust rules (docs/design/trust.md) that the core enforces on its own: a table's randomness comes
from a seed the core draws from the system's secure generator, wide enough that nobody can find it by
trying every value against the dice or tiles they have seen, and the seed never reaches a client."""
import asyncio
import importlib.util
import os
import sys
import unittest

from test_core import ROOT, Client

sys.path.insert(0, ROOT)
from parlord import core  # noqa: E402
from parlord.core import Limits, Server  # noqa: E402
from parlord.store import Store  # noqa: E402


def plugin(name):
    spec = importlib.util.spec_from_file_location("trust_" + name, os.path.join(ROOT, "plugins", name, "plugin.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Seeds(unittest.TestCase):
    def test_table_seeds_are_wide(self):
        seeds = [core.table_seed() for _ in range(64)]
        self.assertTrue(all(0 <= s < 2 ** core.SEED_BITS for s in seeds))
        self.assertEqual(core.SEED_BITS, 63)
        self.assertGreater(max(s.bit_length() for s in seeds), 40)     # not 32 bits; all 64 under 2^41: ~2^-1400
        self.assertEqual(len(set(seeds)), 64)

    def test_the_store_keeps_a_wide_seed(self):
        class T:
            id, type, ver, rules, options, seats, status, state = "1", "backgammon", 1, "standard", {}, [None] * 2, \
                "open", None
            seq, turn_secs, turn_seat, deadline, created, touched, vis, owner, gone = 0, 60, -1, 0.0, 0.0, 0.0, \
                "public", "ann", set()
            seed = 2 ** 63 - 1
        s = Store()
        s.save_table(T)
        self.assertEqual(s.load_tables()[0]["seed"], 2 ** 63 - 1)

    def test_every_game_with_chance_takes_a_wide_seed(self):
        big = 2 ** 63 - 1
        bg = plugin("backgammon")
        st = bg.op_new({"players": 2, "options": {}, "seed": big})["state"]
        self.assertTrue(all(1 <= d <= 6 for d in bg.roll(st)))
        gw = plugin("gridwords")
        st = gw.op_new({"rules": "english", "players": 2, "options": {}, "seed": big})["state"]
        self.assertEqual([len(r) for r in st["racks"]], [7, 7])
        nc = plugin("navalcombat")
        st = nc.op_new({"rules": "classic", "players": 2, "options": {}, "seed": big})["state"]
        r = nc.op_apply({"rules": "classic", "state": st, "seat": 0, "move": ["random"]})
        self.assertNotIn("error", r)
        cf = plugin("cannonfire")
        self.assertNotIn("error", cf.op_new({"rules": "classic", "players": 2, "options": {}, "seed": big}))


class OverProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        rate = (Limits.rate_per_sec, Limits.rate_burst)
        Limits.rate_per_sec, Limits.rate_burst = 1000.0, 1000
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        Limits.rate_per_sec, Limits.rate_burst = rate
        await self.srv.load_plugins()
        self.server = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            c.w.close()
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 10)
        for p in self.srv.plugins.values():
            p.stop()

    async def client(self, name):
        c = Client(*await asyncio.open_connection("127.0.0.1", self.port))
        self.clients.append(c)
        c.w.write(b"PARLORNET/1\r\n")
        await c.r.readline()
        self.assertEqual((await c.req("HELO " + name))[-1], "OK -")
        return c

    async def test_the_seed_is_the_cores_and_stays_there(self):
        """A table's seed is drawn by the core, whatever the creator asks for, and no view, event or list
        carries it."""
        a, b = await self.client("ann"), await self.client("bob")
        tid = (await a.req("NEW backgammon standard seed=5"))[-1].split()[1]      # not an option of the game
        await a.req("JON " + tid)
        await b.req("JON " + tid)
        t = self.srv.tables[tid]
        seed = t.seed
        self.assertEqual(t.state["seed"], seed)
        self.assertGreater(seed, 10 ** 6)      # 63 bits: under a million about once in 10^13
        seen = []
        for c in (a, b):
            seen += await c.req("VUE %s GMT" % tid)
            seen += await c.req("SIN %s 0" % tid)
            seen += await c.req("LST")
        self.assertFalse([l for l in seen if str(seed) in l])


if __name__ == "__main__":
    unittest.main()
