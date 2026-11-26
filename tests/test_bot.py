"""The bot player (docs/bot.md): its accounts in the core (nobody signs in as a bot; the bot's key signs in as
nothing else), its strategies against a random player (by the plugins in this process: fast and seeded), and
the bot itself as a process against a real server: a whole game of every game, several bots at one table,
invitations from the native protocol, the web and telnet, leaving, the turn clock, and its memory.

    python3.13 -m unittest discover -s tests -p "test_bot.py"
"""
import asyncio
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from test_adapters import Browser, Client
from test_auth import frontend, public, say, server
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import botflag  # noqa: E402
from adapters.common import CoreClient, parse_tables  # noqa: E402
from bot import games, play, service  # noqa: E402
from bot.games import gridwords  # noqa: E402
from parlord import bots  # noqa: E402
from parlord.core import Server  # noqa: E402
from speed import slow  # noqa: E402

GAMES = [("reversi", "standard"), ("backgammon", "standard"), ("checkers", "american"), ("fiveinarow", "freestyle"),
         ("dotsandboxes", "standard"), ("mancala", "kalah"), ("navalcombat", "classic"), ("gridwords", "english"),
         ("cannonfire", "classic"), ("rummytile", "standard")]


# ---- the accounts ------------------------------------------------------------------------------

class Accounts(unittest.TestCase):
    """parlord/bots.py, through the core's request dispatch."""

    def test_nobody_signs_in_as_a_bot(self):
        for method, signup in (("none", None), ("password", None), ("password", "email")):
            srv = server(method, signup)
            asyncio.run(say(srv, frontend("bot"), "HELO bot"))      # the account exists, flagged
            for conn in (public(), frontend("web"), frontend("telnet")):
                for line in ("HELO bot", "USER bot", "REG bot", "HELO Bot1", "USER BOT3", "REG bot2", "HELO bot 123456"):
                    r = asyncio.run(say(srv, conn, line))
                    self.assertTrue(r.startswith("ERR RESERVED"), (method, line, r))
            self.assertTrue(bots.is_bot(srv.players["bot"]))
            self.assertIsNone(srv.players.get("bot1"))              # nothing made by a refused sign-in

    def test_the_bot_key_signs_in_with_the_name_alone_and_only_as_a_bot(self):
        for method, signup in (("none", None), ("password", None), ("password", "email")):
            srv = server(method, signup)
            conn = frontend("bot")
            r = asyncio.run(say(srv, conn, "HELO bot1"))
            self.assertRegex(r, r"^OK [A-Za-z0-9_-]{22}$", method)   # a session, no question asked
            self.assertTrue(bots.is_bot(srv.players["bot1"]))
            self.assertEqual(srv.players["bot1"].cred, "")           # no credential is kept for it
            for name in ("alice", "new", "bots", "bot4"):
                r = asyncio.run(say(srv, frontend("bot"), "HELO %s" % name))
                self.assertTrue(r.startswith("ERR RESERVED"), (method, name, r))
            self.assertNotIn("alice", srv.players)

    def test_a_suspended_bot_stays_out(self):
        srv = server("none")
        asyncio.run(say(srv, frontend("bot"), "HELO bot"))
        srv.suspend_player("bot")
        self.assertTrue(asyncio.run(say(srv, frontend("bot"), "HELO bot")).startswith("ERR SUSPENDED"))

    def test_a_person_who_had_the_name_keeps_it(self):
        srv = server("none")
        srv.accounts.player("bot2", create=True)        # an account from before the names were reserved
        self.assertTrue(asyncio.run(say(srv, public(), "HELO bot2")).startswith("OK"))
        self.assertTrue(asyncio.run(say(srv, frontend("bot"), "HELO bot2")).startswith("ERR RESERVED"))
        self.assertFalse(bots.is_bot(srv.players["bot2"]))

    def test_the_flag_is_kept(self):
        d = tempfile.mkdtemp(prefix="pnbot")
        try:
            db = os.path.join(d, "p.db")
            srv = server("none", db=db)
            asyncio.run(say(srv, frontend("bot"), "HELO bot3"))
            srv2 = Server(os.path.join(ROOT, "plugins"), "test", db, "none")
            srv2.load_state()
            self.assertTrue(bots.is_bot(srv2.players["bot3"]))
            self.assertTrue(asyncio.run(say(srv2, public(), "HELO bot3")).startswith("ERR RESERVED"))
        finally:
            shutil.rmtree(d, True)

    def test_frontend_helpers(self):
        lines = ["TAB 4 rummytile/1 standard 3/4 open amy,bot,-,bot1 turn=60 bot=1,3", "TAB 5 reversi/1 standard 1/2 "
                 "open bob,- turn=60", "OK"]
        self.assertEqual(botflag.bot_seats(lines), {"4": {1, 3}, "5": set()})
        self.assertEqual(botflag.bot_names(lines), {"4": {"bot", "bot1"}, "5": set()})
        self.assertEqual(botflag.next_name(["amy", "Bot", "bot1"]), "bot2")
        self.assertEqual(botflag.label("bot1", {"bot1"}), "bot1 (bot)")
        self.assertEqual(botflag.label("amy", {"bot1"}), "amy")


# ---- the strategies against a random player, in this process ------------------------------------

async def local_game(game, rules, n, seed, styles):
    """A game run by its plugin with a strategy per seat ("smart" or "random") -> the END result."""
    g = play.LocalGame(game, rules, n, seed)
    tables = [g.table(s, random.Random(seed * 10 + s)) for s in range(n)]
    module = games.get(g.key)
    for _ in range(4000):
        if g.result:
            return g.result
        moved = False
        for s, t in enumerate(tables):
            v = await t.view()
            if g.result:
                return g.result
            if module.my_turn(v, s):
                key = v.key()
                await play.take_turn(module, t, v, styles[s])
                if (await t.view()).key() == key and not g.result:
                    raise AssertionError("%s: seat %d (%s) did nothing: %s" % (game, s, styles[s], v.lines))
                moved = True
        if not moved:
            raise AssertionError("%s: nobody can move" % game)
    raise AssertionError("%s: no end after 4000 rounds" % game)


@slow
class Strategies(unittest.TestCase):
    """Each game's strategy beats a random player most of the time (seeded, both seats)."""

    def setUp(self):
        self.cap = gridwords.TIME_CAP
        gridwords.TIME_CAP = 0.3

    def tearDown(self):
        gridwords.TIME_CAP = self.cap

    def test_beats_random(self):
        for game, rules in GAMES + [("mancala", "oware"), ("navalcombat", "batnav")]:
            k = 2 if game == "gridwords" else 4
            score = 0.0
            for seed in range(k):
                me = seed % 2
                styles = ["smart", "random"] if me == 0 else ["random", "smart"]
                score += play.won(asyncio.run(local_game(game, rules, 2, 100 + seed, styles)), me)
            self.assertGreaterEqual(score, k - 1, "%s %s: the bot scored %.1f of %d" % (game, rules, score, k))

    def test_bots_against_bots(self):
        """Three bots and a random player, and every seat a bot: the games end."""
        for game, rules in (("rummytile", "standard"), ("cannonfire", "classic"), ("dotsandboxes", "standard"),
                            ("gridwords", "english")):
            end = asyncio.run(local_game(game, rules, 4, 7, ["random", "smart", "smart", "smart"]))
            self.assertRegex(end, r"^(win|draw) ")
        for game, rules in GAMES:
            self.assertRegex(asyncio.run(local_game(game, rules, 2, 3, ["smart", "smart"])), r"^(win|draw) ", game)


# ---- the bot as a process, against a server --------------------------------------------------------

class Native:
    """A player on the public port (sign-in none)."""

    @classmethod
    async def connect(cls, port, name):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"PARLORNET/1\r\n")                # the client speaks first: PARLORNET/1, whose ending sets the line ending
        c = CoreClient(r, w)
        await c.readline()
        _, final = await c.req("HELO %s" % name)
        assert final.startswith("OK"), final
        return c


async def until(pred, what, timeout=20, step=0.1):
    end = time.time() + timeout
    while True:
        r = await pred()
        if r:
            return r
        if time.time() > end:
            raise AssertionError("timed out waiting for %s" % what)
        await asyncio.sleep(step)


async def table_of(c, tid):
    data, _ = await c.req("LST")
    return next((t for t in parse_tables(data) if t["id"] == tid), None)


async def events(c, tid):
    data, _ = await c.req("SIN %s 0" % tid)
    return [l.split(" ", 3)[3] for l in data if l.startswith("EVT ")]


@slow
class WithAServer(unittest.TestCase):
    """A core (sign-in none), the web and telnet adapters, and the bot (python -m bot) with no pause before
    its turns."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.web_port, cls.tel_port = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("web", "telnet", "bot")
        args = stack_args(cls.core, cls.fe, cls.keys)[1:] + ["--auth", "none", "--turn-min", "1", "--sweep", "0.3"]
        cls.procs = [cls.spawn("tests.matrix_core", *args)]
        cls.procs.append(cls.spawn(*adapter_args("adapters.web", cls.web_port, cls.fe, cls.keys, "web")))
        cls.procs.append(cls.spawn(*adapter_args("adapters.telnet", cls.tel_port, cls.fe, cls.keys, "telnet")))
        cls.out = []
        cls.bot = cls.spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                            "--think", "0,0", "--pace", "0", "--poll", "0.3", "--gridwords-secs", "0.3")
        cls.procs.append(cls.bot)
        threading.Thread(target=cls.drain, args=(cls.bot,), daemon=True).start()

    @staticmethod
    def spawn(*args):
        p = subprocess.Popen([sys.executable, "-m"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT if args[0] == "bot" else subprocess.PIPE)
        p.first = p.stdout.readline().decode("latin-1")
        return p

    @classmethod
    def drain(cls, p):
        for line in iter(p.stdout.readline, b""):
            cls.out.append(line.decode("latin-1").rstrip())

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            if p.stderr:
                p.stderr.close()

    def bot_said(self):
        """What the bot logged: nothing but sign-in news is expected (a refused move or a failure is a fault)."""
        return [l for l in self.out if l.strip() and not l.startswith("bot: signed in")]

    def run_async(self, coro, timeout=180):
        return asyncio.run(asyncio.wait_for(coro, timeout))

    async def vs_bots(self, name, game, rules, players=2, bots_=("bot",), extra="turn=1h"):
        """A person opens a table, sits, invites the bots and plays at random to the end. -> (table id, events,
        the views' BOT line seen, LST's bot= seats seen)."""
        c = await Native.connect(self.core, name)
        try:
            _, final = await c.req("NEW %s %s players=%d %s" % (game, rules, players, extra))
            self.assertTrue(final.startswith("OK"), final)
            tid = final.split()[1]
            self.assertEqual((await c.req("JON %s" % tid))[1], "OK 0")
            for b in bots_:
                self.assertEqual((await c.req("INV %s %s" % (tid, b)))[1], "OK")
            t = await until(lambda: self.playing(c, tid), "%s: the bots to sit down" % game)
            self.assertEqual(sorted(n.lower() for n in t["names"][1:]), sorted(bots_))
            data, _ = await c.req("LST")
            flags = botflag.bot_seats(data)[tid]
            vue, _ = await c.req("VUE %s" % tid)
            bot_line = next((l for l in vue if l.startswith("BOT ")), None)
            why = await play.run(play.NetTable(c, tid, 0, 0, random.Random(5), wait_secs=5), (0, 0), "random",
                                 log=self.fail)
            self.assertEqual(why, "over", game)
            if len(bots_) == 1:     # its last words (a table of several bots: each says them)
                await until(lambda: self.said(c, tid, "Good game!"), "%s: the bot's last words" % game)
            return tid, await events(c, tid), bot_line, flags
        finally:
            c.close()

    async def playing(self, c, tid):
        t = await table_of(c, tid)
        return t if t and t["status"] == "playing" else None

    async def said(self, c, tid, text):
        return any(e.startswith("CHT ") and e.endswith(text) for e in await events(c, tid))

    def test_a_whole_game_of_every_game(self):
        started = time.time()

        async def all_games():
            return await asyncio.gather(*(self.vs_bots("p%d" % i, g, r) for i, (g, r) in enumerate(GAMES)))
        results = self.run_async(all_games())
        for (game, _), (tid, evts, bot_line, flags) in zip(GAMES, results):
            self.assertEqual(bot_line, "BOT 1", game)        # the core flags the bot's seat in VUE and LST
            self.assertEqual(flags, {1}, game)
            self.assertRegex(evts[-1] if not evts[-1].startswith("CHT") else evts[-2], r"^END (win|draw) ", game)
            chat = [e for e in evts if e.startswith("CHT ")]
            self.assertEqual(chat, ["CHT 1 " + service.GREETING, "CHT 1 " + service.FAREWELL], game)
        self.assertLess(time.time() - started, 120)
        self.assertEqual(self.bot_said(), [])

    def test_several_bots_at_one_table(self):
        async def both():
            return await asyncio.gather(
                self.vs_bots("rummy4", "rummytile", "standard", 4, ("bot", "bot1", "bot2")),
                self.vs_bots("cannon4", "cannonfire", "classic", 4, ("bot", "bot1", "bot2")))
        for tid, evts, bot_line, flags in self.run_async(both()):
            self.assertEqual(bot_line, "BOT 1 2 3")
            self.assertEqual(flags, {1, 2, 3})
            self.assertTrue(any(e.startswith("END ") for e in evts))
        self.assertEqual(self.bot_said(), [])

    def test_joins_only_when_invited_and_leaves_with_the_people(self):
        async def go():
            c = await Native.connect(self.core, "loner")
            try:
                tid = (await c.req("NEW dotsandboxes standard players=3"))[1].split()[1]
                await c.req("JON %s" % tid)
                await asyncio.sleep(1.5)                    # several looks for invitations: nobody comes
                self.assertEqual((await table_of(c, tid))["names"], ["loner"])
                await c.req("INV %s bot" % tid)
                await until(lambda: self.seated(c, tid, "bot"), "the bot to sit down")
                await c.req("LVE %s" % tid)                 # the person stands up: the bot follows
                await until(lambda: self.gone(c, tid), "the table to go")
            finally:
                c.close()
        self.run_async(go())

    async def seated(self, c, tid, name):
        t = await table_of(c, tid)
        return t and name in t["names"]

    async def gone(self, c, tid):
        return await table_of(c, tid) is None

    def test_the_clock_ends_it_and_the_bot_leaves(self):
        async def go():
            c = await Native.connect(self.core, "sleepy")
            try:
                tid = (await c.req("NEW reversi standard turn=2"))[1].split()[1]
                await c.req("JON %s" % tid)
                await c.req("INV %s bot" % tid)
                await until(lambda: self.playing(c, tid), "the game to start")
                # seat 0 never moves: its clock runs out, the game ends, the bot says goodbye and goes
                await until(lambda: self.said(c, tid, service.FAREWELL), "the bot's last words", 15)
                self.assertIn("END timeout 0", await events(c, tid))
            finally:
                c.close()
        self.run_async(go())

    def test_a_declined_invitation_over_the_limit(self):
        class Fake:
            def __init__(self):
                self.lines = []

            async def req(self, text):
                self.lines.append(text)
                return [], "OK"
        b = service.Bot("127.0.0.1", 1, "k", cap=0, log=lambda text: None)
        f = Fake()
        asyncio.run(b.accept("bot", f, "7"))
        self.assertEqual(f.lines, ["UNV 7 bot"])

    def test_invited_from_the_web(self):
        b = Browser(self.web_port)
        st, _, _, body = b.login("webby")
        self.assertIn("Play the bot", body)
        form = next(f for f in body.split("<li><form")[1:] if 'name="g" value="reversi/1"' in f)
        q = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))
        q.update(turn="1d", vis="public", bot="1")
        st, loc, _, body = b.get("/new.htm?" + urlencode(q))
        self.assertEqual(st, 302, body[-400:])

        async def check():
            c = await Native.connect(self.core, "webwatch")
            try:
                data, _ = await c.req("LST")
                tid = next(t["id"] for t in parse_tables(data) if "webby" in t["names"])
                t = await until(lambda: self.playing(c, tid), "the bot to sit down")
                self.assertEqual(t["names"], ["webby", "bot"])
            finally:
                c.close()
        self.run_async(check())
        st, _, _, body = b.get("/lobby.htm")
        self.assertIn(">webby</a>, <a href=\"/player.htm?who=bot\">bot</a> (bot)", body)
        # a table without the bot: the table page offers it, and its link invites it
        form = next(f for f in body.split("<li><form")[1:] if 'name="g" value="dotsandboxes/1"' in f)
        q = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form))
        q.update(turn="1d", vis="public", np="3")
        st, loc, _, body = b.get("/new.htm?" + urlencode(q))
        st, loc, _, body = b.go(loc)
        link = re.search(r'<a href="([^"]+)">Invite the bot</a>', body)
        self.assertIsNotNone(link, body[-1500:])
        self.assertRegex(link.group(1).replace("&amp;", "&"), r"who=bot(&|$)")
        st, loc, _, body = b.go(link.group(1).replace("&amp;", "&"))
        self.assertEqual(st, 302, body[-400:])
        tid = re.search(r"t=(\d+)", loc).group(1)

        async def seated():
            c = await Native.connect(self.core, "webwatch")
            try:
                await until(lambda: self.seated(c, tid, "bot"), "the bot to sit down")
            finally:
                c.close()
        self.run_async(seated())
        st, loc, _, body = b.go(loc)
        self.assertRegex(body.replace("&amp;", "&"), r'who=bot1(&|")')     # the next seat: the next bot name

    def test_invited_from_telnet(self):
        c = Client(self.tel_port, enc="latin-1", display="4")
        try:
            c.until("Login")
            c.send("new\r\n")
            c.until("Name you want")
            c.send("telly\r\n")
            c.until("Press Enter")
            c.send("\r\n")
            c.until("Your tables")
            c.send("new reversi bot 5m\r\n")                # a table with the bot in the other seat

            async def check(game, names):
                o = await Native.connect(self.core, "telwatch")
                try:
                    async def found():
                        data, _ = await o.req("LST")
                        return next((t for t in parse_tables(data) if "telly" in t["names"] and t["key"] == game
                                     and t["names"] == names), None)
                    await until(found, "%s for %s" % (names, game))
                finally:
                    o.close()
            self.run_async(check("reversi/1", ["telly", "bot"]))
            c.send("menu\r\n")
            c.until("Your tables")
            c.send("new dotsandboxes standard 3p 5m\r\n")
            c.until("Waiting for an opponent")
            c.send("bot\r\n")                               # at the table: invite one
            c.until("Invited bot")
            self.run_async(check("dotsandboxes/1", ["telly", "bot"]))
        finally:
            c.close()

    def test_the_waiting_box_offers_it(self):
        c = Client(self.tel_port, display="1")
        try:
            c.until("Login")
            c.send("new\r\n")
            c.until("Name you want")
            c.send("fully\r\n")
            c.until("Press Enter")
            c.send("\r\n")
            c.until("Your tables", screen=True)
            c.send("new mancala kalah 5m\r\n")
            c.until("Type bot for a bot", screen=True)
            for ch in "bot\r":
                c.send(ch)
            c.until("Invited bot", screen=True)
        finally:
            c.close()

    def test_memory(self):
        """The bot's resident memory after Grid Words (the word list loaded) and a few games."""
        self.run_async(self.vs_bots("memo", "gridwords", "english"))
        rss = int(subprocess.run(["ps", "-o", "rss=", "-p", str(self.bot.pid)], capture_output=True,
                                 text=True).stdout.split()[0]) // 1024
        # 29-36 MB in python:3.13-slim on Linux (docs/bot.md); a macOS host's ps counts more shared pages
        self.assertLess(rss, 45, "the bot holds %d MB" % rss)


if __name__ == "__main__":
    unittest.main()
