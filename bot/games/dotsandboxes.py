"""Dots and Boxes: a line that closes a box whenever there is one (the most
boxes first); otherwise a line that draws no box's third side; otherwise the
line that hands over the fewest boxes, at random among equals."""
from . import seat_flags
from ..rules import plugin

KEY = "dotsandboxes/1"


def dropped(v, seat):
    return seat_flags(v, seat)


def state(v):
    w, h = (int(x) for x in v.one("BRD"))
    hl = "".join(r[1] for r in sorted(v.all("HL"), key=lambda r: int(r[0])))
    vl = "".join(r[1] for r in sorted(v.all("VL"), key=lambda r: int(r[0])))
    return {"w": w, "h": h, "hl": hl, "vl": vl}


def sides(st, bx, by, extra=None):
    """Drawn sides of box bx,by, counting the line extra as drawn."""
    w = st["w"]
    lines = [("h", bx, by), ("h", bx, by + 1), ("v", bx, by), ("v", bx + 1, by)]
    m = plugin("dotsandboxes")
    return sum(1 for ln in lines if ln == extra or not m.is_open(st, *ln))


def boxes_of(st, ln):
    kind, x, y = ln
    if kind == "h":
        cand = [(x, y - 1), (x, y)]
    else:
        cand = [(x - 1, y), (x, y)]
    return [(bx, by) for bx, by in cand if 0 <= bx < st["w"] and 0 <= by < st["h"]]


def score(st, tok, rng):
    ln = plugin("dotsandboxes").parse_line(tok, st)
    if isinstance(ln, str):
        return (-9, 0, 0)
    after = [sides(st, bx, by, ln) for bx, by in boxes_of(st, ln)]
    closes = sum(1 for n in after if n == 4)
    thirds = sum(1 for n in after if n == 3)
    return (closes, -thirds, rng.random())


async def smart(t, v):
    if not v.moves:
        return
    st = state(v)
    await t.move(max(v.moves, key=lambda m: score(st, m, t.rng)))


async def random_turn(t, v):
    if v.moves:
        await t.move(t.rng.choice(v.moves))
