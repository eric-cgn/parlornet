"""The DOoM extension (adapters/doom.py): Grid Words for DOoM clients.

Two scripted clients read the extension's output the way DOoM's help files and BobGrid's code show the period
ones do:

    Telnet    a person at a terminal or MUD client: `connect name password', `place 8d WORD', `commit',
              `b' for the board, reading the text lines.
    BobGrid   BobGrid 2.3's reader, ported line for line from its class files: on "starts the clock.",
              "plays out." and "ends the game." it sends `b', `log2 -tagged' and `track -tagged'; it reads
              the board by character position (the square of column c at 2c+1, the players' rows and the
              status from character 38), the tagged log as whitespace tokens (rack, coordinate and word, or
              a '-' move, score, running total) and the unseen tiles after character 14. It plays with
              pick-and-click's commands: `p WORD 8h' (blanks in lower case), `b', `commit', `b'. Its lines
              end in LF alone, as Java's println does on Unix.

They play whole games against a web player (through adapters/web.py's pages) and against the bot."""
import os
import re
import socket
import sys
import tempfile
import time
import unittest

import procs
from procs import owned, spawn
from test_adapters import WebPlayers
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
from test_gridwords import WORDS, apply, best_play, form_fields, new_state, put, write_words

sys.path.insert(0, ROOT)
from adapters import doom as D  # noqa: E402
from adapters.common import MOTD_BOTTOM, MOTD_TOP  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 20
GUIDE = "   a b c d e f g"     # the start of the column guide, above and below a board
MOTD = ["Welcome to the test parlor.", "Be kind."]


# ---- BobGrid's readers, ported -------------------------------------------------------------------

def bobgrid_board(lines, me=""):
    """BobGrid's DoBoard on the lines after the column guide: one line skipped, fifteen rows, two more lines.
    -> {"tiles": {(col, row): letter}, "players": [(name, rack, score, secs)], "whose": 1|2|None,
    "over": bool, "rack": my rack, "timed": bool, "running": bool}; raises on anything BobGrid would trip on."""
    it = iter(lines)
    next(it)                                    # the top border
    out = {"tiles": {}, "players": [], "whose": None, "over": False, "rack": "", "timed": False, "running": True}
    names = {}
    for row in range(1, 16):
        line = next(it)
        for col in range(1, 16):
            ch = line[2 * col + 1]                      # an IndexError here would be BobGrid's exception
            if "A" <= ch <= "z" and ch.isalpha():
                out["tiles"][(col, row)] = ch
        if row in (2, 3) and len(line) > 38:
            tok = line[38:].split()
            name = tok[0]
            rest = tok[1:]
            rack = ""
            if rest and not rest[0][0].isdigit():
                rack, rest = rest[0], rest[1:]
                if name.lower() == me.lower():
                    out["rack"] = rack
            assert len(rest) >= 2, ("a player's row with no score and clock after the rack (this reader, as "
                                    "ported, takes a negative score for a rack)", line)
            score, t = rest[0], rest[1].rjust(5)
            assert t[2] == ":", ("BobGrid's clock needs MM:SS", line)
            names[row] = name
            out["players"].append((name, rack, int(score), int(t[:2].strip() or 0) * 60 + int(t[3:5])))
        if row == 5 and len(line) > 38:
            text = line[38:]
            if text.startswith("Waiting"):
                out["whose"] = 1 if line[50:].startswith(names.get(2, "\0")) else 2
            elif text.startswith("The game is over."):
                out["over"] = True
        if row == 7 and not line.endswith("running."):
            out["running"] = False
        if row == 9 and len(line) > 38 and line[38:].startswith("Time penalties are in effect"):
            out["timed"] = True
    next(it)
    next(it)
    return out


def review_tokens(lines):
    """BobGrid's Review on a log2 (the lines between the header and [END]): whitespace tokens, four to an
    entry (rack, then a move starting '-' with no word, or a coordinate and a word, then the score and the
    running total). -> [(rack, coordinate or None, word or move, total)]; asserts what BobGrid's DoLog2 needs
    of each line."""
    for l in lines:
        assert len(l) >= 38, l
        if len(l) > 60:
            assert len(l) >= 74, l
    tok = " ".join(lines).split()
    out, i = [], 0
    while i < len(tok):
        rack, move = tok[i], tok[i + 1]
        if move.startswith("-"):
            coord, word, i = None, move, i + 2
        else:
            coord, word, i = move, tok[i + 2], i + 3
        i += 1                                  # the score: skipped
        out.append((rack, coord, word, int(tok[i])))
        i += 1
    return out


def review_square(coord):
    """BobGrid's reading of a log2 coordinate -> (col, row, across)."""
    if coord[0].isdigit():
        if len(coord) == 2 or coord[1].isalpha():
            return ord(coord[1]) - 96, int(coord[0]), True
        return ord(coord[2]) - 96, 10 + int(coord[1]), True
    return ord(coord[0]) - 96, int(coord[1:]), False


class Lines:
    """A client's socket, read as lines (CR LF or LF), with BEL characters taken off."""

    def __init__(self, port, eol="\r\n"):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.eol = eol
        self.buf = b""
        self.queue = []
        self.seen = []

    def send(self, text):
        self.s.sendall((text + self.eol).encode("ascii"))

    def fill(self, timeout):
        self.s.settimeout(max(0.05, timeout))
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return
        if not d:
            raise EOFError("closed; seen %r" % self.seen[-20:])
        self.buf += d
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            text = line.decode("ascii").rstrip("\r").replace("\a", "")
            self.queue.append(text)
            self.seen.append(text)

    def line(self, timeout=WAIT):
        end = time.time() + timeout
        while not self.queue:
            if time.time() > end:
                raise AssertionError("no line came; seen %r" % self.seen[-25:])
            self.fill(end - time.time())
        return self.queue.pop(0)

    def until(self, pred, timeout=WAIT):
        """Lines up to the first that pred accepts (a string: equal to it); returns them all."""
        if isinstance(pred, str):
            want = pred
            pred = lambda l: l == want      # noqa: E731
        got = []
        end = time.time() + timeout
        while True:
            l = self.line(max(0.1, end - time.time()))
            got.append(l)
            if pred(l):
                return got

    def board_rows(self, timeout=WAIT):
        """The rest of a board whose top guide line was just read: the border, fifteen rows, the border and the
        bottom guide, read up to that guide however the lines arrive. A board that is not whole fails here,
        with its lines, rather than in a reader."""
        rows = []
        end = time.time() + timeout
        while not (rows and rows[-1].startswith(GUIDE)):
            if len(rows) >= 18:
                break
            rows.append(self.line(max(0.1, end - time.time())))
        whole = (len(rows) == 18 and rows[-1].startswith(GUIDE) and rows[0].startswith("  +")
                 and rows[16].startswith("  +") and all(re.match(r"[ \d]\d\|", r) for r in rows[1:16]))
        assert whole, "not a whole board after the guide line: %r" % (rows,)
        return rows

    def until_raw(self, text, timeout=WAIT):
        end = time.time() + timeout
        while text.encode() not in self.buf:
            if time.time() > end:
                raise AssertionError("%r not seen; buffer %r" % (text, self.buf[-300:]))
            self.fill(end - time.time())
        i = self.buf.index(text.encode()) + len(text)
        self.buf = self.buf[i:]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Telnet(Lines):
    def login(self, name, password):
        self.until(lambda l: l.startswith("You are connector #"))
        self.send("connect %s %s" % (name, password))
        return self.until(lambda l: l.startswith("Enter `help intro' to begin"))

    def board(self):
        """`b', read the way BobGrid reads it."""
        self.send("b")
        self.until(lambda l: l.startswith(GUIDE))
        rows = self.board_rows()
        return bobgrid_board(rows, self.me), rows


class BobGrid(Lines):
    """BobGrid's side of the conversation (ReadSocket and its triggers)."""

    def __init__(self, port):
        super().__init__(port, eol="\n")
        self.state = None
        self.log = None
        self.track = None
        self.ended = False
        self.turn_lines = []

    def login(self, name, password):
        self.me = name
        self.until(lambda l: l.startswith("You are connector #"))
        self.send("connect %s %s" % (name, password))           # what /CONWIZ wrote to .bgcfg
        return self.until(lambda l: l.startswith("Enter `help intro' to begin"))

    def pump(self, timeout=0.3):
        """Read what has come, acting on it as BobGrid's ReadSocket does."""
        end = time.time() + timeout
        while time.time() < end or self.queue:
            if not self.queue:
                self.fill(end - time.time())
                if not self.queue:
                    continue
            l = self.queue.pop(0)
            if l.startswith(GUIDE):
                self.state = bobgrid_board(self.board_rows(), self.me)
            elif l.startswith("[BEGIN]"):
                self.read_tagged()
            if l.endswith("starts the clock.") or l.endswith("plays out.") or l.endswith("ends the game."):
                self.ended = self.ended or l.endswith("ends the game.")
                for cmd in ("b", "log2 -tagged", "track -tagged"):
                    self.send(cmd)

    def read_tagged(self):
        first = self.line()
        if first.startswith("Unseen"):
            text = first[14:]
            nxt = self.line()
            if nxt != "[END]":
                text += nxt[14:]
                assert self.line() == "[END]"
            assert re.match(r"^[A-Z?]*\Z", text), text          # DoTrackSort counts A-Z and ? only
            self.track = text
            return
        body = []
        while True:
            l = self.line()
            if l == "[END]":
                break
            body.append(l)
        self.log = (first[0:20].strip(), first[40:70].strip(), review_tokens(body))

    def my_turn(self):
        st = self.state
        if not st or st["over"] or st["whose"] is None:
            return False
        names = [p[0] for p in st["players"]]
        return len(names) == 2 and names[st["whose"] - 1].lower() == self.me.lower()


def play_from(board, words):
    """The best play from the test list on a board read as BobGrid reads it -> (score, PLAY tokens) or None."""
    lines = ["X %d %d %s" % (c, r, ch) for (c, r), ch in board["tiles"].items()]
    lines.append("R " + (board["rack"].replace("?", "_") or "-"))
    return best_play(lines, words)


def bag_of(view):
    """The tiles left in the bag, from a VUE answer."""
    return next((int(l.split()[1]) for l in view if l.startswith("BAG ")), 0)


def doom_square(x, y, d, bobgrid=False):
    col = "abcdefghijklmno"[x - 1]
    if d == "H":
        return "%d%s" % (y, col if bobgrid else col.upper())
    return "%s%d" % (col if bobgrid else col.upper(), y)


# ---- pure parts ------------------------------------------------------------------------------------------

class Formats(unittest.TestCase):
    def test_place_syntax(self):
        self.assertEqual(D.parse_place(["8d", "MUZJiKS"]), (4, 8, "H", "MUZJ_IKS"))
        self.assertEqual(D.parse_place(["ill(i)quid", "i8"]), (9, 8, "V", "ILL_IQUID"))
        self.assertEqual(D.parse_place(["word", "a", "h8"]), (8, 8, "H", "WORD"))
        self.assertEqual(D.parse_place(["word", "d", "8h"]), (8, 8, "V", "WORD"))
        self.assertEqual(D.parse_place(["WoRD", "8h"]), (8, 8, "H", "W_ORD"))        # BobGrid's p WoRD 8h
        self.assertEqual(D.parse_place(["WORD", "c12"]), (3, 12, "V", "WORD"))
        self.assertEqual(D.parse_place(["12c", "w_ord"]), (3, 12, "H", "W_ORD"))
        self.assertEqual(D.parse_place(["8d", "word"]), (4, 8, "H", "WORD"))           # one case: no blanks
        for bad in (["8d"], ["8p", "WORD"], ["16a", "WORD"], ["8d", "WO1D"], ["8d", "W((O))"], ["WORD", "x", "8d"]):
            self.assertIsNone(D.parse_place(bad), bad)
        self.assertEqual(D.shown_word("MUZJ_IKS"), "MUZJiKS")
        self.assertEqual(D.square_text(4, 8, "H"), "8D")
        self.assertEqual(D.square_text(4, 8, "V"), "D8")

    def test_refusals_in_doom_words(self):
        self.assertEqual(D.refusal_text("your rack has no Q"), "You would need another `Q' to play that word.")
        self.assertEqual(D.refusal_text("the first word must cover the center square h8"),
                         "Your first play must cross H8.")
        self.assertEqual(D.refusal_text("the word must join the words already on the board"),
                         "That word would not touch any others.")
        self.assertEqual(D.refusal_text("not in the word list: ZZX QQ"), "Not in this server's word list: ZZX, QQ.")
        self.assertEqual(D.refusal_text("the word runs off the board", d="V"),
                         "That would go off the bottom edge of the board.")
        # DOoM players type the whole word: a word that leaves out the tiles at its ends is refused as DOoM did
        st = put(new_state(racks=["OXXXXXX", "TTTTTTT"]), 6, 8, "H", "TO")
        why = apply(st, 0, "PLAY", "8", "8", "H", "O")["error"]
        self.assertEqual(D.refusal_text(why), "There is a tile on the board before that word.")
        st = put(new_state(racks=["SXXXXXX", "TTTTTTT"]), 7, 8, "H", "AT")
        why = apply(st, 0, "PLAY", "6", "8", "H", "S")["error"]
        self.assertEqual(D.refusal_text(why), "There is a tile on the board after that word.")

    def view(self):
        from adapters.games import gridwords as G
        lines = ["V gridwords/1 9", "X 7 8 H 8 8 E 9 8 L 10 8 L 11 8 o", "R AE_RSTU", "K 0 7", "K 1 7",
                 "BAG 79", "T 0 6", "T 1 0", "W 1", "LAST 0 7 8 H HELLo 6", "CLK 1 250 300"]
        return G.parse_view(lines)

    def test_board_as_bobgrid_reads_it(self):
        v = self.view()
        lines = D.board_lines(v, ["ann", "bob"], 1, "bd_7  Grid Words")
        self.assertEqual(len(lines), 19)
        self.assertEqual(lines[0], "   a b c d e f g h i j k l m n o")
        self.assertEqual(lines[1], lines[17])
        self.assertEqual(lines[18], lines[0])
        self.assertTrue(all(len(l) <= 79 for l in lines))
        b = bobgrid_board(lines[1:], "bob")
        self.assertEqual(b["tiles"], {(7, 8): "H", (8, 8): "E", (9, 8): "L", (10, 8): "L", (11, 8): "o"})
        self.assertEqual(b["players"], [("ann", "", 6, 300), ("bob", "AE?RSTU", 0, 250)])
        self.assertEqual((b["rack"], b["whose"], b["over"], b["timed"], b["running"]), ("AE?RSTU", 2, False,
                                                                                       False, True))
        row = lambda r: lines[1 + r]        # noqa: E731  (the guide, the border, then row 1)
        self.assertEqual(row(7)[38:], "The clock is running.")
        self.assertEqual(row(5)[38:], "Waiting for bob to play.")
        self.assertEqual(row(6)[38:], "The bag contains 79 tiles.")
        # premium squares where BobGrid's coloring reads them (character 2c+1): = TW, - DW, " TL, ' DL
        self.assertEqual(row(1)[2 * 1 + 1], "=")
        self.assertEqual(row(2)[2 * 2 + 1], "-")
        self.assertEqual(row(2)[2 * 6 + 1], '"')
        self.assertEqual(row(1)[2 * 4 + 1], "'")
        self.assertEqual(row(8)[2 * 8 + 1], "E")
        watcher = bobgrid_board(D.board_lines(v, ["ann", "bob"], -1)[1:], "carl")
        self.assertEqual(watcher["rack"], "")
        self.assertEqual([p[1] for p in watcher["players"]], ["", ""])

    def test_track_as_bobgrid_reads_it(self):
        v = self.view()
        lines = D.track_lines(v, 1, tagged=True)
        self.assertEqual(lines[0], "[BEGIN]")
        self.assertEqual(lines[-1], "[END]")
        self.assertTrue(lines[1].startswith("Unseen tiles: "))
        text = "".join(l[14:] for l in lines[1:-1])
        self.assertEqual(len(text), 100 - 5 - 7)
        self.assertRegex(text, r"^[A-Z]+\Z")           # one blank on the board, one on my rack: none unseen
        self.assertEqual(text.count("E"), 12 - 1 - 1)
        self.assertEqual(D.unseen(v, -1)[-1:], "?")     # a watcher has not seen the rack's
        self.assertLessEqual(len(lines), 4)

    def test_log2_as_bobgrid_reads_it(self):
        events = ["JOIN 0 ann", "JOIN 1 bob", "START", "TURN 0", "PLAY 0 7 8 H HELLo 6",
                  "X 7 8 H 8 8 E 9 8 L 10 8 L 11 8 o", "DRAW 0 5", "TURN 1", "PLAY 1 9 6 V TALE 4", "X 9 6 T 9 7 A",
                  "TURN 0", "EXCH 0 3", "TURN 1", "PASS 1", "TURN 0", "PLAY 0 2 12 H RATES 10",
                  "ADJ 0 +9 -", "ADJ 1 -9 QZ", "END win 0 25/-5"]
        names, entries = D.game_moves(events)
        self.assertEqual(names, ["ann", "bob"])
        lines = D.log2_lines(names, entries, tagged=True)
        self.assertEqual(lines[1], "%-40s%s" % ("ann", "bob"))
        self.assertEqual(lines[2], "------- 8g    HELLo            +6    6  ------- i6    TALE             +4    4")
        got = review_tokens(lines[2:-1])
        self.assertEqual([(c, w, t) for _, c, w, t in got],
                         [("8g", "HELLo", 6), ("i6", "TALE", 4), (None, "-3", 6), (None, "-", 4),
                          ("12b", "RATES", 16), (None, "-", -5), (None, "-", 25)])
        self.assertEqual([review_square(c) for _, c, _, _ in got if c], [(7, 8, True), (9, 6, False), (2, 12, True)])
        self.assertEqual((lines[-3][40 + 14], lines[-2][14]), ("(", "("))     # the adjustments, slot by slot
        self.assertEqual(D.log2_lines(["a", "b", "c"], [])[0], "This game has too many players for this log format.")


# ---- against a server ------------------------------------------------------------------------------------

class Stack(WebPlayers, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.words_file = write_words(WORDS)
        cls.old_words = os.environ.get("GRIDWORDS_WORDS")
        os.environ["GRIDWORDS_WORDS"] = cls.words_file         # the core, the extension and the bot read it
        fd, cls.motd_file = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(MOTD) + "\n")
        cls.core, cls.fe, cls.doom, cls.web = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("doom", "web", "bot")
        cls.procs = owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password", "--motd",
                               cls.motd_file))
        cls.procs.append(spawn(*adapter_args("adapters.doom", cls.doom, cls.fe, cls.keys, "doom"), "--poll", "0.2",
                               "--prompt-wait", "3"))
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                               "--think", "0,0", "--pace", "0", "--poll", "0.3", "--gridwords-secs", "0.3"))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)
        if cls.old_words is None:
            os.environ.pop("GRIDWORDS_WORDS", None)
        else:
            os.environ["GRIDWORDS_WORDS"] = cls.old_words
        os.remove(cls.words_file)
        os.remove(cls.motd_file)

    def client(self, cls):
        c = cls(self.doom)
        self.addCleanup(c.close)
        return c

    def native(self, name, password):
        from test_fibs import Native
        n = Native(self.core, name, password)
        self.addCleanup(n.close)
        return n

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def words(self):
        return {w.upper() for w in WORDS.split()}


@slow
class Doom(Stack):
    def test_banner_login_motd_and_the_bot_sentence(self):
        pw = self.signup("dora")
        self.signup("dorian")
        t = self.client(Telnet)
        t.me = "dora"
        banner = t.until(lambda l: l.startswith("You are connector #"))
        self.assertIn("This is ParlorNet's DOoM-protocol service for crossword-game clients. It is", banner)
        self.assertIn("To log on:  connect name password", banner)
        self.assertTrue(all(len(l) <= 79 for l in banner))
        t.send("look")
        self.assertEqual(t.line(), "Huh?  Connect first: `connect name password', or `request name' to sign up.")
        t.send("connect dorian wrong-password")     # (a wrong answer holds that name back for a while)
        self.assertEqual(t.line(), "That password and that name do not match.")
        t.send("connect nobody-here x")
        self.assertEqual(t.line(), "There is no player named nobody-here here. To make an account, enter "
                                   "`request nobody-here'.")
        t.send("connect dora %s" % pw)
        got = t.until(lambda l: l.startswith("Enter `help intro' to begin"))
        i = got.index(MOTD_TOP)
        self.assertEqual(got[i - 1], "Welcome, dora. This is ParlorNet's crossword room; games here are unrated.")
        self.assertEqual(got[i:i + 4], [MOTD_TOP] + MOTD + [MOTD_BOTTOM])
        self.assertEqual(got[i + 4], "To play the bot, type invite bot, and the bot joins you on a new board within a "
                                     "few seconds.")
        self.assertEqual(got[i + 5], "Enter `help intro' to begin, `look' to see the boards, or `who' for the players.")
        self.assertEqual(t.line(), "Gaming Room [parlornet:gaming]")
        # the sentence's command works: the bot sits down at a new board
        t.send("invite bot")
        made = t.until(lambda l: l.startswith("You invite bot to bd_"))
        tid = re.search(r"bd_(\d+)", made[-1]).group(1)
        self.assertIn("You set up bd_%s and join the game." % tid, made)
        self.assertEqual(t.line(), "bot is the server's bot. It will join you in a few seconds.")
        t.until("bot joins the game.")
        t.until("dora starts the game.")
        t.send("resign")
        t.until("dora resigns.")
        self.assertEqual(t.line(), "The server ends the game.")
        self.assertRegex(t.line(), r"^> Final scores: dora \d+, bot \d+\.$")
        # who lists the players online and the bot; the protocol is declared
        t.send("who")
        rows = t.until(lambda l: l.startswith("bot "))
        self.assertEqual(rows[0], "Who          Idle  NetR  Where in VR      Doing in VR")
        n = self.native("dora", pw)
        self.assertIn("PRO DOOM %d DOoM crossword-game server" % self.doom, n.req("PRL"))

    def test_join_says_the_client(self):
        """After DOoM's own "joins the game.", a line says what the player plays with, where their program said;
        a later change of program is told too (docs/protocol.md, CLIENT)."""
        pw = self.signup("tara")
        t = self.client(Telnet)
        t.me = "tara"
        t.login("tara", pw)
        t.send("newboard")
        got = t.until(lambda l: l.startswith("Enter `invite <name>'"))
        tid = re.search(r"bd_(\d+)", got[-2]).group(1)
        npw = self.signup("nate")
        n = self.native("nate", npw)
        n.req("CLI PROG BobGrid 2.3")
        n.req("CLI MACH Sun SPARCstation")
        self.assertTrue(n.req("JON %s" % tid)[-1].startswith("OK"))
        t.until("nate joins the game.")
        self.assertEqual(t.line(), "nate plays with BobGrid 2.3 on a Sun SPARCstation.")
        self.assertEqual(t.line(), "tara starts the game.")
        other = self.native("nate", npw)
        other.req("CLI PROG ParlorNet for the Apple IIe 1.1")
        other.req("CHT %s hi" % tid)
        t.until("nate now plays with ParlorNet for the Apple IIe 1.1.")

    def test_telnet_player_and_web_player_play_a_game(self):
        """A person at a terminal sets up a board and invites a web player, who joins from the lobby; they play
        until the game ends, the telnet player with place and commit, the web player with the page's form."""
        pw = self.signup("tess")
        self.web_login("webb")
        t = self.client(Telnet)
        t.me = "tess"
        t.login("tess", pw)
        t.send("newboard")
        got = t.until(lambda l: l.startswith("Enter `invite <name>'"))
        tid = re.search(r"bd_(\d+)", got[-2]).group(1)
        self.assertEqual(got[-2:], ["You set up bd_%s for 2 players (classic rules) and join the game." % tid,
                                    "Enter `invite <name>' to ask someone, or `invite bot' for the server's bot."])
        t.send("invite webb")
        self.assertEqual(t.until(lambda l: l.startswith("You invite")), ["You invite webb to bd_%s." % tid])
        self.assertEqual(t.line(), "webb is on the web or by telnet and will see the invitation there.")
        t.until("webb is invited to this game.")
        lobby = self.web_get("/lobby.htm?n=webb")[2]
        self.assertIn("join.htm", lobby)
        self.assertEqual(self.web_get("/join.htm?t=%s&n=webb" % tid)[0], 302)
        t.until("webb joins the game.")
        self.assertEqual(t.line(), "tess starts the game.")
        self.assertEqual(t.line(), "tess starts the clock.")
        self.assertEqual(t.line(), "It is tess's turn.")
        web = self.native("webb", "webb-Pass-word")
        words = self.words()
        plays = web_plays = 0
        end = time.time() + 240
        my_turn = True
        while True:
            self.assertLess(time.time(), end, t.seen[-30:])
            if my_turn:
                my_turn = False
                b, rows = t.board()
                self.assertEqual(rows[5][38:], "Waiting for tess to play.")
                found = play_from(b, words)
                if found:
                    score, (_, x, y, d, w) = found
                    t.send("place %s %s" % (doom_square(int(x), int(y), d), w))
                    sq = D.square_text(int(x), int(y), d)
                    self.assertEqual(t.line(), "You place %s at %s for %d point%s." % (w, sq, score,
                                                                                      "" if score == 1 else "s"))
                    t.until("Enter `commit' to play it, or `undo' to take it back.")
                    if plays == 0:              # the placed word shows on the board until it is committed
                        placed, _ = t.board()
                        self.assertEqual(len(placed["tiles"]), len(b["tiles"]) + len(w) - sum(
                            1 for i in range(len(w)) if (int(x) + (i if d == "H" else 0),
                                                         int(y) + (i if d == "V" else 0)) in b["tiles"]))
                    t.send("commit")
                    self.assertEqual(t.line(), "tess plays %s at %s for %d points." % (w, sq, score))
                    plays += 1
                elif bag_of(web.req("VUE %s" % tid)) >= 7:     # no word from the list: new tiles instead
                    t.send("exchange %s" % b["rack"])
                    self.assertEqual(t.line(), "tess exchanges %s." % D.tiles_word(len(b["rack"])))
                else:
                    t.send("pass")
                    self.assertEqual(t.line(), "tess passes.")
            else:
                v = web.req("VUE %s" % tid)
                w_turn = next((l for l in v if l.startswith("W ")), "W -1")
                if w_turn == "W 1":
                    found = best_play(v[:-1], words)
                    q = "mv=PASS"
                    if found:
                        _, (_, x, y, d, word) = found
                        q = form_fields(v[:-1], x, y, d, word)      # the web form: the rack's letters only
                        web_plays += 1
                    elif bag_of(v) >= 7:                            # the exchange form: the whole rack back
                        q = "mv=EXCH%%20%s" % next(l.split()[1] for l in v if l.startswith("R "))
                    self.assertEqual(self.web_get("/move.htm?t=%s&%s&n=webb" % (tid, q))[0], 302)
            try:
                l = t.line(0.3)
            except AssertionError:
                continue
            while True:
                if l == "It is tess's turn.":
                    my_turn = True
                if l.endswith("ends the game."):
                    break
                try:
                    l = t.line(0.3)
                except AssertionError:
                    break
            if l.endswith("ends the game."):
                break
        final = t.line()
        self.assertRegex(final, r"^> Final scores: tess -?\d+, webb -?\d+\.$")
        # how long the game runs is the deal's: on the test's short word list a board can leave neither player a
        # word, and six scoreless turns in a row end it (classic rules). Both ways of playing were used, every
        # play made is on the record, and a game no rack played out ended on six scoreless turns.
        turns = [m.groups() for m in (re.match(r"^(tess|webb) (plays \S+ at|passes\.|exchanges)", l)
                                      for l in t.seen) if m]
        played = [who for who, kind in turns if kind.startswith("plays")]
        self.assertEqual((played.count("tess"), played.count("webb")), (plays, web_plays))
        self.assertGreater(plays, 0)
        self.assertGreater(web_plays, 0)
        if not any(l.endswith(" plays out.") for l in t.seen):
            self.assertEqual(len(turns[-6:]), 6)
            self.assertFalse([kind for _, kind in turns[-6:] if kind.startswith("plays")], turns[-8:])
        scores = [int(x) for x in re.findall(r" (-?\d+)", final)]
        view = web.req("VUE %s" % tid)
        self.assertEqual(scores, [int(l.split()[2]) for l in view if l.startswith("T ")])
        # the log, both ways
        t.send("log")
        self.assertRegex(t.line(), r"^  1 tess ")
        t.send("log2 bd_%s" % tid)
        t.until("%-40s%s" % ("tess", "webb"))

    def test_bobgrid_plays_the_bot(self):
        """BobGrid, scripted as its classes behave: it logs in from its config, invites the bot, redraws on the
        server's three lines and plays by pick-and-click's commands until the game is over. Its log and its
        tile tracking agree with its board."""
        pw = self.signup("bobby")
        g = self.client(BobGrid)
        g.login("bobby", pw)
        g.send("invite bot")
        tid = re.search(r"bd_(\d+)", g.until(lambda l: l.startswith("You invite bot to bd_"))[-1]).group(1)
        words = self.words()
        end = time.time() + 240
        plays = 0
        acted_on = None
        while not g.ended or g.queue:
            self.assertLess(time.time(), end, g.seen[-30:])
            g.pump(0.3)
            if g.ended:
                g.pump(1.5)
                break
            if g.my_turn() and acted_on is not g.state:
                acted_on = g.state
                found = play_from(g.state, words)
                if found:
                    score, (_, x, y, d, w) = found
                    g.send("p %s %s" % (w, doom_square(int(x), int(y), d, bobgrid=True)))
                    g.send("b")
                    g.until(lambda l: l.startswith("You place "))
                    g.send("commit")
                    g.send("b")
                    plays += 1
                else:
                    g.send("pass")
        self.assertTrue(g.state["over"])
        self.assertGreater(plays, 0)
        # the log as BobGrid replays it: every play's letters are where its board has them
        p1, p2, entries = g.log
        self.assertEqual((p1, p2), ("bobby", "bot"))
        for rack, coord, word, total in entries:
            if coord:
                col, row, across = review_square(coord)
                for i, ch in enumerate(word):
                    self.assertEqual(g.state["tiles"][(col + i, row) if across else (col, row + i)], ch, coord)
        totals = {}
        for i, (_, _, _, total) in enumerate(entries):
            totals[i % 2] = total
        self.assertEqual([totals[0], totals[1]], [p[2] for p in g.state["players"]])
        # unseen tiles: the set less the board and my own rack
        self.assertEqual(len(g.track), 100 - len(g.state["tiles"]) - len(g.state["rack"]))
        g.send("log2 -tagged bd_%s" % tid)
        g.pump(1)

    def test_invitation_from_the_web_and_decline(self):
        pw = self.signup("ivy")
        self.web_login("wendy")
        t = self.client(Telnet)
        t.me = "ivy"
        t.login("ivy", pw)
        n = self.native("wendy", "wendy-Pass-word")
        tid = n.req("NEW gridwords english")[-1].split()[1]
        n.req("JON %s" % tid)
        self.assertEqual(n.req("INV %s ivy" % tid)[-1], "OK")
        self.assertEqual(t.until(lambda l: "invites you" in l)[-1],
                         "wendy invites you to a game of Grid Words on bd_%s. Enter `accept wendy' to sit down "
                         "there, or `decline wendy'." % tid)
        t.send("decline wendy")
        self.assertEqual(t.line(), "You decline the invitation from wendy.")
        self.assertIn("UNINVITE", " ".join(n.req("SIN %s 0" % tid)))
        n.req("INV %s ivy" % tid)
        t.until(lambda l: "invites you" in l)
        t.send("accept wendy")
        self.assertEqual(t.until(lambda l: l.startswith("You go to"))[-1], "You go to bd_%s." % tid)
        t.until("You are playing in this game.")
        t.send("place 8h HAT")
        self.assertIn(t.line(), ("It's not your turn.", "You place HAT at 8H for 12 points."))
        t.send("ospd cat zzzq")
        t.until("Not in this server's word list: ZZZQ.")
        t.send("ospd cat")
        self.assertEqual(t.line(), "CAT is in this server's word list.")


if __name__ == "__main__":
    unittest.main()
