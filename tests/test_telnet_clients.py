"""The telnet extension tells what each player plays with (docs/protocol.md, Clients and machines): a JOIN and
its CLIENT event as one line of the table's log, a later CLIENT as "now plays", nothing for a CLIENT with no
text, the players' programs when a table opens, and the menus' table lines (LST clients)."""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from adapters import telnet as T  # noqa: E402
from adapters.games.reversi import AnsiScreen, PlainScreen  # noqa: E402

GM = "with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus"
APPLE = "with ParlorNet for the Apple IIe 1.1 on an Apple IIe"


class Core:
    """Answers a table screen's requests: the events given, a reversi view (with CLIENT lines), no notes."""

    auth = "none"

    def __init__(self, events, clients=(), tabs=None):
        self.events, self.clients, self.sent = list(events), list(clients), []
        self.tabs = tabs if tabs is not None else ["TAB 7 reversi/1 standard 2/2 playing ann,bob turn=300"]

    async def req(self, text):
        self.sent.append(text)
        verb = text.split()[0]
        if verb == "SIN":
            n = int(text.split()[2])
            evs = [e for i, e in enumerate(self.events, 1) if i > n]
            return ["EVT 7 %d %s" % (i, e) for i, e in enumerate(self.events, 1) if i > n], "OK %d" % len(self.events)
        if verb == "VUE":
            return (["V reversi/1 %d" % len(self.events), "BRD 8 8"] + ["R %d ........" % i for i in range(1, 9)]
                    + ["C 2 2", "W 0", "S x"] + list(self.clients)), "OK"
        if verb == "LST":
            return list(self.tabs), "OK"
        return [], "OK"


class Sess:
    def __init__(self, core, name="bob"):
        self.core, self.io, self.name = core, None, name


def screen(core, me=1, cls=PlainScreen):
    g = cls.__new__(cls)
    T.GameBase.__init__(g, Sess(core), "7", "reversi/1", me)
    return g


class Log(unittest.TestCase):
    def test_join_and_client_one_line(self):
        core = Core(["JOIN 0 ann", "CLIENT 0 " + GM])
        g = screen(core)
        g.names = ["-", "bob"]
        new = asyncio.run(g.pull())
        self.assertEqual(g.log, ["ann sat down " + GM])
        self.assertEqual(new, [("JOIN", "ann sat down " + GM)], "one line; the board redraws for the JOIN")
        self.assertEqual(g.clients, {0: GM})

    def test_change_mid_game(self):
        core = Core(["JOIN 0 ann", "JOIN 1 bob", "START", "TURN 0"])
        g = screen(core)
        asyncio.run(g.pull())
        core.events += ["CLIENT 0 " + APPLE, "PUT 0 d3 FLP d4"]
        new = asyncio.run(g.pull())
        self.assertEqual(new[0], ("CLIENT", "ann now plays " + APPLE))
        self.assertIn("ann now plays " + APPLE, g.log)
        self.assertNotIn("CLIENT", " ".join(g.log), "never printed raw")

    def test_bare_client_says_nothing(self):
        core = Core(["JOIN 0 ann", "CLIENT 0", "JOIN 1 bob", "CLIENT 1"])
        g = screen(core)
        new = asyncio.run(g.pull())
        self.assertEqual(g.log, ["ann sat down", "bob sat down"])
        self.assertEqual([k for k, _ in new], ["JOIN", "JOIN"])
        self.assertEqual(g.clients, {})

    def test_opening_a_table_says_what_each_opponent_plays_with(self):
        """The events shown on opening start after the JOINs: the view's CLIENT lines say it instead."""
        events = ["JOIN 0 ann", "CLIENT 0 " + GM, "JOIN 1 bob", "START"] + ["TURN %d" % (i % 2) for i in range(20)]
        core = Core(events, ["CLIENT 0 " + GM, "CLIENT 1 with telnet"])
        g = screen(core)
        self.assertTrue(asyncio.run(g.open_view()))
        self.assertIn("VUE 7 GMTC", core.sent)
        self.assertIn("ann plays " + GM, g.log)
        self.assertNotIn("bob plays with telnet", g.log, "not my own")
        self.assertTrue(g.v["rows"], "the CLIENT lines do not reach the game's view parser")

    def test_opening_with_the_join_in_view(self):
        core = Core(["JOIN 0 ann", "CLIENT 0 " + GM], ["CLIENT 0 " + GM])
        g = screen(core)
        asyncio.run(g.open_view())
        self.assertEqual([l for l in g.log if "GameMaster" in l], ["ann sat down " + GM], "said once")

    def test_every_game_describe_lets_client_pass(self):
        """CLIENT never reaches a game's own event texts or board."""
        for key, (ansi, plain) in T.RENDERERS.items():
            core = Core(["JOIN 0 ann", "CLIENT 0 " + GM, "CLIENT 0 " + APPLE])
            g = screen(core, cls=plain)
            asyncio.run(g.pull())
            self.assertEqual(g.log, ["ann sat down " + GM, "ann now plays " + APPLE], key)


class Ansi(unittest.TestCase):
    def test_log_wraps_to_its_width(self):
        core = Core(["JOIN 0 ann", "CLIENT 0 " + GM])
        g = screen(core, cls=AnsiScreen)
        asyncio.run(g.pull())
        lines = T.wrap(g.log[-1], 40)
        self.assertEqual(lines[0], "ann sat down with GameMaster 1.0.0 +")
        self.assertTrue(all(len(l) <= 40 for l in lines))


class Menus(unittest.TestCase):
    TABS = ["TAB 5 reversi/1 standard 1/2 open ann,- turn=3600", "CLIENT 0 " + GM,
            "TAB 6 reversi/1 standard 2/2 playing bob,cy turn=3600", "CLIENT 0 with telnet (VT100)",
            "CLIENT 1 " + APPLE]

    def tables(self):
        return T.parse_tables(self.TABS)

    def test_with_clients_fits(self):
        t5, t6 = self.tables()
        seats = [(0, "ann")]
        line = T.with_clients(["Table 5: %s waits for an opponent. Enter sits down, W watches.",
                               "Table 5: %s waits. Enter sits down, W watches."], seats, t5["clients"], ", ", 78)
        self.assertEqual(line, "Table 5: ann (with GameMaster 1.0.0) waits. Enter sits down, W watches.")
        line = T.with_clients("Table 5: %s waits for an opponent.", seats, t5["clients"], ", ", 78)
        self.assertEqual(line, "Table 5: ann (with GameMaster 1.0.0 + ParlorNet 0.5) waits for an opponent.")
        line = T.with_clients(["Table 6: %s. Enter or W watches the game.", "Table 6: %s. Enter or W watches."],
                              [(0, "bob"), (1, "cy")], t6["clients"], " vs ", 78)
        self.assertEqual(line, "Table 6: bob (with telnet (VT100)) vs cy (with ParlorNet). Enter or W watches.")
        self.assertEqual(T.with_clients("Table 6: %s. Enter or W watches the game.", [(0, "bob"), (1, "cy")],
                                        t6["clients"], " vs ", 60), "Table 6: bob vs cy. Enter or W watches the game.",
                         "nothing fits: the names alone")
        self.assertEqual(T.say_cut("with telnet (VT100 terminal)", 26), "with telnet", "no open bracket left")
        tight = T.with_clients("%s", [(0, "abcdefghijkl")], {0: GM}, ", ", 20)
        self.assertEqual(tight, "abcdefghijkl", "nothing of it fits: the name alone")
        self.assertEqual(T.say_cut(GM, 30), "with GameMaster 1.0.0 +"[:-2])

    def test_plain_menu_lines(self):
        out = []

        class IO:
            def write(self, text):
                out.append(text)

            async def flush(self):
                pass
        sess = T.Session(IO(), Core([], tabs=self.TABS), "plain", "dan")

        async def who():
            return "Nobody is online."
        sess.who_text = who
        asyncio.run(T.PlainMenu(sess).relist())
        text = "".join(out)
        self.assertIn("        ann plays " + GM + "\n", text, "under an open table")
        self.assertNotIn("bob plays", text, "a game being played lists its players only")
        self.assertTrue(all(len(l) <= 79 for l in text.split("\n")))

    def test_menu_asks_for_clients(self):
        core = Core([], tabs=self.TABS)
        sess = T.Session(None, core, "plain", "dan")
        tabs, _ = asyncio.run(sess.load())
        self.assertIn("LST clients", core.sent)
        self.assertEqual(tabs[1]["clients"], {0: "with telnet (VT100)", 1: APPLE})


if __name__ == "__main__":
    unittest.main()
