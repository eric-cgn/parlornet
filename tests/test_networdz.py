"""The Networdz extension (adapters/networdz.py): the parlor as the Networdz host, for Networdz 2.0 guests.

NwGuest below plays the part of a stock Networdz 2.0 copy in Answer mode, as the program was seen to behave
against a scripted host (docs/clients/networdz.md): it answers X with D and its name, takes E, F, A and R, and
when P names its seat it draws a tile for each empty slot at random among the places the last A holds, and
answers Q. It keeps every rack from the other seats' Q, J and O, and the board from J and its own plays. Its
player plays J, passes with K and discards with O after N; M is the host's verdict on its play, and a refused
play whose M lacks the bytes a stock host ends it with stops it, as it stops the program ("Internal Error A3").
It connects to the extension's port itself, as a copy with the dial-out patch will.

The fast tests are the wire's builders and parsers, the tile bookkeeping and the start of a game. The slow ones
play whole games through the core, the extension and the bot."""
import os
import random
import select
import socket
import sys
import threading
import time
import unittest

from test_adapters import spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
from test_gridwords import P, WORDS, best_play, write_words

sys.path.insert(0, ROOT)
from adapters import networdz as A  # noqa: E402
from adapters.games import gridwords as G  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 30
LOG = []                    # the extension's log, for a failing test's message
CORE_WORDS = {w.upper() for w in WORDS.split()}

# Messages captured from Networdz 2.0 (Hank hosting; Annie and Bert answering), zeros cut off.
CAPTURED = {
    "X": "58014301",
    "D": "4401416e6e6965",
    "E": "45040100000000000000000000000200",
    "A": "41042d45515c5e554e0d1e262a1115120010594b62250b4901174a02395643462458483c1850534c05091b130f4f0a04381c"
         "0622471a3523075d3a343f3b273d411f19545b44162952614d3e0c2f322e3103570e630830405a2b1d21603714202c422833"
         "5f36",
    "R": "52040000010002",
    "Q": "5100fafafafafafafa0000004f59444061013c",
    "J": "4a00070803001515210007071600151519000706330007054500151527",
    "O": "4f025a483a2f4b1f26000000000001000000000000000000000001",
    "pass": "4f015d6202373c634f",
    "B": "4202626572742073617973206869",
    # the host's verdicts on a remote seat's plays: refused, down and across, and taken
    "M7down": "4d010701004c4952490000e0156d011916e0",
    "M7across": "4d010700014a4f4e455300e0156d011916e0",
}


def captured(kind):
    raw = CAPTURED[kind]
    m = bytes.fromhex(raw + ("0" if len(raw) % 2 else ""))
    t = {"pass": "O", "M7down": "M", "M7across": "M"}.get(kind, kind).encode()
    return m.ljust(A.SIZES[t], b"\0")


class Wire(unittest.TestCase):
    def test_reader_and_captured_messages(self):
        r = A.Reader()
        stream = b"".join(captured(k) for k in ("X", "D", "A", "R", "Q", "J", "O", "B"))
        got = []
        for i in range(0, len(stream), 37):         # in pieces, as TCP brings them
            got += r.feed(stream[i:i + 37])
        self.assertEqual([k for k, _ in got], list("XDARQJOB"))
        self.assertEqual(r.buf, b"")
        self.assertEqual(r.feed(b"Wjunk"), [("junk", b"Wjunk")])
        self.assertEqual(A.u16s(captured("R")[2:], 4), [0, 1, 2, 0])
        self.assertEqual(A.parse_discards(captured("O")), [0, 6])
        self.assertEqual(A.parse_discards(captured("pass")), [])
        before, draws = A.parse_draw(captured("Q"))
        self.assertEqual((before, draws), ([A.EMPTY] * 7, [0x4f, 0x59, 0x44, 0x40, 0x61, 0x01, 0x3c]))

    def test_builders_match_the_program(self):
        self.assertEqual(A.msg_x(1), captured("X"))
        self.assertEqual(A.msg_b(2, "bert says hi"), captured("B"))
        self.assertEqual(A.msg_r([0, 1, 2]), captured("R"))
        self.assertEqual(A.msg_q(0, [A.EMPTY] * 7, [0x4f, 0x59, 0x44, 0x40, 0x61, 0x01, 0x3c]), captured("Q"))
        j = captured("J")
        self.assertEqual(A.msg_j(0, A.parse_play(j)[:7]), j)
        o = captured("O")
        self.assertEqual(A.msg_o(2, list(o[2:9]), [0, 6]), o)
        self.assertEqual(A.msg_a(list(captured("A")[2:])), captured("A"))
        self.assertEqual(A.msg_k(3), b"K\x03")
        self.assertEqual(A.msg_end("S"), b"S\x04")
        # the verdicts a stock host sends, and the seven bytes they end with
        self.assertEqual(A.msg_m(1, A.NOT_LISTED, False, "LIRI"), captured("M7down"))
        self.assertEqual(A.msg_m(1, A.NOT_LISTED, True, "JONES"), captured("M7across"))
        long = A.msg_m(1, A.NOT_LISTED, True, "VIRION")
        self.assertEqual((long[5:11], long[-7:]), (b"VIRIO\0", A.TAIL))
        self.assertEqual(len(long), A.SIZES[b"M"])
        # the rules, every one off, and the players
        e = A.msg_e()
        self.assertEqual((len(e), A.u16s(e[2:], 7)), (16, [0, 0, 0, 0, 0, 0, 2]))
        f = A.msg_f([(A.PERSON, "ann", 0), (A.REMOTE, "bert", 1), (A.COMPUTER, "bot", 2), (A.NONE, "ParlorNet", 3)])
        self.assertEqual(len(f), A.SIZES[b"F"])
        recs = [f[2 + 84 * i:86 + 84 * i] for i in range(4)]
        self.assertEqual([A.u16s(r, 2) for r in recs], [[2, 100], [3, 100], [1, 100], [0, 100]])
        self.assertEqual([A.cstr(r[4:25]) for r in recs], ["ann", "bert", "bot", "ParlorNet"])
        self.assertEqual(A.u16s(recs[1][74:], 2), [1, 1])         # connected, its seat
        self.assertEqual(A.u16s(recs[0][74:], 2), [0, 0])

    def test_tiles_and_words(self):
        """The ParlorNet configuration's tile set; a play as PLAY tokens; the core's refusals as M's code."""
        self.assertEqual(A.TILESET[:3] + A.TILESET[-3:], "__AYYZ")
        self.assertEqual(len(A.TILESET), 100)
        self.assertEqual(A.letter(A.EMPTY), "")
        self.assertEqual(A.play_move([(6, 8, "P", False), (7, 8, "L", False), (8, 8, "E", False),
                                      (9, 8, "A", False)], {}), ["PLAY", "6", "8", "H", "PLEA"])
        tiles = {(8, 8): "C", (9, 8): "A"}
        self.assertEqual(A.play_move([(10, 8, "T", True)], tiles), ["PLAY", "8", "8", "H", "CA_T"])
        self.assertEqual(A.play_move([(8, 9, "A", False), (8, 10, "T", False)], tiles),
                         ["PLAY", "8", "8", "V", "CAT"])
        self.assertIsNone(A.play_move([(1, 1, "A", False), (2, 2, "B", False)], tiles))
        self.assertIsNone(A.play_move([(1, 1, "A", False), (3, 1, "B", False)], tiles))     # a gap
        self.assertEqual(A.refusal("not in the word list: QXZ ZZ"), (A.NOT_LISTED, "QXZ"))
        # the program's own messages for the other reasons (each seen in the stock program), 7 for the rest
        self.assertEqual(A.refusal("the word must join the words already on the board"), (A.NOT_CONNECTED, ""))
        self.assertEqual(A.refusal("the first word must cover the center square h8"), (A.NOT_CENTER, ""))
        self.assertEqual(A.refusal("a play needs at least one tile from your rack"), (A.NO_TILES, ""))
        self.assertEqual(A.refusal("the first word needs two letters or more", "A"), (A.TOO_SHORT, "A"))
        self.assertEqual(A.refusal("your rack has no Q", "QI"), (A.NOT_LISTED, ""))
        self.assertEqual((A.minus("AABC", "AC"), A.common("AABC", "CAX")), ("AB", "AC"))
        v = A.parse_open_view(["X 8 8 C", "R AB", "RK 0 AB", "RK 1 -", "BO QZ", "BO E", "W 1"])
        self.assertEqual((v["racks"], v["bagtiles"], v["turn"]), ({0: "AB", 1: ""}, "QZE", 1))


class Tiles(unittest.TestCase):
    def game(self, seed=1):
        st = P.op_new({"players": 3, "options": {}, "seed": seed, "rules": "classic"})["state"]
        racks = {s: r for s, r in enumerate(st["racks"])}
        return st, A.Tiles(racks, st["bag"], random.Random(seed))

    def test_every_tile_starts_in_the_bag_and_the_deal_is_the_tables(self):
        st, t = self.game()
        self.assertEqual(sorted(t.bag_letters()), sorted(A.TILESET))
        self.assertEqual(sorted(n for n in t.full_bag()), list(range(100)))
        # another seat's draw: Q with the places of the tiles the table dealt it
        places = t.places_for(st["racks"][1])
        before, draws = t.draw(1, places)
        self.assertEqual((before, sorted(draws)), ([A.EMPTY] * 7, sorted(places)))
        self.assertEqual(sorted(t.rack_letters(1)), sorted(st["racks"][1]))
        # the guest's draw: A holds only its tiles, so its random pick takes them all
        places = t.places_for(st["racks"][0])
        short = t.short_bag(places)
        self.assertEqual(sorted(A.letter(n) for n in short if n != A.EMPTY), sorted(st["racks"][0]))
        draws = [A.EMPTY] * 7
        for slot, p in zip(random.Random(2).sample(range(7), 7), places):
            draws[slot] = p
        self.assertEqual(sorted(t.took(0, [A.EMPTY] * 7, draws)), sorted(places))
        self.assertEqual(sorted(t.rack_letters(0)), sorted(st["racks"][0]))
        self.assertEqual(len(t.bag_letters()), 86)
        # a draw from a place that is empty, or a rack that is not Networdz's, is out of step
        with self.assertRaises(A.Desync):
            t.took(2, [A.EMPTY] * 7, [places[0]] + [A.EMPTY] * 6)
        with self.assertRaises(A.Desync):
            t.took(0, [A.EMPTY] * 7, [A.EMPTY] * 7)
        with self.assertRaises(A.Desync):
            A.Tiles({0: "QQ"}, "")

    def test_plays_and_exchanges(self):
        st, t = self.game()
        rack = "CATSXYZ"
        t = A.Tiles({0: rack, 1: st["racks"][1], 2: st["racks"][2]},
                    A.minus(A.TILESET, rack + st["racks"][1] + st["racks"][2]), random.Random(3))
        for s, r in ((0, rack), (1, st["racks"][1]), (2, st["racks"][2])):
            t.draw(s, t.places_for(r))
        recs = t.play(0, [(8, 8, "C"), (9, 8, "A"), (10, 8, "T")])
        self.assertEqual(sorted((r[0], r[1]) for r in recs if r[0] != A.OFF), [(7, 7), (7, 8), (7, 9)])
        self.assertEqual(len(recs), 7)
        self.assertEqual(t.tiles(), {(8, 8): "C", (9, 8): "A", (10, 8): "T"})
        self.assertEqual(sorted(t.rack_letters(0)), sorted("SXYZ"))
        with self.assertRaises(A.Desync):
            t.play(0, [(1, 1, "Q")])
        # an exchange: the tiles put back are out until the draw is over, then in the bag again
        left = len(t.bag_letters())
        slots = t.put_back(0, "XZ")
        self.assertEqual(len(slots), 2)
        self.assertEqual((t.rack_letters(0).count("X"), len(t.bag_letters())), (0, left))
        t.back_in_bag()
        self.assertEqual(len(t.bag_letters()), left + 2)
        # the guest's own play is checked against its rack
        n = t.racks[1][0]
        placed = t.guest_play(1, [(7, 10, n, "")] + [(A.OFF, A.OFF, t.racks[1][i], "") for i in range(1, 7)])
        self.assertEqual(placed, [(11, 8, A.letter(n), False, 0)])
        with self.assertRaises(A.Desync):
            t.guest_play(1, [(7, 10, n + 1 if n + 1 != t.racks[1][0] else n + 2, "")])

    def test_a_play_that_runs_into_a_blank(self):
        """A blank on the board keeps the letter it stands for: the guest's tiles that run into it or past it
        make one word with it, and the table takes that word."""
        rack = "LTDIFNO"
        t = A.Tiles({0: rack, 1: ""}, A.minus(A.TILESET, rack), random.Random(4))
        t.draw(0, t.places_for(rack))
        # the board of a game where the bot's JOY had a blank for its Y
        board = [(8, 8, "J"), (9, 8, "O"), (10, 8, "y"), (8, 9, "A"), (8, 10, "R"), (9, 10, "I"), (10, 10, "P"),
                 (12, 10, "B"), (13, 10, "A"), (14, 10, "D"), (10, 11, "I"), (11, 11, "C"), (12, 11, "E"),
                 (9, 12, "A"), (10, 12, "T"), (12, 12, "D"), (13, 12, "E"), (14, 12, "N"), (9, 13, "W"),
                 (9, 14, "E")]
        for i in range(0, len(board), A.RACK):
            part = board[i:i + A.RACK]
            t.draw(1, t.places_for("".join("_" if c.islower() else c for _, _, c in part)))
            t.play(1, part)
        self.assertEqual(t.tiles(), {(x, y): c for x, y, c in board})

        def j(*squares):        # the guest's J: these rack letters on these squares, the rest off the board
            recs, used = [(A.OFF, A.OFF, n, "") for n in t.racks[0]], set()
            for x, y, c in squares:
                slot = next(s for s in range(A.RACK) if s not in used and A.letter(t.racks[0][s]) == c)
                used.add(slot)
                recs[slot] = (y - 1, x - 1, t.racks[0][slot], "")
            return [p[:4] for p in t.guest_play(0, recs)]
        # F and L above the blank: FLY down, as the table's best play had it
        tokens = A.play_move(j((10, 6, "F"), (10, 7, "L")), t.tiles())
        self.assertEqual(tokens, ["PLAY", "10", "6", "V", "FLY"])
        cells = ["."] * 225
        for x, y, c in board:
            cells[(y - 1) * 15 + x - 1] = c
        st = {"cells": "".join(cells), "racks": [rack]}
        self.assertEqual(P.evaluate(st, 0, 10, 6, "V", "FLY", None)["placed"], [(10, 6, "F"), (10, 7, "L")])
        # a tile past the blank: the word runs through it
        self.assertEqual(A.play_move(j((11, 8, "T")), t.tiles()), ["PLAY", "8", "8", "H", "JOYT"])
        # the guest's own blank, once the table has taken the play
        t2 = A.Tiles({0: "_AT", 1: ""}, A.minus(A.TILESET, "_AT"), random.Random(5))
        t2.draw(0, t2.places_for("_AT"))
        placed = t2.guest_play(0, [(7, 7, t2.racks[0][0], "C"), (7, 8, t2.racks[0][1], ""),
                                   (7, 9, t2.racks[0][2], "")])
        self.assertEqual(A.play_move([p[:4] for p in placed], t2.tiles()), ["PLAY", "8", "8", "H", "_CAT"])
        t2.played(0, placed)
        self.assertEqual(t2.tiles(), {(8, 8): "c", (9, 8): "A", (10, 8): "T"})

    def test_the_racks_at_the_start(self):
        """A seat that played before Networdz heard of the start: its first rack held what it placed."""
        now = {0: "ABCDEFG", 1: "HIJKLMN"}
        backlog = [("TURN", ["0"], None, {}),
                   ("PLAY", ["0", "8", "8", "H", "QI", "22"], ["8", "8", "Q", "9", "8", "i"], {}),
                   ("X", ["8", "8", "Q", "9", "8", "i"], None, {}), ("DRAW", ["0", "2"], None, {}),
                   ("TURN", ["1"], None, {})]
        self.assertEqual(A.start_racks(now, backlog, 1), {0: "ABCDEQ_", 1: "HIJKLMN"})
        self.assertEqual(A.start_racks(now, [], 1), now)


class Events(unittest.IsolatedAsyncioTestCase):
    async def test_a_play_waits_for_its_tiles(self):
        """A PLAY event whose X event has not come yet (the reply ended between them) is read again with it."""
        g = A.Guest.__new__(A.Guest)
        g.tid, g.cursor, g.started, g.backlog = "5", 3, True, __import__("collections").deque()
        replies = [["EVT 5 4 TURN 1", "EVT 5 5 PLAY 1 8 8 H AT 2"],
                   ["EVT 5 5 PLAY 1 8 8 H AT 2", "EVT 5 6 X 8 8 A 9 8 T", "EVT 5 7 TURN 0"]]

        async def req(text):
            self.assertEqual(text, "SIN 5 %d" % g.cursor)
            return replies.pop(0), "OK"
        g.req = req
        await g.table_events()
        self.assertEqual((g.cursor, [it[0] for it in g.backlog]), (4, ["TURN"]))
        await g.table_events()
        self.assertEqual((g.cursor, [it[0] for it in g.backlog]), (7, ["TURN", "PLAY", "X", "TURN"]))
        self.assertEqual(g.backlog[1][2], ["8", "8", "A", "9", "8", "T"])


# ---- a stock Networdz copy answering, connected to the parlor ----------------------------------------------

class NwGuest:
    """Networdz 2.0 in Answer mode (the module's docstring), connected to the extension."""

    def __init__(self, port, name):
        self.name = name
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.reader = A.Reader()
        self.seat = None
        self.players = []
        self.chats = []             # (seat, text), as the chat window shows them
        self.got = []               # every message, in order: (kind, bytes)
        self.bag = []
        self.racks = {}
        self.board = {}             # (x, y) -> letter (lower case: a blank)
        self.turn = None
        self.new_turn = None
        self.over = None            # "S", "Y" or "Z" once the game is over
        self.verdicts = []          # (code, word) on my plays
        self.pending = None         # my play, until its verdict
        self.rules = None
        self.order = []
        self.crashed = None
        self.asked_discards = False
        self.draws = 0
        self.wait(lambda: self.seat is not None, "X")

    def close(self):
        self.sock.close()

    # ---- the wire
    def send(self, msg):
        self.sock.sendall(msg)

    def pump(self, timeout=0.3):
        ready, _, _ = select.select([self.sock], [], [], timeout)
        if not ready:
            return
        data = self.sock.recv(65536)
        if not data:
            raise AssertionError("the parlor closed the connection; log %r" % LOG[-15:])
        for kind, m in self.reader.feed(data):
            assert kind != "junk", "Networdz cannot read %r" % m[:16]
            assert not self.crashed, "a message after Networdz stopped: %r" % m
            self.got.append((kind, m))
            getattr(self, "on_" + kind, lambda m: None)(m)

    def wait(self, until, what, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            if time.time() > end:
                if os.environ.get("NETWORDZ_TRACE"):
                    with open(os.environ["NETWORDZ_TRACE"], "w") as f:
                        f.write("\n".join(LOG))
                raise AssertionError("waited for %s; chats %r; log %r" % (what, self.chats[-6:], LOG[-20:]))
            self.pump()

    def say(self, text):
        n = len(self.chats)
        self.send(A.msg_b(self.seat, text))
        return n

    def said(self, text, since=0):
        return [t for _, t in self.chats[since:] if text in t]

    def wait_chat(self, text, since=0):
        return self.wait(lambda: self.said(text, since), repr(text))

    # ---- what the host sends
    def on_X(self, m):
        assert m[2] == ord("C") and m[1] == m[3]
        assert m[1] != self.seat, "X for the seat Networdz has: it drops what comes with it"
        self.seat = m[1]
        self.send(b"D" + bytes([self.seat]) + A.text_field(self.name, 21))

    def on_E(self, m):
        self.rules = A.u16s(m[2:], 7)
        self.board, self.racks, self.over, self.turn = {}, {}, None, None

    def on_F(self, m):
        self.players = [(A.u16s(m[2 + 84 * i:], 1)[0], A.cstr(m[6 + 84 * i:27 + 84 * i])) for i in range(4)]

    def on_B(self, m):
        seat = m[1]
        name = self.players[seat][1] if seat < len(self.players) else ""
        self.chats.append((seat, "%s: %s" % (name, A.cstr(m[2:]))))

    def on_A(self, m):
        self.bag = list(m[2:2 + A.BAG])

    def on_R(self, m):
        self.order = A.u16s(m[2:], 4)

    def on_P(self, m):
        if m[1] != self.seat:
            return
        rack = self.racks.setdefault(self.seat, [A.EMPTY] * 7)
        before = list(rack)
        draws = [A.EMPTY] * 7
        for slot in range(7):                   # at random among the places the bag holds, as the program does
            places = [p for p in range(100) if self.bag[p] != A.EMPTY and p not in draws]
            if rack[slot] == A.EMPTY and places:
                draws[slot] = random.choice(places)
        for slot, p in enumerate(draws):
            if p != A.EMPTY:
                rack[slot], self.bag[p] = self.bag[p], A.EMPTY
        self.draws += 1
        self.send(A.msg_q(self.seat, before, draws))

    def on_Q(self, m):
        rack = self.racks.setdefault(m[1], [A.EMPTY] * 7)
        before, draws = A.parse_draw(m)
        assert before == rack, "seat %d's rack before its draw: %r, Networdz has %r" % (m[1], before, rack)
        for slot, p in enumerate(draws):
            if p != A.EMPTY:
                assert rack[slot] == A.EMPTY and self.bag[p] != A.EMPTY, "a draw into a full slot or from no tile"
                rack[slot], self.bag[p] = self.bag[p], A.EMPTY

    def on_I(self, m):
        self.turn, self.new_turn = m[1], m[2]

    def on_J(self, m):
        rack = self.racks[m[1]]
        for slot, (row, col, tile, bl) in enumerate(A.parse_play(m)[:7]):
            assert tile == rack[slot] or row == A.OFF, "seat %d played a tile it lacks" % m[1]
            if row != A.OFF:
                c = A.letter(tile)
                self.board[(col + 1, row + 1)] = bl.lower() if c == "_" else c
                rack[slot] = A.EMPTY

    def on_M(self, m):
        if m[1] != self.seat:
            return
        code, word = m[2], A.cstr(m[5:11])
        if code != A.TAKEN and m[11:] != A.TAIL:
            self.crashed = "Internal Error A3"
            return
        self.verdicts.append((code, word))
        if code == A.TAKEN and self.pending:
            for slot, (x, y, c) in self.pending.items():
                self.board[(x, y)] = c
                self.racks[self.seat][slot] = A.EMPTY
        self.pending = None

    def on_O(self, m):
        rack = self.racks[m[1]]
        for slot in A.parse_discards(m):
            rack[slot] = A.EMPTY

    def on_N(self, m):
        if m[1] == self.seat:
            self.asked_discards = True

    def on_S(self, m):
        self.over = "S"

    def on_Y(self, m):
        self.over = "Y"

    def on_Z(self, m):
        self.over = "Z"

    # ---- my player
    def letters(self, seat=None):
        return "".join(A.letter(n) for n in self.racks.get(self.seat if seat is None else seat, []) if n != A.EMPTY)

    def my_turn(self):
        return self.turn == self.seat and self.over is None

    def play(self, tokens):
        """PLAY x y d word (the table's form) -> J with the tiles from my rack."""
        x, y, d, word = int(tokens[1]), int(tokens[2]), tokens[3], tokens[4]
        dx, dy = (1, 0) if d == "H" else (0, 1)
        rack = self.racks[self.seat]
        recs = [(A.OFF, A.OFF, n, "") for n in rack]
        used, i, blank = {}, 0, False
        for ch in word:
            if ch == "_":
                blank = True
                continue
            cx, cy = x + dx * i, y + dy * i
            i += 1
            if (cx, cy) in self.board:
                blank = False
                continue
            want = "_" if blank else ch
            slot = next(s for s in range(7) if s not in used and rack[s] != A.EMPTY and A.letter(rack[s]) == want)
            used[slot] = (cx, cy, ch.lower() if blank else ch)
            recs[slot] = (cy - 1, cx - 1, rack[slot], ch if blank else "")
            blank = False
        self.pending = used
        self.turn = None                # until the next I
        self.send(A.msg_j(self.seat, recs + [(0, 0, 0, "")]))

    def pass_turn(self, put_back=()):
        """K; then, when the host asks (N), O with these slots put back (none: a pass)."""
        self.asked_discards = False
        self.turn = None                # until the next I
        self.send(A.msg_k(self.seat))
        end = time.time() + 3
        while time.time() < end and not self.asked_discards and self.turn is None:
            self.pump(0.2)
        if self.asked_discards:
            self.send(A.msg_o(self.seat, self.racks[self.seat], list(put_back)))
            for s in put_back:
                self.racks[self.seat][s] = A.EMPTY
        return self.asked_discards


class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.words_file = write_words()
        os.environ[P.WORDS_ENV] = cls.words_file
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("networdz", "bot")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"),
                     spawn(*adapter_args("adapters.networdz", cls.port, cls.fe, cls.keys, "networdz"),
                           "--poll", "0.2", "--chat-gap", "0", "--turn", "10m",
                           *(["--trace"] if os.environ.get("NETWORDZ_TRACE") else []))]
        cls.procs.append(spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", cls.keys + "/bot.key",
                               "--think", "0,0", "--pace", "0", "--poll", "0.3"))
        del LOG[:]
        for i, p in enumerate(cls.procs):
            for f in (p.stdout, p.stderr):
                keep = LOG if (i == 1 and f is p.stderr) else None
                threading.Thread(target=lambda f=f, keep=keep: [keep.append(l.decode(errors="replace").rstrip())
                                                                if keep is not None else None
                                                                for l in iter(f.readline, b"")],
                                 daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
        os.environ.pop(P.WORDS_ENV, None)
        os.remove(cls.words_file)

    def signup(self, name):
        from test_fibs import Stack as FibsStack
        return FibsStack.password_for(self, name)

    def native(self, name, password):
        from test_fibs import Native
        n = Native(self.core, name, password)
        self.addCleanup(n.close)
        return n

    def guest(self, name):
        g = NwGuest(self.port, name)
        self.addCleanup(g.close)
        return g

    def signed_in(self, name):
        pw = self.signup(name)
        g = self.guest(name)
        g.wait_chat("To sign in, type /signin NAME PASSWORD")
        n = g.say("/signin %s" % pw)                # the name Networdz sent in D
        g.wait_chat("Signed in as %s." % name, n)
        return g, pw

    def agree(self, nat, tid, g):
        """The table and Networdz: the same board, my rack, the bag's size and whose turn it is."""
        v = G.parse_view(nat.req("VUE %s" % tid))
        board = {xy: c for xy, c in g.board.items()}
        return (v["tiles"] == board and sorted(v["rack"]) == sorted(g.letters())
                and v["bag"] == sum(1 for n in g.bag[:100] if n != A.EMPTY) and v["turn"] == g.turn)

    def my_move(self, nat, tid, g, words=CORE_WORDS):
        """My turn: the best play from the test list, or a pass. The table found the play legal, so the parlor
        must take it as Networdz sends it."""
        g.wait(lambda: self.agree(nat, tid, g), "the table to agree with Networdz")
        found = best_play(nat.req("VUE %s" % tid), words)
        if found:
            board, rack, n = sorted(g.board.items()), g.letters(), len(g.chats)
            g.play(found[1])
            g.wait(lambda: g.pending is None, "the verdict")
            if g.verdicts[-1][0] != A.TAKEN:
                g.wait(lambda: len(g.chats) > n, "the reason in the chat")
            self.assertEqual(g.verdicts[-1][0], A.TAKEN, "%s refused; board %s; rack %s; chats %r" % (
                " ".join(found[1]), " ".join("%d,%d %s" % (x, y, c) for (x, y), c in board), rack, g.chats[n:]))
            return "play"
        g.pass_turn()
        return "pass"

    def table_of(self, nat, name):
        return next(l.split()[1] for l in nat.req("LST") if l.startswith("TAB ") and name in l.split()[6].split(","))


@slow
class Service(Stack):
    def test_hello_sign_in_and_commands(self):
        g = self.guest("ann")
        self.assertEqual(g.seat, A.LOBBY_SEAT)
        g.wait(lambda: g.players[:2] == [(A.PERSON, "ParlorNet"), (A.REMOTE, "ann")], "the players, named")
        g.wait_chat("To sign in, type /signin NAME PASSWORD")
        self.assertTrue(all(c[1].startswith("ParlorNet: ") for c in g.chats))       # the parlor's own seat
        n = g.say("hello")
        g.wait_chat("Sign in first", n)
        n = g.say("/signin ann wrong")
        g.wait_chat("There is no account ann here", n)
        pw = self.signup("ann")
        n = g.say("/signin ann %s" % pw)
        g.wait_chat("Signed in as ann.", n)
        n = g.say("/top")
        g.wait_chat("meant for", n)
        self.assertTrue(all(len(t) <= len("ParlorNet: ") + A.TEXT_LEN - 1 for _, t in g.chats))
        for cmd, answer in (("/help", "/invite NAME"), ("/time", "Server time"), ("/top", "meant for"),
                            ("/score", "ann:"), ("/tables ann", "ann sits at no table."), ("/list", "No open tables"),
                            ("/open 5", "/open 3 or /open 4")):
            n = g.say(cmd)
            g.wait_chat(answer, n)

    def test_a_whole_game_with_the_bot(self):
        """One guest and the bot to the end: Networdz and the table agree at each of the guest's turns, a word
        the list lacks is refused (M 7 with the word, then the turn again), an exchange and a pass, chat both
        ways, and at the end the same board and racks on both sides, so the same scores."""
        g, pw = self.signed_in("bea")
        n = g.say("/invite bot")
        g.wait_chat("You invited the bot", n)
        nat = self.native("bea", pw)
        tid = g.wait(lambda: self.table_of(nat, "bea"), "bea's table")
        lst = [l for l in nat.req("LST") if l.startswith("TAB %s " % tid)]
        self.assertTrue(lst[0].endswith(" integrity=false"), lst)
        self.assertIn("INT false " + A.REASON, nat.req("VUE %s" % tid))
        g.wait(lambda: g.rules is not None, "New Game")
        g.wait_chat("Integrity: %s." % A.REASON)
        self.assertEqual(g.rules, [0, 0, 0, 0, 0, 0, 2])
        self.assertEqual(g.players[:3], [(A.REMOTE, "bea"), (A.COMPUTER, "bot"), (A.NONE, "ParlorNet")])
        self.assertEqual((g.seat, g.order[:2]), (0, [0, 1]))
        g.wait_chat("bot: Hello!")          # the bot's greeting, under its seat's name in Networdz
        g.say("hello from Networdz")
        g.wait(lambda: any("hello from Networdz" in l for l in nat.req("SIN %s 0" % tid)), "the chat at the table")
        refused = exchanged = passed = False
        for turn in range(80):
            g.wait(lambda: g.my_turn() or g.over, "my turn or the end")
            if g.over:
                break
            if not refused:             # a word the list lacks: refused, and the turn comes back
                g.wait(lambda: self.agree(nat, tid, g), "the table to agree with Networdz")
                bad = self.bad_play(nat, tid, g)
                if bad:
                    g.play(bad)
                    g.wait(lambda: g.verdicts and g.pending is None, "the verdict")
                    self.assertEqual(g.verdicts[-1][0], A.NOT_LISTED)
                    g.wait(lambda: g.my_turn() and g.new_turn == 0, "the same turn again")
                    g.wait_chat("Not in the word list")
                    refused = True
            if not exchanged and len(g.bag) and sum(1 for n in g.bag[:100] if n != A.EMPTY) >= 7:
                self.assertTrue(g.pass_turn(put_back=[0, 1]))
                exchanged = True
                continue
            if exchanged and not passed:
                g.pass_turn()
                passed = True
                continue
            self.my_move(nat, tid, g)
        g.wait(lambda: g.over, "the end")
        self.assertEqual(g.over, "S")
        self.assertTrue(refused and exchanged and passed)
        self.assertIsNone(g.crashed)
        log = nat.req("SIN %s 0" % tid)
        end = [l for l in log if " END " in l]
        self.assertTrue(end, log[-5:])
        adj = {int(l.split()[4]): l.split()[6] for l in log if " ADJ " in l}
        for s, left in adj.items():         # the racks at the end, so the points each loses or gains
            self.assertEqual(sorted(g.letters(s)), sorted("" if left == "-" else left), (s, g.letters(s), left))
        v = G.parse_view(nat.req("VUE %s" % tid))
        self.assertEqual(v["tiles"], g.board)
        self.assertTrue(any(" EXCH 0 2" in l for l in log))
        turns = nat.req("TRN %s" % tid)
        self.assertTrue(any("TRX PROG Networdz 2.0" in l for l in turns), turns)

    def bad_play(self, nat, tid, g):
        """A play my rack can make that the list lacks: two of my tiles beside the board's tiles."""
        v = G.parse_view(nat.req("VUE %s" % tid))
        rack = [c for c in v["rack"] if c != "_"]
        if len(rack) < 2:
            return None
        if not v["tiles"]:
            word = "".join(rack[:2])
            return None if word in CORE_WORDS else ["PLAY", "8", "8", "H", word]
        for (x, y) in sorted(v["tiles"]):
            for tokens in (["PLAY", str(x), str(y + 1), "V"], ["PLAY", str(x + 1), str(y), "H"]):
                word = "".join(rack[:2])
                cx, cy = int(tokens[1]), int(tokens[2])
                if tokens[3] == "V" and all((cx, cy + i) not in v["tiles"] and cy + i <= 15 for i in range(3)) \
                        and (cx, cy - 1) in v["tiles"]:
                    tokens = ["PLAY", str(x), str(y), "V", v["tiles"][(x, y)].upper() + word]
                elif tokens[3] == "H" and all((cx + i, cy) not in v["tiles"] and cx + i <= 15 for i in range(3)) \
                        and (cx - 1, cy) in v["tiles"]:
                    tokens = ["PLAY", str(x), str(y), "H", v["tiles"][(x, y)].upper() + word]
                else:
                    continue
                chk = nat.req("CHK %s %s" % (tid, " ".join(tokens)))[-1]
                if "not in the word list" in chk:
                    return tokens
        return None

    def test_two_guests_and_a_native_player(self):
        """Three players: two Networdz guests and a player on the native protocol (as the web pages play). Each
        guest's Networdz shows the other as Remote and the native player as a Person, and both keep up with the
        table. Then one guest quits: the table goes on, and that seat is a player who is away."""
        g1, pw1 = self.signed_in("cy")
        g2, pw2 = self.signed_in("dee")
        pw3 = self.signup("eli")
        n = g1.say("/open 3")
        g1.wait_chat("open for 3 players", n)
        nat1 = self.native("cy", pw1)
        tid = self.table_of(nat1, "cy")
        n = g2.say("/join %s" % tid)
        g2.wait_chat("You sat down at table %s" % tid, n)
        eli = self.native("eli", pw3)
        self.assertTrue(eli.req("JON %s" % tid)[-1].startswith("OK 2"))
        for g in (g1, g2):
            g.wait(lambda g=g: g.rules is not None, "New Game")
        self.assertEqual(g1.players[:4], [(A.REMOTE, "cy"), (A.REMOTE, "dee"), (A.PERSON, "eli"),
                                          (A.NONE, "ParlorNet")])
        self.assertEqual((g1.seat, g2.seat), (0, 1))
        nat2 = self.native("dee", pw2)
        moves = 0
        for turn in range(12):
            v = G.parse_view(eli.req("VUE %s" % tid))
            if v["turn"] < 0 or moves >= 4:
                break
            if v["turn"] == 2:
                found = best_play(eli.req("VUE %s" % tid), CORE_WORDS)
                eli.req("MOV %s %s" % (tid, " ".join(found[1]) if found else "PASS"))
                continue
            g, nat = (g1, nat1) if v["turn"] == 0 else (g2, nat2)
            g.wait(g.my_turn, "seat %d's turn in Networdz" % g.seat)
            self.my_move(nat, tid, g)
            moves += 1
            g.wait(lambda g=g: G.parse_view(eli.req("VUE %s" % tid))["turn"] != g.seat, "the move at the table")
        for g, nat in ((g1, nat1), (g2, nat2)):
            g.wait(lambda g=g, nat=nat: G.parse_view(nat.req("VUE %s" % tid))["tiles"] == g.board,
                   "seat %d's board" % g.seat)
            g.wait(lambda g=g, nat=nat: [len(g.letters(s)) for s in range(3)]      # every rack, in each program
                   == G.parse_view(nat.req("VUE %s" % tid))["counts"], "seat %d's racks" % g.seat)
        self.assertGreater(moves, 3)
        self.assertGreaterEqual(G.parse_view(eli.req("VUE %s" % tid))["turn"], 0, "the game is still running")
        # dee quits: the table goes on, dee is still seated, and dee's clock runs as any absent player's
        g2.send(b"Z\x01")
        g2.close()
        time.sleep(1)
        lst = [l for l in eli.req("LST") if l.startswith("TAB %s " % tid)]
        self.assertIn(" playing cy,dee,eli ", lst[0])
        v = G.parse_view(eli.req("VUE %s" % tid))
        if v["turn"] == 0:
            g1.wait(g1.my_turn, "cy's turn")
            self.my_move(nat1, tid, g1)
        elif v["turn"] == 2:
            eli.req("MOV %s PASS" % tid)
        self.assertTrue(G.parse_view(eli.req("VUE %s" % tid))["turn"] in (0, 1, 2))
        self.assertTrue(nat2.req("RES %s" % tid)[-1].startswith("OK"))
        g1.wait(lambda: g1.over, "the end at cy's Networdz")
        self.assertEqual(g1.over, "Y")
        g1.wait_chat("dee resigned")
