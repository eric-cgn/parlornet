#!/usr/bin/env python3
"""backgammon/1 game plugin (docs/types/backgammon-1.md).

Pure functions over an opaque state (docs/plugins.md). Absolute points 1-24:
seat 0 moves from 24 down to 1 and bears off below 1; seat 1 moves from 1 up
to 24 and bears off above 24. Counts are signed: positive for seat 0,
negative for seat 1. The server rolls all dice from the table's seed.

Turns are staged: each MOV is one checker step ("13-9", "bar-22", "6-off");
"!undo" takes back the last step and "!end" commits the turn (the core sends
these for UND and END). With the cube on, a turn starts with "roll" or
"double"; the other player answers "take" or "drop".

Either player may offer to resign at a level ("resign-single", "resign-gammon",
"resign-backgammon"); the other answers "accept" or "reject". With the option
match=n a table plays a match to n points: several games, the score, and the
Crawford rule (no doubling in the game after a player first reaches n-1).
"""
import json
import re
import random
import sys

INFO = {
    "type": "backgammon", "ver": 1, "name": "Backgammon",
    "rules": ["standard"], "min": 2, "max": 2, "flags": ["staged"],
    "options": ["cube", "match"],
}
LEVELS = {"single": 1, "gammon": 2, "backgammon": 3}
MAX_MATCH = 99
START = {24: 2, 13: 5, 8: 3, 6: 5}      # seat 0's checkers; seat 1 mirrors them


# ---- dice ---------------------------------------------------------------------

def roll(st):
    """Two dice from the seed and the roll counter (deterministic)."""
    rng = random.Random(st["seed"] * 1000003 + st["rolls"])
    st["rolls"] += 1
    return [rng.randint(1, 6), rng.randint(1, 6)]


def dice_for(d):
    return [d[0]] * 4 if d[0] == d[1] else list(d)


# ---- board helpers --------------------------------------------------------------

def owner(n):
    return 0 if n > 0 else 1 if n < 0 else -1


def sign(seat):
    return 1 if seat == 0 else -1


def home_range(seat):
    return range(1, 7) if seat == 0 else range(19, 25)


def all_home(b, seat):
    if b["bar"][seat]:
        return False
    s = sign(seat)
    for p in range(1, 25):
        if b["pts"][p - 1] * s > 0 and p not in home_range(seat):
            return False
    return True


def dest(seat, frm, d):
    if frm == "bar":
        return 25 - d if seat == 0 else d
    return frm - d if seat == 0 else frm + d


def single_steps(b, seat, d):
    """Legal single steps for one die value: list of (from, to) with to 'off'."""
    s = sign(seat)
    out = []
    if b["bar"][seat]:
        t = dest(seat, "bar", d)
        if b["pts"][t - 1] * -s < 2:
            out.append(("bar", t))
        return out
    home = all_home(b, seat)
    for p in range(1, 25):
        if b["pts"][p - 1] * s <= 0:
            continue
        t = dest(seat, p, d)
        if 1 <= t <= 24:
            if b["pts"][t - 1] * -s < 2:
                out.append((p, t))
        elif home:
            exact = (t == 0) if seat == 0 else (t == 25)
            if exact:
                out.append((p, "off"))
            else:
                # overshoot allowed only from the farthest checker
                farther = range(p + 1, 7) if seat == 0 else range(19, p)
                if not any(b["pts"][q - 1] * s > 0 for q in farther):
                    out.append((p, "off"))
    return out


def apply_step(b, seat, frm, to):
    """Returns (new board, hit?)."""
    b = {"pts": list(b["pts"]), "bar": list(b["bar"]), "off": list(b["off"])}
    s = sign(seat)
    if frm == "bar":
        b["bar"][seat] -= 1
    else:
        b["pts"][frm - 1] -= s
    hit = False
    if to == "off":
        b["off"][seat] += 1
    else:
        if b["pts"][to - 1] * -s == 1:
            b["pts"][to - 1] = 0
            b["bar"][1 - seat] += 1
            hit = True
        b["pts"][to - 1] += s
    return b, hit


def _key(b, dice):
    return (tuple(b["pts"]), tuple(b["bar"]), tuple(b["off"]), tuple(sorted(dice)))


def max_play(b, seat, dice, memo):
    """How many of the dice can be played from here (memoized)."""
    if b["off"][seat] == 15:
        return len(dice)        # the game is won: every remaining die counts as played
    k = _key(b, dice)
    if k in memo:
        return memo[k]
    best = 0
    tried = set()
    for i, d in enumerate(dice):
        if d in tried:
            continue
        tried.add(d)
        for frm, to in single_steps(b, seat, d):
            nb, _ = apply_step(b, seat, frm, to)
            best = max(best, 1 + max_play(nb, seat, dice[:i] + dice[i + 1:], memo))
            if best == len(dice):
                memo[k] = best
                return best
    memo[k] = best
    return best


def board_of(st):
    return {"pts": st["pts"], "bar": st["bar"], "off": st["off"]}


def legal_steps(st):
    """Single steps that begin a way of playing as many dice as possible
    (and the larger die when only one of two can be played): {(from, to): die}."""
    b, seat, dice = board_of(st), st["turn"], list(st["dice"])
    memo = {}
    total = max_play(b, seat, dice, memo)
    if total == 0:
        return {}
    out = {}
    for i, d in enumerate(dice):
        for frm, to in single_steps(b, seat, d):
            nb, _ = apply_step(b, seat, frm, to)
            if 1 + max_play(nb, seat, dice[:i] + dice[i + 1:], memo) == total:
                out.setdefault((frm, to), d)
    # the larger-die rule, for the first step of a two-dice turn where only one can be played
    if total == 1 and len(dice) == 2 and dice[0] != dice[1] and not st.get("steps"):
        big = max(dice)
        if any(d == big for d in out.values()):
            out = {k: d for k, d in out.items() if d == big}
    return out


def tok(frm, to):
    return "%s-%s" % (frm, to)


def parse_tok(t):
    a, _, b = t.lower().partition("-")
    try:
        frm = "bar" if a == "bar" else int(a)
        to = "off" if b == "off" else int(b)
    except ValueError:
        return None
    return frm, to


# ---- state ------------------------------------------------------------------------

def new_state(seed, cube):
    pts = [0] * 24
    for p, n in START.items():
        pts[p - 1] += n
        pts[(25 - p) - 1] -= n
    st = {"pts": pts, "bar": [0, 0], "off": [0, 0], "seed": seed, "rolls": 0, "turn": 0,
          "dice": [], "rolled": [], "phase": "move", "cube": 1, "cube_owner": -1, "use_cube": cube,
          "steps": [], "start": None, "over": False, "result": None,
          "need": 0, "offer": None, "match": 0, "score": [0, 0], "game": 1, "crawford": False,
          "crawford_done": False}
    return st


def reset_board(st):
    """The starting position, for the next game of a match."""
    fresh = new_state(st["seed"], st["use_cube"])
    for k in ("pts", "bar", "off", "dice", "rolled", "cube", "cube_owner", "steps", "start", "need", "offer"):
        st[k] = fresh[k]


def opening(st, events):
    """One die each (ties are rolled again); the higher die starts and plays both."""
    while True:
        d = roll(st)
        if d[0] != d[1]:
            break
    first = 0 if d[0] > d[1] else 1
    st["turn"] = first
    st["rolled"] = d
    st["dice"] = list(d)
    st["phase"] = "move"
    st["start"] = {"pts": list(st["pts"]), "bar": list(st["bar"]), "off": list(st["off"]), "dice": list(st["dice"])}
    st["need"] = max_play(board_of(st), first, list(d), {})
    events += ["OPEN %d %d" % (d[0], d[1]), "TURN %d" % first, "ROLL %d %d %d" % (first, d[0], d[1]),
               "STEPS %d %d" % (first, st["need"])]


def game_over(st, winner, points, kind, events):
    """A game is won. Returns the table's result when the table is over (a
    single game, or the match is decided); otherwise the next game of the
    match begins and None is returned."""
    result = "win %d %d %s" % (winner, points, kind)
    if not st.get("match"):
        st["over"], st["result"] = True, result
        return result
    st["score"][winner] += points
    events += ["WON %d %d %s" % (winner, points, kind), "SCORE %d %d" % tuple(st["score"])]
    if st["score"][winner] >= st["match"]:
        st["over"], st["result"] = True, result
        return result
    crawford = not st["crawford_done"] and st["score"][winner] == st["match"] - 1
    st["crawford"] = crawford
    if crawford:
        st["crawford_done"] = True
    st["game"] += 1
    reset_board(st)
    events.append("GAME %d%s" % (st["game"], " crawford" if crawford else ""))
    opening(st, events)
    return None


def finish(st, events, winner, points, kind):
    """An apply reply for a won game: ends the table or goes on with the match."""
    end = game_over(st, winner, points, kind, events)
    out = {"state": st, "events": events}
    if end:
        out["end"] = end
    return out


def cube_ok(st, seat):
    """May seat double when its turn starts?"""
    return bool(st["use_cube"] and not st.get("crawford") and st["cube"] < 64 and st["cube_owner"] in (-1, seat))


def begin_turn(st, seat, events):
    """Start seat's turn: offer the cube or roll."""
    st["turn"] = seat
    st["steps"] = []
    events.append("TURN %d" % seat)
    if cube_ok(st, seat):
        st["phase"] = "roll"
        st["dice"] = []
        return
    do_roll(st, seat, events)


def do_roll(st, seat, events):
    d = roll(st)
    st["rolled"] = d
    st["dice"] = dice_for(d)
    st["phase"] = "move"
    st["start"] = {"pts": list(st["pts"]), "bar": list(st["bar"]), "off": list(st["off"]), "dice": list(st["dice"])}
    events.append("ROLL %d %d %d" % (seat, d[0], d[1]))
    st["need"] = max_play(board_of(st), seat, list(st["dice"]), {})
    if st["need"]:
        events.append("STEPS %d %d" % (seat, st["need"]))
    else:
        events.append("PASS %d" % seat)
        begin_turn(st, 1 - seat, events)


def won_points(st, winner):
    """(points, kind) for a game won by bearing off."""
    loser = 1 - winner
    mult, kind = 1, "single"
    if st["off"][loser] == 0:
        mult, kind = 2, "gammon"
        s = sign(loser)
        in_home = any(st["pts"][p - 1] * s > 0 for p in home_range(winner))
        if st["bar"][loser] or in_home:
            mult, kind = 3, "backgammon"
    return mult * st["cube"], kind


def result_of(st, winner):
    return "win %d %d %s" % ((winner,) + won_points(st, winner))


def offer_resign(st, seat, kind, check):
    """Offer to resign at a level; the other player answers accept or reject.
    Allowed on either player's turn, except while a double is pending."""
    if kind not in LEVELS:
        return {"error": "resign-single, resign-gammon or resign-backgammon"}
    if st["phase"] not in ("roll", "move"):
        return {"error": "answer the double first" if st["phase"] == "take" else "a resignation is pending"}
    if check:
        return {"info": "resign"}
    other = 1 - seat
    points = LEVELS[kind] * st["cube"]
    st["offer"] = {"seat": seat, "points": points, "kind": kind, "turn": st["turn"], "phase": st["phase"]}
    st["phase"] = "resign"
    events = ["OFFER %d %d %s" % (seat, points, kind)]
    if st["turn"] != other:
        st["turn"] = other
        events.append("TURN %d" % other)
    return {"state": st, "events": events}


# ---- ops ----------------------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    if req.get("players") != 2:
        return {"error": "two players"}
    opts = req.get("options") or {}
    cube = opts.get("cube", "1")
    if cube not in ("0", "1"):
        return {"error": "cube is 0 or 1"}
    match = opts.get("match", "0")
    if not match.isdigit() or int(match) > MAX_MATCH:
        return {"error": "match is a number of points, 0 (one game) to %d" % MAX_MATCH}
    st = new_state(int(req.get("seed") or 0), cube == "1")
    st["match"] = int(match)
    events = ["GAME 1"] if st["match"] else []
    opening(st, events)
    return {"state": st, "events": events}


def moving(st):
    """Has the side to move rolled (also while a resign offer interrupts)?"""
    off = st.get("offer")
    return (off["phase"] if off and st["phase"] == "resign" else st["phase"]) == "move"


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    p = st["pts"]
    wire = ["P 1 " + " ".join(str(n) for n in p[:12]),
            "P 13 " + " ".join(str(n) for n in p[12:]),
            "B %d %d" % tuple(st["bar"]), "O %d %d" % tuple(st["off"]),
            ("D " + " ".join(str(d) for d in st["dice"])).rstrip(),
            "C %d %s" % (st["cube"], "-" if st["cube_owner"] < 0 else st["cube_owner"]),
            "W %s" % ("-" if st["over"] else st["turn"]),
            "Q %s" % ("over" if st["over"] else st["phase"])]
    # added lines (older clients skip tags they don't know)
    live = not st["over"] and moving(st)
    wire.append("N %d" % (st.get("need", 0) if live else 0))
    if live and st.get("rolled"):
        wire.append("DR %d %d" % tuple(st["rolled"]))
    wire.append("CA %d %d" % (cube_ok(st, 0), cube_ok(st, 1)))
    if st.get("match"):
        wire += ["L %d" % st["match"], "T 0 %d" % st["score"][0], "T 1 %d" % st["score"][1],
                 "G %d%s" % (st["game"], " crawford" if st.get("crawford") else "")]
    off = st.get("offer")
    if off and not st["over"]:
        wire.append("RS %d %d %s" % (off["seat"], off["points"], off["kind"]))
    if st["over"]:
        status = "Game over: " + st["result"]
    elif st["phase"] == "resign":
        status = ("Your opponent offers to resign (%d points, %s): accept or reject" % (off["points"], off["kind"])
                  if seat == st["turn"] else "Seat %d offers to resign" % off["seat"])
    elif st["phase"] == "take":
        status = "Doubled to %d: take or drop" % (st["cube"] * 2) if seat == st["turn"] else \
            "Waiting for the answer to your double"
    elif seat == st["turn"]:
        status = {"roll": "Your turn: roll or double", "move": "Your move"}[st["phase"]]
        if st["phase"] == "move" and not legal_steps(st):
            status = "Turn complete: END to finish, UND to take back"
    elif seat < 0:
        status = "Seat %d to play" % st["turn"]
    else:
        status = "Waiting for the other player"
    return {"wire": wire, "geometry": ["BRD 24"], "status": status,
            "data": {k: st.get(k) for k in ("pts", "bar", "off", "dice", "cube", "cube_owner", "turn", "phase",
                                            "need", "rolled", "match", "score", "game", "crawford", "offer")}}


def op_moves(req):
    st, seat = req["state"], req.get("seat", -1)
    if st["over"] or seat != st["turn"]:
        return {"moves": []}
    if st["phase"] == "roll":
        return {"moves": ["roll", "double"]}
    if st["phase"] == "take":
        return {"moves": ["take", "drop"]}
    if st["phase"] == "resign":
        return {"moves": ["accept", "reject"]}
    return {"moves": sorted(tok(f, t) for f, t in legal_steps(st))}


def op_apply(req):
    st = json.loads(json.dumps(req["state"]))
    seat, move = req.get("seat", -1), [m.lower() for m in (req.get("move") or [])]
    check = req.get("check")
    if st["over"]:
        return {"error": "the game is over"}
    if len(move) != 1:
        return {"error": "one step at a time, like 13-9"}
    m = move[0]
    events = []
    if m.startswith("resign-") and seat in (0, 1):
        return offer_resign(st, seat, m[7:], check)
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if st["phase"] == "resign":
        off = st["offer"]
        if m not in ("accept", "reject"):
            return {"error": "accept or reject the resignation first"}
        if check:
            return {"info": m}
        st["offer"] = None
        if m == "accept":
            events.append("ACCEPT %d" % seat)
            return finish(st, events, seat, off["points"], off["kind"])
        events.append("REJECT %d" % seat)
        st["phase"] = off["phase"]
        if off["turn"] != st["turn"]:
            st["turn"] = off["turn"]
            events.append("TURN %d" % st["turn"])
        return {"state": st, "events": events}
    if st["phase"] == "roll":
        if m == "roll":
            if check:
                return {"info": "roll"}
            do_roll(st, seat, events)
            return {"state": st, "events": events, "staged": "more" if st["turn"] == seat else None}
        if m == "double":
            if check:
                return {"info": "double"}
            st["phase"] = "take"
            st["turn"] = 1 - seat
            events.append("DBL %d %d" % (seat, st["cube"] * 2))
            events.append("TURN %d" % (1 - seat))
            return {"state": st, "events": events}
        return {"error": "roll or double first"}
    if st["phase"] == "take":
        doubler = 1 - seat
        if m == "take":
            if check:
                return {"info": "take"}
            st["cube"] *= 2
            st["cube_owner"] = seat
            events.append("TAKE %d %d" % (seat, st["cube"]))
            st["turn"] = doubler
            events.append("TURN %d" % doubler)
            do_roll(st, doubler, events)
            return {"state": st, "events": events}
        if m == "drop":
            if check:
                return {"info": "drop"}
            events.append("DROP %d" % seat)
            return finish(st, events, doubler, st["cube"], "single")
        return {"error": "take or drop"}
    # moving
    if m == "!undo":
        if not st["steps"]:
            return {"error": "nothing to take back"}
        last = st["steps"].pop()
        st["pts"], st["bar"], st["off"], st["dice"] = last["before"]
        events.append("UNDO %d %s %s" % (seat, last["from"], last["to"]))
        return {"state": st, "events": events, "staged": "more"}
    if m == "!end":
        if legal_steps(st):
            return {"error": "you still have moves to play"}
        if check:
            return {"info": "end"}
        if st["off"][seat] == 15:
            return finish(st, events, seat, *won_points(st, seat))
        begin_turn(st, 1 - seat, events)
        return {"state": st, "events": events}
    pt = parse_tok(m)
    if pt is None:
        return {"error": "a step is from-to, like 13-9, bar-22 or 6-off"}
    legal = legal_steps(st)
    if pt not in legal:
        return {"error": "%s is not a legal step now" % m}
    if check:
        return {"info": "ok"}
    d = legal[pt]
    before = [list(st["pts"]), list(st["bar"]), list(st["off"]), list(st["dice"])]
    nb, hit = apply_step(board_of(st), seat, pt[0], pt[1])
    st["pts"], st["bar"], st["off"] = nb["pts"], nb["bar"], nb["off"]
    st["dice"].remove(d)
    st["steps"].append({"from": str(pt[0]), "to": str(pt[1]), "before": before})
    events.append("STEP %d %s %s%s" % (seat, pt[0], pt[1], " HIT" if hit else ""))
    if st["off"][seat] == 15:
        # bearing off the last checker ends the game at once
        return finish(st, events, seat, *won_points(st, seat))
    return {"state": st, "events": events, "staged": "done" if not legal_steps(st) else "more"}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events (the steps taken back left out): "rolled 6-3, moved 13/7 8/5*",
    "doubled to 2", "took the cube at 2"."""
    seat, roll, steps, out = str(req.get("seat")), "", [], []
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != seat:
            continue
        k = p[0]
        if k == "ROLL" and len(p) >= 4:
            roll = "rolled %s-%s" % (p[2], p[3])
        elif k == "STEP" and len(p) >= 4:
            steps.append("%s/%s%s" % (p[2], p[3], "*" if len(p) > 4 else ""))
        elif k == "UNDO" and steps:
            steps.pop()
        elif k == "DBL" and len(p) >= 3:
            out.append("doubled to %s" % p[2])
        elif k == "TAKE" and len(p) >= 3:
            out.append("took the cube at %s" % p[2])
        elif k == "DROP":
            out.append("dropped the cube")
        elif k == "OFFER":
            out.append("offered to resign")
        elif k in ("ACCEPT", "REJECT"):
            out.append("%sed the resignation" % ("accept" if k == "ACCEPT" else "reject"))
    if roll:
        out.append(roll + (", moved " + " ".join(steps) if steps else ""))
    elif steps:
        out.append("moved " + " ".join(steps))
    return {"say": "; ".join(out)}


# ---- a typed command line (the core's typed request; docs/plugins.md) ----------------------------------

INFO["typed"] = True
TYPED_STEP_RE = re.compile(r"^(bar|b|[0-9]{1,2})[-/](off|o|[0-9]{1,2})\Z")
TYPED_COMMANDS = {"roll": ({"move": ["roll"]}, "roll"), "double": ({"move": ["double"]}, "double"),
                  "end": ({"step": "end"}, "end your turn"), "undo": ({"step": "undo"}, "undo a step")}
OFFER_ANSWERS = ("take", "drop", "accept", "reject", "decline")


def offer_answer_refused(st, seat):
    """Why an offer is not answered from the command line, as a sentence."""
    if st["phase"] == "take" and seat == st["turn"]:
        return "A double is answered in its dialog, not from the command line."
    if st["phase"] == "resign" and seat == st["turn"]:
        return "An offer to resign is answered in its dialog, not from the command line."
    return "There is no offer for you to answer."


def op_typed(req):
    """The words typed after a slash at the table -> what the core does with them. A step, as /13-9,
    /bar-22, /b-22, /6-off or /6-o, moves a checker. /roll and /double start a turn, and /end and /undo
    end the turn or take back a step. Offers are answered in their dialogs, so /take, /drop, /accept,
    /reject and /decline are refused."""
    st, seat = req["state"], req.get("seat", -1)
    words = [w.lower() for w in req.get("words") or []]
    if not words:
        return {}
    if len(words) == 1 and words[0] in OFFER_ANSWERS:
        return {"error": offer_answer_refused(st, seat)}
    if len(words) == 1 and words[0] in TYPED_COMMANDS:
        reply, what = TYPED_COMMANDS[words[0]]
        reply = dict(reply, what=what)
    elif all(TYPED_STEP_RE.match(w) for w in words):
        if len(words) > 1:
            return {"error": "Type one step at a time, as in /13-9."}
        a, b = TYPED_STEP_RE.match(words[0]).groups()
        reply = {"move": ["%s-%s" % ("bar" if a == "b" else a, "off" if b == "o" else b)]}
    else:
        return {}
    if seat != st["turn"]:
        return {"error": "It's not your turn."}
    if "move" in reply:
        reply["move"] = list(reply["move"])
    return reply


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply, "say": op_say,
       "typed": op_typed}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            reply = OPS[req["op"]](req)
        except Exception as e:  # a bad request must not kill the plugin
            reply = {"error": "plugin: %s" % e}
        sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
