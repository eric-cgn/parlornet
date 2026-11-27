"""gridwords/1: the plugin's rules, a whole game over the protocol, and the web and telnet frontends.

The plugin reads its word list from the file named by GRIDWORDS_WORDS. These tests write a tiny list of
their own (WORDS below) to a temporary file. RealWordList, at the end, runs only when the shipped list
(plugins/gridwords/words.txt, ENABLE) is present, and plays against it."""
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from game_checks import CSRF, ROOT, SID, ansi_screens, board_image, plain_screen, reads_typed, web_page

sys.path.insert(0, ROOT)
from adapters.common import Refused  # noqa: E402
from adapters.games import gridwords as G  # noqa: E402
from parlord.core import Limits, Server  # noqa: E402
from speed import slow  # noqa: E402

_spec = importlib.util.spec_from_file_location("gw_plugin", os.path.join(ROOT, "plugins", "gridwords", "plugin.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

# A small list written for these tests: common short words, nothing more.
WORDS = """
at to in it on no an as is or of be he we me my by go so do up us am ax ox
cat hat rat bat sat mat eat tea ate art tar net ten ton not sit set sea see toe one nor oar ore
rip tip pit pin tin air ire era ear are red den end and add odd dog god nod don top pot opt hot hit
the she her his its our use sun run urn nut tub but bit bet bed bad lad lid old lot log leg gel eel
elf fly try cry icy axe ice ace age ago ape awe dew due dye fee fig fit fix fox fun gap gas hen hip
hog ink jam jar jet job joy key kit lap law lay map men mix mud nap new now oak oil out owl pan pea
pen pet pie pig pod rib rod rot row rub rug saw sew sip sky sob sow tag tan tap tax tie toy van vet
via wag war was wax web wet who wig win wit yak yes yet zap zip zoo quo
hello word words grid tile tiles rate tear star rats arts note tone stone notes ones nest sent tens
rain train tired diet edit tide ride dire rode doer road toad dote quiz jazz
"""


def write_words(text=WORDS, path=None):
    """The words in text, one per line (the form the plugin reads), in a new file or the one given."""
    f = open(path, "w") if path else tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    f.write("\n".join(text.split()) + "\n")
    f.close()
    return f.name


class WordList(unittest.TestCase):
    """Sets GRIDWORDS_WORDS to the test list for the test, and puts it back after."""
    words = WORDS

    def setUp(self):
        self.path = write_words(self.words)
        self.old = os.environ.get(P.WORDS_ENV)
        os.environ[P.WORDS_ENV] = self.path

    def tearDown(self):
        if self.old is None:
            os.environ.pop(P.WORDS_ENV, None)
        else:
            os.environ[P.WORDS_ENV] = self.old
        os.remove(self.path)


def ruleset_data():
    """standard's ruleset file as JSON data, a fresh copy to change."""
    with open(os.path.join(ROOT, "plugins", "gridwords", "rules", "standard.json")) as f:
        return json.load(f)


def write_ruleset(folder, name, data):
    path = os.path.join(folder, name + ".json")
    with open(path, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    return path


def new_state(players=2, seed=1, racks=None, bag=None):
    st = P.op_new({"players": players, "options": {}, "seed": seed})["state"]
    if racks is not None:
        st["racks"] = list(racks)
    if bag is not None:
        st["bag"] = bag
    return st


def put(st, x, y, d, word):
    """Write a word on the board directly (lower case: a blank)."""
    cells = list(st["cells"])
    dx, dy = (1, 0) if d == "H" else (0, 1)
    for i, c in enumerate(word):
        cells[(y - 1 + dy * i) * P.SIZE + (x - 1 + dx * i)] = c
    st["cells"] = "".join(cells)
    return st


def apply(st, seat, *move, check=False):
    return P.op_apply({"state": st, "seat": seat, "move": list(move), "check": check})


def view_lines(st, seat, seq=40, clock=True):
    """What VUE GMT answers for this state (geometry, the view, the clock, the status)."""
    r = P.op_view({"state": st, "seat": seat})
    lines = ["V gridwords/1 %d" % seq] + r["geometry"] + r["wire"]
    if clock and not st["over"]:
        lines.append("CLK %d 250 300" % st["turn"])
    return lines + ["S " + r["status"]]


# ---- the plugin ------------------------------------------------------------------------

class Setup(WordList):
    def test_info_and_geometry(self):
        self.assertEqual((P.INFO["type"], P.INFO["min"], P.INFO["max"], P.INFO["flags"]),
                         ("gridwords", 2, 4, ["nomoves"]))
        self.assertEqual(P.INFO["rules"], ["standard"])
        rs = P.RULESETS["standard"]
        self.assertEqual(sum(n for _, n in rs.tiles.values()), 100)
        self.assertEqual(rs.tiles["_"], (0, 2))
        kinds = [k for k in rs.premium.values()]
        self.assertEqual([kinds.count(k) for k in ("TW", "DW", "TL", "DL")], [8, 13, 16, 24])
        for (x, y), k in rs.premium.items():     # the layout is symmetric both ways and across the diagonal
            self.assertEqual(rs.premium[(16 - x, y)], k)
            self.assertEqual(rs.premium[(y, x)], k)
        self.assertEqual(rs.premium[(8, 8)], "DW")
        self.assertTrue(rs.symmetric)
        self.assertIsNone(rs.idle)
        geo = P.geometry(rs)
        self.assertEqual(geo[:2], ["BRD 15 15", "CTR 8 8"])
        self.assertIn("TIL _ 0 2", geo)
        self.assertIn("TIL Q %d %d" % rs.tiles["Q"], geo)
        self.assertTrue(all(len(l) <= 78 for l in geo))
        self.assertEqual(P.op_geometry({"rules": "standard"}), {"geometry": geo})
        self.assertEqual(P.op_geometry({}), {"geometry": geo})
        self.assertIn("error", P.op_geometry({"rules": "klingon"}))
        # the frontend draws what the geometry says, and so does the bot
        v = G.parse_view(["V gridwords/1 3"] + geo + ["W 0"])
        self.assertEqual((v["prm"], v["til"], v["ctr"]), (rs.premium, rs.tiles, (8, 8)))
        back = P.from_geometry(geo)
        self.assertEqual((back.premium, back.tiles, back.values), (rs.premium, rs.tiles, rs.values))

    def test_new_game(self):
        for n in (2, 3, 4):
            r = P.op_new({"players": n, "options": {}, "seed": 5})
            st = r["state"]
            self.assertEqual(r["events"], ["TURN 0"])
            self.assertEqual([len(x) for x in st["racks"]], [7] * n)
            self.assertEqual(len(st["bag"]) + 7 * n, 100)
            self.assertEqual(sorted(st["bag"] + "".join(st["racks"])),
                             sorted(c for c, (_, k) in P.RULESETS["standard"].tiles.items() for _ in range(k)))
        self.assertEqual(P.op_new({"players": 2, "seed": 9})["state"], P.op_new({"players": 2, "seed": 9})["state"])
        self.assertIn("error", P.op_new({"players": 1, "seed": 1}))
        self.assertIn("error", P.op_new({"players": 5, "seed": 1}))
        self.assertIn("state", P.op_new({"players": 2, "rules": "standard", "seed": 1}))
        self.assertEqual(P.op_new({"players": 2, "rules": "klingon", "seed": 1}),
                         {"error": "the rulesets are: standard"})

    def test_no_word_list(self):
        os.environ[P.WORDS_ENV] = os.path.join(tempfile.gettempdir(), "no-such-gridwords-list.txt")
        r = P.op_new({"players": 2, "seed": 1})
        self.assertEqual(r, {"error": "this server has no word list for Grid Words yet"})

    def test_word_list_file(self):
        with open(self.path, "w") as f:
            f.write("# a comment\nHello\n  world \nx\nit's\nnaïve\n" + "a" * 16 + "\n")
        words = P.load_words()
        self.assertEqual(words, {"HELLO", "WORLD"})      # 2-15 letters A-Z only, any case

    def test_view_hides_racks(self):
        st = new_state(3)
        mine = P.op_view({"state": st, "seat": 1})["wire"]
        self.assertIn("R " + st["racks"][1], mine)
        self.assertFalse(any(l == "R " + st["racks"][0] for l in mine))
        watcher = P.op_view({"state": st, "seat": -1})["wire"]
        self.assertFalse(any(l.startswith("R ") for l in watcher))
        self.assertEqual([l for l in watcher if l[0] in "KTW"], ["K 0 7", "K 1 7", "K 2 7", "T 0 0", "T 1 0",
                                                                  "T 2 0", "W 0"])
        self.assertIn("BAG 79", watcher)
        self.assertEqual(P.op_moves({"state": st, "seat": 0}), {"check": True})


class Rulesets(unittest.TestCase):
    """standard is the plugin's own. An operator adds rulesets as <site>/rules/gridwords/<id>.json in the same
    format, read when the plugin starts; a file the plugin can't use is left out with a log line."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for f in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def refused(self, data, why, name="mine"):
        write_ruleset(self.dir, name, data)
        rulesets, log = P.load_rulesets(self.dir)
        self.assertEqual(list(rulesets), ["standard"])
        self.assertEqual(len(log), 1)
        self.assertIn("ruleset %s left out: " % os.path.join(self.dir, name + ".json"), log[0])
        self.assertIn(why, log[0])
        os.remove(os.path.join(self.dir, name + ".json"))

    def test_a_site_ruleset_is_added_after_standard(self):
        data = ruleset_data()
        data["tiles"]["Q"] = [9, 1]
        data["board"][0] = "D" + data["board"][0][1:-1] + "D"
        data["symmetric"] = False
        data["idle"] = 6
        write_ruleset(self.dir, "zeta", data)
        write_ruleset(self.dir, "alpha", ruleset_data())
        with open(os.path.join(self.dir, "README.txt"), "w") as f:
            f.write("not a ruleset")
        rulesets, log = P.load_rulesets(self.dir)
        self.assertEqual(list(rulesets), ["standard", "alpha", "zeta"])
        self.assertEqual(log, ["ruleset alpha added from %s" % os.path.join(self.dir, "alpha.json"),
                               "ruleset zeta added from %s" % os.path.join(self.dir, "zeta.json")])
        z = rulesets["zeta"]
        self.assertEqual((z.values["Q"], z.premium[(1, 1)], z.premium[(15, 1)], z.idle), (9, "DW", "DW", 6))
        self.assertIn("TIL Q 9 1", P.geometry(z))
        rulesets, log = P.load_rulesets(os.path.join(self.dir, "none"))        # no site directory: standard
        self.assertEqual((list(rulesets), log), (["standard"], []))

    def test_the_site_directory_comes_from_parlor_site(self):
        with mock.patch.dict(os.environ, {P.SITE_ENV: "/srv/site"}):
            self.assertEqual(P.site_rules_dir(), "/srv/site/rules/gridwords")
        with mock.patch.dict(os.environ, {P.SITE_ENV: ""}):
            self.assertEqual(P.site_rules_dir(), "/run/parlornet/site/rules/gridwords")

    def test_a_bad_file_is_left_out_and_logged(self):
        self.refused("{not json", "The file is not JSON")
        self.refused([1, 2], "not a JSON object")
        self.refused(dict(ruleset_data(), colour="red"), "keys the format doesn't know: colour")
        self.refused(ruleset_data(), "standard is the plugin's own ruleset", name="standard")
        self.refused(ruleset_data(), "A rules id is", name="Big")
        self.refused(ruleset_data(), "A rules id is", name="waytoolongforanid")
        d = ruleset_data()
        del d["tiles"]["Q"]
        self.refused(d, "every letter A to Z and _")
        d = ruleset_data()
        d["tiles"]["Q"] = [11, 1]
        self.refused(d, "Q is worth 11 points, and a letter is worth 0 to 10.")
        d = ruleset_data()
        d["tiles"]["Q"] = ["10", 1]
        self.refused(d, "tiles gives Q as [points, count]")
        d = ruleset_data()
        d["tiles"]["_"] = [1, 2]
        self.refused(d, "A blank is worth 0 points.")
        d = ruleset_data()
        d["tiles"]["E"] = [1, 150]
        self.refused(d, "The bag holds 239 tiles, and it must hold 60 to 200.")
        d = ruleset_data()
        d["board"] = d["board"][:14]
        self.refused(d, "board must be 15 rows of 15 squares.")
        d = ruleset_data()
        d["board"][2] = "x" + d["board"][2][1:]
        self.refused(d, "Row 3 has 'x', and a square is one of . d t D T.")
        d = ruleset_data()
        d["board"][0] = "D" + d["board"][0][1:]         # a1 differs from o1, and the file says it's symmetric
        self.refused(d, "The board says it is symmetric, but a1 and a15 differ.")
        self.refused(dict(ruleset_data(), idle=1), "idle is a number of turns from 2 to 20.")
        self.refused(dict(ruleset_data(), symmetric="yes"), "symmetric is true or false.")
        self.refused(dict(ruleset_data(), about="\u00e9t\u00e9"), "about is plain text")

    def test_symmetry_is_checked_only_when_claimed(self):
        d = ruleset_data()
        d["board"][0] = "D" + d["board"][0][1:]
        d["symmetric"] = False
        write_ruleset(self.dir, "lopsided", d)
        rulesets, log = P.load_rulesets(self.dir)
        self.assertEqual(list(rulesets), ["standard", "lopsided"])

    def test_standard_is_symmetric_and_has_100_tiles(self):
        rs = P.read_ruleset(os.path.join(ROOT, "plugins", "gridwords", "rules", "standard.json"))
        self.assertEqual(rs.name, "standard")
        for (x, y), kind in rs.premium.items():
            self.assertEqual({rs.premium.get(m) for m in P.mirrors(x, y)}, {kind})
        self.assertEqual(sum(n for _, n in rs.tiles.values()), 100)

    def test_a_table_of_a_ruleset_this_server_lost(self):
        st = new_state()
        self.assertEqual(P.op_view({"state": st, "seat": 0, "rules": "gone"}),
                         {"error": "this server has no Grid Words ruleset gone"})


class Scoring(WordList):
    def test_first_play_with_premiums(self):
        st = new_state(racks=["HELLOAB", "TTTTTTT"])
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "HELLO", check=True), {"info": "18 HELLO"})
        # h (3) on a triple letter (4,8), o on the center (a double word): (9 + 1 + 1 + 1 + 1) * 2
        r = apply(st, 0, "PLAY", "4", "8", "H", "hello")
        self.assertEqual(r["events"][:3], ["PLAY 0 4 8 H HELLO 26", "X 4 8 H 5 8 E 6 8 L 7 8 L 8 8 O",
                                           "DRAW 0 5"])
        self.assertEqual(r["events"][3], "TURN 1")
        st2 = r["state"]
        self.assertEqual(st2["scores"], [26, 0])
        self.assertEqual(len(st2["racks"][0]), 7)
        self.assertEqual(st2["racks"][0][:2], "AB")
        self.assertEqual(len(st2["bag"]), 86 - 5)
        self.assertEqual(st2["last"], [0, 4, 8, "H", "HELLO", 26])
        self.assertIn("LAST 0 4 8 H HELLO 26", P.op_view({"state": st2, "seat": 1})["wire"])
        self.assertEqual(st["scores"], [0, 0])          # the state handed in is not changed

    def test_first_play_covers_the_centre(self):
        st = new_state(racks=["HELLOAB", "TTTTTTT"])
        self.assertEqual(apply(st, 0, "PLAY", "1", "1", "H", "HELLO")["error"],
                         "the first word must cover the center square h8")
        st = new_state(racks=["AXXXXXX", "TTTTTTT"])
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "A")["error"], "the first word needs two letters or more")

    def test_cross_words_and_letters_on_the_board(self):
        st = put(new_state(racks=["SEATXYZ", "TTTTTTT"]), 7, 8, "H", "CAT")    # CAT across g8-i8
        st["scores"] = [0, 0]
        # "AT" down from h8 uses the A on the board: only the T is new, on h9 (no premium)
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "V", "AT", check=True), {"info": "2 AT"})
        # "SEA" across row 9 under CAT makes SEA and the cross words CS, AE and TA: refused for those three
        self.assertEqual(apply(st, 0, "PLAY", "7", "9", "H", "SEA")["error"], "not in the word list: CS AE TA")
        # "TO" down from i7 would put O on i8, which holds T
        self.assertEqual(apply(st, 0, "PLAY", "9", "7", "V", "TO")["error"], "i8 already holds T")
        # "AT" in the corner touches nothing
        self.assertEqual(apply(st, 0, "PLAY", "1", "1", "H", "AT")["error"],
                         "the word must join the words already on the board")
        # "EAT" down i6-i8 ending on the T of CAT: e on i6, a on i7 (a double letter): 1 + 2 + 1
        self.assertEqual(apply(st, 0, "PLAY", "9", "6", "V", "EAT", check=True), {"info": "4 EAT"})
        # parallel plays: the main word and a cross word through every new tile must all be words
        st = put(new_state(racks=["ATEXXXX", "TTTTTTT"]), 7, 8, "H", "HE")      # HE across g8-h8
        r = apply(st, 0, "PLAY", "7", "9", "H", "AT", check=True)              # g9 a, h9 t: HA, ET
        self.assertEqual(r["error"], "not in the word list: HA ET")
        st = put(new_state(racks=["ETXXXXX", "TTTTTTT"]), 7, 7, "H", "HE")     # HE across g7-h7
        r = apply(st, 0, "PLAY", "7", "8", "H", "ET", check=True)              # g8 e, h8 t: ET, HE, ET
        self.assertEqual(r["error"], "not in the word list: ET ET")
        st = put(new_state(racks=["TOXXXXX", "TTTTTTT"]), 6, 7, "H", "AN")     # AN across f7-g7
        # "TO" across f8-g8: the main word TO, and the cross words AT (f7-f8) and NO (g7-g8)
        r = apply(st, 0, "PLAY", "6", "8", "H", "TO", check=True)
        self.assertEqual(r, {"info": "6 TO AT NO"})          # TO 2 + AT 2 + NO 2 (no premiums on f8, g8)

    def test_word_must_include_neighbours(self):
        st = put(new_state(racks=["SXXXXXX", "TTTTTTT"]), 7, 8, "H", "CAT")
        self.assertEqual(apply(st, 0, "PLAY", "7", "8", "H", "CATS")["error"], "not in the word list: CATS")
        st = put(new_state(racks=["SXXXXXX", "TTTTTTT"]), 7, 8, "H", "AT")
        self.assertEqual(apply(st, 0, "PLAY", "6", "8", "H", "S")["error"],
                         "the tiles just after f8 are part of the word too")
        st = put(new_state(racks=["OXXXXXX", "TTTTTTT"]), 6, 8, "H", "TO")
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "O")["error"],
                         "the tiles just before h8 are part of the word too")
        self.assertIn("runs off the board", apply(st, 0, "PLAY", "14", "8", "H", "TOO")["error"])

    def test_premiums_count_only_for_new_tiles(self):
        st = put(new_state(racks=["SXXXXXX", "TTTTTTT"]), 4, 8, "H", "HELLO")   # H on a triple letter, O center
        st["racks"][0] = "XAAAAAA"
        r = apply(st, 0, "PLAY", "8", "8", "V", "OX", check=True)    # O old (no double word), X (7) on h9 (plain)
        self.assertEqual(r, {"info": "8 OX"})

    def test_blanks(self):
        st = new_state(racks=["_ATXXXX", "TTTTTTT"])
        r = apply(st, 0, "PLAY", "7", "8", "H", "_CAT")
        # _C A T: a blank as C on g8, A on h8 (the center: double word), T on i8: (0 + 1 + 1) * 2
        self.assertEqual(r["events"][0], "PLAY 0 7 8 H cAT 4")
        self.assertEqual(r["events"][1], "X 7 8 c 8 8 A 9 8 T")
        self.assertEqual(P.at(r["state"]["cells"], 7, 8), "c")
        self.assertIn("X 7 8 c 8 8 A 9 8 T", P.op_view({"state": r["state"], "seat": 0})["wire"])
        # a blank on a premium square is still worth nothing, but a word premium under it counts
        st = new_state(racks=["_ATXXXX", "TTTTTTT"])
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "_CAT", check=True), {"info": "4 cAT"})
        # no blank on the rack: refused; a letter written as a blank needs a blank
        st = new_state(racks=["CATXXXX", "TTTTTTT"])
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "_CAT")["error"], "your rack has no blank")
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "CA_")["error"],
                         "letters are A-Z, with _ before a blank's letter")

    def test_full_rack_bonus(self):
        st = new_state(racks=["STONEAX", "TTTTTTT"])
        self.assertIn("error", apply(st, 0, "PLAY", "8", "8", "H", "STONEAX", check=True))   # not a word
        write_words(WORDS + " stonexa", self.path)
        r = apply(st, 0, "PLAY", "2", "8", "H", "STONEXA", check=True)
        # s b8, t c8, o d8 (triple letter), n e8, e f8, x g8 (7), a h8 (center: double word):
        # (1 + 1 + 3 + 1 + 1 + 7 + 1) * 2 + 50
        self.assertEqual(r, {"info": "80 STONEXA"})

    def test_turns_and_syntax(self):
        st = new_state(racks=["HELLOAB", "ATXXXXX"])
        self.assertEqual(apply(st, 1, "PLAY", "8", "8", "H", "AT")["error"], "not your turn")
        self.assertEqual(apply(st, 1, "PLAY", "8", "8", "H", "AT", check=True), {"info": "4 AT"})  # checks any time
        self.assertEqual(apply(st, -1, "PASS")["error"], "you are not playing")
        for bad in (["PLAY", "8", "8", "X", "HELLO"], ["PLAY", "8", "8", "H"], ["PLAY", "a", "8", "H", "HI"],
                    ["MOVE"], ["PASS", "now"], []):
            self.assertIn("error", apply(st, 0, *bad), bad)
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "HELLO", check=True), {"info": "18 HELLO"})
        self.assertEqual(apply(st, 0, "play", "8", "8", "h", "Hello", check=True), {"info": "18 HELLO"})
        self.assertEqual(apply(st, 0, "PLAY", "8", "8", "H", "HELP")["error"], "your rack has no P")


class Turns(WordList):
    def test_exchange(self):
        st = new_state(racks=["QVXAEIO", "TTTTTTT"])
        r = apply(st, 0, "EXCH", "qvx")
        self.assertEqual(r["events"], ["EXCH 0 3", "TURN 1"])
        st2 = r["state"]
        self.assertEqual(st2["racks"][0][:4], "AEIO")
        self.assertEqual(len(st2["racks"][0]), 7)
        self.assertEqual(len(st2["bag"]), len(st["bag"]))
        self.assertEqual(sorted(st2["bag"] + st2["racks"][0]), sorted(st["bag"] + st["racks"][0]))
        self.assertEqual(st2["scores"], [0, 0])
        self.assertEqual(apply(st, 0, "EXCH", "QQ")["error"], "your rack has no Q")
        self.assertEqual(apply(st, 0, "EXCH", "_")["error"], "your rack has no blank")
        self.assertIn("error", apply(st, 0, "EXCH", "AEIOQVXA"))
        self.assertIn("error", apply(st, 0, "EXCH", "A1"))
        st["bag"] = st["bag"][:6]               # fewer than seven tiles in the bag: no exchanges
        self.assertEqual(apply(st, 0, "EXCH", "Q")["error"], "exchanging needs 7 tiles in the bag")
        self.assertEqual(apply(st, 0, "EXCH", "Q", check=True)["error"], "exchanging needs 7 tiles in the bag")

    def test_passes_end_the_game_with_rack_penalties(self):
        st = new_state(racks=["QAXXXXX", "EEEEEEE"])
        st["scores"] = [30, 20]
        r = None
        for i in range(3):
            r = apply(st, st["turn"], "PASS")
            self.assertEqual(r["events"], ["PASS %d" % (i % 2), "TURN %d" % ((i + 1) % 2)])
            st = r["state"]
        r = apply(st, 1, "PASS")              # two full rounds without a word: the game ends
        self.assertEqual(r["events"], ["PASS 1", "ADJ 0 -46 QAXXXXX", "ADJ 1 -7 EEEEEEE"])     # Q 10, A 1, X 7
        self.assertEqual(r["end"], "win 1 -16/13")
        self.assertEqual(r["state"]["scores"], [-16, 13])
        self.assertEqual(P.op_result({"state": r["state"]}), {"over": True, "scores": [-16, 13]})
        self.assertEqual(apply(r["state"], 0, "PASS")["error"], "the game is over")
        v = P.op_view({"state": r["state"], "seat": 0})
        self.assertIn("W -1", v["wire"])
        self.assertEqual(v["status"], "Game over: win 1 -16/13")

    def test_a_ruleset_may_end_after_six_scoreless_turns(self):
        """A ruleset with idle 6: six turns in a row without a word end the game, however many players there
        are. standard has none: two full rounds."""
        short = P.parse_ruleset("short", dict(ruleset_data(), idle=6))
        with mock.patch.dict(P.RULESETS, {"short": short}):
            for n, rounds in ((2, 4), (3, 6), (4, 8)):
                self.assertEqual(P.idle_limit("short", n), 6)
                self.assertEqual(P.idle_limit("standard", n), rounds)
                self.assertEqual(P.idle_limit(None, n), rounds)
            st = new_state(racks=["QAXXXXX", "EEEEEEE"])
            events = []
            for i in range(6):
                r = P.op_apply({"state": st, "seat": st["turn"], "move": ["PASS"], "rules": "short"})
                events += r["events"]
                st = r["state"]
                self.assertEqual("end" in r, i == 5, i)
            self.assertEqual(events[-3:], ["PASS 1", "ADJ 0 -46 QAXXXXX", "ADJ 1 -7 EEEEEEE"])
            st = new_state(racks=["QVXAEIO", "TTTTTTT"])
            for i in range(5):                      # exchanges count too
                st = P.op_apply({"state": st, "seat": st["turn"], "move": ["EXCH", st["racks"][st["turn"]][0]],
                                 "rules": "short"})["state"]
            self.assertFalse(st["over"])
            r = P.op_apply({"state": st, "seat": st["turn"], "move": ["PASS"], "rules": "short"})
            self.assertTrue(r["state"]["over"])
        self.assertEqual(P.op_apply({"state": st, "seat": st["turn"], "move": ["PASS"], "rules": "short"}),
                         {"error": "this server has no Grid Words ruleset short"})

    def test_a_word_played_resets_the_count(self):
        st = new_state(racks=["ATXXXXX", "EEEEEEE"])
        st = apply(st, 0, "PASS")["state"]
        st = apply(st, 1, "PASS")["state"]
        st = apply(st, 0, "PLAY", "8", "8", "H", "AT")["state"]
        self.assertEqual(st["idle"], 0)
        for _ in range(3):
            r = apply(st, st["turn"], "PASS")
            self.assertNotIn("end", r)
            st = r["state"]
        self.assertIn("end", apply(st, st["turn"], "PASS"))

    def test_going_out_ends_the_game(self):
        st = put(new_state(players=3, racks=["AT", "QZ", "E"], bag=""), 7, 8, "H", "C")
        st["scores"] = [10, 40, 12]
        r = apply(st, 0, "PLAY", "7", "8", "H", "CAT")      # C old, A on the center (double word): (2 + 1 + 1) * 2
        self.assertEqual(r["events"], ["PLAY 0 7 8 H CAT 8", "X 8 8 A 9 8 T", "ADJ 0 +21 -", "ADJ 1 -20 QZ",
                                       "ADJ 2 -1 E"])
        self.assertEqual(r["state"]["scores"], [39, 20, 11])
        self.assertEqual(r["end"], "win 0 39/20/11")
        # a tie at the top is a draw
        st = put(new_state(players=2, racks=["AT", "E"], bag=""), 7, 8, "H", "C")
        st["scores"] = [2, 12]
        r = apply(st, 0, "PLAY", "7", "8", "H", "CAT")
        self.assertEqual((r["state"]["scores"], r["end"]), ([11, 11], "draw 11/11"))

    def test_draws_from_the_bag(self):
        st = new_state(racks=["HELLOAB", "TTTTTTT"], bag="XY")
        r = apply(st, 0, "PLAY", "8", "8", "H", "HELLO")
        self.assertEqual(r["events"][2], "DRAW 0 2")
        self.assertEqual(r["state"]["racks"][0], "ABXY")
        self.assertEqual(r["state"]["bag"], "")
        self.assertNotIn("end", r)

    def test_wire_lines_fit(self):
        st = new_state(players=4)
        for y in range(1, 16):
            put(st, 1, y, "H", "ABCDEFGHIJKLMNO" if y % 2 else "abcdefghijklmno")
        st["last"] = [3, 1, 15, "H", "ABCDEFGHIJKLMNO", 1234]
        r = P.op_view({"state": st, "seat": 2})
        self.assertTrue(all(len(l) <= 78 for l in r["wire"] + r["geometry"]))
        self.assertEqual(sum(len(l.split()) - 1 for l in r["wire"] if l.startswith("X ")), 225 * 3)
        v = G.parse_view(view_lines(st, 2))
        self.assertEqual(len(v["tiles"]), 225)
        self.assertEqual(v["tiles"][(1, 2)], "a")
        self.assertEqual(v["counts"], [7, 7, 7, 7])


# ---- a whole game over the protocol ------------------------------------------------------

class TheBagAndEveryRack(WordList):
    """"open": true in a view request (the core's VUE O, for an extension at a table marked integrity=false): a
    seated player's view also carries every rack and the bag in draw order; a watcher's never does."""

    def test_a_seated_view_shows_every_rack_and_the_bag_in_draw_order(self):
        st = P.op_new({"players": 2, "options": {}, "seed": 3, "rules": "standard"})["state"]
        wire = P.op_view({"state": st, "seat": 1, "open": True})["wire"]
        self.assertIn("RK 0 %s" % st["racks"][0], wire)
        self.assertIn("RK 1 %s" % st["racks"][1], wire)
        order = "".join(l[3:] for l in wire if l.startswith("BO "))
        self.assertEqual(order, st["bag"][::-1])
        self.assertTrue(all(len(l) <= 78 for l in wire))
        self.assertFalse(any(l.startswith(("RK", "BO")) for l in P.op_view({"state": st, "seat": 0})["wire"]))
        self.assertFalse(any(l.startswith(("RK", "BO")) for l in P.op_view({"state": st, "seat": -1,
                                                                           "open": True})["wire"]))
        # the next draw takes the first tiles of that order
        st["racks"][0] = "CATSXYZ"
        r = P.op_apply({"state": st, "seat": 0, "move": ["PLAY", "8", "8", "H", "CAT"], "rules": "standard"})
        self.assertEqual(sorted(r["state"]["racks"][0]), sorted("SXYZ" + order[:3]))

    def test_no_table_options(self):
        self.assertEqual(P.INFO["options"], [])
        self.assertIn("error", P.op_new({"players": 2, "options": {"racks": "open"}, "seed": 3}))


class Client:
    def __init__(self, r, w):
        self.r, self.w = r, w

    async def req(self, text):
        self.w.write((text + "\r\n").encode())
        out = []
        while True:
            l = (await asyncio.wait_for(self.r.readline(), 10)).decode().rstrip("\r\n")
            self.assert_short(l)
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    @staticmethod
    def assert_short(line):
        assert len(line) <= 78, line


def best_play(lines, words):
    """The highest-scoring play from the test list on this view, as (score, move tokens), or None."""
    v = G.parse_view(lines)
    cells = ["."] * 225
    for (x, y), c in v["tiles"].items():
        cells[(y - 1) * 15 + x - 1] = c
    st = {"cells": "".join(cells), "racks": [v["rack"]]}
    rack = v["rack"]
    best = None
    for w in sorted(words):
        for d in "HV":
            dx, dy = (1, 0) if d == "H" else (0, 1)
            for y in range(1, 16 - dy * (len(w) - 1)):
                for x in range(1, 16 - dx * (len(w) - 1)):
                    left, ok = list(rack), True
                    for i, ch in enumerate(w):         # a quick look before the plugin's full check
                        c = cells[(y - 1 + dy * i) * 15 + x - 1 + dx * i]
                        if c == ".":
                            if ch in left:
                                left.remove(ch)
                            elif "_" in left:
                                left.remove("_")
                            else:
                                ok = False
                                break
                        elif c.upper() != ch:
                            ok = False
                            break
                    if not ok or len(left) == len(rack):
                        continue
                    try:
                        r = P.evaluate(st, 0, x, y, d, w, words)
                    except P.Illegal:
                        continue
                    if best is None or r["score"] > best[0]:
                        best = (r["score"], ["PLAY", str(x), str(y), d, w])
    return best


def form_fields(lines, x, y, d, word):
    """A whole-word play on this view as the web's play form takes it: the first empty square and only the
    letters that go on the empty squares (the tiles on the board join the word by themselves)."""
    fx, fy, letters = G.typed_from(G.parse_view(lines)["tiles"], int(x), int(y), d, word)
    return "sq=%s&dir=%s&letters=%s" % (G.sq_name(fx, fy), d, letters)


class CoreInProcess(WordList, unittest.IsolatedAsyncioTestCase):
    """A core in this process with the test word list."""
    method = "none"         # the sign-in method: a name alone (sign-in is tested in test_auth)

    async def asyncSetUp(self):
        self.rate = (Limits.rate_per_sec, Limits.rate_burst)
        Limits.rate_per_sec, Limits.rate_burst = 1000.0, 1000       # as the other protocol tests run the core
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", self.method)
        await self.srv.load_plugins()
        self.server = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            c.w.close()
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 10)
        for p in self.srv.plugins.values():
            p.stop()
        Limits.rate_per_sec, Limits.rate_burst = self.rate

    def seed_tables(self, n):
        """The seed of the tables made from now on: the first draw of Python's random generator seeded with n
        (a real core draws each from the system's secure generator, parlord.core.table_seed)."""
        seed = random.Random(n).getrandbits(32)
        patch = mock.patch("parlord.core.table_seed", lambda: seed)
        patch.start()
        self.addCleanup(patch.stop)

    async def client(self, name):
        c = Client(*await asyncio.open_connection("127.0.0.1", self.port))
        self.clients.append(c)
        c.w.write(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        await c.r.readline()
        r = await c.req("HELO " + name)
        if r[-1].startswith("ASK password"):            # sign-in password (WebForms): the name's password
            r = await c.req("AUT password %s-Pass-word" % name)
        self.assertEqual(r[-1], "OK -")
        return c


class SiteRulesets(CoreInProcess):
    """A ruleset in the site directory is a rules id of its own: GAM lists it after standard, NEW makes tables of
    it, and their geometry is its own, also while the table waits for players (VUE G) and without a table
    (GEO). The core logs the plugin's lines about the files it read."""

    async def asyncSetUp(self):
        self.site = tempfile.mkdtemp()
        folder = os.path.join(self.site, "rules", "gridwords")
        os.makedirs(folder)
        data = ruleset_data()
        data["tiles"]["Q"] = [9, 1]
        data["idle"] = 6
        write_ruleset(folder, "mine", data)
        write_ruleset(folder, "broken", "{")
        old = os.environ.get(P.SITE_ENV)            # only this setting: tearDown puts the word list's back
        os.environ[P.SITE_ENV] = self.site
        self.addCleanup(lambda: os.environ.pop(P.SITE_ENV) if old is None else os.environ.update({P.SITE_ENV: old}))
        self.addCleanup(shutil.rmtree, self.site)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            await super().asyncSetUp()
        self.log = err.getvalue()

    async def test_a_site_ruleset_is_a_rules_id(self):
        self.assertIn("parlord: plugin gridwords: ruleset mine added from %s" % os.path.join(
            self.site, "rules", "gridwords", "mine.json"), self.log)
        self.assertIn("parlord: plugin gridwords: ruleset %s left out: The file is not JSON" % os.path.join(
            self.site, "rules", "gridwords", "broken.json"), self.log)
        a, b = await self.client("ann"), await self.client("bob")
        gam = [l for l in await a.req("GAM") if l.startswith("GAM gridwords/1 ")]
        self.assertEqual(gam, ["GAM gridwords/1 standard Grid_Words 2 4 nomoves",
                               "GAM gridwords/1 mine Grid_Words 2 4 nomoves"])
        geo = await a.req("GEO gridwords mine")
        self.assertEqual(geo[-1], "OK")
        self.assertIn("TIL Q 9 1", geo)
        self.assertIn("TIL Q 10 1", await a.req("GEO gridwords/1 standard"))
        self.assertEqual((await a.req("GEO gridwords broken"))[-1], "ERR NORULES unknown ruleset")
        self.assertEqual((await a.req("GEO chess standard"))[-1], "ERR NOGAME this server does not host that game")
        self.assertEqual(await a.req("GEO reversi standard"), ["OK"])       # a game with no static geometry
        self.assertEqual((await a.req("GEO gridwords"))[-1], "ERR SYNTAX GEO type/ver rules")
        tid = (await a.req("NEW gridwords mine turn=1h"))[-1].split()[1]
        self.assertEqual((await a.req("JON " + tid))[-1], "OK 0")
        waiting = await a.req("VUE %s G" % tid)             # waiting for a player: the geometry already
        self.assertEqual(waiting[1:3], ["BRD 15 15", "CTR 8 8"])
        self.assertIn("TIL Q 9 1", waiting)
        self.assertEqual(waiting[-2:], ["S Waiting for players", "OK"])
        self.assertNotIn("TIL Q 9 1", await a.req("VUE %s" % tid))
        self.assertEqual((await b.req("JON " + tid))[-1], "OK 1")
        self.assertIn("TIL Q 9 1", await a.req("VUE %s G" % tid))
        for _ in range(5):                                  # idle 6: six passes end it, not four
            seat = int(next(l for l in await a.req("VUE %s" % tid) if l.startswith("W ")).split()[1])
            self.assertEqual((await (a if seat == 0 else b).req("MOV %s PASS" % tid))[-1], "OK")
        self.assertIn("W 1", await a.req("VUE %s" % tid))
        self.assertEqual((await b.req("MOV %s PASS" % tid))[-1], "OK")
        self.assertIn("W -1", await a.req("VUE %s" % tid))


class OverProtocol(CoreInProcess):
    @slow
    async def test_a_whole_game(self):
        words = P.load_words()
        a, b = await self.client("ann"), await self.client("bob")
        self.assertIn("GAM gridwords/1 standard Grid_Words 2 4 nomoves", await a.req("GAM"))
        self.seed_tables(2026)                # the table's seed, so the game is the same every time
        tid = (await a.req("NEW gridwords standard turn=1h"))[-1].split()[1]
        self.assertEqual((await a.req("JON " + tid))[-1], "OK 0")
        self.assertEqual((await b.req("JON " + tid))[-1], "OK 1")
        seat = {0: a, 1: b}
        geo = await a.req("VUE %s G" % tid)
        self.assertIn("BRD 15 15", geo)
        self.assertIn("CTR 8 8", geo)
        self.assertNotIn("M", [l.split()[0] for l in await a.req("VUE %s M" % tid)])     # no move list
        plays = turns = 0
        events = []
        while turns < 200:
            turns += 1
            view = await a.req("VUE %s T" % tid)
            self.assertEqual(view[-1], "OK", view)
            w = int(next(l for l in view if l.startswith("W ")).split()[1])
            if w < 0:
                break
            c, other = seat[w], seat[1 - w]
            mine = await c.req("VUE %s T" % tid)
            self.assertIn("S Your move", mine)
            other_view = await other.req("VUE %s" % tid)
            rack_lines = [l for l in other_view if l.startswith("R ")]
            self.assertEqual(len(rack_lines), 1)                # only one's own rack
            self.assertNotIn(rack_lines[0], mine)
            found = best_play(mine[:-1], words)
            if found:
                score, move = found
                chk = await c.req("CHK %s %s" % (tid, " ".join(move)))
                self.assertTrue(chk[-1].startswith("OK %d " % score), chk)
                self.assertIn("ILLEGAL", (await other.req("MOV %s %s" % (tid, " ".join(move))))[-1])
                r = await c.req("MOV %s %s" % (tid, " ".join(move)))
                self.assertEqual(r[-1].strip(), "OK", r)
                self.assertTrue(any(" PLAY %d %s %s %s " % (w, move[1], move[2], move[3]) in l for l in r), r)
                plays += 1
            else:
                r = await c.req("MOV %s PASS" % tid)
                self.assertEqual(r[-1].strip(), "OK", r)
            events += r
        self.assertGreater(plays, 5)
        log = await a.req("SIN %s 0" % tid)
        kinds = [l.split()[3] if l.startswith("EVT") else "" for l in log]
        ends = [l for l, k in zip(log, kinds) if k == "END"]        # (END is a word too: PLAY 1 1 10 H END 6)
        self.assertEqual(len(ends), 1, ends)
        adj = [l.split()[3:] for l, k in zip(log, kinds) if k == "ADJ"]
        self.assertEqual([x[1] for x in adj], ["0", "1"])
        final = await b.req("VUE %s T" % tid)
        scores = [int(l.split()[2]) for l in final if l.startswith("T ")]
        self.assertIn("W -1", final)
        result = ends[0].split(" ", 4)[4]
        self.assertTrue(result.endswith("/".join(map(str, scores))), (result, scores))
        self.assertTrue(any(l.startswith("S Game over: ") for l in final))
        self.assertIn("NOGAME", (await a.req("MOV %s PASS" % tid))[-1])
        # the log reads well on the web and telnet
        names = ["ann", "bob"]
        texts = [G.describe_event(l.split(" ", 3)[3], names) for l in log if l.startswith("EVT")]
        self.assertTrue(any(t and t.startswith("ann plays ") for t in texts))
        self.assertTrue(any(t and ": -" in t and "left on the rack" in t for t in texts) or
                        any(t and "goes out" in t for t in texts))


    async def test_four_seats(self):
        """players=n on NEW sets the seats (2 to 4); the game starts when the last one sits down."""
        cs = [await self.client(n) for n in ("cy", "di", "ed", "flo")]
        self.assertIn("BADOPT", (await cs[0].req("NEW gridwords standard players=5"))[-1])
        tid = (await cs[0].req("NEW gridwords standard players=4"))[-1].split()[1]
        for i, c in enumerate(cs):
            self.assertEqual((await c.req("JON " + tid))[-1], "OK %d" % i)
        lst = await cs[0].req("LST")
        want = "TAB %s gridwords/1 standard 4/4 playing cy,di,ed,flo" % tid
        self.assertTrue(any(l.startswith(want) for l in lst), lst)
        view = await cs[2].req("VUE %s T" % tid)
        self.assertEqual([l for l in view if l.startswith("K ")], ["K 0 7", "K 1 7", "K 2 7", "K 3 7"])
        self.assertIn("BAG 72", view)
        self.assertEqual(len([l for l in view if l.startswith("R ")]), 1)
        for turn in range(8):                   # everybody passes twice: the game ends
            r = await cs[turn % 4].req("MOV %s PASS" % tid)
            self.assertTrue(r[-1].startswith("OK"), r)
        self.assertEqual(len([l for l in r if " ADJ " in l]), 4)
        self.assertTrue(any(" END " in l for l in r))


@slow
class WebForms(CoreInProcess):
    """The web adapter against the core, signed in with sessions (a browser signs up with a password and
    keeps the cookie): /move.htm builds the play from the form's three fields (MOVE_FIELDS), /check.htm
    sends CHK and shows the core's answer, every field checked strictly, and a whole game on the web.
    The core (sign-in password, with its extension channel) and the web adapter run in this process, so
    the table's seed is the test's; the browsers run in threads."""
    method = "password"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from adapters import web as W
        from parlord import frontends
        from test_core import make_keys
        keys = make_keys("web")
        self.srv.keyring = frontends.Keyring(keys)
        self.fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        self.web = W.Web("127.0.0.1", self.fe.sockets[0].getsockname()[1], os.path.join(keys, "web.key"))
        self.http = await asyncio.start_server(self.web.handle, "127.0.0.1", 0)
        self.web_port = self.http.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for srv in (self.http, self.fe):
            srv.close()
        await super().asyncTearDown()

    async def browser(self, name):
        from test_adapters import Browser
        b = Browser(self.web_port)
        st, _, _, body = await asyncio.to_thread(b.login, name, name + "-Pass-word")
        self.assertIn("You are <b>%s</b>" % name, body)
        return b

    async def get(self, b, path):
        """A GET from browser b -> (status, body)."""
        st, _, _, body = await asyncio.to_thread(b.get, path)
        return st, body

    async def seated(self):
        """ann and bob signed in on the web (each signs up there), at a new table: their browsers, their
        protocol connections (the core's own view), the table."""
        ann, bob = await self.browser("ann"), await self.browser("bob")
        a, b = await self.client("ann"), await self.client("bob")
        self.seed_tables(2026)                # the table's seed, so the game is the same every time
        tid = (await a.req("NEW gridwords standard turn=1h"))[-1].split()[1]
        await a.req("JON " + tid)
        st, _ = await self.get(bob, "/join.htm?t=%s&%s" % (tid, await asyncio.to_thread(bob.keep)))
        self.assertEqual(st, 302)
        return ann, bob, a, b, tid

    async def test_play_and_check_from_fields(self):
        words = P.load_words()
        ann, bob, a, b, tid = await self.seated()
        page = (await self.get(ann, "/table.htm?t=%s" % tid))[1]
        for action in ("/move.htm", "/check.htm"):              # both forms carry the session's action check
            form = page[page.index('<form action="%s"' % action):]
            self.assertIn('<input type="hidden" name="x" value="', form[:form.index("</form>")])
        me = "&" + ann.keep()
        found = best_play((await a.req("VUE %s GMT" % tid))[:-1], words)
        self.assertTrue(found)
        score, (_, x, y, d, word) = found
        fields = form_fields((await a.req("VUE %s GMT" % tid))[:-1], x, y, d, word.lower())
        chk = await a.req("CHK %s PLAY %s %s %s %s" % (tid, x, y, d, word))
        self.assertTrue(chk[-1].startswith("OK %d " % score), chk)
        # Check score: the core's answer, the forms keep the play, the page stays put
        st, html = await self.get(ann, "/check.htm?t=%s&%s%s" % (tid, fields, me))
        self.assertEqual(st, 200)
        said = ", ".join(w.upper() for w in chk[-1].split()[2:])
        self.assertIn("<b>Check:</b> %s scores %d points. " % (said, score), html)
        self.assertNotIn("estimate", html)
        self.assertIn("mv=PLAY%%20%s%%20%s%%20%s%%20%s" % (x, y, d, word), html)        # Play it
        self.assertIn('name="letters" size="16" maxlength="30" value="%s"' % word, html)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn("T 0 0", await a.req("VUE %s" % tid))                            # nothing played
        # /check.htm is an action like /move.htm: without x= it is refused and nothing reaches the core
        seq = await a.req("SIN %s 999" % tid)
        st, html = await self.get(ann, "/check.htm?t=%s&%s" % (tid, fields))
        self.assertIn("That link is out of date.", html)
        self.assertNotIn("<b>Check:</b>", html)
        self.assertEqual(await a.req("SIN %s 999" % tid), seq)
        # an illegal play: the core's reason
        bad = await a.req("CHK %s PLAY 1 1 H %s" % (tid, word))
        self.assertTrue(bad[-1].startswith("ERR ILLEGAL"), bad)
        st, html = await self.get(ann, "/check.htm?t=%s&sq=a1&dir=H&letters=%s%s" % (tid, word, me))
        self.assertIn("<b>Check:</b> %s." % bad[-1].split(" ", 2)[2], html)
        self.assertNotIn("Play it", html)
        # every field strictly checked, on both pages; nothing reaches the core
        for q in ("sq=h8&dir=H&letters=A%0DB", "sq=h8&dir=H&letters=" + "A" * 33, "sq=h8&sq=h9&dir=H&letters=AT",
                  "sq=h8&dir=H&letters=%C3%89T", "sq=p1&dir=H&letters=AT", "sq=h8&dir=X&letters=AT",
                  "sq=h8&dir=H", "sq=h8&dir=H&letters=A%00", "sq=h8&dir=H&letters=AT%09", "dir=H&letters=AT"):
            for page in ("/move.htm", "/check.htm"):
                st, html = await self.get(ann, "%s?t=%s&%s%s" % (page, tid, q, me))
                self.assertIn("That isn&#39;t a move this page can send.", html, (page, q))
        self.assertEqual(await a.req("SIN %s 999" % tid), seq)
        # a refused play: the core's reason, with the play named as the board makes it
        st, html = await self.get(ann, "/move.htm?t=%s&sq=a1&dir=H&letters=%s%s" % (tid, word, me))
        self.assertIn("Can&#39;t play %s across from a1: %s." % (word, bad[-1].split(" ", 2)[2]), html)
        self.assertEqual(await a.req("SIN %s 999" % tid), seq)
        # Play: the same fields to /move.htm
        st, html = await self.get(ann, "/move.htm?t=%s&%s%s" % (tid, fields, me))
        self.assertEqual(st, 302, html[-400:])
        self.assertIn("T 0 %d" % score, await a.req("VUE %s" % tid))
        # a word started on a played square: refused in plain words before anything reaches the core
        seq = await a.req("SIN %s 999" % tid)
        sq = G.sq_name(*next(iter(G.parse_view((await b.req("VUE %s GMT" % tid))[:-1])["tiles"])))
        for page in ("/move.htm", "/check.htm"):
            st, html = await self.get(bob, "%s?t=%s&sq=%s&dir=H&letters=x&%s" % (page, tid, sq, bob.keep()))
            self.assertIn("There is already a tile on %s: start your word on an empty square." % sq, html)
        self.assertEqual(await a.req("SIN %s 999" % tid), seq)
        # mv= links still work (Pass), and a game without a web check refuses /check.htm
        st, _ = await self.get(bob, "/move.htm?t=%s&mv=PASS&%s" % (tid, bob.keep()))
        self.assertEqual(st, 302)
        rt = (await a.req("NEW reversi standard"))[-1].split()[1]
        await a.req("JON " + rt)
        await b.req("JON " + rt)
        st, html = await self.get(ann, "/check.htm?t=%s&mv=d3%s" % (rt, me))
        self.assertIn("This game has no check on the web.", html)
        # another browser, without the session: x= alone gets nobody in
        from test_adapters import Browser
        st, html = await self.get(Browser(self.web_port), "/check.htm?t=%s&%s%s" % (tid, fields, me))
        self.assertIn("Sign in to play.", html)
        self.assertNotIn("<b>Check:</b>", html)

    async def test_check_page_is_an_action(self):
        """/check.htm is in the web's ACTIONS: without x= the "out of date" page and nothing reaches the core;
        with x= the fields reach the core as CHK (the lines the core receives are recorded)."""
        ann, bob, a, b, tid = await self.seated()
        got = []
        dispatch = self.srv.dispatch

        async def recording(sess, line):
            got.append(line)
            return await dispatch(sess, line)
        self.srv.dispatch = recording
        fields = "sq=h8&dir=H&letters=AT"
        st, html = await self.get(ann, "/check.htm?t=%s&%s" % (tid, fields))
        self.assertEqual(st, 200)
        self.assertIn("That link is out of date.", html)
        self.assertFalse(any(l.startswith("CHK") for l in got), got)
        st, html = await self.get(ann, "/check.htm?t=%s&%s&x=%s" % (tid, fields, "0" * 16))   # a wrong x=
        self.assertIn("That link is out of date.", html)
        self.assertFalse(any(l.startswith("CHK") for l in got), got)
        await self.get(ann, "/table.htm?t=%s" % tid)
        st, html = await self.get(ann, "/check.htm?t=%s&%s&%s" % (tid, fields, ann.keep()))
        self.assertEqual(st, 200)
        self.assertIn("<b>Check:</b>", html)
        self.assertIn("CHK %s PLAY 8 8 H AT" % tid, got)
        self.assertEqual(got[0].split()[0], "SRC")        # the web's own requests: SRC, SES, then the page's
        self.assertIn("SES", [l.split()[0] for l in got])

    async def test_a_whole_game_on_the_web(self):
        """Both players play from the web form (the best play from the test list, else Pass) until the
        game ends."""
        words = P.load_words()
        ann, bob, a, b, tid = await self.seated()
        who = {0: (ann, a), 1: (bob, b)}
        plays = 0
        for _ in range(200):
            view = await a.req("VUE %s T" % tid)
            w = int(next(l for l in view if l.startswith("W ")).split()[1])
            if w < 0:
                break
            br, c = who[w]
            await self.get(br, "/table.htm?t=%s&r=0" % tid)
            view = (await c.req("VUE %s GMT" % tid))[:-1]
            found = best_play(view, words)
            if found:
                _, (_, x, y, d, word) = found
                q = form_fields(view, x, y, d, word)          # only the rack's letters, from an empty square
                plays += 1
            else:
                q = "mv=PASS"
            st, html = await self.get(br, "/move.htm?t=%s&%s&%s" % (tid, q, br.keep()))
            self.assertEqual(st, 302, html[-400:])
        self.assertGreater(plays, 5)
        for br in (ann, bob):
            st, html = await self.get(br, "/table.htm?t=%s" % tid)
            self.assertIn("Game over", html)
            self.assertNotIn("Pass-word", "".join(br.urls))


# ---- the frontends ---------------------------------------------------------------------------

def midgame(players=2, turn=0):
    st = new_state(players=players, racks=["HEL_OAT", "QUIZJAX", "STONEAB", "RATESIT"][:players])
    put(st, 4, 8, "H", "HELLO")
    put(st, 8, 8, "V", "OAt")
    st["scores"] = [24, 13, 0, 999][:players]
    st["turn"] = turn
    st["last"] = [1, 8, 8, "V", "OAt", 13]
    return st


class Web(WordList):
    def test_waiting(self):
        v = G.parse_view(["V gridwords/1 1", "S Waiting for players"])
        self.assertFalse(v["rows"])

    def test_my_turn(self):
        st = midgame()
        html = web_page(self, G, view_lines(st, 0), seat=0)
        c = board_image(self, G, view_lines(st, 0), 0)
        used = {(p, x, y) for p, x, y in c.used}

        def at(sq):                                                 # the top left of a board square, h10 -> ...
            return G.MARGIN + (ord(sq[0]) - 97) * G.CELL, G.MARGIN + (int(sq[1:]) - 1) * G.CELL
        self.assertIn(("/img/gridwords/th3.gif",) + at("d8"), used)  # tiles, with this table's values
        self.assertIn(("/img/gridwords/bt.gif",) + at("h10"), used)  # the blank on h10, as t
        self.assertIn(("/img/gridwords/stw.gif",) + at("a1"), used)  # premium squares
        self.assertIn(("/img/gridwords/sdl.gif",) + at("d1"), used)
        self.assertIn("Your rack:", html)
        self.assertIn("<tt>H E L _ O A T</tt>", html)
        rack_y = G.MARGIN + 15 * G.CELL + G.RACK_GAP
        self.assertEqual([p for p, _, y in c.used if y == rack_y],
                         [G.IMG + n for n in ("th3.gif", "te1.gif", "tl1.gif", "blank.gif", "to1.gif", "ta1.gif",
                                              "tt1.gif")])
        self.assertIn(("Your rack", 2, rack_y - 11), c.words)
        self.assertEqual(len(re.findall(r"<img ", html)), 1)        # the board, the rack in it: one image
        self.assertIn("Last play: bob, OAt at h8 down, 13 points", html)
        self.assertIn('<form action="/move.htm" method="get">', html)
        self.assertIn('<input type="hidden" name="t" value="3">', html)
        self.assertIn('name="mv"', html)
        self.assertIn('<input type="submit" value="Check score">', html)
        self.assertIn('value="EXCH "', html)
        hrefs = re.findall(r'href="([^"]+)"', html)
        moves = [h for h in hrefs if h.startswith("/move.htm")]
        self.assertEqual(moves, ["/move.htm?s=%s;x=%s;t=3;mv=PASS" % (SID, CSRF)])
        sels = [re.search(r"sel=([^&;]+)", h).group(1) for h in hrefs if "sel=" in h]
        self.assertEqual(len(sels), 225)                           # every square can start a word (map areas)
        self.assertEqual(html.count("<area "), 225)
        for s in sels:
            self.assertRegex(s, G.SEL_RE)
        for mv in ("PASS", "PLAY 8 8 H HELLO", "play 12 3 v _quiz", "EXCH QV_", "exch a"):
            self.assertRegex(mv, G.MOVE_RE)
        for bad in ("PLAY 8 8 H HELLO\r\nBYE", "PLAY 8 8 D HI", "EXCH ABCDEFGH", "PASS 1", "d3"):
            self.assertNotRegex(bad, G.MOVE_RE)

    def test_start_square_and_check(self):
        st = midgame()
        lines = view_lines(st, 0)
        html = web_page(self, G, lines, seat=0, sel="11.6.V")
        self.assertIn("Starting at <b>k6</b>, down.", html)
        self.assertIn((G.IMG + "gov.gif", G.MARGIN + 10 * G.CELL, G.MARGIN + 5 * G.CELL),
                      board_image(self, G, lines, 0, sel="11.6.V").used)
        self.assertIn('Start <input name="sq" size="3" maxlength="3" value="k6"> <select name="dir"><option '
                      'value="H">across<option value="V" selected>down</select> letters <input name="letters" '
                      'size="16" maxlength="30" value="">', html)
        self.assertEqual(html.count('name="letters"'), 2)          # the Play form and the Check score form
        self.assertIn('<form action="/check.htm" method="get">', html)
        self.assertNotIn('name="mv" size="30"', html)
        self.assertIn("sel=11.6.H", html)                         # clicking it again turns it across
        # a play picked with sel= and no answer from the server: the page's own estimate (as the server
        # scores it, but the words are looked up only when it is played)
        html = web_page(self, G, lines, seat=0, sel="PLAY 7 11 H TOE")
        r = P.evaluate(st, 0, 7, 11, "H", "TOE", None)
        self.assertEqual(r["words"], ["TOE", "OAtO"])
        self.assertIn("<b>Check:</b> TOE, OATO scores %d points (an estimate" % r["score"], html)
        self.assertRegex("PLAY 7 11 H TOE", G.MOVE_RE)
        self.assertIn("mv=PLAY%207%2011%20H%20TOE", html)          # Play it
        self.assertIn('name="sq" size="3" maxlength="3" value="g11"', html)
        self.assertIn('name="letters" size="16" maxlength="30" value="TOE"', html)
        html = web_page(self, G, lines, seat=0, sel="PLAY 1 1 H HAT")
        self.assertIn("<b>Check:</b> the word must join the words already on the board.", html)
        self.assertNotIn("Play it", html)

    def test_start_on_a_played_square(self):
        """A click on a played square starts no word: the page says so in plain words. A picked whole-word
        play (after Check score) shows in the forms as the player types it: from its first empty square, the
        rack's letters only."""
        st = midgame()
        lines = view_lines(st, 0)
        html = web_page(self, G, lines, seat=0, sel="8.8.H")                    # h8 holds O
        self.assertIn("<b>There is already a tile on h8.</b> Click the empty square where your word starts", html)
        self.assertIn('name="sq" size="3" maxlength="3" value=""', html)
        self.assertIn("Type only the letters from your rack, in order: the letters already on the board join "
                      "your word by themselves.", html)
        self.assertNotIn("including letters already on the board", html)
        self.assertFalse([u for u in board_image(self, G, lines, 0, sel="8.8.H").used if "/go" in u[0]])
        html = web_page(self, G, lines, seat=0, sel="PLAY 8 8 V OATH")          # OAt on the board, H from the rack
        self.assertIn("Starting at <b>h11</b>, down.", html)
        self.assertIn('name="sq" size="3" maxlength="3" value="h11"', html)
        self.assertIn('name="letters" size="16" maxlength="30" value="H"', html)
        self.assertIn("mv=PLAY%208%208%20V%20OATH", html)                       # Play it: the whole word
        self.assertIn((G.IMG + "gov.gif", G.MARGIN + 7 * G.CELL, G.MARGIN + 10 * G.CELL),
                      board_image(self, G, lines, 0, sel="PLAY 8 8 V OATH").used)

    def test_check_answer(self):
        """/check.htm's answer, from the core, in place of the estimate; the estimate only when the core gave
        none."""
        st = midgame()
        lines = view_lines(st, 0)
        mv = "PLAY 7 11 H TOE"
        html = web_page(self, G, lines, seat=0, check=(mv, "OK 9 TOE OAtO"))
        self.assertIn("<b>Check:</b> TOE, OATO scores 9 points. <a href=", html)
        self.assertNotIn("estimate", html)
        self.assertIn("mv=PLAY%207%2011%20H%20TOE", html)          # Play it
        html = web_page(self, G, lines, seat=0, check=("PLAY 7 11 H TOX", "ERR ILLEGAL not in the word list: TOX"))
        self.assertIn("<b>Check:</b> not in the word list: TOX.", html)
        self.assertNotIn("Play it", html)
        self.assertNotIn("estimate", html)
        html = web_page(self, G, lines, seat=0, check=(mv, "ERR ILLEGAL <b>&\"x\"</b>"))
        self.assertIn("&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;", html)                   # escaped
        # the web adapter passes the move as sel too, so the forms keep it; no answer: the estimate
        html = web_page(self, G, lines, seat=0, sel=mv, check=(mv, None))
        self.assertIn("(an estimate", html)
        self.assertIn('value="TOE"', html)
        # not this player's turn: the answer, without Play it (CHK works while waiting)
        html = web_page(self, G, view_lines(st, 1), seat=1, check=("PLAY 7 11 H QUIZ", "OK 30 QUIZ"))
        self.assertIn("<b>Check:</b> QUIZ scores 30 points.</p>", html)
        self.assertNotIn("/move.htm", html)

    def test_move_from_fields(self):
        from adapters import games
        self.assertEqual(games.problems(G), [])
        self.assertEqual(G.MOVE_FIELDS, ["sq", "dir", "letters"])
        self.assertTrue(G.WEB_CHECK)
        self.assertTrue(G.MOVE_FIELDS_VIEW)
        for vals, mv in ((["h8", "H", "hello"], "PLAY 8 8 H HELLO"), (["O11", "v", "_quiz"], "PLAY 15 11 V _QUIZ"),
                         (["a1", "V", "A" * 15], "PLAY 1 1 V " + "A" * 15), (["k6", "h", "toe"], "PLAY 11 6 H TOE")):
            self.assertEqual(G.move_from_fields(vals), mv)
            self.assertRegex(mv, G.MOVE_RE)
        # on the table's board: the letters typed are the rack's, from an empty square; the tiles on the
        # board join the word (HE on g4-h4, x typed at i4: HEX)
        v = G.parse_view(view_lines(midgame(), 0))
        v["tiles"].update({(7, 4): "H", (8, 4): "E"})
        self.assertEqual(G.move_from_fields(["i4", "H", "x"], v), "PLAY 7 4 H HEX")
        self.assertEqual(G.move_from_fields(["h11", "V", "h"], v), "PLAY 8 8 V OATH")       # OAt above h11
        self.assertEqual(G.move_from_fields(["c8", "H", "s"], v), "PLAY 3 8 H SHELLO")      # HELLO after c8
        for vals, why in ((["h4", "H", "x"], "There is already a tile on h4: start your word on an empty square"),
                          (["o4", "H", "xy"], "From o4 across, those letters run off the board"),
                          (["a1", "V", "A" * 16], "From a1 down, those letters run off the board"),
                          (["i4", "H", "x_"], "Type the letters from your rack, a blank as _ before its letter")):
            with self.assertRaises(Refused) as e:
                G.move_from_fields(vals, v)
            self.assertEqual(str(e.exception), why)
        for vals in ((["p1", "H", "AT"]), (["h16", "H", "AT"]), (["h0", "H", "AT"]), (["h8", "D", "AT"]),
                     (["h8", "", "AT"]), (["h8", "H", ""]), (["h8", "H", "A T"]), (["h8", "H", "A" * 31]),
                     (["8 8", "H", "AT"]), (["h8", "H", "AT1"]), (["h8", "across", "AT"]), (["h8", "H", "\u00c9T"])):
            self.assertIsNone(G.move_from_fields(vals), vals)

    def test_preview_matches_the_server(self):
        st = midgame()
        v = G.parse_view(view_lines(st, 0))
        plays = [(7, 11, "H", "TOE"), (3, 7, "H", "TOE"), (9, 7, "V", "HO"), (8, 6, "V", "_TOOAt"),
                 (1, 1, "H", "AT"), (8, 8, "V", "OATH"), (9, 9, "H", "TO"), (7, 9, "V", "EAT"), (9, 9, "V", "AT"),
                 (2, 9, "H", "HEL_OATZ"), (9, 6, "V", "_ELL")]
        legal = 0
        for play in plays:
            try:
                r = P.evaluate(st, 0, *play, None)
                want = (r["score"], r["words"])
                legal += 1
            except P.Illegal as e:
                want = (None, str(e))
            got = G.preview(v, "HEL_OAT", *play)
            self.assertEqual(got[0], want[0], play)
            if got[0] is not None:
                self.assertEqual(got[1], want[1], play)
        self.assertGreater(legal, 3)

    def test_watcher_and_other_seat(self):
        st = midgame(players=4, turn=3)
        names = ["ann", "bob", "cy", "LONGNAMEHERE"]
        html = web_page(self, G, view_lines(st, -1), seat=-1, names=names)
        self.assertNotIn("Your rack", html)
        self.assertNotIn("<form", html)
        self.assertNotIn("sel=", html)
        self.assertIn("LONGNAMEHERE", html)
        self.assertIn("&gt;", html)
        html = web_page(self, G, view_lines(st, 1), seat=1, names=names)
        self.assertIn("Your rack", html)
        self.assertIn("bob (you)", html)
        self.assertNotIn("<form", html)

    def test_game_over(self):
        st = midgame()
        st["bag"] = ""
        r = apply(st, 0, "PASS")
        for _ in range(3):
            r = apply(r["state"], r["state"]["turn"], "PASS")
        html = web_page(self, G, view_lines(r["state"], 0), seat=0)
        self.assertIn("Game over", G.parse_view(view_lines(r["state"], 0))["status"])
        self.assertNotIn("<form", html)
        self.assertNotIn("/move.htm", html)

    def test_the_game_page_shows_each_ruleset(self):
        """/games/gridwords.htm shows each ruleset the server has, from its geometry (GEO): the letters' points
        and counts, and a picture of its board at /games/gridwords/<rules>.gif."""
        from adapters import web as W
        from adapters.common import parse_games
        mine = P.parse_ruleset("mine", dict(ruleset_data(), tiles=dict(ruleset_data()["tiles"], Q=[9, 1])))
        hosted = parse_games(["GAM gridwords/1 standard Grid_Words 2 4 nomoves",
                              "GAM gridwords/1 mine Grid_Words 2 4 nomoves"])
        geo = {("gridwords/1", "standard"): P.geometry(P.RULESETS["standard"]),
               ("gridwords/1", "mine"): P.geometry(mine)}
        page = W.game_page("gridwords", hosted, None, geo).decode("latin-1")
        self.assertLess(page.index("<b>Standard rules.</b> The bag holds 100 tiles"),
                        page.index("<b>Mine rules.</b> The bag holds 100 tiles"))
        self.assertIn('<img src="/games/gridwords/standard.gif"', page)
        self.assertIn('<img src="/games/gridwords/mine.gif"', page)
        self.assertIn("<tr><th align=\"left\">Points</th><td>1</td><td>3</td><td>2</td>", page)   # A B C
        self.assertNotIn("Standard rules.", W.game_page("gridwords", hosted).decode("latin-1"))
        b = W.ruleset_board(G, "mine", geo[("gridwords/1", "mine")])
        self.assertEqual(b.gif[:3], b"GIF")
        self.assertEqual((b.w, b.h), (W.ruleset_board(G, "standard", geo[("gridwords/1", "standard")]).w, b.h))

    def test_images(self):
        imgs = G.images()
        from adapters import palette as PAL
        self.assertEqual(len(imgs), 6 + 2 + 26 * (G.MAX_POINTS + 1) + 26 + 1 + len(PAL.PRESETS))
        for path, data in imgs.items():
            self.assertTrue(path.startswith("/img/gridwords/"))
            if "/pal-" in path:     # a preset's preview on the colors page: six squares in a row
                self.assertEqual(data[6:10], bytes([145, 0, 25, 0]))
                continue
            self.assertEqual(data[6:10], b"\x18\x00\x18\x00")   # 24 by 24
        self.assertNotEqual(imgs["/img/gridwords/ta1.gif"], imgs["/img/gridwords/ba.gif"])


class Events(unittest.TestCase):
    def test_describe(self):
        names = ["ann", "bob", "-"]
        d = G.describe_event
        self.assertEqual(d("PLAY 0 4 8 H HELLO 24", names), "ann plays HELLO at d8 across for 24")
        self.assertEqual(d("PLAY 1 8 8 V OAt 13", names), "bob plays OAt at h8 down for 13")
        self.assertEqual(d("X 4 8 H", names), "")
        self.assertEqual(d("DRAW 0 5", names), "")
        self.assertEqual(d("EXCH 1 3", names), "bob exchanges 3 tiles")
        self.assertEqual(d("EXCH 2 1", names), "seat 3 exchanges 1 tile")
        self.assertEqual(d("PASS 0", names), "ann passes")
        self.assertEqual(d("ADJ 0 +21 -", names), "ann goes out: +21 for the tiles left on the other racks")
        self.assertEqual(d("ADJ 1 -20 QZ", names), "bob: -20 for QZ left on the rack")
        self.assertIsNone(d("END win 0 35/20/11", names))
        self.assertIsNone(d("JOIN 0 ann", names))
        from adapters import web as W
        self.assertEqual(W.describe("END win 0 35/20", names, G.KEY), "Game over: ann wins 35/20")
        self.assertEqual(W.describe("PASS 1", names, G.KEY), "bob passes")


class Screens(WordList):
    def test_full_screen_each_seat(self):
        st = midgame()
        for me in (0, 1, -1):
            for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, me), me, keys=["ENTER"]).items():
                self.assertIn("Grid Words", shown)
                self.assertIn("3W", shown)
                self.assertIn("86 tiles are in the bag.", shown)
                if me >= 0:
                    self.assertIn("Your rack", shown)
                    self.assertIn([" H  E  L  _  O  A  T ", " Q  U  I  Z  J  A  X "][me], shown)       # letters only
                else:
                    self.assertIn("Watchers can't see them.", shown)
                self.assertFalse([r for r in reqs if r.startswith(("MOV", "CHK"))], (mode, me))
                if me == 0:
                    self.assertIn("Your turn", shown)
                    self.assertIn("Your word starts at i8 and goes across.", shown)    # the cursor moved off the O on h8
                if me == 1:
                    self.assertIn("Waiting for abcdefghijkl.", shown)
                    self.assertIn("played OAt down from", shown)

    def test_cursor_start_and_typed_letters(self):
        st = midgame()
        lines = view_lines(st, 0)
        # a click on g9 starts a word across there, Down points it down; type the rack's letters, Enter: the L
        # above joins the word
        keys = [("CLICK", 3 + 3 * 6 + 1, 2 + 9), "DOWN"] + list("eat") + ["ENTER"]
        for mode, (shown, reqs) in ansi_screens(self, G, lines, 0, keys=keys).items():
            self.assertIn("MOV 3 PLAY 7 8 V LEAT", reqs, mode)
            self.assertIn("Your word starts at g9 and goes down.", shown)
            self.assertIn("[E]", shown)                          # the letters typed show on the board
        # a click on a square starts a word there; Esc clears the start, and then letters are shortcuts
        out = ansi_screens(self, G, lines, 0, keys=[("CLICK", 3 + 3 * 10 + 1, 2 + 6), "ESC"] + list("hi"))
        for mode, (shown, reqs) in out.items():
            self.assertIn("Your word starts at k6 and goes across.", shown)
            self.assertFalse([r for r in reqs if r.startswith("MOV")], mode)
            self.assertIn("H does nothing in Grid Words. Press ? to see the keys.", shown)

    def test_typed_commands(self):
        st = midgame()
        lines = view_lines(st, 0)
        # only the rack's letters, from an empty square; the tiles already on the board join the word
        # a typed play runs on the server (CMD), where the plugin reads it (tests/test_typed_moves.py)
        for typed in ("h11 d h", "g9 a _et", "k6 hi", "pass", "exchange ql_", "check g9 d eat", "k6 a pass"):
            keys = ["/"] + list(typed) + ["ENTER"]
            for mode, (shown, reqs) in ansi_screens(self, G, lines, 0, keys=keys).items():
                self.assertIn("CMD 3 /" + typed, reqs, (typed, mode))
        # a watcher's typed play goes nowhere
        for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, -1), -1,
                                                keys=["/"] + list("pass") + ["ENTER"]).items():
            self.assertFalse([r for r in reqs if r.startswith("MOV")])
        # letters typed from a played square, or off the board: the plugin's reason in plain words, nothing played
        for typed, why in (("h8 a x", "There is already a tile on h8. Start your word on an empty square."),
                           ("check h9 d x", "There is already a tile on h9. Start your word on an empty square."),
                           ("o1 a xy", "From o1 across, those letters run off the board.")):
            for mode, (shown, reqs) in ansi_screens(self, G, lines, 0, keys=["/"] + list(typed) + ["ENTER"],
                                                    prepare=reads_typed(P, st, 0)).items():
                self.assertIn(why, shown, (typed, mode))
                self.assertFalse([r for r in reqs if r.startswith(("MOV", "CHK"))], (typed, mode))

    def test_a_message_shows_once(self):
        """A message takes the hint's line; the line under it is for typing only (a refusal once showed on
        both lines)."""
        def say(g):
            g.say("Can't play X across from i4: not in the word list: X.")
        for mode, (shown, _) in ansi_screens(self, G, view_lines(midgame(), 0), 0, prepare=say).items():
            self.assertEqual(shown.count("not in the word list"), 1, mode)

    def test_crowded_board_four_players(self):
        st = new_state(players=4)
        for y in range(1, 16):
            put(st, 1, y, "H", "QZJXKWVYQZJXKWV" if y % 3 else "qzjxkwvyqzjxkwv")
        st["scores"] = [-999, 9999, 0, 1234]
        st["racks"] = ["QZJXKW_", "", "A", "QZXJ"]
        st["last"] = [3, 1, 15, "H", "QZJXKWVYQZJXKWV", 9999]
        st["turn"] = 3
        names = ("abcdefghijkl", "ABCDEFGHIJKL", "mnopqrstuvwx", "MNOPQRSTUVWX")
        for me in (0, 3, -1):
            for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, me), me, names=names,
                                                    keys=["UP", "ENTER", "ENTER", "ENTER"]).items():
                self.assertIn("9999", shown)
                self.assertIn("-999", shown)
                self.assertIn("MNOPQRSTUVWX", shown)
            text = plain_screen(self, G, view_lines(st, me), me, names=names)
            self.assertIn(" q  z  j ", text)
            self.assertIn("9999", text)

    def test_game_over_screens(self):
        st = midgame()
        r = apply(st, 0, "PASS")
        for _ in range(3):
            r = apply(r["state"], r["state"]["turn"], "PASS")
        lines = view_lines(r["state"], 0)
        self.assertIn("W -1", lines)

        def ended(screen):          # the screen sets over from the END event; these tests give no events
            screen.over = "Game over: abcdefghijkl wins 15/-26"
        for mode, (shown, reqs) in ansi_screens(self, G, lines, 0, keys=["ENTER"], prepare=ended).items():
            self.assertIn("Game over. abcdefghijkl wins 15/-26.", shown)
            self.assertIn("Press Esc to go back to the lobby", shown)
        text = plain_screen(self, G, lines, 0, prepare=ended)
        self.assertIn("Game over. abcdefghijkl wins", text)
        self.assertNotIn("Your turn", text)

    def test_plain(self):
        st = midgame()
        text = plain_screen(self, G, view_lines(st, 0), 0)
        self.assertIn("Grid Words, table 3, with ABCDEFGHIJKL. You are abcdefghijkl.", text)
        self.assertIn("\n  8 |3W  .  .  H  E  L  L  O  .  .  . 3L  .  . 3W |\n", text)
        self.assertIn("ABCDEFGHIJKL played OAt", text)
        self.assertIn("Your rack is H E L _ O A T.", text)
        self.assertIn("Your turn. Type a play, /pass or /exchange. > ", text)
        watch = plain_screen(self, G, view_lines(st, -1), -1)
        self.assertIn("You are watching.", watch)
        self.assertNotIn("Rack:", watch)

    def test_plain_typed_play(self):
        from game_checks import FakeIO, ScreenCore, session
        st = midgame()
        core = ScreenCore(G.KEY, view_lines(st, 0))
        io = FakeIO("plain", ["y"])                 # yes to the pass dialog

        async def readline():
            return await io.readkey()
        io.readline = readline
        s = session(io, core, "plain")
        g = G.PlainScreen(s, "3", G.KEY, 0)
        g.names, g.v = ["ann", "bob"], g.parse(view_lines(st, 0))

        async def go():
            for line in ("g9 d eat", "check h11 d h", "pass", "exchange zz", "hello"):
                await g.command(line)
        asyncio.run(go())
        for line in ("g9 d eat", "check h11 d h", "pass", "exchange zz", "hello"):   # each runs on the server
            self.assertIn("CMD 3 /" + line, core.reqs)
        self.assertFalse([r for r in core.reqs if r.startswith(("MOV", "CHK"))])


# ---- the shipped word list -------------------------------------------------------------

@unittest.skipUnless(os.path.isfile(P.DEFAULT_WORDS), "no plugins/gridwords/words.txt")
class RealWordList(CoreInProcess):
    """The shipped list (ENABLE, plugins/gridwords/SOURCES.md), read from its default place: GRIDWORDS_WORDS
    is unset, so the plugin's process finds words.txt itself. A table starts, a common word plays and a
    non-word made of the same rack is refused."""

    def setUp(self):
        self.old = os.environ.pop(P.WORDS_ENV, None)

    def tearDown(self):
        if self.old is not None:
            os.environ[P.WORDS_ENV] = self.old

    async def test_common_word_plays(self):
        words = P.load_words()
        self.assertGreater(len(words), 150000)
        self.assertTrue({"AA", "CAT", "HELLO", "QAT", "QUIZ", "ZYZZYVA"} <= words)
        self.assertTrue(words.isdisjoint({"XQZ", "AAAA", "PARLORNETT"}))
        a, b = await self.client("ann"), await self.client("bob")
        self.seed_tables(2026)                # the table's seed, so the rack is the same every time
        r = await a.req("NEW gridwords standard")
        self.assertTrue(r[-1].startswith("OK "), r)
        tid = r[-1].split()[1]
        self.assertEqual((await a.req("JON " + tid))[-1], "OK 0")
        self.assertEqual((await b.req("JON " + tid))[-1], "OK 1")
        view = await a.req("VUE %s T" % tid)
        w = int(next(l for l in view if l.startswith("W ")).split()[1])
        c = (a, b)[w]
        rack = G.parse_view((await c.req("VUE %s T" % tid))[:-1])["rack"]

        def from_rack(word):
            left = list(rack)
            for ch in word:
                if ch not in left:
                    return False
                left.remove(ch)
            return True
        common = [x.upper() for x in WORDS.split() if len(x) >= 3]
        word = next((x for x in sorted(common, key=len, reverse=True) if x in words and from_rack(x)), None)
        self.assertIsNotNone(word, rack)
        # a non-word from the same rack: the first arrangement of three of its letters the list does not have
        letters = [ch for ch in rack if ch != "_"]
        bad = next(x for x in ("".join((p, q, s)) for i, p in enumerate(letters) for j, q in enumerate(letters)
                               for k, s in enumerate(letters) if len({i, j, k}) == 3) if x not in words)
        r = await c.req("MOV %s PLAY 8 8 H %s" % (tid, bad))
        self.assertEqual(r[-1], "ERR ILLEGAL not in the word list: " + bad)
        r = await c.req("MOV %s PLAY 8 8 H %s" % (tid, word))
        self.assertEqual(r[-1].strip(), "OK", r)
        self.assertTrue(any(" PLAY %d 8 8 H %s " % (w, word) in l for l in r), r)


if __name__ == "__main__":
    unittest.main()


class TypedPlays(unittest.TestCase):
    """Telnet: a player types only the letters of their own rack, from an empty start square; they fill the
    empty squares and the tiles already on the board join the word (a player typed `x` beside HE and was
    refused). Arrow keys skip played squares."""

    def test_arrows_skip_played_squares(self):
        from adapters.games import gridwords as G
        g = G.GridBase.__new__(G.GridBase)
        g.v = {"rows": [1], "tiles": {(8, 4): "E", (9, 4): "X"}}
        g.cur = (7, 4)
        G.AnsiGrid.on_arrow(g, "RIGHT")
        self.assertEqual(g.cur, (10, 4))
        G.AnsiGrid.on_arrow(g, "LEFT")
        self.assertEqual(g.cur, (7, 4))

    def grid(self, tiles):
        from adapters.games import gridwords as G
        g = G.GridBase.__new__(G.GridBase)
        g.start, g.v = None, {"tiles": tiles}
        return G, g

    def test_new_letters_only_or_the_whole_word(self):
        G, g = self.grid({(7, 4): "H", (8, 4): "E", (8, 1): "A", (8, 2): "L", (8, 3): "L", (8, 5): "E"})
        self.assertEqual(G.GridBase.play_from(g, 9, 4, "H", "x"), "PLAY 7 4 H HEX")    # only the rack's letters
        for args in ((7, 4, "H", "hex"), (8, 4, "H", "x")):                     # never on a played square
            self.assertIsNone(G.GridBase.play_from(g, *args), args)
        self.assertEqual(G.GridBase.play_from(g, 8, 6, "V", "s"), "PLAY 8 1 V ALLEES")
        self.assertEqual(G.GridBase.play_from(g, 9, 3, "V", "_ox"), "PLAY 9 3 V _OX")
        self.assertIsNone(G.GridBase.play_from(g, 15, 4, "H", "xy"))               # off the board

    def test_refusals_are_in_the_players_terms(self):
        G, g = self.grid({})
        self.assertEqual(G.GridBase.described(g, "PLAY 9 4 H X"), "X across from i4")
        self.assertEqual(G.GridBase.described(g, "PLAY 8 1 V ALLEES"), "ALLEES down from h1")
        self.assertEqual(G.GridBase.described(g, "EXCH QV_"), "an exchange of Q, V and a blank")
        self.assertEqual(G.GridBase.described(g, "PASS"), "a pass")


class TelnetDrawing(WordList):
    """The full screen as a terminal shows it: every tile drawn alike (a tile on the center square too), the
    cursor leaving nothing behind, the rack's letters without points, help's second page with the points,
    and on your turn, how to pass or exchange."""

    def board(self, mode, st, me=0):
        from game_checks import AttrScreen, FakeIO, ScreenCore, session
        lines = view_lines(st, me)
        io = FakeIO(mode)
        s = session(io, ScreenCore(G.KEY, lines), mode)
        g = G.AnsiScreen(s, "3", G.KEY, me)
        g.names, g.turn_secs, g.v, g.log = ["ann", "bob"], 300, g.parse(lines), []
        g.snap()
        term = AttrScreen()

        def show(full=False):
            cv, pos = g.screen()
            term.feed(s.painter.frame(cv, pos, full))
            return cv
        return g, term, show

    def test_a_tile_on_the_center_square_looks_like_every_other_tile(self):
        st = put(new_state(), 6, 8, "H", "BOAR")            # the first word: its R covers the center square h8
        for mode in ("pc", "utf8", "ansi"):
            g, term, show = self.board(mode, st)
            self.assertNotIn(g.cur, g.v["tiles"], "the cursor never rests on a played tile")
            cv = show(True)
            self.assertEqual(term.wrong_cells(cv), [], mode)
            looks = {term.cells[cy][cx + 1] for cx, cy in (g.cell_xy(x, 8) for x in range(6, 10))}
            self.assertEqual({c[0] for c in looks}, set("BOAR"))
            self.assertEqual({c[1:] for c in looks}, {term.look_of(g.attrs()["tile"])}, mode)
            g.cur = (8, 8)                                   # even with the cursor on it, R is a tile, reversed
            cv = show()
            self.assertEqual(term.wrong_cells(cv), [], mode)

    def test_moving_the_cursor_leaves_nothing_behind(self):
        st = midgame()
        keys = ["RIGHT"] * 9 + ["DOWN"] * 8 + ["LEFT"] * 17 + ["UP"] * 16 + ["RIGHT", "DOWN"] * 6
        for mode in ("pc", "utf8", "ansi"):
            g, term, show = self.board(mode, st)
            show(True)
            for k in keys:
                g.on_arrow(k)
                cv = show()
                self.assertEqual(term.wrong_cells(cv), [], (mode, k, g.cur))
            fresh = type(term)()
            fresh.feed(g.s.painter.frame(cv, None, True))
            self.assertEqual(term.cells, fresh.cells, mode)

    def test_the_rack_shows_only_letters(self):
        st = midgame()
        for mode, (shown, _) in ansi_screens(self, G, view_lines(st, 0), 0).items():
            rack = next(l for l in shown.split("\n") if " H  E  L  _  O  A  T " in l)
            self.assertNotRegex(rack.split("H  E  L")[1][:20], r"\d", mode)
        self.assertIn("Your rack is H E L _ O A T.\n", plain_screen(self, G, view_lines(st, 0), 0))

    def test_help_has_a_second_page_with_the_points(self):
        st = midgame()
        for mode, (shown, _) in ansi_screens(self, G, view_lines(st, 0), 0, keys=["?", " ", "ESC", "v", "ESC"]).items():
            self.assertIn("Grid Words help, page 1 of 2", shown)
            self.assertIn("Press Space for page 2, or Esc to close.", shown)
            self.assertIn("P passes your turn, and X opens the command line to exchange tiles.", shown)
            self.assertIn("Grid Words help, page 2 of 2", shown)
            self.assertIn("Q and Z are worth 10 points each.", shown)       # the table's own values
            self.assertIn("X is worth 7 points.", shown)
            self.assertIn("The bag starts with 100 tiles: A9 B2 C3 D4 E11", shown)
            self.assertIn("A blank is worth nothing. Type _E to lay a blank as an E.", shown)
            self.assertEqual(shown.count("Grid Words help, page 2 of 2"), 2 * shown.count("page 2 of 2") // 2)
        from game_checks import FakeIO, ScreenCore, session
        io = FakeIO("plain")
        io.out = []
        io.write = io.out.append
        g = G.PlainScreen(session(io, ScreenCore(G.KEY, view_lines(st, 0)), "plain"), "3", G.KEY, 0)
        g.v = g.parse(view_lines(st, 0))
        asyncio.run(g.help(1))
        self.assertIn("/check h8 across QUIZ gives the score without playing.", "".join(io.out))
        self.assertTrue("".join(io.out).rstrip().endswith("Type /help 2 for the reference."))
        asyncio.run(g.help(2))
        self.assertIn("Q and Z are worth 10 points each.", "".join(io.out))
        self.assertTrue(all(len(l) < 80 for l in "".join(io.out).split("\n")))

    def test_your_turn_says_how_to_pass_or_exchange(self):
        st = midgame()
        said = "Your turn. Press Enter where your word starts, P to pass or X to exchange."
        for mode, (shown, _) in ansi_screens(self, G, view_lines(st, 0), 0).items():
            self.assertIn(said, shown, mode)
            self.assertIn("P pass  X exchange", shown, mode)
        for me in (1, -1):              # not your turn, or watching: no such line
            for mode, (shown, _) in ansi_screens(self, G, view_lines(st, me), me).items():
                self.assertNotIn(said, shown, (mode, me))
                self.assertNotIn("P pass", shown, (mode, me))
        # P asks first (the server asks, with the plugin's reading), and X opens the command line with /exchange
        reads = reads_typed(P, st, 0)
        for keys, sent in ((["p", "n"], False), (["p", "y"], True)):
            for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, 0), 0, keys=keys, prepare=reads).items():
                self.assertIn("Pass your turn? You play nothing this turn.", shown)
                self.assertEqual("MOV 3 PASS" in reqs, sent, (keys, mode))
        for mode, (shown, reqs) in ansi_screens(self, G, view_lines(st, 0), 0, keys=["x"] + list("QL") + ["ENTER"],
                                                prepare=reads).items():
            self.assertIn("/exchange puts the tiles you name back in the bag and draws new ones.", shown)
            self.assertIn("MOV 3 EXCH QL", reqs)
        self.assertIn("Type a play, /pass or /exchange.", plain_screen(self, G, view_lines(st, 0), 0))
        self.assertNotIn("/pass", plain_screen(self, G, view_lines(st, 1), 1))

    def test_an_exchange_with_too_few_tiles_in_the_bag(self):
        from game_checks import FakeIO, ScreenCore, session
        st = midgame()
        st["bag"] = st["bag"][:4]
        core = ScreenCore(G.KEY, view_lines(st, 0))
        g = G.PlainScreen(session(FakeIO("plain"), core, "plain"), "3", G.KEY, 0)
        g.names, g.v = ["ann", "bob"], g.parse(view_lines(st, 0))
        err = asyncio.run(g.play_word("EXCH QZ"))
        self.assertEqual(err, "You can't exchange now. The bag holds 4 tiles, and an exchange needs at least seven.")
        self.assertFalse([r for r in core.reqs if r.startswith("MOV")])
        self.assertEqual(G.refusal("EXCH QZ", "exchanging needs 7 tiles in the bag"),
                         "You can't exchange now because the bag holds fewer than seven tiles.")

