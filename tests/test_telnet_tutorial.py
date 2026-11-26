"""The tutorial (adapters/telnet_tutorial.py): a lesson in Tic Tac Toe on the player's own screen. The
whole walk-through runs against a core that fails on any request, which proves that the lesson sends
nothing to the parlor. A key the step doesn't ask for gets the same instruction again, starting with "Not
yet." The lobby lists it, /tutorial opens it without reaching the core, and the first sign-in on a full
screen offers it once."""
import asyncio
import unittest

from game_checks import FakeIO, T

from adapters import telnet_tutorial as P


class NoCore:
    """A core that fails on any request."""
    auth = "password"

    def __init__(self):
        self.reqs = []

    async def req(self, text):
        self.reqs.append(text)
        raise AssertionError("the tutorial sent %r" % text)


WALK = [
    ["ENTER"],                                  # 1 begin
    ["RIGHT", "DOWN"],                          # 2 to the middle square
    ["ENTER"],                                  # 3 X on b2, and O on a1
    ["RIGHT", "UP", "ENTER"],                   # 4 X on c1
    ["u"],                                      # 5 take it back
    ["ENTER"],                                  # 6 X on c1 again, and O on a3
    ["/"],                                      # 7 the command line
    list("time") + ["ENTER"],                   # 8 /time
    ["/", "x", "ESC"],                          # 9 Esc clears the line
    ["/", "c", "3", "ENTER"],                   # 10 a typed move, and O on b1
    ["t"],                                      # 11 the chat screen
    list("hello") + ["ENTER"],                  # 12 said, and the partner answers
    ["ESC"],                                    # 13 back to the board
    ["/"] + list("say good game") + ["ENTER"],  # 14 /say
    ["?", " ", "ESC"],                          # 15 help, both pages
    ["r", "ESC"],                               # 16 the resign dialog, answered with Esc
    ["DOWN", "ENTER"],                          # 17 X on c2 wins
]


def tutorial(keys, moves="arrows"):
    io = FakeIO("ansi", list(keys))
    core = NoCore()
    s = T.Session(io, core, "ansi", "ann")
    s.moves = moves
    return P.Tutorial(s), io, core


class WalkThrough(unittest.TestCase):
    def test_the_whole_lesson_sends_nothing(self):
        keys = [k for step in WALK for k in step] + ["ESC"]       # 18 Esc goes back to the lobby
        g, io, core = tutorial(keys)
        self.assertIsNone(asyncio.run(g.run()))
        self.assertEqual(core.reqs, [])
        self.assertEqual(g.step, len(P.STEPS) - 1)
        self.assertEqual(g.winner(), ("c1", "c2", "c3"))
        self.assertEqual({s for s, m in g.board.items() if m == "O"}, {"a1", "a3", "b1"})
        shown = "\n".join("\n".join(s) for s in io.screens)
        for text in ("Tic Tac Toe tutorial", P.ONLY_HERE, "Step 2 of 18.", "Step 18 of 18.",
                     "Your partner placed an O on a1.", "Server time ", "<ann> hello",
                     "<tutorial partner> Hello!", "<ann> good game", "Tic Tac Toe tutorial help, page 2 of 2",
                     "Resign this game?", "You won the tutorial game.", "This game has no clock.",
                     "/tutorial runs it again any time."):
            self.assertIn(text, shown, text)

    def test_each_instruction_in_turn(self):
        g, io, core = tutorial([])
        for i, step in enumerate(WALK):
            self.assertEqual(g.hint()[0], P.STEPS[i][0], i + 1)
            io.keys[:] = step
            for _ in step:
                try:
                    asyncio.run(g.on_key(asyncio.run(io.readkey())))
                except EOFError:
                    break
            self.assertEqual(g.step, i + 1, "step %d: %s" % (i + 1, P.STEPS[i][0]))
        self.assertEqual(core.reqs, [])

    def test_not_yet(self):
        g, io, core = tutorial(["ENTER", "ENTER"])          # begin, then Enter on a1, not the middle
        asyncio.run(g.on_key("ENTER"))
        asyncio.run(g.on_key("ENTER"))
        self.assertEqual(g.step, 1)
        self.assertEqual(g.hint()[0], "Not yet. " + P.STEPS[1][0])
        self.assertEqual(g.board, {})

    def test_commands_that_need_the_parlor(self):
        g, io, core = tutorial([])
        for cmd in ("who", "invite bot", "list", "note hi"):
            asyncio.run(g.command(cmd))
            self.assertEqual(g.msg, P.SAYS_NOTHING)
        self.assertEqual(core.reqs, [])

    def test_letter_keys(self):
        g, io, core = tutorial([], moves="wasd")
        g.step = 1
        self.assertEqual(g.hint()[0], "Press the arrows or WASD to move to the middle square.")

    def test_the_keys_line_shows_u_only_when_it_applies(self):
        g, io, core = tutorial([])
        self.assertNotIn("U undo", g.screen()[0].lines()[23])
        g.step = 4
        self.assertIn("U undo", g.screen()[0].lines()[23])


class WhereItStarts(unittest.TestCase):
    def test_the_lobby_lists_it(self):
        from test_adapters import TABS, FakeCore
        s = T.Session(FakeIO("ansi"), FakeCore(TABS), "ansi", "abcdefghijkl")
        m = T.AnsiMenu(s)
        asyncio.run(m.load())
        self.assertEqual(m.items[-1], ("tutorial", None))
        m.sel = len(m.items) - 1
        self.assertEqual(m.describe(), "Press Enter to run the tutorial. It is not played on the parlor.")
        opened = []

        async def tutorial():
            opened.append(True)
        s.tutorial = tutorial
        asyncio.run(m.open_selected())
        asyncio.run(m.command("/tutorial"))
        self.assertEqual(opened, [True, True])

    def test_help_page_2_names_it(self):
        from adapters import games as G
        from test_telnet_modes import board
        for module in G.all_games():
            self.assertEqual(board(module, 0).help_pages()[1][-1], "Type /tutorial for a short lesson on these keys.")

    def test_the_first_sign_in_offers_it_once(self):
        """The question comes when no answer is kept, and the answer is kept either way."""
        class Core:
            auth = "password"

            def __init__(self, kept):
                self.kept, self.saved = kept, []

            async def load_pref(self, key, choices, default):
                return self.kept if key == T.TUTORIAL_PREF else default

            async def save_pref(self, key, value):
                self.saved.append((key, value))
                return True

            async def req(self, text):
                return [], "OK"
        for kept, keys, saved, opened in (("new", ["y"], "yes", True), ("new", ["n"], "no", False),
                                          ("new", ["ESC"], "no", False), ("yes", [], None, False),
                                          ("no", [], None, False)):
            core = Core(kept)
            io = FakeIO("ansi", keys)
            s = T.Session(io, core, "ansi", "ann")
            went = []

            async def tutorial():
                went.append(True)
            s.tutorial = tutorial
            asyncio.run(s.offer_tutorial())
            self.assertEqual(core.saved, [(T.TUTORIAL_PREF, saved)] if saved else [], kept)
            self.assertEqual(bool(went), opened, keys)
            shown = "\n".join("\n".join(screen) for screen in io.screens)
            self.assertEqual("Would you like a short tutorial" in shown, kept == "new", kept)


class Kept:
    """A core that keeps PRF values the way the server does, and records every request."""
    auth = "password"

    def __init__(self):
        from test_adapters import TABS
        self.prefs, self.reqs, self.tabs = {}, [], TABS

    async def load_pref(self, key, choices, default):
        value = self.prefs.get(key, "")
        return value if value in choices else default

    async def save_pref(self, key, value):
        self.prefs[key] = value
        return True

    async def req(self, text):
        from test_adapters import FakeCore
        self.reqs.append(text)
        return await FakeCore(self.tabs).req(text)


def sign_in(core, keys):
    """One sign-in on the full screen, until the keys run out. It returns what the screen showed."""
    io = FakeIO("ansi", keys)
    s = T.Session(io, core, "ansi", "ann")
    try:
        asyncio.run(s.run())
    except EOFError:
        pass
    return "\n".join("\n".join(screen) for screen in io.screens)


class FirstSignIn(unittest.TestCase):
    def test_asked_once_and_not_again(self):
        core = Kept()
        first = sign_in(core, ["n"])
        self.assertIn("Would you like a short tutorial on how the games", first)
        self.assertIn("work? You can run it again any time with /tutorial.", first)
        self.assertEqual(core.prefs, {T.TUTORIAL_PREF: "no"})
        second = sign_in(core, [])
        self.assertNotIn("Would you like a short tutorial", second)
        self.assertIn("Your tables", second)

    def test_yes_runs_the_tutorial_and_is_kept(self):
        core = Kept()
        shown = sign_in(core, ["y", "ESC"])             # Yes, then Esc leaves the tutorial for the lobby
        self.assertIn(P.STEPS[0][0], shown)
        self.assertIn("Your tables", shown)
        self.assertEqual(core.prefs, {T.TUTORIAL_PREF: "yes"})
        self.assertNotIn("Would you like a short tutorial", sign_in(core, []))


class NeverSent(unittest.TestCase):
    """/tutorial is one of the commands the client handles itself (docs/design.md), so it never reaches the
    core, from the lobby or from a table, on the full screen or in plain mode."""

    def assertNotSent(self, core):
        self.assertFalse([r for r in core.reqs if "tutorial" in r.lower()], core.reqs)

    def test_from_the_lobby(self):
        core = Kept()
        core.prefs[T.TUTORIAL_PREF] = "no"
        shown = sign_in(core, ["/"] + list("tutorial") + ["ENTER", "ESC"])
        self.assertIn(P.STEPS[0][0], shown)
        self.assertIn(P.ONLY_HERE, shown)
        self.assertNotSent(core)

    def test_from_a_table(self):
        from adapters import games as G
        from test_telnet_modes import board
        for module in G.all_games():
            g = board(module, 0)
            sent, ask = [], g.s.core.req

            async def req(text, ask=ask):
                sent.append(text)
                return await ask(text)
            g.s.core.req = g.core.req = req
            self.assertEqual(asyncio.run(g.run_command("tutorial")), "menu", module.KEY)
            self.assertTrue(g.s.tutorial_next, module.KEY)
            self.assertEqual(sent, [], module.KEY)

    def test_in_plain_mode(self):
        core = Kept()
        s = T.Session(FakeIO("plain"), core, "plain", "ann")
        m = T.PlainMenu(s)
        shown = []

        async def notice(text):
            shown.append(text)
        m.notice = notice
        asyncio.run(m.command("/tutorial"))
        self.assertEqual(shown, [T.PLAIN_TUTORIAL])
        self.assertNotSent(core)


if __name__ == "__main__":
    unittest.main()
