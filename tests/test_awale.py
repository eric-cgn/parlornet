"""The Awale extension (adapters/awale.py): Mancala for Awale 2.x over IRC.

The scripted client below behaves as Awale 2.2.2 does on the wire: it registers
with USER and NICK (no PASS), lists and joins #AWCH0000 with the key, sets the key and the topic, introduces
itself (codes 8 and 1), pings every member that introduces itself, and plays by its own copy of Awale's
"Awale" rule: it decides who starts by the choice numbers (code 9), keeps its own board, checks the board
field of every move it receives against its own, refuses a move that is illegal by its rule, and ends a game
by itself when a player cannot move, as the program does. Commands start with ! (the program refuses /)."""
import os
import random
import re
import socket
import sys
import time
import unittest

import procs
from procs import owned, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import awale as A  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 25
LOG = []                # the extension's log, for a failing test's message


class Board:
    """Awale's "Awale" rule, from one player's side: my six holes from my left, then the other's."""

    def __init__(self):
        self.mine, self.theirs = [4] * 6, [4] * 6
        self.caught, self.lost = 0, 0
        self.moves = 0

    def sow(self, mover_is_me, hole):
        row = (self.mine + self.theirs) if mover_is_me else (self.theirs + self.mine)
        n = row[hole]
        assert n, "an empty hole"
        row[hole] = 0
        pos = hole
        while n:
            pos = (pos + 1) % 12
            if pos == hole:
                continue                    # twelve or more seeds skip the starting hole
            row[pos] += 1
            n -= 1
        take, i = [], pos
        while i >= 6 and row[i] in (2, 3):
            take.append(i)
            i -= 1
        got = sum(row[i] for i in take)
        if take and got != sum(row[6:]):    # a capture that leaves the other nothing captures nothing
            for i in take:
                row[i] = 0
        else:
            got = 0
        if mover_is_me:
            self.mine, self.theirs, self.caught = row[:6], row[6:], self.caught + got
        else:
            self.theirs, self.mine, self.lost = row[:6], row[6:], self.lost + got
        self.moves += 1

    def copy(self):
        b = Board()
        b.mine, b.theirs, b.caught, b.lost, b.moves = list(self.mine), list(self.theirs), self.caught, self.lost, \
            self.moves
        return b

    def legal(self, me=True):
        """Awale: a non-empty hole; one that feeds an empty opponent when any does."""
        own, other = (self.mine, self.theirs) if me else (self.theirs, self.mine)
        holes = [h for h in range(6) if own[h]]
        if sum(other) == 0:
            feeding = [h for h in holes if own[h] > 5 - h]
            if feeding:
                return feeding
        return holes


class Awale:
    """Awale 2.2.2 connected to the extension."""

    def __init__(self, port, name, choose=None):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.nick = "awpl%04x" % random.randrange(0x10000)
        self.name = name
        self.buf = b""
        self.lines = []
        self.members = {}           # nick -> name, from their introductions
        self.pongs = set()
        self.chats = []             # private lines to me (<name> : text)
        self.challenges = []        # nicks that challenged me
        self.accepted, self.refused = [], []
        self.ended = []             # (nick, reason, result) of code 10
        self.opp = None
        self.board = None
        self.started_by_me = None
        self.my_turn = False
        self.over = None            # how my own game ended: "normal", or code 10's reason
        self.choices = list(choose or [])
        self.mine_choice = None
        self.send("USER %s %s %s :%s" % (self.nick, self.nick, self.nick, name))
        self.send("NICK %s" % self.nick)
        self.read(lambda: any(" 376 " in l for l in self.lines))
        self.send("LIST #AWCH0000")
        self.send("JOIN #AWCH0000 GBShare")
        self.send("MODE #AWCH0000 +k GBShare")
        self.send("TOPIC #AWCH0000 :Awale_players'_private_room")
        self.introduce()

    def close(self):
        self.s.close()

    # ---- the wire -----------------------------------------------------------------------------------------
    def send(self, line):
        self.s.sendall(line.encode("latin-1") + b"\n")         # Awale ends its lines with LF alone

    def msg(self, target, code, text):
        self.send("PRIVMSG %s :%03d %s" % (target, code, text))

    def introduce(self):
        self.msg("#AWCH0000", 8, "I'm not playing for the moment.")
        self.msg("#AWCH0000", 1, A.intro(self.name, "me@example.org", "France", 100, "Awale", A.RULE_HASH, "NULL",
                                         "12:00:00", "10.0.2.15", "0", "546", A.NO_GAME, "Hello"))

    def say(self, text):
        self.msg("#AWCH0000", 7, "I say to everybody (#AWCH0000) : (%s)" % text)

    def read(self, until, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            left = end - time.time()
            if left <= 0:
                raise AssertionError("timed out; chats %r; log %r" % (self.chats[-6:], LOG[-12:]))
            self.s.settimeout(min(left, 1.0))
            try:
                data = self.s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                raise EOFError("closed; chats %r; log %r" % (self.chats[-6:], LOG[-12:]))
            self.buf += data
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                line = line.rstrip(b"\r").decode("latin-1")
                self.lines.append(line)
                self.handle(line)

    def nick_of(self, name):
        return next((n for n, nm in self.members.items() if nm.lower() == name.lower()), None)

    def handle(self, line):
        prefix, cmd, args = A.parse_irc(line)
        if cmd == "PING":
            self.send("PONG :%s" % args[-1])
            return
        if cmd != "PRIVMSG" or len(args) < 2:
            return
        m = A.parse_message(args[1])
        if m is None:
            return
        code, body = m
        f = A.fields(body)
        if code == A.INTRO:
            self.members[prefix] = f[0]
            self.msg(prefix, A.PING, "Ping")
        elif code == A.PONG:
            self.pongs.add(prefix)
        elif code == A.SAY:
            self.chats.append(f[1] if len(f) > 1 else "")
        elif code == A.CHALLENGE and f[0] == self.nick:
            self.challenges.append(prefix)
        elif code == A.ACCEPT and f[0] == self.nick:
            self.accepted.append(prefix)
            self.opp = prefix
            self.start_choosing()
        elif code == A.REFUSE and f[0] == self.nick:
            self.refused.append(prefix)
        elif code == A.PLAYING and prefix == self.opp and self.mine_choice is None and self.board is None:
            self.start_choosing()
        elif code == A.CHOOSE and prefix == self.opp:
            theirs = int(f[0])
            if theirs == self.mine_choice:
                self.choose()                   # a tie: both choose again
                return
            self.started_by_me = self.mine_choice > theirs
            self.board = Board()
            self.msg("#AWCH0000", A.MOVE, "I play against %s (%s) MY hole #(-1):(%s)" % (
                self.members[self.opp], self.opp, A.board(0, self.started_by_me, self.board.mine, self.board.theirs,
                                                          0, 0, -1)))
            self.my_turn = self.started_by_me
        elif code == A.MOVE and f[0] == self.nick and prefix == self.opp:
            hole, got = int(f[1]), f[2]
            assert self.board is not None and not self.my_turn, "a move out of turn"
            assert hole in self.board.legal(me=False), "Awale refuses hole %d: %r" % (hole, self.board.theirs)
            self.board.sow(False, hole)
            want = A.board(self.board.moves, not self.started_by_me, self.board.theirs, self.board.mine,
                           self.board.lost, self.board.caught, hole)
            assert got == want, "the board of the move %r is not Awale's %r" % (got, want)
            self.my_turn = True
            self.check_end()
        elif code == A.ENDED and f[0] == self.nick:
            self.ended.append((prefix, int(f[1]), int(f[2])))
            if self.over is None:
                self.over = int(f[1])
                self.msg("#AWCH0000", A.ENDED, "The game with %s (%s) ended because : (%s) x Result : (-1) x" % (
                    self.members.get(prefix, "?"), prefix, f[1]))
                self.introduce()

    def start_choosing(self):
        self.msg("#AWCH0000", A.PLAYING, "I'm now playing with %s (%s)" % (self.members[self.opp], self.opp))
        self.choose()

    def choose(self):
        self.mine_choice = self.choices.pop(0) if self.choices else random.randrange(32768)
        self.msg(self.opp, A.CHOOSE, "I choose : (%d)" % self.mine_choice)

    def check_end(self):
        """Awale ends the game when the player to move cannot play any hole."""
        b = self.board
        stuck = not any(b.mine) if self.my_turn else not any(b.theirs)
        if stuck and self.over is None:
            self.over = "normal"
            self.my_turn = False
            self.msg("#AWCH0000", A.ENDED, "The game with %s (%s) ended because : (4) It terminated normally. "
                                           "Result : (-1) x" % (self.members[self.opp], self.opp))
            self.introduce()

    # ---- moves ----------------------------------------------------------------------------------------------
    def challenge(self, name):
        nick = self.nick_of(name)
        assert nick, "no member %s: %r" % (name, self.members)
        self.opp = nick
        self.msg("#AWCH0000", A.CHALLENGE, "I want to challenge %s (%s)" % (name, nick))

    def accept(self, nick):
        self.opp = nick
        self.msg("#AWCH0000", A.ACCEPT, "I accept the challenge from %s (%s)" % (self.members[nick], nick))

    def refuse(self, nick):
        self.msg("#AWCH0000", A.REFUSE, "I refuse the challenge from %s (%s)" % (self.members[nick], nick))

    def play(self, hole=None):
        b = self.board
        if hole is None:
            hole = b.legal()[0]
        b.sow(True, hole)
        self.my_turn = False
        self.msg("#AWCH0000", A.MOVE, "I play against %s (%s) MY hole #(%d):(%s)" % (
            self.members[self.opp], self.opp, hole,
            A.board(b.moves, self.started_by_me, b.mine, b.theirs, b.caught, b.lost, hole)))
        self.check_end()


class Wire(unittest.TestCase):
    """The formats, against what Awale 2.2.2 sent in a captured session."""

    def test_board_field(self):
        # the Mac's first move (hole 2), its sixth (hole 5), and one after captures (9 and 5)
        self.assertEqual(A.board(1, True, [4, 4, 0, 5, 5, 5], [5, 4, 4, 4, 4, 4], 0, 0, 2),
                         "0001004040005050505040404040400000002")
        self.assertEqual(A.board(0, True, [4] * 6, [4] * 6, 0, 0, -1), "000000404040404040404040404040000ffff")
        self.assertEqual(A.board(0, False, [4] * 6, [4] * 6, 0, 0, -1), "000010404040404040404040404040000ffff")
        got = A.read_board("0009001010001010802010908060500050002")
        self.assertEqual((got["moves"], got["mine"], got["theirs"], got["caught"], got["lost"], got["hole"]),
                         (9, [1, 1, 0, 1, 1, 8], [2, 1, 9, 8, 6, 5], 0, 5, 2))
        self.assertEqual(A.read_board("ffff00000000000000000000000000030ffff")["lost"], 48)
        self.assertEqual(A.read_board("003300002000102010100010101000c1a0002")["caught"], 12)

    def test_messages(self):
        self.assertEqual(A.parse_irc(":awpl1c4e!x@h PRIVMSG #AWCH0000 :006 I play against bot (awpl0411) MY "
                                     "hole #(2):(0001004040005050505040404040400000002)")[:2],
                         ("awpl1c4e", "PRIVMSG"))
        code, body = A.parse_message("006 I play against bot (awpl0411) MY hole #(2):(abc)")
        self.assertEqual((code, A.fields(body)), (6, ["awpl0411", "2", "abc"]))
        f = A.fields(A.intro("anne", "a@b", "France", 100, "Awale", A.RULE_HASH, "NULL", "03:20:57", "10.0.2.15",
                             "0", "546", A.NO_GAME, "Hello (there)"))
        self.assertEqual(f, ["anne", "a@b", "France", "100", "Awale", A.RULE_HASH, "NULL", "03:20:57", "10.0.2.15",
                             "0", "546", A.NO_GAME, "Hello [there]"])

    def test_who_starts(self):
        """The bigger number starts; a tie chooses again."""
        self.assertEqual(A.answer_choice(4769, True), 4768)
        self.assertEqual(A.answer_choice(4769, False), 4770)
        self.assertEqual(A.answer_choice(0, True), 0)
        self.assertEqual(A.answer_choice(32767, False), 32767)

    def test_nicks_and_commands(self):
        a = A.nick_for("bot", set())
        self.assertRegex(a, r"^awpl[0-9a-f]{4}$")
        self.assertNotEqual(A.nick_for("bot", {a}), a)
        self.assertEqual(A.bang(["Commands: /list, /invite NAME, /help.", "1/2 and a/b stay"]),
                         ["Commands: !list, !invite NAME, !help.", "1/2 and a/b stay"])


class Stack(unittest.TestCase):
    auth = "password"

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("awale", "bot")
        del LOG[:]
        cls.procs = owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth))
        cls.procs.append(spawn(*adapter_args("adapters.awale", cls.port, cls.fe, cls.keys, "awale"),
                               "--poll", "0.2", "--lobby-poll", "0.5", "--trace", err=LOG))
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def native(self, name, password):
        from test_fibs import Native
        n = Native(self.core, name, password)
        self.addCleanup(n.close)
        return n

    def awale(self, name, choose=None):
        a = Awale(self.port, name, choose)
        self.addCleanup(a.close)
        return a

    def signed_in(self, name):
        pw = self.signup(name)
        a = self.awale(name)
        a.read(lambda: a.nick_of("ParlorNet"))
        if self.auth != "none":
            a.read(lambda: any("!signin NAME PASSWORD" in c for c in a.chats))
            a.say("!signin %s %s" % (name, pw))
        a.read(lambda: any(c.startswith("Welcome, %s." % name) for c in a.chats))
        a.read(lambda: a.nick_of("bot") in a.pongs)
        return a, pw


@slow
class Service(Stack):
    def test_sign_in_and_commands(self):
        pw = self.signup("ada")
        a = self.awale("ada")
        a.read(lambda: any("!signin NAME PASSWORD" in c for c in a.chats))
        self.assertIn(a.nick_of("ParlorNet"), a.pongs or a.read(lambda: a.pongs))
        a.say("!who")
        a.read(lambda: any("Sign in first" in c for c in a.chats))
        a.say("!signin nobody some-password")
        a.read(lambda: any("There is no account nobody here" in c for c in a.chats))
        self.assertIsNone(a.nick_of("bot"))
        a.say("!signin ada %s" % pw)
        a.read(lambda: any(c.startswith("Welcome, ada.") for c in a.chats))
        a.read(lambda: a.nick_of("bot"))
        n = len(a.chats)
        a.say("!help")
        a.read(lambda: any(c.startswith("Commands: !list") for c in a.chats[n:]))
        self.assertFalse(any(re.search(r"(^|\s)/[a-z]", c) for c in a.chats[n:]), a.chats[n:])
        n = len(a.chats)
        a.say("hello")
        a.read(lambda: any("during a game" in c for c in a.chats[n:]))
        # a challenge to the server itself is refused
        a.challenge("ParlorNet")
        a.read(lambda: a.refused)
        self.assertFalse(any("signin" in l and pw in l for l in LOG), "the password is never logged")

    def test_a_whole_game_against_the_bot(self):
        a, _ = self.signed_in("bea")
        a.challenge("bot")
        a.read(lambda: a.accepted)
        a.read(lambda: a.board is not None)
        self.assertTrue(a.started_by_me, "the Mac sits in seat 0 at a table it opens, and moves first")
        a.read(lambda: any("you move first" in c for c in a.chats))
        for _ in range(400):
            if a.over is not None:
                break
            if a.my_turn:
                a.play(a.board.legal()[len(a.board.legal()) // 2])
            a.read(lambda: a.my_turn or a.over is not None)
        self.assertIsNotNone(a.over, "the game ends")
        a.read(lambda: any("Final score" in c for c in a.chats))
        final = [c for c in a.chats if "Final score" in c][-1]
        me, them = map(int, re.search(r"Final score (\d+) to (\d+)", final).groups())
        if a.over == A.DEADLOCK:            # the core ended it first: what Awale shows are the captures
            self.assertEqual((a.board.caught, a.board.lost), (me, them))
        else:                               # Awale ended it: each takes the seeds on its own row
            self.assertEqual((a.board.caught + sum(a.board.mine), a.board.lost + sum(a.board.theirs)), (me, them))
        a.read(lambda: any(":008 " in l and a.opp in l for l in a.lines[-5:]))     # the bot is free again

    def test_an_invitation_arrives_as_a_challenge(self):
        a, _ = self.signed_in("cid")
        pw = self.signup("dan")
        d = self.native("dan", pw)
        tid = d.req("NEW mancala/1 oware")[-1].split()[1]
        self.assertTrue(d.req("JON %s 0" % tid)[-1].startswith("OK"))
        self.assertTrue(d.req("INV %s cid 1" % tid)[-1].startswith("OK"))
        a.read(lambda: a.challenges)
        self.assertEqual(a.members[a.challenges[0]], "dan")
        a.choices = [100]                   # dan sits in seat 0: the member's number must be bigger
        a.accept(a.challenges[0])
        a.read(lambda: a.board is not None)
        self.assertFalse(a.started_by_me)
        d.req("MOV %s 3" % tid)
        a.read(lambda: a.my_turn)
        self.assertEqual(a.board.theirs, [4, 4, 0, 5, 5, 5])
        a.play(0)
        end = time.time() + WAIT
        while time.time() < end and "W 0" not in d.req("VUE %s" % tid):
            time.sleep(0.2)
        self.assertIn("B 4 4 0 5 5 5 0 0 5 5 5 5 5 0", d.req("VUE %s" % tid))
        d.req("CHT %s good game" % tid)
        a.read(lambda: "good game" in a.chats)
        d.req("RES %s" % tid)
        a.read(lambda: a.ended)
        self.assertEqual(a.ended[-1][1:], (A.ABANDONED, A.LOST))
        a.read(lambda: any("dan resigned" in c for c in a.chats))

    def test_who_sits_down_says_with_what(self):
        """A player who sits down at the Mac's open table: the line that says so says what they play with."""
        a, _ = self.signed_in("ida")
        n = len(a.chats)
        a.say("!open")
        a.read(lambda: any("is open" in c for c in a.chats[n:]))
        tid = re.search(r"table (\w+) is open", next(c for c in a.chats[n:] if "is open" in c)).group(1)
        pw = self.signup("jim")
        j = self.native("jim", pw)
        j.req("CLI PROG Xawale 0.9")
        n = len(a.chats)
        self.assertTrue(j.req("JON %s" % tid)[-1].startswith("OK 1"))
        a.read(lambda: any("jim sat down at table %s with Xawale 0.9. Accept their challenge to play." % tid in c
                           for c in a.chats[n:]))

    def test_a_refused_invitation_is_declined(self):
        a, _ = self.signed_in("eve")
        pw = self.signup("fay")
        f = self.native("fay", pw)
        tid = f.req("NEW mancala/1 oware")[-1].split()[1]
        f.req("JON %s 0" % tid)
        f.req("INV %s eve 1" % tid)
        a.read(lambda: a.challenges)
        a.refuse(a.challenges[0])
        a.read(lambda: any("declined" in c for c in a.chats))
        end = time.time() + WAIT
        while time.time() < end and not any("UNINVITE" in l and "declined" in l for l in f.req("SIN %s 0" % tid)):
            time.sleep(0.2)
        self.assertTrue(any("UNINVITE" in l and "declined" in l for l in f.req("SIN %s 0" % tid)))
        # store rules: named in the chat, never offered as a challenge
        n = len(a.challenges)
        tid = f.req("NEW mancala/1 kalah")[-1].split()[1]
        f.req("JON %s 0" % tid)
        f.req("INV %s eve 1" % tid)
        a.read(lambda: any("Oware rules only" in c for c in a.chats))
        self.assertEqual(len(a.challenges), n)

    def test_the_mac_cancels_its_game(self):
        a, _ = self.signed_in("gil")
        a.challenge("bot")
        a.read(lambda: a.board is not None)
        a.play(a.board.legal()[0])
        a.read(lambda: a.my_turn)
        tid = next(re.search(r"table (\d+)", c).group(1) for c in a.chats if "Your game with" in c)
        a.msg("#AWCH0000", A.ENDED, "The game with bot (%s) ended because : (1) I abandonned. Result : (1) He won."
              % a.opp)
        a.read(lambda: any("You resigned table %s." % tid in c for c in a.chats))


class NameOnly(Stack):
    """A parlor that signs in by name alone: the name in Awale's Network setup is the sign-in."""
    auth = "none"

    def test_the_setup_name_signs_in(self):
        a = self.awale("hal")
        a.read(lambda: any(c.startswith("Welcome, hal.") for c in a.chats))
        a.read(lambda: a.nick_of("bot"))


if __name__ == "__main__":
    unittest.main()
