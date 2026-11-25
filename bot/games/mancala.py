"""Mancala: a move that earns another turn first (kalah), then the one that
gains its store the most (a capture counts in full) less what the other side
could capture straight after, then the move that keeps the most seeds on its
own side. Only the moves the server lists are played, so oware's must-feed
rule holds."""
from ..rules import plugin

KEY = "mancala/1"


def state(v):
    ru = v.one("RU", ["kalah", "4"])
    return {"rules": ru[0], "b": [int(x) for x in v.one("B")], "over": False}


def score(st, seat, pit):
    m = plugin("mancala")
    b, _, captured, again, _ = m.sow(st, seat, pit)
    gain = b[m.STORE[seat]] - st["b"][m.STORE[seat]]
    after = dict(st, b=b)
    other = 1 - seat
    threat = 0 if again else max([m.sow(after, other, p)[2] for p in m.legal(after, other)] or [0])
    mine = sum(b[i] for i in m.own_row(seat)) + b[m.STORE[seat]]
    return (1 if again else 0, gain - threat, mine, -pit)


async def smart(t, v):
    if not v.moves:
        return
    st = state(v)
    best = max(v.moves, key=lambda p: score(st, t.seat, int(p)))
    await t.move(best)


async def random_turn(t, v):
    if v.moves:
        await t.move(t.rng.choice(v.moves))
