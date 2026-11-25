"""Checkers: every whole turn the rules allow is tried (a capture is
compulsory, and a line of jumps goes on to its end). It takes the most pieces,
then prefers a turn after which the other side can take the least, then
crowning, then keeping its own back row; ties at random. A draw offer is
accepted when it is not ahead in pieces."""
import json

from ..rules import plugin

KEY = "checkers/1"


def state(v, seat):
    cells = v.one("B")[0]
    steps = []
    path = [int(x) for x in v.one("J", [])]
    if len(path) >= 2:          # a line of jumps under way: the next step goes on from its last square
        steps = [{"from": path[-2], "to": path[-1], "cap": 1, "crowned": False, "piece": "", "capp": ""}]
    return {"cells": cells, "turn": seat, "phase": "move", "steps": steps, "over": False}


def material(cells, seat):
    men, kings = ("x", "X") if seat == 0 else ("o", "O")
    return cells.count(men) + 1.5 * cells.count(kings)


def turns(st, seat, limit=400):
    """Every way to finish the turn from st: [(tokens, cells after)]."""
    m = plugin("checkers")
    out = []

    def walk(s, toks):
        if len(out) >= limit:
            return
        lg = m.legal(s)
        if not lg or (toks and not s["steps"][-1]["cap"]):
            if toks:
                out.append((toks, s["cells"]))
            return
        for (a, b), cap in sorted(lg.items()):
            s2 = json.loads(json.dumps(s))
            if m.play_step(s2, seat, a, b, []):
                continue
            walk(s2, toks + [m.tok(a, b, cap)])
    walk(st, [])
    return out


def threat(cells, seat):
    """The most pieces the other side could take on its next turn."""
    other = 1 - seat
    st = {"cells": cells, "turn": other, "phase": "move", "steps": [], "over": False}
    m = plugin("checkers")
    if not m.all_jumps(cells, other):
        return 0
    best = 0
    for toks, after in turns(st, other, 60):
        best = max(best, material(cells, seat) - material(after, seat))
    return best


def score(cells, after, seat, rng):
    back = range(0, 4) if seat == 0 else range(28, 32)
    man = "x" if seat == 0 else "o"
    took = material(cells, 1 - seat) - material(after, 1 - seat)
    kept_back = sum(1 for i in back if after[i] == man)
    return (took - 0.9 * threat(after, seat) + (material(after, seat) - material(cells, seat)) * 0.6
            + 0.05 * kept_back + rng.random() * 0.01)


async def smart(t, v):
    q = (v.one("Q") or ["move"])[0]
    seat = t.seat
    if q == "draw":
        cells = v.one("B")[0]
        ahead = material(cells, seat) > material(cells, 1 - seat)
        await t.move("reject" if ahead else "accept")
        return
    if q == "done":
        await t.end()
        return
    st = state(v, seat)
    options = turns(st, seat)
    if not options:
        if v.moves:
            await t.move(v.moves[0])
        return
    cells = st["cells"]
    toks, _ = max(options, key=lambda o: score(cells, o[1], seat, t.rng))
    await play(t, toks)


async def play(t, toks):
    final = "OK"
    for tok in toks:
        final = await t.move(tok)
        if not final.startswith("OK"):
            return
    if final == "OK done":
        await t.end()


async def random_turn(t, v):
    q = (v.one("Q") or ["move"])[0]
    if q == "done":
        await t.end()
        return
    if q == "draw":
        await t.move(t.rng.choice(["accept", "reject"]))
        return
    if not v.moves:
        return
    final = await t.move(t.rng.choice(v.moves))
    while final == "OK more":
        nv = await t.view()
        if nv is None or not nv.moves:
            return
        final = await t.move(t.rng.choice(nv.moves))
    if final == "OK done":
        await t.end()
