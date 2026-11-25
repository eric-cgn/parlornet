"""The shared chat commands (adapters/chatcmd.py): parsing, help, and the default commands against a scripted
core."""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import chatcmd as CC  # noqa: E402


class Core:
    """A scripted core: {request prefix: (data, final)}, and the requests seen."""

    def __init__(self, answers):
        self.answers, self.seen = answers, []

    async def req(self, text):
        self.seen.append(text)
        for prefix, got in self.answers.items():
            if text.startswith(prefix):
                return got
        return [], "ERR UNKNOWN"


def run(coro):
    return asyncio.run(coro)


def console(core, commands=CC.NAMES, **kw):
    said = []
    c = CC.Console(said.extend, core.req, "ann", commands, **kw)
    return c, said


TABLES = ["TAB 3 reversi/1 standard 2/2 playing ann,dan turn=600",
          "TAB 5 reversi/1 standard 1/2 open bob,- turn=600",
          "TAB 6 reversi/1 standard 1/2 open ann,- turn=600",
          "TAB 7 reversi/1 standard 1/2 open cy,- turn=600 vis=private",
          "TAB 8 checkers/1 standard 1/2 open eve,- turn=600"]


class Parse(unittest.TestCase):
    def test_slash_words_and_chat(self):
        self.assertEqual(CC.parse("/list"), CC.Command("list", [], "/list"))
        self.assertEqual(CC.parse("  /RESUME 12 ").name, "resume")
        self.assertEqual(CC.parse("/resume 12").args, ["12"])
        self.assertIsNone(CC.parse("list my games please"))        # chat in a chat box
        self.assertIsNone(CC.parse(""))
        self.assertEqual(CC.parse("/dance"), CC.Command("unknown", ["dance"], "/dance"))

    def test_quiet_aliases(self):
        for word in ("/bye", "/logout", "/exit"):
            self.assertEqual(CC.parse(word).name, "quit")
        self.assertEqual(CC.parse("/?").name, "help")
        self.assertIsNone(CC.parse("bye"))                          # words without the slash are chat
        self.assertEqual(CC.parse("/play bot"), CC.Command("unknown", ["play"], "/play bot"))


class Help(unittest.TestCase):
    def test_one_line_each_in_a_fixed_order_and_short(self):
        lines = CC.help_lines(["help", "list", "who"])
        self.assertEqual(lines, ["/list: your games and open tables", "/who [NAME]: players online, or one",
                                 "/help: these commands"])
        for line in CC.help_lines(CC.NAMES):
            self.assertLessEqual(len(line), 42, line)
        self.assertNotIn("/play", " ".join(CC.help_lines(CC.NAMES)))
        self.assertIn("/invite NAME: invite a player (bot too)", CC.help_lines(CC.NAMES))

    def test_compact_help_wraps(self):
        lines = CC.help_lines(CC.NAMES, ["A note."], compact=True, width=40)
        self.assertTrue(lines[0].startswith("Commands: /list, /resume [table],"))
        self.assertEqual(lines[-1], "A note.")
        self.assertTrue(all(len(l) <= 40 for l in lines), lines)

    def test_commands_line(self):
        self.assertEqual(CC.commands_line(["help", "open", "list"]), "Commands: /list, /open, /help.")


class Defaults(unittest.TestCase):
    def test_chat_passes_and_unknown_and_missing_commands_are_answered(self):
        c, said = console(Core({}), ["help"])
        self.assertFalse(run(c.handle("hello all")))
        self.assertTrue(run(c.handle("/dance")))
        self.assertTrue(run(c.handle("/who")))
        self.assertTrue(run(c.handle("/resume")))
        self.assertEqual(said, ["No such command: /dance. Type /help for the commands.", CC.NOT_HERE,
                                CC.NOT_HERE])

    def test_a_name_is_needed(self):
        c, said = console(Core({}))
        run(c.handle("/invite"))
        run(c.handle("/accept bad*name"))
        self.assertEqual(said, ["Type /invite NAME.", "Type /accept NAME."])

    def test_handlers_replace_defaults_and_add_quiet_extras(self):
        got = []

        async def mine(cmd):
            got.append(cmd)
        c, said = console(Core({}), ["help", "list"], handlers={"list": mine, "invite": mine})
        run(c.handle("/list"))
        run(c.handle("/invite dan"))
        self.assertEqual([g.name for g in got], ["list", "invite"])
        run(c.handle("/help"))
        self.assertEqual(said, ["/list: your games and open tables", "/help: these commands"])

    def test_say_may_be_a_coroutine_and_lines_wrap(self):
        said = []

        async def say(lines):
            said.extend(lines)
        core = Core({"WHO": (["PLR dan", "PLR bot1 bot", "PLR bot2 bot", "PLR averyveryverylongname"], "OK")})
        c = CC.Console(say, core.req, lambda: "ann", ["who"], width=24)
        run(c.handle("/who"))
        self.assertEqual(said, ["Online: dan,", "averyveryverylongname,", "bot1."])

    def test_motd(self):
        c, said = console(Core({"MOTD": (["MOTD Hello."], "OK")}))
        run(c.handle("/motd"))
        self.assertEqual(said[1], "Hello.")
        c, said = console(Core({"MOTD": ([], "OK")}))
        run(c.handle("/motd"))
        self.assertEqual(said, ["There is no message of the day."])

    def test_list(self):
        core = Core({"LST": (TABLES, "OK"), "VUE 3": (["W 1", "CLK 1 300 600"], "OK")})
        c, said = console(core, kinds=["reversi/1"], label="Reversi")
        run(c.handle("/list"))
        self.assertEqual(said, ["Your Reversi games in progress:", "Table 3 with dan: dan's move, 5m left.",
                                "Your table 6 is open.", "Open tables:",
                                "Table 5: bob waits for a player. Type /join 5 to sit down."])

    def test_open_and_close(self):
        seated = []

        async def on_seat(*a):
            seated.append(a)
        core = Core({"NEW": ([], "OK 9"), "JON": ([], "OK 0"), "LST": (TABLES, "OK"), "LVE": ([], "OK")})
        c, said = console(core, kinds=["reversi/1"], new="standard turn=600", seated=on_seat)
        run(c.handle("/open"))
        self.assertEqual(core.seen[:2], ["NEW reversi/1 standard turn=600", "JON 9 0"])
        self.assertEqual(seated, [("9", 0, None, "open")])
        run(c.handle("/close"))
        self.assertIn("LVE 6", core.seen)
        self.assertEqual(said[-1], "Your table 6 is closed.")

    def test_invite_makes_a_private_table(self):
        core = Core({"IVL": ([], "OK"), "LST": (TABLES, "OK"), "NEW": ([], "OK 9"), "JON": ([], "OK 0"),
                     "INV": ([], "OK")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/invite bot"))
        self.assertEqual(core.seen[2:], ["NEW reversi/1 standard vis=private", "JON 9 0", "INV 9 bot 1"])
        self.assertEqual(said, ["You invited the bot to table 9. It will accept in a few seconds."])
        run(c.handle("/invite ann"))
        self.assertEqual(said[-1], "You cannot invite yourself.")

    def test_invite_joins_a_waiting_player_or_takes_their_invitation(self):
        core = Core({"IVL": ([], "OK"), "LST": (TABLES, "OK"), "JON 5": ([], "OK 1")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/invite bob"))
        self.assertEqual(core.seen[-1], "JON 5")
        self.assertEqual(said, ["You sat down at bob's table 5."])
        core = Core({"IVL": (["IVT 4 reversi/1 standard dan open seat=1"], "OK"), "JON 4": ([], "OK 1")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/invite dan"))
        self.assertEqual(said, ["You sat down at table 4 with dan."])

    def test_accept_and_decline(self):
        ivl = (["IVT 4 reversi/1 standard dan open seat=1", "IVT 8 checkers/1 standard eve open seat=1"], "OK")
        core = Core({"IVL": ivl, "JON 4": ([], "OK 1"), "UNV": ([], "OK")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/accept eve"))                # another game's invitation: not taken here
        self.assertEqual(said[-1], "You have no invitation from eve here.")
        run(c.handle("/decline eve"))               # but it can be refused from here
        self.assertEqual(core.seen[-1], "UNV 8 ann")
        self.assertEqual(said[-1], "You declined eve's invitation.")
        run(c.handle("/accept DAN"))
        self.assertEqual(core.seen[-2:], ["JON 4", "LST table=4 clients"])
        self.assertEqual(said[-1], "You sat down at table 4 with dan.")


class Resign(unittest.TestCase):
    def core(self):
        return Core({"LST": (TABLES, "OK"), "RES": ([], "OK")})

    def test_a_second_resign_confirms_the_current_game(self):
        core = self.core()
        c, said = console(core, kinds=["reversi/1"], current=lambda: "3")
        run(c.handle("/resign"))
        self.assertEqual(said, ["Resign table 3? /resign again to confirm."])
        self.assertNotIn("RES 3", core.seen)
        run(c.handle("/resign"))
        self.assertEqual(core.seen[-1], "RES 3")
        self.assertEqual(said[-1], "You resigned table 3.")
        run(c.handle("/resign"))                     # asks again: a confirmation is used once
        self.assertEqual(said[-1], "Resign table 3? /resign again to confirm.")

    def test_the_ask_lapses_and_yes_resigns_at_once(self):
        core = self.core()
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/resign"))
        self.assertEqual(said[-1], "Type /resign TABLE (its number in /list).")
        run(c.handle("/resign 3"))
        c.resign_ask = ("3", c.resign_ask[1] - CC.RESIGN_SECS - 1)       # more than RESIGN_SECS ago
        run(c.handle("/resign 3"))
        self.assertEqual(said[-1], "Resign table 3? /resign again to confirm.")
        self.assertNotIn("RES 3", core.seen)
        run(c.handle("/resign #3 yes"))
        self.assertEqual(core.seen[-1], "RES 3")

    def test_only_a_running_game_of_mine_of_this_kind(self):
        core = self.core()
        for tid in ("5", "6", "8", "99"):            # someone else's, open, another kind, none
            c, said = console(core, kinds=["reversi/1"])
            run(c.handle("/resign %s yes" % tid))
            self.assertEqual(said, ["Table %s is not a running game of yours." % tid])
        self.assertFalse([r for r in core.seen if r.startswith("RES")])
        c, said = console(Core({"LST": (TABLES, "OK"), "RES": ([], "ERR NOGAME the game is not running")}),
                          kinds=["reversi/1"])
        run(c.handle("/resign 3 yes"))
        self.assertTrue(said[-1].startswith("Can't resign table 3:"), said)


class Join(unittest.TestCase):
    def test_by_number_or_by_name_without_an_invitation(self):
        seated = []

        async def on_seat(*a):
            seated.append(a)
        core = Core({"IVL": ([], "OK"), "LST": (TABLES, "OK"), "JON 5": ([], "OK 1")})
        c, said = console(core, kinds=["reversi/1"], seated=on_seat)
        run(c.handle("/join 5"))
        self.assertEqual(core.seen[-1], "JON 5")
        self.assertEqual(said, ["You sat down at table 5 with bob."])
        self.assertEqual(seated, [("5", 1, "bob", "join")])
        run(c.handle("/join BOB"))
        self.assertEqual(said[-1], "You sat down at table 5 with bob.")

    def test_tables_that_are_not_open_to_me(self):
        core = Core({"IVL": ([], "OK"), "LST": (TABLES, "OK")})
        c, said = console(core, kinds=["reversi/1"])
        for arg in ("3", "6", "7", "8", "99"):        # playing, mine, private, another game's, none
            run(c.handle("/join %s" % arg))
        run(c.handle("/join cy"))
        run(c.handle("/join"))
        self.assertFalse([r for r in core.seen if r.startswith("JON")])
        self.assertEqual(said[0], "Table 3 has no open seat for you. /list shows the open tables.")
        self.assertEqual(said[-2], "cy is not waiting at an open table. /list shows the open tables.")
        self.assertEqual(said[-1], "Type /join and a table or a name.")

    def test_join_and_accept_are_the_same_for_an_inviter(self):
        ivl = (["IVT 4 reversi/1 standard dan open seat=1"], "OK")
        for word in ("join", "accept"):
            core = Core({"IVL": ivl, "LST": (TABLES, "OK"), "JON 4": ([], "OK 1")})
            c, said = console(core, kinds=["reversi/1"])
            run(c.handle("/%s dan" % word))
            self.assertEqual((core.seen[-2:], said), (["JON 4", "LST table=4 clients"],
                                                      ["You sat down at table 4 with dan."]), word)

    def test_list_without_join_says_to_invite(self):
        tables = CC.parse_tables(TABLES)
        self.assertIn("Table 5: bob waits for a player. Invite bob to play there.", CC.open_lines(tables, "ann", False))


class Lines(unittest.TestCase):
    def test_game_lines(self):
        self.assertEqual(CC.game_lines([], "Backgammon"), ["You have no Backgammon games in progress."])
        self.assertEqual(CC.game_lines([], "", entering=True), [])
        self.assertEqual(CC.game_lines([("3", 0, "dan", 0, 90000)], ""),
                         ["Your games in progress:", "Table 3 with dan: your move, 1d 1h left."])
        self.assertEqual(CC.game_lines([("3", 0, "dan and eve", 2, None, "eve")], "Grid Words")[1],
                         "Table 3 with dan and eve: eve's move.")



GM = "with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus"
CLIENT_TABLES = ["TAB 4 reversi/1 standard 1/2 open dan,- turn=600", "CLIENT 0 " + GM,
                 "TAB 5 reversi/1 standard 1/2 open bob,- turn=600", "CLIENT 0 on an Amiga 500",
                 "TAB 9 reversi/1 standard 1/2 open eve,- turn=600"]


class Clients(unittest.TestCase):
    """What each player plays with (LST clients, the CLIENT event; docs/protocol.md, Clients and machines)."""

    def test_join_says(self):
        lines = ["EVT 5 1 JOIN 0 ann", "EVT 5 2 CLIENT 0 " + GM, "EVT 5 3 JOIN 1 bob", "EVT 5 4 START",
                 "EVT 5 5 CLIENT 1 with Telnet on a VT100", "EVT 5 6 JOIN 2 cy", "EVT 5 7 CLIENT 2"]
        self.assertEqual(CC.join_says(lines), {0: GM}, "a CLIENT after a later event, or bare, is not a join's")
        self.assertEqual(CC.join_says(["JOIN 1 bob", "CLIENT 1 on an Amiga 500"]), {1: "on an Amiga 500"})
        self.assertEqual(CC.client_of(["1", "with", "X"]), (1, "with X"))
        self.assertIsNone(CC.client_of(["1"]), "a bare CLIENT says nothing")

    def test_list_and_tables(self):
        core = Core({"LST": (CLIENT_TABLES, "OK"), "GAM": (["GAM reversi/1 standard Reversi 2 2"], "OK")})
        c, said = console(core, kinds=["reversi/1"], label="Reversi")
        run(c.handle("/list"))
        self.assertEqual(core.seen[0], "LST game=reversi/1 clients")
        self.assertIn("Table 4: dan (%s) waits for a player. Type /join 4 to sit down." % GM, said)
        self.assertIn("Table 9: eve waits for a player. Type /join 9 to sit down.", said, "nothing known")
        said.clear()
        run(c.handle("/tables bob"))
        self.assertEqual(core.seen[-2], "LST player=bob clients")
        self.assertIn("Table 5, Reversi: bob (on an Amiga 500) waits. /join 5", said)

    def test_sitting_down_says_what_the_others_play_with(self):
        core = Core({"IVL": ([], "OK"), "LST": (CLIENT_TABLES, "OK"), "JON": ([], "OK 1")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/join 4"))
        self.assertEqual(said[-1], "You sat down at table 4 with dan, who plays %s." % GM)
        run(c.handle("/join 9"))
        self.assertEqual(said[-1], "You sat down at table 9 with eve.")
        run(c.handle("/invite bob"))
        self.assertEqual(said[-1], "You sat down at bob's table 5. bob plays on an Amiga 500.")
        ivl = (["IVT 4 reversi/1 standard dan open seat=1"], "OK")
        core = Core({"IVL": ivl, "LST table=4 clients": (CLIENT_TABLES[:2], "OK"), "JON 4": ([], "OK 1")})
        c, said = console(core, kinds=["reversi/1"])
        run(c.handle("/accept dan"))
        self.assertEqual(said, ["You sat down at table 4 with dan, who plays %s." % GM])

    def test_narrow_console_wraps(self):
        core = Core({"IVL": ([], "OK"), "LST": (CLIENT_TABLES, "OK"), "JON": ([], "OK 1")})
        said = []
        c = CC.Console(said.extend, core.req, "ann", CC.NAMES, kinds=["reversi/1"], width=30)
        run(c.handle("/join 4"))
        self.assertTrue(all(len(l) <= 30 for l in said), said)
        self.assertIn("Macintosh Plus.", " ".join(said))


if __name__ == "__main__":
    unittest.main()
