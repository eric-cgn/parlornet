#!/usr/bin/env python3
"""rummytile/1 game plugin (docs/types/rummytile-1.md): Rummy Tile Classic.

Pure functions over an opaque state (docs/plugins.md). 106 tiles: 1-13 in
four colors (R red, B blue, K black, Y yellow), two of each, and two jokers
(J). A tile is a string: "R7", "B12", "J". The table is a list of sets, each
a list of tiles kept in a tidy order (runs by number with jokers in place,
groups by color), so a position in a set always means the same thing to
every client.

Turns are staged: each MOV is one step that changes the table and the
player's rack tentatively; "!undo" takes back the last step and "!end" ends
the turn (the core sends these for UND and END). At the end every set on the
table must be valid, at least one rack tile must have been played, the first
meld must reach 30 points from the player's own tiles, and a joker taken from
the table must have had the tile it stood for played from the rack. If not,
the turn is undone and the player draws a tile (or passes when the pool is
empty).

Step tokens (case-insensitive; sets are numbered from 1 in table order):
  R7-3       rack tile(s) into set 3        R7B7K7-n  rack tiles into a new set
  3.2-5      tile 2 of set 3 to set 5       3.R7-n    R7 of set 3 to a new set
  3.2-r      a tile played this turn back to the rack
  3-5        all of set 3 onto set 5        3/4       split set 3 before its tile 4
  reset      start the turn over (UND takes the reset back)

The extra op "drop" (flag "drop") lets one seat leave a game of three or four
(it resigned or ran out of time) while the others play on; the core calls it
for RES and turn timeouts while more than two seats are still playing.
"""
import json
import random
import re
import sys
from collections import Counter

INFO = {
    "type": "rummytile", "ver": 1, "name": "Rummy Tile Classic",
    "rules": ["standard"], "min": 2, "max": 4, "flags": ["staged", "nomoves", "drop"],
    "options": [],
}
COLOURS = "RBKY"
HAND = 14
MELD = 30
JOKER_PENALTY = 30
MAX_SET = 13
MAX_SETS = 99
MAX_STEPS = 100
RACK_LINE = 16          # tiles per H line (keeps view lines under 80 bytes)

TILE = r"(?:[RBKY](?:1[0-3]|[1-9])|J)"
TILE_RE = re.compile(TILE)
PLACE_RE = re.compile(r"^((?:%s)+)-([0-9]{1,2}|N)\Z" % TILE)
MOVE_RE = re.compile(r"^([0-9]{1,2})\.([0-9]{1,2}|%s)-([0-9]{1,2}|N|R)\Z" % TILE)
MERGE_RE = re.compile(r"^([0-9]{1,2})-([0-9]{1,2})\Z")
SPLIT_RE = re.compile(r"^([0-9]{1,2})/([0-9]{1,2})\Z")
REASONS = {"none": "nothing was played from the rack", "sets": "a set on the table is not valid",
           "meld": "the first meld is under %d points" % MELD,
           "joker": "a joker was taken without playing the tile it stood for"}


class Illegal(Exception):
    pass


# ---- tiles and sets --------------------------------------------------------------

def num(t):
    return int(t[1:])


def tile_key(t):
    return (4, 0) if t == "J" else (COLOURS.index(t[0]), num(t))


def sort_rack(rack):
    return sorted(rack, key=tile_key)


def all_tiles():
    return [c + str(n) for c in COLOURS for n in range(1, 14) for _ in (0, 1)] + ["J", "J"]


def split_set(s):
    nat = [t for t in s if t != "J"]
    return nat, len(s) - len(nat)


def runlike(s):
    """(color, lowest start, highest start) when the tiles can lie in one run in
    some order, else None. The run need not be 3 long yet."""
    nat, jk = split_set(s)
    if not nat or len(s) > MAX_SET or len({t[0] for t in nat}) != 1:
        return None
    nums = [num(t) for t in nat]
    if len(set(nums)) != len(nums):
        return None
    lo, hi, n = min(nums), max(nums), len(s)
    if hi - lo + 1 > n:
        return None
    return nat[0][0], max(1, hi - n + 1), min(lo, 14 - n)


def grouplike(s):
    nat, jk = split_set(s)
    return bool(nat) and len(s) <= 4 and len({num(t) for t in nat}) == 1 and len({t[0] for t in nat}) == len(nat)


def kind(s):
    """'r' a valid run, 'g' a valid group, 'x' not a valid set."""
    if len(s) < 3:
        return "x"
    if runlike(s):
        return "r"
    return "g" if grouplike(s) else "x"


def tidy(s):
    """The set in its shown order. A run: by number, jokers in the gaps, spare
    jokers at the top end (at the bottom once the top reaches 13). A group, or
    anything else: by color and number, jokers last."""
    nat, jk = split_set(s)
    r = runlike(s)
    if r:
        colour, _, start = r
        have = {num(t) for t in nat}
        return [colour + str(v) if v in have else "J" for v in range(start, start + len(s))]
    return sorted(nat, key=tile_key) + ["J"] * jk


def value(s):
    """Points of a set as it lies (a joker counts as the tile it stands for)."""
    r = runlike(s)
    if r:
        start = r[2]
        return sum(range(start, start + len(s)))
    nat, _ = split_set(s)
    return num(nat[0]) * len(s) if nat else 0


def joker_options(s):
    """Every tile a joker in this valid set could stand for."""
    nat, jk = split_set(s)
    out = set()
    if not jk or not nat:
        return out
    r = runlike(s)
    if r and len(s) >= 3:
        colour, lo, hi = r
        have = {num(t) for t in nat}
        for start in range(lo, hi + 1):
            out |= {colour + str(v) for v in range(start, start + len(s)) if v not in have}
    if len(s) >= 3 and grouplike(s):
        n = num(nat[0])
        out |= {c + str(n) for c in COLOURS if c not in {t[0] for t in nat}}
    return out


def rack_value(rack):
    return sum(JOKER_PENALTY if t == "J" else num(t) for t in rack)


def jokers_ok(start, table, played):
    """The joker rule: every joker that lay on the table when the turn began
    still stands for a tile it could stand for then, or a tile it could stand
    for was played from the rack this turn (each played tile frees one joker)."""
    before = [joker_options(s) for s in start for t in s if t == "J"]
    after = [joker_options(s) for s in table for t in s if t == "J"]
    avail = Counter({t: n for t, n in played.items() if t != "J"})

    def fit(i, used):
        if i == len(before):
            return True
        for j, opts in enumerate(after):
            if j not in used and opts & before[i] and fit(i + 1, used | {j}):
                return True
        for t in sorted(before[i]):
            if avail[t] > 0:
                avail[t] -= 1
                ok = fit(i + 1, used)
                avail[t] += 1
                if ok:
                    return True
        return False
    return fit(0, frozenset())


# ---- the turn ----------------------------------------------------------------------

def played_from(st, rack):
    """The tiles played from the mover's rack this turn (a Counter)."""
    return Counter(st["start"]["rack"]) - Counter(rack)


def evaluate(st):
    """-> (code, tiles played, first-meld points or None, bad set numbers) for the turn so far.
    code: ok (END commits), none (nothing played), sets, meld, joker."""
    seat = st["turn"]
    played = played_from(st, st["racks"][seat])
    n = sum(played.values())
    table, k0 = st["table"], st["start"]["k0"]
    bad = [i + 1 for i, s in enumerate(table) if kind(s) == "x"]
    pts = None if st["melded"][seat] else sum(value(s) for s in table[k0:] if kind(s) != "x")
    if n == 0:
        code = "none"
    elif bad:
        code = "sets"
    elif pts is not None and pts < MELD:
        code = "meld"
    elif not jokers_ok(st["start"]["table"], table, played):
        code = "joker"
    else:
        code = "ok"
    return code, n, pts, bad


def begin_turn(st, seat, events):
    st["turn"] = seat
    st["start"] = {"table": [list(s) for s in st["table"]], "rack": list(st["racks"][seat]),
                   "k0": len(st["table"])}
    st["steps"] = []
    events.append("TURN %d" % seat)


def deltas(old, new, removed):
    """Events that turn the old table into the new one: DEL k (old numbers, highest
    first), then SET k kind tiles for every set changed or added (new numbers)."""
    out = ["DEL %d" % (i + 1) for i in sorted(removed, reverse=True)]
    kept = [i for i in range(len(old)) if i not in removed]
    for j, s in enumerate(new):
        if j >= len(kept) or old[kept[j]] != s:
            out.append("SET %d %s %s" % (j + 1, kind(s), " ".join(s)))
    return out


class Work:
    """One step's changes to the table and the mover's rack."""

    def __init__(self, st, seat):
        self.st, self.seat = st, seat
        self.table = [list(s) for s in st["table"]]
        self.rack = list(st["racks"][seat])
        self.events = []

    def set_at(self, k, what="set"):
        if not 1 <= k <= len(self.table):
            raise Illegal("there is no set %d" % k)
        if not self.st["melded"][self.seat] and k <= self.st["start"]["k0"]:
            raise Illegal("before your first meld you may only build new sets from your own tiles")
        return self.table[k - 1]

    def room(self, s, more):
        if len(s) + more > MAX_SET:
            raise Illegal("a set holds at most %d tiles" % MAX_SET)

    def new_set(self, tiles):
        if len(self.table) >= MAX_SETS:
            raise Illegal("the table holds at most %d sets" % MAX_SETS)
        self.table.append(tidy(tiles))

    def finish(self, old):
        removed = [i for i, s in enumerate(self.table) if not s]
        self.table = [s for s in self.table if s]
        self.events += deltas(old, self.table, removed)

    def step(self, tok):
        old = [list(s) for s in self.table]
        t = tok.upper()
        seat = self.seat
        m = PLACE_RE.match(t)
        if m:
            tiles = TILE_RE.findall(m.group(1))
            have = Counter(self.rack)
            need = Counter(tiles)
            for x, n in need.items():
                if have[x] < n:
                    raise Illegal("you have %s %s on your rack" % ("no" if not have[x] else "only %d" % have[x], x))
            if len(tiles) > MAX_SET:
                raise Illegal("a set holds at most %d tiles" % MAX_SET)
            if m.group(2) == "N":
                self.new_set(tiles)
            else:
                k = int(m.group(2))
                s = self.set_at(k)
                self.room(s, len(tiles))
                self.table[k - 1] = tidy(s + tiles)
            for x in tiles:
                self.rack.remove(x)
            self.events.append("PUT %d %s %s" % (seat, "".join(tiles), m.group(2).lower()))
            return self.finish(old)
        m = MOVE_RE.match(t)
        if m:
            k = int(m.group(1))
            s = self.set_at(k)
            which, dest = m.group(2), m.group(3)
            if which.isdigit():
                pos = int(which)
                if not 1 <= pos <= len(s):
                    raise Illegal("set %d has no tile %d" % (k, pos))
            elif which in s:
                pos = s.index(which) + 1
            else:
                raise Illegal("set %d has no %s" % (k, which))
            tile = s[pos - 1]
            rest = s[:pos - 1] + s[pos:]
            if dest == "R":
                if played_from(self.st, self.rack)[tile] <= 0:
                    raise Illegal("only tiles played from your rack this turn can go back to it")
                self.rack.append(tile)
                self.events.append("BACK %d %d %s" % (seat, k, tile))
            elif dest == "N":
                self.new_set([tile])
                self.events.append("MOVE %d %d %s n" % (seat, k, tile))
            else:
                d = int(dest)
                if d == k:
                    raise Illegal("%s is in set %d already" % (tile, k))
                ds = self.set_at(d)
                self.room(ds, 1)
                self.table[d - 1] = tidy(ds + [tile])
                self.events.append("MOVE %d %d %s %d" % (seat, k, tile, d))
            self.table[k - 1] = tidy(rest)
            return self.finish(old)
        m = MERGE_RE.match(t)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            sa, sb = self.set_at(a), self.set_at(b)
            if a == b:
                raise Illegal("a set cannot join itself")
            self.room(sb, len(sa))
            self.table[b - 1] = tidy(sb + sa)
            self.table[a - 1] = []
            self.events.append("MERGE %d %d %d" % (seat, a, b))
            return self.finish(old)
        m = SPLIT_RE.match(t)
        if m:
            k, pos = int(m.group(1)), int(m.group(2))
            s = self.set_at(k)
            if not 2 <= pos <= len(s):
                raise Illegal("set %d can be split before tile 2 to %d" % (k, len(s)) if len(s) > 1
                              else "set %d has one tile" % k)
            self.new_set(s[pos - 1:])
            self.table[k - 1] = tidy(s[:pos - 1])
            self.events.append("SPLIT %d %d %d" % (seat, k, pos))
            return self.finish(old)
        if t == "RESET":
            start = self.st["start"]
            if self.table == start["table"] and Counter(self.rack) == Counter(start["rack"]):
                raise Illegal("nothing to start over")
            self.table = [list(s) for s in start["table"]]
            self.rack = list(start["rack"])
            self.events.append("RESET %d" % seat)
            return None
        raise Illegal("not a step: try R7-3, R7B7K7-n, 3.2-5, 3-5, 3/4 or reset")


def finish_game(st, events, out_seat):
    """The game ends: out_seat emptied the rack, or (None) the pool is out and
    every player passed, or all but one seat left. The lowest rack of the seats
    still playing wins; each other player scores minus the difference to it (a
    seat that left: minus its whole rack), the winner (or winners, sharing)
    the sum of those."""
    totals = [rack_value(r) for r in st["racks"]]
    live = live_seats(st)
    low = min(totals[i] for i in live)
    winners = [i for i in live if totals[i] == low]
    loss = [0 if i in winners else (t - low if i in live else t) for i, t in enumerate(totals)]
    share = sum(loss) // len(winners)
    scores = [share if i in winners else -loss[i] for i in range(len(totals))]
    st["over"], st["scores"] = True, scores
    st["steps"] = []
    if out_seat is not None:
        events.append("OUT %d" % out_seat)
    txt = " ".join(str(x) for x in scores)
    st["result"] = ("win %d %s" % (winners[0], txt)) if len(winners) == 1 else "draw %s" % txt
    return st["result"]


def end_turn(st, seat, check):
    code, n, pts, bad = evaluate(st)
    if check:
        if code == "ok":
            return {"info": "ok %d tile%s%s" % (n, "" if n == 1 else "s",
                                                "" if pts is None else " meld %d" % pts)}
        if code == "sets":
            nums = " ".join(str(b) for b in bad)
            return {"error": ("sets %s are not valid" if len(bad) > 1 else "set %s is not valid") % nums}
        if code == "meld":
            return {"error": "the first meld is %d points; %d needed" % (pts, MELD)}
        return {"error": REASONS[code]}
    events = []
    out = {"state": st, "events": events}
    if code == "ok":
        if pts is not None:
            st["melded"][seat] = True
            events.append("MELD %d %d" % (seat, pts))
        events.append("DONE %d %d" % (seat, n))
        st["passes"] = 0
        if not st["racks"][seat]:
            out["end"] = finish_game(st, events, seat)
            return out
    else:
        if code != "none" or st["steps"]:
            st["table"] = [list(s) for s in st["start"]["table"]]
            st["racks"][seat] = list(st["start"]["rack"])
            events.append("UNDONE %d %s" % (seat, code))
        if st["pool"]:
            st["racks"][seat].append(st["pool"].pop())
            st["racks"][seat] = sort_rack(st["racks"][seat])
            events.append("DRAW %d %d" % (seat, len(st["pool"])))
            st["passes"] = 0
        else:
            events.append("PASS %d" % seat)
            st["passes"] += 1
            if st["passes"] >= len(live_seats(st)):
                out["end"] = finish_game(st, events, None)
                return out
    st["racks"][seat] = sort_rack(st["racks"][seat])
    begin_turn(st, next_seat(st, seat), events)
    return out


def live_seats(st):
    """The seats still playing (a seat that resigned or ran out of time is gone; see op_drop)."""
    gone = st.get("gone") or [False] * st["n"]
    return [i for i in range(st["n"]) if not gone[i]]


def next_seat(st, seat):
    live = live_seats(st)
    return next((i for i in live if i > seat), live[0])


def op_drop(req):
    """A seat leaves a running game (it resigned or ran out of time) and the others play on: the core calls it
    for RES and turn timeouts while more than two seats are still playing. A staged turn of the leaving seat is undone; its
    rack stays out of play (counted against it at the end; it never wins). With one seat left, that seat wins."""
    st = json.loads(json.dumps(req["state"]))
    seat, why = req.get("seat", -1), str(req.get("why") or "resign")
    if st["over"]:
        return {"error": "the game is over"}
    st.setdefault("gone", [False] * st["n"])
    if not 0 <= seat < st["n"] or st["gone"][seat]:
        return {"error": "that seat is not playing"}
    events = []
    out = {"state": st, "events": events}
    if seat == st["turn"] and st["steps"]:
        st["table"] = [list(s) for s in st["start"]["table"]]
        st["racks"][seat] = list(st["start"]["rack"])
        events.append("UNDONE %d none" % seat)
    st["gone"][seat] = True
    events.append("GONE %d %s" % (seat, why if why in ("resign", "timeout") else "resign"))
    live = live_seats(st)
    if len(live) == 1:
        out["end"] = finish_game(st, events, None)
        return out
    if seat == st["turn"]:
        begin_turn(st, next_seat(st, seat), events)
    return out


# ---- ops ----------------------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    n = req.get("players")
    if not isinstance(n, int) or not INFO["min"] <= n <= INFO["max"]:
        return {"error": "two to four players"}
    if req.get("options"):
        return {"error": "this game has no options (the seat count is players=2, 3 or 4 on NEW)"}
    tiles = all_tiles()
    random.Random(int(req.get("seed") or 0)).shuffle(tiles)
    racks = [sort_rack(tiles[i * HAND:(i + 1) * HAND]) for i in range(n)]
    st = {"n": n, "pool": tiles[n * HAND:], "racks": racks, "table": [], "melded": [False] * n,
          "turn": 0, "over": False, "result": None, "scores": None, "passes": 0, "start": None, "steps": [],
          "gone": [False] * n}
    events = ["DEAL %d %d" % (HAND, len(st["pool"]))]
    begin_turn(st, 0, events)
    return {"state": st, "events": events}


def status_text(st, seat):
    if st["over"]:
        sc = st["scores"]
        if st["result"].startswith("win"):
            w = int(st["result"].split()[1])
            return "Game over: seat %d wins (scores %s)" % (w, " ".join(str(x) for x in sc))
        return "Game over: a draw (scores %s)" % " ".join(str(x) for x in sc)
    t = st["turn"]
    if seat != t:
        return ("Seat %d to play" if seat < 0 else "Waiting for seat %d") % t
    code, n, pts, bad = evaluate(st)
    if code == "none":
        return "Your turn: play tiles from your rack, or end the turn to draw a tile"
    if code == "ok":
        return "Your turn: END plays your %d tile%s" % (n, "" if n == 1 else "s")
    if code == "sets":
        return "Your turn: set %s is not valid yet" % bad[0]
    if code == "meld":
        return "Your turn: first meld %d points, %d needed" % (pts, MELD)
    return "Your turn: a joker taken from the table needs its tile from your rack"


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    n = st["n"]
    start = st["start"]["table"] if st["start"] and not st["over"] else st["table"]
    wire = ["P %d" % len(st["pool"]),
            "C " + " ".join(str(len(r)) for r in st["racks"]),
            "I " + " ".join("1" if m else "0" for m in st["melded"]),
            "G " + " ".join("1" if g else "0" for g in (st.get("gone") or [False] * n)),
            "W %s" % ("-" if st["over"] else st["turn"])]
    for i, s in enumerate(st["table"]):
        wire.append("ST %d %s%s %s" % (i + 1, kind(s), "" if s in start else "*", " ".join(s)))
    if 0 <= seat < n:
        rack = st["racks"][seat]
        for i in range(0, len(rack), RACK_LINE):
            wire.append("H " + " ".join(rack[i:i + RACK_LINE]))
    if not st["over"]:
        code, played, pts, _ = evaluate(st)
        wire.append("TP %d %s %s" % (played, "-" if pts is None else pts, code))
    else:
        wire += ["T %d %d" % (i, x) for i, x in enumerate(st["scores"])]
    return {"wire": wire, "geometry": ["TIL %s 13 2 2" % COLOURS], "status": status_text(st, seat),
            "data": {"pool": len(st["pool"]), "racks": [len(r) for r in st["racks"]], "melded": st["melded"],
                     "turn": st["turn"], "table": st["table"], "over": st["over"], "scores": st["scores"],
                     "rack": st["racks"][seat] if 0 <= seat < n else None}}


def op_moves(req):
    return {"check": True}


def op_apply(req):
    st = json.loads(json.dumps(req["state"]))
    seat, move = req.get("seat", -1), [str(m) for m in (req.get("move") or [])]
    check = bool(req.get("check"))
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if not move:
        return {"error": "no move"}
    if move == ["!end"]:
        return end_turn(st, seat, check)
    if move == ["!undo"]:
        if not st["steps"]:
            return {"error": "nothing to take back"}
        if check:
            return {"info": "undo"}
        last = st["steps"].pop()
        st["table"], st["racks"][seat] = last["table"], last["rack"]
        return {"state": st, "events": ["UNDO %d" % seat], "staged": "more"}
    if any(m.startswith("!") for m in move):
        return {"error": "UND and END go alone"}
    if len(st["steps"]) >= MAX_STEPS:
        return {"error": "too many steps: END or UND"}
    w = Work(st, seat)
    try:
        for tok in move:
            w.step(tok)
    except Illegal as e:
        return {"error": str(e)}
    if check:
        return {"info": "ok"}
    st["steps"].append({"table": st["table"], "rack": st["racks"][seat]})
    st["table"], st["racks"][seat] = w.table, sort_rack(w.rack)
    code = evaluate(st)[0]
    return {"state": st, "events": w.events, "staged": "done" if code == "ok" else "more"}


def op_result(req):
    st = req["state"]
    return {"over": True, "scores": st["scores"]} if st["over"] else {"over": False}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "played 5 tiles", "made the first meld, 30 points", "drew a
    tile", "passed"."""
    seat, out = str(req.get("seat")), ""
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != seat:
            continue
        if p[0] == "MELD" and len(p) >= 3:
            out = "made the first meld, %s points" % p[2]
        elif p[0] == "DONE" and len(p) >= 3 and not out.startswith("made"):
            out = "played %s tile%s" % (p[2], "" if p[2] == "1" else "s")
        elif p[0] == "DRAW":
            out = "drew a tile"
        elif p[0] == "PASS":
            out = "passed"
    return {"say": out}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "drop": op_drop, "say": op_say}


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
