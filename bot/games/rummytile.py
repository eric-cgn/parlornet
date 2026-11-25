"""Rummy Tile Classic: it plays only from its own rack and never rearranges
the table. It finds the sets its rack can make (runs and groups, a joker
filling a gap) and plays the combination that puts down the most tiles, the
most points on a tie; before its first meld only once those sets reach 30
points. After the first meld it also lays tiles off onto the sets on the
table. Then END, which draws a tile when it played nothing. The server checks
the turn (CHK !end) before it is ended; a turn it would not take is reset."""
import itertools
from collections import Counter

from . import seat_flags
from ..rules import plugin

KEY = "rummytile/1"
COLOURS = "RBKY"
NODES = 20000


def dropped(v, seat):
    return seat_flags(v, seat)


def rack(v):
    return [t for row in v.all("H") for t in row]


def table(v):
    return [row[2:] for row in sorted(v.all("ST"), key=lambda r: int(r[0]))]


def melded(v, seat):
    i = v.one("I", [])
    return seat < len(i) and i[seat] == "1"


def candidates(tiles):
    """Every set the tiles can make: [(tiles of the set, points)]."""
    m = plugin("rummytile")
    have = Counter(tiles)
    jokers = have["J"]
    out = []
    for n in range(1, 14):
        cols = [c for c in COLOURS if have[c + str(n)]]
        for k in (4, 3):
            for combo in itertools.combinations(cols, k):
                out.append([c + str(n) for c in combo])
            for j in range(1, min(jokers, k - 1) + 1):
                for combo in itertools.combinations(cols, k - j):
                    if len(combo) >= 1:
                        out.append([c + str(n) for c in combo] + ["J"] * j)
    for c in COLOURS:
        for lo in range(1, 12):
            for hi in range(lo + 2, 14):
                nums = range(lo, hi + 1)
                missing = sum(1 for x in nums if not have[c + str(x)])
                if missing > jokers or missing >= len(nums):
                    continue
                out.append([c + str(x) if have[c + str(x)] else "J" for x in nums])
    seen, sets = set(), []
    for s in out:
        k = tuple(sorted(s))
        if k in seen or m.kind(s) == "x":
            continue
        seen.add(k)
        sets.append((s, m.value(s)))
    sets.sort(key=lambda x: (-len(x[0]), -x[1]))
    return sets


def best_combo(tiles, need):
    """The sets to put down: the most tiles (then points), at least need points in all."""
    cands = candidates(tiles)
    best = [(0, 0), []]
    nodes = [0]

    def dfs(i, avail, chosen, n, pts):
        nodes[0] += 1
        if pts >= need and (n, pts) > best[0]:
            best[0], best[1] = (n, pts), list(chosen)
        if nodes[0] > NODES:
            return
        for j in range(i, len(cands)):
            s, v = cands[j]
            c = Counter(s)
            if all(avail[t] >= k for t, k in c.items()):
                chosen.append(s)
                dfs(j + 1, avail - c, chosen, n + len(s), pts + v)
                chosen.pop()
    dfs(0, Counter(tiles), [], 0, 0)
    return best[1]


def lay_offs(tiles, sets):
    """[(tile, set number)] that each still leave a valid set, one after another."""
    m = plugin("rummytile")
    sets = [list(s) for s in sets]
    left = list(tiles)
    out = []
    changed = True
    while changed and left:
        changed = False
        for t in sorted(set(left), key=lambda x: (x == "J", x)):
            for k, s in enumerate(sets):
                if len(s) < m.MAX_SET and m.kind(s + [t]) != "x":
                    sets[k] = m.tidy(s + [t])
                    left.remove(t)
                    out.append((t, k + 1))
                    changed = True
                    break
            if changed:
                break
    return out


async def finish(t):
    """END if the server would take the turn as it stands; else start it over first (END then draws)."""
    if not (await t.check("!end")).startswith("OK"):
        await t.move("reset")
    await t.end()


async def smart(t, v):
    seat = t.seat
    tiles = rack(v)
    sets = table(v)
    first = not melded(v, seat)
    tp = v.one("TP", ["0"])
    if tp[0] != "0":                    # a turn under way (taken up again): start it over
        await t.move("reset")
    played = False
    combo = best_combo(tiles, 30 if first else 1)
    for s in combo:
        final = await t.move("%s-n" % "".join(s))
        if not final.startswith("OK"):
            break
        played = True
        for x in s:
            tiles.remove(x)
        sets.append(s)
    if not first:
        for tile, k in lay_offs(tiles, sets):
            if not (await t.move("%s-%d" % (tile, k))).startswith("OK"):
                break
            played = True
    if played:
        await finish(t)
    else:
        await t.end()


async def random_turn(t, v):
    seat = t.seat
    tiles = rack(v)
    if v.one("TP", ["0"])[0] != "0":
        await t.move("reset")
    need = 30 if not melded(v, seat) else 1
    fits = [s for s, pts in candidates(tiles) if pts >= need]
    if fits and t.rng.random() < 0.5:
        await t.move("%s-n" % "".join(t.rng.choice(fits)))
        await finish(t)
    else:
        await t.end()
