"""The plugins' typed op: a `/` command line typed at a table, turned into what the core does with it.

Each plugin is loaded in process and asked with OPS["typed"], on states made by its own new op and a few
applied moves. A typed move's tokens are then played with the plugin's own apply, to show it takes them."""
import importlib.util
import os
import tempfile
import unittest
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES = ("reversi", "fiveinarow", "dotsandboxes", "mancala", "cannonfire", "backgammon", "checkers", "gridwords",
         "navalcombat", "rummytile")
NOT_YOUR_TURN = {"error": "It's not your turn."}


def load(game):
    spec = importlib.util.spec_from_file_location("typed_%s_plugin" % game,
                                                  os.path.join(ROOT, "plugins", game, "plugin.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLUGINS = {g: load(g) for g in GAMES}


class Typed(unittest.TestCase):
    """Shared helpers: P is the plugin under test."""
    game = None
    rules = None

    @property
    def P(self):
        return PLUGINS[self.game]

    def new(self, players=2, options=None, seed=7):
        r = self.P.OPS["new"]({"op": "new", "rules": self.rules, "players": players, "options": options or {},
                               "seed": seed})
        self.assertNotIn("error", r)
        return r["state"]

    def typed(self, st, seat, line):
        return self.P.OPS["typed"]({"op": "typed", "rules": self.rules, "state": st, "seat": seat,
                                    "words": line.split()})

    def apply(self, st, seat, move):
        return self.P.OPS["apply"]({"op": "apply", "rules": self.rules, "state": st, "seat": seat, "move": move})

    def plays(self, st, seat, line, move=None):
        """Types line, checks the move it gives (when move is given), plays it and -> the new state."""
        reply = self.typed(st, seat, line)
        self.assertIn("move", reply, (line, reply))
        if move is not None:
            self.assertEqual(reply["move"], move, line)
        r = self.apply(st, seat, reply["move"])
        self.assertNotIn("error", r, (line, reply, r))
        return r["state"]

    def step(self, st, seat, line, step, ask=None):
        """Types a staged step's word, checks the reply, plays it as the core does (UND, END) -> the state."""
        reply = self.typed(st, seat, line)
        self.assertEqual(reply.get("step"), step, (line, reply))
        self.assertEqual(reply.get("ask"), ask, (line, reply))
        r = self.apply(st, seat, ["!" + step])
        self.assertNotIn("error", r, (line, r))
        return r["state"]


class Info(unittest.TestCase):
    def test_every_plugin_answers_typed(self):
        for game, P in PLUGINS.items():
            self.assertIs(P.INFO.get("typed"), True, game)
            self.assertIs(P.OPS.get("typed"), P.op_typed, game)
            self.assertIs(P.OPS["info"]({"op": "info"}).get("typed"), True, game)


class Reversi(Typed):
    game = "reversi"

    def test_square(self):
        st = self.new()
        self.assertEqual(self.typed(st, 0, "D3"), {"move": ["d3"]})
        st = self.plays(st, 0, "d3", ["d3"])
        self.assertEqual(st["turn"], 1)

    def test_not_a_move_and_off_turn(self):
        st = self.new()
        for line in ("", "hello", "d3 e4", "3d", "d"):
            self.assertEqual(self.typed(st, 0, line), {}, line)
        self.assertEqual(self.typed(st, 1, "d3"), NOT_YOUR_TURN)

    def test_shape_only(self):
        """A square shape goes to apply, which judges it."""
        st = self.new()
        reply = self.typed(st, 0, "a1")
        self.assertEqual(reply, {"move": ["a1"]})
        self.assertIn("error", self.apply(st, 0, reply["move"]))


class FiveInARow(Typed):
    game = "fiveinarow"

    def test_point(self):
        st = self.new()
        st = self.plays(st, 0, "H8", ["h8"])
        self.assertEqual(self.typed(st, 0, "h9"), NOT_YOUR_TURN)
        self.plays(st, 1, "h9", ["h9"])

    def test_not_a_move(self):
        st = self.new()
        for line in ("", "pass", "h8 h9", "88"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


class DotsAndBoxes(Typed):
    game = "dotsandboxes"

    def test_line_forms(self):
        for line, token in (("a1-b1", "a1-b1"), ("a1b1", "a1b1"), ("a1 b1", "a1-b1"), ("B1-A1", "b1-a1")):
            st = self.new()
            reply = self.typed(st, 0, line)
            self.assertEqual(reply, {"move": [token], "what": "draw %s" % token}, line)
            st2 = self.plays(st, 0, line)
            self.assertNotEqual(st2["hl"][0], ".", line)     # the line a1-b1 is drawn

    def test_not_a_move_and_off_turn(self):
        st = self.new()
        for line in ("", "a1", "a1 b1 c1", "draw"):
            self.assertEqual(self.typed(st, 0, line), {}, line)
        self.assertEqual(self.typed(st, 1, "a1-b1"), NOT_YOUR_TURN)


class Mancala(Typed):
    game = "mancala"

    def test_pit_forms(self):
        for line in ("3", "pit 3", "sow 3", "Sow 3"):
            st = self.new()
            self.assertEqual(self.typed(st, 0, line), {"move": ["3"], "what": "sow pit 3"}, line)
            self.plays(st, 0, line)

    def test_not_a_move_and_off_turn(self):
        st = self.new()
        for line in ("", "pit three", "3 4", "hello"):
            self.assertEqual(self.typed(st, 0, line), {}, line)
        self.assertIn("error", self.typed(st, 0, "pit"))
        self.assertEqual(self.typed(st, 1, "3"), NOT_YOUR_TURN)


class CannonFire(Typed):
    game = "cannonfire"

    def test_fire(self):
        st = self.new()
        for line in ("fire 45 60", "45 60", "FIRE 045 60"):
            self.assertEqual(self.typed(st, 0, line), {"move": ["FIRE", "45", "60"], "what": "fire"}, line)
        self.plays(st, 0, "fire 45 60")
        self.assertIn("error", self.typed(st, 0, "fire 45"))
        self.assertEqual(self.typed(st, 1, "fire 45 60"), NOT_YOUR_TURN)

    def test_drive(self):
        st = self.new()
        self.assertEqual(self.typed(st, 0, "drive left 5"), {"move": ["MOVE", "L", "5"], "what": "drive left"})
        self.assertEqual(self.typed(st, 0, "drive right 5"), {"move": ["MOVE", "R", "5"], "what": "drive right"})
        self.assertEqual(self.typed(st, 0, "move r 3"), {"move": ["MOVE", "R", "3"], "what": "drive right"})
        self.assertEqual(self.typed(st, 0, "drive -5"), {"move": ["MOVE", "-5"], "what": "drive left"})
        self.assertEqual(self.typed(st, 1, "drive left 5"), NOT_YOUR_TURN)
        for line in ("drive", "drive up", "drive left five", "move left 5 6"):
            self.assertIn("error", self.typed(st, 0, line), line)

    def test_drive_whole_budget(self):
        """/drive left alone drives as far as one move goes: the whole budget, rounded up to columns."""
        st = self.new(options={"w": "72"})
        whole = str(max(1, (self.P.budget(st) + 9) // 10))
        self.assertEqual(whole, "8")
        moved = 0
        for way, side in (("left", "L"), ("right", "R")):
            reply = self.typed(st, 0, "drive " + way)
            self.assertEqual(reply, {"move": ["MOVE", side, whole], "what": "drive " + way})
            r = self.apply(st, 0, reply["move"])
            if "error" not in r:
                moved += 1
                self.assertTrue(r["state"]["moved"])
            else:
                self.assertIn("cannot move", r["error"])      # the ground or the edge, never the token's form
        self.assertGreater(moved, 0)

    def test_not_a_move(self):
        st = self.new()
        for line in ("", "hello", "45", "45 60 70", "aim 45 60"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


class Backgammon(Typed):
    game = "backgammon"

    def legal(self, st, seat):
        return self.P.OPS["moves"]({"op": "moves", "state": st, "seat": seat})["moves"]

    def finish_turn(self, st):
        """Plays the seat to move's legal steps until none is left, then ends the turn by typing /end."""
        seat = st["turn"]
        while self.legal(st, seat):
            st = self.plays(st, seat, self.legal(st, seat)[0].replace("-", "/"))
        return self.step(st, seat, "end", "end")

    def test_steps(self):
        st = self.new(options={"cube": "0"})
        seat = st["turn"]
        first = self.legal(st, seat)[0]
        self.assertEqual(self.typed(st, seat, first.upper()), {"move": [first]})
        st2 = self.plays(st, seat, first, [first])
        self.step(st2, seat, "undo", "undo")
        self.assertEqual(self.typed(st, 1 - seat, first), NOT_YOUR_TURN)
        self.assertIn("error", self.typed(st, seat, "13-9 6-5"))

    def test_step_forms(self):
        st = self.new()
        seat = st["turn"]
        for line, token in (("13-9", "13-9"), ("13/9", "13-9"), ("bar-22", "bar-22"), ("b-22", "bar-22"),
                            ("B/22", "bar-22"), ("6-off", "6-off"), ("6-o", "6-off"), ("6/OFF", "6-off")):
            self.assertEqual(self.typed(st, seat, line), {"move": [token]}, line)

    def test_roll_double_end_undo(self):
        self.doubled()

    def doubled(self):
        """A game with the cube: the opening turn played and ended, the next seat rolls and doubles.
        -> (state, the doubling seat)."""
        st = self.new()
        seat = st["turn"]
        self.assertEqual(self.typed(st, seat, "end"), {"step": "end", "what": "end your turn"})
        self.assertEqual(self.typed(st, seat, "undo"), {"step": "undo", "what": "undo a step"})
        st = self.finish_turn(st)
        other = st["turn"]
        self.assertEqual(other, 1 - seat)
        self.assertEqual(st["phase"], "roll")
        self.assertEqual(self.typed(st, other, "roll"), {"move": ["roll"], "what": "roll"})
        self.assertEqual(self.typed(st, seat, "roll"), NOT_YOUR_TURN)
        self.plays(st, other, "roll")
        st = self.plays(st, other, "double", ["double"])
        self.assertEqual(st["phase"], "take")
        return st, other

    def test_offers_are_answered_in_dialogs(self):
        st, doubler = self.doubled()
        taker = 1 - doubler
        for word in ("take", "drop", "accept", "reject", "decline", "TAKE"):
            self.assertEqual(self.typed(st, taker, word),
                             {"error": "A double is answered in its dialog, not from the command line."}, word)
            self.assertEqual(self.typed(st, doubler, word), {"error": "There is no offer for you to answer."})
        # an offer to resign
        st = self.new()
        seat = st["turn"]
        r = self.apply(st, seat, ["resign-gammon"])
        self.assertNotIn("error", r)
        st = r["state"]
        self.assertEqual(self.typed(st, 1 - seat, "accept"),
                         {"error": "An offer to resign is answered in its dialog, not from the command line."})

    def test_not_a_command(self):
        st = self.new()
        for line in ("", "hello", "pass", "13", "13-9-5", "roll 6"):
            self.assertEqual(self.typed(st, st["turn"], line), {}, line)


class Checkers(Typed):
    game = "checkers"

    def test_steps_end_undo(self):
        st = self.new()
        st2 = self.plays(st, 0, "11-15", ["11-15"])
        self.step(st2, 0, "undo", "undo")
        st3 = self.step(st2, 0, "end", "end")
        self.assertEqual(st3["turn"], 1)
        self.assertEqual(self.typed(st3, 0, "22-18"), NOT_YOUR_TURN)
        self.assertEqual(self.typed(st3, 0, "end"), NOT_YOUR_TURN)
        self.plays(st3, 1, "22-18")
        self.assertEqual(self.typed(st, 0, "15x22x31"), {"move": ["15x22x31"]})
        self.assertEqual(self.typed(st, 0, "15X22"), {"move": ["15x22"]})

    def test_draw_offer(self):
        st = self.new()
        self.assertEqual(self.typed(st, 1, "draw"), {"move": ["draw"], "what": "offer a draw"})   # off turn too
        st = self.plays(st, 1, "draw")
        self.assertEqual(st["phase"], "draw")
        for word in ("accept", "reject", "decline"):
            self.assertEqual(self.typed(st, 0, word),
                             {"error": "A draw offer is answered in its dialog, not from the command line."})
            self.assertEqual(self.typed(st, 1, word), {"error": "There is no offer for you to answer."})

    def test_not_a_command(self):
        st = self.new()
        for line in ("", "hello", "11", "roll", "pass"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


WORDS = "he hex ex quiz qi za ab be ax xi zax example"


class GridWords(Typed):
    game = "gridwords"
    rules = "standard"

    @classmethod
    def setUpClass(cls):
        f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        f.write("\n".join(WORDS.split()) + "\n")
        f.close()
        cls.path = f.name
        cls.old = os.environ.get("GRIDWORDS_WORDS")
        os.environ["GRIDWORDS_WORDS"] = cls.path

    @classmethod
    def tearDownClass(cls):
        if cls.old is None:
            os.environ.pop("GRIDWORDS_WORDS", None)
        else:
            os.environ["GRIDWORDS_WORDS"] = cls.old
        os.unlink(cls.path)

    def fresh(self, rack0="QUIZHEA", rack1="XEBAZ_I"):
        st = self.new()
        st["racks"] = [rack0, rack1]
        return st

    def with_he(self):
        """HE across from h8, played by seat 0: seat 1 to move."""
        st = self.plays(self.fresh(), 0, "h8 he", ["PLAY", "8", "8", "H", "HE"])
        self.assertEqual(st["turn"], 1)
        return st

    def test_play_forms(self):
        st = self.fresh()
        for line in ("h8 across QUIZ", "h8 QUIZ", "H8 a quiz", "h8 right QUIZ", "h8 h QUIZ", "play 8 8 H quiz"):
            self.assertEqual(self.typed(st, 0, line)["move"], ["PLAY", "8", "8", "H", "QUIZ"], line)
        for line in ("h8 down QUIZ", "h8 d QUIZ", "h8 v QUIZ", "play 8 8 v QUIZ"):
            self.assertEqual(self.typed(st, 0, line)["move"], ["PLAY", "8", "8", "V", "QUIZ"], line)
        reply = self.typed(st, 0, "h8 across QUIZ")
        self.assertEqual(reply["what"], "play QUIZ across from h8")
        st = self.plays(st, 0, "h8 across QUIZ")
        self.assertEqual(st["scores"][0], 46)

    def test_layout_over_board_tiles(self):
        st = self.with_he()
        # x typed beside HE plays HEX, from HE's first square
        reply = self.typed(st, 1, "j8 x")
        self.assertEqual(reply, {"move": ["PLAY", "8", "8", "H", "HEX"], "what": "play HEX across from h8"})
        st2 = self.plays(st, 1, "j8 x")
        self.assertEqual(st2["last"][4], "HEX")
        # down from below H: the H on the board starts the word
        self.assertEqual(self.typed(st, 1, "h9 down e")["move"], ["PLAY", "8", "8", "V", "HE"])
        # a blank
        self.assertEqual(self.typed(st, 1, "j8 _x")["move"], ["PLAY", "8", "8", "H", "HE_X"])
        self.plays(st, 1, "j8 _x")

    def test_layout_refusals(self):
        st = self.with_he()
        self.assertEqual(self.typed(st, 1, "h8 across X"),
                         {"error": "There is already a tile on h8. Start your word on an empty square."})
        self.assertEqual(self.typed(st, 1, "n8 across ABE"),
                         {"error": "From n8 across, those letters run off the board."})
        self.assertEqual(self.typed(st, 1, "p8 ABE"), {"error": "There is no square p8 on the board."})
        self.assertIn("_", self.typed(st, 1, "j8 x_")["error"])
        # off turn, the turn comes first
        self.assertEqual(self.typed(st, 0, "h8 across X"), NOT_YOUR_TURN)

    def test_check(self):
        st = self.fresh()
        self.assertEqual(self.typed(st, 0, "check h8 across QUIZ"), {"say": "QUIZ would score 46."})
        self.assertEqual(self.typed(st, 1, "check h8 BE"), {"say": "BE would score 8."})     # off turn
        self.assertEqual(self.typed(st, 1, "check h8 QUIZ"),
                         {"say": "QUIZ can't be played there. Your rack has no Q."})
        self.assertEqual(self.typed(st, 0, "check a1 QUIZ"),
                         {"say": "QUIZ can't be played there. The first word must cover the center square h8."})
        st = self.with_he()
        self.assertEqual(self.typed(st, 1, "check j8 x"), {"say": "HEX would score 11."})
        say = self.typed(st, 1, "check h9 across ex")["say"]       # the cross words are named too
        self.assertRegex(say, r"^EX, HE and EX would score [0-9]+\.$")
        self.assertEqual(self.typed(st, 1, "check j8 B"), {"say": "HEB can't be played there. "
                                                                  "HEB is not in the word list."})
        self.assertEqual(self.typed(st, 1, "check h8 X"),
                         {"say": "There is already a tile on h8. Start your word on an empty square."})
        self.assertIn("error", self.typed(st, 1, "check"))
        self.assertIn("error", self.typed(st, 1, "check hello"))
        self.assertNotEqual(self.typed(st, 1, "check j8 x"), {})

    def test_check_all_seven(self):
        st = self.fresh(rack0="EXAMPLE")
        score = self.P.evaluate(st, 0, 8, 8, "H", "EXAMPLE", None)["score"]
        self.assertEqual(score, 96)
        self.assertEqual(self.typed(st, 0, "check h8 example"),
                         {"say": "EXAMPLE would score 96, with 50 for using all seven tiles."})

    def test_pass_and_exchange(self):
        st = self.fresh()
        self.assertEqual(self.typed(st, 0, "pass"), {"move": ["PASS"], "ask": "pass", "what": "pass"})
        self.plays(st, 0, "pass")
        for line in ("exchange qz", "exch QZ", "swap Q Z"):
            self.assertEqual(self.typed(st, 0, line), {"move": ["EXCH", "QZ"], "what": "exchange those tiles"}, line)
        self.plays(st, 0, "exchange qz")
        self.assertIn("error", self.typed(st, 0, "exchange"))
        self.assertIn("error", self.typed(st, 0, "exchange ABCDEFGH"))
        self.assertEqual(self.typed(st, 1, "pass"), NOT_YOUR_TURN)
        self.assertEqual(self.typed(st, 1, "exchange q"), NOT_YOUR_TURN)
        self.assertEqual(self.typed(st, 1, "h8 QUIZ"), NOT_YOUR_TURN)

    def test_not_a_command(self):
        st = self.fresh()
        for line in ("", "hello", "h8", "h8 sideways QUIZ", "h8 QU1Z", "play 8 8 X QUIZ", "draw"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


class NavalCombat(Typed):
    game = "navalcombat"
    rules = "classic"

    def test_placement_tokens(self):
        self.placed()

    def placed(self):
        """Seat 0 places its fleet by typed squares and readies it -> the state."""
        st = self.new()
        self.assertEqual(st["fleet"], [5, 4, 3, 3, 2])
        self.assertEqual(self.typed(st, 0, "a1 across"), {"move": ["a1-e1"], "what": "place a ship at a1-e1"})
        st = self.plays(st, 0, "a1", ["a1-e1"])
        st = self.plays(st, 0, "a3 right", ["a3-d3"])
        st = self.plays(st, 0, "A5 down", ["a5-a7"])
        st = self.plays(st, 0, "c5 v", ["c5-c7"])
        self.assertEqual(self.typed(st, 0, "j1 across"),
                         {"error": "From j1 across, the next ship runs off the grid because it is 2 squares long."})
        self.assertEqual(self.typed(st, 0, "a10 down")["error"][:10], "From a10 d")
        self.assertEqual(self.typed(st, 0, "k1"), {"error": "There is no square k1 on the grid."})
        st = self.plays(st, 0, "e5 h", ["e5-f5"])
        self.assertIn("Every ship is placed", self.typed(st, 0, "h1")["error"])
        st = self.plays(st, 0, "undo", ["undo"])
        st = self.plays(st, 0, "e5-f5", ["e5-f5"])
        st = self.plays(st, 0, "ready", ["ready"])
        self.assertEqual(self.typed(st, 0, "h1"), {"error": "Your fleet is ready, so it can no longer change."})
        # seat 1 places at the same time, whoever is on the clock
        self.assertEqual(self.typed(st, 1, "b2 down")["move"], ["b2-b6"])
        return st

    def test_words(self):
        st = self.new()
        self.assertEqual(self.typed(st, 1, "reset")["move"], ["clear"])
        self.assertEqual(self.typed(st, 1, "clear")["move"], ["clear"])
        st = self.plays(st, 1, "random", ["random"])
        st = self.plays(st, 1, "reset", ["clear"])
        self.assertEqual(st["ships"][1], [])
        st = self.plays(st, 1, "random")
        self.plays(st, 1, "ready", ["ready"])

    def test_shooting(self):
        st = self.placed()
        st = self.plays(st, 1, "random")
        st = self.plays(st, 1, "ready")
        self.assertEqual(st["phase"], "shoot")
        self.assertEqual(self.typed(st, 0, "E5"), {"move": ["e5"], "what": "fire at e5"})
        self.assertEqual(self.typed(st, 1, "e5"), NOT_YOUR_TURN)
        self.assertEqual(self.typed(st, 0, "e5 across"), {})
        st = self.plays(st, 0, "e5")
        self.assertEqual(self.typed(st, 0, "e6"), NOT_YOUR_TURN)
        self.plays(st, 1, "e6")

    def test_not_a_command(self):
        st = self.new()
        for line in ("", "hello", "a1 sideways", "a1 down now", "pass"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


TILES = Counter("%s%d" % (c, n) for c in "RBKY" for n in range(1, 14) for _ in range(2)) + Counter({"J": 2})


class RummyTile(Typed):
    game = "rummytile"

    def state(self, racks, pool=None):
        """A state set up by hand: the racks as given, the pool the rest of the tiles (or as given)."""
        P = self.P
        used = Counter(t for r in racks for t in r)
        rest = list((TILES - used).elements())
        st = {"n": len(racks), "pool": rest if pool is None else list(pool), "racks": [P.sort_rack(r) for r in racks],
              "table": [], "melded": [False] * len(racks), "turn": 0, "over": False, "result": None,
              "scores": None, "passes": 0, "start": None, "steps": [], "gone": [False] * len(racks)}
        P.begin_turn(st, 0, [])
        return st

    def rack(self):
        return self.state([["R10", "R11", "R12", "R1", "R2", "R3", "B5", "K7"], ["B1", "B2", "B3", "Y9"]])

    def test_nothing_played(self):
        st = self.rack()
        self.assertEqual(self.typed(st, 0, "end"), {"step": "end", "what": "end your turn", "ask": "pass"})
        self.assertEqual(self.typed(st, 0, "pass"), {"step": "end", "ask": "pass", "what": "pass"})
        self.assertEqual(self.typed(st, 0, "check"),
                         {"say": "You have not played anything yet, so ending your turn now would take a tile."})
        self.step(st, 0, "pass", "end", ask="pass")
        empty = self.state([["R1", "R2"], ["B1"]], pool=[])
        self.assertEqual(self.typed(empty, 0, "check"),
                         {"say": "You have not played anything yet, so ending your turn now would pass."})

    def test_a_turn_that_counts(self):
        st = self.rack()
        self.assertEqual(self.typed(st, 0, "R10R11R12-n"), {"move": ["R10R11R12-N"]})
        st = self.plays(st, 0, "r10r11r12-n")
        self.assertEqual(self.typed(st, 0, "end"), {"step": "end", "what": "end your turn"})
        self.assertEqual(self.typed(st, 0, "check"),
                         {"say": "Your turn would count. You have played 3 tiles. Your first meld is 33 points."})
        self.assertEqual(self.typed(st, 0, "pass"),
                         {"error": "You have already played this turn. End your turn with /end, or take your "
                                   "steps back with /undo."})
        self.step(st, 0, "undo", "undo")
        st2 = self.step(st, 0, "end", "end")
        self.assertEqual(st2["turn"], 1)
        self.assertEqual(st2["table"], [["R10", "R11", "R12"]])

    def test_a_turn_that_would_not_count(self):
        st = self.plays(self.rack(), 0, "R1R2R3-n")
        self.assertEqual(self.typed(st, 0, "end"), {"step": "end", "what": "end your turn", "ask": "end"})
        self.assertEqual(self.typed(st, 0, "check"),
                         {"say": "Your turn would not count yet. Your first meld is 6 points, and it needs at least "
                                 "30. If you end it now, your tiles go back and you take a tile."})
        bad = self.plays(self.rack(), 0, "B5K7-n")
        self.assertEqual(self.typed(bad, 0, "check")["say"],
                         "Your turn would not count yet. Set 1 is not valid. If you end it now, your tiles go back "
                         "and you take a tile.")
        self.step(bad, 0, "end", "end", ask="end")
        # a step played then reset leaves the table as it was: ending is a pass again
        st = self.plays(st, 0, "reset", ["reset"])
        self.assertEqual(self.typed(st, 0, "end")["ask"], "pass")

    def test_step_tokens(self):
        st = self.rack()
        st = self.plays(st, 0, "R10R11R12-n R1R2R3-N")
        self.assertEqual(len(st["table"]), 2)
        for line in ("3.2-5", "3.R7-n", "3.2-r", "3-5", "3/4", "R7-3 B7-3"):
            self.assertEqual(self.typed(st, 0, line), {"move": line.upper().split()}, line)
        st = self.plays(st, 0, "1/2")
        self.assertEqual(len(st["table"]), 3)

    def test_off_turn_and_not_a_command(self):
        st = self.rack()
        for line in ("R1-n", "end", "undo", "reset", "pass", "check"):
            self.assertEqual(self.typed(st, 1, line), NOT_YOUR_TURN, line)
        for line in ("", "hello", "split", "sort", "R1", "check R1-n", "R1-n hello"):
            self.assertEqual(self.typed(st, 0, line), {}, line)


if __name__ == "__main__":
    unittest.main()
