"""A player's limits on games (docs/pnp.md, Tables): games.active_max games being played at once, and
games.waiting_per_type open tables of one game type waited at; and the server's games.tables_max. Checked when
a player sits down (NEW, JON, an invitation taken up); bots are exempt; a game that starts is never refused."""
import os
import sys
import unittest

from test_core import ROOT

sys.path.insert(0, ROOT)
from adapters.common import err_text  # noqa: E402
from parlord import admin as PA  # noqa: E402
from parlord.core import MAXLINE, Limits, Server, Session  # noqa: E402

WAIT_RV = "ERR LIMIT you are already waiting at 2 Reversi tables; start or leave one"


class FakeWriter:
    def get_extra_info(self, key):
        return ("127.0.0.1", 1) if key == "peername" else None

    def write(self, b):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class LimitsBase(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(await self.srv.dispatch(sess, "HELO %s" % name), ["OK -"])
        return sess

    async def req(self, sess, line):
        return (await self.srv.dispatch(sess, line))[-1]

    async def open_table(self, sess, game="reversi standard"):
        """NEW, then sit down: the table's id."""
        r = await self.req(sess, "NEW %s" % game)
        self.assertTrue(r.startswith("OK "), r)
        tid = r.split()[1]
        self.assertTrue((await self.req(sess, "JON %s" % tid)).startswith("OK"))
        return tid

    async def game(self, a, b, game="reversi standard"):
        """A game a and b are playing: the table's id."""
        tid = await self.open_table(a, game)
        self.assertTrue((await self.req(b, "JON %s" % tid)).startswith("OK"))
        self.assertEqual(self.srv.tables[tid].status, "playing")
        return tid


class Waiting(LimitsBase):
    async def test_two_open_tables_of_a_type_then_refused(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        await self.open_table(amy)
        t2 = await self.open_table(amy)
        self.assertEqual(await self.req(amy, "NEW reversi standard"), WAIT_RV)
        theirs = await self.open_table(bo)
        await self.open_table(amy, "dotsandboxes standard players=3")      # other types: unaffected
        await self.open_table(amy, "checkers american")
        # standing up from one makes room again
        self.assertEqual(await self.req(amy, "LVE %s" % t2), "OK")
        t3 = await self.open_table(amy)
        self.assertEqual(await self.req(amy, "NEW reversi standard"), WAIT_RV)
        # sitting down where the game starts at once is not waiting: allowed at the limit
        self.assertEqual(await self.req(amy, "JON %s" % theirs), "OK 1")
        self.assertEqual(self.srv.tables[theirs].status, "playing")
        self.assertTrue(t3)

    async def test_joining_an_open_table_that_leaves_you_waiting_is_refused(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        await self.open_table(amy, "dotsandboxes standard players=3")
        await self.open_table(amy, "dotsandboxes standard players=3")
        theirs = await self.open_table(bo, "dotsandboxes standard players=3")
        self.assertEqual(await self.req(amy, "JON %s" % theirs),
                         "ERR LIMIT you are waiting at 2 Dots and Boxes tables; start or leave one")
        self.assertEqual(self.srv.tables[theirs].seats, ["bo", None, None])

    async def test_taking_up_an_invitation_is_sitting_down(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        await self.open_table(amy)
        await self.open_table(amy)
        tid = await self.open_table(bo, "dotsandboxes standard players=3")
        await self.open_table(amy, "dotsandboxes standard players=3")
        await self.open_table(amy, "dotsandboxes standard players=3")
        self.assertTrue((await self.req(bo, "INV %s amy" % tid)).startswith("OK"))
        self.assertEqual(err_text(await self.req(amy, "JON %s" % tid)),
                         "you are waiting at 2 Dots and Boxes tables; start or leave one")
        self.assertIn("amy", self.srv.tables[tid].invites)        # the invitation stands

    async def test_a_long_game_name_still_fits_the_line(self):
        amy = await self.player("amy")
        await self.open_table(amy, "rummytile standard")
        await self.open_table(amy, "rummytile standard")
        r = await self.req(amy, "NEW rummytile standard")
        self.assertEqual(r, "ERR LIMIT you are waiting at 2 Rummy Tile Classic tables; start or leave one")
        self.assertLessEqual(len(r), MAXLINE - 2)

    async def test_the_setting(self):
        amy = await self.player("amy")
        self.srv.set_setting("games.waiting_per_type", "1")
        await self.open_table(amy)
        self.assertEqual(await self.req(amy, "NEW reversi standard"),
                         "ERR LIMIT you are already waiting at 1 Reversi table; start or leave one")
        self.srv.set_setting("games.waiting_per_type", "3")
        await self.open_table(amy)
        await self.open_table(amy)
        self.assertEqual(await self.req(amy, "NEW reversi standard"),
                         "ERR LIMIT you are already waiting at 3 Reversi tables; start or leave one")


class Active(LimitsBase):
    async def test_at_the_limit_no_more_seats_until_a_game_ends(self):
        amy, bo, cy = [await self.player(n) for n in ("amy", "bo", "cy")]
        self.srv.set_setting("games.active_max", "2")
        g1 = await self.game(amy, bo)
        await self.game(bo, amy, "checkers american")
        full = "ERR LIMIT you are playing 2 games, the most allowed here; finish one first"
        self.assertEqual(await self.req(amy, "NEW reversi standard"), full)
        theirs = await self.open_table(cy)
        self.assertEqual(await self.req(amy, "JON %s" % theirs), full)
        # an invitation taken up
        inv = await self.open_table(cy, "dotsandboxes standard")
        self.assertTrue((await self.req(cy, "INV %s amy" % inv)).startswith("OK"))
        self.assertEqual(await self.req(amy, "JON %s" % inv), full)
        # watching is not sitting down
        self.assertEqual(await self.req(amy, "SEE %s" % theirs), "OK")
        # a resigned game does not count
        self.assertEqual(await self.req(amy, "RES %s" % g1), "OK")
        self.assertEqual(self.srv.tables[g1].status, "done")
        self.assertEqual(await self.req(amy, "JON %s" % inv), "OK 1")

    async def test_a_seat_left_in_a_running_game_does_not_count(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        self.srv.set_setting("games.active_max", "1")
        tid = await self.game(amy, bo)
        self.assertTrue((await self.req(amy, "NEW reversi standard")).startswith("ERR LIMIT"))
        self.srv.tables[tid].gone.add(0)            # as a "drop" game lets a seat go while the others play on
        self.assertTrue((await self.req(amy, "NEW reversi standard")).startswith("OK"))

    async def test_waiting_tables_that_start_may_pass_the_limit(self):
        amy, bo, cy = [await self.player(n) for n in ("amy", "bo", "cy")]
        self.srv.set_setting("games.active_max", "2")
        await self.game(amy, bo, "checkers american")
        t1 = await self.open_table(amy)
        t2 = await self.open_table(amy)
        self.assertEqual(await self.req(bo, "JON %s" % t1), "OK 1")
        self.assertEqual(await self.req(cy, "JON %s" % t2), "OK 1")          # never refused: amy is not sitting
        self.assertEqual([self.srv.tables[t].status for t in (t1, t2)], ["playing", "playing"])
        self.assertEqual(await self.req(amy, "NEW reversi standard"),
                         "ERR LIMIT you are playing 3 games, over the 2 allowed; finish one first")

    async def test_the_default_is_twenty(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        self.assertEqual(self.srv.limit("games.active_max"), 20)
        for _ in range(20):
            await self.game(amy, bo)
        self.assertEqual(await self.req(amy, "NEW checkers american"),
                         "ERR LIMIT you are playing 20 games, the most allowed here; finish one first")
        self.assertEqual(await self.req(bo, "NEW checkers american"),
                         "ERR LIMIT you are playing 20 games, the most allowed here; finish one first")


class Bots(LimitsBase):
    async def test_a_bot_is_exempt_from_both(self):
        amy, bo, cy, bot = [await self.player(n) for n in ("amy", "bo", "cy", "botty")]
        self.srv.players["botty"].bot = True       # as parlord/bots.py flags the bot's accounts
        self.srv.set_setting("games.active_max", "1")
        self.srv.set_setting("games.waiting_per_type", "1")
        await self.game(bot, amy)
        await self.game(bot, bo)
        await self.open_table(bot, "dotsandboxes standard players=3")
        await self.open_table(bot, "dotsandboxes standard players=3")
        # invited, as the bot always is
        tid = await self.open_table(cy, "dotsandboxes standard players=3")
        await self.req(cy, "INV %s botty" % tid)
        self.assertEqual(await self.req(bot, "JON %s" % tid), "OK 1")
        # the person is not: amy plays one game
        self.assertTrue((await self.req(amy, "NEW reversi standard")).startswith("ERR LIMIT you are playing 1 game,"))


class Settings(LimitsBase):
    def test_values(self):
        s = self.srv
        self.assertEqual([s.limit(k) for k in ("games.tables_max", "games.active_max",
                                                    "games.waiting_per_type")], [400, 20, 2])
        for v, want in (("5", 5), ("0", 20), ("", 20), ("x", 20), ("-3", 20), (" 7 ", 7)):
            s.settings["games.active_max"] = v      # as the settings file gives it
            self.assertEqual(s.limit("games.active_max"), want, v)
        check = PA.SETTINGS["games.waiting_per_type"]
        self.assertEqual([check("3"), check("0"), check(""), check(" 04 ")], ["3", "", "", "4"])
        for bad in ("100", "-1", "two"):
            with self.assertRaises(ValueError):
                check(bad)
        with self.assertRaises(ValueError):
            PA.SETTINGS["games.active_max"]("1000")
        self.assertEqual(PA.SETTINGS["games.tables_max"]("5000"), "5000")

    async def test_the_admin_sets_and_clears_them(self):
        adm = PA.Admin(self.srv)
        self.assertEqual(adm.op_set(None, {"key": "games.active_max", "value": "7"}), {"ok": True})
        self.assertEqual(self.srv.limit("games.active_max"), 7)
        self.assertEqual(adm.op_get(None, {})["effective"]["games.active_max"], "7")
        self.assertEqual(self.srv.store.settings().get("games.active_max"), "7")
        self.assertFalse(adm.op_set(None, {"key": "games.active_max", "value": "abc"})["ok"])
        self.assertTrue(adm.op_set(None, {"key": "games.active_max", "value": "0"})["ok"])   # 0: the default
        self.assertNotIn("games.active_max", self.srv.settings)
        g = adm.op_get(None, {})["effective"]
        self.assertEqual([g["games.active_max"], g["games.waiting_per_type"], g["games.tables_max"]],
                         ["20", "2", "400"])

    async def test_tables_max(self):
        amy, bo = await self.player("amy"), await self.player("bo")
        self.assertEqual(Limits.tables_max, 400)
        self.srv.set_setting("games.tables_max", "2")
        await self.open_table(amy)
        await self.open_table(bo)
        self.assertEqual(await self.req(amy, "NEW checkers american"), "ERR FULL too many tables, try later")


class Online(LimitsBase):
    """players.online_max: signed-in players at once (bots exempt), ERR BUSY at sign-in; WHO keeps in step."""

    async def test_a_full_server_refuses_new_sign_ins(self):
        self.srv.set_setting("players.online_max", "2")
        amy, bo = await self.player("amy"), await self.player("bo")
        busy = "ERR BUSY wait=60 the server is full (2 players), try again later"
        cy = Session(FakeWriter())
        self.assertEqual(await self.req(cy, "HELO cy"), busy)
        self.assertEqual(await self.req(cy, "REG cy"), busy)
        self.assertIsNone(cy.player)
        await self.player("amy")                            # a player signed in already may sign in again
        bot = Session(FakeWriter(), frontend="bot")         # the bot's own connection: never refused
        self.assertTrue((await self.req(bot, "HELO bot")).startswith("OK "))
        self.assertTrue(self.srv.players["bot"].bot)
        self.assertEqual(await self.req(cy, "HELO cy"), busy)          # and it does not count
        self.assertTrue((await self.req(bo, "OUT")).startswith("OK"))
        self.assertEqual(await self.req(cy, "HELO cy"), "OK -")
        self.assertTrue(amy.player)
        from adapters.common import no_wait         # how the frontends show it
        self.assertEqual(no_wait(busy.split(" ", 2)[2]), "The server is full (2 players), try again later")

    async def test_who_lists_as_many(self):
        self.srv.set_setting("players.online_max", "3")
        for n in ("amy", "bo", "cy"):
            await self.player(n)
        self.srv.set_setting("players.online_max", "1")     # lowered with players signed in: none is cut off
        lines = await self.srv.dispatch(await self.player("amy"), "WHO")
        self.assertEqual(lines, ["PLR amy idle=0", "PLR bo idle=0", "PLR cy idle=0", "OK"])   # 1 and the 4 bots


class OpenHoursAndTurns(LimitsBase):
    async def test_open_tables_stay_a_day(self):
        amy = await self.player("amy")
        tid = await self.open_table(amy)
        self.srv.tables[tid].touched -= 7 * 3600            # past the old 6 hours
        await self.srv.sweep()
        self.assertIn(tid, self.srv.tables)
        self.srv.tables[tid].touched -= 18 * 3600           # 25 hours
        await self.srv.sweep()
        self.assertNotIn(tid, self.srv.tables)
        tid = await self.open_table(amy)
        self.srv.set_setting("games.open_hours", "2")
        self.srv.tables[tid].touched -= 3 * 3600
        await self.srv.sweep()
        self.assertNotIn(tid, self.srv.tables)

    async def test_the_longest_turn_is_five_days(self):
        amy = await self.player("amy")
        self.assertEqual(await self.req(amy, "NEW reversi standard turn=6d"),
                         "ERR BADOPT turn is a time per turn, 1m to 5d (like 5m, 1h, 1d)")
        self.assertTrue((await self.req(amy, "NEW reversi standard turn=5d")).startswith("OK"))
        self.srv.set_setting("games.turn_max_days", "10")
        self.assertTrue((await self.req(amy, "NEW checkers american turn=6d")).startswith("OK"))
        self.srv.set_setting("games.turn_max_days", "2")
        self.srv.set_setting("server.turn_default", str(3 * 86400))
        self.assertEqual(self.srv.turn_default(), 2 * 86400)          # the default kept within the longest
        with self.assertRaises(ValueError):
            PA.SETTINGS["server.turn_default"]("7d")


class TableList(LimitsBase):
    """LST's filters, its order and its cap (lists.max)."""

    def ids(self, lines):
        return [l.split()[1] for l in lines if l.startswith("TAB ")]

    async def lst(self, sess, line="LST"):
        return await self.srv.dispatch(sess, line)

    async def test_order_cap_and_more(self):
        amy = await self.player("amy")
        others = [await self.player("p%d" % i) for i in range(12)]
        opened = []
        for p in others:                                    # 24 open tables, two each
            opened += [await self.open_table(p), await self.open_table(p, "checkers american")]
        g = await self.game(others[0], amy, "dotsandboxes standard")     # amy's game
        mine = await self.open_table(amy)
        pl = await self.game(others[1], others[2], "dotsandboxes standard")
        bo = await self.player("bo")
        inv = await self.open_table(bo, "dotsandboxes standard")
        await self.req(bo, "INV %s amy" % inv)
        lines = await self.lst(amy)
        self.assertEqual(self.ids(lines)[:3], [g, mine, inv])                   # own and invitations first
        self.assertEqual(self.ids(lines)[3:], list(reversed(opened + [inv]))[1:21])   # open ones, newest first
        self.assertEqual(lines[-2:], ["MORE 5", "OK"])                          # 4 open and the game cut
        self.assertNotIn(pl, self.ids(lines))
        self.srv.set_setting("lists.max", "40")
        lines = await self.lst(amy)
        self.assertEqual(self.ids(lines)[-1], pl)                               # games being played last
        self.assertEqual(lines[-1], "OK")
        # the extension channel may ask for every table
        self.srv.set_setting("lists.max", "1")
        ext = Session(FakeWriter(), frontend="web")
        self.assertTrue((await self.req(ext, "HELO amy")).startswith("OK "))
        self.assertEqual(len(self.ids(await self.lst(ext, "LST all"))), 28)
        self.assertEqual(await self.lst(amy, "LST all"), ["ERR NOTFRONTEND LST all is only for server extensions"])

    async def test_filters(self):
        amy, bo, cy = [await self.player(n) for n in ("amy", "bo", "cy")]
        rv = await self.open_table(bo)
        ck = await self.open_table(bo, "checkers american")
        game = await self.game(bo, cy)
        r = await self.req(bo, "NEW reversi standard vis=private")
        priv = r.split()[1]
        await self.req(bo, "JON %s" % priv)
        await self.req(bo, "INV %s amy" % priv)
        mine = await self.open_table(amy, "checkers american")
        self.assertEqual(self.ids(await self.lst(amy, "LST open")), [priv, mine, ck, rv])
        self.assertEqual(self.ids(await self.lst(amy, "LST playing")), [game])
        self.assertEqual(self.ids(await self.lst(amy, "LST game=reversi")), [priv, rv, game])
        self.assertEqual(self.ids(await self.lst(amy, "LST game=checkers/1 open")), [mine, ck])
        self.assertEqual(self.ids(await self.lst(amy, "LST mine")), [priv, mine])
        # player=: a private table only when the asker sits there too (amy is only invited)
        self.assertEqual(self.ids(await self.lst(amy, "LST player=BO")), [ck, rv, game])
        self.assertEqual(self.ids(await self.lst(cy, "LST player=bo")), [game, ck, rv])     # cy's own first
        self.assertEqual(self.ids(await self.lst(bo, "LST player=bo")), [rv, ck, game, priv])
        self.assertEqual(self.ids(await self.lst(amy, "LST player=cy playing")), [game])
        self.assertEqual(self.ids(await self.lst(amy, "LST player=nobody")), [])
        self.assertEqual(self.ids(await self.lst(cy, "LST table=%s" % rv)), [rv])
        self.assertEqual(self.ids(await self.lst(cy, "LST table=%s" % priv)), [])     # not cy's to see
        for bad in ("LST closed", "LST game=", "LST player=a.b", "LST table=x", "LST open playing"):
            self.assertEqual(await self.lst(amy, bad),
                             ["ERR SYNTAX LST [open|playing] [mine] [game=type] [player=name] [table=id]"], bad)


class ChatCommands(LimitsBase):
    """/tables NAME and /who NAME (adapters/chatcmd.py) against the core."""

    async def console(self, sess, kinds=("reversi/1",)):
        from adapters import chatcmd as CC
        said = []

        async def req(text):
            r = await self.srv.dispatch(sess, text)
            return r[:-1], r[-1]
        return CC.Console(said.extend, req, sess.player.name, CC.NAMES, kinds=kinds), said

    async def test_tables_and_who(self):
        amy, bo, cy = [await self.player(n) for n in ("amy", "bo", "cy")]
        rv = await self.open_table(bo)
        game = await self.game(bo, cy, "checkers american")
        self.assertEqual(await self.req(bo, "PFL SET PREF Macintosh SE/30"), "OK")
        c, said = await self.console(amy)
        await c.handle("/tables bo")
        self.assertEqual(said, ["Table %s, Reversi: bo waits. /join %s" % (rv, rv),
                                "Table %s, Checkers: bo and cy are playing." % game])
        del said[:]
        await c.handle("/who bo")
        self.assertEqual(said[0], "bo: online, plays on Macintosh SE/30.")
        self.assertEqual(len(said), 3)
        del said[:]
        self.srv.players["cy"].active -= 900                 # online (presence.idle, 30m) but idle
        await c.handle("/who cy")
        self.assertEqual(said[0], "cy: idle 15m.")
        del said[:]
        await self.req(cy, "OUT")
        self.srv.players["cy"].active -= 3600
        await c.handle("/who cy")
        self.assertEqual(said[0], "cy: not online.")
        del said[:]
        await c.handle("/who zed")
        self.assertEqual(said, ["No player called zed."])
        del said[:]
        await c.handle("/tables amy")
        self.assertEqual(said, ["amy sits at no table."])
        del said[:]
        await c.handle("/tables")
        self.assertEqual(said, ["Type /tables NAME."])
        del said[:]
        await c.handle("/help")
        self.assertIn("/who [NAME]: players online, or one", said)
        self.assertIn("/tables NAME: NAME's tables", said)
