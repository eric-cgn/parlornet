"""The practice game (adapters/telnet_practice.py): a lesson in Tic Tac Toe on the player's own screen. The
whole walk-through runs against a core that fails on any request, which proves that the lesson sends
nothing to the parlor. A key the step doesn't ask for gets the same instruction again, starting with "Not
yet." The lobby lists it, /practice opens it, and the first sign-in on a full screen offers it once."""
import asyncio
import unittest

from game_checks import FakeIO, T

from adapters import telnet_practice as P


class NoCore:
    """A core that fails on any request."""
    auth = "password"

    def __init__(self):
        self.reqs = []

    async def req(self, text):
        self.reqs.append(text)
        raise AssertionError("the practice game sent %r" % text)


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


def practice(keys, moves="arrows"):
    io = FakeIO("ansi", list(keys))
    core = NoCore()
    s = T.Session(io, core, "ansi", "ann")
    s.moves = moves
    return P.Practice(s), io, core


class WalkThrough(unittest.TestCase):
    def test_the_whole_lesson_sends_nothing(self):
        keys = [k for step in WALK for k in step] + ["ESC"]       # 18 Esc goes back to the lobby
        g, io, core = practice(keys)
        self.assertIsNone(asyncio.run(g.run()))
        self.assertEqual(core.reqs, [])
        self.assertEqual(g.step, len(P.STEPS) - 1)
        self.assertEqual(g.winner(), ("c1", "c2", "c3"))
        self.assertEqual({s for s, m in g.board.items() if m == "O"}, {"a1", "a3", "b1"})
        shown = "\n".join("\n".join(s) for s in io.screens)
        for text in ("Tic Tac Toe practice", P.ONLY_HERE, "Step 2 of 18.", "Step 18 of 18.",
                     "Your partner placed an O on a1.", "Server time ", "<ann> hello",
                     "<practice partner> Hello!", "<ann> good game", "Tic Tac Toe practice help, page 2 of 2",
                     "Resign this game?", "You won the practice game.", "This game has no clock."):
            self.assertIn(text, shown, text)

    def test_each_instruction_in_turn(self):
        g, io, core = practice([])
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
        g, io, core = practice(["ENTER", "ENTER"])          # begin, then Enter on a1, not the middle
        asyncio.run(g.on_key("ENTER"))
        asyncio.run(g.on_key("ENTER"))
        self.assertEqual(g.step, 1)
        self.assertEqual(g.hint()[0], "Not yet. " + P.STEPS[1][0])
        self.assertEqual(g.board, {})

    def test_commands_that_need_the_parlor(self):
        g, io, core = practice([])
        for cmd in ("who", "invite bot", "list", "note hi"):
            asyncio.run(g.command(cmd))
            self.assertEqual(g.msg, P.SAYS_NOTHING)
        self.assertEqual(core.reqs, [])

    def test_letter_keys(self):
        g, io, core = practice([], moves="wasd")
        g.step = 1
        self.assertEqual(g.hint()[0], "Press the arrows or WASD to move to the middle square.")

    def test_the_keys_line_shows_u_only_when_it_applies(self):
        g, io, core = practice([])
        self.assertNotIn("U undo", g.screen()[0].lines()[23])
        g.step = 4
        self.assertIn("U undo", g.screen()[0].lines()[23])


class WhereItStarts(unittest.TestCase):
    def test_the_lobby_lists_it(self):
        from test_adapters import TABS, FakeCore
        s = T.Session(FakeIO("ansi"), FakeCore(TABS), "ansi", "abcdefghijkl")
        m = T.AnsiMenu(s)
        asyncio.run(m.load())
        self.assertEqual(m.items[-1], ("practice", None))
        m.sel = len(m.items) - 1
        self.assertEqual(m.describe(), "Press Enter to play the practice game. It is not played on the parlor.")
        opened = []

        async def practice():
            opened.append(True)
        s.practice = practice
        asyncio.run(m.open_selected())
        asyncio.run(m.command("/practice"))
        self.assertEqual(opened, [True, True])

    def test_help_page_2_names_it(self):
        from adapters import games as G
        from test_telnet_modes import board
        for module in G.all_games():
            self.assertEqual(board(module, 0).help_pages()[1][-1], "Type /practice for a short lesson on these keys.")

    def test_the_first_sign_in_offers_it_once(self):
        class Core:
            auth = "password"

            def __init__(self, kept):
                self.kept, self.saved = kept, []

            async def load_pref(self, key, choices, default):
                return self.kept if key == T.PRACTICE_PREF else default

            async def save_pref(self, key, value):
                self.saved.append((key, value))
                return True

            async def req(self, text):
                return [], "OK"
        for kept, keys, asked, opened in (("new", ["y"], True, True), ("new", ["ESC"], True, False),
                                          ("asked", [], False, False)):
            core = Core(kept)
            s = T.Session(FakeIO("ansi", keys), core, "ansi", "ann")
            went = []

            async def practice():
                went.append(True)
            s.practice = practice
            asyncio.run(s.offer_practice())
            self.assertEqual(core.saved, [(T.PRACTICE_PREF, "asked")] if asked else [], kept)
            self.assertEqual(bool(went), opened, keys)


if __name__ == "__main__":
    unittest.main()
