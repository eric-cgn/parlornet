"""The command request, CMD [table] [YES] /line (docs/protocol.md, Commands): the shared commands
(parlord/commands.py) run by the core for any frontend, in the lobby, at a waiting table and in a game; typed
moves through the game's own reading of them; the confirmation dialogs; refusals; the line limit and the
rate limit. The last class runs the core and the bot as processes, and /invite bot seats the bot."""
import asyncio
import os
import sys
import time
import unittest

from test_core import ROOT, free_port, make_keys, stack_args
from test_smoke import Conn, until

sys.path.insert(0, ROOT)
from parlord import commands  # noqa: E402
from parlord.core import MAXLINE, Server, Session  # noqa: E402


class Writer:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


def inf(reply):
    """The INF lines of a reply, as text to show."""
    return [l[4:] for l in reply if l.startswith("INF ")]


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        await self.srv.load_plugins()

    async def asyncTearDown(self):
        for p in self.srv.plugins.values():
            p.stop()
            if p.proc:
                await p.proc.wait()

    async def player(self, name):
        sess = Session(Writer())
        self.assertEqual(await self.srv.dispatch(sess, "HELO %s" % name), ["OK -"])
        return sess

    async def req(self, sess, line):
        return await self.srv.dispatch(sess, line)

    async def cmd(self, sess, line):
        """CMD line -> the reply, which must end with OK and fit the protocol's lines."""
        out = await self.req(sess, "CMD " + line)
        self.assertEqual(out[-1], "OK", out)
        for l in out:
            self.assertLessEqual(len(l) + 2, MAXLINE, l)
            self.assertRegex(l, r"^(INF( .*)?|DLG [a-z]+( \d+)?|OK)$", l)
        return out

    async def says(self, sess, line):
        """CMD line -> the text it shows, as one string."""
        return " ".join(inf(await self.cmd(sess, line)))

    async def events(self, sess, tid, n=0):
        return [l.split(" ", 3)[3] for l in await self.req(sess, "SIN %s %d" % (tid, n)) if l.startswith("EVT ")]

    async def game(self, a, b, game="reversi standard"):
        """A game a and b play, a in seat 0: the table's id."""
        tid = (await self.req(a, "NEW %s" % game))[-1].split()[1]
        self.assertEqual((await self.req(a, "JON %s 0" % tid))[-1], "OK 0")
        self.assertEqual((await self.req(b, "JON %s 1" % tid))[-1], "OK 1")
        self.assertEqual(self.srv.tables[tid].status, "playing")
        return tid


class Request(Base):
    async def test_the_form_and_its_errors(self):
        s = Session(Writer())
        self.assertTrue((await self.req(s, "CMD /who"))[-1].startswith("ERR NOHELO"))
        ann, bob = await self.player("ann"), await self.player("bob")
        self.assertEqual(await self.req(ann, "CMD who"), ["ERR SYNTAX CMD [table] [YES] /command"])
        self.assertEqual(await self.req(ann, "CMD"), ["ERR SYNTAX CMD [table] [YES] /command"])
        self.assertEqual(await self.req(ann, "CMD 1 2 /who"), ["ERR SYNTAX CMD [table] [YES] /command"])
        self.assertEqual(await self.req(ann, "CMD 9 /who"), ["ERR NOTABLE no such table"])
        tid = (await self.req(bob, "NEW reversi standard"))[-1].split()[1]
        await self.req(bob, "JON %s" % tid)
        self.assertEqual(await self.req(ann, "CMD %s /who" % tid), ["ERR NOTAT sit or watch first"])
        await self.req(ann, "SEE %s" % tid)
        self.assertEqual(inf(await self.cmd(ann, "%s /who" % tid)), ["Online: ann, bob."])
        self.assertEqual(inf(await self.cmd(ann, "/WHO")), ["Online: ann, bob."])     # as typed, in any case

    async def test_it_counts_as_activity(self):
        ann = await self.player("ann")
        ann.player.active = 0
        await self.cmd(ann, "/time")
        self.assertGreater(ann.player.active, 0)

    async def test_each_request_it_sends_counts_against_the_rate_limit(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        for i in range(2):
            await self.game(ann, bob)
        ann.tokens, ann.stamp = 2.5, time.time()    # /list sends LST, then VUE for each game: three requests
        self.assertEqual(await self.req(ann, "CMD /list"), ["ERR RATE slow down"])
        ann.tokens, ann.stamp = 30, time.time()
        self.assertIn("Your games in progress:", await self.says(ann, "/list"))
        self.assertLess(ann.tokens, 28)

    async def test_long_text_is_wrapped_to_the_line(self):
        ann = await self.player("ann")
        lines = inf(await self.cmd(ann, "/open"))       # the games there are: longer than a line
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(l) <= MAXLINE - 6 for l in lines))
        self.assertEqual(lines[0], "Type /open and a game, such as /open backgammon. The games are backgammon,")


class Lobby(Base):
    async def test_help_and_the_commands_that_only_look(self):
        ann = await self.player("ann")
        help_ = inf(await self.cmd(ann, "/help"))
        self.assertEqual(help_[0], "/list: your games and open tables")
        for name in commands.CORE_COMMANDS:
            self.assertTrue(any(l.startswith("/%s" % name) for l in help_), name)
        for name in ("resume", "signin", "accept", "decline"):
            self.assertFalse(any(l.startswith("/%s" % name) for l in help_), name)
        self.assertEqual(await self.says(ann, "/who"), "Online: ann.")
        self.assertEqual(await self.says(ann, "/score bob"), "No player called bob.")
        self.assertRegex(await self.says(ann, "/time"), r"^(Signed in \d+:\d\d\. )?Server time \d\d:\d\d UTC\.$")
        self.assertEqual(await self.says(ann, "/list"),
                         "You have no games in progress. No open tables are waiting for a player.")
        self.assertEqual(await self.says(ann, "/dance"), "No such command: /dance. Type /help for the commands.")
        self.assertEqual(await self.says(ann, "/resume"), commands.NOT_HERE)
        self.assertEqual(await self.says(ann, "/say hello"), "You can say a line only at a table.")
        self.assertEqual(await self.says(ann, "/note"), "Notes belong to a game, so type /note at its table.")
        self.assertEqual(await self.says(ann, "/d3"), "No such command: /d3. Type /help for the commands.")

    async def test_open_needs_a_game_in_the_lobby_and_join_takes_a_seat(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        self.assertIn("The games are", await self.says(ann, "/open"))
        self.assertEqual(await self.says(ann, "/open chess"), "There is no game called chess here. The games are "
                         "backgammon, cannonfire, checkers, dotsandboxes, fiveinarow, gridwords, mancala, navalcombat, "
                         "reversi and rummytile.")
        self.assertEqual(await self.says(ann, "/open Grid_Words"), "Your table 1 is open: anyone can join it. "
                         "/close closes it.")
        t = self.srv.tables["1"]
        self.assertEqual((t.type, t.rules, t.seats[0]), ("gridwords", "english", "ann"))
        self.assertIn("Table 1: ann waits for a player. Type /join 1 to sit down.", await self.says(bob, "/list"))
        self.assertEqual(await self.says(bob, "/join ann"), "You sat down at table 1 with ann.")
        self.assertEqual(t.status, "playing")

    async def test_invitations_are_answered_in_their_dialog(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        await self.says(ann, "/invite bob reversi")
        self.assertEqual([l.split()[4] for l in await self.req(bob, "IVL") if l.startswith("IVT")], ["ann"])
        for line in ("/accept ann", "/decline ann"):
            self.assertEqual(await self.says(bob, line), commands.IN_DIALOG)
        self.assertEqual(len([l for l in await self.req(bob, "IVL") if l.startswith("IVT")]), 1)   # untouched

    async def test_invite_in_the_lobby_needs_a_game_unless_the_player_waits(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        self.assertIn("To invite bob, name a game too, such as /invite bob backgammon.",
                      await self.says(ann, "/invite bob"))
        self.assertEqual(self.srv.tables, {})
        await self.says(bob, "/open checkers")
        self.assertEqual(await self.says(ann, "/invite bob"), "You sat down at bob's table 1.")

    async def test_quit_asks_first_and_then_signs_out(self):
        ann = await self.player("ann")
        out = await self.cmd(ann, "/quit")
        self.assertEqual(out, ["DLG quit", "INF Sign out? Your games go on without you.", "OK"])
        self.assertIsNotNone(ann.player)
        self.assertEqual(await self.cmd(ann, "/bye"), out)
        self.assertEqual(inf(await self.cmd(ann, "YES /quit")), ["You are signed out. Your games go on without you."])
        self.assertIsNone(ann.player)
        self.assertTrue((await self.req(ann, "CMD /who"))[-1].startswith("ERR NOHELO"))


class WaitingTable(Base):
    async def table(self, ann):
        await self.says(ann, "/open reversi")
        return "1"

    async def test_invite_and_uninvite_there(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.table(ann)
        self.assertEqual(await self.says(ann, "1 /invite bob"), "You invited bob to table 1.")
        self.assertEqual([l.split()[1] for l in await self.req(bob, "IVL") if l.startswith("IVT")], [tid])
        self.assertEqual(await self.says(ann, "1 /invite ann"), "You cannot invite yourself.")
        self.assertEqual(await self.says(ann, "1 /invite nobody"), "There is no player called nobody here.")
        self.assertEqual(await self.says(ann, "1 /uninvite bob"), "You withdrew bob's invitation.")
        self.assertEqual(await self.says(ann, "1 /uninvite bob"), "bob is not invited to table 1.")
        self.assertEqual(len(self.srv.tables), 1)       # no other table was made

    async def test_invite_bot_invites_the_bot_there(self):
        bot = Session(Writer(), frontend="bot", fingerprint="f")
        self.assertTrue((await self.req(bot, "HELO bot"))[-1].startswith("OK"))
        ann = await self.player("ann")
        tid = await self.table(ann)
        self.assertEqual(await self.says(ann, "1 /invite bot"),
                         "You invited the bot to table 1. It will sit down in a few seconds.")
        self.assertIn("bot", self.srv.tables[tid].invites)
        self.assertEqual(await self.says(ann, "1 /invite bot"), "Every free seat at table 1 is held for someone "
                         "invited.")
        self.assertIn("INVITE - bot", await self.events(ann, tid))

    async def test_invite_bot_on_a_parlor_without_the_bot(self):
        ann = await self.player("ann")
        await self.table(ann)
        self.assertEqual(await self.says(ann, "1 /invite bot"), "The bot does not play on this parlor.")

    async def test_close_and_typed_moves_before_the_start(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.table(ann)
        self.assertEqual(await self.says(ann, "1 /d3"), "The game at table 1 has not started yet.")
        await self.req(bob, "SEE %s" % tid)
        self.assertEqual(await self.says(bob, "1 /close"), "Table 1 can be closed only while you wait there alone.")
        self.assertEqual(await self.says(ann, "1 /close"), "Your table 1 is closed.")
        self.assertNotIn(tid, self.srv.tables)

    async def test_the_command_leaves_the_event_cursor_alone(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.table(ann)
        self.assertEqual(await self.events(ann, tid), ["JOIN 0 ann"])
        await self.says(ann, "1 /invite bob")
        self.assertEqual([l.split(" ", 3)[3] for l in await self.req(ann, "SIN %s" % tid) if l.startswith("EVT")],
                         ["INVITE - bob"])


class InAGame(Base):
    async def test_a_typed_move_is_played_and_its_events_come_after(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob)
        await self.req(ann, "SIN %s" % tid)
        self.assertEqual(await self.cmd(ann, "%s /d3" % tid), ["OK"])
        evts = [l.split(" ", 3)[3] for l in await self.req(ann, "SIN %s" % tid) if l.startswith("EVT")]
        self.assertEqual(evts[0], "PUT 0 d3 FLP d4")
        self.assertEqual(await self.says(ann, "%s /f5" % tid), "It's not your turn.")
        self.assertEqual(await self.says(bob, "%s /a1" % tid), "You can't play a1. That square flips nothing.")
        self.assertEqual(await self.says(bob, "%s /dance" % tid), "No such command: /dance. Type /help for the "
                         "commands.")

    async def test_say_at_the_table_by_a_player_and_a_watcher(self):
        ann, bob, cy = await self.player("ann"), await self.player("bob"), await self.player("cy")
        tid = await self.game(ann, bob)
        await self.req(cy, "SEE %s" % tid)
        self.assertEqual(await self.cmd(ann, "%s /say good luck" % tid), ["OK"])
        self.assertEqual(await self.cmd(cy, "%s /say  hello all " % tid), ["OK"])
        self.assertEqual(await self.says(ann, "%s /say" % tid), "Type /say and what you want to say.")
        evts = await self.events(bob, tid)
        self.assertIn("CHT 0 good luck", evts)
        self.assertIn("CHT - hello all", evts)
        self.assertEqual(await self.says(cy, "%s /d3" % tid), "No such command: /d3. Type /help for the commands.")

    async def test_notes_of_the_table(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob)
        self.assertEqual(await self.says(ann, "%s /note" % tid), "Table %s has no notes." % tid)
        self.assertEqual(await self.says(ann, "%s /note played on a C64" % tid), "Noted at table %s." % tid)
        self.assertRegex(await self.says(bob, "%s /note" % tid), r"^[A-Z][a-z]{2} \d+ ann: played on a C64$")

    async def test_resign_asks_first_and_the_confirmed_form_resigns(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob)
        ask = await self.cmd(ann, "%s /resign" % tid)
        self.assertEqual(ask, ["DLG resign %s" % tid,
                               "INF Resign this game? bob wins. Leaving the table never resigns.", "OK"])
        self.assertEqual(await self.cmd(ann, "%s /resign" % tid), ask)            # asking twice confirms nothing
        self.assertEqual(await self.cmd(ann, "%s /resign yes" % tid), ask)        # nor does a typed yes
        self.assertEqual(await self.cmd(ann, "/resign %s yes" % tid), ask)
        self.assertEqual(self.srv.tables[tid].status, "playing")
        self.assertEqual(inf(await self.cmd(ann, "%s YES /resign" % tid)), ["You resigned table %s." % tid])
        self.assertEqual(self.srv.tables[tid].status, "done")
        self.assertIn("END resign 0", await self.events(bob, tid))
        self.assertEqual(await self.says(ann, "%s /d3" % tid), "The game at table %s is over." % tid)

    async def test_resign_from_the_lobby_names_the_table(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob)
        self.assertEqual((await self.cmd(bob, "/resign %s" % tid))[0], "DLG resign %s" % tid)
        self.assertEqual(await self.says(bob, "/resign"), "Type /resign TABLE (its number in /list).")

    async def test_resign_with_more_players_says_the_others_play_on(self):
        a, b, c = await self.player("ann"), await self.player("bob"), await self.player("cy")
        tid = (await self.req(a, "NEW dotsandboxes standard players=3"))[-1].split()[1]
        for i, s in enumerate((a, b, c)):
            await self.req(s, "JON %s %d" % (tid, i))
        self.assertEqual(inf(await self.cmd(b, "%s /resign" % tid)),
                         ["Resign this game? The others play on. Leaving the table never resigns."])

    async def test_pass_asks_first(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob, "gridwords english")
        self.assertEqual(await self.cmd(ann, "%s /pass" % tid),
                         ["DLG pass %s" % tid, "INF Pass your turn? You play nothing this turn.", "OK"])
        self.assertFalse(any(e.startswith("PASS") for e in await self.events(ann, tid)))
        self.assertEqual(await self.cmd(ann, "%s YES /pass" % tid), ["OK"])
        self.assertTrue(any(e.startswith("PASS 0") for e in await self.events(ann, tid)))

    async def test_offers_are_answered_in_their_dialog(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob, "checkers american")
        self.assertEqual(await self.cmd(ann, "%s /draw" % tid), ["OK"])          # making an offer is a command
        self.assertEqual(await self.says(bob, "%s /accept" % tid), commands.IN_DIALOG)
        self.assertIn("dialog", await self.says(bob, "%s /reject" % tid))
        self.assertFalse(any(e.startswith("END") for e in await self.events(ann, tid)))

    async def test_staged_steps_and_their_refusals(self):
        ann, bob = await self.player("ann"), await self.player("bob")
        tid = await self.game(ann, bob, "checkers american")
        await self.req(ann, "SIN %s" % tid)
        mover = ann if self.srv.tables[tid].turn_seat == 0 else bob
        moves = [l[2:] for l in await self.req(mover, "VUE %s M" % tid) if l.startswith("M ")]
        self.assertEqual(await self.cmd(mover, "%s /%s" % (tid, moves[0])), ["OK"])
        self.assertEqual(await self.cmd(mover, "%s /undo" % tid), ["OK"])
        self.assertEqual(await self.cmd(mover, "%s /%s" % (tid, moves[0])), ["OK"])
        self.assertEqual(await self.cmd(mover, "%s /end" % tid), ["OK"])
        self.assertNotEqual(self.srv.tables[tid].turn_seat, 0 if mover is ann else 1)


class Shared(unittest.TestCase):
    def test_chatcmd_is_the_shared_implementation(self):
        from adapters import chatcmd
        self.assertIs(chatcmd.Console, commands.Console)
        self.assertIs(chatcmd.parse, commands.parse)
        self.assertIs(chatcmd.COMMANDS, commands.COMMANDS)
        self.assertTrue(issubclass(commands.CoreConsole, commands.Console))

    def test_the_dialogs_fit_and_say_what_design_md_says(self):
        with open(os.path.join(ROOT, "docs", "design.md")) as f:
            design = f.read()
        for kind, words in commands.DIALOGS.items():
            text = words % "ann wins." if "%s" in words else words
            self.assertIn(text, design.replace('"', ""), kind)


class WithTheBot(unittest.TestCase):
    """The core and the bot as processes: /invite bot at a waiting table seats the bot."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.core, cls.fe = free_port(), free_port()
        cls.keys = make_keys("bot")
        cls.procs = []
        for args in (stack_args(cls.core, cls.fe, cls.keys) + ["--auth", "none"],
                     ["bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                      "--think", "0,0", "--pace", "0", "--poll", "0.2"]):
            p = subprocess.Popen([sys.executable, "-m"] + args, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
            cls.procs.append(p)
            p.stdout.readline()

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()

    def test_invite_bot_seats_the_bot(self):
        c = Conn(self.core)
        try:
            self.assertEqual(c.req("HELO amy")[-1], "OK -")
            until(lambda: "bot" in " ".join(c.req("WHO")), "the bot to sign in")
            self.assertEqual(c.req("CMD /open reversi"),
                             ["INF Your table 1 is open: anyone can join it. /close closes it.", "OK"])
            self.assertEqual(c.req("CMD 1 /invite bot"),
                             ["INF You invited the bot to table 1. It will sit down in a few seconds.", "OK"])
            until(lambda: any(l.startswith("TAB 1 ") and " playing amy,bot " in l for l in c.req("LST")),
                  "the bot to sit down")
            self.assertEqual(c.req("CMD 1 /d3"), ["OK"])
            self.assertTrue(any(l.startswith("EVT 1 ") and l.endswith(" PUT 0 d3 FLP d4") for l in c.req("SIN 1 0")))
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
