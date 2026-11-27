"""The bot's open tables (docs/bot.md, Open tables): one open table of every two-player game, made with the
game's defaults and seated by the bot, and a new one as soon as a player sits at it. A core and the bot run in
this process, so a test can turn a game off, end a table, let it go idle, restart the bot or the core, and
watch every change the core saves: at no moment may a game have two such tables.

    python3.13 -m unittest discover -s tests -p "test_bot_open.py"
"""
import asyncio
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

from test_bot import Native
from test_core import ROOT, free_port, make_keys

sys.path.insert(0, ROOT)
from adapters.common import parse_tables  # noqa: E402
from bot import play, service  # noqa: E402
from parlord import bots, frontends  # noqa: E402
from parlord import core  # noqa: E402
from parlord.core import Server  # noqa: E402
from parlord.commands import Console  # noqa: E402
from parlord.replies import bot_waits  # noqa: E402
from speed import slow  # noqa: E402

TWO = ("reversi/1", "backgammon/1", "checkers/1", "fiveinarow/1", "mancala/1", "navalcombat/1")
MORE = ("dotsandboxes/1", "gridwords/1", "rummytile/1", "cannonfire/1")


async def until(pred, what, timeout=15, step=0.05):
    end = time.time() + timeout
    while True:
        r = pred()
        if asyncio.iscoroutine(r):
            r = await r
        if r:
            return r
        if time.time() > end:
            raise AssertionError("timed out waiting for %s" % what)
        await asyncio.sleep(step)


class Parlor:
    """A core on a database file, with its extension channel (the bot's key) and its public port, in this
    event loop. Every save of a table is checked: no game ever has two of the bot's open tables."""

    def __init__(self, d):
        self.dir, self.db = d, os.path.join(d, "p.db")
        self.keys = make_keys("bot")
        self.fe, self.pub = free_port(), free_port()
        self.doubles = []
        self.srv = None

    async def start(self):
        core.Limits.rate_per_sec = core.Limits.rate_burst = 1000      # the players here play with no pause
        srv = Server(os.path.join(ROOT, "plugins"), "test", self.db, "none")
        srv.use_auth()
        await srv.load_plugins()
        srv.load_state()
        srv.keyring = frontends.Keyring(self.keys)
        self.listeners = [await asyncio.start_server(srv.handle_frontend, "127.0.0.1", self.fe, limit=1024),
                          await asyncio.start_server(srv.handle, "127.0.0.1", self.pub, limit=1024)]
        save = srv.save

        def checked(t):
            save(t)
            self.check()
        srv.save = checked
        self.srv = srv
        return srv

    async def stop(self):
        for ls in self.listeners:
            ls.close()
        for sess in list(self.srv.sessions):
            try:
                sess.writer.transport.abort()
            except Exception:
                pass
        for ls in self.listeners:
            await ls.wait_closed()
        for p in self.srv.plugins.values():
            p.stop()
        self.srv.store.db.close()

    def standing(self):
        """{game type/ver: [table ids]}: the open public tables where only bots sit (what a player sees as the
        bot's open tables), and those that carry the bot's mark with nobody seated yet."""
        out = {}
        for t in self.srv.tables.values():
            seated = [n for n in t.seats if n]
            only_bots = all(bots.is_bot(self.srv.players.get(n.lower())) for n in seated)
            marked = t.data.get(service.OPEN_KEY) == service.OPEN_VALUE
            if t.status == "open" and t.vis == "public" and only_bots and (seated or marked):
                out.setdefault("%s/%d" % (t.type, t.ver), []).append(t.id)
        return out

    def check(self):
        for key, ids in self.standing().items():
            if len(ids) > 1:
                self.doubles.append((key, sorted(ids)))

    def one_each(self):
        s = self.standing()
        return s if sorted(s) == sorted(TWO) and all(len(v) == 1 for v in s.values()) else None

    def table(self, tid):
        return self.srv.tables.get(tid)


class BotRun:
    """The bot (bot/service.py) in this event loop, with no pause before its turns."""

    def __init__(self, parlor, names=service.NAMES, cap=20):
        self.log = []
        self.bot = service.Bot("127.0.0.1", parlor.fe, os.path.join(parlor.keys, "bot.key"), names, cap,
                               think=(0, 0), pace=0, poll=0.2, scan=0.5, log=self.log.append)
        self.task = asyncio.create_task(self.bot.run())

    async def stop(self):
        tasks = [self.task] + [t for g in self.bot.games.values() for t in g.values()]
        tasks += [o.task for o in self.bot.open.values() if o.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@slow
class OpenTables(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pnopen")
        self.rate = core.Limits.rate_per_sec, core.Limits.rate_burst

    def tearDown(self):
        core.Limits.rate_per_sec, core.Limits.rate_burst = self.rate
        shutil.rmtree(self.dir, True)

    def go(self, body, timeout=120):
        async def run():
            parlor = Parlor(self.dir)
            await parlor.start()
            runs = []
            try:
                await body(parlor, runs)
            finally:
                for r in runs:
                    await r.stop()
                await parlor.stop()
        asyncio.run(asyncio.wait_for(run(), timeout))

    async def started(self, parlor, runs, **kw):
        r = BotRun(parlor, **kw)
        runs.append(r)
        await until(parlor.one_each, "an open table of every two-player game")
        return r

    def test_one_open_table_of_every_two_player_game(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            srv = parlor.srv
            for key, (tid,) in parlor.standing().items():
                t = srv.tables[tid]
                self.assertEqual(t.rules, srv.plugins[key].info["rules"][0], key)    # the default rules
                self.assertEqual(t.turn_secs, srv.turn_default(), key)               # the default time
                self.assertEqual((t.options, len(t.seats), t.vis), ({}, 2, "public"), key)
                self.assertEqual([n for n in t.seats if n], ["bot"], key)             # bot holds them all
            for key in MORE:                                    # a game for more players gets none
                self.assertNotIn(key, parlor.standing())
            before = parlor.standing()
            await asyncio.sleep(2)                              # ten looks or so: nothing changes
            self.assertEqual(parlor.standing(), before)
            c = await Native.connect(parlor.pub, "ann")
            try:
                data, _ = await c.req("LST open")
                shown = [t for t in parse_tables(data) if bot_waits(t)]
                self.assertEqual(sorted(t["key"] for t in shown), sorted(TWO))
                self.assertTrue(all(t["names"] == ["bot"] and t["bots"] == {0} for t in shown))
            finally:
                c.close()
            self.assertEqual(parlor.doubles, [])
            self.assertEqual(runs[0].log[1:], [])
        self.go(body)

    def test_a_player_sits_down_and_the_next_one_opens(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            old = parlor.standing()["reversi/1"][0]
            c = await Native.connect(parlor.pub, "ann")
            try:
                self.assertEqual((await c.req("JON %s" % old))[1], "OK 1")       # one step: the game starts
                self.assertEqual(parlor.table(old).status, "playing")
                fresh = await until(lambda: [x for x in parlor.standing().get("reversi/1", []) if x != old],
                                    "the next reversi table", 5)
                self.assertGreater(int(fresh[0]), int(old))
                self.assertNotIn(service.OPEN_KEY, parlor.table(old).data)    # the mark is off the game
                why = await play.run(play.NetTable(c, old, 1, 0, random.Random(3), wait_secs=5), (0, 0), "random",
                                     log=self.fail)
                self.assertEqual(why, "over")
                said = [e.split(" ", 2)[2] for _, e in parlor.table(old).events if e.startswith("CHT ")]
                self.assertEqual(said[0], service.GREETING)
            finally:
                c.close()
            await until(lambda: service.FAREWELL in [e.split(" ", 2)[2] for _, e in parlor.table(old).events
                                                     if e.startswith("CHT ")], "the bot's last words")
            self.assertTrue(parlor.one_each())
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_two_players_race_for_one_table(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            tid = parlor.standing()["checkers/1"][0]
            a, b = await Native.connect(parlor.pub, "amy"), await Native.connect(parlor.pub, "bob")
            try:
                got = await asyncio.gather(a.req("JON %s" % tid), b.req("JON %s" % tid))
                finals = sorted(f for _, f in got)
                self.assertEqual(finals[0], "ERR CLOSED table is not open")          # exactly one sat down
                self.assertEqual(finals[1], "OK 1")
                loser = a if got[0][1].startswith("ERR") else b
                fresh = await until(lambda: [x for x in parlor.standing().get("checkers/1", []) if x != tid],
                                    "the next checkers table", 5)
                self.assertEqual((await loser.req("JON %s" % fresh[0]))[1], "OK 1")
                await until(lambda: len([x for x in parlor.standing().get("checkers/1", [])
                                         if x not in (tid, fresh[0])]) == 1, "a third checkers table", 5)
            finally:
                a.close()
                b.close()
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_a_game_turned_off_and_on(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            srv = parlor.srv
            tid = parlor.standing()["mancala/1"][0]
            srv.set_setting("games.off", "mancala/1")
            await until(lambda: "mancala/1" not in parlor.standing() and tid not in srv.tables,
                        "the bot to leave the mancala table", 5)
            await asyncio.sleep(1)
            self.assertNotIn("mancala/1", parlor.standing())
            srv.set_setting("games.off", "")
            await until(parlor.one_each, "a mancala table again", 5)
            srv.set_setting("games.off", "reversi/1:standard")      # its only ruleset off: the game is off
            await until(lambda: "reversi/1" not in parlor.standing(), "the reversi table to go", 5)
            srv.set_setting("games.off", "")
            await until(parlor.one_each, "a reversi table again", 5)
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_the_operator_or_the_idle_limit_closes_it(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            srv = parlor.srv
            tid = parlor.standing()["backgammon/1"][0]
            self.assertTrue(await srv.end_table(tid))                       # the operator ends it
            await until(lambda: parlor.one_each() and tid not in parlor.standing()["backgammon/1"],
                        "a new backgammon table", 5)
            tid = parlor.standing()["navalcombat/1"][0]
            srv.tables[tid].touched = 0                                   # nothing happened there for long
            await srv.sweep()
            self.assertNotIn(tid, srv.tables)
            await until(lambda: parlor.one_each() and tid not in parlor.standing()["navalcombat/1"],
                        "a new naval combat table", 5)
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_the_bot_restarts(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            before = parlor.standing()
            await runs.pop().stop()
            await asyncio.sleep(0.5)
            self.assertEqual(parlor.standing(), before)            # the tables stay while the bot is away
            await self.started(parlor, runs)
            await asyncio.sleep(2)                                  # it takes them up again, and makes none
            self.assertEqual(parlor.standing(), before)
            tid = before["fiveinarow/1"][0]
            c = await Native.connect(parlor.pub, "ann")
            try:
                self.assertEqual((await c.req("JON %s" % tid))[1], "OK 1")
                await until(lambda: any(e.endswith(service.GREETING) for _, e in parlor.table(tid).events),
                            "the bot's greeting", 5)
                await until(parlor.one_each, "the next five in a row table", 5)
            finally:
                c.close()
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_the_core_restarts(self):
        async def body(parlor, runs):
            await self.started(parlor, runs)
            before = parlor.standing()
            await parlor.stop()
            await asyncio.sleep(0.5)
            await parlor.start()                                    # the same database and ports
            self.assertEqual(parlor.standing(), before)
            await until(lambda: all(o.tid in before[k] for k, o in runs[0].bot.open.items())
                        and len(runs[0].bot.open) == len(TWO), "the bot back at its tables", 20)
            self.assertEqual(parlor.standing(), before)
            tid = before["reversi/1"][0]
            c = await Native.connect(parlor.pub, "ann")
            try:
                self.assertEqual((await c.req("JON %s" % tid))[1], "OK 1")
                await until(lambda: any(e.endswith(service.GREETING) for _, e in parlor.table(tid).events),
                            "the bot's greeting", 5)
                await until(parlor.one_each, "the next reversi table", 5)
            finally:
                c.close()
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_left_over_tables_are_put_right(self):
        """Tables from a bot that stopped halfway: a marked table nobody sits at yet, and two of one game."""
        async def body(parlor, runs):
            srv = parlor.srv
            from test_auth import frontend, say
            conn = frontend("bot")
            await say(srv, conn, "HELO bot")
            empty = (await say(srv, conn, "NEW reversi standard")).split()[1]
            await say(srv, conn, "TDS %s bot open" % empty)
            pair = []
            for _ in range(2):
                tid = (await say(srv, conn, "NEW checkers american")).split()[1]
                await say(srv, conn, "TDS %s bot open" % tid)
                await say(srv, conn, "JON %s" % tid)
                pair.append(tid)
            parlor.doubles = []
            await self.started(parlor, runs)
            await asyncio.sleep(1)
            parlor.doubles = []
            s = parlor.standing()
            self.assertEqual(s["reversi/1"], [empty])               # taken up: the bot sat down there
            self.assertEqual(srv.tables[empty].seats[0], "bot")
            self.assertEqual(s["checkers/1"], [pair[0]])            # the older one stays
            self.assertNotIn(pair[1], srv.tables)
            await asyncio.sleep(1)
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_the_names_share_the_work(self):
        """bot seats every open table while it has room, then bot1; a bot process without the name bot keeps
        none, so two processes never open the same game twice."""
        async def body(parlor, runs):
            other = BotRun(parlor, names=("bot2", "bot3"))
            runs.append(other)
            await asyncio.sleep(1.5)
            self.assertEqual(parlor.standing(), {})                  # no keeper without the name bot
            await self.started(parlor, runs, names=("bot", "bot1"), cap=1)
            self.assertEqual({parlor.table(i).seats[0] for v in parlor.standing().values() for i in v}, {"bot"},
                             (parlor.standing(), runs[-1].log))
            tid = parlor.standing()["reversi/1"][0]
            c = await Native.connect(parlor.pub, "ann")
            try:
                self.assertEqual((await c.req("JON %s" % tid))[1], "OK 1")    # bot now plays its one game
                fresh = await until(lambda: [x for x in parlor.standing().get("reversi/1", []) if x != tid],
                                    "the next reversi table", 5)
                self.assertEqual(parlor.table(fresh[0]).seats[0], "bot1")
            finally:
                c.close()
            await asyncio.sleep(1)
            self.assertTrue(parlor.one_each())
            self.assertEqual(parlor.doubles, [])
        self.go(body)

    def test_invite_bot_still_makes_a_table(self):
        """The shared /invite bot makes the player's own table, and /join bot names the games to pick from."""
        async def body(parlor, runs):
            await self.started(parlor, runs)
            c = await Native.connect(parlor.pub, "ann")
            out = []
            try:
                con = Console(out.extend, c.req, "ann", ["list", "invite", "join"], kinds=list(TWO),
                              new="standard")
                await con.handle("/join bot")
                said = " ".join(out)
                self.assertRegex(said, r"^bot waits at a table of each of these games: .*Reversi \d+.*\. "
                                       r"Type /join and the number\.$")
                con.kinds = ["reversi/1"]
                del out[:]
                await con.handle("/invite bot")
                said = " ".join(out)
                self.assertRegex(said, r"^You invited the bot to table \d+\.", out)
                tid = said.split()[6].rstrip(".")
                self.assertNotIn(tid, parlor.standing()["reversi/1"])
                self.assertEqual(parlor.table(tid).vis, "private")
                await until(lambda: "bot" in parlor.table(tid).seats or "bot1" in parlor.table(tid).seats,
                            "the bot to accept", 5)
            finally:
                c.close()
        self.go(body)


class Listing(unittest.TestCase):
    """How the lobbies tell the bot's open tables from the others, from LST alone."""

    LINES = ["TAB 7 reversi/1 standard 1/2 open bot,- turn=86400 bot=0",
             "TAB 8 reversi/1 standard 1/2 open ann,- turn=3600",
             "TAB 9 checkers/1 american 2/2 playing bob,bot turn=86400 bot=1",
             "TAB 10 reversi/1 standard 1/2 open bot,- turn=86400 vis=private bot=0",
             "TAB 11 rummytile/1 standard 2/4 open amy,bot,-,- turn=86400 bot=1", "OK"]

    def test_the_bots_open_tables(self):
        from adapters import botflag
        tabs = {t["id"]: t for t in parse_tables(self.LINES)}
        self.assertEqual(tabs["7"]["bots"], {0})
        self.assertEqual([i for i, t in tabs.items() if bot_waits(t)], ["7"])     # not private, not with a person
        self.assertEqual(sorted(tabs, key=lambda i: botflag.list_rank(tabs[i])), ["8", "10", "11", "7", "9"])

    def test_web_lists_them_after_a_persons_table(self):
        from adapters import web as W
        from game_checks import page_link
        games = [{"key": "reversi/1", "rules": "standard", "name": "Reversi", "min": 2, "max": 2}]
        html = W.lobby_page("cy", page_link, games, parse_tables(self.LINES[:3]),
                            bots={"7": {"bot"}, "9": {"bot"}}).decode("ascii")
        self.assertLess(html.index("t=8"), html.index("t=7"))           # a person's table, then the bot's
        self.assertLess(html.index("t=7"), html.index("t=9"))           # then the game being played
        self.assertIn("bot</a> (bot)", html)

    def test_invite_passes_over_them_and_join_finds_them(self):
        tabs = parse_tables(self.LINES[:4])
        con = Console(lambda lines: None, None, "cy", ["invite", "join"], kinds=["reversi/1"])
        self.assertIsNone(con.waiting_table(tabs, name="bot"))              # /invite bot makes a table
        self.assertEqual(con.waiting_table(tabs, name="ann")["id"], "8")
        self.assertEqual(con.waiting_table(tabs, tid="7")["id"], "7")       # /join 7 sits down there


if __name__ == "__main__":
    unittest.main()
