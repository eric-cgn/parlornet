"""Telnet's game mode and command mode (docs/design.md). In game mode every key is a shortcut and nothing types;
/ opens the command line on row 22, where the lit keys go dark, Enter runs the line, Esc clears it and Up and
Down recall earlier lines. Each letter has one meaning in every game, and a letter a game doesn't use does
nothing. ? opens help everywhere, and Esc unwinds one step at a time until it reaches the lobby. Every game's
sentence line and keys line read the same way on your turn and on the other player's turn. Plain mode sends a
line without a slash to the chat, except a line that is a move."""
import asyncio
import unittest

from game_checks import FakeIO, ScreenCore, T, session
from test_movement_keys import live_lines

from adapters import games as G


def board(module, me=0, keys=(), moves="arrows", names=("ann", "bob")):
    """A game's full screen at the start of a game, as seat me, for a session signed in as that seat."""
    io = FakeIO("ansi", list(keys))
    core = ScreenCore(module.KEY, live_lines(module, max(me, 0)), names)
    s = session(io, core, "ansi", names[me] if me >= 0 else "eve")
    s.moves = moves
    g = module.AnsiScreen(s, "3", module.KEY, me)
    g.names, g.turn_secs, g.v = list(names), 300, g.parse(core.lines)
    g.snap()
    return g


def press(g, *keys):
    """Keys as they arrive, each handled in turn; what the last one returned (dialogs read keys too)."""
    g.io.keys[:0] = list(keys)
    out = []

    async def go():
        while g.io.keys:
            out.append(await g.on_key(await g.io.readkey()))
    try:
        asyncio.run(go())
    except EOFError:
        pass
    return out[-1] if out else None


def rows(g):
    return g.screen()[0].lines()


def mover(module):
    """The seat whose turn it is at the start, and the other one."""
    g = board(module, 0)
    turn = g.v["turn"] if g.v["turn"] in (0, 1) else 0
    return turn, 1 - turn


def screens_text(g):
    return "\n".join("\n".join(s) for s in g.io.screens)


class SentenceAndKeys(unittest.TestCase):
    def test_every_game_on_your_turn_and_theirs(self):
        for module in G.all_games():
            mine, theirs = mover(module)
            g = board(module, mine)
            r = rows(g)
            self.assertRegex(r[21], r"^ (Your turn\.|Your shot\.|Place your)", module.KEY)
            self.assertNotIn(";", r[21], module.KEY)
            self.assertRegex(r[23], r"^ (Arrows|Enter) ", module.KEY)
            self.assertRegex(r[23], r"\? (help|more)  (R resign  )?/ type  Esc (lobby|cancel)$", module.KEY)
            if module.KEY != "navalcombat/1":           # both players place their fleets at once
                g = board(module, theirs)
                r = rows(g)
                self.assertEqual(r[21], " Waiting for %s. Press T to chat, or ? for help." % g.names[mine], module.KEY)
                self.assertNotIn("Enter", r[23], module.KEY)
                self.assertTrue(r[23].endswith("T chat  ? help  R resign  / type  Esc lobby"), (module.KEY, r[23]))
            g = board(module, -1)
            r = rows(g)
            self.assertEqual(r[21], " You are watching ann and bob. Press T to chat, or Esc to leave.", module.KEY)
            self.assertTrue(r[23].endswith("T chat  ? help  / type  Esc lobby"), (module.KEY, r[23]))

    def test_the_lit_keys_line(self):
        from adapters.games import reversi
        g = board(reversi, mover(reversi)[0])
        cv = g.screen()[0]
        self.assertEqual(cv.lines()[23], " Arrows move  Enter place  T chat  ? help  R resign  / type  Esc lobby")
        self.assertEqual(cv.at[23][1], T.A_KEY)                     # "Arrows" is lit


class CommandMode(unittest.TestCase):
    def test_slash_darkens_the_screen(self):
        from adapters.games import reversi
        g = board(reversi, mover(reversi)[0])
        lit = g.screen()[0]
        self.assertTrue(any("46" in a for row in lit.at for a in row))     # the board's cursor
        press(g, "/")
        cv = g.screen()[0]
        self.assertEqual(cv.lines()[22], " /")
        self.assertEqual(cv.lines()[23], " Enter run it  Esc back to the game  Backspace erase")
        self.assertFalse([a for a in cv.at[23] if a == T.A_KEY])           # nothing lit
        self.assertFalse(any("46" in a for row in cv.at for a in row))     # no cursor on the board
        self.assertIn("Type a command, or a move such as /d3, and press Enter.", cv.lines()[21])
        press(g, *"say")
        self.assertEqual(g.screen()[0].lines()[21], " /say sends a line to the table.")

    def test_enter_runs_and_esc_clears(self):
        from adapters.games import reversi
        g = board(reversi, 0)
        press(g, "/", *"say hi", "ESC")
        self.assertEqual(g.buf, "")
        self.assertFalse([r for r in g.s.core.reqs if r.startswith("CHT")])
        press(g, "/", *"say hi", "ENTER")
        self.assertEqual(g.buf, "")
        self.assertIn("CHT 3 hi", g.s.core.reqs)
        press(g, "/", "x", "BS", "BS")                  # Backspace past the slash closes the line
        self.assertEqual(g.buf, "")
        press(g, "/", *"nosuch", "ENTER")                # the server's words, as on every frontend
        self.assertEqual(g.msg, "No such command: /nosuch. Type /help for the commands.")

    def test_up_recalls_earlier_lines(self):
        from adapters.games import reversi
        g = board(reversi, 0)
        press(g, "/", *"say one", "ENTER", "/", *"say two", "ENTER", "/", "UP")
        self.assertEqual(g.buf, "/say two")
        press(g, "UP")
        self.assertEqual(g.buf, "/say one")
        press(g, "UP")
        self.assertEqual(g.buf, "/say one")
        press(g, "DOWN")
        self.assertEqual(g.buf, "/say two")
        press(g, "DOWN")
        self.assertEqual(g.buf, "/")

    def test_no_letter_types_in_game_mode(self):
        for module in G.all_games():
            g = board(module, mover(module)[0])
            for k in "aiqwy":
                press(g, k)
                self.assertEqual(g.buf, "", (module.KEY, k))
            self.assertFalse([r for r in g.s.core.reqs if r.split()[0] in ("MOV", "CHT", "RES")], module.KEY)

    def test_typed_moves_need_the_slash(self):
        from adapters.games import reversi
        g = board(reversi, mover(reversi)[0])
        sq = g.v["moves"][0]
        press(g, *sq)                                   # a letter and a digit, as keys: nothing types or plays
        self.assertEqual(g.buf, "")
        self.assertFalse([r for r in g.s.core.reqs if r.startswith("MOV")])
        press(g, "/", *sq, "ENTER")                     # a typed move runs on the server, as every command
        self.assertIn("CMD 3 /%s" % sq, g.s.core.reqs)


class OneMeaningPerLetter(unittest.TestCase):
    def test_every_letter_means_one_thing(self):
        for module in G.all_games():
            for k in module.AnsiScreen.HOTKEYS:
                if k.isalpha():
                    self.assertIn(k, T.SHORTCUTS, (module.KEY, k))

    def test_a_letter_a_game_does_not_use(self):
        for module in G.all_games():
            g = board(module, mover(module)[0])
            unused = next(c for c in "afijklqswy" if c not in module.AnsiScreen.HOTKEYS)
            press(g, unused)
            self.assertEqual(g.msg, "%s does nothing in %s. Press ? to see the keys." % (unused.upper(), module.NAME))

    def test_y_and_n_answer_only_dialogs(self):
        for module in G.all_games():
            self.assertNotIn("y", module.AnsiScreen.HOTKEYS, module.KEY)
            self.assertNotIn("n", module.AnsiScreen.HOTKEYS, module.KEY)


class Confirmations(unittest.TestCase):
    def test_resign_asks_and_starts_on_no(self):
        from adapters.games import reversi
        for keys, sent in ((["r", "ENTER"], False), (["r", "ESC"], False), (["r", "n"], False),
                           (["r", "y"], True), (["r", "LEFT", "ENTER"], True), (["/"] + list("resign") + ["ENTER", "y"], True)):
            g = board(reversi, 0)
            press(g, *keys)
            self.assertEqual("RES 3" in g.s.core.reqs, sent, keys)
        self.assertIn("Resign this game? bob wins.", screens_text(g))
        self.assertIn("Leaving the table never resigns.", screens_text(g))
        g = board(reversi, 0, keys=["n"])
        asyncio.run(g.command("resign yes"))            # a yes typed after the command confirms nothing
        self.assertNotIn("RES 3", g.s.core.reqs)
        self.assertIn("Resign this game? bob wins.", screens_text(g))

    def test_pass_asks(self):
        from adapters.games import gridwords
        g = board(gridwords, mover(gridwords)[0], keys=[])

        async def typed(words):                         # the plugin's reading: a pass that asks first
            return {"move": ["PASS"], "ask": "pass"} if words == ["pass"] else {}
        g.s.core.typed = typed
        press(g, "p", "ESC")
        self.assertNotIn("MOV 3 PASS", g.s.core.reqs)
        self.assertIn("Pass your turn? You play nothing this turn.", screens_text(g))
        press(g, "p", "y")
        self.assertIn("MOV 3 PASS", g.s.core.reqs)

    def test_sign_out_asks(self):
        from adapters.games import reversi
        g = board(reversi, 0)
        self.assertIsNone(press(g, "/", *"quit", "ENTER", "n"))
        self.assertFalse(g.s.quitting)
        self.assertEqual(press(g, "/", *"quit", "ENTER", "y"), "menu")
        self.assertTrue(g.s.quitting)
        self.assertIn("Sign out? Your games go on without you.", screens_text(g))


class ChatScreen(unittest.TestCase):
    def test_t_and_tab_open_it(self):
        from adapters.games import checkers
        for key in ("t", "TAB"):
            g = board(checkers, mover(checkers)[0])
            press(g, key)
            r = rows(g)
            self.assertEqual(r[1], " Chat at table 3. It is your turn on the board.")
            self.assertEqual(r[23], " Enter send  / command  Esc board")
            press(g, *"is it?", "ENTER")             # ? types once the line has words
            self.assertIn("CHT 3 is it?", g.s.core.reqs)
            press(g, "?", "ESC")                         # on an empty line ? opens help
            self.assertIn("help, page 1 of 2", screens_text(g))
            press(g, *"/time", "ENTER")
            self.assertIn("CMD 3 /time", g.s.core.reqs)
            press(g, "ESC")
            self.assertFalse(g.chatting)


class HelpAndEsc(unittest.TestCase):
    """? opens help everywhere, and Esc unwinds one step at a time: a dialog, the command line, the chat
    screen and a pick each close first, and then Esc goes back to the lobby."""

    def test_every_game(self):
        for module in G.all_games():
            g = board(module, mover(module)[0])
            press(g, "?", "ESC")
            self.assertIn("%s help, page 1 of 2" % module.NAME, screens_text(g), module.KEY)
            self.assertIsNone(g.overlay)
            steps = [["/", "x"], ["t"], ["r"]]
            if module.KEY in ("backgammon/1", "checkers/1", "rummytile/1", "navalcombat/1", "gridwords/1"):
                steps.append(["ENTER"])                 # a pick, or a word's start
            for keys in steps:
                result = press(g, *keys, "ESC")
                self.assertIsNone(result, (module.KEY, keys))
                self.assertEqual((g.buf, g.chatting, g.overlay), ("", False, None), (module.KEY, keys))
            self.assertFalse([r for r in g.s.core.reqs if r.split()[0] in ("MOV", "RES", "END")], module.KEY)
            self.assertEqual(press(g, "ESC"), "menu", module.KEY)
            g.io.screens.clear()
            press(g, "/", "?", "ESC")                   # on an empty command line ? is help too
            self.assertIn("%s help, page 1 of 2" % module.NAME, screens_text(g), module.KEY)

    def test_the_lobby(self):
        io = FakeIO("ansi", [])
        from test_adapters import TABS, FakeCore
        s = T.Session(io, FakeCore(TABS), "ansi", "abcdefghijkl")
        m = T.AnsiMenu(s)
        asyncio.run(m.load())
        m.io.keys = ["ESC"]
        asyncio.run(m.on_key("?"))
        self.assertIn("Lobby help", screens_text(m))
        asyncio.run(m.on_key("/"))
        asyncio.run(m.on_key("ESC"))
        self.assertEqual(m.buf, "")
        self.assertIsNone(asyncio.run(m.on_key("ESC")))     # nothing to cancel, and nowhere further back


class PlainLines(unittest.TestCase):
    def plain(self, keys=()):
        from adapters.games import reversi
        io = FakeIO("plain", list(keys))
        io.out = []
        io.write = io.out.append
        core = ScreenCore(reversi.KEY, live_lines(reversi, 0), ("ann", "bob"))
        s = session(io, core, "plain", "ann")
        g = reversi.PlainScreen(s, "3", reversi.KEY, 0)
        g.names, g.v = ["ann", "bob"], g.parse(core.lines)
        return g, core

    def test_a_bare_line_is_chat_unless_it_is_a_move(self):
        g, core = self.plain()
        sq = g.v["moves"][0]
        asyncio.run(g.line_in(sq))
        self.assertFalse([r for r in core.reqs if r.startswith(("MOV", "CHT"))])
        self.assertIn("To play %s, type /%s. To say it, type /say %s.\n" % (sq, sq, sq), "".join(g.io.out))
        asyncio.run(g.line_in("hello all"))
        self.assertIn("CHT 3 hello all", core.reqs)
        asyncio.run(g.line_in("/" + sq))
        self.assertIn("CMD 3 /%s" % sq, core.reqs)


if __name__ == "__main__":
    unittest.main()


class LastMove(unittest.TestCase):
    """The last move is said in one sentence in the panel, in the shared words, and marked on the board."""

    CASES = {
        "reversi/1": (["PUT 1 d3 x d4"], "bob placed a disc on d3 and flipped 1."),
        "fiveinarow/1": (["PUT 1 h8"], "bob placed a stone on h8."),
        "dotsandboxes/1": (["LINE 1 a1-b1 BOX a1"], "bob drew a1-b1 and closed a box."),
        "mancala/1": (["SOW 1 3 4"], "bob sowed pit 3."),
        "cannonfire/1": (["FIRE 1 45 60", "DMG 0 80"], "bob fired at angle 45 with power 60 and hit you."),
        "backgammon/1": (["ROLL 1 6 3", "STEP 1 12 18", "STEP 1 12 15"], "bob rolled 6 and 3 and moved 12-18 and 12-15."),
        "checkers/1": (["JUMP 1 15 22 18", "JUMP 1 22 31 26"], "bob jumped 15x22x31."),
        "gridwords/1": (["PLAY 1 8 7 V DOZE 19"], "bob played DOZE down from h7 for 19 points."),
        "navalcombat/1": (["SHOT 1 e10 sunk 2"], "bob sank your 2-square ship."),
        "rummytile/1": (["DONE 1 3"], "bob played 3 tiles."),
    }

    def test_every_game_says_it(self):
        for module in G.all_games():
            events, want = self.CASES[module.KEY]
            g = board(module, 0)
            for e in events:
                said = g.last_move(e)
                g.last = said if said is not None else g.last
            self.assertEqual(g.last, want, module.KEY)
            self.assertIn(want.split()[1], "\n".join(rows(g)), module.KEY)        # in the panel
        from adapters.games import reversi
        g = board(reversi, 1)
        self.assertEqual(g.last_move("PUT 1 d3 x d4"), "You placed a disc on d3 and flipped 1.")

    def test_the_board_marks_it(self):
        from adapters.games import backgammon, checkers, reversi
        g = board(reversi, 0)
        filled = next((x, y) for y, row in enumerate(g.v["rows"]) for x, c in enumerate(row) if c in "01")
        g.last_sq = g.sq(filled)
        x, y = g.cell_xy(*filled)
        self.assertEqual(rows(g)[y][x], "[")
        g = board(backgammon, 0)
        g.moved_to = {"13"}
        self.assertIn("13*", "\n".join(rows(g)))
        g = board(checkers, 0)
        g.last_to, g.jumped = 15, (18,)
        text = "\n".join(rows(g))
        self.assertIn("[15]", text)
        self.assertIn("[18]", text)


class Help(unittest.TestCase):
    """Every game's help has two pages with the same headings. Space turns the page, Left turns back, and Esc
    or Enter closes it. Every line fits the box."""

    def test_two_pages_in_every_game(self):
        for module in G.all_games():
            g = board(module, 0)
            one, two = g.help_pages()
            heads = [l for l in one if l in ("Moving", "Keys for %s" % module.NAME, "Commands", "Every game")]
            self.assertEqual(heads, ["Moving", "Keys for %s" % module.NAME, "Commands", "Every game"], module.KEY)
            self.assertEqual(one[-2:], g.EVERY_GAME)
            self.assertEqual(two[-2:], ["Every table also takes /say, /invite, /note, /time and /leave.",
                                        "Type /tutorial for a short lesson on these keys."])
            for page in (one, two):
                self.assertLessEqual(len(page), T.HELP_ROWS, module.KEY)
                for l in page:
                    self.assertLessEqual(len(l), 70, (module.KEY, l))
                    self.assertNotIn(";", l, (module.KEY, l))
            g = board(module, 0, moves="wasd")
            self.assertLessEqual(len(g.help_pages()[1]), T.HELP_ROWS, module.KEY)

    def test_turning_the_pages(self):
        from adapters.games import checkers
        g = board(checkers, 0)
        press(g, "?", " ", "LEFT", " ", " ", "ENTER")
        titles = [s[i] for s in g.io.screens for i in range(24) if "help, page" in s[i]]
        pages = [t.split("page ")[1][0] for t in titles]
        self.assertEqual([p for i, p in enumerate(pages) if i == 0 or p != pages[i - 1]], ["1", "2", "1", "2", "1"])
        self.assertIsNone(g.overlay)


class GridWordsModes(unittest.TestCase):
    """Grid Words has three modes. In game mode letters are shortcuts. Enter on an empty square starts entry
    mode, where Right or Down point the word, letters lay tiles from the rack, Backspace takes one back, Enter
    plays and Esc stops. / opens command mode from either."""

    def screen(self):
        from adapters.games import gridwords
        g = board(gridwords, mover(gridwords)[0])
        g.v["rack"] = list("QUIZEA_")
        g.cur = (8, 8)
        return g

    def test_entry_mode(self):
        g = self.screen()
        press(g, "ENTER")
        r = rows(g)
        self.assertEqual(r[21], " Your word starts at h8 and goes across. Press Down to go down, or type.")
        self.assertEqual(r[23], " Letters lay tiles  Enter play  Backspace take back  Esc stop")
        self.assertFalse([a for a in g.screen()[0].at[23] if a == T.A_KEY])
        self.assertIn("Your word starts at h8 and goes across.", r[20])
        press(g, "DOWN")
        self.assertEqual(g.start, (8, 8, "V"))
        press(g, "q", "u", "w")
        self.assertEqual(g.word, "QU")
        self.assertEqual(g.msg, "You have no W on your rack.")
        self.assertEqual(g.rack_left(), list("IZEA_"))
        x, y = g.cell_xy(8, 10)
        self.assertEqual(rows(g)[y][x + 1], "v")                  # where the next tile goes
        press(g, "BS")
        self.assertEqual(g.word, "Q")
        press(g, "RIGHT")                                         # once a tile is laid, the word keeps its way
        self.assertEqual(g.start, (8, 8, "V"))
        press(g, "/", "x", "ESC")                                 # the command line, and back to entry mode
        self.assertEqual((g.buf, g.word), ("", "Q"))
        press(g, "ESC")                                           # Esc stops, and the tiles go back on the rack
        self.assertEqual((g.start, g.word), (None, ""))
        self.assertEqual(g.rack_left(), list("QUIZEA_"))
        press(g, "q")
        self.assertEqual(g.msg, "Q does nothing in Grid Words. Press ? to see the keys.")

    def test_the_score_as_you_type(self):
        g = self.screen()
        replies = {"CHK": "OK 32 QUIZ"}
        req = g.s.core.req

        async def scored(text):
            if text.startswith("CHK"):
                g.s.core.reqs.append(text)
                return [], replies["CHK"]
            return await req(text)
        g.s.core.req = scored
        press(g, "ENTER", *"quiz")
        self.assertEqual(rows(g)[21], " QUIZ scores 32. Press Enter to play it, or keep typing letters.")
        press(g, "ENTER")
        self.assertIn("MOV 3 PLAY 8 8 H QUIZ", g.s.core.reqs)


class Lobby(unittest.TestCase):
    """In the lobby a letter means what it means at a table, or nothing: N opens a table and the rest are
    commands. An invitation is a dialog with Accept and Decline."""

    def lobby(self, keys=(), invites=()):
        from test_adapters import TABS, FakeCore

        class Core(FakeCore):
            reqs = []

            async def req(self, text):
                self.reqs.append(text)
                if text == "IVL":
                    return list(invites), "OK"
                if text == "JON 40":                    # walt's table: sitting down there seats me
                    self.tabs = [t.replace("walt,-", "walt,abcdefghijkl") for t in self.tabs]
                return await FakeCore.req(self, text)
        io = FakeIO("ansi", list(keys))
        s = T.Session(io, Core(TABS), "ansi", "abcdefghijkl")
        s.enter = self.entered
        m = T.AnsiMenu(s)
        asyncio.run(m.load())
        self.went = []
        return m

    async def entered(self, tid, watch=False, sit=False):
        self.went.append((tid, watch, sit))
        return None

    def test_letters(self):
        self.assertEqual(set(T.AnsiMenu.HOTKEYS), {"n", "?"})
        m = self.lobby()
        for k in "rpqwxo":
            asyncio.run(m.on_key(k))
            self.assertEqual(m.msg, "%s does nothing in the lobby. Press ? to see the keys." % k.upper())
        self.assertEqual(m.screen()[0].lines()[23], " Arrows pick  Enter open  N new table  ? help  / type")

    def test_commands(self):
        m = self.lobby()
        asyncio.run(m.command("/join walt"))                # the open table where walt waits, sat down at there
        self.assertIn("JON 40", m.s.core.reqs)
        self.assertEqual(self.went[-1], ("40", False, False))
        asyncio.run(m.command("/watch 10"))
        self.assertEqual(self.went[-1], ("10", True, False))
        asyncio.run(m.command("/resume"))
        self.assertIn(self.went[-1][0], ("3", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21"))
        asyncio.run(m.command("/nosuch"))
        self.assertEqual(m.msg, "No such command: /nosuch. Type /help for the commands.")
        asyncio.run(m.command("/new reversi"))             # one name for each action
        self.assertEqual(m.msg, "No such command: /new. Type /help for the commands.")
        m = self.lobby([" "])
        asyncio.run(m.command("/accept walt"))             # an invitation is answered in its dialog
        self.assertIn("An invitation or an offer is answered in its own", screens_text(m))

    def test_an_invitation_is_a_dialog(self):
        inv = ["IVT 12 reversi/1 standard ann waiting"]
        m = self.lobby(["y"], invites=inv)
        m.sel = next(i for i, it in enumerate(m.items) if it[0] == "invite")
        asyncio.run(m.open_selected())
        self.assertEqual(self.went[-1], ("12", False, True))
        self.assertIn("ann invites you to play", screens_text(m))
        m = self.lobby(["ESC"], invites=inv)
        m.sel = next(i for i, it in enumerate(m.items) if it[0] == "invite")
        asyncio.run(m.open_selected())
        self.assertEqual(self.went, [])


class PlainMode(unittest.TestCase):
    """Plain mode follows the same rules line by line: the header names the game, the table and the players,
    the side text follows the panel's order, the prompt is the sentence line made short, Esc clears a typed
    line before it leaves, and help comes in two pages."""

    def plain(self, keys=()):
        from adapters.games import reversi
        io = FakeIO("plain", list(keys))
        io.out = []
        io.write = io.out.append
        core = ScreenCore(reversi.KEY, live_lines(reversi, 0), ("ann", "bob"))
        s = session(io, core, "plain", "ann")
        g = reversi.PlainScreen(s, "3", reversi.KEY, 0)
        g.names, g.v = ["ann", "bob"], g.parse(core.lines)
        return g, core

    def test_the_screen(self):
        g, _ = self.plain()
        text = "\n".join(g.lines())
        self.assertIn("Reversi, table 3, with bob. You are ann.", text)
        self.assertIn("Your turn.", text)
        self.assertRegex(g.prompt(), r"^Your turn\. Type a square such as /[a-h]\d\. > $")
        g.me = -1
        self.assertEqual(g.prompt(), "You are watching. > ")

    def test_esc_clears_the_line_first(self):
        g, core = self.plain(["h", "i", "ESC", "ESC"])
        self.assertIsNone(asyncio.run(g.run()))
        self.assertFalse([r for r in core.reqs if r.startswith("CHT")])

    def test_help_in_two_pages(self):
        g, _ = self.plain()
        asyncio.run(g.line_in("/help"))
        self.assertIn("Type /help 2 for the reference.", "".join(g.io.out))
        asyncio.run(g.line_in("/help 2"))
        self.assertIn("The letters name the columns from the left", "".join(g.io.out))

    def test_events_print_as_sentences(self):
        g, _ = self.plain()
        self.assertEqual(g.as_sentence("bob plays d3 (flips 2)"), "bob plays d3 (flips 2).")
        self.assertEqual(g.as_sentence("<bob> hi there"), "<bob> hi there")
