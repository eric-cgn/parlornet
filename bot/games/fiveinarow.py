"""Five in a Row: win at once if it can; else block a point where the other
player would win; else the point that best extends its own lines and blocks
the other's (an open three of theirs counts nearly as much as an open four of
its own), among the points near the stones."""
from ..rules import plugin

KEY = "fiveinarow/1"
DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))
# value of a line through a new stone: (length, open ends) -> points
VALUE = {(4, 2): 100000, (4, 1): 10000, (3, 2): 5000, (3, 1): 500, (2, 2): 200, (2, 1): 50, (1, 2): 10, (1, 1): 2}


def state(v):
    n = int(v.one("BRD", ["15"])[0])
    rules = v.one("RUL", ["freestyle"])[0]
    cells = ["."] * (n * n)
    m = plugin("fiveinarow")
    for row in v.all("P"):
        for pt in row[1:]:
            i = m.parse_sq(pt, n)
            if i is not None:
                cells[i] = row[0]
    return {"n": n, "rules": rules, "cells": "".join(cells)}


def wins(st, i, seat):
    m = plugin("fiveinarow")
    s = dict(st, cells=st["cells"][:i] + str(seat) + st["cells"][i + 1:])
    return bool(m.line_through(s, i, seat))


def line_value(st, i, seat):
    n, cells, me = st["n"], st["cells"], str(seat)
    x0, y0 = i % n, i // n
    total = 0
    for dx, dy in DIRS:
        length, ends = 1, 0
        for sgn in (1, -1):
            x, y = x0 + sgn * dx, y0 + sgn * dy
            while 0 <= x < n and 0 <= y < n and cells[y * n + x] == me:
                length += 1
                x, y = x + sgn * dx, y + sgn * dy
            if 0 <= x < n and 0 <= y < n and cells[y * n + x] == ".":
                ends += 1
        if length >= 5:
            total += 1000000
        elif ends:
            total += VALUE.get((length, ends), 0)
    return total


def candidates(st):
    n, cells = st["n"], st["cells"]
    near = set()
    for i, c in enumerate(cells):
        if c == ".":
            continue
        x0, y0 = i % n, i // n
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                x, y = x0 + dx, y0 + dy
                if 0 <= x < n and 0 <= y < n and cells[y * n + x] == ".":
                    near.add(y * n + x)
    if not near:
        near = {(n // 2) * n + n // 2}
    return sorted(near)


def choose(st, seat, rng):
    other = 1 - seat
    cands = candidates(st)
    for i in cands:
        if wins(st, i, seat):
            return i
    for i in cands:
        if wins(st, i, other):
            return i
    return max(cands, key=lambda i: (line_value(st, i, seat) * 1.1 + line_value(st, i, other), rng.random()))


async def smart(t, v):
    st = state(v)
    i = choose(st, t.seat, t.rng)
    await t.move(plugin("fiveinarow").sq_name(st["n"], i))


async def random_turn(t, v):
    st = state(v)
    free = [i for i, c in enumerate(st["cells"]) if c == "."]
    if free:
        await t.move(plugin("fiveinarow").sq_name(st["n"], t.rng.choice(free)))
