#!/usr/bin/env python3
"""cannonfire/1 game plugin (docs/types/cannonfire-1.md).

Tanks on a destructible heightmap, 2 to 8 players, taking turns to fire one
shell each with an angle and a power. Before its shot a tank may drive left or
right once (four times a game), as far as 10% of the arena width along the
ground. Speaks the plugin protocol of
docs/plugins.md: one JSON object per line on stdin, one JSON reply per line on
stdout. Pure functions of their arguments; the state is opaque to the server.

Everything is integer arithmetic (the sine table below, fixed-point positions
and velocities, a small linear congruential generator for the terrain and the
wind), so the same seed and the same shots give the same game on any machine.
The type spec gives every constant and rule, so another implementation can
replay a game exactly.

State: {"w": width, "h": [heights], "tanks": [[x, hp], ...], "aim": [[angle, power], ...],
        "score": [...], "wind": w, "windon": bool, "rng": int, "turn": seat, "over": bool,
        "shots": n, "hit": [x, y] or None, "moves": [moves left per seat], "moved": bool}
("moves" and "moved" are absent from states saved before tanks could move: 4 each and False.)

The extra op "drop" (flag "drop") lets one seat leave a game of three or more
(it resigned or ran out of time) while the others play on: its tank becomes a
wreck (0 hit points), so it is skipped in turn order like any destroyed tank.
With one tank left, that seat wins. The core calls it for RES and turn
timeouts while more than two seats are still playing.
"""
import json
import sys

INFO = {
    "type": "cannonfire", "ver": 1, "name": "Cannon Fire",
    "rules": ["classic"], "min": 2, "max": 8, "flags": ["nomoves", "drop"],
    "options": ["w", "seed", "wind"],
}

HMAX = 63           # the highest terrain the encoding carries ('o')
W_MIN, W_MAX, W_DEFAULT = 40, 200, 72
FP = 65536          # fixed point: 1 column (or 1 height unit) = 65536
GRAV = 320          # gravity, subtracted from vy every step
VK = 512            # launch speed per point of power
WK = 6              # wind acceleration per step, per point of wind
MAX_STEPS = 4000    # a shell still flying after this many steps is lost
RADIUS = 7          # crater radius of the classic shell
DMG_MAX = 20        # the damage of a burst at a tank's center (a direct hit): five destroy a tank
REACH = 10          # blast damage reaches tanks closer than this: the crater radius and a margin of 3
TANK_H = 2          # a tank stands 2 units tall on its column
KILL_BONUS = 25     # points for destroying another tank
MOVES = 4           # moves per tank per game, at most one a turn, before the shot
CLIMB = 4           # a step to the next column up or down by more than this is too steep to drive
STEP = [10, 14, 22, 32, 41]     # the path cost of a step to the next column, in tenths of a column, by the
#                                 height change d (0 .. CLIMB): round(10 * sqrt(1 + d * d))
TRJ_POINTS = 18     # trajectory samples sent at most
TRJ_PER_LINE = 6    # x y pairs per TRJ event line
ZCH_PER_LINE = 40   # terrain characters per ZCH event line
Z_PER_LINE = 60     # terrain characters per Z view line

# round(65536 * sin(d degrees)) for d = 0..90
SIN = [0, 1144, 2287, 3430, 4572, 5712, 6850, 7987, 9121, 10252, 11380, 12505, 13626, 14742, 15855, 16962,
       18064, 19161, 20252, 21336, 22415, 23486, 24550, 25607, 26656, 27697, 28729, 29753, 30767, 31772, 32768,
       33754, 34729, 35693, 36647, 37590, 38521, 39441, 40348, 41243, 42126, 42995, 43852, 44695, 45525, 46341,
       47143, 47930, 48703, 49461, 50203, 50931, 51643, 52339, 53020, 53684, 54332, 54963, 55578, 56175, 56756,
       57319, 57865, 58393, 58903, 59396, 59870, 60326, 60764, 61183, 61584, 61966, 62328, 62672, 62997, 63303,
       63589, 63856, 64104, 64332, 64540, 64729, 64898, 65048, 65177, 65287, 65376, 65446, 65496, 65526, 65536]


# ---- deterministic randomness ------------------------------------------------------

def rnd(st, n):
    """0 <= r < n from the state's generator: rng = (rng * 1103515245 + 12345) mod 2^31, r = (rng >> 16) mod n."""
    st["rng"] = (st["rng"] * 1103515245 + 12345) % 2147483648
    return (st["rng"] >> 16) % n


def isqrt(n):
    """Integer square root (floor)."""
    if n <= 0:
        return 0
    x = n
    y = (x + 1) // 2
    while y < x:
        x, y = y, (y + n // y) // 2
    return x


def enc(heights):
    return "".join(chr(0x30 + h) for h in heights)


def letter(seat):
    return "ABCDEFGH"[seat]


# ---- a new game ----------------------------------------------------------------------

def make_terrain(st, w):
    """Knots every 8 columns on a random walk, joined by an integer smoothstep, plus a little noise."""
    k = 8
    knots = [16 + rnd(st, 24)]
    for _ in range(w // k + 1):
        knots.append(min(48, max(6, knots[-1] + rnd(st, 25) - 12)))
    h = []
    for x in range(w):
        i, t = x // k, x % k
        s = t * t * (3 * k - 2 * t)                      # 0 .. 512
        v = knots[i] + (knots[i + 1] - knots[i]) * s // (k * k * k)
        h.append(min(50, max(1, v + rnd(st, 3) - 1)))
    return h


def place_tanks(st, n):
    """One tank per slot of w/n columns, jittered; seats shuffled over the slots; a flat pad under each."""
    w, h = st["w"], st["h"]
    jit = w // (4 * n)
    xs = []
    for i in range(n):
        x = (2 * i + 1) * w // (2 * n) + rnd(st, 2 * jit + 1) - jit
        xs.append(min(w - 3, max(2, x)))
    order = list(range(n))
    for i in range(n - 1, 0, -1):                        # Fisher-Yates
        j = rnd(st, i + 1)
        order[i], order[j] = order[j], order[i]
    tanks = [None] * n
    for slot, seat in enumerate(order):
        x = xs[slot]
        for c in (x - 1, x + 1):
            h[c] = h[x]
        tanks[seat] = [x, 100]
    return tanks


def parse_opts(opts):
    w, seed, windon = W_DEFAULT, None, True
    for k, v in (opts or {}).items():
        if k == "w":
            if not str(v).isdigit() or not W_MIN <= int(v) <= W_MAX:
                return None, "w is the arena width, %d to %d" % (W_MIN, W_MAX)
            w = int(v)
        elif k == "seed":
            if not str(v).isdigit():
                return None, "seed is a number"
            seed = int(v) % 2147483648
        elif k == "wind":
            if v not in ("0", "1"):
                return None, "wind is 0 or 1"
            windon = v == "1"
        else:
            return None, "unknown option %s (w, seed, wind)" % k
    return (w, seed, windon), None


def new_state(n, seed, w=W_DEFAULT, windon=True):
    st = {"w": w, "rng": seed % 2147483648, "turn": 0, "over": False, "shots": 0, "hit": None,
          "windon": windon, "wind": 0, "score": [0] * n, "moves": [MOVES] * n, "moved": False}
    st["h"] = make_terrain(st, w)
    st["tanks"] = place_tanks(st, n)
    st["aim"] = [[45 if x < w // 2 else 135, 50] for x, _ in st["tanks"]]
    if windon:
        st["wind"] = rnd(st, 11) - 5
    return st


# ---- one shot ------------------------------------------------------------------------

def launch(angle, power):
    """(vx, vy) in fixed point per step. Angle 0 points to higher columns, 90 straight up, 180 to lower."""
    speed = power * VK
    if angle <= 90:
        vx = speed * SIN[90 - angle] >> 16
        vy = speed * SIN[angle] >> 16
    else:
        vx = -(speed * SIN[angle - 90] >> 16)
        vy = speed * SIN[180 - angle] >> 16
    return vx, vy


def fly(st, seat, angle, power):
    """Run the shell. -> (path [(x, y) per step], impact (col, y) or None, column where it left)."""
    w, h = st["w"], st["h"]
    tx = st["tanks"][seat][0]
    x, y = tx * FP + FP // 2, (h[tx] + TANK_H) * FP
    vx, vy = launch(angle, power)
    ax = st["wind"] * WK
    cleared = False
    path = []
    for _ in range(MAX_STEPS):
        vx += ax
        vy -= GRAV
        x += vx
        y += vy
        if x < 0 or x >= w * FP:
            return path, None, (0 if x < 0 else w - 1)
        c = x >> 16
        path.append((c, y >> 16))
        if c != tx:
            cleared = True
        for s, (kx, hp) in enumerate(st["tanks"]):
            if kx == c and hp > 0 and (s != seat or cleared) and y < (h[c] + TANK_H) * FP:
                return path, (c, h[c] + 1), None
        if y < h[c] * FP:
            return path, (c, h[c]), None
    return path, None, x >> 16


def sample(path):
    """At most TRJ_POINTS points spread evenly over the flight (the last one included)."""
    n = len(path)
    if n <= TRJ_POINTS:
        pts = path
    else:
        pts = [path[(i + 1) * n // TRJ_POINTS - 1] for i in range(TRJ_POINTS)]
    return [(x, max(0, y)) for x, y in pts]


def crater(h, cx, cy, r):
    """Remove the disc of radius r round (cx, cy) from every column; dirt above the hole falls into it.
    -> the columns that changed."""
    changed = []
    for i in range(max(0, cx - r), min(len(h), cx + r + 1)):
        dy = isqrt(r * r - (i - cx) * (i - cx))
        lo, hi = max(0, cy - dy), min(h[i], cy + dy + 1)
        if hi > lo:
            h[i] -= hi - lo
            changed.append(i)
    return changed


def result_text(st):
    alive = [s for s, (_, hp) in enumerate(st["tanks"]) if hp > 0]
    pts = "-".join(str(p) for p in st["score"])
    if len(alive) == 1:
        return "win %d %s" % (alive[0], pts)
    return "draw %s" % pts


def next_seat(st, seat):
    n = len(st["tanks"])
    for i in range(1, n + 1):
        s = (seat + i) % n
        if st["tanks"][s][1] > 0:
            return s
    return seat


def shoot(st, seat, angle, power):
    """Apply one shot to a copy of the state. -> (state, events, end result or None)."""
    st = json.loads(json.dumps(st))
    h, tanks = st["h"], st["tanks"]
    st["aim"][seat] = [angle, power]
    st["shots"] += 1
    ev = ["FIRE %d %d %d" % (seat, angle, power)]
    path, impact, out = fly(st, seat, angle, power)
    pts = sample(path)
    for i in range(0, len(pts), TRJ_PER_LINE):
        ev.append("TRJ " + " ".join("%d %d" % p for p in pts[i:i + TRJ_PER_LINE]))
    hp0 = [hp for _, hp in tanks]
    if impact is None:
        ev.append("OUT %d" % out)
        st["hit"] = None
    else:
        cx, cy = impact
        st["hit"] = [cx, cy]
        ev.append("HIT %d %d %d" % (cx, cy, RADIUS))
        ground = [h[x] for x, _ in tanks]
        for s, (x, hp) in enumerate(tanks):             # blast damage, by distance from the tank's center
            if hp <= 0:
                continue
            d = isqrt((x - cx) ** 2 + (ground[s] + 1 - cy) ** 2)
            if d < REACH:
                tanks[s][1] = max(0, hp - DMG_MAX * (REACH - d) // REACH)
                ev.append("DMG %d %d" % (s, tanks[s][1]))
        changed = crater(h, cx, cy, RADIUS)
        if changed:
            lo, hi = changed[0], changed[-1]
            for i in range(lo, hi + 1, ZCH_PER_LINE):
                ev.append("ZCH %d %s" % (i, enc(h[i:min(hi + 1, i + ZCH_PER_LINE)])))
        for s, (x, hp) in enumerate(tanks):             # tanks fall with the ground under them, unharmed
            if hp > 0 and h[x] < ground[s]:
                ev.append("FALL %d %d %d" % (s, x, hp))
    killed = 0
    for s, (_, hp) in enumerate(tanks):
        if s != seat:
            st["score"][seat] += hp0[s] - hp
        if hp0[s] > 0 and hp <= 0:
            ev.append("DIE %d" % s)
            if s != seat:
                killed += 1
    st["score"][seat] += KILL_BONUS * killed
    if sum(1 for _, hp in tanks if hp > 0) <= 1:
        st["over"] = True
        return st, ev, result_text(st)
    if st["windon"]:
        wind = min(9, max(-9, st["wind"] + rnd(st, 5) - 2))
        if wind != st["wind"]:
            st["wind"] = wind
            ev.append("N %d" % wind)
    st["turn"] = next_seat(st, seat)
    st["moved"] = False
    ev.append("TURN %d" % st["turn"])
    return st, ev, None


# ---- one move ------------------------------------------------------------------------

def moves_left(st):
    return st.get("moves") or [MOVES] * len(st["tanks"])


def budget(st):
    """The path one move may use, in tenths of a column: 10% of the arena width (w tenths)."""
    return st["w"]


def walk(st, seat, d, limit):
    """Drive tank seat one column at a time towards d (-1 or +1), each step costing STEP[height change],
    while the path used stays within limit (tenths of a column). -> (column reached, path used, what
    stopped it: "edge", "tank", "steep" or "far")."""
    w, h = st["w"], st["h"]
    x = st["tanks"][seat][0]
    taken = {tx for s, (tx, _) in enumerate(st["tanks"]) if s != seat}     # living tanks and wrecks
    used = 0
    while True:
        nx = x + d
        if not 0 <= nx < w:
            return x, used, "edge"
        if nx in taken:
            return x, used, "tank"
        dh = abs(h[nx] - h[x])
        if dh > CLIMB:
            return x, used, "steep"
        if used + STEP[dh] > limit:
            return x, used, "far"
        x, used = nx, used + STEP[dh]


STOPS = {"edge": "the arena edge is there", "tank": "another tank is in the way",
         "steep": "the ground is too steep", "far": "that is not enough path for one column"}


def drive(st, seat, d, n):
    """One move of up to n columns of path towards d (-1 or +1). -> (state, events) or an error text.
    The turn stays with the seat: it fires next."""
    if st.get("moved"):
        return "one move a turn, and you have moved: now fire"
    left = moves_left(st)
    if left[seat] <= 0:
        return "no moves left this game: fire"
    x0 = st["tanks"][seat][0]
    x, used, why = walk(st, seat, d, min(10 * n, budget(st)))
    if x == x0:
        return "the tank cannot move %s: %s" % ("left" if d < 0 else "right", STOPS[why])
    st = json.loads(json.dumps(st))
    st["tanks"][seat][0] = x
    st["moves"] = list(left)
    st["moves"][seat] -= 1
    st["moved"] = True
    return st, ["MOVE %d %d %d %d %d" % (seat, x0, x, used, st["moves"][seat])]


# ---- the plugin operations -------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    n = req.get("players")
    if not isinstance(n, int) or not 2 <= n <= 8:
        return {"error": "two to eight players"}
    opts, err = parse_opts(req.get("options"))
    if err:
        return {"error": err}
    w, seed, windon = opts
    if w < 8 * n:
        return {"error": "the arena needs 8 columns per player (w=%d or more)" % (8 * n)}
    st = new_state(n, seed if seed is not None else int(req.get("seed") or 0), w, windon)
    return {"state": st, "events": ["N %d" % st["wind"], "TURN 0"]}


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    w, h = st["w"], st["h"]
    wire = ["Z %d %s" % (i, enc(h[i:i + Z_PER_LINE])) for i in range(0, w, Z_PER_LINE)]
    wire += ["K %d %d %d" % (s, x, hp) for s, (x, hp) in enumerate(st["tanks"])]
    wire += ["A %d %d %d" % (s, a, p) for s, (a, p) in enumerate(st["aim"])]
    left = moves_left(st)
    wire += ["L %d %d" % (s, m) for s, m in enumerate(left)]
    wire.append("N %d" % st["wind"])
    if st.get("hit"):
        wire.append("H %d %d" % tuple(st["hit"]))
    wire.append("W %d" % (-1 if st["over"] else st["turn"]))
    wire.append("B %d %d" % (budget(st), 1 if st.get("moved") else 0))
    wire += ["T %d %d" % (s, p) for s, p in enumerate(st["score"])]
    gone = st.get("gone") or []
    if any(gone):
        wire.append("G " + " ".join("1" if g else "0" for g in gone))
    if st["over"]:
        r = result_text(st).split()
        status = ("Game over: tank %s wins" % letter(int(r[1]))) if r[0] == "win" else "Game over: no tank left"
    elif seat == st["turn"]:
        status = "Your shot"
    elif seat < 0:
        status = "Tank %s to fire" % letter(st["turn"])
    else:
        status = "Waiting for tank %s to fire" % letter(st["turn"])
    return {"wire": wire, "geometry": ["ARN %d %d" % (w, HMAX)], "status": status,
            "data": {"w": w, "h": h, "tanks": st["tanks"], "aim": st["aim"], "wind": st["wind"],
                     "turn": st["turn"], "score": st["score"], "hit": st.get("hit"), "gone": gone,
                     "moves": left, "moved": bool(st.get("moved")), "budget": budget(st)}}


def op_moves(req):
    return {"check": True}


def parse_drive(move):
    """['MOVE', 'L'|'R', n] or ['MOVE', '-n'|'+n'] -> (direction -1 or +1, n) or an error text."""
    words = [str(m) for m in move[1:]]
    if len(words) == 2 and words[0].upper() in ("L", "R"):
        d, n = (-1 if words[0].upper() == "L" else 1), words[1]
    elif len(words) == 1 and words[0][:1] in ("-", "+"):
        d, n = (-1 if words[0][0] == "-" else 1), words[0][1:]
    else:
        return "a move is MOVE L n or MOVE R n (or MOVE -n, MOVE +n): n columns of path"
    if not (n.isdigit() and len(n) <= 3 and int(n) > 0):
        return "the distance is a whole number of columns, 1 or more"
    return d, int(n)


def parse_move(move):
    """['FIRE', angle, power] -> (angle, power) or an error text."""
    if len(move) != 3 or str(move[0]).upper() != "FIRE":
        return "a shot is FIRE angle power, like FIRE 45 60; a move is MOVE L n or MOVE R n"
    a, p = str(move[1]), str(move[2])
    if not (a.isdigit() and p.isdigit() and len(a) <= 3 and len(p) <= 3):
        return "angle and power are whole numbers"
    a, p = int(a), int(p)
    if a > 180:
        return "the angle is 0 to 180 (90 is straight up)"
    if p > 100:
        return "the power is 0 to 100"
    return a, p


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if move and str(move[0]).upper() == "MOVE":
        how = parse_drive(move)
        if isinstance(how, str):
            return {"error": how}
        got = drive(st, seat, *how)
        if isinstance(got, str):
            return {"error": got}
        st2, events = got
        f = events[0].split()
        if req.get("check"):
            return {"info": "from %s to %s used %s of %d" % (f[2], f[3], f[4], budget(st))}
        return {"state": st2, "events": events}
    shot = parse_move(move)
    if isinstance(shot, str):
        return {"error": shot}
    if req.get("check"):
        return {"info": "angle %d power %d" % shot}
    standing = [s for s, (_, hp) in enumerate(st["tanks"]) if hp > 0]
    st, events, end = shoot(st, seat, *shot)
    out = {"state": st, "events": events}
    if end:
        out["end"] = end
        if end.startswith("draw"):      # the last tanks destroyed each other: they share the draw (scores)
            out["tied"] = standing
    return out


def op_result(req):
    st = req["state"]
    if not st["over"]:
        return {"over": False}
    return {"over": True, "scores": st["score"]}


def op_drop(req):
    """A seat leaves a running game (it resigned or ran out of time) and the others fire on. Its tank becomes
    a wreck: GONE seat why, then DIE seat if it was still standing, then TURN for the next living tank if it
    was that seat's turn. Points it scored stay. With one tank left, that seat wins (with none, a draw)."""
    st = json.loads(json.dumps(req["state"]))
    seat, why = req.get("seat", -1), str(req.get("why") or "resign")
    if st["over"]:
        return {"error": "the game is over"}
    tanks = st["tanks"]
    gone = st.setdefault("gone", [False] * len(tanks))
    if not isinstance(seat, int) or not 0 <= seat < len(tanks) or gone[seat]:
        return {"error": "that seat is not playing"}
    gone[seat] = True
    events = ["GONE %d %s" % (seat, why if why in ("resign", "timeout") else "resign")]
    out = {"state": st, "events": events}
    if tanks[seat][1] > 0:
        tanks[seat][1] = 0
        events.append("DIE %d" % seat)
    if sum(1 for _, hp in tanks if hp > 0) <= 1:
        st["over"] = True
        out["end"] = result_text(st)
        return out
    if seat == st["turn"]:
        st["turn"] = next_seat(st, seat)
        st["moved"] = False
        events.append("TURN %d" % st["turn"])
    return out

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "fired at angle 45, power 60", "moved 3 columns left"."""
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != str(req.get("seat")):
            continue
        if p[0] == "FIRE" and len(p) >= 4:
            return {"say": "fired at angle %s, power %s" % (p[2], p[3])}
        if p[0] == "MOVE" and len(p) >= 4:
            try:
                n = int(p[3]) - int(p[2])
            except ValueError:
                return {"say": "moved"}
            return {"say": "moved %d column%s %s" % (abs(n), "" if abs(n) == 1 else "s", "left" if n < 0 else "right")}
    return {"say": ""}


# ---- a typed command line (the core's typed request; docs/plugins.md) ----------------------------------

INFO["typed"] = True
DRIVE_WAYS = {"l": "L", "left": "L", "r": "R", "right": "R"}
DRIVE_HINT = "Type /drive left or /drive right, and the columns to drive if you like, as in /drive left 5."
FIRE_HINT = "Type /fire with an angle and a power, as in /fire 45 60."


def short_number(word):
    """True for a whole number of one to three digits."""
    return word.isascii() and word.isdigit() and len(word) <= 3


def op_typed(req):
    """The words typed after a slash at the table -> what the core does with them. /fire 45 60, or the
    two numbers alone, fires at that angle and power. /drive left 5 or /drive right 5 drives that many
    columns, /drive left alone as far as one move goes, and /move is the same as /drive."""
    st, seat = req["state"], req.get("seat", -1)
    words = [w.lower() for w in req.get("words") or []]
    if not words:
        return {}
    if words[0] in ("drive", "move"):
        rest = words[1:]
        if len(rest) == 1 and rest[0][:1] in ("-", "+") and short_number(rest[0][1:]):
            way = "L" if rest[0][0] == "-" else "R"
            reply = {"move": ["MOVE", rest[0][0] + str(int(rest[0][1:]))]}
        elif rest and rest[0] in DRIVE_WAYS and (len(rest) == 1 or (len(rest) == 2 and short_number(rest[1]))):
            way = DRIVE_WAYS[rest[0]]
            columns = int(rest[1]) if len(rest) == 2 else max(1, (budget(st) + 9) // 10)
            reply = {"move": ["MOVE", way, str(columns)]}
        else:
            return {"error": DRIVE_HINT}
        reply["what"] = "drive left" if way == "L" else "drive right"
    else:
        fire = words[0] == "fire"
        rest = words[1:] if fire else words
        if len(rest) == 2 and all(short_number(w) for w in rest):
            reply = {"move": ["FIRE", str(int(rest[0])), str(int(rest[1]))], "what": "fire"}
        elif fire:
            return {"error": FIRE_HINT}
        else:
            return {}
    if seat != st["turn"]:
        return {"error": "It's not your turn."}
    return reply


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "drop": op_drop, "say": op_say, "typed": op_typed}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            reply = OPS[req["op"]](req)
        except Exception as e:  # a bad request must not kill the plugin
            reply = {"error": "plugin: %s" % e}
        sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
