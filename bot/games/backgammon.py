"""Backgammon: every way to play the roll is tried (with the game's own rules
for which steps are legal) and the resulting positions are scored: checkers
hit, points made (more in its home board), blots left where the other side
can reach them, checkers borne off and its pip count. It never doubles; it
takes a double when its pip count is at most about 12% behind the doubler's,
else it drops; it accepts a resignation offered to it."""
from ..rules import plugin

KEY = "backgammon/1"


def state(v, seat):
    p1 = [int(x) for x in v.one("P")[1:]]
    p13 = [int(x) for x in v.all("P")[1][1:]]
    dice = [int(x) for x in v.one("D", [])]
    rolled = [int(x) for x in v.one("DR", [])]
    full = rolled * 2 if len(rolled) == 2 and rolled[0] == rolled[1] else rolled
    return {"pts": p1 + p13, "bar": [int(x) for x in v.one("B")], "off": [int(x) for x in v.one("O")],
            "turn": seat, "dice": dice, "steps": [] if sorted(dice) == sorted(full) else ["played"]}


def pips(st, seat):
    if seat == 0:
        return sum(p * n for p, n in zip(range(1, 25), st["pts"]) if n > 0) + 25 * st["bar"][0]
    return sum((25 - p) * -n for p, n in zip(range(1, 25), st["pts"]) if n < 0) + 25 * st["bar"][1]


def evaluate(st, start, seat):
    s = 1 if seat == 0 else -1
    other = 1 - seat
    score = 3.0 * (st["bar"][other] - start["bar"][other])     # checkers hit
    score += 0.6 * (st["off"][seat] - start["off"][seat])
    home = range(1, 7) if seat == 0 else range(19, 25)
    theirs = [p for p in range(1, 25) if st["pts"][p - 1] * s < 0]
    if st["bar"][other]:
        theirs.append(25 if seat == 0 else 0)       # where a checker on the bar comes in from
    for p in range(1, 25):
        n = st["pts"][p - 1] * s
        if n >= 2:
            score += 1.0 + (0.6 if p in home else 0.0)
        elif n == 1:
            # a blot: in reach of a checker of theirs behind it (they move the other way)
            reach = any(0 < (q - p if seat == 0 else p - q) <= 12 for q in theirs)
            score -= 2.0 if reach else 0.2
    return score - 0.01 * pips(st, seat)


def sequences(st, seat):
    """{position key: (state after, the steps)} for every way to finish the roll from st."""
    m = plugin("backgammon")
    frontier = {None: (st, [])}
    done = {}
    for _ in range(5):
        if not frontier:
            break
        nxt = {}
        for s, path in frontier.values():
            lg = m.legal_steps(s) if s["off"][seat] < 15 else {}
            if not lg:
                done[(tuple(s["pts"]), tuple(s["bar"]), tuple(s["off"]))] = (s, path)
                continue
            for (frm, to), d in lg.items():
                nb, _ = m.apply_step(m.board_of(s), seat, frm, to)
                dice = list(s["dice"])
                dice.remove(d)
                s2 = dict(s, pts=nb["pts"], bar=nb["bar"], off=nb["off"], dice=dice, steps=s["steps"] + ["played"])
                k = (tuple(nb["pts"]), tuple(nb["bar"]), tuple(nb["off"]), tuple(sorted(dice)))
                if k not in nxt:
                    nxt[k] = (s2, path + [m.tok(frm, to)])
        frontier = nxt
    for s, path in frontier.values():
        done[(tuple(s["pts"]), tuple(s["bar"]), tuple(s["off"]))] = (s, path)
    return done


def takes(st, seat):
    """Take a double: its pips at most about 12% more than the doubler's."""
    mine, theirs = pips(st, seat), pips(st, 1 - seat)
    return mine <= theirs * 1.12 + 2


async def smart(t, v):
    q = (v.one("Q") or ["move"])[0]
    seat = t.seat
    if q == "roll":
        await t.move("roll")
    elif q == "take":
        await t.move("take" if takes(state(v, seat), seat) else "drop")
    elif q == "resign":
        await t.move("accept")
    elif q == "move":
        st = state(v, seat)
        options = sequences(st, seat)
        if not options:
            await t.end()
            return
        best = max(options.values(), key=lambda o: (evaluate(o[0], st, seat), t.rng.random()))
        await play(t, best[1])


async def play(t, steps):
    final = "OK done"
    for step in steps:
        final = await t.move(step)
        if not final.startswith("OK"):
            return
    if final == "OK done":
        await t.end()


async def random_turn(t, v):
    q = (v.one("Q") or ["move"])[0]
    if q == "roll":
        await t.move("roll")
    elif q in ("take", "resign"):
        await t.move(t.rng.choice(v.moves or ["take"]))
    elif q == "move":
        if not v.moves:
            await t.end()
            return
        final = await t.move(t.rng.choice(v.moves))
        while final == "OK more":
            nv = await t.view()
            if nv is None or not nv.moves:
                break
            final = await t.move(t.rng.choice(nv.moves))
        if final in ("OK done", "OK more"):
            await t.end()
