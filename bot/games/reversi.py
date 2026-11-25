"""Reversi: a corner if one is free to take; never a square next to an empty
corner unless nothing else is left; otherwise the move that flips the most
discs (ties broken at random)."""
from ..rules import plugin

KEY = "reversi/1"


def board(v):
    rows = [r[1] for r in sorted(v.all("R"), key=lambda r: int(r[0]))]
    n = len(rows)
    return {"n": n, "cells": "".join(rows), "turn": 0, "over": False}


def score(st, sq, seat, rng):
    rv = plugin("reversi")
    n = st["n"]
    x, y = rv.parse_sq(sq, n)
    corners = [(0, 0), (n - 1, 0), (0, n - 1), (n - 1, n - 1)]
    if (x, y) in corners:
        return 1000 + rng.random()
    near = any(st["cells"][cy * n + cx] == "." and max(abs(cx - x), abs(cy - y)) == 1 for cx, cy in corners)
    return len(rv.flips(st, x, y, seat)) - (100 if near else 0) + rng.random() / 2


async def smart(t, v):
    if not v.moves:
        return
    st = board(v)
    best = max(v.moves, key=lambda m: score(st, m, t.seat, t.rng))
    await t.move(best)


async def random_turn(t, v):
    if v.moves:
        await t.move(t.rng.choice(v.moves))
