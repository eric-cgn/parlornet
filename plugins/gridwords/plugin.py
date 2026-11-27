#!/usr/bin/env python3
"""gridwords/1 game plugin (docs/types/gridwords-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments
(and of the word list and the rulesets, read at start); the state is opaque to
the server.

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
import re
import os
import random
import sys

SIZE = 15
CENTRE = (8, 8)
RACK = 7
FULL_RACK_BONUS = 50
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"     # "_" is the blank
WORDS_ENV = "GRIDWORDS_WORDS"
DEFAULT_WORDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "words.txt")

# ---- the rulesets ------------------------------------------------------------------
# A ruleset is a rules id of gridwords/1, the word every table has from NEW type rules, and its data: what each
# letter is worth, how many of each are in the bag, the bonus squares of the 15 by 15 board, and how many turns
# in a row without a word played end the game. standard is the plugin's own (rules/standard.json beside this
# file) and the default. An operator adds others as files in the site directory,
# <site>/rules/gridwords/<id>.json, in the same format (docs/types/gridwords-1.md, Rulesets). The plugin reads
# them once, when it starts. A file it can't use is left out, and the core logs why (INFO's "log").

BUILT_IN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")
DEFAULT = "standard"
SITE_ENV = "PARLOR_SITE"
DEFAULT_SITE = "/run/parlornet/site"
RULES_ID_RE = re.compile(r"^[a-z][a-z0-9]{0,11}\Z")
SQUARES = {".": None, "d": "DL", "t": "TL", "D": "DW", "T": "TW"}
KIND_CHAR = {k: c for c, k in SQUARES.items() if k}
MAX_POINTS = 10             # a letter's points: 0 to 10
TILES_MIN, TILES_MAX = 60, 200      # tiles in the bag, blanks included
IDLE_MIN, IDLE_MAX = 2, 20
ABOUT_MAX = 300
FILE_MAX = 16 * 1024
KEYS = ("about", "symmetric", "tiles", "board", "idle")


class Ruleset:
    """A ruleset's data. tiles {letter: (points, count)}, "_" the blank; values {letter: points}; premium
    {(x, y): "TW" | "DW" | "TL" | "DL"}; idle: the turns in a row without a word played that end the game, or
    None for two full rounds (twice the number of players)."""

    def __init__(self, name, tiles, premium, idle=None, about="", symmetric=False):
        self.name, self.tiles, self.premium, self.idle = name, tiles, premium, idle
        self.about, self.symmetric = about, symmetric
        self.values = {c: v for c, (v, _) in tiles.items()}

    def idle_limit(self, np):
        return self.idle if self.idle else 2 * np


def mirrors(x, y):
    """The eight squares a square stands for under the board's symmetry: both axes and both diagonals."""
    out = set()
    for a, b in ((x, y), (y, x)):
        for px in (a, SIZE + 1 - a):
            for py in (b, SIZE + 1 - b):
                out.add((px, py))
    return out


def parse_ruleset(name, data):
    """A ruleset file's JSON -> Ruleset. Raises ValueError with the reason, a sentence, when it isn't one."""
    if not isinstance(data, dict):
        raise ValueError("The file is not a JSON object.")
    extra = sorted(set(data) - set(KEYS))
    if extra:
        raise ValueError("The file has keys the format doesn't know: %s." % ", ".join(extra))
    tiles = data.get("tiles")
    if not isinstance(tiles, dict) or sorted(tiles) != sorted(LETTERS):
        raise ValueError("tiles must give every letter A to Z and _ for the blank, and nothing else.")
    out = {}
    for c in LETTERS:
        t = tiles[c]
        if not (isinstance(t, list) and len(t) == 2 and all(type(n) is int for n in t)):
            raise ValueError("tiles gives %s as [points, count], two whole numbers." % c)
        points, count = t
        if not 0 <= points <= MAX_POINTS:
            raise ValueError("%s is worth %d points, and a letter is worth 0 to %d." % (c, points, MAX_POINTS))
        if not 0 <= count <= TILES_MAX:
            raise ValueError("The bag can't hold %d of %s." % (count, c))
        out[c] = (points, count)
    if out["_"][0] != 0:
        raise ValueError("A blank is worth 0 points.")
    total = sum(n for _, n in out.values())
    if not TILES_MIN <= total <= TILES_MAX:
        raise ValueError("The bag holds %d tiles, and it must hold %d to %d." % (total, TILES_MIN, TILES_MAX))
    board = data.get("board")
    if not (isinstance(board, list) and len(board) == SIZE
            and all(isinstance(r, str) and len(r) == SIZE for r in board)):
        raise ValueError("board must be %d rows of %d squares." % (SIZE, SIZE))
    premium = {}
    for y, row in enumerate(board, 1):
        for x, c in enumerate(row, 1):
            if c not in SQUARES:
                raise ValueError("Row %d has %r, and a square is one of . d t D T." % (y, c))
            if SQUARES[c]:
                premium[(x, y)] = SQUARES[c]
    symmetric = data.get("symmetric", False)
    if not isinstance(symmetric, bool):
        raise ValueError("symmetric is true or false.")
    if symmetric:
        for (x, y), kind in sorted(premium.items()):
            for m in sorted(mirrors(x, y)):
                if premium.get(m) != kind:
                    raise ValueError("The board says it is symmetric, but %s%d and %s%d differ." % (
                        chr(96 + x), y, chr(96 + m[0]), m[1]))
    idle = data.get("idle")
    if idle is not None and not (type(idle) is int and IDLE_MIN <= idle <= IDLE_MAX):
        raise ValueError("idle is a number of turns from %d to %d." % (IDLE_MIN, IDLE_MAX))
    about = data.get("about", "")
    if not (isinstance(about, str) and len(about) <= ABOUT_MAX and about.isascii() and about.isprintable()):
        raise ValueError("about is plain text of up to %d characters." % ABOUT_MAX)
    return Ruleset(name, out, premium, idle, about, symmetric)


def read_ruleset(path):
    """A ruleset file -> Ruleset, named by the file. Raises ValueError with the reason."""
    name = os.path.basename(path)[:-len(".json")]
    if not RULES_ID_RE.match(name):
        raise ValueError("A rules id is a lower-case letter and then up to 11 lower-case letters or digits.")
    try:
        if os.path.getsize(path) > FILE_MAX:
            raise ValueError("The file is larger than %d KB." % (FILE_MAX // 1024))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError("The file can't be read: %s." % e)
    except json.JSONDecodeError as e:
        raise ValueError("The file is not JSON: %s." % e)
    return parse_ruleset(name, data)


def site_rules_dir():
    return os.path.join(os.environ.get(SITE_ENV) or DEFAULT_SITE, "rules", "gridwords")


def load_rulesets(site_dir=None):
    """({rules id: Ruleset}, [log lines]): standard first, then the site directory's rulesets by id. A site
    file that is not a ruleset, or that takes standard's id, is left out with a line saying why."""
    rulesets = {DEFAULT: read_ruleset(os.path.join(BUILT_IN, DEFAULT + ".json"))}
    log = []
    site_dir = site_dir or site_rules_dir()
    try:
        names = sorted(os.listdir(site_dir))
    except OSError:
        names = []
    for f in names:
        if not f.endswith(".json"):
            continue
        path = os.path.join(site_dir, f)
        try:
            rs = read_ruleset(path)
            if rs.name in rulesets:
                raise ValueError("%s is the plugin's own ruleset." % rs.name)
        except ValueError as e:
            log.append("ruleset %s left out: %s" % (path, e))
            continue
        rulesets[rs.name] = rs
        log.append("ruleset %s added from %s" % (rs.name, path))
    return rulesets, log


RULESETS, RULES_LOG = load_rulesets()


def ruleset(rules):
    """The Ruleset of a rules id (none given: standard). Raises Illegal when this server doesn't have it."""
    rs = RULESETS.get(rules or DEFAULT)
    if rs is None:
        raise Illegal("this server has no Grid Words ruleset %s" % rules)
    return rs


def from_geometry(lines):
    """A table's static geometry (its PRM and TIL lines, docs/types/gridwords-1.md) -> Ruleset, for a program
    that sees a table only through its view, as the bot does. idle is not in the geometry and stays None."""
    tiles, premium = {}, {}
    for l in lines:
        f = l.split()
        if f[:1] == ["PRM"] and len(f) >= 2:
            for i in range(2, len(f) - 1, 2):
                premium[(int(f[i]), int(f[i + 1]))] = f[1]
        elif f[:1] == ["TIL"] and len(f) == 4:
            tiles[f[1]] = (int(f[2]), int(f[3]))
    return Ruleset("", tiles, premium)


INFO = {
    "type": "gridwords", "ver": 1, "name": "Grid Words",
    "rules": list(RULESETS), "min": 2, "max": 4, "flags": ["nomoves"],
    "options": [], "geometry": True, "log": RULES_LOG,
}

# A view request may carry "open": true, which the core sends for a server extension whose program receives the bag
# and every rack (VUE O at a table marked integrity=false; docs/protocol.md). A seated player's view then also has
# every rack and the bag in draw order (RK and BO lines).


def idle_limit(rules, np):
    """The turns in a row without a word played that end a game of np players under these rules."""
    return ruleset(rules).idle_limit(np)


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


def rack_value(rack, rs):
    return sum(rs.values.get(c, 0) for c in rack)


def evaluate(st, seat, x, y, d, text, words, rs=None):
    """Check a play and score it with the ruleset rs (None: standard). words is the word list (None: do not
    check words). -> {"score", "words", "placed", "rack", "cells", "full"}; raises Illegal with the reason."""
    rs = rs or RULESETS[DEFAULT]
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
            v = 0 if c.islower() else rs.values.get(c, 0)
            if (cx, cy) in fresh:
                kind = rs.premium.get((cx, cy))
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


def finish(st, events, rs, out_seat=None):
    """The game ends: every rack's points come off its owner's score; a player
    who went out gains the total of the others' racks."""
    left = [rack_value(r, rs) for r in st["racks"]]
    for s in range(st["np"]):
        adj = -left[s] + (sum(left) if s == out_seat else 0)
        st["scores"][s] += adj
        events.append("ADJ %d %+d %s" % (s, adj, st["racks"][s] or "-"))
    st["over"] = True
    st["result"] = result_text(st)


def next_turn(st, events):
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
    return out


# ---- the plugin operations ---------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    n = req.get("players")
    if not isinstance(n, int) or not 2 <= n <= 4:
        return {"error": "two to four players"}
    rs = RULESETS.get(req.get("rules") or DEFAULT)
    if rs is None:
        return {"error": "the rulesets are: %s" % " ".join(INFO["rules"])}
    if req.get("options"):
        return {"error": "no options for this game"}
    if not load_words():
        return {"error": "this server has no word list for Grid Words yet"}
    seed = int(req.get("seed") or 0)
    bag = [c for c, (_, count) in sorted(rs.tiles.items()) for _ in range(count)]
    random.Random(seed).shuffle(bag)
    bag = "".join(bag)
    racks = []
    for _ in range(n):
        racks.append(bag[-RACK:])
        bag = bag[:-RACK]
    st = {"np": n, "seed": seed, "cells": "." * (SIZE * SIZE), "bag": bag, "racks": racks,
          "scores": [0] * n, "turn": 0, "idle": 0, "shuffles": 0, "last": None, "over": False, "result": ""}
    return {"state": st, "events": ["TURN 0"]}


def geometry(rs):
    """A ruleset's static geometry: the board, its center, its bonus squares and its tiles."""
    out = ["BRD %d %d" % (SIZE, SIZE), "CTR %d %d" % CENTRE]
    for kind in ("TW", "DW", "TL", "DL"):
        squares = sorted(((y, x) for (x, y), k in rs.premium.items() if k == kind))
        pairs = ["%d %d" % (x, y) for y, x in squares]
        for i in range(0, len(pairs), 10):
            out.append("PRM %s %s" % (kind, " ".join(pairs[i:i + 10])))
    out += ["TIL %s %d %d" % (c, v, k) for c, (v, k) in sorted(rs.tiles.items())]
    return out


def op_geometry(req):
    """The static geometry of a ruleset without a game: for a table still waiting for players, and for GEO."""
    rs = RULESETS.get(req.get("rules") or DEFAULT)
    if rs is None:
        return {"error": "this server has no Grid Words ruleset %s" % req.get("rules")}
    return {"geometry": geometry(rs)}


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    try:
        rs = ruleset(req.get("rules"))
    except Illegal as e:
        return {"error": str(e)}
    triples = ["%d %d %s" % (i % SIZE + 1, i // SIZE + 1, c) for i, c in enumerate(st["cells"]) if c != "."]
    wire = ["X " + " ".join(triples[i:i + 9]) for i in range(0, len(triples), 9)]
    if 0 <= seat < st["np"]:
        wire.append("R " + (st["racks"][seat] or "-"))
        if req.get("open"):     # every rack, and the bag with the next tile first
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
    return {"wire": wire, "geometry": geometry(rs), "status": status, "data": data}


def op_moves(req):
    return {"check": True}


def op_result(req):
    st = req["state"]
    return {"over": True, "scores": st["scores"]} if st["over"] else {"over": False}


def op_apply(req):
    """A move, or (check) whether it would be taken."""
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    check = bool(req.get("check"))
    if st["over"]:
        return {"error": "the game is over"}
    if not 0 <= seat < st["np"]:
        return {"error": "you are not playing"}
    if not check and seat != st["turn"]:
        return {"error": "not your turn"}
    verb = move[0].upper() if move else ""
    try:
        rs = ruleset(req.get("rules"))
        if verb == "PLAY":
            return play(st, seat, move, check, rs)
        if verb == "EXCH":
            return exchange(st, seat, move, check, rs)
        if verb == "PASS" and len(move) == 1:
            if check:
                return {"info": "0"}
            st = copy_state(st)
            events = ["PASS %d" % seat]
            return idle_turn(st, events, rs)
    except Illegal as e:
        return {"error": str(e)}
    return {"error": "a move is PLAY x y H|V letters, EXCH letters or PASS"}


def play(st, seat, move, check, rs):
    if len(move) != 5 or not move[1].isdigit() or not move[2].isdigit() or move[3].upper() not in ("H", "V"):
        raise Illegal("PLAY x y H|V letters, like PLAY 8 8 H WORD")
    words = load_words()
    if not words:
        raise Illegal("this server has no word list")
    x, y, d = int(move[1]), int(move[2]), move[3].upper()
    r = evaluate(st, seat, x, y, d, move[4], words, rs)
    if check:
        info = str(r["score"])
        for w in r["words"]:
            if len(info) + 1 + len(w) > 70:
                break
            info += " " + w
        return {"info": info}
    st = copy_state(st)
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
    if k:
        events.append("DRAW %d %d" % (seat, k))
    if not rack and not st["bag"]:
        finish(st, events, rs, out_seat=seat)
        return {"state": st, "events": events, "end": st["result"]}
    next_turn(st, events)
    return {"state": st, "events": events}


def exchange(st, seat, move, check, rs):
    if len(move) != 2 or not all(c == "_" or "A" <= c <= "Z" for c in move[1].upper()) or len(move[1]) > RACK:
        raise Illegal("EXCH letters, like EXCH QV_ (_ is a blank)")
    give = move[1].upper()
    rack = list(st["racks"][seat])
    for c in give:
        if c not in rack:
            raise Illegal("your rack has no %s" % ("blank" if c == "_" else c))
        rack.remove(c)
    if len(st["bag"]) < RACK:
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
    return idle_turn(st, ["EXCH %d %d" % (seat, len(give))], rs)


def idle_turn(st, events, rs):
    """A pass or an exchange. The ruleset's idle limit of turns in a row without a word played ends the
    game (Ruleset.idle_limit)."""
    st["idle"] += 1
    if st["idle"] >= rs.idle_limit(st["np"]):
        finish(st, events, rs)
        return {"state": st, "events": events, "end": st["result"]}
    next_turn(st, events)
    return {"state": st, "events": events}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "played FRAULEIN for 65", "exchanged 3 tiles", "passed"."""
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != str(req.get("seat")):
            continue
        if p[0] == "PLAY" and len(p) >= 7:
            return {"say": "played %s for %s" % (p[5], p[6])}
        if p[0] == "EXCH" and len(p) >= 3:
            return {"say": "exchanged %s tile%s" % (p[2], "" if p[2] == "1" else "s")}
        if p[0] == "PASS":
            return {"say": "passed"}
    return {"say": ""}


# ---- a typed command line (the core's typed request; docs/plugins.md) ----------------------------------
# A player types only the letters of their own rack, from an empty start square. The letters fill the empty
# squares in order, and the tiles already on the board between, just before and just after them join the
# word, so `i4 x` beside HE plays HEX. The move made (PLAY x y H|V letters) holds the whole word from its first
# square, the board's letters included. This is the layout telnet uses for typed plays.

INFO["typed"] = True
TYPED_SQUARE_RE = re.compile(r"^[a-z][0-9]{1,2}\Z")
TYPED_LETTERS_RE = re.compile(r"^[A-Za-z_]{1,30}\Z")
TYPED_EXCHANGE_RE = re.compile(r"^[A-Za-z_]{1,7}\Z")
ACROSS_WORDS = ("across", "a", "right", "h", "horizontal")
DOWN_WORDS = ("down", "d", "v", "vertical")
CHECK_HINT = "Type the word to check after /check, as in /check h8 across QUIZ."
EXCHANGE_HINT = "Type the tiles to put back after /exchange, as in /exchange QXZ, with _ for a blank."


class Refused(Exception):
    """Typed letters that make no play. The reason is a sentence for the player."""


def letter_tokens(typed):
    """Typed letters -> one token per tile: 'A', or '_A' for a blank's letter."""
    out, blank = [], False
    for c in typed.upper():
        if c == "_":
            blank = True
            continue
        out.append(("_" if blank else "") + c)
        blank = False
    return out


def lay_out(st, x, y, d, typed):
    """Where a player's typed letters go from the empty start square x, y, across (H) or down (V).
    -> (sx, sy, word), the whole word from its first square as PLAY takes it. Raises Refused when the start
    square has a tile, no letter is typed, or the letters run off the board."""
    cells = st["cells"]

    def tile(cx, cy):
        return inside(cx, cy) and at(cells, cx, cy) != "."

    if tile(x, y):
        raise Refused("There is already a tile on %s. Start your word on an empty square." % sq_name(x, y))
    toks = letter_tokens(typed)
    if not toks or typed.endswith("_"):
        raise Refused("Type the letters from your rack, with _ before the letter a blank stands for.")
    dx, dy = (1, 0) if d == "H" else (0, 1)
    sx, sy = x, y
    while tile(sx - dx, sy - dy):
        sx, sy = sx - dx, sy - dy
    word, cx, cy = [], sx, sy
    while (cx, cy) != (x, y):
        word.append(at(cells, cx, cy).upper())
        cx, cy = cx + dx, cy + dy
    i = 0
    while i < len(toks):
        if not inside(cx, cy):
            raise Refused("From %s %s, those letters run off the board." % (
                sq_name(x, y), "across" if d == "H" else "down"))
        if tile(cx, cy):
            word.append(at(cells, cx, cy).upper())
        else:
            word.append(toks[i])
            i += 1
        cx, cy = cx + dx, cy + dy
    while tile(cx, cy):
        word.append(at(cells, cx, cy).upper())
        cx, cy = cx + dx, cy + dy
    return sx, sy, "".join(word)


def typed_play(st, words):
    """The words of a typed play -> its move tokens ['PLAY', x, y, 'H' or 'V', letters], or None when the
    words are not a play. The forms are `h8 QUIZ` (across), `h8 across QUIZ`, `h8 down QUIZ` and the
    protocol's own `play 8 8 H QUIZ`. Raises Refused when the letters make no play."""
    low = [w.lower() for w in words]
    if len(words) == 5 and low[0] == "play" and words[1].isascii() and words[1].isdigit() \
            and words[2].isascii() and words[2].isdigit() and low[3] in ("h", "v") \
            and TYPED_LETTERS_RE.match(words[4]):
        return ["PLAY", str(int(words[1])), str(int(words[2])), low[3].upper(), words[4].upper()]
    if len(words) not in (2, 3) or not TYPED_SQUARE_RE.match(low[0]) or not TYPED_LETTERS_RE.match(words[-1]):
        return None
    if len(words) == 3 and low[1] not in ACROSS_WORDS + DOWN_WORDS:
        return None
    d = "V" if len(words) == 3 and low[1] in DOWN_WORDS else "H"
    x, y = ord(low[0][0]) - ord("a") + 1, int(low[0][1:])
    if not inside(x, y):
        raise Refused("There is no square %s on the board." % low[0])
    sx, sy, word = lay_out(st, x, y, d, words[-1])
    return ["PLAY", str(sx), str(sy), d, word]


def shown(play):
    """A play's move tokens -> the play in the player's words: 'HEX across from g4'."""
    return "%s %s from %s" % (play[4].replace("_", ""), "across" if play[3] == "H" else "down",
                              sq_name(int(play[1]), int(play[2])))


def listed(items):
    """['QUIZ', 'QI', 'ZA'] -> 'QUIZ, QI and ZA'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def as_sentence(reason):
    """A refusal from evaluate -> a sentence: 'your rack has no Q' -> 'Your rack has no Q.'"""
    head = "not in the word list: "
    if reason.startswith(head):
        bad = reason[len(head):].split()
        return "%s %s not in the word list." % (listed(bad), "is" if len(bad) == 1 else "are")
    return reason[:1].upper() + reason[1:].rstrip(".") + "."


def check_sentence(st, seat, play, rs):
    """What a play would score, or why it can't be played, worked out with the game's own rules."""
    words = load_words()
    if not words:
        return "This server has no word list for Grid Words."
    word = play[4].replace("_", "")
    try:
        r = evaluate(st, seat, int(play[1]), int(play[2]), play[3], play[4], words, rs)
    except Illegal as e:
        return "%s can't be played there. %s" % (word, as_sentence(str(e)))
    text = "%s would score %d" % (listed([w.upper() for w in r["words"]]), r["score"])
    if r["full"]:
        return text + ", with %d for using all seven tiles." % FULL_RACK_BONUS
    return text + "."


def op_typed(req):
    """The words typed after a slash at the table -> what the core does with them. /h8 across QUIZ (also
    /h8 QUIZ and /h8 down QUIZ) plays the letters from h8, /play 8 8 H QUIZ plays as the protocol writes
    it, /pass passes after asking, /exchange TILES puts those tiles back in the bag, and /check followed by
    a play says what it would score, on any player's turn."""
    st, seat = req["state"], req.get("seat", -1)
    words = req.get("words") or []
    low = [w.lower() for w in words]
    if not low:
        return {}
    if low[0] == "check":
        if len(words) == 1:
            return {"error": CHECK_HINT}
        try:
            play = typed_play(st, words[1:])
        except Refused as e:
            return {"say": str(e)}
        try:
            rs = ruleset(req.get("rules"))
        except Illegal as e:
            return {"error": as_sentence(str(e))}
        return {"say": check_sentence(st, seat, play, rs)} if play else {"error": CHECK_HINT}
    refused = None
    if low == ["pass"]:
        reply = {"move": ["PASS"], "ask": "pass", "what": "pass"}
    elif low[0] in ("exchange", "exch", "swap"):
        letters = "".join(words[1:])
        if not TYPED_EXCHANGE_RE.match(letters):
            return {"error": EXCHANGE_HINT}
        reply = {"move": ["EXCH", letters.upper()], "what": "exchange those tiles"}
    else:
        try:
            play = typed_play(st, words)
        except Refused as e:
            play, refused = None, str(e)
        if not play and not refused:
            return {}
        reply = {"move": play, "what": "play " + shown(play)} if play else None
    if seat != st["turn"]:
        return {"error": "It's not your turn."}
    return {"error": refused} if refused else reply


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "say": op_say, "typed": op_typed, "geometry": op_geometry}


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
