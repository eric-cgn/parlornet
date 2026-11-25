"""Cannon Fire: aims at the nearest tank still standing. It tries shots
(angles every 5 degrees towards the target, powers every 5) with the game's
own flight and blast rules, and takes the one that does the most damage to
other tanks and least to itself, nearest the target on a tie; then it adds a
small random error (up to 2 degrees and 3 points of power), so it can be
beaten. It never drives its tank."""
from . import seat_flags
from ..rules import plugin

KEY = "cannonfire/1"


def dropped(v, seat):
    return seat_flags(v, seat)


def state(v):
    w = int(v.one("ARN")[0])
    h = []
    for row in sorted(v.all("Z"), key=lambda r: int(r[0])):
        h += [ord(c) - 0x30 for c in row[1]]
    tanks = [[int(r[1]), int(r[2])] for r in sorted(v.all("K"), key=lambda r: int(r[0]))]
    return {"w": w, "h": h[:w], "tanks": tanks, "wind": int(v.one("N", ["0"])[0])}


def damage(st, impact, seat):
    """Hit points the burst takes from tank seat."""
    m = plugin("cannonfire")
    x, hp = st["tanks"][seat]
    if impact is None or hp <= 0:
        return 0
    cx, cy = impact
    d = m.isqrt((x - cx) ** 2 + (st["h"][x] + 1 - cy) ** 2)
    return min(hp, m.DMG_MAX * (m.REACH - d) // m.REACH) if d < m.REACH else 0


def aim(st, seat):
    """(angle, power) of the best shot found, and its score."""
    m = plugin("cannonfire")
    me = st["tanks"][seat][0]
    living = [s for s, (x, hp) in enumerate(st["tanks"]) if hp > 0 and s != seat]
    if not living:
        return (90, 0), 0
    target = min(living, key=lambda s: abs(st["tanks"][s][0] - me))
    tx = st["tanks"][target][0]
    angles = range(15, 90, 5) if tx > me else range(95, 170, 5)
    best = None
    for a in angles:
        for p in range(20, 101, 5):
            _, impact, _ = m.fly(st, seat, a, p)
            hurt = sum(damage(st, impact, s) for s in living)
            own = damage(st, impact, seat)
            miss = abs(impact[0] - tx) if impact else 99
            key = (hurt - 2 * own, -miss)
            if best is None or key > best[0]:
                best = (key, a, p)
    return (best[1], best[2]), best[0]


async def smart(t, v):
    st = state(v)
    (a, p), _ = aim(st, t.seat)
    a = max(0, min(180, a + t.rng.randint(-2, 2)))
    p = max(1, min(100, p + t.rng.randint(-3, 3)))
    await t.move("FIRE %d %d" % (a, p))


async def random_turn(t, v):
    await t.move("FIRE %d %d" % (t.rng.randint(10, 170), t.rng.randint(25, 100)))
