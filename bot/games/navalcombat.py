"""Naval Combat: the fleet placed at random by the server (random, then
ready). Shooting, hunt and target: next to a ship it has hit and not sunk,
along the line when two hits line up; otherwise at random on a checkerboard
of the squares not yet fired at, leaving out squares beside a sunk ship where
ships may not touch (batnav)."""
KEY = "navalcombat/1"
N = 10


def grid(v, seat):
    """The other seat's waters as this player sees them: {(x, y): char}."""
    other = str(1 - seat)
    g = {}
    for row in v.all("B"):
        if len(row) == 3 and row[0] == other:
            y = int(row[1]) - 1
            for x, c in enumerate(row[2]):
                g[(x, y)] = c
    return g


def sq(x, y):
    return "%s%d" % (chr(ord("a") + x), y + 1)


def choose(v, seat, rng):
    g = grid(v, seat)
    free = {}
    for m in v.moves:
        x, y = ord(m[0]) - ord("a"), int(m[1:]) - 1
        free[(x, y)] = m
    if not free:
        return None
    hits = [p for p, c in g.items() if c == "x"]
    no_touch = 1 in [int(x) for x in v.one("FL", [])]
    scores = {}
    for (x, y), m in free.items():
        s = 0.0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if g.get((x + dx, y + dy)) == "x":
                s += 10
                if g.get((x + 2 * dx, y + 2 * dy)) == "x":
                    s += 20             # the line of two hits goes on here
        if no_touch and any(g.get((x + dx, y + dy)) == "#" for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            s -= 50                     # a ship cannot lie beside a sunk one
        if not hits and (x + y) % 2 == 0:
            s += 1
        scores[(x, y)] = s + rng.random()
    return free[max(scores, key=scores.get)]


async def smart(t, v):
    q = (v.one("Q") or [""])[0]
    if q == "place":
        if v.one("N"):
            if not (await t.move("random")).startswith("OK"):
                return
        await t.move("ready")
    elif q == "shoot":
        m = choose(v, t.seat, t.rng)
        if m:
            await t.move(m)


async def random_turn(t, v):
    q = (v.one("Q") or [""])[0]
    if q == "place":
        await smart(t, v)
    elif v.moves:
        await t.move(t.rng.choice(v.moves))
