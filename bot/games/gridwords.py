"""Grid Words: the highest-scoring play it finds from its rack in about a
second. It walks every row and column from each starting square that can
reach a tile on the board (the center on the first play), placing rack
letters left to right and dropping a branch as soon as no word in the list
starts that way or a letter would make a cross word that is not in the list.
Every play found is scored by the game's own rules, with the letter values and
bonus squares of the table's ruleset from its view's geometry, a blank costing a little,
and the best one is confirmed with CHK before it is played. With nothing
found it exchanges its rack (keeping blanks and S) while the bag can take it,
else passes.

The word list is the plugin's own compact Lexicon (sorted fixed-width records
per word length), read once per process when the first Grid Words turn needs
it; prefixes are looked up in it by binary search."""
import heapq
import time

from ..rules import plugin

KEY = "gridwords/1"
SIZE = 15
TIME_CAP = 1.0          # seconds of search a turn (the runner's option --gridwords-secs)
MEMO = 20000            # prefixes a search remembers at most (about 2 MB)
ALL_LENGTHS = sum(1 << n for n in range(2, SIZE + 1))


def lexicon():
    return plugin("gridwords").load_words()


def state(v, seat):
    cells = ["."] * (SIZE * SIZE)
    for row in v.all("X"):
        for i in range(0, len(row) - 2, 3):
            x, y, c = int(row[i]), int(row[i + 1]), row[i + 2]
            cells[(y - 1) * SIZE + (x - 1)] = c
    r = v.one("R", ["-"])[0]
    rack = "" if r == "-" else r.upper()
    bag = int(v.one("BAG", ["0"])[0])
    return {"cells": "".join(cells), "racks": [""] * seat + [rack], "bag": bag}


def ruleset(v):
    """The table's ruleset as its view's geometry gives it (the PRM and TIL lines of VUE's flag G)."""
    return plugin("gridwords").from_geometry(v.lines)


class Finder:
    """The plays a rack can make on a board, found within a time limit. Each play found goes to found(x, y,
    d, text) (by default kept in a list). For each prefix it asks the word list once which word lengths
    start that way (a bit mask), and remembers at most MEMO prefixes, so a search holds little memory."""

    def __init__(self, lex, cells, rack, deadline, found=None):
        self.lex, self.cells, self.rack, self.deadline = lex, cells, rack, deadline
        self.data, self.spans = lex._data, lex._spans
        self.memo = {}
        self.plays = []
        self.found = found or (lambda *play: self.plays.append(play))
        self.empty_board = cells.count(".") == SIZE * SIZE
        self.timed_out = False

    def starts(self, pre):
        """A bit mask of the word lengths n (bit n) for which some word starts with pre (bytes). Only the
        lengths its prefix one letter shorter has are looked up."""
        r = self.memo.get(pre)
        if r is not None:
            return r
        m = len(pre)
        parent = self.starts(pre[:-1]) if m > 1 else ALL_LENGTHS
        r, data = 0, self.data
        for n in range(max(2, m), SIZE + 1):
            if not parent >> n & 1:
                continue
            start, count = self.spans[n]
            lo, hi = 0, count
            while lo < hi:
                mid = (lo + hi) >> 1
                at = start + mid * n
                if data[at:at + m] < pre:
                    lo = mid + 1
                else:
                    hi = mid
            if lo < count and data[start + lo * n:start + lo * n + m] == pre:
                r |= 1 << n
        if len(self.memo) >= MEMO:
            self.memo.clear()
        self.memo[pre] = r
        return r

    def line(self, d, i):
        """Row (d "H") or column ("V") i, 0-based: its 15 cells and a function to their board index."""
        if d == "H":
            return [self.cells[i * SIZE + j] for j in range(SIZE)], (lambda j: i * SIZE + j)
        return [self.cells[j * SIZE + i] for j in range(SIZE)], (lambda j: j * SIZE + i)

    def cross(self, d, i):
        """For each empty square of the line: None (no cross word there) or the letters a cross word allows."""
        line, idx = self.line(d, i)
        out = [None] * SIZE
        for j in range(SIZE):
            if line[j] != ".":
                continue
            # the cross word runs across the line: along the other direction through square (i, j)
            before, after = "", ""
            k = i - 1
            while k >= 0 and self.at(d, k, j) != ".":
                before = self.at(d, k, j).upper() + before
                k -= 1
            k = i + 1
            while k < SIZE and self.at(d, k, j) != ".":
                after += self.at(d, k, j).upper()
                k += 1
            if before or after:
                out[j] = frozenset(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if before + c + after in self.lex)
        return out

    def at(self, d, line_i, j):
        """The square at line line_i, place j of a line in direction d."""
        return self.cells[line_i * SIZE + j] if d == "H" else self.cells[j * SIZE + line_i]

    def search(self):
        dirs = ("H",) if self.empty_board else ("H", "V")
        for d in dirs:
            for i in (range(7, 8) if self.empty_board else range(SIZE)):
                self.search_line(d, i)
                if self.timed_out:
                    return self.plays
        return self.plays

    def search_line(self, d, i):
        line, _ = self.line(d, i)
        cross = self.cross(d, i)
        anchors = [j for j in range(SIZE) if line[j] != "." or cross[j] is not None
                   or (j > 0 and line[j - 1] != ".") or (j < SIZE - 1 and line[j + 1] != ".")]
        if self.empty_board:
            anchors = [7]
        if not anchors:
            return
        ends = [e for e in range(SIZE) if e == SIZE - 1 or line[e + 1] == "."]
        for s in range(SIZE):
            if time.monotonic() > self.deadline:
                self.timed_out = True
                return
            if s > 0 and line[s - 1] != ".":
                continue
            first = next((a for a in anchors if a >= s), None)
            if first is None:
                break
            empties = sum(1 for j in range(s, first) if line[j] == ".")
            if empties >= len(self.rack) + (0 if line[first] == "." else 1):
                continue
            self.lengths = sum(1 << (e - s + 1) for e in ends if e > s)     # where a word from s may end
            self.walk(d, i, line, cross, s, s, b"", "", self.rack, 0, False)

    def walk(self, d, i, line, cross, s, pos, pre, text, rack, placed, joined):
        if pos > s + 1 and placed and joined and (pos == SIZE or line[pos] == "."):
            if self.starts(pre) >> (pos - s) & 1:         # a word of exactly these letters
                x, y = (s + 1, i + 1) if d == "H" else (i + 1, s + 1)
                self.found(x, y, d, text)
        if pos == SIZE:
            return
        lengths = self.lengths
        c = line[pos]
        if c != ".":
            p2 = pre + c.upper().encode("ascii")
            if self.starts(p2) & lengths:
                self.walk(d, i, line, cross, s, pos + 1, p2, text + c.upper(), rack, placed, True)
            return
        if not rack:
            return
        allowed = cross[pos]
        here_joins = allowed is not None or (self.empty_board and pos == 7 and i == 7)
        tried = set()
        for k, t in enumerate(rack):
            if t in tried:
                continue
            tried.add(t)
            rest = rack[:k] + rack[k + 1:]
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if t == "_" else t
            for L in letters:
                if allowed is not None and L not in allowed:
                    continue
                p2 = pre + L.encode("ascii")
                if self.starts(p2) & lengths:
                    self.walk(d, i, line, cross, s, pos + 1, p2, text + ("_" + L if t == "_" else L), rest,
                              placed + 1, joined or here_joins)


def plays(st, seat, secs, rs, best=8):
    """[(score, x, y, d, text)] best first: the best few the search finds in secs (best=None: all of them).
    Each is scored by the game's own rules with the ruleset rs as it is found."""
    lex = lexicon()
    if not lex:
        return []
    m = plugin("gridwords")
    out = []

    def found(x, y, d, text):
        try:
            r = m.evaluate(st, seat, x, y, d, text, lex, rs)
        except m.Illegal:
            return
        play = (r["score"] - 8 * text.count("_"), x, y, d, text)
        if best is None:
            out.append(play)
        elif len(out) < best:
            heapq.heappush(out, play)
        elif play > out[0]:
            heapq.heapreplace(out, play)
    Finder(lex, st["cells"], st["racks"][seat], time.monotonic() + secs, found).search()
    out.sort(reverse=True)
    return out


def keep(rack):
    """The tiles to exchange: all but blanks and S."""
    return "".join(c for c in rack if c not in "_S")


async def no_play(t, st):
    give = keep(st["racks"][t.seat])
    if st["bag"] >= 7 and give and (await t.move("EXCH %s" % give)).startswith("OK"):
        return
    await t.move("PASS")


async def smart(t, v, secs=None):
    st = state(v, t.seat)
    found = plays(st, t.seat, secs or TIME_CAP, ruleset(v))
    for score, x, y, d, text in found[:5]:
        tok = "PLAY %d %d %s %s" % (x, y, d, text)
        if (await t.check(tok)).startswith("OK"):
            if (await t.move(tok)).startswith("OK"):
                return
    await no_play(t, st)


async def random_turn(t, v):
    st = state(v, t.seat)
    found = plays(st, t.seat, 0.05, ruleset(v), None)
    if found and t.rng.random() < 0.8:
        score, x, y, d, text = t.rng.choice(found)
        if (await t.move("PLAY %d %d %s %s" % (x, y, d, text))).startswith("OK"):
            return
    await t.move("PASS")
