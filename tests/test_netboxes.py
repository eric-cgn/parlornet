"""NetBoxes 1.1 on the NetSprocket extension (adapters/nsp_netboxes.py): Dots and Boxes on PowerPC Macs.

The scripted client below joins the way the program does: a NetSprocket
join request with player type 0, its version request once approved, its hello once it has a slot, then chat
lines and lines as NetBoxes' own messages. Like the program, it keeps the game itself: the players in slot
order, whose turn it is (it moves only when a PASS gives it the turn, never as the receiver of a start), the
lines drawn and the boxes, and it fails a test when the host sends what the program could not follow (a line
already drawn, a PASS after a line that closed a box, a move while its own turn)."""
import os
import socket
import sys
import time
import unittest

import procs
from procs import owned, spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import netboxeswire as W  # noqa: E402
from adapters import nsp_netboxes as B  # noqa: E402
from adapters import nspwire as N  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 25
LOG = []                # the extension's log, for a failing test's message


def email_signup(core, name, addr, mails, timeout=10):
    """A sign-up by email over the native protocol, as on the web or by telnet, left pending: -> the password
    in its mail, read from the file parlord's test transport writes (email.test_file). Never printed."""
    import json
    import re
    s = socket.create_connection(("127.0.0.1", core), timeout=5)
    s.sendall(b"PARLORNET/1\r\n")           # the opening first, then the greeting
    f = s.makefile("rb")
    f.readline()

    def req(text):
        s.sendall(text.encode() + b"\r\n")
        while True:
            l = f.readline().decode().strip()
            if l.startswith(("OK", "ERR", "ASK")):
                return l
    assert req("REG %s" % name) == "ASK email Email_address"
    assert req("AUT email %s" % addr) == "ASK password Password_from_the_email secret"
    s.close()
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(mails, encoding="utf-8") as fh:
                mine = [m for m in (json.loads(l) for l in fh if l.strip()) if m["to"] == addr]
        except OSError:
            mine = []
        if mine:
            return re.search(r"Password:\s+(\S+)", mine[-1]["body"]).group(1)
        time.sleep(0.1)
    raise AssertionError("no mail to %s" % addr)


class Stack(unittest.TestCase):
    """The core, the NetSprocket extension and the bot."""
    core_extra = ()             # more arguments for the core (Pending)
    adapter_extra = ()          # more arguments for the extension

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("netsprocket", "bot")
        del LOG[:]
        cls.procs = owned(cls)
        cls.procs.append(spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password", *cls.core_extra))
        cls.procs.append(spawn(*adapter_args("adapters.netsprocket", cls.port, cls.fe, cls.keys, "netsprocket"),
                               "--poll", "0.2", *cls.adapter_extra, err=LOG))
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


class Box:
    """NetBoxes joining over TCP/IP."""

    def __init__(self, port, name, password):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.reader = W.Reader()
        self.frames = []
        self.mid = 0
        self.name = name
        self.slot = self.face = None
        self.may_start = None
        self.players = {}       # slot -> [name, face, NetSprocket id]
        self.chats = []         # (name, text)
        self.game = None        # dict once a game starts
        self.aborts = 0
        self.closed = False
        self.send(W.JOIN_REQUEST, 0, 0, W.join_request(name, password, W.PLAYER_TYPE))

    def close(self):
        self.s.close()

    def send(self, what, frm, to, body):
        self.mid += 1
        self.s.sendall(W.pack(what, frm, to, self.mid, 0, body))

    def msg(self, what, body, to=0):
        self.send(what, 2, to, body)

    # ---- the wire -----------------------------------------------------------------------------------------
    def read(self, until, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            left = end - time.time()
            if left <= 0:
                raise AssertionError("timed out; chats %r; log %r" % (self.chats[-8:], LOG[-12:]))
            self.s.settimeout(min(left, 1.0))
            try:
                data = self.s.recv(65536)
            except socket.timeout:
                continue
            if not data:
                self.closed = True
                got = until()
                if got:
                    return got
                raise EOFError("closed; chats %r; log %r" % (self.chats[-8:], LOG[-12:]))
            for f in self.reader.feed(data):
                self.frames.append(f)
                self.handle(f)

    def said(self, text):
        return [t for n, t in self.chats if text in t]

    def handle(self, f):
        k, b = f.what, f.body
        if k == W.JOIN_APPROVED:
            self.msg(W.VERSION_ASK, W.version_ask(), to=-1)
        elif k == W.VERSION_REPLY:
            self.slot, self.face, _, self.may_start, version = W.parse_version_reply(b)
            assert version == W.VERSION
            self.players[self.slot] = [self.name, self.face, 2]
            self.msg(W.HELLO, W.hello(self.name, self.slot, self.face))
        elif k in (W.HELLO, W.HELLO_BACK):
            name, slot, face = W.parse_hello(b)
            assert slot != self.slot, "a player put in the Mac's own slot"
            self.players[slot] = [name, face, f.frm]
            faces = [p[1] for p in self.players.values()]
            assert len(faces) == len(set(faces)), "two players with one face: %r" % self.players
        elif k == W.PLAYER_LEFT:
            _, pid, _ = W.parse_player_left(b)
            for s, p in list(self.players.items()):
                if p[2] == pid and s != self.slot:
                    del self.players[s]
        elif k == W.CHAT:
            text, name = W.parse_chat(b)
            self.chats.append((name, text))
        elif k == W.START:
            cols, rows, secs, first, mines = W.parse_start(b)
            assert not mines
            order = sorted(self.players)
            self.game = {"cols": cols, "rows": rows, "secs": secs, "order": order, "out": set(),
                         "turn": order[first], "mine": False, "lines": set(), "boxes": {}, "peg": None,
                         "over": False, "passes": 0}
        elif k == W.ABORT:
            self.aborts += 1
            if self.game:
                self.game["over"] = True
        elif self.game is None or self.game["over"]:
            return
        elif k == W.PEG:
            assert not self.game["mine"], "a peg from another player on the Mac's turn"
            self.game["peg"] = W.parse_peg(b)
        elif k == W.LINE:
            assert not self.game["mine"], "a line from another player on the Mac's turn"
            x, y = self.game["peg"]
            boxed = self.draw(W.line_token(x, y, W.parse_line(b)), self.game["turn"])
            self.game["last_boxed"] = boxed
        elif k == W.PASS:
            assert not self.game.get("last_boxed"), "a PASS after a line that closed a box"
            self.game["passes"] += 1
            self.next_turn()
        elif k == W.STEP_OUT:
            g = self.game
            g["out"].add(b[0])
            if g["turn"] == b[0]:
                self.next_turn()

    # ---- the game as the program keeps it -----------------------------------------------------------------
    def next_turn(self):
        g = self.game
        i = g["order"].index(g["turn"])
        for _ in range(len(g["order"])):
            i = (i + 1) % len(g["order"])
            if g["order"][i] not in g["out"]:
                break
        g["turn"] = g["order"][i]
        g["mine"] = g["turn"] == self.slot

    def all_lines(self):
        g = self.game
        return ([W.line_token(x, y, 1) for y in range(g["rows"]) for x in range(g["cols"] - 1)] +
                [W.line_token(x, y, 3) for y in range(g["rows"] - 1) for x in range(g["cols"])])

    def sides(self, x, y):
        g = self.game
        return sum(t in g["lines"] for t in (W.line_token(x, y, 1), W.line_token(x, y + 1, 1),
                                               W.line_token(x, y, 3), W.line_token(x + 1, y, 3)))

    def boxes_of(self, token):
        x, y, d = W.line_move(token)
        g = self.game
        near = [(x, y - 1), (x, y)] if d == 1 else [(x - 1, y), (x, y)]
        return [b for b in near if 0 <= b[0] < g["cols"] - 1 and 0 <= b[1] < g["rows"] - 1]

    def draw(self, token, slot):
        g = self.game
        assert token not in g["lines"], "the line %s again" % token
        g["lines"].add(token)
        took = [b for b in self.boxes_of(token) if self.sides(*b) == 4]
        for b in took:
            g["boxes"][b] = slot
        if len(g["lines"]) == len(self.all_lines()):
            g["over"] = True
        return bool(took)

    def choose(self):
        free = [t for t in self.all_lines() if t not in self.game["lines"]]
        take = [t for t in free if any(self.sides(*b) == 3 for b in self.boxes_of(t))]
        safe = [t for t in free if all(self.sides(*b) < 2 for b in self.boxes_of(t))]
        return (take or safe or free)[0]

    def play(self, token=None):
        """Draw a line on the Mac's turn (its dot, its direction), and PASS when it closed no box."""
        g = self.game
        assert g["mine"], "not the Mac's turn"
        token = token or self.choose()
        x, y, d = W.line_move(token)
        if (len(g["lines"]) + len(token)) % 2:          # the other end of the line, now and then
            x, y, d = (x + 1, y, 2) if d == 1 else (x, y + 1, 0)
        self.msg(W.PEG, W.peg(x, y))
        self.msg(W.LINE, W.line(d))
        if not self.draw(token, self.slot) and not g["over"]:
            self.msg(W.PASS, W.pass_turn())
            self.next_turn()
        return token

    def say(self, text):
        self.msg(W.CHAT, W.chat(text, self.name))


class Netboxes(Stack):
    def sin(self, n, tid, text):
        end = time.time() + WAIT
        while time.time() < end:
            if any(text in l for l in n.req("SIN %s 0" % tid)):
                return True
            time.sleep(0.2)
        return False

    def box(self, name, password):
        m = Box(self.port, name, password)
        self.addCleanup(m.close)
        return m

    def joined(self, name, pw):
        m = self.box(name, pw)
        m.read(lambda: m.said("Welcome, %s." % name))
        f = m.frames[0]
        self.assertEqual(f.what, W.JOIN_APPROVED)
        self.assertEqual(W.parse_join_approved(f.body), [(1, "ParlorNet"), (2, name)])
        self.assertEqual(f.body[20:24], bytes(4), "the players have NetBoxes' own player type, 0")
        self.assertEqual((m.slot, m.may_start), (0, False))
        self.assertEqual(m.players[1][0], "ParlorNet")
        return m

    def play_out(self, *macs):
        """Each Mac plays its turns until the game is over; the bot or a native player moves meanwhile."""
        for _ in range(500):
            if all(m.game["over"] for m in macs):
                return
            mover = next((m for m in macs if m.game["mine"] and not m.game["over"]), None)
            if mover is None:
                end = time.time() + WAIT
                while time.time() < end and not any(m.game["mine"] or m.game["over"] for m in macs):
                    for m in macs:
                        try:
                            m.read(lambda: m.game["mine"] or m.game["over"], timeout=0.2)
                        except AssertionError:
                            pass
                continue
            n = len(mover.game["lines"])
            mover.play()
            for m in macs:
                if m is not mover and not m.game["over"]:
                    m.read(lambda: len(m.game["lines"]) > n)
        self.fail("the game did not end")


class Wire(unittest.TestCase):
    """The formats, against the bytes of a game captured between two copies of NetBoxes."""

    def test_sizes_and_fields(self):
        captured = {   # what, the whole message as captured
            W.VERSION_ASK: "104000000000010100000002ffffffff000000020017a918000000201ed58c00",
            W.VERSION_REPLY: "10400000000001000000000100000002000000050017a7180000002401010001006e04d0",
            W.PEG: "104000000000080100000002000000000000000500190fb00000002005002094",
            W.LINE: "1040000000000802000000020000000000000006001913660000002003d53268",
            W.PASS: "1040000000000803000000020000000000000007001913670000002000d53268",
            W.ABORT: "10400000000008040000000200000000000000330020e7230000001c",
        }
        for what, hexed in captured.items():
            f, = W.Reader().feed(bytes.fromhex(hexed))
            self.assertEqual(f.what, what)
        f, = W.Reader().feed(bytes.fromhex(captured[W.VERSION_REPLY]))
        self.assertEqual(W.parse_version_reply(f.body), (1, 1, False, True, 110))
        self.assertEqual(len(W.version_reply(1, 1, may_start=True)), len(f.body))
        self.assertEqual(W.version_reply(1, 1, may_start=True)[:6], f.body[:6])
        f, = W.Reader().feed(bytes.fromhex(captured[W.PEG]))
        self.assertEqual(W.parse_peg(f.body), (5, 0))
        f, = W.Reader().feed(bytes.fromhex(captured[W.LINE]))
        self.assertEqual(W.line_token(5, 0, W.parse_line(f.body)), "f1-f2")
        self.assertEqual([len(W.pack(1, 1, 0, 1, 0, b)) for b in (W.hello("a", 1, 1), W.chat("hi", "a"),
                                                                    W.start(6, 4, 60, 0), W.peg(0, 0),
                                                                    W.line(1), W.pass_turn(), W.abort())],
                         [64, 320, 440, 32, 32, 32, 28])
        self.assertEqual(W.start(6, 4, 60, 0)[:4], bytes.fromhex("06043c00"))
        self.assertEqual(W.parse_start(W.start(21, 11, 999, 3)), (21, 11, 255, 3, False))
        self.assertEqual(W.parse_chat(W.chat("hello from host", "Hosty")), ("hello from host", "Hosty"))
        self.assertEqual(W.parse_hello(W.hello("bot", 2, 5)), ("bot", 2, 5))
        self.assertEqual(W.hello("bot", 2, 5)[0], 10, "short names fill the ten characters the game window shows")
        self.assertEqual(W.parse_points(W.points(-1, 1)), (-1, 1))

    def test_lines(self):
        self.assertEqual(W.line_token(1, 1, 2), "a2-b2")        # left from b2
        self.assertEqual(W.line_token(0, 1, 0), "a1-a2")        # up from a2
        self.assertEqual(W.line_token(3, 4, 1), "d5-e5")
        for tok in ("a1-b1", "c2-c3", "i9-i10", "e10-f10"):
            x, y, d = W.line_move(tok)
            self.assertEqual(W.line_token(x, y, d), tok)
        with self.assertRaises(W.WireError):
            W.line_move("a1-c1")

    def test_the_join_and_the_program(self):
        """The join request has no game ID; the first message after the approval tells the programs apart."""
        body = bytes.fromhex("054a6f696e790000000000000000dddddddddddd00000000000100010008000000000000000000"
                             "000000000000010000000000000000000000dddddd000000000000000000000000000000")
        self.assertEqual(W.parse_join(body), ("Joiny", "", 0))
        ask = W.Frame(W.VERSION_ASK, 2, -1, 1, 0, bytes(4))
        hello = W.Frame(1, 2, 0, 1, 0, bytes(40))
        self.assertTrue(B.recognises(ask))
        self.assertFalse(B.recognises(hello))

    def test_netsprockets_own_messages(self):
        """The join request and the host's replies, shared by every NetSprocket game (adapters/nspwire.py)."""
        body = N.join_request("anne", "secret")
        self.assertEqual(len(N.pack(N.JOIN_REQUEST, 0, 0, 1, 0, body)), 103)
        self.assertEqual(N.parse_join(body), ("anne", "secret", 0))
        stale = bytearray(body)
        stale[10:16] = b"\xdd" * 6          # the program leaves old bytes after the counted name
        self.assertEqual(N.parse_join(bytes(stale))[0], "anne")
        self.assertEqual(N.parse_join_approved(N.join_approved([(1, "a"), (2, "bob")], 5)), [(1, "a"), (2, "bob")])
        self.assertEqual(len(N.join_approved([(1, "a"), (2, "b")], 0)), 99)
        self.assertEqual(N.parse_player_joined(N.player_joined(2, 2, "bob")), (2, 2, "bob"))
        self.assertEqual(N.read_pstr(N.join_denied("No.")), "No.")
        frames = N.Reader().feed(N.pack(1, 1, 2, 3, 4, b"xy") + N.pack(2, -1, 0, 5, 6, b""))
        self.assertEqual([(f.what, f.frm, f.body) for f in frames], [(1, 1, b"xy"), (2, -1, b"")])


@slow
class Service(Netboxes):
    def test_a_wrong_password_said_in_the_chat_window(self):
        """NetBoxes never shows NetSprocket's join denial, so the reason comes in its chat window, and then the
        connection closes."""
        self.signup("nan")
        m = self.box("nan", "not-the-password")
        m.read(lambda: m.closed and m.chats, timeout=WAIT)
        self.assertEqual(m.frames[0].what, W.JOIN_APPROVED)
        self.assertTrue(m.said("Check your ParlorNet name and password"), m.chats)
        self.assertEqual(m.chats[0][0], "ParlorNet")
        self.assertNotIn(W.JOIN_DENIED, [f.what for f in m.frames])

    def test_commands_and_tables_made_elsewhere(self):
        """A table of the majority ruleset is refused (it ends before the board is full); a standard table
        made elsewhere is played at any size, here 3 by 3 boxes (4 by 4 dots, smaller than NetBoxes' own
        dialog allows), with the Mac moving second."""
        pw, pw2 = self.signup("ola"), self.signup("pim")
        m = self.joined("ola", pw)
        m.say("/help")
        m.read(lambda: m.said("every seat is taken"))
        self.assertTrue(m.said("/invite NAME"))
        m.say("hello")
        m.read(lambda: m.said("once you have a table"))
        m.say("/open 12x3")
        m.read(lambda: m.said("Can't open a table"))
        n = self.native("pim", pw2)
        tid = n.req("NEW dotsandboxes/1 majority")[-1].split()[1]
        self.assertTrue(n.req("JON %s 0" % tid)[-1].startswith("OK"))
        self.assertTrue(n.req("INV %s ola 1" % tid)[-1].startswith("OK"))
        m.say("/accept pim")
        m.read(lambda: m.said("NetBoxes cannot play table %s: its rules are majority." % tid))
        n.req("LVE %s" % tid)
        tid = n.req("NEW dotsandboxes/1 standard size=3x3")[-1].split()[1]
        self.assertTrue(n.req("JON %s 0" % tid)[-1].startswith("OK"))
        self.assertTrue(n.req("INV %s ola 1" % tid)[-1].startswith("OK"))
        m.say("/accept pim")
        m.read(lambda: m.game is not None)
        self.assertEqual((m.game["cols"], m.game["rows"]), (4, 4))
        self.assertFalse(m.game["mine"])
        self.assertEqual(m.players[1][0], "pim")
        for _ in range(3):
            v = n.req("VUE %s M" % tid)
            line = next(l.split()[1] for l in v if l.startswith("M "))
            self.assertTrue(n.req("MOV %s %s" % (tid, line))[-1].startswith("OK"))
            m.read(lambda: m.game["mine"])
            got = m.play()
            self.assertTrue(self.sin(n, tid, "LINE 1 %s" % got))

    def test_a_game_against_the_bot(self):
        """The Mac makes the table, so it moves first: NetBoxes gives the first turn only to the starter, so the
        game starts with the bot, who passes at once. The Mac plays its turns to the end, and every line and
        every PASS from the parlor is one the program can follow; the chat gives the server's result."""
        pw = self.signup("ray")
        m = self.joined("ray", pw)
        m.say("/invite bot 6x3")
        m.read(lambda: m.game and m.game["mine"])
        g = m.game
        self.assertEqual((g["cols"], g["rows"], g["secs"]), (7, 4, 255))
        self.assertEqual(g["order"], [0, 1])
        self.assertEqual(m.players[1][0], "bot")
        self.assertEqual(g["passes"], 1, "the opening pass")
        self.assertTrue(m.said("Your game with bot on table"))
        self.play_out(m)
        m.read(lambda: m.said("is over"))
        line = m.said("is over")[0]
        mine = sum(1 for s in g["boxes"].values() if s == m.slot)
        self.assertIn("Boxes: ray %d, bot %d." % (mine, 18 - mine), line)
        self.assertEqual(m.aborts, 0, "a game played to the end is not aborted")
        # another game in the same session: the window still up from the last is aborted first
        m.say("/invite bot")
        m.read(lambda: m.game is not g and m.game and m.game["mine"])
        self.assertEqual(m.aborts, 1)
        self.assertEqual((m.game["cols"], m.game["rows"]), (6, 6))

    def test_the_mac_leaves_its_game(self):
        """Abort Game on the Mac (or closing its game window) resigns the game on the server."""
        pw = self.signup("sid")
        m = self.joined("sid", pw)
        m.say("/invite bot")
        m.read(lambda: m.game and m.game["mine"])
        m.play()
        m.read(lambda: m.game["mine"])
        m.msg(W.ABORT, W.abort())
        m.read(lambda: m.said("counts as resigned"))
        m.read(lambda: m.said("sid resigned"))

    def test_who_sits_down_says_with_what(self):
        """The chat says what each player who sits down plays with (the CLIENT event after JOIN), and so does
        the line that seats a Mac at a table where others wait (LST clients)."""
        pw, pw2, pw3 = self.signup("wes"), self.signup("xia"), self.signup("yul")
        a = self.joined("wes", pw)
        a.say("/open 5x3 3")
        a.read(lambda: a.said("is open"))
        tid = a.said("is open")[0].split()[2]
        b = self.joined("xia", pw2)
        b.say("/join wes")
        b.read(lambda: b.said("You sat down at table %s with wes, who plays with NetBoxes" % tid))
        a.read(lambda: a.said("xia sits down with NetBoxes"))
        n = self.native("yul", pw3)
        n.req("CLI MACH Amiga 500")
        self.assertTrue(n.req("JON %s" % tid)[-1].startswith("OK 2"))
        a.read(lambda: a.said("yul sits down on an Amiga 500."))

    def test_two_macs_and_a_native_player(self):
        """Three seats: two Macs and a native player. Each Mac has slot 0 and sees the others in the table's
        order from its own seat, so the turns go round alike everywhere. The native player resigns during the
        game: the Macs skip that player (STEP_OUT) and play on to the end."""
        pw, pw2, pw3 = self.signup("tia"), self.signup("uma"), self.signup("vic")
        a = self.joined("tia", pw)
        a.say("/open 5x3 3")
        a.read(lambda: a.said("is open"))
        tid = a.said("is open")[0].split()[2]
        b = self.joined("uma", pw2)
        b.say("/join tia")
        b.read(lambda: b.said("You sat down at table %s" % tid))
        n = self.native("vic", pw3)
        self.assertTrue(n.req("JON %s" % tid)[-1].startswith("OK 2"))
        a.read(lambda: a.game and a.game["mine"])
        b.read(lambda: b.game is not None)
        self.assertEqual([a.players[s][0] for s in a.game["order"]], ["tia", "uma", "vic"])
        self.assertEqual([b.players[s][0] for s in b.game["order"]], ["uma", "vic", "tia"])
        self.assertFalse(b.game["mine"])
        b.say("good luck")
        a.read(lambda: ("uma", "good luck") in a.chats)
        tok = a.play()          # tia's line, then uma's
        b.read(lambda: tok in b.game["lines"] and b.game["mine"])
        tok = b.play()
        a.read(lambda: tok in a.game["lines"])
        self.assertFalse(a.game["mine"])
        self.assertTrue(n.req("RES %s" % tid)[-1].startswith("OK"))     # vic's turn: vic leaves the game
        a.read(lambda: a.game["out"] and a.game["mine"] and a.said("vic resigned and is out of the game"))
        b.read(lambda: b.game["out"])
        self.assertEqual((a.game["out"], b.game["out"]), ({2}, {1}))
        self.play_out(a, b)
        a.read(lambda: a.said("is over"))
        self.assertIn("vic 0.", a.said("is over")[0])
        self.assertEqual({(k, {0: "tia", 1: "uma"}[v]) for k, v in a.game["boxes"].items()},
                         {(k, {0: "uma", 2: "tia"}[v]) for k, v in b.game["boxes"].items()})

    def test_another_program_gets_netsprockets_join_denial(self):
        """A program the parlor does not know, joining with another player type, is refused with NetSprocket's
        own join denial, which carries the reason."""
        self.signup("ole")
        s = socket.create_connection(("127.0.0.1", self.port), timeout=WAIT)
        self.addCleanup(s.close)
        s.sendall(N.pack(N.JOIN_REQUEST, 0, 0, 1, 0, N.join_request("ole", "not-the-password", 1)))
        r, frames, end = N.Reader(), [], time.time() + WAIT
        while not frames and time.time() < end:
            frames += r.feed(s.recv(65536))
        self.assertEqual(frames[0].what, N.JOIN_DENIED)
        self.assertIn("Check your ParlorNet name and password", N.read_pstr(frames[0].body))


@slow
class Pending(Netboxes):
    """Sign-up by email: the Join dialog cannot ask for the new password a mailed one leads to, so the reason,
    to sign in on the web or by telnet first, goes in the chat window; the log says so too."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mails = os.path.join(cls.tmp.name, "mails")
        settings = os.path.join(cls.tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("email.transport=test\nemail.test_file=%s\n" % cls.mails)
        cls.core_extra = ("--signup", "email", "--settings-file", settings)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.tmp.cleanup()

    def test_a_mailed_password_is_refused_with_where_to_go(self):
        from parlord.auth import NEWPASS_TEXT
        pw = email_signup(self.core, "pia", "pia@example.org", self.mails)
        m = self.box("pia", pw)
        m.read(lambda: m.closed and m.chats, timeout=WAIT)
        self.assertTrue(m.said(NEWPASS_TEXT), m.chats)
        deadline = time.time() + 5
        while not any("refused: " + NEWPASS_TEXT in l for l in LOG) and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(any("refused: " + NEWPASS_TEXT in l for l in LOG), "the log says why")


if __name__ == "__main__":
    unittest.main()
