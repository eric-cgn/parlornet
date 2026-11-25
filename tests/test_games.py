"""The game registry (adapters/games/): every game module passes the generic checks, and a module dropped
into the package shows up in the web lobby, the telnet menu, the Games table and /games.htm with no edit
anywhere else."""
import asyncio
import contextlib
import glob
import io
import os
import re
import sys
import unittest

from game_checks import (ROOT, FakeIO, ScreenCore, T, ansi_screens, module_problems, page_link, plain_screen,
                         session)

from adapters import games  # noqa: E402
from adapters import web as W  # noqa: E402
from adapters.common import parse_games  # noqa: E402

GAMES_DIR = os.path.join(ROOT, "adapters", "games")

# The smallest game module there can be: what docs/games-howto.md asks for, and nothing else.
DUMMY = '''"""A game that exists only while tests/test_games.py runs (it writes this file and removes it)."""
import re

from ..telnet import AnsiGame, Canvas, GameBase, PlainGame, status_bar, title_bar
from ..web import esc, make_gif

KEY = "testdummy/1"
NAME = "Test Dummy"
ORDER = 999
PLAYERS = "2"
ABOUT = ("Two players poke each other in turn.", ["Standard rules: whoever pokes last wins."])
MOVE_RE = re.compile(r"^poke$")
SEL_RE = None


def parse_view(lines):
    v = {"rows": False, "turn": -1, "moves": [], "status": "", "seq": 0, "clock": None}
    for l in lines:
        f = l.split()
        if f and f[0] == "V":
            v["seq"] = int(f[2])
        elif f and f[0] == "W":
            v["turn"], v["rows"] = int(f[1]), True
        elif f and f[0] == "M":
            v["moves"].append(f[1])
        elif f and f[0] == "S":
            v["status"] = l[2:]
    return v


def describe_event(text, names):
    return "%s pokes" % names[int(text.split()[1])] if text.startswith("POKE ") else None


def images():
    return {"/img/testdummy/dot.gif": make_gif(2, 2, [(0, 0, 0), (255, 255, 255)], [0, 1, 1, 0])}


def web_parts(v, tid, link, seat, names, sel):
    board = '<img src="/img/testdummy/dot.gif" width="2" height="2" alt="dot">'
    act = '<a href="%s">Poke</a>' % esc(link("/move.htm", t=tid, mv="poke")) if seat == v["turn"] else ""
    return board, "<p>%s vs %s</p>" % (esc(names[0]), esc(names[1])), "", act


class DummyBase(GameBase):
    parse = staticmethod(parse_view)

    def describe(self, text):
        return describe_event(text, self.names) or super().describe(text)

    def move_word(self, cmd, words=()):
        return cmd if MOVE_RE.match(cmd) else None


class AnsiScreen(DummyBase, AnsiGame):
    TITLE, HELP = NAME, ["Type poke to poke."]

    def compose(self):
        cv = Canvas()
        title_bar(cv, self.g, "%s %s table %s" % (NAME, self.g["v"], self.tid))
        if not self.v["rows"]:
            self.draw_waiting(cv, 10, 8)
        self.draw_log(cv, 2, 40)
        self.input_line(cv)
        status_bar(cv, [("T", "talk"), ("?", "help"), ("Esc", "menu")])
        return cv


class PlainScreen(DummyBase, PlainGame):
    HELP_TEXT = " poke pokes. say <text>, resign, menu."

    def lines(self):
        return ["", "== %s, table %s ==" % (NAME, self.tid), "Waiting for an opponent." if not self.v["rows"]
                else "%s to poke." % self.name(self.v["turn"])]
'''


def lobby_html(games_list):
    """The lobby for a player, with links as web.py's link() makes them for a browser without cookies."""
    return W.lobby_page("amy", page_link, games_list, [])


class Registry(unittest.TestCase):
    def test_every_module_passes(self):
        self.assertEqual(games.load_problems(), {})
        keys = [m.KEY for m in games.all_games()]
        self.assertEqual(keys[:2], ["reversi/1", "backgammon/1"])
        paths = {}
        for m in games.all_games():
            word, ver = m.KEY.split("/")
            self.assertEqual(module_problems(m), [], m.KEY)
            self.assertTrue(os.path.exists(os.path.join(ROOT, "plugins", word, "plugin.py")), m.KEY)
            self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "types", "%s-%s.md" % (word, ver))), m.KEY)
            for p in m.images():
                self.assertNotIn(p, paths, "%s and %s both have %s" % (m.KEY, paths.get(p), p))
                paths[p] = m.KEY
            about, rules = m.ABOUT
            for text in [about, m.NAME] + rules:
                text.encode("ascii")
            self.assertTrue(re.match(r"^[0-9]+(-[0-9]+)?$", m.PLAYERS), m.KEY)

    def test_move_fields_are_checked(self):
        """MOVE_FIELDS (optional): short lower-case names, none the site uses itself, with move_from_fields."""
        import types
        from adapters.games import reversi
        fake = types.ModuleType("fake")
        fake.__dict__.update({k: getattr(reversi, k) for k in games.REQUIRED})
        self.assertEqual(games.problems(fake), [])
        for fields in (["mv"], ["t", "x"], ["Angle"], [], ["a"] * 2, ["a", "b", "c", "d", "e", "f", "g"],
                       "angle", ["toolongname"]):
            fake.MOVE_FIELDS = fields
            fake.move_from_fields = lambda vals: None
            self.assertIn("MOVE_FIELDS", " ".join(games.problems(fake)), fields)
        fake.MOVE_FIELDS = ["angle", "power"]
        self.assertEqual(games.problems(fake), [])
        del fake.move_from_fields
        self.assertIn("move_from_fields: missing", " ".join(games.problems(fake)))

    def test_every_game_waits_for_an_opponent(self):
        """Before the game starts (a view with no state yet), every screen of every game fits and says so."""
        for m in games.all_games():
            lines = ["V %s 1" % m.KEY, "S Waiting for players"]
            self.assertFalse(m.parse_view(lines)["rows"], m.KEY)
            for me in (0, -1):
                for mode, (shown, _) in ansi_screens(self, m, lines, me, keys=["?", "WAIT", "x", "ESC"]).items():
                    self.assertIn("Waiting for an opponent", shown, (m.KEY, mode, me))
                self.assertIn("Waiting for an opponent", plain_screen(self, m, lines, me))

    def test_lookups_follow_the_registry(self):
        self.assertEqual(list(T.RENDERERS), [m.KEY for m in games.all_games()])
        self.assertEqual(list(W.GAME_PAGES), [m.KEY for m in games.all_games()])
        self.assertEqual(T.RENDERERS["reversi/1"], (T.AnsiReversi, T.PlainReversi))
        self.assertEqual(W.GAME_PAGES["backgammon/1"][0], "Backgammon")
        self.assertIn("/img/h.gif", W.IMAGES)
        self.assertIn("/img/bg/d6.gif", W.IMAGES)
        self.assertNotIn("/img/nope.gif", W.IMAGES)
        self.assertNotIn("reversi/1x", T.RENDERERS)
        # the moved names are still found where they were
        from adapters.common import describe_bg_event, parse_backgammon_view, parse_reversi_view
        self.assertEqual(parse_reversi_view(["V reversi/1 4", "W 1"])["turn"], 1)
        self.assertEqual(parse_backgammon_view(["V backgammon/1 4", "D 3 1"])["dice"], [3, 1])
        self.assertEqual(describe_bg_event("ROLL 0 3 1", ["ann", "bob"]), "ann rolls 3-1")
        self.assertEqual(W.describe("PUT 1 d3 FLP d4", ["ann", "bob"], "reversi/1"), "bob plays d3 (flips 1)")


class DroppedModule(unittest.TestCase):
    """adapters/games/testdummy.py (and no other edit) puts Test Dummy in the lobby, the telnet menu, the
    Games table and /games.htm; removing it takes it out again."""

    def setUp(self):
        self.path = os.path.join(GAMES_DIR, "testdummy.py")
        self.assertFalse(os.path.exists(self.path))
        with open(self.path, "w") as f:
            f.write(DUMMY)
        games.reload()

    def tearDown(self):
        os.remove(self.path)
        for pyc in glob.glob(os.path.join(GAMES_DIR, "__pycache__", "testdummy.*")):
            os.remove(pyc)
        sys.modules.pop("adapters.games.testdummy", None)
        games.reload()

    def hosted(self):
        return parse_games(["GAM reversi/1 standard Reversi 2 2", "GAM backgammon/1 standard Backgammon 2 2",
                            "GAM testdummy/1 standard Test_Dummy 2 2"])

    def test_it_shows_up_everywhere(self):
        self.assertEqual(games.load_problems(), {})
        m = games.get("testdummy/1")
        self.assertIsNotNone(m)
        self.assertEqual(module_problems(m), [])
        # the web lobby: a Start button, as for every hosted game with a module
        lobby = lobby_html(self.hosted()).decode("ascii")
        self.assertIn('value="Start a game of Test Dummy"', lobby)
        self.assertIn('value="Start a game of Reversi"', lobby)
        # the web table page's pieces, its images and its move words
        self.assertEqual(W.GAME_PAGES["testdummy/1"][0], "Test Dummy")
        self.assertIn("/img/testdummy/dot.gif", W.IMAGES)
        self.assertTrue(any(g.MOVE_RE.match("poke") for g in games.all_games()))
        self.assertEqual(W.describe("POKE 1", ["ann", "bob"], "testdummy/1"), "bob pokes")
        # the telnet menu: a new-table entry, and its screens
        io = FakeIO("pc")
        s = session(io, ScreenCore("testdummy/1", ["V testdummy/1 1"]), "pc")
        menu = T.AnsiMenu(s)
        asyncio.run(menu.load())
        new = [it[1]["key"] for it in menu.mine if it[0] == "new"]
        self.assertEqual(new[:2], ["reversi/1", "backgammon/1"])
        self.assertIn("testdummy/1", new)
        menu.sel = next(i for i, it in enumerate(menu.items) if it[0] == "new" and it[1]["key"] == "testdummy/1")
        self.assertIn("Start a new Test Dummy table", "\n".join(menu.compose().lines()))
        self.assertEqual(T.RENDERERS["testdummy/1"], (m.AnsiScreen, m.PlainScreen))
        shown = ansi_screens(self, m, ["V testdummy/1 2", "W 0", "M poke"], 0, keys=["t", "WAIT", "x", "ESC"])
        self.assertIn("Test Dummy", shown["pc"][0])
        _, reqs = ansi_screens(self, m, ["V testdummy/1 2", "W 0", "M poke"], 0,
                               keys=list("poke") + ["ENTER"])["ansi"]
        self.assertIn("MOV 3 poke", reqs)
        # the games page and its own page: after the planned games
        plain = lambda h: re.sub(r"</?font[^>]*>", "", h)  # noqa: E731
        table = plain(W.games_page(self.hosted()).decode("ascii"))
        self.assertIn('<a href="/games/testdummy.htm"><b>Test Dummy</b></a></td><td align="center">2</td>', table)
        # the docs site's Clients page: its own section, saying it has no native client and linking to the
        # browsers and telnet programs, which are listed once at the bottom
        from adapters import clients as C
        from docsite import build
        frontends = plain(C.frontends_body(build.games()))
        section = frontends[frontends.index('<a name="testdummy">'):frontends.index('<a name="native">')]
        self.assertIn("No native client yet.", section)
        self.assertIn('<a href="/index.html#generic">Play by web or telnet</a>', section)
        self.assertNotIn("/setup/", section)
        names = re.findall(r'<a href="/games/[a-z0-9]+\.htm"><b>([^<]+)</b></a>', table)
        self.assertEqual(names[:2], ["Reversi", "Backgammon"])
        self.assertEqual(names[-1], "Test Dummy")
        self.assertIn("Rummy Tile Classic", names)
        self.assertIn('<td align="center">2</td><td>not on this server</td>',
                      plain(W.games_page(parse_games(["GAM reversi/1 standard R 2 2"])).decode("ascii")))
        index = plain(W.games_page(self.hosted()).decode("ascii"))
        self.assertIn('<a name="testdummy"></a><a href="/games/testdummy.htm"><b>Test Dummy</b></a>', index)
        self.assertIn("Two players poke each other in turn.", index)
        page = plain(W.game_page("testdummy", self.hosted()).decode("ascii"))
        self.assertIn("<h1>Test Dummy</h1>", page)
        self.assertIn("whoever pokes last wins", page)

    def test_removed_it_is_gone(self):
        self.tearDown()
        try:
            self.assertIsNone(games.get("testdummy/1"))
            self.assertNotIn("testdummy/1", T.RENDERERS)
            self.assertNotIn("Test Dummy", lobby_html(self.hosted()).decode("ascii"))
            self.assertNotIn("Test Dummy", W.games_page(None).decode("ascii"))
            from adapters import clients as C
            from docsite import build
            self.assertNotIn("Test Dummy", C.frontends_body(build.games()))
        finally:
            self.setUp()

    def test_a_broken_module_is_skipped(self):
        with open(self.path, "w") as f:
            f.write(DUMMY.replace("ORDER = 999\n", ""))
        sys.modules.pop("adapters.games.testdummy", None)
        games.reload()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(games.get("testdummy/1"))
        self.assertIn("games: testdummy not loaded: ORDER: missing", err.getvalue())
        self.assertIn("ORDER: missing", " ".join(games.load_problems()["testdummy"]))
        self.assertEqual([m.KEY for m in games.all_games()][:2], ["reversi/1", "backgammon/1"])


if __name__ == "__main__":
    unittest.main()
