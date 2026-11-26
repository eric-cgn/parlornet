"""The admin interface (parlord/admin.py) and the admin adapter's blue-screen
menus (adapters/admin.py): python3 -m unittest discover -s tests"""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import unittest

from test_core import ROOT, Client as CoreClient, free_port, make_keys
from test_adapters import Client

sys.path.insert(0, ROOT)
from adapters import admin as A  # noqa: E402
from adapters import catalog  # noqa: E402
from parlord import admin as PA  # noqa: E402
from parlord import auth  # noqa: E402
from parlord.core import Server, Session  # noqa: E402
from speed import slow  # noqa: E402


class FakeWriter:
    def __init__(self, peer):
        self.peer, self.data, self.closed = peer, b"", False

    def get_extra_info(self, key):
        return self.peer if key == "peername" else None

    def write(self, b):
        self.data += b

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class Base(unittest.IsolatedAsyncioTestCase):
    method = "password"

    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", self.method)
        await self.srv.load_plugins()
        self.adm = PA.Admin(self.srv)
        self.conn = PA.AdminConn()

    async def asyncTearDown(self):
        for p in self.srv.plugins.values():
            p.stop()
            if p.proc:
                await p.proc.wait()

    async def req(self, op, conn=None, **kw):
        kw["op"] = op
        return await self.adm.dispatch(conn or self.conn, kw)

    async def admin(self):
        """Sign in with the setup password and set the permanent one."""
        r = await self.req("login", password=self.adm.setup)
        self.assertEqual(r, {"ok": True, "setup": True})
        self.assertTrue((await self.req("set_password", password="correct horse"))["ok"])

    async def player_with(self, srv, name, password):
        sess = Session(FakeWriter(("127.0.0.1", 1)))
        r = await srv.dispatch(sess, "HELO %s" % name)
        if r[-1].startswith("ASK password"):
            r = await srv.dispatch(sess, "AUT password %s" % password)
        self.assertTrue(r[-1].startswith("OK"), r)
        srv.sessions.add(sess)
        return sess

    def unlock(self):
        for adm in (self.adm, getattr(self, "adm2", None)):  # as if the wait were over (the count stays)
            if adm is not None:
                adm.fails = {k: (f, m, 0.0) for k, (f, m, _) in adm.fails.items()}

    async def player(self, name):
        """A connection signed in as name (with the password name-Pass-word, chosen at the first sign-in)."""
        sess = Session(FakeWriter(("127.0.0.1", 1)))
        pw = name + "-Pass-word"
        r = await self.srv.dispatch(sess, "HELO %s" % name)
        if r[-1].startswith("ASK password Choose"):         # sign up
            await self.srv.dispatch(sess, "AUT password %s" % pw)
            r = await self.srv.dispatch(sess, "AUT confirm %s" % pw)
        elif r[-1].startswith("ASK password"):
            r = await self.srv.dispatch(sess, "AUT password %s" % pw)
        self.assertTrue(r[-1].startswith("OK"), r)
        self.srv.sessions.add(sess)
        return sess


class Passwords(Base):
    async def test_no_key_no_admin_socket(self):
        """Without a keyring (and without an admin-role key in the TLS
        handshake) the admin socket answers nothing at all, loopback or not."""
        for peer in (("203.0.113.5", 4000), ("127.0.0.1", 4000), None):
            w = FakeWriter(peer)
            w.transport = type("T", (), {"abort": lambda self: None})()
            r = asyncio.StreamReader()
            r.feed_data(b'{"op": "hello"}\n')
            r.feed_eof()
            await self.adm.handle(r, w)
            self.assertEqual(w.data, b"")

    async def test_first_launch_and_lockouts(self):
        self.assertRegex(self.adm.setup, r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")
        self.assertIn(self.adm.setup, PA.banner(self.adm.setup))
        self.assertIn("telnet <host> 6510", PA.banner(self.adm.setup))
        self.assertFalse((await self.req("get"))["ok"])                    # not signed in
        for n in (1, 2):                                                    # two tries before any wait
            r = await self.req("login", password="guess")
            self.assertEqual((r["ok"], r["wait"]), (False, 0))
        r = await self.req("login", password="guess")
        self.assertEqual((r["ok"], r["wait"]), (False, 60))
        r = await self.req("login", password=self.adm.setup)              # right, but locked
        self.assertFalse(r["ok"])
        self.assertIn("Too many wrong passwords", r["error"])
        self.unlock()
        r = await self.req("login", password="guess again")
        self.assertEqual(r["wait"], auth.lock_secs(4))
        self.unlock()
        self.assertEqual(await self.req("login", password=self.adm.setup.upper()), {"ok": True, "setup": True})
        self.assertEqual(self.adm.fails, {})                                # a right one clears the count
        r = await self.req("get")
        self.assertEqual(r["error"], "set the permanent admin password first")
        self.assertFalse((await self.req("set_password", password="short"))["ok"])
        self.assertTrue((await self.req("set_password", password="correct horse"))["ok"])
        self.assertTrue((await self.req("get"))["ok"])
        stored = self.srv.store.get("admin:pw")
        self.assertTrue(stored.startswith("scrypt$") and "correct horse" not in stored)
        # after a restart: no setup password, the permanent one works, the old setup one does not
        adm2 = self.adm2 = PA.Admin(self.srv)
        self.assertIsNone(adm2.setup)
        self.assertTrue(adm2.configured())
        c = PA.AdminConn()
        self.assertFalse((await adm2.dispatch(c, {"op": "login", "password": self.adm.setup}))["ok"])
        self.unlock()
        self.assertTrue((await adm2.dispatch(c, {"op": "login", "password": "correct horse"}))["ok"])
        # wrong passwords follow the player schedule, for the address they come from only
        far = {"op": "login", "source": "203.0.113.9"}
        for n in (1, 2, 3, 4, 5):
            self.unlock()
            r = await adm2.dispatch(PA.AdminConn(), dict(far, password="nope"))
            self.assertEqual(r["wait"], auth.lock_secs(n))
        r = await adm2.dispatch(PA.AdminConn(), dict(far, password="correct horse"))
        self.assertFalse(r["ok"])
        self.assertGreater(r["wait"], 800)
        r = await adm2.dispatch(PA.AdminConn(), {"op": "login", "password": "correct horse", "source": "127.0.0.1"})
        self.assertTrue(r["ok"])                       # the operator, from elsewhere, is not kept out
        # the maintenance command clears it, and the next start makes a new setup password
        db = os.path.join(os.environ.get("TMPDIR", "/tmp"), "parlord-admin-test-%d.db" % os.getpid())
        try:
            from parlord.store import Store
            st = Store(db)
            st.set("admin:pw", PA.hash_password("x" * 8))
            st.db.close()
            subprocess.run([sys.executable, "-m", "parlord.admin", "--db", db, "--reset-password"], cwd=ROOT,
                           check=True, capture_output=True)
            self.assertIsNone(Store(db).get("admin:pw"))
        finally:
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db + suffix):
                    os.remove(db + suffix)

    def test_hash(self):
        h = PA.hash_password("secret pw")
        self.assertTrue(PA.check_password("secret pw", h))
        self.assertFalse(PA.check_password("secret pX", h))
        self.assertNotEqual(h, PA.hash_password("secret pw"))       # salted
        self.assertFalse(PA.check_password("x", "garbage"))


class Requests(Base):
    async def test_settings_take_effect(self):
        await self.admin()
        srv = self.srv
        # sign-in method, live
        r = await self.req("set", key="auth.signin", value="none")
        self.assertTrue(r["ok"])
        self.assertEqual(r["note"], "Sign-up is now none, which sign-in none needs.")
        self.assertEqual(srv.auth.name, "none+none")
        self.assertEqual(srv.auth.greeting(), "AUTH=none SIGNUP=none")
        self.assertFalse((await self.req("set", key="auth.signin", value="ldap"))["ok"])
        r = await self.req("set", key="auth.signup", value="email")
        self.assertFalse(r["ok"])
        self.assertIn("cannot work", r["error"])
        g = await self.req("get")
        self.assertEqual((g["effective"]["auth.signin"], g["effective"]["auth.signup"]), ("none", "none"))
        # name and message of the day
        self.assertFalse((await self.req("set", key="server.name", value="two words"))["ok"])
        self.assertTrue((await self.req("set", key="server.name", value="club.parlor"))["ok"])
        self.assertEqual(srv.name, "club.parlor")
        motd = "Welcome to the club.\n\nTables close at midnight."
        self.assertTrue((await self.req("set", key="server.motd", value=motd))["ok"])
        sess = await self.player("ann")
        out = await srv.cmd_motd(sess, [], "MOTD")
        self.assertEqual(out, ["MOTD Welcome to the club.", "MOTD", "MOTD Tables close at midnight.", "OK"])
        self.assertFalse((await self.req("set", key="server.motd", value="\n".join("x" * 21)))["ok"])
        # games offered
        gam = await srv.cmd_gam(sess, [], "GAM")
        self.assertTrue(any(l.startswith("GAM reversi/1 standard") for l in gam))
        self.assertTrue((await self.req("set", key="games.off", value="reversi/1:standard"))["ok"])
        gam = await srv.cmd_gam(sess, [], "GAM")
        self.assertFalse(any(l.startswith("GAM reversi") for l in gam))
        self.assertTrue(any(l.startswith("GAM backgammon/1 standard") for l in gam))
        self.assertEqual((await srv.cmd_new(sess, ["reversi", "standard"], ""))[0][:10], "ERR NOGAME")
        self.assertTrue((await srv.cmd_new(sess, ["backgammon", "standard"], ""))[0].startswith("OK"))
        self.assertTrue((await self.req("set", key="games.off", value=""))["ok"])
        # default time per turn
        self.assertTrue((await self.req("set", key="server.turn_default", value="15m"))["ok"])
        tid = (await srv.cmd_new(sess, ["reversi", "standard"], ""))[0].split()[1]
        self.assertEqual(srv.tables[tid].turn_secs, 900)
        self.assertFalse((await self.req("set", key="server.turn_default", value="5s"))["ok"])
        # get shows them, never the SMTP password
        self.assertTrue((await self.req("set", key="email.smtp_password", value="hunter2"))["ok"])
        self.assertFalse((await self.req("set", key="email.from", value="not an address"))["ok"])
        self.assertTrue((await self.req("set", key="email.from", value="Club <noreply@club.example>"))["ok"])
        self.assertFalse((await self.req("set", key="email.limit.per_day", value="500"))["ok"])
        self.assertTrue((await self.req("set", key="email.limit.per_day", value="100"))["ok"])
        self.assertFalse((await self.req("set", key="admin:pw", value="x"))["ok"])
        g = await self.req("get")
        self.assertEqual(g["settings"]["email.smtp_password"], "set")
        self.assertNotIn("hunter2", json.dumps(g))
        self.assertEqual(g["effective"]["server.turn_default"], "900")
        self.assertEqual(g["limits"]["per_day"][0], 100)
        self.assertEqual(sorted(x["key"] for x in g["games"]), sorted(srv.plugins))     # every game installed
        self.assertTrue({"backgammon/1", "reversi/1"} <= set(srv.plugins))
        # stored: a new Server on the same database keeps them
        self.assertEqual(srv.store.settings()["server.name"], "club.parlor")

    async def test_accounts_and_tables(self):
        await self.admin()
        srv = self.srv
        ann, bob = await self.player("ann"), await self.player("bob")
        r = await self.req("accounts")
        self.assertEqual([a["name"] for a in r["accounts"]], ["ann", "bob"])
        self.assertTrue(r["accounts"][0]["online"])
        self.assertEqual([a["name"] for a in (await self.req("accounts", q="BO"))["accounts"]], ["bob"])
        # suspend signs the player out and refuses sign-in
        self.assertTrue((await self.req("suspend", name="bob"))["ok"])
        self.assertTrue(bob.closing and bob.writer.closed)
        r = await srv.dispatch(Session(None), "HELO bob")
        self.assertEqual(r, ["ERR SUSPENDED this name is suspended"])
        self.assertTrue((await self.req("unsuspend", name="bob"))["ok"])
        self.assertFalse(srv.player("bob").suspended)
        self.assertFalse((await self.req("suspend", name="nobody"))["ok"])
        # reset under password: a temporary password for the operator; every session of ann ends
        srv.player("ann").fails, srv.player("ann").locked_until = 3, time.time() + 900
        r = await self.req("reset", name="ann")
        self.assertTrue(r["ok"])
        self.assertTrue(r["code"] in r["text"], "the text gives the temporary password")
        self.assertEqual(srv.player("ann").locked_until, 0)
        self.assertTrue(ann.closing)
        ann = Session(FakeWriter(("127.0.0.1", 1)))
        await srv.dispatch(ann, "HELO ann")
        self.assertEqual((await srv.dispatch(ann, "AUT password ann-Pass-word"))[-1], "ERR WRONG Wrong password; try again")
        srv.player("ann").locked_until = 0
        await srv.dispatch(ann, "HELO ann")
        self.assertEqual((await srv.dispatch(ann, "AUT password " + r["code"]))[-1][:16], "ASK password New")
        await srv.dispatch(ann, "AUT password Ann-new-word")
        self.assertEqual((await srv.dispatch(ann, "AUT confirm Ann-new-word"))[-1], "OK -")
        srv.sessions.add(ann)
        # tables: a running game ends as abandoned; an open table goes
        tid = (await srv.cmd_new(ann, ["reversi", "standard"], ""))[0].split()[1]
        bob2 = await self.player("bob")
        await srv.cmd_jon(ann, [tid], "")
        await srv.cmd_jon(bob2, [tid], "")
        open_tid = (await srv.cmd_new(bob2, ["reversi", "standard"], ""))[0].split()[1]
        t = await self.req("tables")
        self.assertEqual([(x["id"], x["status"]) for x in t["tables"]], [(tid, "playing"), (open_tid, "open")])
        self.assertEqual(t["tables"][0]["seats"], ["ann", "bob"])
        self.assertTrue((await self.req("end_table", id=tid))["ok"])
        self.assertEqual(srv.tables[tid].status, "done")
        self.assertEqual(srv.tables[tid].events[-1][1], "END abandoned")
        self.assertTrue((await self.req("end_table", id=open_tid))["ok"])
        self.assertNotIn(open_tid, srv.tables)
        self.assertFalse((await self.req("end_table", id="999"))["ok"])
        # delete: gone, and a connection closing later does not bring it back
        self.assertTrue((await self.req("delete", name="bob"))["ok"])
        self.assertIsNone(srv.player("bob"))
        srv.save_player(bob2.player)
        self.assertNotIn("bob", [r[0] for r in srv.store.load_players()])
        # status
        s = await self.req("status")
        self.assertEqual((s["players"], s["tables"]["done"], s["auth"]), (1, 1, "password+password"))
        self.assertIn("reversi/1:standard", s["games"])
        self.assertIn("uptime", s)

    async def test_with_invitations_and_games_table(self):
        """The admin work together with invitations, private tables and GAM before sign-in."""
        await self.admin()
        srv = self.srv
        ann = await self.player("ann")
        await self.player("bob")
        # GAM before sign-in follows games.off too
        anon = Session(FakeWriter(("127.0.0.1", 2)))
        self.assertTrue((await self.req("set", key="games.off", value="backgammon/1"))["ok"])
        gam = await srv.dispatch(anon, "GAM")          # not signed in
        self.assertFalse(any(l.startswith("GAM backgammon") for l in gam))
        self.assertTrue(any(l.startswith("GAM reversi/1 standard") for l in gam))
        self.assertTrue((await self.req("set", key="games.off", value=""))["ok"])
        # a private table with an invitation and a held seat; the admin list shows both
        tid = (await srv.cmd_new(ann, ["reversi", "standard", "vis=private"], ""))[0].split()[1]
        await srv.cmd_jon(ann, [tid, "0"], "")
        self.assertEqual(await srv.cmd_inv(ann, [tid, "bob", "1"], ""), ["OK"])
        t = next(x for x in (await self.req("tables"))["tables"] if x["id"] == tid)
        self.assertEqual((t["vis"], t["owner"], t["invited"]), ("private", "ann", ["bob"]))
        # deleting the invited player withdraws the invitation and frees the seat
        self.assertTrue((await self.req("delete", name="bob"))["ok"])
        self.assertEqual(srv.tables[tid].invites, {})
        self.assertEqual(srv.tables[tid].events[-1][1], "UNINVITE 1 bob withdrawn")
        self.assertEqual(srv.store.load_invites(tid), {})
        # the telnet and web text for the operator's end and for invitations
        from adapters.telnet import describe_event
        self.assertEqual(describe_event("END abandoned", ["ann", "bob"]), "The operator ended this game")
        self.assertEqual(describe_event("INVITE 1 bob", ["ann", "-"]), "bob is invited")
        self.assertEqual(describe_event("UNINVITE - bob", ["ann", "-"]), "bob is no longer invited")
        self.assertEqual(describe_event("UNINVITE - bob declined", ["ann", "-"]), "bob declined the invitation")

    async def test_email_controls(self):
        await self.admin()
        srv = self.srv
        r = await self.req("test_email", to="op@example.org")
        self.assertEqual(r, {"ok": True, "transport": "log"})
        self.assertEqual((await self.req("email"))["email"]["minute"], 1)
        self.assertFalse((await self.req("test_email", to="not-an-address"))["ok"])
        self.assertTrue((await self.req("pause_email"))["ok"])
        self.assertTrue((await self.req("email"))["email"]["paused"])
        self.assertTrue((await self.req("set", key="auth.signup", value="email"))["ok"])
        self.assertEqual(srv.auth.name, "password+email")
        s = Session(None)
        self.assertEqual((await srv.dispatch(s, "HELO cat"))[-1][:11], "ASK confirm")
        self.assertEqual((await srv.dispatch(s, "AUT confirm y"))[-1][:9], "ASK email")
        r = await srv.dispatch(s, "AUT email cat@example.org")
        self.assertEqual(r[0][:10], "ERR PAUSED")
        self.assertTrue((await self.req("resume_email"))["ok"])
        self.assertFalse((await self.req("email"))["email"]["paused"])
        # bounces hold new addresses; resuming lets them through again
        for i in range(4):          # addresses parlord mailed (another sender's bounces do not count)
            srv.store.email_sent("x%d@example.org" % i, "x%d" % i, True)
            srv.store.block_email("x%d@example.org" % i, "bounced")
        srv.store.db.execute("UPDATE email_log SET at = at - 3601")     # mailed an hour ago (out of the minute's cap)
        self.assertTrue(srv.new_signups_held())
        self.assertTrue((await self.req("email"))["email"]["new_held"])
        await self.req("resume_email")
        self.assertFalse(srv.new_signups_held())
        # a sign-up: a pending account the accounts list shows with the time it has left
        s = Session(None)
        await srv.dispatch(s, "HELO dan")
        await srv.dispatch(s, "AUT confirm y")
        r = await srv.dispatch(s, "AUT email dan@example.org")
        self.assertEqual(r[-1], "ASK password Password_from_the_email secret")
        a = (await self.req("accounts", q="dan"))["accounts"][0]
        self.assertTrue(86000 < a["pending"] <= 86400, a["pending"])
        self.assertEqual((a["email"], a["verified"]), ("dan@example.org", False))
        temp = re.search(r"Password:\s+(\S+)", srv.auth.mailer.last["body"]).group(1)
        await srv.dispatch(s, "AUT password " + temp)
        await srv.dispatch(s, "AUT password Dan-own-word")
        r = await srv.dispatch(s, "AUT confirm Dan-own-word")
        self.assertTrue(r[-1].startswith("OK"), r)
        a = (await self.req("accounts", q="dan"))["accounts"][0]
        self.assertEqual((a["pending"], a["verified"]), (0, True))
        f = Session(None, frontend="web")                                  # a session elsewhere
        f.pending = None
        await srv.dispatch(f, "HELO dan")
        r = await srv.dispatch(f, "AUT password Dan-own-word")
        self.assertTrue(r[-1].startswith("OK"), r)
        # reset: a temporary password for the operator to give; the old one stops working
        r = await self.req("reset", name="dan")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["code"] in r["text"], "the text gives the temporary password")
        self.assertEqual(await srv.dispatch(f, "LST"), ["ERR SIGNEDOUT your session has ended; sign in again"])
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        g = Session(None)
        await srv.dispatch(g, "HELO dan")
        self.assertTrue((await srv.dispatch(g, "AUT password Dan-own-word"))[-1].startswith("ERR WRONG"))


class Catalog(unittest.TestCase):
    def test_catalog(self):
        self.assertEqual(catalog.parse("admin, WEB,bogus"), ["web", "admin"])
        self.assertIn("telnet", catalog.default_enabled())
        self.assertNotIn("nosuch", catalog.default_enabled())
        self.assertEqual(dict((n, p) for n, _, p, _ in catalog.ADAPTERS)["admin"], 6510)


class Widgets(unittest.TestCase):
    def test_keys(self):
        io = A.AdminIO(None, None)
        io._feed(b"\tx\x1b[Z\x1b[3~\x1b[A\r\n")
        self.assertEqual(io.keys, ["TAB", "x", "BTAB", "DEL", "UP", "ENTER"])


@slow
class LiveTUI(unittest.TestCase):
    """parlord with its admin socket and the admin adapter as processes,
    driven over a raw socket with key bytes."""

    def setUp(self):
        self.core, self.adm, self.tui = free_port(), free_port(), free_port()
        self.keys = make_keys("admin", role="admin")
        self.p = subprocess.Popen([sys.executable, "-m", "parlord", "--port", str(self.core), "--admin-port",
                                   str(self.adm), "--admin-telnet", str(self.tui), "--keys", self.keys,
                                   "--frontend-port", "0"], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out = b""
        while out.count(b"=" * 72) < 2:
            line = self.p.stdout.readline()
            self.assertTrue(line, out)
            out += line
        self.banner = out.decode()
        self.setup = re.search(r"setup password:\s+(\S+)", self.banner).group(1)
        self.a = subprocess.Popen([sys.executable, "-m", "adapters.admin", "--port", str(self.tui),
                                   "--core-admin", "127.0.0.1:%d" % self.adm,
                                   "--key", os.path.join(self.keys, "admin.key")], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.a.stdout.readline()

    def tearDown(self):
        for p in (self.p, self.a):
            p.kill()
            p.wait()
            p.stdout.close()

    def greeting(self):
        async def get():
            c = await CoreClient.connect(self.core)
            c.w.close()
            return c.greeting
        return asyncio.run(get())

    def test_admin_socket_needs_an_admin_key(self):
        from parlord import frontends
        frontends.write_key(self.keys, "web", "frontend")

        async def go():
            c = await A.AdminCore.connect("127.0.0.1", self.adm, os.path.join(self.keys, "admin.key"))
            self.assertEqual(c.w.get_extra_info("ssl_object").version(), "TLSv1.3")
            r = await c.req("hello")
            c.close()
            self.assertTrue(r["ok"])
            with self.assertRaises(A.CoreGone):          # a frontend key is not an admin key
                await A.AdminCore.connect("127.0.0.1", self.adm, os.path.join(self.keys, "web.key"))
            r, w = await asyncio.open_connection("127.0.0.1", self.adm)     # plain text: nothing
            w.write(b'{"op": "hello"}\n')
            await w.drain()
            self.assertEqual(await asyncio.wait_for(r.read(100), 12), b"")
            w.close()
        asyncio.run(go())

    def test_setup_by_keys(self):
        self.assertIn("ParlorNet is not set up yet", self.banner)
        self.assertIn("telnet <host> %d" % self.tui, self.banner)
        c = Client(self.tui)
        try:
            c.until("Choice [")
            c.send("\r\n")                                  # pc: CP437, the default
            c.until("one-time setup password", screen=True)
            c.send("wrong-pass-word\r")
            c.until("Wrong password; try again", screen=True)    # the first: no wait yet
            c.send("\r")
        finally:
            c.close()
        # the sign-in is now locked for a minute: start again with a fresh core (its database is in memory)
        self.tearDown()
        self.setUp()
        c = Client(self.tui)
        try:
            c.until("Choice [")
            c.send("\r\n")
            c.until("one-time setup password", screen=True)
            c.send(self.setup + "\r")
            c.until("Choose the permanent admin password", screen=True)
            c.send("tablesforall\r")
            c.until("Type it again", screen=True)
            c.send("tablesforall\r")
            c.until("The admin password is set", screen=True)
            c.send("\r")
            screen = c.until("Main menu", screen=True)
            for item in ("Server", "Sign-in", "Games", "Adapters", "Accounts", "Tables", "Status", "Exit"):
                self.assertIn(item, screen)
            self.assertIn("┌", screen)
            self.assertIn("<Select>", screen)
            c.send("\r")                                    # Server
            c.until("Turn time", screen=True)
            c.send("\r")                                    # Name
            c.until("Server name", screen=True)
            c.send("\x7f" * 12 + "festival.lan\r")
            c.until("festival.lan", screen=True)
            self.assertTrue(self.greeting().startswith("PARLORNET/1 festival.lan AUTH=password SIGNUP=password"))
            c.send("\x1b[B\r")                              # Message
            c.until("Message of the day", screen=True)
            c.send("Welcome to the festival.\rTables by the stage.\t\r")
            c.gone("Message of the day")
            c.send("\x1b")                                  # back to the main menu
            c.until("Main menu", screen=True)
            c.send("\x1b[B\r")                              # Sign-in
            c.until("How players prove", screen=True)
            c.send("\r")                                    # Method
            c.until("Sign-in method", screen=True)
            c.send("\x1b[A\r")                              # password -> none
            c.until("Sign-up is now none, which sign-in none needs.", screen=True)
            c.send("\r")
            c.until("Sign-in  none", screen=True)
            self.assertIn("AUTH=none", self.greeting())
            c.send("\x1b")
            c.until("Main menu", screen=True)
            c.send("\x1b[B\r")                              # Games: what is offered, and the limits
            screen = c.until("20 games a player plays at once", screen=True)
            self.assertIn("2 open tables a player waits at, per game", screen)
            self.assertIn("400 tables on the server", screen)
            c.send("\x1b[B\r")                              # Active games
            c.until("The most games one player may be playing at once", screen=True)
            c.send("\x7f" * 4 + "25\r")
            c.until("25 games a player plays at once", screen=True)
            c.send("\x1b")
            c.until("Main menu", screen=True)
            c.send("\x1b[F\r")                              # Exit
            c.until("Signed out")
            self.assertEqual(c.vt.errors, [])
        finally:
            c.close()

        async def motd():
            cl = await CoreClient.connect(self.core)
            r = await cl.req("MOTD")
            cl.w.close()
            return r
        self.assertEqual(asyncio.run(motd()), ["MOTD Welcome to the festival.", "MOTD Tables by the stage.", "OK"])
        # the permanent password from now on, in the ASCII character set
        c = Client(self.tui, "ascii")
        try:
            c.until("Choice [")
            c.send("3\r\n")
            c.until("Admin password for festival.lan", screen=True)
            c.send("tablesforall\r")
            screen = c.until("Main menu", screen=True)
            self.assertIn("+----", screen)
            self.assertNotIn("┌", screen)
            c.send("\x1b[F\x1b[A\x1b[A\r")                   # Status
            screen = c.until("Up for", screen=True)
            self.assertIn("festival.lan (sign-in: none, sign-up: none)", screen)
            self.assertIn("Sign-ups      0 of 60 pending (each deleted after 24 hours)", screen)
            self.assertIn("0 addresses in cooldown (one email in 7 days)", screen)
            c.send("s")                                     # Sign-ups: clear address cooldowns
            c.until("Clearing lets", screen=True)
            c.send("\x1b[B\r")                              # All addresses: asked first
            c.until("Clear the sign-up cooldown of every address (0 now)?", screen=True)
            c.send("y")
            c.until("Cleared 0 addresses.", screen=True)
            c.send("\r")
            c.until("Up for", screen=True)
            c.send("s")
            c.until("Clearing lets", screen=True)
            c.send("\r")                                    # One address: typed, hashed, not kept
            c.until("is not stored", screen=True)
            c.send("someone@example.org\r")
            c.until("That address was not in its cooldown.", screen=True)
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
