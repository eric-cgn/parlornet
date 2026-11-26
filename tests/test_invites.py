"""Invitations end to end (docs/design/invites.md): the core's model (WHO, the reasons on UNINVITE, expiry,
an inviter who stands up), and each frontend's own way to invite and to be invited: IOS match / accept /
decline (Reversi The Eclipse's MATCH offer), FIBS invite / join / decline, Outland's challenge and its
answer, telnet's invitation list, decline and invite, the web's Join and Decline. The bot runs as a
process and is invited from IOS, FIBS and Outland, where it shows in the player lists."""
import os
import re
import struct
import subprocess
import sys
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import procs
from test_adapters import Client, WebPlayers
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
from test_ios import Eclipse, legal, scanf
from test_fibs import MacFIBS
from test_outland import FAST, Gateway

sys.path.insert(0, ROOT)
from adapters import outland as O  # noqa: E402
from adapters.common import describe_invite_event, parse_players, uninvite_of  # noqa: E402
from adapters import botflag  # noqa: E402
from parlord.core import Limits, Server, Session  # noqa: E402
from speed import slow  # noqa: E402


class FakeWriter:
    def __init__(self):
        self.data = b""

    def get_extra_info(self, key):
        return ("127.0.0.1", 1) if key == "peername" else None

    def write(self, b):
        self.data += b

    async def drain(self):
        pass

    def close(self):
        pass


class CoreBase(unittest.IsolatedAsyncioTestCase):
    """The core in-process (sign-in none)."""

    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        await self.srv.load_plugins()

    async def asyncTearDown(self):
        for p in self.srv.plugins.values():
            p.stop()
            if p.proc:
                await p.proc.wait()

    async def player(self, name):
        sess = Session(FakeWriter())
        r = await self.srv.dispatch(sess, "HELO %s" % name)
        self.assertEqual(r, ["OK -"])
        return sess

    async def req(self, sess, line):
        return await self.srv.dispatch(sess, line)

    def kinds(self, tid):
        return [text for _, text in self.srv.tables[tid].events if "INVITE" in text]


class CoreModel(CoreBase):
    """What each way an invitation ends says, and WHO."""

    async def test_who_lists_the_players_connected_and_flags_bots(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        self.srv.players["bo"].bot = True           # as parlord/bots.py flags the bot's accounts
        self.assertEqual(await self.req(amy, "WHO"), ["PLR amy idle=0", "PLR bo idle=0 bot", "OK"])
        self.assertEqual(parse_players(["PLR amy idle=0", "PLR bo idle=0 bot", "OK"]), [("amy", False), ("bo", True)])
        self.assertEqual(botflag.listed([("amy", False), ("bot", True), ("bot1", True)]), ["amy", "bot"])
        await self.req(bo, "OUT")
        self.assertEqual(await self.req(amy, "WHO"), ["PLR amy idle=0", "OK"])
        self.assertEqual((await self.req(Session(FakeWriter()), "WHO"))[0][:9], "ERR NOHEL")

    async def test_every_way_an_invitation_ends_says_why(self):
        amy, bo, cy, di = [await self.player(n) for n in ("amy", "bo", "cy", "di")]
        tid = (await self.req(amy, "NEW dotsandboxes standard players=3"))[0].split()[1]
        await self.req(amy, "JON %s" % tid)
        await self.req(cy, "JON %s" % tid)
        for line in ("INV %s bo" % tid, "UNV %s bo" % tid,            # withdrawn by the inviter
                     "INV %s bo" % tid):
            self.assertEqual(await self.req(amy, line), ["OK"])
        self.assertEqual(await self.req(bo, "UNV %s bo" % tid), ["OK"])       # declined by bo
        self.assertEqual(await self.req(amy, "INV %s di" % tid), ["OK"])
        self.assertEqual(await self.req(cy, "INV %s bo" % tid), ["OK"])
        await self.req(amy, "LVE %s" % tid)                                   # amy's own invitations end
        self.assertEqual(await self.req(di, "IVL"), ["OK"])
        self.assertEqual(len(await self.req(bo, "IVL")), 2)                   # cy's still stands
        self.srv.tables[tid].invites["bo"]["at"] -= Limits.invite_secs + 1   # an hour passes
        await self.srv.sweep()
        self.assertEqual(await self.req(bo, "IVL"), ["OK"])
        self.assertEqual(self.kinds(tid), ["INVITE - bo", "UNINVITE - bo withdrawn", "INVITE - bo",
                                           "UNINVITE - bo declined", "INVITE - di", "INVITE - bo",
                                           "UNINVITE - di left", "UNINVITE - bo expired"])
        # the game starts: the rest end
        await self.req(cy, "INV %s di" % tid)
        await self.req(cy, "INV %s amy" % tid)
        await self.req(bo, "JON %s" % tid)
        self.assertEqual(await self.req(di, "JON %s" % tid), ["OK 2"])
        self.assertEqual(self.kinds(tid)[-1], "UNINVITE - amy started")
        self.assertEqual(self.srv.store.load_invites(tid), {})
        # the frontends' words for each, and an older core's UNINVITE without a reason
        self.assertEqual(describe_invite_event("UNINVITE - bo declined"), "bo declined the invitation")
        self.assertEqual(describe_invite_event("UNINVITE - bo expired"), "bo's invitation expired")
        self.assertEqual(describe_invite_event("UNINVITE 1 bo"), "bo is no longer invited")
        self.assertEqual(uninvite_of("UNINVITE 1 bo left"), ("bo", "left"))
        self.assertIsNone(uninvite_of("JOIN 1 bo"))


    async def test_invitations_out_are_at_most_twice_the_free_seats(self):
        amy, bo, cy, di = [await self.player(n) for n in ("amy", "bo", "cy", "di")]
        tid = (await self.req(amy, "NEW reversi standard"))[0].split()[1]
        await self.req(amy, "JON %s" % tid)                                   # one free seat: two invitations
        self.assertEqual(await self.req(amy, "INV %s bo" % tid), ["OK"])
        self.assertEqual(await self.req(amy, "INV %s cy" % tid), ["OK"])
        self.assertEqual(await self.req(amy, "INV %s di" % tid),
                         ["ERR LIMIT this table has 2 invitations out, twice its free seats"])
        self.assertEqual(await self.req(amy, "INV %s bo" % tid), ["OK"])      # renewing one is not a new one
        await self.req(cy, "UNV %s cy" % tid)                                 # one ends: room for another
        self.assertEqual(await self.req(amy, "INV %s di" % tid), ["OK"])
        big = (await self.req(amy, "NEW cannonfire classic players=4"))[0].split()[1]
        await self.req(amy, "JON %s" % big)                                   # three free seats: six
        for name in ("bo", "cy", "di"):
            self.assertEqual(await self.req(amy, "INV %s %s" % (big, name)), ["OK"])


class Presence(CoreBase):
    """Who is online (docs/accounts.md): signed in and active within presence.idle; looking is not activity;
    the bot is online while connected; an invitation to a player not online is kept (OK offline)."""

    async def test_the_idle_cutoff_and_what_counts(self):
        clock = [1000.0]
        real = time.time
        import parlord.core as core
        core.time.time = lambda: clock[0]           # a fake clock for the core
        self.addCleanup(setattr, core.time, "time", real)
        amy, bo = await self.player("amy"), await self.player("bo")
        self.srv.set_setting("presence.idle", "600")
        self.assertEqual(await self.req(amy, "WHO"), ["PLR amy idle=0", "PLR bo idle=0", "OK"])
        clock[0] += 400
        for line in ("LST", "IVL", "WHO", "SIN 1", "GAM", "MOTD", "PRF LST"):      # looking: not activity
            await self.req(bo, line)
        await self.req(amy, "ACT")
        self.assertEqual(await self.req(amy, "WHO"), ["PLR amy idle=0", "PLR bo idle=400", "OK"])
        clock[0] += 201                                                            # bo: 601 s idle
        self.assertEqual(await self.req(amy, "WHO"), ["PLR amy idle=201", "OK"])
        tid = (await self.req(amy, "NEW reversi standard"))[0].split()[1]
        await self.req(amy, "JON %s" % tid)
        self.assertEqual(await self.req(amy, "INV %s bo" % tid), ["OK offline"])     # kept: it waits for bo
        self.assertEqual(len(await self.req(bo, "IVL")), 2)
        await self.req(bo, "CHT %s hi" % tid)                                       # doing: back online
        self.assertIn("PLR bo idle=0", await self.req(amy, "WHO"))
        self.assertEqual(await self.req(amy, "INV %s bo" % tid), ["OK"])
        # a bot: online while connected, whatever it last did
        bot = await self.player("robo")
        self.srv.players["robo"].bot = True       # as parlord/bots.py flags the bot's accounts
        clock[0] += 10000
        self.assertEqual(await self.req(amy, "WHO"), ["PLR robo idle=0 bot", "OK"])
        await self.req(bot, "OUT")
        self.assertEqual(await self.req(amy, "WHO"), ["OK"])
        # the setting: from the admin menus, checked
        from parlord.admin import SETTINGS
        self.assertEqual(SETTINGS["presence.idle"]("30m"), "1800")
        with self.assertRaises(ValueError):
            SETTINGS["presence.idle"]("10")


@slow
class Stack(WebPlayers, unittest.TestCase):
    """A core (sign-in none), the web, telnet, IOS, FIBS and Outland adapters, and the bot."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe = free_port(), free_port()
        cls.web, cls.tel, cls.ios, cls.fibs, cls.out = [free_port() for _ in range(5)]
        cls.keys = make_keys("web", "telnet", "ios", "fibs", "outland", "bot")
        args = stack_args(cls.core, cls.fe, cls.keys)[1:] + ["--auth", "none", "--sweep", "0.3"]
        cls.procs = procs.owned(cls)
        cls.procs.append(cls.spawn("tests.matrix_core", *args))
        cls.procs.append(cls.spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))
        cls.procs.append(cls.spawn(*adapter_args("adapters.telnet", cls.tel, cls.fe, cls.keys, "telnet")))
        cls.procs.append(cls.spawn(*adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios"),
                                   "--prompt-wait", "2"))
        cls.procs.append(cls.spawn(*adapter_args("adapters.fibs", cls.fibs, cls.fe, cls.keys, "fibs"),
                                   "--poll", "0.25", "--prompt-wait", "2"))
        cls.procs.append(cls.spawn(*adapter_args("adapters.outland", cls.out, cls.fe, cls.keys, "outland"), *FAST))
        cls.bot_out = []
        cls.bot = procs.spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                              "--think", "0,0", "--pace", "0", "--poll", "0.3", stderr=subprocess.STDOUT,
                              out=cls.bot_out)
        cls.procs.append(cls.bot)
        cls.wait_for_bot()

    @staticmethod
    def spawn(*args):
        return procs.spawn(*args, stderr=subprocess.STDOUT if args[0] == "bot" else subprocess.DEVNULL)

    @classmethod
    def wait_for_bot(cls):
        """Until the core lists the bot as online (its lobby connection signed in)."""
        import socket
        end = time.time() + 20
        while time.time() < end:
            s = socket.create_connection(("127.0.0.1", cls.core), timeout=5)
            s.sendall(b"PARLORNET/1\r\n")      # the client speaks first: PARLORNET/1, then the greeting
            f = s.makefile("rb")
            f.readline()
            s.sendall(b"HELO botcheck\r\nWHO\r\n")
            lines = []
            while not (len(lines) > 1 and lines[-1].startswith("OK")):
                lines.append(f.readline().decode().strip())
            s.close()
            if any(l.startswith("PLR bot ") and l.endswith(" bot") for l in lines):
                return
            time.sleep(0.3)
        raise AssertionError("the bot did not sign in")

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def eclipse(self, name):
        c = Eclipse(self.ios)
        ok, asked, _ = c.login(name)
        self.assertTrue(ok)
        self.addCleanup(c.close)
        return c

    def web_table(self, name, game="reversi%2F1", vis="public"):
        self.web_login(name)
        loc = self.web_get("/new.htm?g=%s&turn=1h&vis=%s&n=%s" % (game, vis, name))[1]
        return parse_qs(urlsplit(loc).query)["t"][0]

    def web_invite(self, name, tid, who):
        st, loc, body = self.web_get("/invite.htm?t=%s&who=%s&n=%s" % (tid, who, name))
        self.assertEqual(st, 302, body[-400:])

    # ---- IOS ------------------------------------------------------------------------------------
    def test_ios_eclipse_matches_the_bot(self):
        """The bot is a row of Eclipse's who list; its Match button (match ... bot ...) gives a game with it,
        and the bot answers moves."""
        c = self.eclipse("ecla")
        rows, _ = c.who()
        self.assertEqual(rows.get("bot"), "---")
        c.send("fi bot")
        self.assertIn("server's bot", c.wait(lambda l: "bot" in l and "server" in l))
        c.send("match  20 0 120 bot 20 0 120 b")             # Eclipse's form: I play black
        c.wait(lambda l: "challenge to bot is pending" in l)
        g = c.game(False, timeout=15)
        self.assertEqual((g["black"], g["white"]), ("ecla", "bot"))
        p = c.pb(lambda p: p["turn"] == "Y")
        c.send("PM %s +0.00     1.00" % legal(p["cells"], "*")[0])
        p = c.pb(lambda p: p["colour"] == "*" and p["turn"] == "Y" and p["move_no"] == 2, timeout=15)
        self.assertEqual(p["white"], "bot")                     # the bot moved; my turn again
        c.send("resign")
        c.game(True)

    def test_ios_player_invited_from_the_web_accepts_and_declines(self):
        c = self.eclipse("iris")
        c.who()
        tid = self.web_table("wanda")
        self.web_invite("wanda", tid, "iris")
        m = c.wait(lambda l: l.startswith("<ios> MATCH"))
        f = scanf(m[m.index(")") + 1:], "%ld %ld %ld %s %s %ld %ld %ld %s")
        self.assertEqual((f[0], f[3], f[4], f[8]), (60, "FROM", "wanda", "black"))     # 1h a turn; wanda at seat 0
        self.assertRegex(c.raw_line()[15:], r"^-?\d+ \.\. -?\d+ -?\d+ -?\d+ \.\. -?\d+")   # read at once
        c.send("accept wanda")
        g = c.game(False)
        self.assertEqual((g["black"], g["white"]), ("wanda", "iris"))
        c.pb()
        c.send("resign")
        c.game(True)
        # a second invitation, declined: the web table's log says so
        tid = self.web_table("wanda")
        self.web_invite("wanda", tid, "iris")
        c.wait(lambda l: l.startswith("<ios> MATCH"))
        c.send("decline wanda")
        c.wait(lambda l: "You decline the match with wanda" in l)
        body = self.web_get("/table.htm?t=%s&n=wanda" % tid)[2]
        self.assertIn("iris declined the invitation", body)
        # another game's invitation is a line of text
        tid = self.web_table("wanda", "mancala%2F1")
        self.web_invite("wanda", tid, "iris")
        self.assertIn("table %s" % tid, c.wait(lambda l: "invites you to Mancala" in l))

    def test_ios_challenge_reaches_a_web_player_who_declines(self):
        c = self.eclipse("ivan")
        self.web_login("wilf")
        c.send("match wilf 10 0 0 white")
        c.wait(lambda l: "challenge to wilf is pending" in l)
        lobby = self.web_get("/lobby.htm?n=wilf")[2]
        self.assertIn("Invitations for you", lobby)
        tid = re.search(r"/uninvite\.htm\?[^\"]*\bt=(\d+)", lobby).group(1)
        self.assertEqual(self.web_get("/uninvite.htm?t=%s&who=wilf&n=wilf" % tid)[0], 302)
        c.wait(lambda l: "wilf declines the match" in l)
        c.send("match nosuchname")
        c.wait(lambda l: "There is no player called nosuchname here." in l)

    # ---- FIBS -----------------------------------------------------------------------------------
    def test_fibs_who_lists_the_bot_and_invite_plays_it(self):
        mac = MacFIBS(self.fibs)
        self.addCleanup(mac.close)
        mac.login("fern", "")
        mac.send("rawwho")
        row = mac.wait(lambda l: l.startswith("who: ") and l.split()[3] == "bot")
        self.assertEqual(MacFIBS.classify(row), "rawwho")
        self.assertEqual(row.split()[2], "R-")                  # ready to be invited
        mac.send("invite bot 1")
        mac.wait(lambda l: l.startswith("** You invited bot to a 1 point match."))
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: "has joined you" in l, timeout=15)), "match-start")
        mac.send("leave")

    def test_fibs_player_invited_from_the_web_joins_and_declines(self):
        mac = MacFIBS(self.fibs)
        self.addCleanup(mac.close)
        mac.login("finn", "")
        tid = self.web_table("wade", "backgammon%2F1")
        self.web_invite("wade", tid, "finn")
        inv = mac.wait(lambda l: "wants to play" in l)
        self.assertEqual(inv, "wade wants to play a 1 point match with you.")
        self.assertEqual(MacFIBS.classify(inv), "invite")
        self.assertEqual(mac.wait(lambda l: l.startswith("Type")), "Type 'join wade' to accept.")
        mac.send("join wade")
        self.assertEqual(MacFIBS.classify(mac.wait(lambda l: l.startswith("** You are now playing"))),
                         "match-start")
        self.web_get("/resign.htm?t=%s&sure=1&n=wade" % tid)
        tid = self.web_table("wade", "backgammon%2F1")
        self.web_invite("wade", tid, "finn")
        mac.wait(lambda l: "wade wants to play" in l)
        mac.send("decline wade")
        mac.wait(lambda l: l.startswith("** You decline the invitation from wade."))
        self.assertIn("finn declined the invitation", self.web_get("/table.htm?t=%s&n=wade" % tid)[2])

    # ---- Outland --------------------------------------------------------------------------------
    def outland(self, name):
        g = Gateway(self.out)
        self.addCleanup(g.close)
        g.login(name, "")
        g.open_backgammon()
        return g, g.lobby()

    def test_outland_challenges_the_bot(self):
        g, players = self.outland("olga")
        self.assertIn("bot", players)
        self.assertEqual(players["bot"][1], 1)                 # available
        ch = O.record(O.C_CHALLENGE)
        ch[2] = players["bot"][0]
        struct.pack_into(">H", ch, 4, 1)
        g.send_rec(ch)
        got = g.rec(O.S_RESPONSE, timeout=15)
        self.assertEqual((got[2], got[6], got[7]), (players["bot"][0], 1, 0))     # accepted; I am Red
        g.send_rec(O.record(O.C_START, 0))
        g.rec(O.S_ROLL, timeout=15)                            # the opening roll: the game runs

    def test_outland_player_invited_from_the_web(self):
        g, _ = self.outland("otto")
        tid = self.web_table("wren", "backgammon%2F1")
        self.web_invite("wren", tid, "otto")
        inv = g.rec(O.S_CHALLENGE, timeout=10)
        frm = inv[1]
        resp = bytearray(inv)
        resp[0], resp[6], resp[7] = O.C_RESPONSE, 1, 1         # accepted, as White
        g.send_rec(resp)
        g.send_rec(O.record(O.C_START, 1))
        r = g.rec(O.S_ROLL, timeout=15)
        self.assertIn(frm, (r[O.O_MOVER], g.pid))
        self.web_get("/resign.htm?t=%s&sure=1&n=wren" % tid)
        # declined: the web table's log says so
        tid = self.web_table("wren", "backgammon%2F1")
        self.web_invite("wren", tid, "otto")
        g.send_rec(O.record(O.C_RELOBBY))
        inv = g.rec(O.S_CHALLENGE, timeout=10)
        resp = bytearray(inv)
        resp[0], resp[6], resp[7] = O.C_RESPONSE, 0, 0
        g.send_rec(resp)
        end = time.time() + 10
        while "otto declined the invitation" not in self.web_get("/table.htm?t=%s&n=wren" % tid)[2]:
            self.assertLess(time.time(), end)
            time.sleep(0.3)

    # ---- telnet ---------------------------------------------------------------------------------
    def telnet(self, name):
        c = Client(self.tel, enc="latin-1", display="4")
        self.addCleanup(c.s.close)
        c.until("Login")
        c.send("new\r\n")                                     # a new account (sign-in none: the name alone)
        c.until("Name you want")
        c.send("%s\r\n" % name)
        c.until("Press Enter")
        c.send("\r\n")
        c.until("Your tables")
        return c

    def test_telnet_lists_declines_and_sends_invitations(self):
        self.web_login("wyn")
        c = self.telnet("tess")
        tid = self.web_table("wyn")
        self.web_invite("wyn", tid, "tess")
        c.send("list\r\n")
        c.until("Invitations for you:")
        c.until("from wyn")
        c.until("wyn invites you to play")                  # asked once at the prompt: N declines it
        c.send("n\r\n")
        c.until("You declined wyn's invitation to table %s." % tid)
        self.assertIn("tess declined the invitation", self.web_get("/table.htm?t=%s&n=wyn" % tid)[2])
        tid = self.web_table("wyn")
        self.web_invite("wyn", tid, "tess")
        c.send("join %s\r\n" % tid)                             # join accepts it: the held seat
        c.until("O tess")                                      # seated: the game started
        c.send("/leave\r\n")
        c.until("Your tables")
        # at a table of its own, telnet invites by name; the web lobby shows it
        c.send("open mancala kalah 5m\r\n")
        c.until("Waiting for a second player")
        c.send("/invite wyn\r\n")
        c.until("You invited wyn to table")
        self.assertIn("Invitations for you", self.web_get("/lobby.htm?n=wyn")[2])
        c.send("/uninvite wyn\r\n")
        c.until("You withdrew wyn's invitation.")


@slow
class Idle(WebPlayers, unittest.TestCase):
    """The idle cutoff through the frontends, with presence.idle at 4 seconds (the settings file) and the
    adapters reporting typing at once: an idle player leaves the who lists with each protocol's own
    departure notice and comes back with its arrival notice when they type; a web page's own polls do
    not keep its player online."""
    IDLE = 4

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        settings = os.path.join(cls.tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("presence.idle=%d\n" % cls.IDLE)
        cls.core, cls.fe, cls.web, cls.ios, cls.fibs = [free_port() for _ in range(5)]
        cls.keys = make_keys("web", "ios", "fibs")
        env = dict(os.environ, PARLOR_ACT_EVERY="0")
        args = stack_args(cls.core, cls.fe, cls.keys)[1:] + ["--auth", "none", "--settings-file", settings]
        cls.procs = procs.owned(cls)
        cls.procs.append(Stack.spawn("tests.matrix_core", *args))
        cls.procs.append(cls.spawn_env(env, *adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))
        cls.procs.append(cls.spawn_env(env, *adapter_args("adapters.ios", cls.ios, cls.fe, cls.keys, "ios")))
        cls.procs.append(cls.spawn_env(env, *adapter_args("adapters.fibs", cls.fibs, cls.fe, cls.keys, "fibs"),
                                       "--poll", "0.25"))

    @staticmethod
    def spawn_env(env, *args):
        return procs.spawn(*args, env=env, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)
        cls.tmp.cleanup()

    def who(self):
        """The core's WHO, from a connection that signs in for it (and is online itself)."""
        import socket
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")         # the client speaks first: PARLORNET/1, then the greeting
        f = s.makefile("rb")
        f.readline()
        s.sendall(b"HELO whocheck\r\nWHO\r\n")
        lines = []
        while not (len(lines) > 1 and lines[-1].startswith("OK")):
            lines.append(f.readline().decode().strip())
        s.close()
        self.last_who = lines
        return {l.split()[1] for l in lines if l.startswith("PLR ")}

    def test_idle_players_leave_the_lists_and_come_back(self):
        c = Eclipse(self.ios)
        self.assertTrue(c.login("ivy")[0])
        self.addCleanup(c.close)
        mac = MacFIBS(self.fibs)
        self.addCleanup(mac.close)
        mac.login("fay", "")
        c.presence("fay", True)
        # fay does nothing: IOS hears she left, FIBS hears ivy left (ivy is idle too)
        c.presence("fay", False, timeout=self.IDLE + 10)
        mac.wait(lambda l: l == "ivy logs out.", timeout=self.IDLE + 10)
        self.assertNotIn("fay", self.who())
        mac.send("who")                                     # fay types: back, once
        c.presence("fay", True, timeout=8)
        c.send("who")                                       # ivy types: FIBS hears it
        mac.wait(lambda l: l == "ivy logs in.", timeout=8)
        rows, _ = c.who()
        self.assertIn("ivy", rows)
        self.assertNotIn("nobody", rows)

    def test_a_web_pages_own_polls_are_not_activity(self):
        self.web_login("wes")
        loc = self.web_get("/new.htm?g=reversi%2F1&turn=1h&n=wes")[1]
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        self.assertIn("wes", self.who())
        end = time.time() + self.IDLE + 1
        while time.time() < end:                            # the script's look for news, the page's reloads
            self.web_get("/seq.htm?t=%s&n=wes" % tid)
            self.web_get("/table.htm?t=%s&a=1&js=1&n=wes" % tid)
            time.sleep(0.5)
        self.assertNotIn("wes", self.who(), self.last_who)
        body = self.web_get("/lobby.htm?n=wes")[2]           # a page wes asked for: online again
        self.assertIn("Online now: ", body)
        self.assertIn("wes", self.who())


if __name__ == "__main__":
    unittest.main()
