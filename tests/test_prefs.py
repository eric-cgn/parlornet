"""PRF: a little data per server extension and player (docs/protocol.md). An in-process core with its
public port and its extension channel (TLS 1.3 with a key per extension)."""
import asyncio
import os
import sys
import tempfile
import unittest

from test_core import ROOT, Client, make_keys

sys.path.insert(0, ROOT)
from adapters.common import MOVE_KEYS, MOVE_KEYS_PREF, CoreClient, CoreError  # noqa: E402
from parlord import core as C, frontends  # noqa: E402
from parlord.admin import Admin  # noqa: E402


@unittest.skipUnless(frontends.supported(), "the extension channel needs Python 3.13")
class Prefs(unittest.IsolatedAsyncioTestCase):
    db = ":memory:"

    async def asyncSetUp(self):
        self.keys = make_keys("telnet", "web")
        self.servers, self.conns = [], []
        self.rate = C.Limits.rate_per_sec, C.Limits.rate_burst
        C.Limits.rate_per_sec, C.Limits.rate_burst = 1000.0, 1000
        await self.start()

    async def start(self):
        self.srv = C.Server(os.path.join(ROOT, "plugins"), name="test", db=self.db, auth="none")
        self.srv.load_state()
        self.srv.keyring = frontends.Keyring(self.keys)
        pub = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0, limit=1024)
        ext = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0, limit=1024)
        self.servers += [pub, ext]
        self.port, self.fe = pub.sockets[0].getsockname()[1], ext.sockets[0].getsockname()[1]

    async def stop(self):
        for c in self.conns:
            c.w.close()
        self.conns = []
        for s in self.servers:
            s.close()
        for s in self.servers:
            await asyncio.wait_for(s.wait_closed(), 5)     # the connections' ends write to the database
        self.servers = []
        self.srv.store.db.close()

    async def asyncTearDown(self):
        await self.stop()
        C.Limits.rate_per_sec, C.Limits.rate_burst = self.rate

    async def ext(self, name, player=None):
        c = await CoreClient.connect("127.0.0.1", self.fe, os.path.join(self.keys, name + ".key"))
        self.conns.append(c)
        if player:
            await c.helo(player)
        return c

    async def native(self, player=None):
        c = await Client.connect(self.port)
        self.conns.append(c)
        if player:
            self.assertEqual(await c.req("HELO " + player), ["OK -"])
        return c

    async def test_extensions_are_isolated_from_each_other(self):
        tl, web, nat = await self.ext("telnet", "ann"), await self.ext("web", "ann"), await self.native("ann")
        await tl.pref_set("movekeys", "wasd")
        self.assertEqual(await web.prefs(), {})
        self.assertEqual(await nat.req("PRF LST"), ["OK"])
        await web.pref_set("movekeys", "ijkl")
        self.assertEqual(await nat.req("PRF SET movekeys hjkl"), ["OK"])
        self.assertEqual(await tl.pref_get("movekeys"), "wasd")
        self.assertEqual(await web.pref_get("movekeys"), "ijkl")
        self.assertEqual(await nat.req("PRF GET movekeys"), ["PRF movekeys hjkl", "OK"])
        await web.pref_del("movekeys")
        self.assertIsNone(await web.pref_get("movekeys"))
        self.assertEqual(await tl.prefs(), {"movekeys": "wasd"})
        # the namespace is the key's name, never a word of the request: there is nothing to name another
        self.assertIn("SYNTAX", (await nat.req("PRF LST telnet"))[-1])
        self.assertIn("SYNTAX", (await nat.req("PRF GET telnet movekeys"))[-1])
        self.assertEqual(self.srv.store.prefs(C.NATIVE_NS, "ann"), [("movekeys", "hjkl")])

    async def test_limits(self):
        c = await self.native("ann")
        L = C.Limits
        for i in range(L.pref_keys):
            self.assertEqual(await c.req("PRF SET k%d v" % i), ["OK"])
        self.assertEqual(await c.req("PRF SET more v"), ["ERR LIMIT at most 16 keys; PRF DEL one first"])
        self.assertEqual(await c.req("PRF SET k3 changed"), ["OK"])     # replacing a key is not a new one
        self.assertEqual(await c.req("PRF DEL k0"), ["OK"])
        self.assertEqual(await c.req("PRF SET more v"), ["OK"])
        for i in range(L.pref_keys):
            await c.req("PRF DEL k%d" % i)
        await c.req("PRF DEL more")
        # bytes: 15 keys of 12 and values of 56 are 1020 bytes; one more passes 1024
        for i in range(15):
            self.assertEqual(await c.req("PRF SET key-%08d %s" % (i, "v" * 56)), ["OK"])
        self.assertEqual(await c.req("PRF SET k vvvv"), ["ERR LIMIT at most 1024 bytes of keys and values"])
        self.assertEqual(await c.req("PRF SET k vvv"), ["OK"])                 # 1024 exactly
        self.assertEqual(sum(len(k) + len(v) for k, v in self.srv.store.prefs(C.NATIVE_NS, "ann")), 1024)
        bad_key = "ERR BADKEY a key is 1 to 12 of A-Z a-z 0-9 _ -"
        bad_value = "ERR BADVALUE a value is 1 to 56 printable ASCII characters"
        self.assertEqual(await c.req("PRF GET a.b"), [bad_key])
        self.assertEqual(await c.req("PRF GET abcdefghijklm"), [bad_key])          # 13
        self.assertEqual(await c.req("PRF SET x " + "v" * 57), [bad_value])
        c.w.write(b"PRF SET x a\x01b\r\n")                                         # a control character
        self.assertEqual(await c.line(), bad_value)
        c.w.write(b"PRF SET x caf\xc3\xa9\r\n")                                    # not ASCII
        self.assertEqual(await c.line(), bad_value)
        for line in ("PRF", "PRF SET x", "PRF GET", "PRF DEL a b", "PRF FROB x"):
            self.assertIn("ERR SYNTAX", (await c.req(line))[-1], line)
        # keys are case-insensitive; a value keeps its spaces and case
        self.assertEqual(await c.req("PRF DEL K"), ["OK"])
        self.assertEqual(await c.req("PRF DEL key-00000000"), ["OK"])
        self.assertEqual(await c.req("PRF SET Mode  Two  Words"), ["OK"])
        self.assertEqual(await c.req("PRF GET MODE"), ["PRF mode Two  Words", "OK"])

    async def test_only_the_players_own_session(self):
        nat = await self.native()
        self.assertEqual(await nat.req("PRF LST"), ["ERR NOHELO say HELO first"])     # not signed in
        ann = await self.ext("telnet", "ann")
        await ann.pref_set("movekeys", "esdf")
        bob = await self.ext("telnet", "bob")
        self.assertEqual(await bob.prefs(), {})                                       # bob sees only his own
        # another connection of the same extension resuming ann's session sees hers
        again = await self.ext("telnet")
        self.assertEqual(await again.resume(ann.sid), "ann")
        self.assertEqual(await again.prefs(), {"movekeys": "esdf"})
        # another extension cannot take the session up, so cannot reach it either
        web = await self.ext("web")
        with self.assertRaises(CoreError):
            await web.resume(ann.sid)
        with self.assertRaises(CoreError) as e:
            await web.prefs()
        self.assertEqual(e.exception.code, "NOHELO")
        # signed out: nothing
        await ann.out()
        with self.assertRaises(CoreError):
            await ann.pref_get("movekeys")

    async def test_deleted_with_the_account_and_admin_counts(self):
        tl, nat = await self.ext("telnet", "ann"), await self.native("ann")
        await tl.pref_set("movekeys", "wasd")
        await tl.pref_set("mode", "pc")
        await nat.req("PRF SET colour green")
        await (await self.ext("telnet", "bob")).pref_set("movekeys", "hjkl")
        admin = Admin(self.srv)
        info = admin.player_info(self.srv.player("ann"))
        self.assertEqual(info["prefs"], {"native": [1, 11], "telnet": [2, 18]})    # counts, never values
        self.assertNotIn("wasd", repr(info))
        accounts = {a["name"]: a for a in admin.op_accounts(None, {})["accounts"]}
        self.assertEqual(accounts["bob"]["prefs"], {"telnet": [1, 12]})
        await self.srv.delete_player(self.srv.player("ann"))
        self.assertEqual(self.srv.store.pref_usage("ann"), {})
        self.assertEqual(self.srv.store.pref_usage("bob"), {"telnet": (1, 12)})    # others keep theirs
        again = await self.ext("telnet", "ann")                                       # the name, new again
        self.assertEqual(await again.prefs(), {})

    async def test_load_and_save_helpers(self):
        tl = await self.ext("telnet", "ann")
        self.assertEqual(await tl.load_pref(MOVE_KEYS_PREF, MOVE_KEYS, "arrows"), "arrows")    # nothing kept
        self.assertTrue(await tl.save_pref(MOVE_KEYS_PREF, "wasd"))
        self.assertEqual(await tl.load_pref(MOVE_KEYS_PREF, MOVE_KEYS, "arrows"), "wasd")
        await tl.pref_set(MOVE_KEYS_PREF, "zzz")
        self.assertEqual(await tl.load_pref(MOVE_KEYS_PREF, MOVE_KEYS, "arrows"), "arrows")    # not a choice
        self.assertFalse(await tl.save_pref(MOVE_KEYS_PREF, "x" * 57))                         # refused
        self.assertFalse(await tl.save_pref("bad.key", "x"))
        signed_out = await self.ext("telnet")
        self.assertEqual(await signed_out.load_pref(MOVE_KEYS_PREF, MOVE_KEYS, "ijkl"), "ijkl")  # never raises


@unittest.skipUnless(frontends.supported(), "the extension channel needs Python 3.13")
class Persistence(Prefs):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "parlor.db")
        await super().asyncSetUp()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.tmp.cleanup()

    async def test_kept_across_a_restart(self):
        tl, nat = await self.ext("telnet", "ann"), await self.native("ann")
        await tl.pref_set("movekeys", "wasd")
        await nat.req("PRF SET colour green")
        await self.stop()
        await self.start()
        self.assertEqual(await (await self.ext("telnet", "ann")).prefs(), {"movekeys": "wasd"})
        self.assertEqual(await (await self.native("ann")).req("PRF LST"), ["PRF colour green", "OK"])
        self.assertEqual(await (await self.ext("web", "ann")).prefs(), {})


if __name__ == "__main__":
    unittest.main()
