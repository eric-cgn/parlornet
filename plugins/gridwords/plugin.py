#!/usr/bin/env python3
"""gridwords/1 game plugin (docs/types/gridwords-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments
(and of the word list); the state is opaque to the server.

The word list is the operator's choice: a text file, one word per line, named
by the environment setting GRIDWORDS_WORDS, else words.txt next to this file.
None is shipped. Without one a table cannot be started (new says why).

State: {"np": players, "seed": int, "cells": 225 chars ('.' empty, 'A'-'Z' a
tile, 'a'-'z' a blank standing for that letter; row 1 first), "bag": letters
('_' a blank), "racks": [letters per seat], "scores": [...], "turn": seat,
"idle": turns in a row without a word played, "shuffles": int, "last": the last
play or None, "over": bool, "result": END text once over}
Squares are 1-based: x across from the left, y down from the top.
"""
import collections.abc
import json
import os
import random
import sys

SIZE = 15
CENTRE = (8, 8)
RACK = 7
FULL_RACK_BONUS = 50

# letter: (points, tiles in the set); "_" is the blank
TILES = {
    "A": (1, 9), "B": (3, 2), "C": (3, 2), "D": (2, 4), "E": (1, 12), "F": (4, 2), "G": (2, 3),
    "H": (4, 2), "I": (1, 9), "J": (8, 1), "K": (5, 1), "L": (1, 4), "M": (3, 2), "N": (1, 6),
    "O": (1, 8), "P": (3, 2), "Q": (10, 1), "R": (1, 6), "S": (1, 4), "T": (1, 6), "U": (1, 4),
    "V": (4, 2), "W": (4, 2), "X": (8, 1), "Y": (4, 2), "Z": (10, 1), "_": (0, 2),
}
VALUES = {k: v for k, (v, _) in TILES.items()}


def _premiums():
    """The standard 15x15 layout: one eighth of the board, mirrored."""
    eighth = {
        "TW": [(1, 1), (8, 1)],
        "DW": [(2, 2), (3, 3), (4, 4), (5, 5), (8, 8)],
        "TL": [(6, 2), (6, 6)],
        "DL": [(4, 1), (7, 3), (8, 4), (7, 7)],
    }
    out = {}
    for kind, squares in eighth.items():
        for x, y in squares:
            for a, b in ((x, y), (y, x)):
                for px in (a, SIZE + 1 - a):
                    for py in (b, SIZE + 1 - b):
                        out[(px, py)] = kind
    return out


PREMIUM = _premiums()
WORDS_ENV = "GRIDWORDS_WORDS"
DEFAULT_WORDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.txt")

# The rulesets. Both play the standard board, tiles and scoring above; they differ only in when a game
# with no word played for a while ends. "idle" is the number of turns in a row without a word played (passes
# and exchanges) that ends the game: None means two full rounds (twice the number of players), and a number
# means that many turns whatever the number of players. classic follows club and tournament play, where six
# scoreless turns in a row end the game (the convention of the period's crossword-game servers).
RULESETS = {
    "english": {"idle": None},
    "classic": {"idle": 6},
}

INFO = {
    "type": "gridwords", "ver": 1, "name": "Grid Words",
    "rules": list(RULESETS), "min": 2, "max": 4, "flags": ["nomoves"],
    "options": ["racks"],
}

# racks=open: every seated player's view also shows every rack and the bag in draw order (RK and BO lines).
# It is for period programs whose link sends both racks and the whole bag to each player's machine; such a
# program shows only its own player's rack. At a table with open
# racks, a play may also carry OWN: the player's own program has checked its words against its own list, so
# words missing from the server's list are accepted and named in an UNLISTED event.
OPEN_RACKS = ("open", "hidden")

# A table that keeps no integrity (the core's INT; docs/types/gridwords-1.md, Tables that keep no integrity):
# a player's own program runs the game, as a hosting peer program does, and the table follows it. The core says
# so in new ("integrity": false) with the seat of the table's creator, whose program it is ("authority"). Then
# the state has "trust" and "auth", and:
#   - every move hands the turn to the authority, and only the authority gives it on: TURN n;
#   - the authority may take back the play just made by another seat, which its program refused: VOID, with
#     the word it refused, which is then refused at the table too;
#   - the authority may end the game when its program does: OVER, alone or after a move of its own;
#   - a seat may say what its own rack holds, drawn by its own program from the same bag: RACK letters;
#   - a play may carry OWN, and an exchange needs only as many tiles in the bag as it puts back;
#   - turns without a word played never end the game: the authority's program decides.
TRUST_VERBS = ("RACK", "TURN", "VOID", "OVER")
REFUSED_KEEP = 200          # the words a table remembers that its authority's program refused (VOID word)


def idle_limit(rules, np):
    """The turns in a row without a word played that end a game of np players under these rules."""
    idle = RULESETS.get(rules or "english", RULESETS["english"])["idle"]
    return idle if idle else 2 * np


class Illegal(Exception):
    pass


# ---- the word list ---------------------------------------------------------------

_lexicon = {"key": None, "words": None}


def word_list_path():
    return os.environ.get(WORDS_ENV) or DEFAULT_WORDS


class Lexicon(collections.abc.Set):
    """The word list, compact: one bytes object holding, for each length 2-15,
    the words of that length sorted and run together as fixed-width records
    (no separators). A lookup is a binary search among the words of its
    length. ENABLE's 168,551 words of 2-15 letters take 1.5 MB here (a
    lookup about 2 microseconds), against 17 MB as a set of str. Read-only;
    it behaves as a set of upper-case str."""

    __slots__ = ("_data", "_spans", "_len")

    def __init__(self, lines):
        """lines: an iterable of text lines; each is stripped and upper-cased,
        and kept if it is then 2-15 letters A-Z. Words arriving sorted (as in
        ENABLE) are stored as they come, one line at a time."""
        runs = [bytearray() for _ in range(SIZE + 1)]
        last = [b""] * (SIZE + 1)
        unsorted = set()
        for line in lines:
            w = line.strip().upper()
            n = len(w)
            if not (2 <= n <= SIZE and w.isascii() and w.isalpha()):
                continue
            b = w.encode("ascii")
            if b <= last[n]:
                if b == last[n]:
                    continue                # a repeat of the word before it
                unsorted.add(n)
            last[n] = b
            runs[n] += b
        for n in unsorted:                  # an operator's list out of order: sort that length
            run = runs[n]
            runs[n] = bytearray().join(sorted({bytes(run[i:i + n]) for i in range(0, len(run), n)}))
        spans, at = [(0, 0)] * (SIZE + 1), 0
        for n in range(2, SIZE + 1):
            spans[n] = (at, len(runs[n]) // n)
            at += len(runs[n])
        self._data = b"".join(runs)
        self._spans = tuple(spans)
        self._len = sum(c for _, c in spans)

    def __contains__(self, word):
        if not isinstance(word, str):
            return False
        n = len(word)
        if not 2 <= n <= SIZE or not word.isascii():
            return False
        key = word.encode("ascii")
        data = self._data
        start, lo, hi = self._spans[n][0], 0, self._spans[n][1]
        while lo < hi:
            mid = (lo + hi) >> 1
            at = start + mid * n
            rec = data[at:at + n]
            if rec < key:
                lo = mid + 1
            elif rec > key:
                hi = mid
            else:
                return True
        return False

    def __len__(self):
        return self._len

    def __iter__(self):
        data = self._data
        for n in range(2, SIZE + 1):
            start, count = self._spans[n]
            for i in range(count):
                at = start + i * n
                yield data[at:at + n].decode("ascii")

    def nbytes(self):
        """The bytes this list holds (its data and its own objects)."""
        return sys.getsizeof(self._data) + sys.getsizeof(self._spans) + sum(map(sys.getsizeof, self._spans)) \
            + sys.getsizeof(self)


def load_words(path=None):
    """The word list (a Lexicon of upper-case words, 2-15 letters A-Z), or None
    when there is none. Read once, when first needed, and again when the file
    changes."""
    path = path or word_list_path()
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if _lexicon["key"] != key:
        _lexicon.update(key=None, words=None)       # the old list goes before the new one is read
        with open(path, encoding="utf-8", errors="replace") as f:
            words = Lexicon(f)
        _lexicon.update(key=key, words=words or None)
    return _lexicon["words"]


# ---- squares and rules -------------------------------------------------------------

def sq_name(x, y):
    """1-based x, y -> 'h8' (column letter, row number), for messages."""
    return "%s%d" % (chr(ord("a") + x - 1), y)


def at(cells, x, y):
    return cells[(y - 1) * SIZE + (x - 1)]


def inside(x, y):
    return 1 <= x <= SIZE and 1 <= y <= SIZE


def parse_letters(text):
    """'HEL_LO' -> [('H', False), ('E', False), ('L', False), ('L', True), ('O', False)]
    (a '_' marks the next letter as a blank). Case does not matter. None if malformed."""
    out, blank = [], False
    for c in text.upper():
        if c == "_" and not blank:
            blank = True
        elif "A" <= c <= "Z":
            out.append((c, blank))
            blank = False
        else:
            return None
    return out if out and not blank else None


def rack_value(rack):
    return sum(VALUES[c] for c in rack)


def evaluate(st, seat, x, y, d, text, words):
    """Check a play and score it. words is the word list (None: do not check
    words). -> {"score", "words", "placed", "rack", "cells", "full"}; raises
    Illegal with the reason."""
    letters = parse_letters(text)
    if not letters:
        raise Illegal("letters are A-Z, with _ before a blank's letter")
    if not inside(x, y):
        raise Illegal("that square is not on the board")
    dx, dy = (1, 0) if d == "H" else (0, 1)
    n = len(letters)
    ex, ey = x + dx * (n - 1), y + dy * (n - 1)
    if not inside(ex, ey):
        raise Illegal("the word runs off the board")
    cells = st["cells"]
    rack = list(st["racks"][seat])
    placed = []
    for i, (ch, blank) in enumerate(letters):
        cx, cy = x + dx * i, y + dy * i
        have = at(cells, cx, cy)
        if have != ".":
            if have.upper() != ch:
                raise Illegal("%s already holds %s" % (sq_name(cx, cy), have.upper()))
            continue
        need = "_" if blank else ch
        if need not in rack:
            raise Illegal("your rack has no %s" % ("blank" if blank else ch))
        rack.remove(need)
        placed.append((cx, cy, ch.lower() if blank else ch))
    if not placed:
        raise Illegal("a play needs at least one tile from your rack")
    if inside(x - dx, y - dy) and at(cells, x - dx, y - dy) != ".":
        raise Illegal("the tiles just before %s are part of the word too" % sq_name(x, y))
    if inside(ex + dx, ey + dy) and at(cells, ex + dx, ey + dy) != ".":
        raise Illegal("the tiles just after %s are part of the word too" % sq_name(ex, ey))
    if cells.count(".") == SIZE * SIZE:
        if not any((px, py) == CENTRE for px, py, _ in placed):
            raise Illegal("the first word must cover the center square %s" % sq_name(*CENTRE))
        if n < 2:
            raise Illegal("the first word needs two letters or more")
    elif len(placed) == n and not any(
            inside(px + ax, py + ay) and at(cells, px + ax, py + ay) != "."
            for px, py, _ in placed for ax, ay in ((1, 0), (-1, 0), (0, 1), (0, -1))):
        raise Illegal("the word must join the words already on the board")
    new = list(cells)
    for px, py, c in placed:
        new[(py - 1) * SIZE + (px - 1)] = c
    new = "".join(new)
    runs = [(x, y, dx, dy, n)] if n >= 2 else []
    for px, py, _ in placed:                    # the cross word through each new tile
        cdx, cdy = dy, dx
        sx, sy = px, py
        while inside(sx - cdx, sy - cdy) and at(new, sx - cdx, sy - cdy) != ".":
            sx, sy = sx - cdx, sy - cdy
        length = 0
        while inside(sx + cdx * length, sy + cdy * length) and at(new, sx + cdx * length, sy + cdy * length) != ".":
            length += 1
        if length >= 2:
            runs.append((sx, sy, cdx, cdy, length))
    if not runs:
        raise Illegal("a play must make a word of two letters or more")
    fresh = {(px, py) for px, py, _ in placed}
    total, formed = 0, []
    for sx, sy, wdx, wdy, length in runs:
        points, mult, word = 0, 1, ""
        for i in range(length):
            cx, cy = sx + wdx * i, sy + wdy * i
            c = at(new, cx, cy)
            word += c
            v = 0 if c.islower() else VALUES[c]
            if (cx, cy) in fresh:
                kind = PREMIUM.get((cx, cy))
                v *= {"DL": 2, "TL": 3}.get(kind, 1)
                mult *= {"DW": 2, "TW": 3}.get(kind, 1)
            points += v
        total += points * mult
        formed.append(word)
    if words is not None:
        bad = [w.upper() for w in formed if w.upper() not in words]
        if bad:
            raise Illegal(("not in the word list: " + " ".join(bad))[:64])
    full = len(placed) == RACK
    if full:
        total += FULL_RACK_BONUS
    return {"score": total, "words": formed, "placed": placed, "rack": "".join(rack), "cells": new, "full": full}


def result_text(st):
    scores = st["scores"]
    top = max(scores)
    tally = "/".join(str(s) for s in scores)
    leaders = [i for i, s in enumerate(scores) if s == top]
    return ("win %d %s" % (leaders[0], tally)) if len(leaders) == 1 else "draw %s" % tally


def gone_out(st):
    """The seat that has played its last tile with the bag empty, or None."""
    return next((s for s, r in enumerate(st["racks"]) if not r), None) if not st["bag"] else None


def finish(st, events, out_seat=None):
    """The game ends: every rack's points come off its owner's score; a player
    who went out gains the total of the others' racks."""
    left = [rack_value(r) for r in st["racks"]]
    for s in range(st["np"]):
        adj = -left[s] + (sum(left) if s == out_seat else 0)
        st["scores"][s] += adj
        events.append("ADJ %d %+d %s" % (s, adj, st["racks"][s] or "-"))
    st["over"] = True
    st["result"] = result_text(st)


def next_turn(st, events):
    """The next seat moves; at a table that keeps no integrity the authority's program says who (TURN)."""
    if st.get("trust") and st.get("auth", -1) >= 0:
        st["turn"] = st["auth"]
    else:
        st["turn"] = (st["turn"] + 1) % st["np"]
    events.append("TURN %d" % st["turn"])


def draw(st, rack, k):
    k = min(k, len(st["bag"]))
    if k:
        rack, st["bag"] = rack + st["bag"][-k:], st["bag"][:-k]
    return rack, k


def copy_state(st):
    out = dict(st)
    out["racks"] = list(st["racks"])
    out["scores"] = list(st["scores"])
    if "undo" in out:
        out["undo"] = None          # a move made since: the play before it can no longer be taken back (VOID)
    return out


def trusted(req, move):
    """A table that keeps no integrity: what a player's own program says (TRUST_VERBS). RACK is any seat's,
    about its own rack; TURN, VOID and OVER are the authority's."""
    st, seat, verb = req["state"], req.get("seat", -1), move[0].upper()
    check = bool(req.get("check"))
    if not 0 <= seat < st["np"]:
        return {"error": "you are not playing"}
    if verb != "RACK" and seat != st.get("auth"):
        return {"error": "only the program that runs this table says that"}
    if verb == "RACK":
        if len(move) != 2:
            return {"error": "RACK letters, like RACK AEIN_ST (_ is a blank)"}
        want = "" if move[1] == "-" else move[1].upper()
        if len(want) > RACK or not all(c == "_" or "A" <= c <= "Z" for c in want):
            return {"error": "RACK letters, like RACK AEIN_ST (_ is a blank)"}
        pool = list(st["racks"][seat] + st["bag"])     # the rack goes back under the bag, then the letters come out
        for c in want:
            if c not in pool:
                return {"error": "the bag has no %s for that rack" % ("blank" if c == "_" else c)}
            pool.reverse()
            pool.remove(c)
            pool.reverse()
        if check:
            return {"info": "0"}
        new = copy_state(st)
        new["undo"] = st.get("undo")        # saying a rack is no move: a play just made may still be taken back
        new["racks"][seat] = want
        new["bag"] = "".join(pool)
        return {"state": new, "events": []}
    if verb == "TURN":
        if len(move) != 2 or not move[1].isdigit() or not 0 <= int(move[1]) < st["np"]:
            return {"error": "TURN seat"}
        if check:
            return {"info": "0"}
        new = copy_state(st)
        new["turn"] = int(move[1])
        return {"state": new, "events": ["TURN %d" % new["turn"]]}
    if verb == "VOID":
        undo = st.get("undo")
        if len(move) > 2 or not undo:
            return {"error": "there is no play to take back"}
        word = move[1].upper() if len(move) == 2 else ""
        if word and not (word.isascii() and word.isalpha() and len(word) <= SIZE):
            return {"error": "VOID [word], the word the program refused"}
        if check:
            return {"info": "0"}
        last = st["last"]
        new = dict(undo, undo=None)
        new["turn"] = last[0]
        refused = list(st.get("refused") or [])
        if word and word not in refused:        # not played again at this table: the program refuses it
            refused = (refused + [word])[-REFUSED_KEEP:]
        new["refused"] = refused
        return {"state": new, "events": ["VOID %d %s" % (last[0], word or last[4]), "TURN %d" % last[0]]}
    if len(move) != 1:                  # OVER alone: the authority's program ended the game
        return {"error": "OVER, alone or after a move"}
    if check:
        return {"info": "0"}
    new = copy_state(st)
    events = ["OVER %d" % seat]
    finish(new, events, out_seat=gone_out(new))
    return {"state": new, "events": events, "end": new["result"]}


# ---- the plugin operations ---------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    n = req.get("players")
    if not isinstance(n, int) or not 2 <= n <= 4:
        return {"error": "two to four players"}
    if req.get("rules", "english") not in INFO["rules"]:
        return {"error": "the rulesets are: %s" % " ".join(INFO["rules"])}
    shown = (req.get("options") or {}).get("racks", "hidden")
    if shown not in OPEN_RACKS:
        return {"error": "racks is open or hidden"}
    if not load_words():
        return {"error": "this server has no word list for Grid Words yet"}
    seed = int(req.get("seed") or 0)
    bag = [c for c, (_, count) in sorted(TILES.items()) for _ in range(count)]
    random.Random(seed).shuffle(bag)
    bag = "".join(bag)
    racks = []
    for _ in range(n):
        racks.append(bag[-RACK:])
        bag = bag[:-RACK]
    st = {"np": n, "seed": seed, "cells": "." * (SIZE * SIZE), "bag": bag, "racks": racks,
          "scores": [0] * n, "turn": 0, "idle": 0, "shuffles": 0, "last": None, "over": False, "result": ""}
    if shown == "open":
        st["open"] = True
    if req.get("integrity") is False:       # the core's INT: the authority's program runs the game
        auth = req.get("authority")
        st["trust"] = True
        st["auth"] = auth if isinstance(auth, int) and 0 <= auth < n else -1
        st["undo"] = None
        if st["auth"] >= 0:                 # the authority's program says who starts (TURN)
            st["turn"] = st["auth"]
    return {"state": st, "events": ["TURN %d" % st["turn"]]}


def geometry():
    out = ["BRD %d %d" % (SIZE, SIZE), "CTR %d %d" % CENTRE]
    for kind in ("TW", "DW", "TL", "DL"):
        squares = sorted(((y, x) for (x, y), k in PREMIUM.items() if k == kind))
        pairs = ["%d %d" % (x, y) for y, x in squares]
        for i in range(0, len(pairs), 10):
            out.append("PRM %s %s" % (kind, " ".join(pairs[i:i + 10])))
    out += ["TIL %s %d %d" % (c, v, k) for c, (v, k) in sorted(TILES.items())]
    return out


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    triples = ["%d %d %s" % (i % SIZE + 1, i // SIZE + 1, c) for i, c in enumerate(st["cells"]) if c != "."]
    wire = ["X " + " ".join(triples[i:i + 9]) for i in range(0, len(triples), 9)]
    if 0 <= seat < st["np"]:
        wire.append("R " + (st["racks"][seat] or "-"))
        if st.get("open"):              # racks=open: every rack, and the bag with the next tile first
            wire += ["RK %d %s" % (s, r or "-") for s, r in enumerate(st["racks"])]
            order = st["bag"][::-1]
            wire += ["BO " + order[i:i + 60] for i in range(0, len(order), 60)] or ["BO -"]
    wire += ["K %d %d" % (s, len(r)) for s, r in enumerate(st["racks"])]
    wire.append("BAG %d" % len(st["bag"]))
    wire += ["T %d %d" % (s, p) for s, p in enumerate(st["scores"])]
    wire.append("W %d" % (-1 if st["over"] else st["turn"]))
    if st["last"]:
        wire.append("LAST %d %d %d %s %s %d" % tuple(st["last"]))
    if st["over"]:
        status = "Game over: " + st["result"]
    elif seat == st["turn"]:
        status = "Your move"
    elif seat < 0:
        status = "Seat %d to move" % st["turn"]
    else:
        status = "Waiting for another player"
    data = {"cells": st["cells"], "counts": [len(r) for r in st["racks"]], "bag": len(st["bag"]),
            "scores": st["scores"], "turn": -1 if st["over"] else st["turn"], "last": st["last"]}
    if 0 <= seat < st["np"]:
        data["rack"] = st["racks"][seat]
    return {"wire": wire, "geometry": geometry(), "status": status, "data": data}


def op_option(req):
    """An option changed on a running game (docs/plugins.md): only racks, and only from hidden to open, as the
    core's OPR does with every player's consent. The state then shows as at a table made with racks=open; the
    bag and the racks stay as they are."""
    if req.get("key") != "racks" or req.get("value") != "open":
        return {"error": "only racks=open can be set on a running game"}
    st = copy_state(req["state"])
    st["open"] = True
    return {"state": st}


def op_moves(req):
    return {"check": True}


def op_result(req):
    st = req["state"]
    return {"over": True, "scores": st["scores"]} if st["over"] else {"over": False}


def op_apply(req):
    """A move, or (check) whether it would be taken. At a table with open racks a move may end with
    OVER: the mover's own program has ended the game after this turn by its own rule (it shows the end and
    takes nothing more), so the table ends it too, as a game that ran out of scoring turns."""
    move = req.get("move") or []
    st = req["state"]
    if st.get("trust") and move and move[0].upper() in TRUST_VERBS and not st["over"]:
        return trusted(req, move)
    if move and move[-1].upper() == "OVER":
        if not (st.get("open") or st.get("trust")):
            return {"error": "OVER is for tables with open racks"}
        if st.get("trust") and req.get("seat") != st.get("auth"):
            return {"error": "only the program that runs this table ends its game"}
        r = op_apply(dict(req, move=move[:-1]))
        if "state" in r and not r["state"]["over"]:
            events = r["events"]
            if events and events[-1].startswith("TURN "):
                events.pop()
            events.append("OVER %d" % req.get("seat", -1))
            finish(r["state"], events, out_seat=gone_out(r["state"]))
            r["end"] = r["state"]["result"]
        return r
    return apply_move(req)


def apply_move(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    check = bool(req.get("check"))
    if st["over"]:
        return {"error": "the game is over"}
    if not 0 <= seat < st["np"]:
        return {"error": "you are not playing"}
    if not check and seat != st["turn"]:
        return {"error": "not your turn"}
    verb = move[0].upper() if move else ""
    limit = idle_limit(req.get("rules"), st["np"])
    try:
        if verb == "PLAY":
            return play(st, seat, move, check, req.get("rules"))
        if verb == "EXCH":
            return exchange(st, seat, move, check, limit)
        if verb == "PASS" and len(move) == 1:
            if check:
                return {"info": "0"}
            st = copy_state(st)
            events = ["PASS %d" % seat]
            return idle_turn(st, events, limit)
    except Illegal as e:
        return {"error": str(e)}
    return {"error": "a move is PLAY x y H|V letters, EXCH letters or PASS"}


def play(st, seat, move, check, rules=None):
    own = len(move) == 6 and move[5].upper() == "OWN"
    if (len(move) != 5 and not own) or not move[1].isdigit() or not move[2].isdigit() \
            or move[3].upper() not in ("H", "V"):
        raise Illegal("PLAY x y H|V letters, like PLAY 8 8 H WORD")
    if own and not (st.get("open") or st.get("trust")):
        raise Illegal("OWN is for tables with open racks")
    words = load_words()
    if not words:
        raise Illegal("this server has no word list")
    x, y, d = int(move[1]), int(move[2]), move[3].upper()
    # OWN: the mover's own program checked the words against its own list; the server's list only names them
    r = evaluate(st, seat, x, y, d, move[4], None if own else words)
    refused = [w.upper() for w in r["words"] if w.upper() in (st.get("refused") or ())] if not own else []
    if refused:
        raise Illegal("the host's program does not take %s" % refused[0])
    unlisted = [w.upper() for w in r["words"] if w.upper() not in words] if own else []
    if check:
        info = str(r["score"])
        for w in r["words"]:
            if len(info) + 1 + len(w) > 70:
                break
            info += " " + w
        return {"info": info}
    before = st
    st = copy_state(st)
    if st.get("trust") and seat != st.get("auth"):     # the authority's program may still refuse it (VOID)
        st["undo"] = dict(before, undo=None)
    rack, k = draw(st, r["rack"], RACK - len(r["rack"]))
    st["racks"][seat] = rack
    st["cells"] = r["cells"]
    st["scores"][seat] += r["score"]
    st["idle"] = 0
    dx, dy = (1, 0) if d == "H" else (0, 1)
    word = "".join(at(st["cells"], x + dx * i, y + dy * i) for i in range(len(parse_letters(move[4]))))
    st["last"] = [seat, x, y, d, word, r["score"]]
    events = ["PLAY %d %d %d %s %s %d" % (seat, x, y, d, word, r["score"]),
              "X " + " ".join("%d %d %s" % p for p in r["placed"])]
    if unlisted:
        events.append(("UNLISTED %d %s" % (seat, " ".join(unlisted)))[:70])
    if k:
        events.append("DRAW %d %d" % (seat, k))
    if not rack and not st["bag"] and not (st.get("trust") and seat != st.get("auth")):
        finish(st, events, out_seat=seat)       # INT: another seat goes out once the authority's program takes
        return {"state": st, "events": events, "end": st["result"]}     # the play and says OVER
    next_turn(st, events)
    return {"state": st, "events": events}


def exchange(st, seat, move, check, limit=None):
    if len(move) != 2 or not all(c == "_" or "A" <= c <= "Z" for c in move[1].upper()) or len(move[1]) > RACK:
        raise Illegal("EXCH letters, like EXCH QV_ (_ is a blank)")
    give = move[1].upper()
    rack = list(st["racks"][seat])
    for c in give:
        if c not in rack:
            raise Illegal("your rack has no %s" % ("blank" if c == "_" else c))
        rack.remove(c)
    if st.get("trust"):             # the hosting program's rule: as many tiles in the bag as go back
        if len(st["bag"]) < len(give):
            raise Illegal("the bag has only %d tiles" % len(st["bag"]))
    elif len(st["bag"]) < RACK:
        raise Illegal("exchanging needs %d tiles in the bag" % RACK)
    if check:
        return {"info": "0"}
    st = copy_state(st)
    rack, k = draw(st, "".join(rack), len(give))
    bag = list(st["bag"] + give)
    random.Random(st["seed"] * 1009 + st["shuffles"] + 1).shuffle(bag)
    st["bag"] = "".join(bag)
    st["shuffles"] += 1
    st["racks"][seat] = rack
    return idle_turn(st, ["EXCH %d %d" % (seat, len(give))], limit)


def idle_turn(st, events, limit=None):
    """A pass or an exchange. limit turns in a row without a word played end the game (by default two
    full rounds; idle_limit gives each ruleset's)."""
    st["idle"] += 1
    if st["idle"] >= (limit or 2 * st["np"]) and not st.get("trust"):     # INT: the authority's program ends it
        finish(st, events)
        return {"state": st, "events": events, "end": st["result"]}
    next_turn(st, events)
    return {"state": st, "events": events}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "played FRAULEIN for 65", "exchanged 3 tiles", "passed"."""
    for e in req.get("events") or []:
        p = e.split()
        if p[:1] == ["VOID"] and len(p) >= 3:      # the authority's program refused another seat's play
            return {"say": "refused the play %s" % p[2]}
        if len(p) < 2 or p[1] != str(req.get("seat")):
            continue
        if p[0] == "PLAY" and len(p) >= 7:
            return {"say": "played %s for %s" % (p[5], p[6])}
        if p[0] == "EXCH" and len(p) >= 3:
            return {"say": "exchanged %s tile%s" % (p[2], "" if p[2] == "1" else "s")}
        if p[0] == "PASS":
            return {"say": "passed"}
    for e in req.get("events") or []:      # OVER alone: the player's own program ended the game
        if e == "OVER %s" % req.get("seat"):
            return {"say": "ended the game"}
    return {"say": ""}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "option": op_option, "say": op_say}


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
