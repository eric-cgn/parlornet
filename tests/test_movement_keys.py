"""Telnet movement keys: letters that move the cursor as the arrows do (IJKL, ESDF, WASD, HJKL), for
terminals that send no arrow keys. In every letter set: the letters move in the menu, a picker and every
game's board, never fire a hotkey, every hotkey and typed command is still reached (: opens the command
line; : then a hotkey letter does what the key does), Grid Words takes typed words, the status bar and the
help show the keys that apply, and the choice is kept with the player for the next sign-in (the core's
per-extension data, PRF). Arrows split across packets still arrive as arrows."""
import asyncio
import importlib.util
import os
import time
import unittest

from game_checks import ROOT, FakeIO, ScreenCore, T, session
from test_adapters import LONG, TABS, Client, FakeCore, TelnetBase

from adapters import games as G

SETS = ["ijkl", "esdf", "wasd", "hjkl"]
LETTERS = {m: T.MOVE_SETS[m][1] for m in SETS}         # up, down, left, right
_PLUGINS = {}


def plugin(word):
    if word not in _PLUGINS:
        spec = importlib.util.spec_from_file_location("mk_" + word, os.path.join(ROOT, "plugins", word, "plugin.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _PLUGINS[word] = m
    return _PLUGINS[word]


def live_lines(module, seat=0):
    """A two-player game just started, as VUE GMT shows it to seat (the plugin's own new, view and moves)."""
    word = module.KEY.split("/")[0]
    p = plugin(word)
    rules = p.op_info({})["rules"][0]
    st = p.op_new({"players": 2, "options": {}, "seed": 1, "rules": rules})["state"]
    r = p.op_view({"state": st, "seat": seat, "rules": rules})
    m = p.op_moves({"state": st, "seat": seat, "rules": rules}).get("moves", [])
    return (["V %s 5" % module.KEY] + r.get("geometry", []) + r["wire"] + ["CLK 0 250 300", "S " + r["status"]]
            + ["M " + (" ".join(x) if isinstance(x, list) else x) for x in m])


def board(module, moves, keys=(), me=0):
    """The module's full screen in pc mode for a session with these movement keys."""
    io = FakeIO("pc", list(keys))
    core = ScreenCore(module.KEY, live_lines(module, max(me, 0)), (LONG, "bob"))
    s = session(io, core, "pc")
    s.moves = moves
    g = module.AnsiScreen(s, "3", module.KEY, me)
    g.names, g.turn_secs, g.v = [LONG, "bob"], 300, g.parse(core.lines)
    g.snap()
    return g


def state(g):
    """What a key can change on a screen: its simple attributes (the clock's time left aside)."""
    return {k: v for k, v in vars(g).items()
            if k not in ("last_key", "clock_at") and isinstance(v, (int, float, str, tuple, list, bool, type(None)))}


def press(g, *keys):
    """Keys as they arrive on the line: queued ahead of the rest, so a hotkey letter sees typing follow."""
    g.io.keys[:0] = list(keys)

    async def go():
        for _ in keys:
            if not g.io.keys:
                break
            await g.on_key(await g.io.readkey())
    asyncio.run(go())


def record_hotkeys(g):
    """Replace the screen's hotkey with one that only records the keys."""
    got = []

    async def hotkey(k):
        got.append(k)
    g.hotkey = hotkey
    return got


def menu(moves, keys=()):
    io = FakeIO("pc", list(keys))
    s = T.Session(io, FakeCore(TABS), "pc", LONG, moves)
    m = T.AnsiMenu(s)
    asyncio.run(m.load())
    return m


class Boards(unittest.TestCase):
    def test_letters_move_on_every_board(self):
        """Each letter, either case, does on every board exactly what its arrow does, and sends nothing."""
        for module in G.all_games():
            moved = False
            for moves in SETS:
                start = state(board(module, moves))
                for d, letter in zip(T.DIRECTIONS, LETTERS[moves]):
                    by_arrow = board(module, moves)
                    press(by_arrow, d)
                    for k in (letter, letter.upper()):
                        by_letter = board(module, moves)
                        press(by_letter, k)
                        self.assertEqual(state(by_letter), state(by_arrow), (module.KEY, moves, k))
                        self.assertEqual(by_letter.s.core.reqs, by_arrow.s.core.reqs, (module.KEY, moves, k))
                        self.assertEqual(by_letter.buf, "", (module.KEY, moves, k))
                    moved = moved or state(by_arrow) != start
            self.assertTrue(moved, "%s: no arrow moved anything on the opening screen" % module.KEY)

    def test_no_movement_letter_fires_a_hotkey(self):
        for module in G.all_games():
            for moves in SETS:
                for letter in LETTERS[moves] + LETTERS[moves].upper():
                    g = board(module, moves)
                    got = record_hotkeys(g)
                    press(g, letter)
                    self.assertEqual((got, g.buf), ([], ""), (module.KEY, moves, letter))
                    self.assertFalse([r for r in g.s.core.reqs if r.split()[0] in ("MOV", "END", "UND", "RES", "CHT")],
                                     (module.KEY, moves, letter))

    def test_every_hotkey_is_reached(self):
        """A hotkey that is not a movement letter works as it always did; one that is: : then the letter, at
        once or with Enter. The arrows-only mode is unchanged."""
        for module in G.all_games():
            for moves in ["arrows"] + SETS:
                for h in module.AnsiScreen.HOTKEYS:
                    g = board(module, moves)
                    got = record_hotkeys(g)
                    if g.move_of(h):
                        press(g, ":", h)                    # nothing follows: the key
                    else:
                        press(g, h)
                    self.assertEqual(got, [h], (module.KEY, moves, h))
                    g = board(module, moves)
                    got = record_hotkeys(g)
                    press(g, ":", h, "ENTER")               # Enter follows at once: the line :h, run
                    self.assertEqual((got, g.buf), ([h], ""), (module.KEY, moves, h))

    def test_typed_commands_after_colon(self):
        """: opens the command line on every board in every mode: text starting with a movement letter
        types, and the command runs without the colon."""
        for module in G.all_games():
            for moves in SETS:
                g = board(module, moves)
                press(g, ":", *"say hello", "ENTER")
                self.assertIn("CHT 3 hello", g.s.core.reqs, (module.KEY, moves))
                g = board(module, moves, keys=["n"])
                press(g, ":", *"resign", "ENTER")       # asks first: no
                self.assertNotIn("RES 3", g.s.core.reqs)
                self.assertTrue(any("Resign this game?" in "\n".join(scr) for scr in g.io.screens), module.KEY)

    def test_status_bar_and_help_show_the_keys(self):
        from adapters.games import backgammon, navalcombat, reversi
        g = board(reversi, "ijkl")
        g.v["moves"] = g.v["moves"] or ["d3"]
        cv, _ = g.screen()
        self.assertIn("IJKL move", cv.lines()[23])
        self.assertNotIn("Arrows", cv.lines()[23])
        cv = board(reversi, "arrows").screen()[0]
        self.assertIn("Arrows move", cv.lines()[23])
        g = board(navalcombat, "wasd")
        bar = g.screen()[0].lines()[23]
        self.assertIn("WASD move", bar)
        self.assertIn(":d turn", bar)
        self.assertIn(":a random", bar)
        self.assertIn("Esc menu", bar)                  # still fits
        self.assertIn(":d turn", g.screen()[0].lines()[20])
        g = board(navalcombat, "ijkl")
        self.assertIn("D turn", g.screen()[0].lines()[23])
        # a click on ":d" is the D key, not a movement
        g = board(navalcombat, "wasd")
        cv = g.screen()[0]
        g.s.painter.prev = cv
        got = record_hotkeys(g)
        x = cv.lines()[23].index(":d")
        press(g, ("CLICK", x, 23))
        self.assertEqual(got, ["d"])
        # the help box: the letters first, then the game's own help; on two pages when they do not fit
        for module in G.all_games():
            for moves in SETS:
                g = board(module, moves, keys=["x", "x"])
                asyncio.run(g.help())
                text = "\n".join("\n".join(scr) for scr in g.io.screens)
                up, down, left, right = LETTERS[moves].upper()
                self.assertIn("%s up, %s left, %s down, %s right" % (up, left, down, right), text, module.KEY)
                self.assertIn("type : first", text)
                self.assertIn(module.AnsiScreen.HELP[0], text)
                taken = [c for c in moves if c in module.AnsiScreen.HOTKEYS]
                for c in taken:
                    self.assertIn(":" + c, text.split("type : first")[1], (module.KEY, moves))
        g = board(backgammon, "esdf", keys=["x", "x"])
        asyncio.run(g.help())
        self.assertIn("The E and D keys: type :e or :d.", "\n".join("\n".join(s) for s in g.io.screens))
        g = board(reversi, "arrows")
        self.assertEqual(g.keys_help(), [])

    def test_hints_name_the_colon_keys(self):
        from adapters.games import backgammon, checkers
        g = board(checkers, "wasd")
        g.v["phase"] = "draw"
        self.assertEqual(g.kn("A"), ":a")
        self.assertEqual(g.kn("N"), "N")
        g = board(backgammon, "ijkl")
        self.assertEqual(g.kn("D"), "D")
        g = board(backgammon, "esdf")
        self.assertEqual(g.kn("D"), ":d")


class GridWords(unittest.TestCase):
    def test_typing_words_in_every_mode(self):
        from adapters.games import gridwords
        for moves in ["arrows"] + SETS:
            word = (LETTERS.get(moves) or "word").upper()         # every movement letter in the word
            for case in (str.upper, str.lower):
                g = board(gridwords, moves)
                g.cur = (8, 8)
                press(g, "ENTER")                                   # a start, across, on h8
                self.assertEqual(g.start, (8, 8, "H"))
                press(g, *case(word))
                self.assertEqual(g.buf, case(word), moves)
                self.assertEqual(g.cur, (8, 8))                     # nothing moved
                self.assertEqual("".join(g.pending()[(8 + i, 8)] for i in range(4)), word)
                press(g, "ENTER")
                self.assertIn("MOV 3 PLAY 8 8 H %s" % word, g.s.core.reqs, moves)
            g = board(gridwords, moves)                              # no start: : opens the line
            press(g, ":", *"h8 d %s" % word.lower(), "ENTER")
            self.assertIn("MOV 3 PLAY 8 8 V %s" % word, g.s.core.reqs, moves)
            g = board(gridwords, moves)                              # a start and : together
            g.cur = (3, 4)
            press(g, "ENTER", ":", *word, "ENTER")
            self.assertIn("MOV 3 PLAY 3 4 H %s" % word, g.s.core.reqs, moves)

    def test_start_then_letters_and_status(self):
        from adapters.games import gridwords
        g = board(gridwords, "hjkl")
        press(g, "l")
        self.assertEqual(g.cur, (9, 8))
        self.assertIn("type :h8 a WORD", g.hint()[0])
        press(g, "ENTER", "ESC")                                    # Esc clears the start: letters move again
        press(g, "h")
        self.assertEqual((g.cur, g.buf), ((8, 8), ""))
        press(g, "ENTER")
        self.assertIn("Arrow keys move", g.screen()[0].lines()[23])


class Menu(unittest.TestCase):
    def test_letters_move_in_the_menu(self):
        for moves in SETS:
            up, down, left, right = LETTERS[moves]
            m = menu(moves)
            got = record_hotkeys(m)
            m.sel = 0
            for k in (down, down.upper(), down):
                asyncio.run(m.on_key(k))
            self.assertEqual(m.sel, 3, moves)
            asyncio.run(m.on_key(up))
            self.assertEqual(m.sel, 2)
            for k in (left, right, left.upper(), right.upper()):
                asyncio.run(m.on_key(k))
            self.assertEqual((m.sel, m.buf, got), (2, "", []), moves)
            bar = m.screen()[0].lines()[23]
            self.assertIn("%s pick" % moves.upper(), bar)
            self.assertIn("Q quit", bar)

    def test_watch_key_under_wasd(self):
        m = menu("wasd")
        got = record_hotkeys(m)
        m.sel = 0
        asyncio.run(m.on_key("w"))
        self.assertEqual((got, m.sel), ([], 0))                     # W is up here (already at the top)
        asyncio.run(m.on_key(":"))
        asyncio.run(m.on_key("w"))
        self.assertEqual(got, ["w"])
        self.assertIn(":w watch", m.screen()[0].lines()[23])
        m = menu("wasd")
        m.sel = next(i for i, it in enumerate(m.items) if it[0] == "table" and it[1]["id"] == "40")
        self.assertIn(":w watches", m.describe())

    def test_picker(self):
        for moves in SETS:
            up, down, left, right = LETTERS[moves]
            m = menu(moves, keys=[down, right.upper(), down, up, "ENTER"])
            i = asyncio.run(m.choose("Pick", ["One of four"], ["a", "b", "c", "d"], 0))
            self.assertEqual(i, 2, moves)
            self.assertTrue(any("%s and Enter, or 1-4" % moves.upper() in "\n".join(s) for s in m.io.screens))
            m = menu(moves, keys=["DOWN", "ENTER"])                 # the arrows still work
            self.assertEqual(asyncio.run(m.choose("Pick", ["x"], ["a", "b"], 0)), 1)


class Setting(unittest.TestCase):
    def test_options_key_sets_it_for_the_session(self):
        m = menu("arrows", keys=["WAIT", "DOWN", "DOWN", "DOWN", "ENTER"])
        self.assertEqual(m.move_set(), "")
        asyncio.run(m.on_key("o"))
        self.assertEqual(m.s.moves, "wasd")
        self.assertIn("Movement keys: WASD, kept for next time", m.msg)
        self.assertTrue(any("Movement keys" in "\n".join(s) and "HJKL (vi)" in "\n".join(s) for s in m.io.screens))
        # a table opened later in the session has the same keys
        from adapters.games import reversi
        g = reversi.AnsiScreen(m.s, "3", reversi.KEY, 0)
        self.assertEqual(g.move_set(), "wasd")
        # typed, too: options (or keys); back to the arrows
        m.io.keys = ["1"]
        asyncio.run(m.command("keys"))
        self.assertEqual((m.s.moves, m.move_set()), ("arrows", ""))
        self.assertEqual(m.msg, "Movement keys: the arrows only.")
        # a session made without a choice has the arrows only (main_session passes the kept one)
        self.assertEqual(T.Session(FakeIO("pc"), FakeCore(TABS), "pc", LONG).moves, "arrows")
        self.assertEqual(T.Session(FakeIO("pc"), FakeCore(TABS), "pc", LONG, "nonsense").moves, "arrows")

    def test_terminal_types(self):
        for tt in ("xterm-256color", "VT100", "vt220", "ansi", "PuTTY", "linux", "syncterm"):
            self.assertTrue(T.arrows_for_ttype(tt), tt)
        for tt in (None, "", "vt52", "DEC-VT52", "dumb", "adm3a", "wyse50", "tvi925", "mac", "unknown"):
            self.assertFalse(T.arrows_for_ttype(tt), tt)

    def test_help_lists_the_options_key(self):
        m = menu("ijkl", keys=["x", "x"])
        asyncio.run(m.help())
        text = "\n".join("\n".join(s) for s in m.io.screens)
        self.assertIn("O  options: letter keys that move the cursor", text)
        self.assertIn("I up, J left, K down, L right", text)


class SplitArrows(unittest.TestCase):
    def test_arrow_split_across_packets_is_still_an_arrow(self):
        """ESC [ A in three packets is UP, not the letter A (left in WASD)."""
        from test_telnet_naws import FakeWriter
        from adapters.games import reversi
        io = T.TelnetIO(None, FakeWriter())
        for part in (b"\x1b", b"[", b"A", b"\x1bO", b"D", b"a"):
            io._feed(part)
        self.assertEqual(io.keys, ["UP", "LEFT", "a"])
        g = board(reversi, "wasd")
        g.cur = (3, 3)
        press(g, *io.keys)
        self.assertEqual(g.cur, (1, 2))                     # up, left, and a: left again


class Login(TelnetBase):
    """The login offers the movement keys when the terminal type suggests no arrow keys."""
    auth = "none"

    def test_offered_without_a_terminal_type(self):
        c = Client(self.tel_port)
        c.until("Login")
        c.send("new\r\n")
        c.until("Name you want")
        c.send("nina\r\n")
        c.until("Choice [")
        c.send("1\r\n")
        out = c.until("Press Enter for the menu, or K and Enter to choose movement keys.")
        self.assertIn("Your terminal may not send arrow keys", out)
        c.send("k\r\n")
        c.until("4) WASD       W up, A left, S down, D right")
        c.until("Choice [1]: ")
        c.send("2\r\n")
        c.until("IJKL pick", screen=True)
        c.until("Choose a table with IJKL or the arrows", screen=True)
        self.hint_says(c, "Reversi")
        c.send("k")                                         # down
        self.hint_says(c, "Backgammon")
        c.send("i")                                         # up
        self.hint_says(c, "Reversi")
        c.close()

    def test_kept_for_the_next_sign_in(self):
        """IJKL chosen with O, log off, sign in again: IJKL is active."""
        c = Client(self.tel_port)
        self.login(c, "ivy", "1")
        c.send("o")
        c.until("The choice is kept for your next visit.", screen=True)
        c.send("\x1b[B\r")                                 # down to IJKL, Enter
        c.until("Movement keys: IJKL, kept for next time.", screen=True)
        c.send("q")
        c.until("Log off?", screen=True)
        c.send("y")
        c.until("Bye.")
        c.close()
        c = Client(self.tel_port)
        out = self.login(c, "ivy", "1", new=False)
        self.assertIn("Movement keys: IJKL, as you chose before", out)
        c.until("IJKL pick", screen=True)
        self.hint_says(c, "Reversi")
        c.send("k")                                         # down
        self.hint_says(c, "Backgammon")
        c.close()

    def hint_says(self, c, text):
        end = time.time() + 8
        while text not in c.vt.lines()[21]:
            self.assertLess(time.time(), end, c.vt.text())
            c.pump()

    def test_not_offered_to_xterm(self):
        c = Client(self.tel_port, enc="utf-8", ttype="xterm-256color")
        c.until("Login")
        c.send("new\r\n")
        c.until("Name you want")
        c.send("otto\r\n")
        c.until("Choice [")
        c.send("2\r\n")
        out = c.until("Press Enter for the menu.")
        self.assertNotIn("movement keys", out)
        c.send("\r\n")
        c.until("Arrows pick", screen=True)
        c.close()


if __name__ == "__main__":
    unittest.main()
