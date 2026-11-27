"""The GameMaster extension (adapters/gamemaster.py): a scripted ParlorNet rulebook signs in over its tunnel
and carries GameMaster's calls and games inside it, with the call record and the messages that
GameMaster's rulebooks and application use. All ten games play through it: Othello, Dots&Boxes and BattleShips against a web
player and against the bot, and ParlorNet's seven. A call from GameMaster by itself, outside the tunnel, is
refused. The parts first: the call record, the 10-byte address, the moves of each rulebook both ways, and the
check of an uploaded game."""
import os
import queue
import random
import re
import socket
import sys
import threading
import time
import unittest
from urllib.parse import quote

import procs
from test_adapters import Client, WebPlayers
from test_adapters import query_of  # noqa: E402
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import gamemaster as GM  # noqa: E402
from bot.play import LocalGame  # noqa: E402
from speed import slow  # noqa: E402

TIMEOUT = 20


# ---- the parts ------------------------------------------------------------------------------------

class Parts(unittest.TestCase):
    def test_call_record(self):
        """786 bytes: the game's size, the versions at 0x44, the rulebook's ID string at 0x4C, its name at
        0x8C and the caller's address at 0xCC, as GameMaster 1.0.0 sends them."""
        rec = GM.CallRecord.build("Dots&Boxes [PNL]", 0x0100, 0x0100, 568, rb_name="Dots&Boxes",
                                  address="10.0.2.15", machine="user\u2019s Macintosh".encode("mac_roman")
                                  .decode("latin-1"))
        self.assertEqual(len(rec), 786)
        self.assertEqual(rec[4:26], b"\x15Shall we play a game?")
        self.assertEqual(rec[0x2F0:0x301], b"\x10user\xd5s Macintosh")
        self.assertEqual(rec[:4], b"\x00\x00\x02\x38")
        self.assertEqual(rec[0x44:0x4C], b"\x01\x00\x01\x00\x01\x00\x01\x00")
        self.assertEqual(rec[0x4C:0x4C + 17], b"\x10Dots&Boxes [PNL]")
        self.assertEqual(rec[0xCC:0xCC + 10], b"\x0910.0.2.15")
        c = GM.CallRecord(rec)
        self.assertEqual((c.size, c.version, c.needs, c.rb_version, c.rb_oldest), (568, 0x100, 0x100, 0x100, 0x100))
        self.assertEqual((c.rb_id, c.rb_name, c.address, c.key), ("Dots&Boxes [PNL]", "Dots&Boxes", "10.0.2.15",
                                                                   GM.GREETING))
        self.assertEqual(c.machine, "user\xd5s Macintosh")
        self.assertEqual(GM.bcd(0x0020), "0.2.0")
        rb = GM.RULEBOOKS["Othello [RHS]"]
        for version, oldest, ok in ((0x20, 0x20, True), (0x30, 0x20, True), (0x30, 0x30, False), (0x10, 0x10, False)):
            self.assertEqual(rb.takes(GM.CallRecord(GM.CallRecord.build(rb.rb_id, version, oldest, 0))), ok)

    def test_grid_words_plays_every_ruleset(self):
        """The Grid Words rulebook is sent the table's own board and letter values, so it plays every Grid Words
        ruleset, and the lobby knows all of them by the rulebook's own (standard). Other rulebooks play only
        theirs."""
        for rules in ("standard", "classic", "mine"):
            self.assertEqual(GM.game_of("gridwords/1", rules), "Grid Words")
            self.assertEqual(GM.lobby_rules("gridwords/1", rules), "standard")
        self.assertEqual(GM.game_of("mancala/1", "kalah"), "Mancala")
        self.assertIsNone(GM.game_of("mancala/1", "oware"))
        self.assertIsNone(GM.lobby_rules("mancala/1", "oware"))
        self.assertIsNone(GM.game_of("chess/1", "standard"))

    def test_grid_words_board_lines(self):
        """SQ: the 225 squares row by row (. d t D T), and LV: the points of A to Z, from the view's PRM and TIL
        lines. None without them."""
        geo = ["BRD 15 15", "CTR 8 8", "PRM TW 1 1 15 15", "PRM DW 8 8", "PRM TL 2 1", "PRM DL 1 2",
               "TIL A 1 9", "TIL Q 9 1", "TIL _ 0 2", "X 8 8 A"]
        sq, lv = GM.geometry_lines(geo)
        self.assertEqual(sq, "SQ T" + "t" + "." * 13 + "d" + "." * 14 + "." * 15 * 5 + "." * 7 + "D" + "." * 7 +
                         "." * 15 * 6 + "." * 14 + "T")
        self.assertEqual(len(sq), 3 + 225)
        self.assertEqual(lv, "LV 1 " + "0 " * 15 + "9 " + "0 " * 8 + "0")
        self.assertEqual(GM.geometry_lines(["X 8 8 A", "W 0"]), [])

    def test_messages_and_address(self):
        """Pascal strings: the length, then ~ (GameMaster's own) or # (the rulebook's), then the text. The
        answer to a call is ~Send Game Across Port : and 1, 0, the IPv4 address and the port in 32 bits; in the
        tunnel, 0.0.0.0 and 0x504E0000 plus the call's number."""
        self.assertEqual(GM.control(GM.SEND_GAME), b"\x18~Send Game Across Port :")
        self.assertEqual(GM.rulebook("M0003012"), b"\x09#M0003012")
        self.assertEqual(GM.address_bytes("192.0.2.10", 6508), b"\x01\x00\xc0\x00\x02\x0a\x00\x00\x19\x6c")
        self.assertEqual(GM.address_bytes("0.0.0.0", GM.TUNNEL_PORT + 3), b"\x01\x00\x00\x00\x00\x00PN\x00\x03")
        self.assertEqual(sorted(GM.RULEBOOKS), sorted([
            "Othello [RHS]", "Dots&Boxes [PNL]", "BattleShips [RHS]", "Checkers [ParlorNet]", "Mancala [ParlorNet]",
            "Five in a Row [ParlorNet]", "Grid Words [ParlorNet]", "Rummy Tile [ParlorNet]",
            "Cannon Fire [ParlorNet]", "Backgammon [ParlorNet]"]))

    def test_dots_lines_both_ways(self):
        """Every line of the 13 by 11 board: M0rrrccc across (dot row r, dot columns c-1 to c), M1rrrccc down
        (dot column c, dot rows r-1 to r), and back."""
        plugin = LocalGame("dotsandboxes", "majority", 2, 0)
        lines = plugin.m.open_lines(plugin.state)
        self.assertEqual(len(lines), 13 * 12 + 11 * 14)
        msgs = set()
        for tok in lines:
            msg = GM.DotsAndBoxes.to_mac(tok)
            self.assertRegex(msg, r"^M[01]\d{6}\Z")
            self.assertEqual(GM.DotsAndBoxes.to_token(msg), tok)
            msgs.add(msg)
        self.assertEqual(len(msgs), len(lines))
        self.assertEqual(GM.DotsAndBoxes.to_mac("a1-b1"), "M0000001")
        self.assertEqual(GM.DotsAndBoxes.to_mac("a1-a2"), "M1001000")
        self.assertEqual(GM.DotsAndBoxes.to_mac("m12-n12"), "M0011013")
        for bad in ("M0012001", "M1000000", "M0000000", "M2001001", "X0000001", "M00000010", "M00a0001"):
            self.assertIsNone(GM.DotsAndBoxes.to_token(bad), bad)

    def test_battleships_fleet_both_ways(self):
        toks = GM.BattleShips.fleet_tokens("00h01h02h03h99v")
        self.assertIsNone(toks)                                     # the tug would leave the grid at j10
        toks = GM.BattleShips.fleet_tokens("00h01h02h03h88v")
        self.assertEqual(toks, ["a1-e1", "a2-d2", "a3-c3", "a4-c4", "i9-i10"])
        of = ["OF %d %s" % (n, t) for n, t in zip((5, 4, 3, 3, 2), toks)]
        self.assertEqual(GM.BattleShips.fleet_text(of), "00h01h02h03h88v")
        self.assertIsNone(GM.BattleShips.fleet_text(of[:4]))
        self.assertIsNone(GM.BattleShips.fleet_tokens("00h01h02h03h8"))

    def test_fresh_games(self):
        """A saved game in progress is turned down; a new one, or bytes that are not the expected record,
        are taken."""
        self.assertTrue(GM.Othello.fresh(othello_state()))
        moved = bytearray(othello_state())
        moved[4 + (2 + 1) * 10 + 4 + 1] = 2
        self.assertFalse(GM.Othello.fresh(bytes(moved)))
        self.assertTrue(GM.Othello.fresh(b"\x07" * 200))
        self.assertTrue(GM.DotsAndBoxes.fresh(dots_state()))
        self.assertFalse(GM.DotsAndBoxes.fresh(dots_state(drawn=1)))
        self.assertTrue(GM.DotsAndBoxes.fresh(b"\x05" * 568))
        self.assertTrue(GM.Checkers.fresh(checkers_state()))
        self.assertFalse(GM.Checkers.fresh(checkers_state(cells="x" * 11 + "." * 9 + "o" * 12)))
        self.assertFalse(GM.Checkers.fresh(checkers_state(turn=1)))
        self.assertTrue(GM.Checkers.fresh(b"\x07" * 1200))

    def test_checkers_moves(self):
        """A Checkers move is its squares, - for a step and x for jumps, and goes to the core as one token; a
        line of jumps takes back as many steps as it has."""
        for good in ("11-15", "15x22", "15x22x31", "1x10x19x28"):
            self.assertEqual(GM.Checkers.to_core(good), [good])
        for bad in ("11-15-19", "15x22-26", "x15", "15", "", "11 15", "a1-b2"):
            self.assertIsNone(GM.Checkers.to_core(bad), bad)
        self.assertEqual((GM.Checkers.steps("11-15"), GM.Checkers.steps("15x22x31")), (1, 2))

    def test_mancala_moves_and_record(self):
        """A Mancala move is a pit, 1 to 6; a new game has four seeds in each pit and empty stores."""
        self.assertEqual([GM.Mancala.to_core(p) for p in "16"], [["1"], ["6"]])
        for bad in ("0", "7", "12", "", "a"):
            self.assertIsNone(GM.Mancala.to_core(bad), bad)
        self.assertTrue(GM.Mancala.fresh(parlor_state(b"PNmc", bytes([4] * 6 + [0] + [4] * 6 + [0]))))
        self.assertFalse(GM.Mancala.fresh(parlor_state(b"PNmc", bytes([0] + [5] * 5 + [0] + [4] * 6 + [0]))))

    def test_grid_words_moves_and_record(self):
        """Grid Words moves are the core's tokens, as the rulebook builds them; a new game has an empty board."""
        for good in ("PASS", "PLAY 8 8 H WORD", "PLAY 15 1 V _QI", "EXCH QV_"):
            self.assertEqual(GM.GridWords.to_core(good), [good])
        for bad in ("PLAY 0 8 H WORD", "PLAY 8 8 D WORD", "PLAY 8 8 H word", "EXCH ABCDEFGH", "pass", ""):
            self.assertIsNone(GM.GridWords.to_core(bad), bad)
        self.assertTrue(GM.GridWords.fresh(parlor_state(b"PNgw", bytes(260))))
        # a refusal in the player's terms, as the web and telnet word it
        self.assertEqual(GM.GridWords.refusal("PLAY 7 4 H HEX", "not in the word list: HEX"),
                         "Can't play HEX across from g4: not in the word list: HEX.")
        self.assertEqual(GM.Checkers.refusal("11-15", "not your turn"), "not your turn")
        self.assertFalse(GM.GridWords.fresh(parlor_state(b"PNgw", bytes(112) + b"A" + bytes(147))))

    def test_rummy_tile_steps_and_record(self):
        """Rummy Tile steps are the core's tokens; a new game has no set on the table."""
        for good in ("R7B7K7-n", "R7-3", "3.2-5", "3.R7-n", "3.J-r", "3-5", "3/4", "reset", "UND", "END"):
            self.assertEqual(GM.RummyTile.to_core(good), [good])
        for bad in ("R14-3", "3.2-x", "PASS", "", "R7"):
            self.assertIsNone(GM.RummyTile.to_core(bad), bad)
        self.assertTrue(GM.RummyTile.fresh(parlor_state(b"PNrt", bytes(800))))
        self.assertFalse(GM.RummyTile.fresh(parlor_state(b"PNrt", b"\x00\x02" + bytes(798))))

    def test_cannon_fire_moves_and_record(self):
        """Cannon Fire moves are the core's FIRE and MOVE tokens; a new game has no arena yet."""
        for good in ("FIRE 45 60", "FIRE 0 0", "FIRE 180 100", "MOVE L 7", "MOVE R 999"):
            self.assertEqual(GM.CannonFire.to_core(good), [good])
        for bad in ("FIRE 181 50", "FIRE 45 101", "MOVE L 0", "MOVE U 3", "fire 1 1", ""):
            self.assertIsNone(GM.CannonFire.to_core(bad), bad)
        self.assertTrue(GM.CannonFire.fresh(parlor_state(b"PNcf", bytes(400))))

    def test_backgammon_moves_and_record(self):
        """A Backgammon move is one of the core's words, or a whole turn of one to four steps; a new game has the
        starting position at 1024 (seat 0's checkers counted up, seat 1's down) and nothing on the bar or off."""
        for good in ("roll", "double", "take", "drop", "accept", "reject", "end"):
            self.assertEqual(GM.Backgammon.to_core(good), [good])
        self.assertEqual(GM.Backgammon.to_core("13-9 9-4"), ["13-9", "9-4"])
        self.assertEqual(GM.Backgammon.to_core("bar-22 6-off 5-off 4-off"), ["bar-22", "6-off", "5-off", "4-off"])
        for bad in ("13-9 9-4 8-4 6-2 5-1", "25-20", "0-off", "off-3", "13/9", "roll 13-9", "ROLL", "", "resign"):
            self.assertIsNone(GM.Backgammon.to_core(bad), bad)
        start = bytes(v & 0xFF for v in (-2, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, -5, 5, 0, 0, 0, -3, 0, -5, 0, 0, 0, 0, 2))
        self.assertTrue(GM.Backgammon.fresh(parlor_state(b"PNbg", start + bytes(80))))
        moved = bytearray(start)
        moved[5], moved[4] = 4, 1
        self.assertFalse(GM.Backgammon.fresh(parlor_state(b"PNbg", bytes(moved) + bytes(80))))
        self.assertFalse(GM.Backgammon.fresh(parlor_state(b"PNbg", start + b"\x01" + bytes(79))))

    def test_five_in_a_row_moves_and_record(self):
        """A Five in a Row move is a point of the 15 by 15 board; a new game has no stone."""
        for good in ("a1", "h8", "o15", "a10"):
            self.assertEqual(GM.FiveInARow.to_core(good), [good])
        for bad in ("p1", "a16", "a0", "h", "8h", "H8", ""):
            self.assertIsNone(GM.FiveInARow.to_core(bad), bad)
        self.assertTrue(GM.FiveInARow.fresh(parlor_state(b"PNfr", bytes(232))))
        self.assertFalse(GM.FiveInARow.fresh(parlor_state(b"PNfr", bytes(112) + b"\x01" + bytes(119))))


class FakeSession:
    """What a game's translation uses of a session, for the parts: the seat, the table, the messages sent."""

    def __init__(self, seat=0, view=(), here=False):
        self.seat, self.tid, self.positions, self.view, self.here = seat, "7", True, list(view), here
        self.sent = []

    async def send(self, text):
        self.sent.append(text)

    async def req(self, text):
        return self.view, "OK 9"

    def made_here(self):
        return self.here

    def note(self, text):
        pass


def run(coro):
    import asyncio
    return asyncio.run(coro)


class Positions(unittest.TestCase):
    """The whole position each of ParlorNet's rulebooks is sent, from the core's view (POS ... POE)."""

    def test_checkers(self):
        cells = "x" * 11 + "." * 9 + "o" * 11 + "O"
        view = ["V checkers/1 12", "B " + cells, "C 11 0 11 1", "W 1", "Q move", "L 3 40", "D 0"]
        s = FakeSession(seat=1)
        run(GM.Checkers(s).send_position(view))
        self.assertEqual(s.sent, ["POS 1 1 1", "BD " + cells, "L 3 40", "POE"])
        self.assertTrue(GM.Checkers.half_made(view + ["J 11 15"], 1))
        self.assertFalse(GM.Checkers.half_made(view + ["J 11 15"], 0))
        g = GM.Checkers(FakeSession())
        run(g.send_position(view + ["J 22 18"]))         # the other side's turn half made: sent again at its end
        self.assertTrue(g.stale)

    def test_mancala_and_five_in_a_row(self):
        view = ["RU kalah 4", "B 0 5 5 5 5 4 1 4 4 4 4 4 4 0", "LM 0 1", "W 1"]
        s = FakeSession()
        run(GM.Mancala(s).send_position(view))
        self.assertEqual(s.sent, ["POS 0 1 0", "BD 0 5 5 5 5 4 1 4 4 4 4 4 4 0", "LM 0 1", "POE"])
        view = ["P 0 h8 a1", "P 1 o15", "C 2 1", "L 1 o15", "W 0"]
        s = FakeSession()
        run(GM.FiveInARow(s).send_position(view))
        cells = ["0"] * 225
        cells[0], cells[7 * 15 + 7], cells[224] = "1", "1", "2"
        self.assertEqual(s.sent, ["POS 0 0 0", "BD " + "".join(cells), "LS o15", "POE"])

    def test_grid_words(self):
        """The board with a blank in lower case, the player's rack, the scores, the bag and the last play."""
        view = ["X 7 8 C 8 8 a 9 8 T 8 9 O", "R AE_RSTU", "K 0 7", "K 1 7", "BAG 80", "T 0 5", "T 1 2", "W 1",
                "LAST 1 8 8 V AO 2"]
        s = FakeSession()
        run(GM.GridWords(s).send_position(view))
        cells = ["."] * 225
        cells[7 * 15 + 6], cells[7 * 15 + 7], cells[7 * 15 + 8], cells[8 * 15 + 7] = "C", "a", "T", "O"
        self.assertEqual(s.sent, ["POS 0 1 0", "BD " + "".join(cells), "K AE_RSTU", "S 5 2", "B 80", "LP 8 8 V 2",
                                  "POE"])
        self.assertTrue(all(len(l) <= 250 for l in s.sent))

    def test_rummy_tile_and_cannon_fire(self):
        view = ["P 70", "C 14 15", "I 0 1", "G 0 0", "W 0", "ST 1 r R1 R2 R3", "H R7 B7", "TP 0 - none"]
        s = FakeSession()
        g = GM.RummyTile(s)
        run(g.send_position(view))
        self.assertEqual(s.sent, ["POS 0 0 0", "Z", "ST 1 r R1 R2 R3", "H R7 B7", "TP 0 - none", "P 70", "C 14 15",
                                  "I 0 1", "W 0", "POE"])
        self.assertEqual(g.shown, s.sent[1:-1])              # the same state is not sent twice
        view = ["Z 0 0123", "K 0 1 100", "K 1 3 90", "A 0 45 50", "A 1 120 60", "L 0 4", "L 1 3", "N -2",
                "H 2 1", "W 1", "B 30 0", "T 0 0", "T 1 10"]
        s = FakeSession(seat=1)
        run(GM.CannonFire(s).send_position(view))
        self.assertEqual(s.sent, ["POS 1 1 0", "Z 0 0123", "K 0 1 100", "K 1 3 90", "AIM 0 45 50", "AIM 1 120 60",
                                  "L 0 4", "L 1 3", "N -2", "H 2 1", "B 30 0", "T 0 0", "T 1 10", "W 1", "POE"])

    def test_backgammon(self):
        """The side of the home board first (H; right unless the player keeps it on the left), then the state
        from the view: BD with the 24 points, the bars and the trays, the cube, the dice, the steps the roll
        needs, who may double, the phase and W."""
        view = ["V backgammon/1 9", "P 1 -2 0 0 0 0 4 1 3 0 0 0 -5", "P 13 4 0 0 0 -3 1 -5 0 0 0 0 2",
                "B 0 0", "O 0 0", "D 3 1", "C 1 -", "W 1", "Q move", "N 2", "DR 3 1", "CA 1 1", "CLK 1 50 60"]
        s = FakeSession(seat=1)
        g = GM.Backgammon(s)
        run(g.send_position(view))
        self.assertEqual(s.sent, ["H right", "POS 1 1 0", "BD -2 0 0 0 0 4 1 3 0 0 0 -5 4 0 0 0 -3 1 -5 0 0 0 0 2 0 0 0 0",
                                  "CU 1 -", "DI 3 1", "DR 3 1", "N 2", "CA 1 1", "Q move", "W 1", "POE"])
        s = FakeSession(view=["PRF bghome left"])
        run(GM.Backgammon(s).home())
        self.assertEqual(s.sent, ["H left"])
        # the last turn played, from its events: the steps taken back are left out, a hit is marked
        g = GM.Backgammon(FakeSession())
        for kind, args in (("ROLL", ["1", "6", "5"]), ("STEP", ["1", "1", "7"]), ("UNDO", ["1", "1", "7"]),
                           ("STEP", ["1", "12", "18"]), ("STEP", ["1", "1", "6", "HIT"]), ("TURN", ["0"])):
            g.track(kind, args)
        self.assertEqual(g.last, "LM 1 6 5 12-18 1-6*")
        for kind, args in (("ROLL", ["0", "6", "6"]), ("PASS", ["0"]), ("TURN", ["1"])):
            g.track(kind, args)
        self.assertEqual(g.last, "LM 0 6 6 pass")

    def test_a_move_for_this_seat_from_elsewhere(self):
        """This seat's move that this session did not make (the web, telnet) makes the Mac's board stale: the end
        of the turn sends the whole position instead of the other side's moves. Its own moves do not."""
        view = ["P 0 h8 a1", "P 1 o15", "W 0"]
        s = FakeSession(view=view)
        g = GM.FiveInARow(s)
        run(g.on_event("PUT", ["0", "a1"]))
        self.assertTrue(g.stale)
        run(g.on_event("PUT", ["1", "o15"]))
        run(g.on_event("TURN", ["0"]))
        self.assertEqual(s.sent[0], "POS 0 0 0")
        self.assertFalse(g.stale)
        s = FakeSession(view=view, here=True)
        g = GM.FiveInARow(s)
        run(g.on_event("PUT", ["0", "a1"]))
        run(g.on_event("PUT", ["1", "o15"]))
        run(g.on_event("TURN", ["0"]))
        self.assertEqual(s.sent, ["M o15"])


class Wiring(unittest.TestCase):
    def test_the_container_starts_it_on_one_port(self):
        """deploy/start.py: the GameMaster extension listens on 6508 only, for the ParlorNet rulebook's tunnel,
        and needs no address. PARLOR_GAMEMASTER_PORT declares another port, where the host publishes 6508 under
        another number. deploy/install.sh and the Dockerfile publish 6508 alone."""
        import shutil
        import tempfile
        from unittest import mock
        from test_review_fixes import load_start
        from adapters import catalog
        self.assertEqual(dict((n, p) for n, _, p, _ in catalog.ADAPTERS)["gamemaster"], 6508)
        self.assertNotIn("gamemaster", catalog.PUBLISHED)
        with open(os.path.join(ROOT, "deploy", "install.sh")) as f:
            install = f.read()
        self.assertRegex(install, r"for p in [0-9 ]* 6508 6512 ")
        with open(os.path.join(ROOT, "Dockerfile")) as f:
            docker = f.read()
        self.assertIn("gamemaster:10052", docker)
        self.assertIn(" 6508 6512 ", [l for l in docker.splitlines() if l.startswith("EXPOSE")][0])
        for text in (install, docker):
            self.assertNotIn("6509", text)
            self.assertNotIn("PARLOR_GAMEMASTER_ADDRESS", text)
        self.assertEqual(GM.parse_args([]).port, 6508)
        for declared in (None, "7508"):
            d = tempfile.mkdtemp(prefix="pndata")
            try:
                env = {"PARLOR_DB": os.path.join(d, "parlord.db"), "PARLOR_ADAPTERS": "gamemaster",
                       "PARLOR_SETTINGS": os.path.join(d, "none.conf"), "PARLOR_GAMEMASTER_PORT": declared or ""}
                st = load_start(env)
                started, out = [], []
                with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                        mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                        mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                        mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))), \
                        mock.patch.object(st.signal, "signal"), mock.patch.object(st.sys, "argv", ["start.py"]):
                    with self.assertRaises(KeyboardInterrupt):
                        st.main()
                args = dict(started)["gamemaster"]
                self.assertEqual(args[args.index("--port") + 1], "6508")
                for gone in ("--game-port", "--public-address", "--public-game-port"):
                    self.assertNotIn(gone, args)
                if declared:
                    self.assertEqual(args[args.index("--public-port") + 1], declared)
                else:
                    self.assertNotIn("--public-port", args)
                from adapters.common import CoreClient
                kept = CoreClient.key_file
                try:
                    GM.parse_args(args[1:])                       # the extension takes them
                finally:
                    CoreClient.key_file = kept
            finally:
                shutil.rmtree(d, True)


class SetupPage(unittest.TestCase):
    def test_the_rulebook_is_required_and_the_bot_is_plain(self):
        """The GameMaster setup page says that ParlorNet's own rulebook is required and carries GameMaster's
        own Othello, Dots&Boxes and BattleShips too, and in a sentence how to get a game with the bot. Nothing
        of Simple Talk is left."""
        from adapters import clients as C
        self.assertEqual(C.BY_ID["gamemaster"].port, 6508)
        with open(os.path.join(ROOT, "docs", "clients", "gamemaster.md"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("You need ParlorNet's own rulebook to play on a parlor.", page)
        self.assertIn("GameMaster's own Othello, Dots&Boxes and BattleShips as well as ParlorNet's seven", page)
        self.assertIn("[ParlorNet's own rulebook](http://docs.parlornet.org/parlornet-rulebook.html)", page)
        self.assertIn('Click "Othello vs the bot"', page)
        self.assertIn("To play the bot, choose Bot in the Opponent menu.", page)
        self.assertIn("or the bot if nobody joins within a minute", page)
        self.assertIn("each player's own program receives the other player's fleet", page)
        self.assertNotIn("Without the rulebook", page)
        self.assertEqual(page.count("Simple Talk"), 1)          # the history's list of the 1991 rulebooks
        for auth in ("none", "password", "new"):
            self.assertNotIn("Simple Talk", C.SIGNIN["gamemaster"][auth])
        self.assertIn("ParlorNet rulebook's window", C.SIGNIN["gamemaster"]["password"])
        for name in ("Console", "Players", "help_lines", "BOT_HOWTO", "ipv4"):
            self.assertFalse(hasattr(GM, name), name)


def othello_state():
    """Othello's game record for a new game: the handle, board[-1..8, -1..8] by column, move and mypiece
    (white), cheat, the piece counts and the rest."""
    rec = bytearray(122)
    for (x, y), p in (((3, 3), 1), ((3, 4), 2), ((4, 3), 2), ((4, 4), 1)):
        rec[4 + (x + 1) * 10 + y + 1] = p
    rec[104] = rec[105] = 2
    return bytes(rec)


def parlor_state(magic, data, turn=0):
    """A record of one of ParlorNet's game rulebooks: the 1024-byte header (the game's code, layout 1, the
    seat to move at 8), then the game's own fields."""
    rec = bytearray(1024)
    rec[:6] = magic + b"\x00\x01"
    rec[8] = turn
    return bytes(rec) + data + bytes(4)


def backgammon_state():
    """The Backgammon rulebook's record of a new game (eric-cgn/parlornet-gamemaster, rulebooks/backgammon): the
    header, then the starting position at 1024, seat 0's checkers counted up and seat 1's down, and the rest."""
    start = bytes(v & 0xFF for v in (-2, 0, 0, 0, 0, 5, 0, 3, 0, 0, 0, -5, 5, 0, 0, 0, -3, 0, -5, 0, 0, 0, 0, 2))
    return parlor_state(b"PNbg", start + bytes(80))


def checkers_state(cells=None, turn=0):
    """The Checkers rulebook's record (eric-cgn/parlornet-gamemaster, rulebooks/checkers): the 1024-byte header
    (PNck, layout 1, the seat to move at 8), then the 32 squares."""
    rec = bytearray(1096)
    rec[:6] = b"PNck\x00\x01"
    rec[8] = turn
    rec[1024:1056] = (cells or "x" * 12 + "." * 8 + "o" * 12).encode()
    return bytes(rec)


def checkers_turn(g, seat, rnd):
    """A whole turn for seat, chosen at random from the steps the rules allow -> "15x22x31"."""
    st, steps = g.state, []
    while True:
        moves = g.m.op_moves({"rules": "american", "state": st, "seat": seat})["moves"]
        tok = rnd.choice(moves)
        r = g.m.op_apply({"rules": "american", "state": st, "seat": seat, "move": [tok]})
        steps.append(tok)
        st = r["state"]
        if r.get("staged") != "more" or "end" in r:
            break
    return steps[0] + "".join(t[t.index("x"):] for t in steps[1:])


def checkers_play(g, seat, move):
    """A whole turn on the local copy, as the rulebook plays it: the steps, then the end of the turn."""
    got = g.apply(seat, [move], False)
    if got == "OK done":
        got = g.apply(seat, ["!end"], False)
    return got


def dots_state(drawn=0):
    """Dots&Boxes' game record (568 bytes): the lines from 0x0A, gray (2) local and to move at 0x1F4."""
    rec = bytearray(0x238)
    for i in range(drawn):
        rec[0x0A + i] = 1
    rec[0x1F4] = rec[0x1F5] = 2
    return bytes(rec)


# ---- a scripted GameMaster -----------------------------------------------------------------------------

def stock_call(port, rb_id, version, oldest, size=0, machine="Test Mac"):
    """GameMaster 1.0.0 calling by itself (Game > Connect): the call record straight to the port, as GameMaster
    sends it to 918. -> (the answer's text, whether the parlor then closed the connection)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT)
    try:
        s.sendall(GM.CallRecord.build(rb_id, version, oldest, size, address="10.0.2.15", machine=machine))
        n = recv_exactly(s, 1)[0]
        body = recv_exactly(s, n).decode("latin-1")
        try:
            closed = s.recv(1) == b""
        except OSError:
            closed = True
        return body, closed
    finally:
        s.close()


TUNNEL_WRONG = b"ERR PROTOCOL wrong protocol; see http://docs.parlornet.org/docs/protocol.html\r\n"


class Tunnel:
    """The ParlorNet rulebook's connection to the call port, scripted: PARLORNET/1, the greeting, the sign-in
    and lobby lines, and the GameMaster connections inside it (GMO, GMS, GMD, GMX; GMK from the parlor)."""

    def __init__(self, port, opening=b"PARLORNET/1\r\n"):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT)
        self.s.sendall(opening)
        self.f = self.s.makefile("rb")
        self.greeting = self.f.readline().decode().rstrip("\r\n")
        self.lines = queue.Queue()
        self.cond = threading.Condition()
        self.bufs, self.acked, self.closed = {}, set(), set()
        self.ended = False
        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        try:
            for raw in self.f:
                l = raw.decode().rstrip("\r\n")
                f = l.split()
                with self.cond:
                    if f and f[0] == "GMD":
                        self.bufs.setdefault(int(f[1]), bytearray()).extend(bytes.fromhex(f[2]))
                    elif f and f[0] == "GMK":
                        self.acked.add(int(f[1]))
                    elif f and f[0] == "GMX":
                        self.closed.add(int(f[1]))
                    else:
                        self.lines.put(l)
                    self.cond.notify_all()
        except (OSError, ValueError):
            pass
        with self.cond:
            self.ended = True
            self.cond.notify_all()

    def line(self, text):
        self.s.sendall(text.encode() + b"\r\n")

    def req(self, text):
        self.line(text)
        out = []
        while True:
            l = self.lines.get(timeout=TIMEOUT)
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def data(self, n, data):
        for i in range(0, len(data), 36):
            self.line("GMD %d %s" % (n, data[i:i + 36].hex()))

    def wait(self, test, timeout=TIMEOUT):
        end = time.time() + timeout
        with self.cond:
            while not test():
                left = end - time.time()
                if left <= 0:
                    return False
                self.cond.wait(left)
        return True

    def recv(self, n, count, timeout=TIMEOUT):
        """count bytes of connection n; None when it closed first."""
        ok = self.wait(lambda: len(self.bufs.get(n, b"")) >= count or n in self.closed or self.ended, timeout)
        with self.cond:
            b = self.bufs.setdefault(n, bytearray())
            if len(b) < count:
                if ok:
                    return None
                raise socket.timeout("nothing on connection %d" % n)
            out = bytes(b[:count])
            del b[:count]
            return out

    def close(self):
        try:
            self.s.shutdown(socket.SHUT_RDWR)
            self.s.close()
        except OSError:
            pass


class TunnelGame:
    """A game of GameMaster's through the tunnel, as the rulebook's hooks carry it: the call on connection
    call, then the game connection on game. Read and written as GameMaster above."""

    def __init__(self, tunnel, call, game, who="bot"):
        self.t, self.c, self.n, self.who = tunnel, call, game, who
        self.got = []

    def call(self, rb_id, version, oldest, state=b"", needs=0x0100):
        """-> the answer's text; on ~Send Game Across Port : the game connection is open and the game sent."""
        t = self.t
        t.line("GMO %d %s" % (self.c, self.who))
        assert t.wait(lambda: self.c in t.acked or self.c in t.closed), "no GMK"
        t.data(self.c, GM.CallRecord.build(rb_id, version, oldest, len(state), needs=needs, address="10.0.2.15",
                                           machine="Tunnel Mac"))
        n = t.recv(self.c, 1)[0]
        body = t.recv(self.c, n)
        if body != b"~" + GM.SEND_GAME.encode():
            return body.decode("latin-1")
        self.addr = t.recv(self.c, 10)
        t.line("GMX %d" % self.c)
        t.line("GMS %d %d" % (self.n, self.c))
        assert t.wait(lambda: self.n in t.acked or self.n in t.closed), "no GMK for the game"
        t.data(self.n, state)
        return GM.SEND_GAME

    def read(self, timeout=TIMEOUT):
        head = self.t.recv(self.n, 1, timeout)
        if head is None:
            return None
        body = self.t.recv(self.n, head[0], timeout)
        m = (body[:1] == b"~", body[1:].decode("latin-1"))
        self.got.append(m)
        return m

    def expect(self, text, timeout=TIMEOUT):
        """Read until GameMaster's own message text; -> the rulebook messages read before it."""
        before = []
        end = time.time() + timeout
        while True:
            m = self.read(max(0.1, end - time.time()))
            if m is None:
                raise AssertionError("closed before ~%s (read %r)" % (text, self.got[-10:]))
            if m == (True, text):
                return before
            before.append(m)

    def ctl(self, text):
        self.t.data(self.n, GM.control(text))

    def send(self, text):
        self.t.data(self.n, GM.rulebook(text))

    def close(self):
        pass


class TelnetPlain:
    """A telnet player in the plain mode, typing lines (tests/test_adapters.py's Client)."""

    def __init__(self, c):
        self.c = c

    def until(self, text, timeout=TIMEOUT):
        end = time.time() + timeout
        while text.encode("latin-1") not in self.c.raw[self.c.mark:]:
            if self.c.closed or time.time() > end:
                raise AssertionError("%r not seen by telnet: %r" % (text, self.c.raw[-600:]))
            self.c.pump()
        self.c.mark = self.c.raw.index(text.encode("latin-1"), self.c.mark) + len(text)


def recv_exactly(s, n):
    out = b""
    while len(out) < n:
        b = s.recv(n - len(out))
        if not b:
            raise EOFError
        out += b
    return out


class Native:
    """The core's public port as a player (sign-in none), for what the core says about a table."""

    def __init__(self, port, name):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT)
        self.s.sendall(b"PARLORNET/1\r\n")
        self.f = self.s.makefile("rb")
        self.f.readline()
        assert self.req("HELO %s" % name)[-1].startswith("OK")

    def req(self, text):
        self.s.sendall(text.encode() + b"\r\n")
        out = []
        while True:
            l = self.f.readline().decode().rstrip("\r\n")
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def tables(self):
        from adapters.common import parse_tables
        return parse_tables(self.req("LST"))

    def until_event(self, tid, pattern, seq=0, timeout=TIMEOUT):
        """WAT until an event matching pattern; -> (its line, the next seq)."""
        end = time.time() + timeout
        while time.time() < end:
            lines = self.req("WAT %s %d 5" % (tid, seq))
            for l in lines[:-1]:
                if re.search(pattern, l):
                    return l, int(l.split()[2])
            seq = int(lines[-1].split()[1])
        raise AssertionError("no event %r at table %s" % (pattern, tid))

    def close(self):
        self.f.close()
        self.s.close()


# ---- with a server ---------------------------------------------------------------------------------

@slow
class WithAServer(WebPlayers, unittest.TestCase):
    """A core (sign-in none), the web, the GameMaster extension and the bot."""

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.web, cls.gm, cls.tel = [free_port() for _ in range(5)]
        cls.keys = make_keys("web", "gamemaster", "bot", "telnet")
        args = stack_args(cls.core, cls.fe, cls.keys)[1:] + ["--auth", "none", "--sweep", "0.3"]
        cls.procs = procs.owned(cls)
        cls.procs.append(cls.spawn("tests.matrix_core", *args))
        cls.procs.append(cls.spawn(*adapter_args("adapters.web", cls.web, cls.fe, cls.keys, "web")))
        cls.procs.append(cls.spawn(*adapter_args("adapters.telnet", cls.tel, cls.fe, cls.keys, "telnet")))
        cls.gm_out = []
        cls.gm_proc = cls.spawn(*adapter_args("adapters.gamemaster", cls.gm, cls.fe, cls.keys, "gamemaster"),
                                "--wait", "6",
                                "--linger", "1", "--turn", "1h", "--opening-secs", "2", out=cls.gm_out)
        cls.procs.append(cls.gm_proc)
        cls.procs.append(cls.spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key",
                                   os.path.join(cls.keys, "bot.key"), "--think", "0,0", "--pace", "0",
                                   "--poll", "0.3"))
        cls.wait_for_bot()

    @staticmethod
    def spawn(*args, out=None):
        import subprocess
        p = procs.spawn(*args, stderr=subprocess.STDOUT if args[0] in ("bot", "adapters.gamemaster")
                        else subprocess.DEVNULL, out=out)
        if out is not None:
            out.insert(0, p.first)
        return p

    @classmethod
    def wait_for_bot(cls):
        end = time.time() + 20
        while time.time() < end:
            n = Native(cls.core, "botcheck")
            lines = n.req("WHO")
            n.close()
            if any(l.startswith("PLR bot ") and l.endswith(" bot") for l in lines):
                return
            time.sleep(0.3)
        raise AssertionError("the bot did not sign in")

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def log_has(self, text):
        end = time.time() + 5
        while time.time() < end:
            if any(text in l for l in self.gm_out):
                return True
            time.sleep(0.05)
        return False

    def gm_table(self, watcher, name, key):
        """The table the GameMaster player sits at."""
        end = time.time() + TIMEOUT
        while time.time() < end:
            t = next((t for t in watcher.tables() if t["key"] == key and name in t["names"]), None)
            if t:
                return t
            time.sleep(0.1)
        raise AssertionError("no %s table for %s" % (key, name))

    def finished(self, m, tid, watcher, pattern):
        """The Mac gets the hang-up after the game (the linger is a second here); the table ended so."""
        self.assertEqual(m.expect(GM.HANG_UP, 10), [])
        self.assertIsNone(m.read(5))
        watcher.req("SEE %s" % tid)
        lines = watcher.req("SIN %s 0" % tid)
        end = [l for l in lines if " END " in l]
        self.assertTrue(end and re.search(pattern, end[0]), lines[-5:])
        return end[0]

    # ---- the tunnel ----------------------------------------------------------------------------
    def tunnel(self, name):
        t = Tunnel(self.gm)
        self.addCleanup(t.close)
        self.assertTrue(t.greeting.startswith("PARLORNET/1 "), t.greeting)
        self.assertEqual(t.req("HELO %s" % name)[-1], "OK -")
        return t

    def game(self, name, rb_id, version, oldest, state, who="bot"):
        """name signs in on a tunnel of its own and starts one game there (GMO 1 who, the game on 2): -> the
        game, at ~Just a moment, I'll see."""
        g = TunnelGame(self.tunnel(name), 1, 2, who)
        self.assertEqual(g.call(rb_id, version, oldest, state), GM.SEND_GAME)
        self.assertEqual(g.expect(GM.JUST_A_MOMENT), [])
        return g

    def test_tunnel_sign_in_and_lobby(self):
        """The rulebook's connection: the core's greeting, nothing but the sign-in before it (a call is
        closed at once), then the lobby's relayed requests; the session id never reaches the Mac."""
        t = Tunnel(self.gm)
        self.addCleanup(t.close)
        self.assertTrue(t.greeting.startswith("PARLORNET/1 "))
        t.line("GMO 1 bot")
        self.assertTrue(t.wait(lambda: 1 in t.closed))
        self.assertTrue(t.req("WHO")[-1].startswith("ERR NOHELO"))
        self.assertEqual(t.req("HELO tunlob")[-1], "OK -")
        who = t.req("WHO")
        self.assertTrue(any(l.startswith("PLR bot ") for l in who), who)
        self.assertTrue(any(l.startswith("PLR tunlob ") for l in who), who)
        self.assertTrue(t.req("NEW reversi standard")[-1].startswith("ERR UNKNOWN"))
        self.assertTrue(t.req("GMO 9 bot")[-1].startswith("ERR SYNTAX"))
        self.assertEqual(t.req("BYE"), ["OK"])

    def test_tunnel_two_games_against_the_bot(self):
        """Othello and BattleShips at once over one tunnel, each against the bot: both tables exist together,
        then each game is played to its end and hung up after it. The answer to a tunneled call carries no
        address, only the call's number."""
        t = self.tunnel("tunboth")
        oth, bat = TunnelGame(t, 1, 2), TunnelGame(t, 3, 4)
        self.assertEqual(oth.call("Othello [RHS]", 0x0020, 0x0020, othello_state()), GM.SEND_GAME)
        self.assertEqual(oth.addr, GM.address_bytes("0.0.0.0", GM.TUNNEL_PORT + 1))
        self.assertEqual(oth.expect(GM.JUST_A_MOMENT), [])
        self.assertEqual(oth.expect(GM.PLAY_IT_SAM), [])
        self.assertEqual(bat.call("BattleShips [RHS]", 0x0020, 0x0020, bytes(2400)), GM.SEND_GAME)
        self.assertEqual(bat.addr, GM.address_bytes("0.0.0.0", GM.TUNNEL_PORT + 3))
        bat.expect(GM.JUST_A_MOMENT)
        self.assertEqual(bat.expect(GM.PLAY_IT_SAM), [])
        w = Native(self.core, "tunboth")
        self.addCleanup(w.close)
        to = self.gm_table(w, "tunboth", "reversi/1")
        tb = self.gm_table(w, "tunboth", "navalcombat/1")
        self.assertEqual((to["seated"], tb["seated"]), (["tunboth", "bot"], ["tunboth", "bot"]))
        result = self.play_othello(oth)
        self.finished(oth, to["id"], w, r"END (win|draw) .*%s" % re.escape(result.split()[-1]))
        result = self.play_battleships(bat)
        self.assertTrue(result.startswith("win 0 17-"), result)
        self.finished(bat, tb["id"], w, r"END win 0 17-")

    # ---- Checkers, ParlorNet's own rulebook ----------------------------------------------------------
    def checkers_game(self, name, who="bot"):
        """A Checkers game through the tunnel: the call, the table, and the Mac at seat 0 (Black)."""
        t = self.tunnel(name)
        ck = TunnelGame(t, 1, 2, who)
        self.assertEqual(ck.call("Checkers [ParlorNet]", 0x0100, 0x0100, checkers_state()), GM.SEND_GAME)
        self.assertEqual(ck.expect(GM.JUST_A_MOMENT), [])
        self.assertEqual(ck.expect(GM.PLAY_IT_SAM), [])
        w = Native(self.core, name)
        self.addCleanup(w.close)
        return ck, w, self.gm_table(w, name, "checkers/1")

    def test_checkers_against_the_bot_through_the_tunnel(self):
        """A scripted Checkers rulebook plays a whole game against the bot. Each of its moves is shown only once
        the parlor answers A; a move the core refuses comes back as R with the reason, and changes nothing.
        The bot's turns arrive as M, whole, and are answered A; the end comes as E with the core's words."""
        ck, w, t = self.checkers_game("tunck")
        self.assertEqual((t["seated"], t["rules"]), (["tunck", "bot"], "american"))
        g = LocalGame("checkers", "american", 2, 0)
        rnd = random.Random(5)
        ck.send("M 1-5")                                   # onto its own man: refused
        got = ck.read()
        self.assertEqual(got[1][:2], "R ", got)
        ck.send("M 22-18")                                 # White's man: refused too
        self.assertEqual(ck.read()[1][:2], "R ")
        turns = 0
        while not g.result:
            seat = g.state["turn"]
            if seat == 0:
                move = checkers_turn(g, 0, rnd)
                ck.send("M " + move)
                got = ck.read()
                self.assertEqual(got, (False, "A " + move))
            else:
                got = ck.read()
                self.assertIsNotNone(got, "closed in the middle of the game")
                self.assertRegex(got[1], r"^M \d+(-\d+|(x\d+)+)\Z")
                move = got[1][2:]
                ck.send("A " + move)
            self.assertTrue(checkers_play(g, seat, move).startswith("OK"), (seat, move))
            turns += 1
        got = ck.read()
        self.assertEqual(got, (False, "E " + g.result), got)
        self.assertGreater(turns, 10)
        ck.send("M 9-13")                                  # after the end: refused, the game is over
        self.assertEqual(ck.read(), (False, "R the game is over"))
        self.finished(ck, t["id"], w, r"END %s" % re.escape(g.result))

    def test_checkers_draw_offer_and_resignation(self):
        """D offers a draw: the bot, not ahead, accepts (DY), and the end follows (E draw agreed). In a second
        game G resigns, and E says so."""
        ck, w, t = self.checkers_game("tundraw")
        ck.send("D")
        self.assertEqual(ck.read(), (False, "DY"))
        self.assertEqual(ck.read(), (False, "E draw agreed"))
        ck.send("E draw agreed")                           # the rulebook's answer to DY: ignored
        self.finished(ck, t["id"], w, r"END draw agreed")
        ck, w, t = self.checkers_game("tunres")
        ck.send("M 11-15")
        self.assertEqual(ck.read(), (False, "A 11-15"))
        self.assertRegex(ck.read()[1], r"^M \d+-\d+\Z")
        ck.send("G")
        self.assertEqual(ck.read(), (False, "E resign 0"))
        self.assertEqual(ck.expect(GM.HANG_UP, 10), [])

    def test_mancala_against_the_bot_through_the_tunnel(self):
        """A scripted Mancala rulebook plays a whole game of kalah against the bot: a pit at a time, each shown
        once the parlor answers A, another move after a last seed in the store, and the bot's moves as M, one
        a pit. An empty pit is refused (R)."""
        t = self.tunnel("tunmc")
        mc = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(mc.call("Mancala [ParlorNet]", 0x0100, 0x0100,
                                 parlor_state(b"PNmc", bytes([4] * 6 + [0] + [4] * 6 + [0]))), GM.SEND_GAME)
        mc.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tunmc")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tunmc", "mancala/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tunmc", "bot"], "kalah"))
        g = LocalGame("mancala", "kalah", 2, 0)
        rnd = random.Random(4)
        mc.send("M 3")                                     # pit 3 holds 4: sown, the last seed in the store
        self.assertEqual(mc.read(), (False, "A 3"))
        self.assertTrue(g.apply(0, ["3"], False).startswith("OK"))
        mc.send("M 3")                                     # now empty: refused
        self.assertEqual(mc.read()[1][:2], "R ")
        while not g.result:
            seat = g.state["turn"]
            if seat == 0:
                move = rnd.choice(g.m.op_moves({"rules": "kalah", "state": g.state, "seat": 0})["moves"])
                mc.send("M " + move)
                self.assertEqual(mc.read(), (False, "A " + move))
            else:
                got = mc.read()
                self.assertIsNotNone(got, "closed in the middle of the game")
                self.assertRegex(got[1], r"^M [1-6]\Z")
                move = got[1][2:]
                mc.send("A " + move)
            self.assertTrue(g.apply(seat, [move], False).startswith("OK"), (seat, move))
        self.assertEqual(mc.read(), (False, "E " + g.result))
        self.finished(mc, tab["id"], w, r"END %s" % re.escape(g.result))

    def test_five_in_a_row_against_the_bot_through_the_tunnel(self):
        """A scripted Five in a Row rulebook plays Black against the bot to the end: a taken point is refused,
        each stone shows once the parlor answers A, the bot's stones come as M."""
        t = self.tunnel("tunfr")
        fr = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(fr.call("Five in a Row [ParlorNet]", 0x0100, 0x0100, parlor_state(b"PNfr", bytes(232))),
                         GM.SEND_GAME)
        fr.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tunfr")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tunfr", "fiveinarow/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tunfr", "bot"], "freestyle"))
        g = LocalGame("fiveinarow", "freestyle", 2, 0)
        rnd = random.Random(6)
        free = ["%s%d" % (chr(97 + x), y + 1) for x in range(15) for y in range(15)]
        taken = []
        while not g.result:
            seat = g.state["turn"]
            if seat == 0:
                if len(taken) == 2:                         # a point already taken: refused
                    fr.send("M " + taken[1])
                    self.assertEqual(fr.read()[1][:2], "R ")
                move = rnd.choice(free)
                fr.send("M " + move)
                self.assertEqual(fr.read(), (False, "A " + move))
            else:
                got = fr.read()
                self.assertIsNotNone(got, "closed in the middle of the game")
                self.assertRegex(got[1], r"^M [a-o]\d{1,2}\Z")
                move = got[1][2:]
                fr.send("A " + move)
            self.assertTrue(g.apply(seat, [move], False).startswith("OK"), (seat, move))
            free.remove(move)
            taken.append(move)
        self.assertEqual(fr.read(), (False, "E " + g.result))
        self.finished(fr, tab["id"], w, r"END %s" % re.escape(g.result))

    def test_grid_words_against_the_bot_through_the_tunnel(self):
        """A scripted Grid Words rulebook against the bot: the parlor deals (K), keeps the scores (S) and the bag
        (B); a play of tiles not on the rack is refused; a word from the rack across the center is accepted
        and scored; then the Mac exchanges and passes while the bot plays words, until the game ends."""
        t = self.tunnel("tungw")
        gw = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(gw.call("Grid Words [ParlorNet]", 0x0100, 0x0100, parlor_state(b"PNgw", bytes(260))),
                         GM.SEND_GAME)
        gw.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tungw")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tungw", "gridwords/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tungw", "bot"], "standard"))
        state = {}

        def told():
            """K, S and B, as the extension sends them after a move."""
            for verb in ("K", "S", "B"):
                got = gw.read()
                self.assertEqual(got[1].split()[0], verb, got)
                state[verb] = got[1][2:]
        self.assertEqual(self.board_data(gw), self.standard_board())     # the table's board, once at the start
        told()
        rack = state["K"]
        self.assertEqual((len(rack), state["S"], state["B"]), (7, "0 0", "86"))
        gw.send("M PLAY 8 8 H ZZZZ")                     # tiles not on the rack
        self.assertRegex(gw.read()[1], r"^R Can't play ZZZZ across from h8: .+\.\Z")
        from bot.rules import plugin
        words = plugin("gridwords").load_words()
        letters = sorted(rack.replace("_", ""))
        word = next(x for x in sorted(words, key=len, reverse=True)
                    if 2 <= len(x) <= 7 and all(x.count(c) <= letters.count(c) for c in set(x)))
        gw.send("M PLAY %d 8 H %s" % (9 - len(word), word))
        self.assertEqual(gw.read(), (False, "A PLAY %d 8 H %s" % (9 - len(word), word)))
        told()
        self.assertGreater(int(state["S"].split()[0]), 0)
        turns = 0
        while True:
            got = gw.read(60)
            self.assertIsNotNone(got, "closed in the middle of the game")
            if got[1].startswith("E "):
                break
            self.assertRegex(got[1], r"^M (PLAY \d+ \d+ [HV] [A-Za-z]+|EXCH \d|PASS)\Z")
            gw.send("A " + got[1][2:])
            told()
            try:
                got = gw.read(1)                            # the end, when the bot's play ended the game
                break
            except socket.timeout:
                pass
            turns += 1
            if turns % 2 and int(state["B"]) >= 7:
                move = "EXCH " + state["K"][:2]
            else:
                move = "PASS"
            gw.send("M " + move)
            got = gw.read(60)
            if got[1].startswith("E "):
                break
            self.assertEqual(got, (False, "A " + move))
            told()
        self.assertRegex(got[1], r"^E (win \d|draw) -?\d+/-?\d+\Z")
        self.finished(gw, tab["id"], w, r"END %s" % re.escape(got[1][2:]))

    def test_rummy_tile_against_the_bot_through_the_tunnel(self):
        """A scripted Rummy Tile rulebook against the bot: the parlor deals and sends the whole state (Z, the
        sets, the rack, TP, P, C, I, W); a step is played at once and answered A with the state, a step the core
        refuses is answered R; UND takes it back; END with nothing played draws a tile. After some turns the
        Mac resigns."""
        t = self.tunnel("tunrt")
        rt = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(rt.call("Rummy Tile [ParlorNet]", 0x0100, 0x0100, parlor_state(b"PNrt", bytes(800))),
                         GM.SEND_GAME)
        rt.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tunrt")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tunrt", "rummytile/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tunrt", "bot"], "standard"))

        def state():
            """One whole state: Z ... W -> {verb: [lines]}."""
            got = rt.read()
            self.assertEqual(got, (False, "Z"))
            out = {}
            while True:
                got = rt.read(30)
                self.assertIsNotNone(got)
                out.setdefault(got[1].split()[0], []).append(got[1])
                if got[1].startswith("W "):
                    return out
        st = state()
        rack = " ".join(l[2:] for l in st["H"]).split()
        self.assertEqual((len(rack), st["P"], st["C"], st["W"]), (14, ["P 78"], ["C 14 14"], ["W 0"]))
        rt.send("M %s-99" % rack[0])                      # no set 99
        self.assertEqual(rt.read()[1][:2], "R ")
        rt.send("M %s-n" % rack[0])                       # a set of one: played, not yet valid
        self.assertEqual(rt.read(), (False, "A %s-n" % rack[0]))
        st = state()
        self.assertEqual(st["ST"], ["ST 1 x* %s" % rack[0]])
        self.assertEqual(st["TP"], ["TP 1 0 sets"])          # one tile played; no valid set yet
        rt.send("M UND")
        self.assertEqual(rt.read(), (False, "A UND"))
        self.assertNotIn("ST", state())
        for turn in range(4):
            rt.send("M END")                               # nothing played: a tile is drawn
            self.assertEqual(rt.read(), (False, "A END"))
            st = state()                                   # the turn passed to the bot
            if st["W"] == ["W 0"]:
                continue
            st = state()                                   # and came back after its turn
            self.assertEqual(st["W"], ["W 0"])
        self.assertGreaterEqual(len(" ".join(l[2:] for l in st["H"]).split()), 15)
        rt.send("G")
        got = rt.read()
        while got[1][0] in "ZSHTPCIW":
            got = rt.read()
        self.assertEqual(got, (False, "E resign 0"))

    def test_cannon_fire_against_the_bot_through_the_tunnel(self):
        """A scripted Cannon Fire rulebook against the bot: the state comes from the parlor (the terrain, the
        tanks, the wind, whose turn), a drive left is played and a second drive in the turn refused, and every
        shot, the Mac's and the bot's, comes as F and its path; the Mac fires until the game ends."""
        t = self.tunnel("tuncf")
        cf = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(cf.call("Cannon Fire [ParlorNet]", 0x0100, 0x0100, parlor_state(b"PNcf", bytes(400))),
                         GM.SEND_GAME)
        cf.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tuncf")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tuncf", "cannonfire/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tuncf", "bot"], "classic"))

        def until_w():
            seen = []
            while True:
                got = cf.read(30)
                self.assertIsNotNone(got, "closed in the middle of the game")
                seen.append(got[1])
                if got[1].startswith(("W 0", "W 1", "E ")):     # W -1: the end follows
                    return seen
        seen = until_w()
        self.assertTrue(any(l.startswith("Z 0 ") for l in seen), seen)
        self.assertEqual(sorted(l.split()[1] for l in seen if l.startswith("K ")), ["0", "1"])
        self.assertEqual(len([l for l in seen if l.startswith("AIM ")]), 2)    # the view's A: A answers moves
        self.assertFalse([l for l in seen if l.startswith("A ")])
        self.assertEqual(seen[-1], "W 0")
        def until_answer(move):
            """The lines before A move: A, which ends the Mac's wait, comes after the shot and the state."""
            seen = []
            while True:
                got = cf.read(30)
                self.assertIsNotNone(got, "closed in the middle of the game")
                if got[1] == "A " + move:
                    return seen
                self.assertFalse(got[1].startswith("R "), (move, got))
                seen.append(got[1])
        rnd = random.Random(8)
        moved = shots = 0
        while True:
            if not moved:
                cf.send("M MOVE L 999")
                got = cf.read()
                if got[1][:2] == "R ":                      # at the edge, or blocked
                    pass
                else:
                    seen = [got[1]] + until_answer("MOVE L 999")
                    self.assertEqual(seen[-1], "W 0", seen)
                    cf.send("M MOVE R 3")                   # one drive a turn
                    self.assertEqual(cf.read()[1][:2], "R ")
                moved = 1
            move = "FIRE %d %d" % (rnd.randint(20, 160), rnd.randint(30, 90))
            cf.send("M " + move)
            seen = until_answer(move)
            self.assertIn("F 0", seen)                      # the shot, then the state (W), then A
            self.assertTrue(seen[seen.index("F 0") + 1].startswith("TRJ "), seen)
            self.assertTrue(seen.index("F 0") < max(i for i, l in enumerate(seen) if l.startswith("W ")), seen)
            shots += 1
            if seen[-1] != "W 1":                           # the shot ended the game
                got = cf.read(30)
                self.assertTrue(got and got[1].startswith("E "), got)
                seen = [got[1]]
                break
            seen = until_w()                                # the bot's shot
            self.assertIn("F 1", seen)
            if seen[-1].startswith("E "):
                break
            self.assertEqual(seen[-1], "W 0")
        self.assertRegex(seen[-1], r"^E (win \d|draw) \d+-\d+\Z")
        self.assertGreater(shots, 1)
        self.finished(cf, tab["id"], w, r"END %s" % re.escape(seen[-1][2:]))

    def test_backgammon_against_the_bot_through_the_tunnel(self):
        """A scripted Backgammon rulebook against the bot: H and the state (BD ... W) come first. When its turn
        starts the Mac rolls, or once doubles (the bot takes), stages a whole turn by the rules and sends it; the A
        that answers comes after the state the move leads to. A step the core refuses is answered R, with nothing
        played. The bot's turns arrive as the state, with LM, the turn it played. The game is played to its end;
        H left is then kept with the player, and the next game starts with it and is resigned."""
        from bot.rules import plugin
        m = plugin("backgammon")
        t = self.tunnel("tunbg")
        bg = TunnelGame(t, 1, 2, "bot")
        self.assertEqual(bg.call("Backgammon [ParlorNet]", 0x0100, 0x0100, backgammon_state()), GM.SEND_GAME)
        bg.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, "tunbg")
        self.addCleanup(w.close)
        tab = self.gm_table(w, "tunbg", "backgammon/1")
        self.assertEqual((tab["seated"], tab["rules"]), (["tunbg", "bot"], "standard"))
        self.assertEqual(bg.read(), (False, "H right"))
        seen = []

        def until(answer=None):
            """The lines up to the end of a state (W), or up to A answer -> (the last whole state, E's result)."""
            st, cur = None, {}
            while True:
                got = bg.read(30)
                self.assertIsNotNone(got, "closed in the middle of the game")
                line = got[1]
                seen.append(line)
                if line.startswith("E "):
                    return st, line[2:]
                if answer is not None and line == "A " + answer:
                    return st, None
                self.assertFalse(line.startswith(("R ", "M ")), (answer, line))
                cur[line.split()[0]] = line
                if line.startswith("W "):
                    self.assertEqual(list(cur)[:6], ["BD", "CU", "DI", "DR", "N", "CA"], cur)
                    st, cur = cur, {}
                    if answer is None:
                        return st, None

        def board(st):
            f = [int(x) for x in st["BD"].split()[1:]]
            return {"pts": f[:24], "bar": f[24:26], "off": f[26:28]}

        def pips(b, seat):
            s = 1 if seat == 0 else -1
            return 25 * b["bar"][seat] + sum((p if seat == 0 else 25 - p) * n * s
                                             for p, n in zip(range(1, 25), b["pts"]) if n * s > 0)

        def a_turn(st, rnd):
            """A whole turn for seat 0 by the rules, chosen at random -> its steps."""
            s2 = dict(board(st), turn=0, dice=[int(x) for x in st["DI"].split()[1:]], steps=[])
            toks = []
            while s2["off"][0] < 15:
                lg = sorted(m.legal_steps(s2).items(), key=str)
                if not lg:
                    break
                (frm, to), d = lg[rnd.randrange(len(lg))]
                nb, _ = m.apply_step(m.board_of(s2), 0, frm, to)
                dice = list(s2["dice"])
                dice.remove(d)
                s2 = dict(s2, pts=nb["pts"], bar=nb["bar"], off=nb["off"], dice=dice, steps=s2["steps"] + [1])
                toks.append(m.tok(frm, to))
            return toks

        rnd = random.Random(11)
        st, end = until()
        doubled = refused = False
        turns = 0
        while end is None:
            if st["W"] != "W 0":
                st, end = until()
                continue
            q = st["Q"].split()[1]
            if q == "roll":
                b = board(st)
                word = "roll"
                if not doubled and st["CA"] == "CA 1 1" and pips(b, 1) <= pips(b, 0) * 1.12 + 2:
                    word, doubled = "double", True
                bg.send("M " + word)
                got, end = until(word)
                st = got or st
                if word == "double":
                    self.assertEqual((st["Q"], st["W"]), ("Q take", "W 1"))
            else:
                self.assertEqual(q, "move")
                n = int(st["N"].split()[1])
                if not refused:
                    bg.send("M 1-2")
                    got = bg.read()
                    self.assertEqual(got[1][:2], "R ", got)
                    refused = True
                toks = a_turn(st, rnd)
                self.assertTrue(len(toks) == n or board(st)["off"][0] + len(toks) == 15, (toks, st))
                move = " ".join(toks)
                bg.send("M " + move)
                got, end = until(move)
                st = got or st
                turns += 1
        self.assertTrue(doubled)
        self.assertIn("CU 2 1", seen)                       # the bot took the cube at 2
        self.assertTrue(any(l.startswith("LM 1 ") for l in seen), seen[-20:])
        self.assertRegex(end, r"^win [01] (2|4|6) (single|gammon|backgammon)\Z")
        self.assertGreater(turns, 10)
        bg.send("H left")                                   # after the end: kept all the same
        self.finished(bg, tab["id"], w, r"END %s" % re.escape(end))
        bg = TunnelGame(t, 3, 4, "bot")
        self.assertEqual(bg.call("Backgammon [ParlorNet]", 0x0100, 0x0100, backgammon_state()), GM.SEND_GAME)
        bg.expect(GM.PLAY_IT_SAM)
        self.assertEqual(bg.read(), (False, "H left"))
        bg.send("H right")
        bg.send("G")
        got = bg.read()
        while not got[1].startswith("E "):
            got = bg.read()
        self.assertEqual(got, (False, "E resign 0"))

    def test_tunnel_end_ends_its_games(self):
        """When the rulebook's connection ends, its game is resigned and its sign-in ends; a call outside the
        tunnel from the same address and Mac is not taken for the tunnel's player."""
        t = self.tunnel("tunend")
        oth = TunnelGame(t, 1, 2)
        oth.call("Othello [RHS]", 0x0020, 0x0020, othello_state())
        oth.expect(GM.PLAY_IT_SAM)
        self.assertEqual(stock_call(self.gm, "Othello [RHS]", 0x0020, 0x0020, 122, machine="Tunnel Mac"),
                         ("~" + GM.DONT_SEND_CODE, True))
        w = Native(self.core, "tunend")
        self.addCleanup(w.close)
        tid = self.gm_table(w, "tunend", "reversi/1")["id"]
        oth.send("23")                      # d3... as c4: white's opening move
        t.close()
        line, _ = w.until_event(tid, r" END ")
        self.assertIn("resign 0", line)
        self.assertTrue(self.log_has("tunend's tunnel ended"))

    # ---- what the turns were played with (CLI) -----------------------------------------------------------
    def first_turn(self, t, name):
        """name, signed in on tunnel t, plays Checkers' first move against the bot through it: -> the TRX lines
        of that turn, as the core records it (TRN, seen by the player, seated at the private table)."""
        ck = TunnelGame(t, 1, 2)
        self.assertEqual(ck.call("Checkers [ParlorNet]", 0x0100, 0x0100, checkers_state()), GM.SEND_GAME)
        ck.expect(GM.PLAY_IT_SAM)
        w = Native(self.core, name)
        self.addCleanup(w.close)
        tid = self.gm_table(w, name, "checkers/1")["id"]
        ck.send("M 11-15")
        self.assertEqual(ck.read(), (False, "A 11-15"))
        lines = w.req("TRN %s" % tid)
        self.assertTrue(lines[-1].startswith("OK"), lines)
        at = next(i for i, l in enumerate(lines) if l.startswith("TRN ") and l.split()[5] == name)
        out = []
        for l in lines[at + 1:]:
            if not l.startswith("TRX "):
                break
            out.append(l)
        return out

    def test_the_rulebook_says_what_it_is_and_the_mac(self):
        """The rulebook from 0.4 sends CLI PROG and CLI MACH once signed in; the tunnel answers OK as the core
        does, cleans them as the core does, lists them for CLI alone, and its games' turns are recorded with
        them."""
        t = self.tunnel("tuncli")
        self.assertEqual(t.req("CLI PROG GameMaster 1.0.0 + ParlorNet 0.4"), ["OK"])
        self.assertEqual(t.req("CLI MACH  Macintosh\tSE/30 "), ["OK"])
        self.assertEqual(t.req("CLI"), ["CLI PROG GameMaster 1.0.0 + ParlorNet 0.4", "CLI MACH Macintosh SE/30",
                                        "OK"])
        self.assertTrue(t.req("CLI WHAT x")[-1].startswith("ERR SYNTAX"))
        trx = self.first_turn(t, "tuncli")
        self.assertIn("TRX PROG GameMaster 1.0.0 + ParlorNet 0.4", trx)
        self.assertIn("TRX MACH Macintosh SE/30", trx)

    def test_a_rulebook_that_says_nothing_is_gamemaster_on_a_macintosh(self):
        """A rulebook before 0.4 sends no CLI: the turns say GameMaster, with the version its call record
        carries, on a Macintosh. One that names only its program gets the Macintosh too."""
        trx = self.first_turn(self.tunnel("tunold"), "tunold")
        self.assertIn("TRX PROG %s 1.0.0" % GM.CLIENT_NAME, trx)
        self.assertEqual(GM.CLIENT_NAME, "GameMaster")
        self.assertIn("TRX MACH Macintosh", trx)
        t = self.tunnel("tunprog")
        self.assertEqual(t.req("CLI PROG GameMaster + ParlorNet 0.4"), ["OK"])
        trx = self.first_turn(t, "tunprog")
        self.assertIn("TRX PROG GameMaster + ParlorNet 0.4", trx)
        self.assertIn("TRX MACH Macintosh", trx)

    # ---- resuming a game being played ------------------------------------------------------------------
    def bot_table(self, name, key, rules, seat=0):
        """A table of name's against the bot, started on the core's own port (as the web and telnet start
        theirs): name at seat, the bot at the other. -> (the player's connection, the table)."""
        w = Native(self.core, name)
        self.addCleanup(w.close)
        final = w.req("NEW %s %s turn=1h" % (key, rules))[-1]
        self.assertTrue(final.startswith("OK "), final)
        tid = final.split()[1]
        self.assertTrue(w.req("JON %s %d" % (tid, seat))[-1].startswith("OK"))
        self.assertTrue(w.req("INV %s bot %d" % (tid, 1 - seat))[-1].startswith("OK"))
        w.until_event(tid, r" START")
        return w, tid

    def my_turn(self, w, tid, seat):
        """Until the view says seat moves; -> the view's lines (without OK)."""
        end = time.time() + TIMEOUT
        while time.time() < end:
            view = w.req("VUE %s" % tid)
            if "W %d" % seat in view:
                return view[:-1]
            time.sleep(0.1)
        raise AssertionError("never seat %d's turn at table %s" % (seat, tid))

    @staticmethod
    def record(w, tid):
        """The core's record of a table: its LST line and every event."""
        tab = [l for l in w.req("LST") if l.startswith("TAB %s " % tid)]
        return tab, w.req("SIN %s 0" % tid)[:-1]

    def resumed(self, name, rb_id, magic, data, tid, version=0x0110):
        """The lobby's Resume: GMO 1 @TABLE through the tunnel, the call with a new game, ~Play it Sam. -> (the
        tunnel, the game)."""
        t = self.tunnel(name)
        g = TunnelGame(t, 1, 2, "@%s" % tid)
        self.assertEqual(g.call(rb_id, version, 0x0100, parlor_state(magic, data)), GM.SEND_GAME)
        self.assertEqual(g.expect(GM.JUST_A_MOMENT), [])
        self.assertEqual(g.expect(GM.PLAY_IT_SAM), [])
        return t, g

    def board_data(self, g):
        """SQ and LV, the Grid Words board and letter values sent before the position or the first K."""
        return [g.read()[1], g.read()[1]]

    @staticmethod
    def standard_board():
        """SQ and LV of the standard ruleset, as the extension makes them from its geometry."""
        from bot.rules import plugin
        P = plugin("gridwords")
        return GM.geometry_lines(P.geometry(P.RULESETS["standard"]))

    def position(self, g):
        """POS ... POE, the first thing after ~Play it Sam -> its lines."""
        got = g.read()
        self.assertTrue(got[1].startswith("POS "), got)
        lines = [got[1]]
        while lines[-1] != "POE":
            got = g.read()
            self.assertIsNotNone(got)
            lines.append(got[1])
        return lines

    @staticmethod
    def grid_words_position(view):
        """What the core's view of a Grid Words table says, as the Mac must be sent it (seat 0, to move)."""
        cells, scores, last = ["."] * 225, {}, []
        for l in view:
            f = l.split()
            if f[0] == "X":
                for i in range(1, len(f), 3):
                    cells[(int(f[i + 1]) - 1) * 15 + int(f[i]) - 1] = f[i + 2]
            elif f[0] == "T":
                scores[f[1]] = f[2]
            elif f[0] == "LAST":
                last = ["LP %s %s %s %d" % (f[2], f[3], f[4], len(f[5]))]
        rack = next(l[2:] for l in view if l.startswith("R "))
        bag = next(l[4:] for l in view if l.startswith("BAG "))
        return ["POS 0 0 0", "BD " + "".join(cells), "K " + rack, "S %s %s" % (scores["0"], scores["1"]),
                "B " + bag] + last + ["POE"]

    def test_grid_words_started_by_telnet_resumed_in_gamemaster(self):
        """A Grid Words game started by telnet against the bot, two moves played there, is taken up from
        GameMaster: the first thing the Mac is sent is the whole position, exactly as the core's view has it
        (the board with blanks in lower case, the rack, both scores, the bag, the last play, whose turn). The
        table's record does not change. The Mac then plays, the bot answers, and the Mac's hang-up leaves the
        game at its table, still being played."""
        from test_matrix import typed
        w = Native(self.core, "gwback")                 # the account (sign-in none: a name signs up)
        self.addCleanup(w.close)
        c = Client(self.tel, enc="latin-1", display="4")
        self.addCleanup(c.s.close)
        tel = TelnetPlain(c)
        tel.until("Login")
        c.send("gwback\r\n")
        tel.until("Press Enter")
        c.send("\r\n")
        tel.until("Your tables")
        c.send("invite bot gridwords standard 2p 1h\r\n")
        tab = self.gm_table(w, "gwback", "gridwords/1")
        tid = tab["id"]
        w.until_event(tid, r" START")
        from bot.rules import plugin
        words = plugin("gridwords").load_words()
        view = self.my_turn(w, tid, 0)                  # the first move: a word from the rack across the center
        rack = next(l[2:] for l in view if l.startswith("R "))
        letters = sorted(rack.replace("_", ""))
        word = next(x for x in sorted(words, key=len, reverse=True)
                    if 2 <= len(x) <= 7 and all(x.count(ch) <= letters.count(ch) for ch in set(x)))
        tel.until("table %s" % tid)
        c.send("/" + typed("gridwords/1", "PLAY %d 8 H %s" % (9 - len(word), word)) + "\r\n")
        _, seq = w.until_event(tid, r" PLAY 0 ")
        view = self.my_turn(w, tid, 0)                  # the bot played; the second: an exchange
        rack = next(l[2:] for l in view if l.startswith("R "))
        c.send("/exchange %s\r\n" % rack[:2])
        w.until_event(tid, r" EXCH 0 ", seq)
        view = self.my_turn(w, tid, 0)
        before = self.record(w, tid)
        want = self.grid_words_position(view)
        self.assertGreater(225 - want[1].count("."), len(word) - 1)
        t, gw = self.resumed("gwback", "Grid Words [ParlorNet]", b"PNgw", bytes(260), tid)
        self.assertEqual(self.board_data(gw), self.standard_board())
        self.assertEqual(self.position(gw), want)
        self.assertEqual(self.record(w, tid), before)  # nothing changed at the table
        self.assertEqual(self.gm_table(w, "gwback", "gridwords/1")["seated"], ["gwback", "bot"])
        gw.send("M PASS")                               # the Mac plays; the bot answers
        self.assertEqual(gw.read(), (False, "A PASS"))
        for verb in ("K", "S", "B"):
            self.assertEqual(gw.read()[1].split()[0], verb)
        got = gw.read(30)
        self.assertRegex(got[1], r"^M (PLAY \d+ \d+ [HV] [A-Za-z]+|EXCH \d|PASS)\Z")
        gw.send("A " + got[1][2:])
        gw.ctl(GM.HANG_UP)
        self.assertTrue(self.log_has("the table %s plays on" % tid))
        t.close()
        time.sleep(0.5)
        tab = next(x for x in w.tables() if x["id"] == tid)
        self.assertEqual(tab["status"], "playing")
        self.assertFalse([l for l in w.req("SIN %s 0" % tid) if " END " in l])

    def test_grid_words_from_telnet_listed_and_resumed(self):
        """A Grid Words game started by telnet against the bot is listed in TBL's Your games under the
        rulebook's ruleset and taken up through the tunnel with the table's board and values and the exact
        position. An open table and an invitation to one reach the lobby the same way. A Mancala table of oware,
        which plays differently, is neither listed, taken up nor joined."""
        from test_matrix import typed
        w = Native(self.core, "gweng")
        self.addCleanup(w.close)
        c = Client(self.tel, enc="latin-1", display="4")
        self.addCleanup(c.s.close)
        tel = TelnetPlain(c)
        tel.until("Login")
        c.send("gweng\r\n")
        tel.until("Press Enter")
        c.send("\r\n")
        tel.until("Your tables")
        c.send("invite bot gridwords standard 2p 1h\r\n")
        tab = self.gm_table(w, "gweng", "gridwords/1")
        tid = tab["id"]
        self.assertEqual(tab["rules"], "standard")
        w.until_event(tid, r" START")
        from bot.rules import plugin
        words = plugin("gridwords").load_words()
        view = self.my_turn(w, tid, 0)
        letters = sorted(next(l[2:] for l in view if l.startswith("R ")).replace("_", ""))
        word = next(x for x in sorted(words, key=len, reverse=True)
                    if 2 <= len(x) <= 7 and all(x.count(ch) <= letters.count(ch) for ch in set(x)))
        tel.until("table %s" % tid)
        c.send("/" + typed("gridwords/1", "PLAY %d 8 H %s" % (9 - len(word), word)) + "\r\n")
        w.until_event(tid, r" PLAY 0 ")
        view = self.my_turn(w, tid, 0)                  # the bot has played too
        before = self.record(w, tid)
        # another player's open Grid Words table, with an invitation to it, and an open oware table
        o = Native(self.core, "gwhost")
        self.addCleanup(o.close)
        inv = o.req("NEW gridwords standard turn=1h")[-1].split()[1]
        o.req("JON %s 0" % inv)
        self.assertTrue(o.req("INV %s gweng 1" % inv)[-1].startswith("OK"))
        oware = o.req("NEW mancala oware turn=1h")[-1].split()[1]
        o.req("JON %s 0" % oware)
        t = self.tunnel("gweng")
        lines = t.req("TBL")
        self.assertIn("YG %s gridwords/1 standard 0 0 bot" % tid, lines)
        self.assertIn("WG %s gridwords/1 standard gwhost" % inv, lines)
        self.assertFalse([l for l in lines if l.split()[1:2] == [oware]], lines)
        self.assertTrue([l for l in t.req("IVL") if l.startswith("IVT %s gridwords/1 standard gwhost " % inv)])
        gw = TunnelGame(t, 1, 2, "@%s" % tid)          # Resume in Your games
        self.assertEqual(gw.call("Grid Words [ParlorNet]", 0x0110, 0x0100, parlor_state(b"PNgw", bytes(260))),
                         GM.SEND_GAME)
        self.assertEqual(gw.expect(GM.JUST_A_MOMENT), [])
        self.assertEqual(gw.expect(GM.PLAY_IT_SAM), [])
        self.assertEqual(self.board_data(gw), self.standard_board())
        self.assertEqual(self.position(gw), self.grid_words_position(view))
        self.assertEqual(self.record(w, tid), before)  # nothing changed at the table
        gw.send("M PASS")
        self.assertEqual(gw.read(), (False, "A PASS"))
        # the oware table: Mancala's rulebook plays kalah, so it neither takes that game up nor sits down there
        kalah = parlor_state(b"PNmc", bytes([4] * 6 + [0] + [4] * 6 + [0]))
        mc = TunnelGame(t, 3, 4, "@%s" % oware)
        mc.call("Mancala [ParlorNet]", 0x0110, 0x0100, kalah)
        self.assertEqual(mc.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Table %s is not a game of Mancala here." % oware)
        mc = TunnelGame(t, 5, 6, "#%s" % oware)
        mc.call("Mancala [ParlorNet]", 0x0110, 0x0100, kalah)
        self.assertEqual(mc.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Table %s is no longer waiting for a player." % oware)
        self.assertEqual(next(x for x in o.tables() if x["id"] == oware)["seated"], ["gwhost", "-"])

    def test_checkers_resumed_as_the_second_player(self):
        """A Checkers table where the bot moves first: resumed at seat 1 with no swap, the position says
        so (POS 1), and BD holds the 32 squares the core keeps. A whole move from the Mac is played."""
        w, tid = self.bot_table("ckback", "checkers/1", "american", seat=1)
        view = self.my_turn(w, tid, 1)
        before = self.record(w, tid)
        t, ck = self.resumed("ckback", "Checkers [ParlorNet]", b"PNck", checkers_state()[1024:], tid)
        cells = next(l[2:] for l in view if l.startswith("B "))
        quiet = next(l for l in view if l.startswith("L "))
        self.assertEqual(self.position(ck), ["POS 1 1 0", "BD " + cells, quiet, "POE"])
        self.assertEqual(self.record(w, tid), before)
        g = LocalGame("checkers", "american", 2, 0)
        g.state["cells"] = cells
        g.state["turn"] = 1
        move = checkers_turn(g, 1, random.Random(3))
        ck.send("M " + move)
        self.assertEqual(ck.read(), (False, "A " + move))
        self.assertRegex(ck.read(30)[1], r"^M \d+(-\d+|(x\d+)+)\Z")      # the bot's answer
        self.assertEqual(self.gm_table(w, "ckback", "checkers/1")["seated"], ["bot", "ckback"])

    def test_mancala_and_five_in_a_row_resumed(self):
        """Mancala's position is its 14 counts and the last move; Five in a Row's the 225 points and the last
        stone. Each is taken up after a move made on the core's port, and plays on."""
        w, tid = self.bot_table("mcback", "mancala/1", "kalah")
        self.assertTrue(w.req("MOV %s 1" % tid)[-1].startswith("OK"))
        view = self.my_turn(w, tid, 0)
        t, mc = self.resumed("mcback", "Mancala [ParlorNet]", b"PNmc", bytes([4] * 6 + [0] + [4] * 6 + [0]), tid)
        counts = next(l[2:] for l in view if l.startswith("B "))
        lm = [l for l in view if l.startswith("LM ")]
        self.assertEqual(self.position(mc), ["POS 0 0 0", "BD " + counts] + lm + ["POE"])
        pit = next(str(i + 1) for i, n in enumerate(counts.split()[:6]) if n != "0")
        mc.send("M " + pit)
        self.assertEqual(mc.read(), (False, "A " + pit))
        w, tid = self.bot_table("frback", "fiveinarow/1", "freestyle")
        self.assertTrue(w.req("MOV %s h8" % tid)[-1].startswith("OK"))
        self.my_turn(w, tid, 0)
        t, fr = self.resumed("frback", "Five in a Row [ParlorNet]", b"PNfr", bytes(232), tid)
        pos = self.position(fr)
        self.assertEqual(pos[0], "POS 0 0 0")
        bd = pos[1][3:]
        self.assertEqual((len(bd), bd[7 * 15 + 7], bd.count("1"), bd.count("2")), (225, "1", 1, 1))
        self.assertEqual(pos[2], "LS " + "%s%d" % (chr(97 + bd.index("2") % 15), bd.index("2") // 15 + 1))

    def test_a_move_made_elsewhere_sends_the_position_again(self):
        """The same player keeps the table open on the core's port as well and plays a move there: the Mac's
        board missed it, so at the end of that turn the Mac is sent the whole position; the bot's move then
        comes as a move."""
        w, tid = self.bot_table("frboth", "fiveinarow/1", "freestyle")
        self.my_turn(w, tid, 0)
        t, fr = self.resumed("frboth", "Five in a Row [ParlorNet]", b"PNfr", bytes(232), tid)
        self.position(fr)
        self.assertTrue(w.req("MOV %s a1" % tid)[-1].startswith("OK"))     # played elsewhere
        pos = self.position(fr)                         # at the end of that turn: the whole position
        self.assertEqual((pos[0], pos[1][3], pos[1][3 + 7 * 15 + 7], pos[1].count("2")), ("POS 0 1 0", "1", "0", 0))
        got = fr.read(30)                               # then the bot's stone, as a move
        self.assertRegex(got[1], r"^M [a-o]\d{1,2}\Z")
        fr.send("A " + got[1][2:])
        fr.send("M o15")
        self.assertEqual(fr.read(), (False, "A o15"))
        self.assertRegex(fr.read(30)[1], r"^M [a-o]\d{1,2}\Z")       # the bot's next stone, as a move again

    def test_rummy_tile_and_cannon_fire_resumed(self):
        """Rummy Tile and Cannon Fire take up a table with the state they are sent after every step, between
        POS and POE: the rack and the table's sets; the terrain, the tanks, the wind and the turn."""
        w, tid = self.bot_table("rtback", "rummytile/1", "standard")
        view = self.my_turn(w, tid, 0)
        t, rt = self.resumed("rtback", "Rummy Tile [ParlorNet]", b"PNrt", bytes(800), tid)
        pos = self.position(rt)
        self.assertEqual((pos[0], pos[1], pos[-2], pos[-1]), ("POS 0 0 0", "Z", "W 0", "POE"))
        self.assertEqual([l for l in pos if l.startswith("H ")], [l for l in view if l.startswith("H ")])
        rt.send("M END")
        self.assertEqual(rt.read(), (False, "A END"))
        w, tid = self.bot_table("cfback", "cannonfire/1", "classic")
        view = self.my_turn(w, tid, 0)
        t, cf = self.resumed("cfback", "Cannon Fire [ParlorNet]", b"PNcf", bytes(400), tid)
        pos = self.position(cf)
        self.assertEqual(pos[0], "POS 0 0 0")
        self.assertEqual([l for l in pos if l.startswith("Z ")], [l for l in view if l.startswith("Z ")])
        for verb in ("K", "N", "W"):
            self.assertEqual([l for l in pos if l.startswith(verb + " ")],
                             [l for l in view if l.startswith(verb + " ")])
        self.assertEqual(len([l for l in pos if l.startswith("AIM ")]), 2)
        cf.send("M FIRE 60 50")
        seen = []
        while not seen or seen[-1] != "A FIRE 60 50":      # the shot and the state come before A
            got = cf.read()
            self.assertIsNotNone(got)
            seen.append(got[1])
        self.assertEqual(seen[:2], ["F 0", seen[1]])
        self.assertTrue(seen[1].startswith("TRJ ") and seen[-2].startswith("W "), seen)

    def test_backgammon_resumed(self):
        """A Backgammon game started on the core's port against the bot is taken up from GameMaster: H, then the
        whole state between POS and POE, with the board as the core's view has it; the Mac's move is answered."""
        w, tid = self.bot_table("bgback", "backgammon/1", "standard")
        view = self.my_turn(w, tid, 0)
        t, bg = self.resumed("bgback", "Backgammon [ParlorNet]", b"PNbg", backgammon_state()[1024:-4], tid)
        self.assertEqual(bg.read(), (False, "H right"))
        pos = self.position(bg)
        self.assertEqual((pos[0], pos[-2], pos[-1]), ("POS 0 0 0", "W 0", "POE"))
        p1 = next(l for l in view if l.startswith("P 1 "))[4:].split()
        p13 = next(l for l in view if l.startswith("P 13 "))[5:].split()
        self.assertEqual(pos[1].split()[1:25], p1 + p13)
        q = next(l for l in pos if l.startswith("Q "))
        word = "roll" if q == "Q roll" else None
        if word is None:                # the cube is on: the turn starts with roll unless it was rolled already
            self.assertEqual(q, "Q move")
            return
        bg.send("M roll")
        got = bg.read()
        while got[1] != "A roll":
            self.assertFalse(got[1].startswith("R "), got)
            got = bg.read()

    def test_tables_of_more_than_two_stay_off_gamemaster(self):
        """GameMaster plays two (a call joins two Macs). A three-player Grid Words table: its invitation is
        not in the lobby, the lobby's Accept naming its host is refused with the reason, and when it is being
        played the GameMaster player cannot take it up; no table changes."""
        w = Native(self.core, "gwthree")
        self.addCleanup(w.close)
        final = w.req("NEW gridwords/1 standard players=3 turn=1h vis=public")[-1]
        self.assertTrue(final.startswith("OK "), final)
        tid = final.split()[1]
        self.assertTrue(w.req("JON %s 0" % tid)[-1].startswith("OK"))
        t = self.tunnel("macthree")
        self.assertTrue(w.req("INV %s macthree 1" % tid)[-1].startswith("OK"))
        before = self.record(w, tid)
        self.assertFalse([l for l in t.req("IVL") if l.startswith("IVT %s " % tid)])
        g = TunnelGame(t, 1, 2, "gwthree")
        self.assertEqual(g.call("Grid Words [ParlorNet]", 0x0110, 0x0100, parlor_state(b"PNgw", bytes(260))),
                         GM.SEND_GAME)
        line = t.lines.get(timeout=TIMEOUT)
        self.assertEqual(line, "INF " + GM.BIGGER % (tid, 3))
        self.assertEqual(self.record(w, tid), before)
        g.ctl(GM.HANG_UP)
        # being played, with the GameMaster player seated: not taken up
        self.assertTrue(w.req("INV %s bot 2" % tid)[-1].startswith("OK"))
        p = Native(self.core, "macthree")
        self.addCleanup(p.close)
        self.assertTrue(p.req("JON %s 1" % tid)[-1].startswith("OK"))
        w.until_event(tid, r" START")
        before = self.record(w, tid)
        t3 = self.tunnel("macthree")
        r = TunnelGame(t3, 1, 2, "@%s" % tid)
        r.call("Grid Words [ParlorNet]", 0x0110, 0x0100, parlor_state(b"PNgw", bytes(260)))
        r.expect(GM.JUST_A_MOMENT)
        self.assertEqual(r.expect(GM.NOT_INTERESTED), [])
        self.assertEqual(t3.lines.get(timeout=TIMEOUT), "INF " + GM.BIGGER % (tid, 3))
        self.assertEqual(self.record(w, tid), before)

    def test_only_your_own_games_resume(self):
        """Another player's table, a table not being played, an Othello table (GameMaster's own Othello takes
        no position) and an older rulebook are all refused with the reason on the lobby's INF line; none of
        the tables changes."""
        w, tid = self.bot_table("ownera", "fiveinarow/1", "freestyle")
        self.my_turn(w, tid, 0)
        before = self.record(w, tid)
        t = self.tunnel("intruder")
        g = TunnelGame(t, 1, 2, "@%s" % tid)
        self.assertEqual(g.call("Five in a Row [ParlorNet]", 0x0110, 0x0100, parlor_state(b"PNfr", bytes(232))),
                         GM.SEND_GAME)
        g.expect(GM.JUST_A_MOMENT)
        self.assertEqual(g.expect(GM.NOT_INTERESTED), [])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Table %s is not one of your games." % tid)
        self.assertEqual(self.record(w, tid), before)
        # the owner, with a 1.0.0 rulebook that takes no position
        t = self.tunnel("ownera")
        g = TunnelGame(t, 1, 2, "@%s" % tid)
        g.call("Five in a Row [ParlorNet]", 0x0100, 0x0100, parlor_state(b"PNfr", bytes(232)))
        self.assertEqual(g.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertIn("install the latest ParlorNet Rulebooks", t.lines.get(timeout=TIMEOUT))
        self.assertEqual(self.record(w, tid), before)
        # an Othello table of the player's own
        o = Native(self.core, "ownero")
        self.addCleanup(o.close)
        oid = o.req("NEW reversi standard turn=1h")[-1].split()[1]
        o.req("JON %s 0" % oid)
        o.req("INV %s bot 1" % oid)
        o.until_event(oid, r" START")
        t = self.tunnel("ownero")
        g = TunnelGame(t, 1, 2, "@%s" % oid)
        g.call("Othello [RHS]", 0x0020, 0x0020, othello_state())
        self.assertEqual(g.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Othello cannot be taken up half way in GameMaster. "
                                                       "Play table %s on the web or by telnet." % oid)
        # an open table, not yet being played
        oid = o.req("NEW fiveinarow freestyle turn=1h")[-1].split()[1]
        o.req("JON %s 0" % oid)
        g = TunnelGame(t, 3, 4, "@%s" % oid)
        g.call("Five in a Row [ParlorNet]", 0x0110, 0x0100, parlor_state(b"PNfr", bytes(232)))
        self.assertEqual(g.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF The game at table %s is not being played now." % oid)
        self.assertEqual(next(x for x in o.tables() if x["id"] == oid)["seated"], ["ownero", "-"])

    def test_the_lobby_lists_and_joins_tables(self):
        """TBL: the player's own games being played (with whose turn and the opponent), open public tables where
        someone waits, and other players' games. GMO c #TABLE sits down at a waiting player's table, swapping
        sides when the first seat is taken."""
        w, mine = self.bot_table("lister", "mancala/1", "kalah")
        o, theirs = self.bot_table("other1", "checkers/1", "american")
        wt = Native(self.core, "waiter")
        self.addCleanup(wt.close)
        waiting = wt.req("NEW checkers american turn=1h")[-1].split()[1]
        wt.req("JON %s 0" % waiting)
        t = self.tunnel("lister")
        lines = t.req("TBL")
        self.assertEqual(lines[-1], "OK")
        self.assertEqual(lines[0], "YG %s mancala/1 kalah 0 0 bot" % mine)
        self.assertIn("WG %s checkers/1 american waiter" % waiting, lines)
        self.assertIn("PG %s checkers/1 american other1 bot" % theirs, lines)
        ck = TunnelGame(t, 1, 2, "#%s" % waiting)
        ck.call("Checkers [ParlorNet]", 0x0100, 0x0100, checkers_state())
        ck.expect(GM.PLAY_IT_SAM)
        self.assertEqual(ck.expect(GM.REQUEST_SWAP), [])
        ck.ctl(GM.SWAP)
        end = time.time() + TIMEOUT
        while next(x for x in wt.tables() if x["id"] == waiting)["status"] != "playing":
            self.assertLess(time.time(), end)
            time.sleep(0.1)
        self.assertEqual(next(x for x in wt.tables() if x["id"] == waiting)["seated"], ["waiter", "lister"])

    # ---- the call ----------------------------------------------------------------------------
    def test_a_stock_call_is_refused(self):
        """GameMaster by itself (Game > Connect), outside the ParlorNet rulebook: a call record straight to the
        port is answered with GameMaster's own refusal, ~Dont Send Code, which the Mac shows as "isn't
        interested", and the connection closes. Each refusal is one line in the log. A Simple Talk call, once
        the console, is refused the same way, and the parlor never asks for rulebook code."""
        before = len(self.gm_out)
        for rb, version, size in (("Othello [RHS]", 0x0020, 122), ("Dots&Boxes [PNL]", 0x0100, 568),
                                  ("Simple Talk [PNL]", 0x0100, 852), ("Checkers [ParlorNet]", 0x0110, 1096)):
            self.assertEqual(stock_call(self.gm, rb, version, version, size), ("~" + GM.DONT_SEND_CODE, True), rb)
        self.assertTrue(self.log_has("without the ParlorNet rulebook (Checkers [ParlorNet]): refused"))
        lines = [l for l in self.gm_out[before:] if "without the ParlorNet rulebook" in l]
        self.assertEqual(len(lines), 4, lines)
        self.assertRegex(lines[0], r"^gamemaster\[\d+\] a GameMaster call from 127\.0\.0\.1 without the ParlorNet "
                                   r"rulebook \(Othello \[RHS\]\): refused$")
        self.assertFalse(any("Send Code Please" in l for l in self.gm_out))
        # a connection that sends neither PARLORNET/1 nor a call record: ERR PROTOCOL, and closed
        self.assertEqual(self.opening(b"GET / HTTP/1.0\r\n\r\n"), TUNNEL_WRONG)

    def opening(self, first):
        """What the tunnel's port answers first before it closes the connection."""
        s = socket.create_connection(("127.0.0.1", self.gm), timeout=TIMEOUT)
        try:
            s.sendall(first)
            out = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    return out
                out += chunk
        finally:
            s.close()

    def test_the_tunnels_opening(self):
        """The tunnel opens as the core's public port does: PARLORNET/1 in any case, any of the four endings,
        up to 10 bytes before it; otherwise ERR PROTOCOL or ERR VERSION, and the close."""
        for first in (b"PARLORNET/1\r\n", b"PARLORNET/1\r", b"parlornet/1\n", b"PARLORNET/1\n\r",
                      b"\r\n0123456PARLORNET/1\r\n"):
            t = Tunnel(self.gm, first)
            self.assertTrue(t.greeting.startswith("PARLORNET/1 "), (first, t.greeting))
            t.close()
        t = Tunnel(self.gm, b"PARLORNET/1\r\nHELO macopen\r\n")        # a line straight after the opening
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "OK -")
        t.close()
        for first in (b"01234567890PARLORNET/1\r\n", b"PARLORNET/1 \r\n", b"\xff\xfd\x03\xff\xfb\x18\xff\xfb\x1f\xff\xfb",
                      b"hello there, parlor\r\n"):
            self.assertEqual(self.opening(first), TUNNEL_WRONG, first)
        self.assertEqual(self.opening(b"PARLORNET/2\r\n"),
                         b"ERR VERSION wrong version; see http://docs.parlornet.org/docs/protocol.html\r\n")

    def test_the_tunnels_time_limit(self):
        s = socket.create_connection(("127.0.0.1", self.gm), timeout=TIMEOUT)
        t0 = time.time()
        s.sendall(b"PARLOR")
        self.assertEqual(s.makefile("rb").read(), TUNNEL_WRONG)      # 2 seconds here (--opening-secs), else 20
        self.assertGreaterEqual(time.time() - t0, 1.8)
        s.close()

    def test_refusals(self):
        """In the tunnel: another rulebook (Simple Talk among them), or a version this parlor does not play:
        GameMaster's refusal (~Dont Send Code). A caller needing a newer GameMaster: ~Go away, I'm old. Never a
        request for rulebook code."""
        t = self.tunnel("macref")
        for n, (rb, version, oldest) in enumerate((
                ("Eskimo Chess", 0x0100, 0x0100), ("Connect 4 [RHS]", 0x0010, 0x0010), ("Simple Talk [PNL]", 0x0100,
                                                                                       0x0100),
                ("Othello [RHS]", 0x0030, 0x0030), ("Othello [RHS]", 0x0010, 0x0010)), 1):
            got = TunnelGame(t, n, 8).call(rb, version, oldest, othello_state())
            self.assertEqual(got, "~" + GM.DONT_SEND_CODE, rb)
        self.assertEqual(TunnelGame(t, 6, 8).call("Othello [RHS]", 0x20, 0x20, othello_state(), needs=0x0200),
                         "~" + GM.TOO_OLD)
        self.assertTrue(self.log_has("a rulebook this parlor does not play: refused"))
        self.assertFalse(any("Send Code Please" in l for l in self.gm_out))

    def test_a_saved_game_is_turned_down(self):
        g = self.game("macsave", "Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state(drawn=3))
        self.assertEqual(g.expect(GM.NOT_INTERESTED), [])

    # ---- Othello ------------------------------------------------------------------------------
    def play_othello(self, m, opponent=None, me=0):
        """Othello on both sides of a local copy of the rules; GameMaster plays seat me (0: white, first; 1
        after a swap). opponent(token): the other side's move, for a web player."""
        g = LocalGame("reversi", "standard", 2, 0)
        rnd = random.Random(1)
        while not g.result:
            seat = g.state["turn"]
            moves = g.m.op_moves({"rules": "standard", "state": g.state, "seat": seat})["moves"]
            if seat == me:
                tok = rnd.choice(moves)
                m.send("%d%d" % (ord(tok[0]) - 97, int(tok[1:]) - 1))
            else:
                sent = None
                if opponent:
                    sent = rnd.choice(moves)
                    opponent(sent)
                got = m.read()
                self.assertIsNotNone(got, "closed in the middle of the game")
                self.assertFalse(got[0], got)
                self.assertRegex(got[1], r"^[0-7][0-7]\Z")
                tok = "%s%d" % (chr(97 + int(got[1][0])), int(got[1][1]) + 1)
                if sent:
                    self.assertEqual(tok, sent)
            self.assertTrue(g.apply(seat, [tok], False).startswith("OK"), (seat, tok))
        return g.result

    def web_othello_table(self, name, invite):
        """A web player's Othello table (they sit at seat 0), with the GameMaster player invited."""
        self.web_login(name)
        loc = self.web_get("/new.htm?g=reversi%%2F1&turn=1h&vis=private&n=%s" % name)[1]
        tid = query_of(loc)["t"][0]
        st, _, body = self.web_get("/invite.htm?t=%s&who=%s&n=%s" % (tid, invite, name))
        self.assertEqual(st, 302, body[-300:])
        return tid

    def test_othello_swaps_to_take_a_web_invitation(self):
        """A web player who sits at seat 0 invites the GameMaster player, who takes it with the lobby's Accept
        (GMO naming the inviter): the call asks the Mac to swap sides (~Request to swap); after ~Swap the Mac
        plays black, second."""
        t = self.tunnel("macswp")                    # the account the web player invites
        tid = self.web_othello_table("webswp", "macswp")
        w = Native(self.core, "webswp")
        self.addCleanup(w.close)
        self.assertEqual(next(x for x in w.tables() if x["id"] == tid)["seated"], ["webswp", "-"])
        m = TunnelGame(t, 1, 2, "webswp")
        self.assertEqual(m.call("Othello [RHS]", 0x0020, 0x0020, othello_state()), GM.SEND_GAME)
        self.assertEqual(m.expect(GM.JUST_A_MOMENT), [])
        self.assertEqual(m.expect(GM.PLAY_IT_SAM), [])
        self.assertEqual(m.expect(GM.REQUEST_SWAP), [])
        m.ctl(GM.SWAP)
        self.assertEqual(self.gm_table(w, "macswp", "reversi/1")["seated"], ["webswp", "macswp"])
        seen = [0]

        def opponent(tok):
            _, seen[0] = w.until_event(tid, r" TURN 0\b", seen[0])
            st, _, body = self.web_get("/move.htm?t=%s&mv=%s&n=webswp" % (tid, tok))
            self.assertEqual(st, 302, body[-300:])
        result = self.play_othello(m, opponent, me=1)
        self.finished(m, tid, w, re.escape(result))

    def test_othello_swap_declined(self):
        """The Mac's player will not swap: the game hangs up and the web player's table stays open."""
        t = self.tunnel("macdec")                    # the account the web player invites
        tid = self.web_othello_table("webdec", "macdec")
        m = TunnelGame(t, 1, 2, "anyone")
        m.call("Othello [RHS]", 0x0020, 0x0020, othello_state())
        m.expect(GM.PLAY_IT_SAM)
        m.expect(GM.REQUEST_SWAP)
        m.ctl(GM.DONT_SWAP)
        self.assertEqual(m.expect(GM.HANG_UP), [])
        w = Native(self.core, "webdec")
        self.addCleanup(w.close)
        self.assertEqual(next(x for x in w.tables() if x["id"] == tid)["seated"], ["webdec", "-"])
        self.assertTrue(self.log_has("would not swap sides"))

    def test_othello_against_a_web_player(self):
        """The Opponent menu names a player: a private table with seat 1 held for them; the web player's Join
        takes it."""
        self.web_login("wendo")
        m = self.game("macwo", "Othello [RHS]", 0x0020, 0x0020, othello_state(), who="wendo")
        w = Native(self.core, "wendo")
        self.addCleanup(w.close)
        end = time.time() + TIMEOUT
        inv = []
        while not inv and time.time() < end:
            inv = [l for l in w.req("IVL") if l.startswith("IVT ") and " macwo " in l]
            time.sleep(0.1)
        self.assertTrue(inv)
        tid = inv[0].split()[1]
        self.assertIn("seat=1", inv[0])
        mine = Native(self.core, "macwo")               # the table is private: its own player looks
        self.addCleanup(mine.close)
        tab = next(x for x in mine.tables() if x["id"] == tid)
        self.assertEqual((tab["rules"], tab["vis"], tab["seated"]), ("standard", "private", ["macwo", "-"]))
        st, _, body = self.web_get("/join.htm?t=%s&n=wendo" % tid)
        self.assertEqual(st, 302, body[-300:])
        self.assertEqual(m.expect(GM.PLAY_IT_SAM), [])

        def web_move(tok):
            st, _, body = self.web_get("/move.htm?t=%s&mv=%s&n=wendo" % (tid, tok))
            self.assertEqual(st, 302, body[-300:])
        seen = [0]

        def opponent(tok):
            # the Mac's last move must be in before the web player's
            _, seen[0] = w.until_event(tid, r" TURN 1\b", seen[0])
            web_move(tok)
        result = self.play_othello(m, opponent)
        self.finished(m, tid, w, re.escape(result))

    # ---- Dots&Boxes ---------------------------------------------------------------------------------
    def play_dots(self, m, opponent=None):
        """Dots&Boxes on 13 by 11 to a majority; GameMaster (seat 0, gray) closes boxes when it can."""
        g = LocalGame("dotsandboxes", "majority", 2, 0)
        rnd = random.Random(2)
        plies = 0
        while not g.result:
            seat = g.state["turn"]
            moves = g.m.op_moves({"rules": "majority", "state": g.state, "seat": seat})["moves"]
            if seat == 0:
                closing = [t for t in moves if "BOX" in g.m.op_apply({"rules": "majority", "state": g.state,
                                                                     "seat": 0, "move": [t]})["events"][0]]
                tok = (closing or moves)[0] if closing else rnd.choice(moves)
                m.send(mac_line(tok))
            else:
                if opponent:
                    tok = rnd.choice(moves)
                    opponent(tok)
                got = m.read()
                self.assertIsNotNone(got, "closed in the middle of the game")
                self.assertRegex(got[1], r"^M[01]\d{6}\Z")
                tok = gm_line(got[1])
            self.assertTrue(g.apply(seat, [tok], False).startswith("OK"), (seat, tok))
            plies += 1
        self.assertLess(g.state["bx"].count("."), 143)
        return g.result

    def test_dots_against_a_web_player(self):
        """Wait for opponent (anyone): a public table, which a web player joins."""
        self.web_login("webdot")
        m = self.game("macdot", "Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state(), who="anyone")
        w = Native(self.core, "webdot")
        self.addCleanup(w.close)
        t = self.gm_table(w, "macdot", "dotsandboxes/1")
        self.assertEqual((t["seated"], t["rules"], t["vis"], t["status"]), (["macdot", "-"], "majority", "public",
                                                                             "open"))
        st, _, body = self.web_get("/join.htm?t=%s&n=webdot" % t["id"])
        self.assertEqual(st, 302, body[-300:])
        self.assertEqual(m.expect(GM.PLAY_IT_SAM), [])
        seen = [0]

        def opponent(tok):
            _, seen[0] = w.until_event(t["id"], r" TURN 1\b", seen[0])
            st, _, body = self.web_get("/move.htm?t=%s&mv=%s&n=webdot" % (t["id"], tok))
            self.assertEqual(st, 302, body[-300:])
        result = self.play_dots(m, opponent)
        self.finished(m, t["id"], w, re.escape(result))

    def test_join_a_waiting_players_table(self):
        """The lobby's Join (GMO #TABLE): the game sits down at that player's open table, with no invitation; a
        table that is not open is answered on the lobby's INF line."""
        w = Native(self.core, "webjn")
        self.addCleanup(w.close)
        tid = w.req("NEW dotsandboxes/1 majority")[-1].split()[1]
        self.assertTrue(w.req("JON %s 1" % tid)[-1].startswith("OK"))            # seat 0 left for GameMaster
        t = self.tunnel("macjn")
        gone = TunnelGame(t, 3, 4, "#99999")
        self.assertEqual(gone.call("Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state()), GM.SEND_GAME)
        self.assertEqual(gone.expect(GM.NOT_INTERESTED), [(True, GM.JUST_A_MOMENT)])
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Table 99999 is no longer waiting for a player.")
        m = TunnelGame(t, 1, 2, "#%s" % tid)
        m.call("Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state())
        m.expect(GM.JUST_A_MOMENT)
        self.assertEqual(m.expect(GM.PLAY_IT_SAM), [])
        tab = next(x for x in w.tables() if x["id"] == tid)
        self.assertEqual((tab["seated"], tab["status"]), (["macjn", "webjn"], "playing"))
        m.ctl(GM.HANG_UP)

    def test_the_lobby_hears_what_the_opponent_plays_with(self):
        """GameMaster says only its own words when a game starts, so the lobby gets an INF line with what the
        opponent plays with, and another when they play on from another program (docs/protocol.md, CLIENT)."""
        w = Native(self.core, "webcl")
        self.addCleanup(w.close)
        w.req("CLI PROG Netscape Navigator 3.0")
        w.req("CLI MACH Macintosh")
        tid = w.req("NEW dotsandboxes/1 majority")[-1].split()[1]
        self.assertTrue(w.req("JON %s 1" % tid)[-1].startswith("OK"))
        t = self.tunnel("maccl")
        m = TunnelGame(t, 1, 2, "#%s" % tid)
        m.call("Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state())
        m.expect(GM.JUST_A_MOMENT)
        m.expect(GM.PLAY_IT_SAM)
        self.assertEqual(t.lines.get(timeout=TIMEOUT),
                         "INF Table %s: webcl plays with Netscape Navigator 3.0 on a Macintosh." % tid)
        w2 = Native(self.core, "webcl")
        self.addCleanup(w2.close)
        w2.req("CLI PROG Lynx 2.8")
        w2.req("CHT %s hello" % tid)
        self.assertEqual(t.lines.get(timeout=TIMEOUT), "INF Table %s: webcl now plays with Lynx 2.8." % tid)
        m.ctl(GM.HANG_UP)

    def test_dots_against_the_bot_when_nobody_comes(self):
        """Wait for opponent, and nobody joins within --wait: the bot is invited to the open table, and the
        game is played to its end."""
        m = self.game("macdob", "Dots&Boxes [PNL]", 0x0100, 0x0100, dots_state(), who="anyone")
        self.assertEqual(m.expect(GM.PLAY_IT_SAM, 30), [])
        w = Native(self.core, "dobwatch")
        self.addCleanup(w.close)
        t = self.gm_table(w, "macdob", "dotsandboxes/1")
        self.assertEqual(t["seated"], ["macdob", "bot"])
        m.ctl("Request to restart")
        self.assertEqual(m.read(), (True, "Dont Restart"))
        result = self.play_dots(m)
        self.finished(m, t["id"], w, re.escape(result))

    # ---- BattleShips --------------------------------------------------------------------------------
    def play_battleships(self, m, opponent=None):
        """BattleShips: GameMaster sends its fleet and gets the other; it fires its salvos at the other
        fleet's squares first (it has them, as the rulebook does), marking one square twice to unmark it;
        the other side's salvos arrive as six squares, Fire and Done. opponent(kind, ...): the web player's
        part."""
        g = LocalGame("navalcombat", "salvo", 2, 0)
        mine = "00h01h02h03h88v"
        m.send(mine)
        for tok in GM.BattleShips.fleet_tokens(mine) + ["ready"]:
            self.assertTrue(g.apply(0, [tok], False).startswith("OK"))
        if opponent:
            opponent("place")
        got = m.read()
        self.assertFalse(got[0])
        self.assertEqual(len(got[1]), 15, got)
        theirs = GM.BattleShips.fleet_tokens(got[1])
        for tok in theirs + ["ready"]:
            self.assertTrue(g.apply(1, [tok], False).startswith("OK"), tok)
        targets = [(x, y) for t in theirs for x, y in squares(t)]
        rnd = random.Random(3)
        salvos = 0
        while not g.result:
            seat = g.state["turn"]
            if seat == 0:
                shots = targets[:6]
                del targets[:6]
                if len(shots) < 6:
                    free = [(x, y) for x in range(10) for y in range(10)
                            if g.state["shots"][1][y * 10 + x] == "." and (x, y) not in shots]
                    shots += free[:6 - len(shots)]
                m.send("99" if (9, 9) not in shots else "00")    # marked, then unmarked
                m.send("99" if (9, 9) not in shots else "00")
                for x, y in shots:
                    m.send("%d%d" % (x, y))
                m.send("Fire")
                for x, y in sorted(shots, key=lambda p: (p[1], p[0])):
                    if not g.result:
                        self.assertTrue(g.apply(0, ["%s%d" % (chr(97 + x), y + 1)], False).startswith("OK"))
                m.send("Done")
                salvos += 1
            else:
                if opponent:
                    opponent("salvo", g, rnd)
                got = []
                while True:
                    msg = m.read()
                    self.assertIsNotNone(msg, "closed in the middle of the game")
                    if msg[1] == "Fire":
                        break
                    self.assertRegex(msg[1], r"^\d\d\Z")
                    got.append(msg[1])
                self.assertEqual(m.read(), (False, "Done"))
                self.assertEqual(len(got), 6)
                for s_ in got:
                    self.assertTrue(g.apply(1, ["%s%d" % (chr(97 + int(s_[0])), int(s_[1]) + 1)], False)
                                    .startswith("OK"))
        self.assertEqual(salvos, 3)         # 17 squares in three salvos
        return g.result

    def test_battleships_against_a_web_player(self):
        self.web_login("webbat")
        m = self.game("macweb", "BattleShips [RHS]", 0x0020, 0x0020, bytes(2400), who="webbat")
        w = Native(self.core, "webbat")
        self.addCleanup(w.close)
        end = time.time() + TIMEOUT
        inv = []
        while not inv and time.time() < end:
            inv = [l for l in w.req("IVL") if l.startswith("IVT ") and " macweb " in l]
            time.sleep(0.1)
        tid = inv[0].split()[1]
        self.assertEqual(self.web_get("/join.htm?t=%s&n=webbat" % tid)[0], 302)
        self.assertEqual(m.expect(GM.PLAY_IT_SAM), [])
        seen = [0]

        def web_move(tok):
            st, _, body = self.web_get("/move.htm?t=%s&mv=%s&n=webbat" % (tid, quote(tok)))
            self.assertEqual(st, 302, body[-300:])

        def opponent(kind, g=None, rnd=None):
            if kind == "place":
                web_move("random")
                web_move("ready")
                return
            # the turn after the Mac's salvo (a TURN 1 also comes while seat 1 is still placing its fleet)
            _, seen[0] = w.until_event(tid, r" SHOT 0 ", seen[0])
            _, seen[0] = w.until_event(tid, r" TURN 1\b", seen[0])
            for tok in rnd.sample(g.m.op_moves({"rules": "salvo", "state": g.state, "seat": 1})["moves"], 6):
                web_move(tok)
                # the local copy follows when the salvo reaches the Mac
                _, seen[0] = w.until_event(tid, r" SHOT 1 ", seen[0])
        result = self.play_battleships(m, opponent)
        self.finished(m, tid, w, re.escape(result))
        # the web player's view never showed the Mac's fleet; the Mac was sent the web player's
        page = self.web_get("/table.htm?t=%s&n=webbat" % tid)[2]
        self.assertNotIn("OF 5", page)


def squares(token):
    (x0, y0), (x1, y1) = ((ord(p[0]) - 97, int(p[1:]) - 1) for p in token.split("-"))
    if y0 == y1:
        return [(x, y0) for x in range(min(x0, x1), max(x0, x1) + 1)]
    return [(x0, y) for y in range(min(y0, y1), max(y0, y1) + 1)]


def mac_line(tok):
    """A line as Dots&Boxes sends it, written out here from the rulebook's source (SendMove)."""
    (x1, y1), (x2, y2) = sorted((ord(d[0]) - 97, int(d[1:]) - 1) for d in tok.split("-"))
    if y1 == y2:                    # across: on dot row y1, from dot column x1 to x1 + 1
        return "M0%03d%03d" % (y1, x1 + 1)
    return "M1%03d%03d" % (y2, x1)  # down: on dot column x1, from dot row y1 to y1 + 1


def gm_line(msg):
    r, c = int(msg[2:5]), int(msg[5:8])
    if msg[1] == "0":
        return "%s%d-%s%d" % (chr(96 + c), r + 1, chr(97 + c), r + 1)
    return "%s%d-%s%d" % (chr(97 + c), r, chr(97 + c), r + 1)


if __name__ == "__main__":
    unittest.main()
