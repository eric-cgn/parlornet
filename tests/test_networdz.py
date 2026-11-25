"""The Networdz extension (adapters/networdz.py): Grid Words for Networdz 2.0 on Windows.

NwHost below plays the part of Networdz 2.0 as the host ("Local / Dial mode"), as games captured between copies
of the program show it: it connects once for each Remote player and sends X;
the answer is D with a name, which a later D changes. New Game sends E, F and A, then G to each seat in turn for
the tile that decides who starts (H answers), A again, R with the order of play, and P to each seat in that
order, which draws (Q). Then I gives each turn. The host plays its own player's turns (J, M, P, Q), checks every
remote play against its own dictionary (M: 0 taken, 7 refused, and then I to the same seat again), and asks for
discards after a pass (N) while the bag has tiles. It sends every message to every remote copy, so the parlor's
seats see each other's. It ends the game (S) when a player goes out with the bag empty or every player passes in
a row with the bag empty. It reads the stream message by message, so a message of the wrong length fails a test.

The core runs with the small word list of the gridwords tests; the host's dictionary is that list without the
words in REFUSED, so the bot's plays of them are refused by the host and taken back at the parlor."""
import os
import random
import re
import select
import socket
import sys
import threading
import time
import unittest

from test_adapters import spawn
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args
from test_gridwords import P, WORDS, write_words

sys.path.insert(0, ROOT)
from adapters import networdz as A  # noqa: E402
from adapters.games import gridwords as G  # noqa: E402
from speed import slow  # noqa: E402

WAIT = 30
LOG = []                    # the extension's log, for a failing test's message
CORE_WORDS = set(WORDS.upper().split())
REFUSED = {"TEA", "EAT", "ATE", "TAN", "ANT"}      # in the parlor's list, not in the host's dictionary

# Messages captured from Networdz 2.0 (three players: Hank hosting, Annie and Bert answering), zeros cut off.
CAPTURED = {
    "X": "58014301",
    "D": "4401416e6e6965",
    "E": "45040100000000000000000000000200",
    "F": "46040200640048616e6b00000000000000000000000000000000000000000000000000000000000000000000000000000000"
         "00000000000000000000000000000000000000000000000000000000000000000000000003006400416e6e69650000000000"
         "00000000000000000000003137322e32312e302e330000000000000000000000000000000000000000000000000000000000"
         "0000000000000000000001000100000000000000030064004265727400000000000000000000000000000000003137322e32"
         "312e302e36000000000000000000000000000000000000000000000000000000000000000000000000000000010002000000"
         "00000000000064",
    "A": "41042d45515c5e554e0d1e262a1115120010594b62250b4901174a02395643462458483c1850534c05091b130f4f0a04381c"
         "0622471a3523075d3a343f3b273d411f19545b44162952614d3e0c2f322e3103570e630830405a2b1d21603714202c422833"
         "5f36",
    "R": "52040000010002",
    "Q": "5100fafafafafafafa0000004f59444061013c",
    "J": "4a00070803001515210007071600151519000706330007054500151527",
    "M": "4d0000000141004541001690b06e0150e2b0",
    "O": "4f025a483a2f4b1f26000000000001000000000000000000000001",
    "pass": "4f015d6202373c634f",
    "B": "4202626572742073617973206869",
    "H": "4802fafafafa08fafa",
}


def captured(kind):
    raw = CAPTURED[kind]
    m = bytes.fromhex(raw + ("0" if len(raw) % 2 else ""))
    t = {"pass": "O"}.get(kind, kind).encode()
    return m.ljust(A.SIZES[t], b"\0")


class Wire(unittest.TestCase):
    def test_reader_and_captured_messages(self):
        r = A.Reader()
        stream = b"".join(captured(k) for k in ("X", "E", "F", "A", "H", "R", "Q", "J", "M", "O", "B"))
        got = []
        for i in range(0, len(stream), 37):         # in pieces, as TCP brings them
            got += r.feed(stream[i:i + 37])
        self.assertEqual([k for k, _ in got], list("XEFAHRQJMOB"))
        self.assertEqual(r.buf, b"")
        self.assertEqual(r.feed(b"Wjunk"), [("junk", b"Wjunk")])
        self.assertEqual(A.parse_rules(captured("E")), {"racks": 1, "hints": 0, "tournament": 0, "four": 0,
                                                        "trade": 0, "timed": 0, "minutes": 2})
        players = A.parse_players(captured("F"))
        self.assertEqual([(p[0], p[1]) for p in players], [(2, "Hank"), (3, "Annie"), (3, "Bert"), (0, "")])
        self.assertEqual(players[1][2], "172.21.0.3")
        self.assertEqual(A.u16s(captured("R")[2:], 4), [0, 1, 2, 0])

    def test_tiles_and_the_bag(self):
        """The ParlorNet configuration's tile set: blanks first, then each letter; the host's first draw."""
        self.assertEqual(A.TILESET[:3] + A.TILESET[-3:], "__AYYZ")
        self.assertEqual(len(A.TILESET), 100)
        bag = list(captured("A")[2:2 + A.BAG])
        self.assertTrue(A.bag_ok(bag))
        self.assertFalse(A.bag_ok(bag[:99] + [120] + bag[100:]))
        q = captured("Q")
        draws = list(q[12:12 + A.RACK])
        self.assertEqual("".join(A.letter(bag[p]) for p in draws), "AGEELPI")
        self.assertEqual(A.letters_of([3, A.EMPTY, 0]), "A_")

    def test_builders_match_the_program(self):
        self.assertEqual(A.msg_d(1, "Annie"), captured("D"))
        self.assertEqual(A.msg_b(2, "bert says hi"), captured("B"))
        self.assertEqual(A.msg_h(2, 4, 8), captured("H"))
        o = captured("O")
        self.assertEqual(A.msg_o(2, list(o[2:9]), [0, 6]), o)
        p = captured("pass")
        self.assertEqual(A.msg_o(1, list(p[2:9]), []), p)
        rack = [A.EMPTY] * 7
        draws = [0x4f, 0x59, 0x44, 0x40, 0x61, 0x01, 0x3c]
        q = A.msg_q(0, rack, draws)
        self.assertEqual(q, captured("Q"))
        j = captured("J")
        self.assertEqual(A.msg_j(0, A.parse_play(j)[:7]), j)
        self.assertEqual(A.msg_k(3), b"K\x03")

    def test_the_hosts_play_as_a_move(self):
        """The captured J: PLEA across the center, from h8's left (the board is empty)."""
        play = A.parse_play(captured("J"))
        bag = []
        for p in play[:7]:
            if p[0] != A.OFF:
                bag.append((p[1] + 1, p[0] + 1, A.letter(p[2]), False))
        self.assertEqual(A.play_move(bag, {}), ["PLAY", "6", "8", "H", "PLEA"])
        # a tile at the end of a word on the board, and a blank
        tiles = {(8, 8): "C", (9, 8): "A"}
        self.assertEqual(A.play_move([(10, 8, "T", True)], tiles), ["PLAY", "8", "8", "H", "CA_T"])
        self.assertEqual(A.play_move([(8, 9, "A", False), (8, 10, "T", False)], tiles),
                         ["PLAY", "8", "8", "V", "CAT"])
        self.assertIsNone(A.play_move([(1, 1, "A", False), (2, 2, "B", False)], tiles))
        self.assertEqual(A.parse_racks(["RK 0 AB_", "RK 1 -", "R x"]), {0: "AB_", 1: ""})
        self.assertEqual(A.minus("AABC", "AC"), "AB")


# ---- Networdz as the host ----------------------------------------------------------------------------------

class NwHost:
    """Networdz 2.0 hosting a game with one Person (seat 0) and remote players at the parlor (seats 1 to n)."""

    def __init__(self, port, name, remotes=1, words=None):
        self.name, self.port = name, port
        self.in_game = False
        self.words = words or (CORE_WORDS - REFUSED)
        self.socks, self.readers = {}, {}
        self.names = {0: name}
        self.chats = []             # (seat, text), as Networdz's chat window shows them
        self.got = []               # every message from the parlor, in order: (seat of the link, kind, bytes)
        self.inbox = []
        self.closed = set()
        for s in range(1, remotes + 1):
            k = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
            self.socks[s], self.readers[s] = k, A.Reader()
            k.sendall(b"X" + bytes([s]) + b"C" + bytes([s]))
            self.wait(lambda s=s: s in self.names, "D for seat %d" % s)
        self.over = False

    def close(self):
        for k in self.socks.values():
            k.close()

    # ---- the wire
    def send(self, msg, to=None, but=None):
        for s, k in self.socks.items():
            if (to is None or s == to) and s != but and s not in self.closed:
                k.sendall(msg)

    def pump(self, timeout=0.5):
        live = [k for s, k in self.socks.items() if s not in self.closed]
        if not live:
            raise AssertionError("every connection closed; log %r" % LOG[-15:])
        ready, _, _ = select.select(live, [], [], timeout)
        for k in ready:
            s = next(x for x, y in self.socks.items() if y is k)
            data = k.recv(65536)
            if not data:
                self.closed.add(s)
                continue
            for kind, m in self.readers[s].feed(data):
                assert kind != "junk", "Networdz cannot read %r" % m[:16]
                # a seat speaks for itself; the parlor's own lines may also come from a seat nobody has
                # (Networdz shows ": text")
                assert m[1] == s or (kind == "B" and m[1] not in self.socks and m[1] != 0), \
                    "seat %d's connection sent a message for seat %d: %r" % (s, m[1], m)
                self.got.append((s, kind, m))
                if kind == "D":
                    # during a game Networdz takes a D for a move out of turn
                    assert not self.in_game, "D during a game: %r" % m
                    self.names[s] = A.cstr(m[2:])
                elif kind == "B":
                    self.chats.append((s, A.cstr(m[2:])))
                else:
                    self.inbox.append((s, kind, m))

    def wait(self, until, what, timeout=WAIT):
        end = time.time() + timeout
        while True:
            got = until()
            if got:
                return got
            if time.time() > end:
                raise AssertionError("waited for %s; %r; chats %r; log %r" % (what, getattr(self, "differs", ""), self.chats[-8:], LOG[-20:]))
            self.pump()

    def expect(self, seat, kinds):
        """The next message from a seat, of one of the kinds; anything else fails."""
        def first():
            for i, (s, k, m) in enumerate(self.inbox):
                if s == seat:
                    return self.inbox.pop(i)
            return None
        s, k, m = self.wait(first, "%s from seat %d" % (kinds, seat))
        assert k in kinds, "seat %d sent %s, Networdz waits for %s: %r" % (seat, k, kinds, m)
        self.send(m, but=seat)          # the host passes every remote message on to the other remotes
        return k, m

    def say(self, text):
        n = len(self.chats)
        self.send(A.msg_b(0, text))
        return n

    def said(self, text, since=0):
        return [t for _, t in self.chats[since:] if text in t]

    def wait_chat(self, text, since=0):
        return self.wait(lambda: self.said(text, since), repr(text))

    # ---- the game
    @property
    def seats(self):
        return [0] + sorted(self.socks)

    def new_game(self, rules=None, tiles=None):
        r = {"hints": 0, "racks": 1, "tournament": 0, "four": 0, "trade": 0, "timed": 0, "minutes": 2}
        r.update(rules or {})
        self.in_game = True
        self.send(b"E\x04" + b"".join(bytes([r[k], 0]) for k in
                                      ("racks", "hints", "tournament", "four", "trade", "timed", "minutes")))
        f = b"F\x04"
        for i in range(4):
            if i in self.seats:
                kind, name, addr = (A.PERSON, self.name, "") if i == 0 else (A.REMOTE, self.names[i], "127.0.0.1")
                e = bytes([kind, 0, 100, 0]) + A.text_field(name, 21) + A.text_field(addr, 49) \
                    + (bytes([1, 0, i, 0]) if i else b"\0" * 4)
            else:
                e = bytes([0, 0, 100, 0])
            f += e.ljust(84, b"\0")
        self.send(f)
        n = tiles or len(A.TILESET)
        self.bag = list(range(n))
        random.shuffle(self.bag)
        self.send_bag()
        self.racks = {s: [A.EMPTY] * A.RACK for s in self.seats}
        self.cells = "." * 225
        self.passes = 0

    def send_bag(self):
        self.send(b"A\x04" + bytes(self.bag + [0] * (A.BAG - len(self.bag))))

    def pick_start(self, first=None, take=None):
        """G to each seat; the lowest tile starts (blanks lowest), the first such seat on a tie, or first. Then the
        bag again, R and every seat's draw, and I. take: letters the host's own first draw takes."""
        picks = {}
        for s in self.seats:
            self.send(b"G" + bytes([s]))
            if s == 0:
                place = random.choice([i for i, t in enumerate(self.bag) if t != A.EMPTY])
                self.send(A.msg_h(0, 0, place))
            else:
                _, m = self.expect(s, "H")
                slots = list(m[2:2 + A.RACK])
                place = slots[2 * s]            # Networdz reads seat n's pick in slot 2n
                assert place != A.EMPTY and slots.count(A.EMPTY) == A.RACK - 1, "a pick in the wrong slot: %r" % m
            picks[s] = A.TILESET[self.bag[place]]
        if first is None:
            first = min(self.seats, key=lambda s: (A.TILESET.index(picks[s]), s))
        i = self.seats.index(first)
        self.order = self.seats[i:] + self.seats[:i]
        random.shuffle(self.bag)
        self.send_bag()
        self.send(b"R\x04" + b"".join(bytes([s, 0]) for s in (self.order + [0] * 4)[:4]))
        for s in self.order:
            self.draw(s, take if s == 0 else None)
        self.turn = first
        self.send(b"I" + bytes([first, 1, 0]))
        return first

    def letters(self, s):
        return A.letters_of(self.racks[s])

    def draw(self, s, take=None):
        self.send(b"P" + bytes([s]))
        if s == 0:
            rack, draws = self.racks[0], [A.EMPTY] * A.RACK
            before = list(rack)
            take = list(take or "")
            for i in range(A.RACK):
                left = [p for p, t in enumerate(self.bag) if t != A.EMPTY]
                if take:                        # these letters first, from wherever they are in the bag
                    want = take.pop(0)
                    left = [p for p in left if A.TILESET[self.bag[p]] == want] or left
                if rack[i] == A.EMPTY and left:
                    p = random.choice(left)
                    rack[i], draws[i], self.bag[p] = self.bag[p], p, A.EMPTY
            self.send(A.msg_q(0, before, draws))
            return
        _, m = self.expect(s, "Q")
        rack, draws = list(m[2:2 + A.RACK]), list(m[12:12 + A.RACK])
        for i in range(A.RACK):
            if draws[i] != A.EMPTY:         # Q: the rack before the draw, and the place drawn for each slot
                assert self.bag[draws[i]] != A.EMPTY, "seat %d drew place %d twice" % (s, draws[i])
                assert rack[i] == A.EMPTY == self.racks[s][i], "seat %d drew into a full slot %d" % (s, i)
                rack[i], self.bag[draws[i]] = self.bag[draws[i]], A.EMPTY
            else:
                assert rack[i] == self.racks[s][i], "seat %d's rack changed without a draw" % s
        self.racks[s] = rack

    def bag_left(self):
        return sum(1 for t in self.bag if t != A.EMPTY)

    def next_turn(self, same=False):
        if not same:
            i = self.order.index(self.turn)
            self.turn = self.order[(i + 1) % len(self.order)]
        self.send(b"I" + bytes([self.turn, 0 if same else 1, 0]))

    def apply(self, s, placed):
        """placed: [(x, y, tile, blank's letter)] -> the words it makes, or None when the host refuses them."""
        letters = "".join((b.lower() if b else A.TILESET[t]) for x, y, t, b in placed)
        cells = list(self.cells)
        for x, y, t, b in placed:
            cells[(y - 1) * 15 + x - 1] = b.lower() if b else A.TILESET[t]
        st = {"cells": self.cells, "racks": ["".join(("_" if b else A.TILESET[t]) for _, _, t, b in placed)]}
        tokens = A.play_move([(x, y, (b or A.TILESET[t]), bool(b)) for x, y, t, b in placed],
                             {(i % 15 + 1, i // 15 + 1): c for i, c in enumerate(self.cells) if c != "."})
        assert tokens, "not in a line: %r" % letters
        r = P.evaluate(st, 0, int(tokens[1]), int(tokens[2]), tokens[3], tokens[4], None)
        if any(w.upper() not in self.words for w in r["words"]):
            return None, [w.upper() for w in r["words"] if w.upper() not in self.words][0]
        self.cells = r["cells"]
        return r["words"], None

    def remote_turn(self):
        """A remote seat's turn, as Networdz runs it -> ("play", words) / ("refused", word) / ("pass", put back)."""
        s = self.turn
        k, m = self.expect(s, "JK")
        if k == "J":
            recs = A.parse_play(m)
            placed = [(c + 1, r + 1, t, b) for r, c, t, b in recs[:A.RACK] if r != A.OFF]
            for i, (r, c, t, b) in enumerate(recs[:A.RACK]):
                assert t == self.racks[s][i], "seat %d's J has another tile in slot %d" % (s, i)
            words, bad = self.apply(s, placed)
            if words is None:
                self.send(b"M" + bytes([s, 7, 1, 0]) + A.text_field(bad, 13))
                self.next_turn(same=True)
                return "refused", bad
            self.send(b"M" + bytes([s, 0, 0, 1]) + b"\0" * 13)
            self.passes = 0
            for i, (r, c, t, b) in enumerate(recs[:A.RACK]):
                if r != A.OFF:
                    self.racks[s][i] = A.EMPTY
            if self.bag_left():
                self.draw(s)
            if not self.letters(s) and not self.bag_left():
                self.end()
                return "play", words
            self.next_turn()
            return "play", words
        if not self.bag_left():
            self.passes += 1
            if self.passes >= len(self.seats):
                self.end()
                return "pass", []
            self.next_turn()
            return "pass", []
        self.send(b"N" + bytes([s]))
        _, o = self.expect(s, "O")
        flags = A.u16s(o[14:], A.RACK)
        put = [i for i in range(A.RACK) if flags[i]]
        back = [self.racks[s][i] for i in put]
        for i in put:
            self.racks[s][i] = A.EMPTY
        if put:
            self.draw(s)
            spots = [i for i, t in enumerate(self.bag) if t == A.EMPTY]
            for t in back:
                self.bag[spots.pop(random.randrange(len(spots)))] = t
            self.send_bag()
            self.passes = 0
        else:
            self.passes += 1
        self.next_turn()
        return "pass", put

    def host_play(self, words):
        """The host's own turn: its best play from words, or a pass."""
        best = None
        rack = self.letters(0)
        st = {"cells": self.cells, "racks": [rack]}
        for w in sorted(words):
            for d in "HV":
                dx, dy = (1, 0) if d == "H" else (0, 1)
                for y in range(1, 16 - dy * (len(w) - 1)):
                    for x in range(1, 16 - dx * (len(w) - 1)):
                        left, ok, text = list(rack), True, ""
                        for i, ch in enumerate(w):         # a quick look before the plugin's full check
                            c = self.cells[(y - 1 + dy * i) * 15 + x - 1 + dx * i]
                            if c != ".":
                                ok = ok and c.upper() == ch
                                text += ch
                            elif ch in left:
                                left.remove(ch)
                                text += ch
                            elif "_" in left:
                                left.remove("_")
                                text += "_" + ch
                            else:
                                ok = False
                            if not ok:
                                break
                        if not ok or len(left) == len(rack):
                            continue
                        try:
                            r = P.evaluate(st, 0, x, y, d, text, words)
                        except P.Illegal:
                            continue
                        if best is None or r["score"] > best[0]:
                            best = (r["score"], r)
        if best is None:
            self.send(b"K\x00")
            if self.bag_left():
                self.send(b"N\x00")
                self.send(A.msg_o(0, self.racks[0], []))
            self.passes += 1
            if not self.bag_left() and self.passes >= len(self.seats):
                self.end()
            else:
                self.next_turn()
            return None
        r = best[1]
        recs, used = [], set()
        for px, py, c in r["placed"]:
            want = "_" if c.islower() else c
            i = next(i for i, t in enumerate(self.racks[0]) if i not in used and t != A.EMPTY
                     and A.TILESET[t] == want)
            used.add(i)
            recs.append((i, (py - 1, px - 1, self.racks[0][i], c.upper() if c.islower() else "")))
        slots = dict(recs)
        j = [slots.get(i, (A.OFF, A.OFF, self.racks[0][i], "")) for i in range(A.RACK)]
        self.send(A.msg_j(0, j))
        self.send(b"M\x00\x00\x00\x01" + b"\0" * 13)
        self.cells = r["cells"]
        self.passes = 0
        for i in used:
            self.racks[0][i] = A.EMPTY
        if self.bag_left():
            self.draw(0)
        if not self.letters(0) and not self.bag_left():
            self.end()
        else:
            self.next_turn()
        return r["words"]

    def end(self):
        self.send(b"S\x04")
        self.over = True
        self.in_game = False


class Stack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.words_file = write_words()
        os.environ[P.WORDS_ENV] = cls.words_file
        cls.core, cls.fe, cls.port = free_port(), free_port(), free_port()
        cls.keys = make_keys("networdz", "bot")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password"),
                     spawn(*adapter_args("adapters.networdz", cls.port, cls.fe, cls.keys, "networdz"),
                           "--poll", "0.2", "--chat-gap", "0", "--keep", "3")]
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

    @staticmethod
    def agree(nat, tid, h):
        """The table's view (a seated player's, with every rack) and the host's game: the same board, racks and
        turn (the table's seat n is Networdz's seat n here)."""
        lines = nat.req("VUE %s" % tid)
        v, racks = G.parse_view(lines), A.parse_racks(lines)
        board = {(i % 15 + 1, i // 15 + 1): c for i, c in enumerate(h.cells) if c != "."}
        h.differs = (v["tiles"] == board, {s: (racks.get(s), h.letters(s)) for s in h.seats}, v["turn"], h.turn)
        return (v["tiles"] == board and all(sorted(racks.get(s, "")) == sorted(h.letters(s)) for s in h.seats)
                and v["turn"] == h.turn)

    def host(self, name, remotes=1):
        h = NwHost(self.port, name, remotes)
        self.addCleanup(h.close)
        return h

    def signed_in(self, name, remotes=1):
        pw = self.signup(name)
        h = self.host(name, remotes)
        h.wait_chat("To sign in, type /signin NAME PASSWORD")
        n = h.say("/signin %s %s" % (name, pw))
        h.wait_chat("Signed in as %s." % name, n)
        return h, pw


@slow
class Service(Stack):
    def test_hello_sign_in_and_rules(self):
        h = self.host("ann")
        self.assertEqual(h.names[1], "ParlorNet")
        h.wait_chat("To sign in, type /signin NAME PASSWORD")
        self.assertTrue(all(s == 1 for s, _ in h.chats))        # the parlor's own seat speaks
        n = h.say("hello")
        h.wait_chat("Sign in first", n)
        n = h.say("/signin ann wrong")
        h.wait_chat("There is no account ann here", n)
        pw = self.signup("ann")
        n = h.say("/signin ann %s" % pw)
        h.wait_chat("Signed in as ann.", n)
        h.wait_chat("1 remote seat here", n)
        n = h.say("/help")
        h.wait_chat("/invite NAME", n)
        # rules the parlor does not play, and another tile set, are refused in the chat
        n = h.say("/invite bot")
        h.wait_chat("You invited the bot", n)
        n = len(h.chats)
        h.new_game(rules={"trade": 1, "four": 1})
        h.wait_chat("ParlorNet plays without Trade Blanks or Exchange 4 of a kind", n)
        n = len(h.chats)
        h.new_game(tiles=104)
        h.wait_chat("Load PARLOR.CFG", n)

    def test_a_game_with_the_bot(self):
        """The host's player and the bot: every move both ways, the host's dictionary refusing the bot's words
        (taken back at the parlor, and a pass after the second refusal), and the end. The table and Networdz
        must agree at every step."""
        h, pw = self.signed_in("bea")
        n = h.say("/invite bot")
        h.wait_chat("You invited the bot", n)
        h.wait(lambda: h.names.get(1) == "bot", "the bot's name on seat 1")
        nat = self.native("bea", pw)
        tid = next(l.split()[1] for l in nat.req("LST") if " bea," in l)
        lst = nat.req("LST")
        self.assertTrue(any(l.startswith("TAB %s gridwords/1 classic 2/2 playing bea,bot" % tid)
                            and l.endswith(" integrity=false") for l in lst), lst)
        self.assertIn("INT false the host's Networdz deals the tiles", nat.req("VUE %s" % tid))
        notes = nat.req("NOTE %s" % tid)
        self.assertTrue(any("integrity=false: the host's Networdz deals the tiles" in l for l in notes), notes)
        h.new_game()
        h.pick_start()
        refused = plays = 0
        for turn in range(40):          # then the host ends the game (S), as it does once the bag is empty
            if h.over:
                break
            if h.turn == 0:     # the table and Networdz agree: the board, both racks and whose turn it is
                h.wait(lambda: self.agree(nat, tid, h), "the table to agree with Networdz")
                h.host_play(CORE_WORDS - REFUSED)
            else:
                kind, what = h.remote_turn()
                if kind == "refused":
                    refused += 1
                elif kind == "play":
                    plays += 1
        if not h.over:
            if h.turn != 0:
                h.remote_turn()
            h.wait(lambda: self.agree(nat, tid, h), "the table to agree with Networdz")
            h.end()
        end = h.wait(lambda: G.parse_view(nat.req("VUE %s" % tid))["turn"] == -1 or None, "the table's end")
        self.assertTrue(end)
        self.assertGreater(plays, 0)
        log = nat.req("SIN %s 0" % tid)
        self.assertTrue(any(" END " in l for l in log), log[-5:])
        if refused:
            self.assertTrue(any(" VOID 1 " in l for l in log))
        turns = nat.req("TRN %s" % tid)
        self.assertTrue(any("TRX PROG Networdz 2.0" in l for l in turns), turns)
        self.assertFalse(any(re.match(r"TRN \S+ \d+ \S+ 0 bea", l) and i + 1 < len(turns)
                             and not turns[i + 1].startswith("TRX MOVE") for i, l in enumerate(turns)), turns)

    def test_the_hosts_deal_is_every_seats(self):
        """The host's Networdz deals every rack. Here it starts and its first draw takes the tiles the table
        shows on the bot's rack (or the rarest ones while it shows none): the table takes the host's rack, the
        bot's rack comes from what the host's bag has left, and the two agree."""
        h, pw = self.signed_in("kit")
        n = h.say("/invite bot")
        h.wait_chat("You invited the bot", n)
        h.wait(lambda: h.names.get(1) == "bot", "the bot's name on seat 1")
        nat = self.native("kit", pw)
        tid = next(l.split()[1] for l in nat.req("LST") if " kit," in l)
        h.new_game()
        shown = A.parse_racks(nat.req("VUE %s" % tid)).get(1, "")
        h.pick_start(first=0, take=shown or "JKQXZ__")
        self.assertEqual(shown, "")                         # the table deals nothing itself
        self.assertEqual(sorted(h.letters(0)), sorted("JKQXZ__"))
        for turn in range(8):
            if h.over or (turn >= 5 and h.turn == 0):
                break
            if h.turn == 0:
                h.wait(lambda: self.agree(nat, tid, h), "the table to agree with Networdz")
                self.assertEqual(G.parse_view(nat.req("VUE %s" % tid))["bag"], h.bag_left())
                h.host_play(CORE_WORDS - REFUSED)
            else:
                h.remote_turn()
        if not h.over:
            h.wait(lambda: self.agree(nat, tid, h), "the table to agree with Networdz")
            self.assertEqual(G.parse_view(nat.req("VUE %s" % tid))["bag"], h.bag_left())
        self.assertFalse(any("the table refused" in l or "Networdz's bag has no" in l for l in LOG), LOG[-10:])

    def test_two_remote_seats_a_player_and_the_bot(self):
        """Three players: the host, a native player and the bot. The native player's exchange and pass reach
        Networdz as K, O and Q, and their chat as a line from their seat; the host's chat reaches the table."""
        h, pw = self.signed_in("cy", remotes=2)
        pw_d = self.signup("dee")
        n = h.say("/invite dee")
        h.wait_chat("dee is not online now. Your invitation waits.", n)
        dee = self.native("dee", pw_d)
        tid = next(l.split()[1] for l in dee.req("IVL") if l.startswith("IVT"))
        self.assertTrue(dee.req("JON %s" % tid)[-1].startswith("OK 1"))
        h.wait(lambda: h.names.get(1) == "dee", "dee's name on seat 1")
        n = h.say("/invite bot")
        h.wait_chat("You invited the bot", n)
        h.wait(lambda: h.names.get(2) == "bot", "the bot's name on seat 2")
        h.new_game()
        h.pick_start()
        n = h.say("good luck")
        cht = dee.req("SIN %s 0" % tid)
        h.wait(lambda: any("good luck" in l for l in dee.req("SIN %s 0" % tid)), "the host's chat at the table")
        dee.req("CHT %s hello from the web" % tid)
        h.wait_chat("hello from the web", n)
        self.assertTrue(any(s == 1 and t == "hello from the web" for s, t in h.chats))
        for turn in range(30):
            if h.over:
                break
            if h.turn == 0:
                h.host_play(CORE_WORDS - REFUSED)
            elif h.turn == 1:
                h.wait(lambda: G.parse_view(dee.req("VUE %s" % tid))["turn"] == 1, "dee's turn at the table")
                rack = G.parse_view(dee.req("VUE %s" % tid))["rack"]
                if turn < 6:
                    give = rack[:2].replace("_", "") or rack[:1]
                    self.assertTrue(dee.req("MOV %s EXCH %s" % (tid, give))[-1].startswith("OK"))
                    kind, put = h.remote_turn()
                    self.assertEqual((kind, len(put) >= 1), ("pass", True))
                else:
                    self.assertTrue(dee.req("MOV %s PASS" % tid)[-1].startswith("OK"))
                    kind, put = h.remote_turn()
                    self.assertEqual((kind, put), ("pass", []))
                self.assertEqual(sorted(A.parse_racks(dee.req("VUE %s" % tid)).get(1, "")), sorted(h.letters(1)))
            else:
                h.remote_turn()
        self.assertGreaterEqual(turn, 6)
        self.assertNotIn("ERR", " ".join(cht))

    def test_four_seats_take_an_invitation_each(self):
        """Three Remote players: the table has four seats, and an invitation for each of the three free seats
        is within the table's limit on invitations (twice its free seats)."""
        h, pw = self.signed_in("hal", remotes=3)
        self.signup("ivy")
        self.signup("jo")
        n = h.say("/invite ivy")
        h.wait_chat("ivy is not online now. Your invitation waits.", n)
        n = h.say("/invite jo")
        h.wait_chat("jo is not online now. Your invitation waits.", n)
        n = h.say("/invite bot")
        h.wait_chat("You invited the bot", n)
        h.wait(lambda: h.names.get(1) == "bot", "the bot's name on seat 1")
        nat = self.native("hal", pw)
        tab = [l for l in nat.req("LST mine") if l.startswith("TAB ") and " hal," in l]
        self.assertEqual(len(tab), 1, tab)
        self.assertIn(" 2/4 open hal,bot,-,- ", tab[0])
        self.assertTrue(tab[0].endswith(" integrity=false"), tab[0])

    def test_the_mark_is_for_server_extensions(self):
        """INT from a player's own program is refused; an ordinary table shows no mark."""
        pw = self.signup("eve")
        nat = self.native("eve", pw)
        tid = nat.req("NEW gridwords classic")[-1].split()[1]
        self.assertTrue(nat.req("INT %s false my own reasons" % tid)[-1].startswith("ERR NOTFRONTEND"))
        self.assertFalse(any("integrity" in l for l in nat.req("LST")))
        self.assertFalse(any(l.startswith("INT") for l in nat.req("VUE %s" % tid)))

    def test_who_sits_down_says_with_what(self):
        """The line that says a player sat down says what they play with (the CLIENT event after JOIN), cut to
        Networdz' line."""
        h, pw = self.signed_in("hob", remotes=2)
        pw_i, pw_j = self.signup("ira"), self.signup("jay")
        n = h.say("/invite ira")
        h.wait_chat("Your invitation waits", n)
        ira = self.native("ira", pw_i)
        ira.req("CLI PROG Xwords 1.2")
        tid = next(l.split()[1] for l in ira.req("IVL") if l.startswith("IVT"))
        self.assertTrue(ira.req("JON %s" % tid)[-1].startswith("OK 1"))
        h.wait_chat("ira sits down at table %s with Xwords 1.2." % tid, n)
        n = h.say("/invite jay")
        h.wait_chat("Your invitation waits", n)
        jay = self.native("jay", pw_j)
        jay.req("CLI PROG GameMaster 1.0.0 + ParlorNet 0.5")
        jay.req("CLI MACH Macintosh Quadra 840AV")
        n = len(h.chats)
        self.assertTrue(jay.req("JON %s" % tid)[-1].startswith("OK 2"))
        h.wait_chat("jay sits down at table %s with GameMaster 1.0.0 + ParlorNet 0.5." % tid, n)
        self.assertLessEqual(len(h.said("jay sits down", n)[0]), A.SAY_LEN, "the machine left out to fit")

    def test_new_game_before_everyone_is_seated(self):
        """New Game before the table is full waits at the first pick; the seat's name comes after the game."""
        h, pw = self.signed_in("fay")
        pw_g = self.signup("gus")
        n = h.say("/invite gus")
        h.wait_chat("Your invitation waits", n)
        n = len(h.chats)
        h.new_game()
        h.send(b"G\x00" + A.msg_h(0, 0, 3))
        h.send(b"G\x01")
        h.wait_chat("waits for 1 more player", n)
        time.sleep(1)
        self.assertFalse([k for s, k, m in h.inbox if k == "H"])        # no pick until gus sits down
        gus = self.native("gus", pw_g)
        tid = next(l.split()[1] for l in gus.req("IVL") if l.startswith("IVT"))
        self.assertTrue(gus.req("JON %s" % tid)[-1].startswith("OK 1"))
        h.wait_chat("gus sits down (named after this game)", n)
        _, m = h.expect(1, "H")
        self.assertEqual(h.names[1], "ParlorNet")
        h.end()
        h.wait(lambda: h.names.get(1) == "gus", "gus's name after the game")
