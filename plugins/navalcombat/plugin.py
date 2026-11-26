#!/usr/bin/env python3
"""navalcombat/1 game plugin (docs/types/navalcombat-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

Two players each place a fleet on their own 10 by 10 grid, then take turns
firing one shot at the other's grid. The state holds both fleets; a view
shows the viewer's own fleet and, of the other fleet, only the squares that
have been shot at (and ships once they are sunk). Events never name a square
of a ship that has not been hit.

State: {"rules": "classic" | "batnav" | "salvo", "n": 10, "fleet": [lengths in placement order],
        "phase": "place" | "shoot" | "over", "ships": [[[x, y, across (0/1), length], ...] per seat],
        "ready": [bool, bool], "shots": [n*n chars per seat: the shots at that seat's waters,
        '.' none, 'o' miss, 'x' hit], "turn": seat to shoot, "last": [seat, square] or None,
        "seed": int, "draws": [random placements made, per seat], "winner": seat or -1,
        "fired": shots fired so far in this turn's salvo (salvo only; absent in older states)}
Squares are a column letter and a row number from the top: a1 .. j10.

The salvo ruleset is GameMaster's BattleShips: the classic fleet, six shots
a turn whatever is left afloat, and each seat's view carries the other fleet once both are ready (OF
lines), because GameMaster's rulebook works out the hits on the other grid itself.
"""
import json
import re
import random
import sys

N = 10
FLEETS = {
    "classic": [5, 4, 3, 3, 2],                     # ships may touch
    "batnav": [4, 3, 3, 2, 2, 2, 1, 1, 1, 1],       # ships may not touch, even diagonally
    "salvo": [5, 4, 3, 3, 2],                       # as classic, with six shots a turn
}
NO_TOUCH = {"classic": False, "batnav": True, "salvo": False}
SALVO = {"salvo": 6}                                # shots a turn (one in the other rulesets)
WORDS = ("random", "undo", "clear", "ready")
# The core passes the table's ruleset as "rules" in every call; new() keeps it in the state.
INFO = {
    "type": "navalcombat", "ver": 1, "name": "Naval Combat",
    "rules": ["classic", "batnav", "salvo"], "min": 2, "max": 2, "flags": [],
    "options": [],
}


def sq_name(x, y):
    return "%s%d" % (chr(ord("a") + x), y + 1)


def parse_sq(tok, n=N):
    tok = tok.lower()
    if len(tok) < 2 or not tok[0].isalpha() or not tok[1:].isdigit():
        return None
    x, y = ord(tok[0]) - ord("a"), int(tok[1:]) - 1
    return (x, y) if 0 <= x < n and 0 <= y < n else None


def ship_cells(ship):
    x, y, across, length = ship
    return [(x + i, y) if across else (x, y + i) for i in range(length)]


def ship_token(ship):
    cells = ship_cells(ship)
    return "%s-%s" % (sq_name(*cells[0]), sq_name(*cells[-1]))


def parse_place(tok, n=N):
    """'a1-e1' (either end first) -> (x, y, across, length) with x, y the top or left end, or None."""
    parts = tok.lower().split("-")
    if len(parts) != 2:
        return None
    a, b = parse_sq(parts[0], n), parse_sq(parts[1], n)
    if a is None or b is None or (a[0] != b[0] and a[1] != b[1]):
        return None
    (x0, y0), (x1, y1) = sorted([a, b], key=lambda p: (p[1], p[0]))
    if x0 > x1:
        x0, x1 = x1, x0
    across = 1 if y0 == y1 else 0       # a one-square ship counts as across
    return (x0, y0, across, max(x1 - x0, y1 - y0) + 1)


def fits(ships, ship, n, no_touch):
    """Can ship go on the grid with the ships already there?"""
    taken = set()
    for s in ships:
        for cx, cy in ship_cells(s):
            taken.add((cx, cy))
            if no_touch:
                taken.update((cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    for cx, cy in ship_cells(ship):
        if not (0 <= cx < n and 0 <= cy < n) or (cx, cy) in taken:
            return False
    return True


def placements(st, seat):
    """Every legal place for the seat's next ship (empty when the fleet is complete)."""
    ships = st["ships"][seat]
    if len(ships) >= len(st["fleet"]):
        return []
    length, n, nt = st["fleet"][len(ships)], st["n"], NO_TOUCH[st["rules"]]
    out = []
    for y in range(n):
        for x in range(n):
            for across in ((1,) if length == 1 else (1, 0)):
                s = (x, y, across, length)
                if fits(ships, s, n, nt):
                    out.append(s)
    return out


def random_fleet(st, seat, keep):
    """Place the ships after the first `keep` at random; start the whole fleet again when they do not fit."""
    rng = random.Random(st["seed"] * 7919 + seat * 104729 + st["draws"][seat])
    n, nt, fleet = st["n"], NO_TOUCH[st["rules"]], st["fleet"]
    base = [list(s) for s in st["ships"][seat][:keep]]
    for attempt in range(400):
        ships = [list(s) for s in base] if attempt < 50 else []
        for length in fleet[len(ships):]:
            for _ in range(200):
                across = rng.randrange(2) if length > 1 else 1
                x = rng.randrange(n - (length - 1 if across else 0))
                y = rng.randrange(n - (0 if across else length - 1))
                if fits(ships, (x, y, across, length), n, nt):
                    ships.append([x, y, across, length])
                    break
            else:
                break
        if len(ships) == len(fleet):
            return ships
    raise RuntimeError("no room for the fleet")


def owner_at(st, seat):
    """{(x, y): ship index} for seat's fleet."""
    return {c: i for i, s in enumerate(st["ships"][seat]) for c in ship_cells(s)}


def is_sunk(st, seat, ship):
    shots, n = st["shots"][seat], st["n"]
    return all(shots[cy * n + cx] == "x" for cx, cy in ship_cells(ship))


def afloat(st, seat):
    return [s[3] for s in st["ships"][seat] if not is_sunk(st, seat, s)]


def hits(st, seat):
    """Hits landed by seat (on the other seat's fleet)."""
    return st["shots"][1 - seat].count("x")


def clock_seat(st):
    """During placement, the first seat whose fleet is not ready (that seat is on the clock)."""
    return 0 if not st["ready"][0] else 1


# ---- the plugin operations ----------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    opts = req.get("options") or {}
    rules = req.get("rules") or "classic"
    if rules not in FLEETS:
        return {"error": "unknown ruleset"}
    if opts:
        return {"error": "no options for this game"}
    if req.get("players") != 2:
        return {"error": "two players"}
    st = {"rules": rules, "n": N, "fleet": list(FLEETS[rules]), "phase": "place",
          "ships": [[], []], "ready": [False, False], "shots": ["." * (N * N), "." * (N * N)],
          "turn": 0, "last": None, "seed": int(req.get("seed") or 0), "draws": [0, 0], "winner": -1}
    if rules in SALVO:
        st["fired"] = 0
    return {"state": st, "events": ["TURN 0"]}


def salvo_size(st):
    return SALVO.get(st["rules"], 1)


def shots_left(st):
    """Shots the seat to move has left in this turn."""
    return salvo_size(st) - st.get("fired", 0)


def board_rows(st, seat, viewer):
    """Seat's waters as the viewer may see them: a list of n strings. '.' nothing known (or open water),
    'o' a miss, 'x' a hit, '#' a square of a sunk ship, and only on the viewer's own waters 's' a ship
    square not hit. Ships of another seat are never shown until every square of them is hit."""
    n, shots = st["n"], st["shots"][seat]
    own = owner_at(st, seat)
    sunk = {i for i, s in enumerate(st["ships"][seat]) if is_sunk(st, seat, s)}
    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            shot, ship = shots[y * n + x], own.get((x, y))
            if ship is not None and ship in sunk:
                row.append("#")
            elif shot in "ox":
                row.append(shot)
            elif viewer == seat and ship is not None:
                row.append("s")
            else:
                row.append(".")
        rows.append("".join(row))
    return rows


def view_turn(st, seat):
    if st["phase"] == "over":
        return -1
    if st["phase"] == "place":
        return seat if seat in (0, 1) and not st["ready"][seat] else clock_seat(st)
    return st["turn"]


def status_text(st, seat):
    if st["phase"] == "over":
        w = st["winner"]
        if seat < 0:
            return "Game over: seat %d sank the whole fleet" % w
        return "Game over: %s" % ("you sank the whole fleet" if seat == w else "your fleet is sunk")
    if st["phase"] == "place":
        if seat < 0:
            return "The players are placing their fleets"
        if st["ready"][seat]:
            return "Your fleet is ready; waiting for the other fleet"
        k = len(st["ships"][seat])
        if k == len(st["fleet"]):
            return "Fleet placed: ready when you are"
        return "Place your fleet: the %d-square ship next (%d of %d)" % (st["fleet"][k], k + 1, len(st["fleet"]))
    if seat == st["turn"]:
        if salvo_size(st) > 1:
            left = shots_left(st)
            return "Your salvo: %d shot%s left of %d" % (left, "" if left == 1 else "s", salvo_size(st))
        return "Your shot"
    if seat < 0:
        return "Seat %d to shoot" % st["turn"]
    if salvo_size(st) > 1:
        return "Waiting for the other player's salvo"
    return "Waiting for the other player's shot"


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    seat = seat if seat in (0, 1) else -1
    turn = view_turn(st, seat)
    wire = ["Q %s" % st["phase"], "W %s" % ("-" if turn < 0 else turn),
            "RD %d %d" % (int(st["ready"][0]), int(st["ready"][1]))]
    boards = {}
    for s in (0, 1):
        boards[s] = board_rows(st, s, seat)
        wire += ["B %d %d %s" % (s, y + 1, r) for y, r in enumerate(boards[s]) if r != "." * st["n"]]
    if seat >= 0:
        wire += ["Y %d %s" % (s[3], ship_token(s)) for s in st["ships"][seat]]
        k = len(st["ships"][seat])
        if st["phase"] == "place" and not st["ready"][seat] and k < len(st["fleet"]):
            wire.append("N %d" % st["fleet"][k])
        if st["rules"] in SALVO and all(st["ready"]):     # salvo: the other fleet, for the player's program
            wire += ["OF %d %s" % (s[3], ship_token(s)) for s in st["ships"][1 - seat]]
    sunk = {}
    for s in (0, 1):
        sunk[s] = [ship_token(sh) for sh in st["ships"][s] if is_sunk(st, s, sh)]
        wire += ["K %d %d %s" % (s, sh[3], ship_token(sh)) for sh in st["ships"][s] if is_sunk(st, s, sh)]
    left = [len(afloat(st, s)) if st["ready"][s] else len(st["fleet"]) for s in (0, 1)]
    wire.append("L %d %d" % tuple(left))
    if st["last"]:
        wire.append("LS %d %s" % tuple(st["last"]))
    if st["rules"] in SALVO and st["phase"] == "shoot":
        wire.append("SL %d" % shots_left(st))
    # data: the same filtered view as JSON; never the state itself (nor the other fleet)
    data = {"phase": st["phase"], "turn": turn, "ready": list(st["ready"]), "boards": [boards[0], boards[1]],
            "sunk": [sunk[0], sunk[1]], "afloat": left, "fleet": list(st["fleet"]), "rules": st["rules"]}
    if seat >= 0:
        data["mine"] = [ship_token(s) for s in st["ships"][seat]]
    return {"wire": wire, "geometry": ["BRD %d %d" % (st["n"], st["n"]), "FL " + " ".join(map(str, st["fleet"]))],
            "status": status_text(st, seat), "data": data}


def legal_moves(st, seat):
    if seat not in (0, 1) or st["phase"] == "over":
        return []
    if st["phase"] == "place":
        if st["ready"][seat]:
            return []
        out = [ship_token(s) for s in placements(st, seat)]
        out.append("random")
        if st["ships"][seat]:
            out += ["undo", "clear"]
        if len(st["ships"][seat]) == len(st["fleet"]):
            out.append("ready")
        return out
    if seat != st["turn"]:
        return []
    n, shots = st["n"], st["shots"][1 - seat]
    return [sq_name(x, y) for y in range(n) for x in range(n) if shots[y * n + x] == "."]


def op_moves(req):
    return {"moves": legal_moves(req["state"], req.get("seat", -1))}


def place(st, seat, tok, check):
    ships, fleet = st["ships"][seat], st["fleet"]
    if st["ready"][seat]:
        return {"error": "your fleet is ready and can no longer change; waiting for the other fleet"}
    word = tok.lower()
    if word in WORDS:
        if word == "ready" and len(ships) < len(fleet):
            return {"error": "place every ship first (%d of %d placed)" % (len(ships), len(fleet))}
        if word in ("undo", "clear") and not ships:
            return {"error": "no ship placed yet"}
        if check:
            return {"info": ""}
        st = json.loads(json.dumps(st))
        if word == "random":
            keep = len(ships) if len(ships) < len(fleet) else 0
            st["ships"][seat] = random_fleet(st, seat, keep)
            st["draws"][seat] += 1
        elif word == "undo":
            st["ships"][seat].pop()
        elif word == "clear":
            st["ships"][seat] = []
        else:
            return ready(st, seat)
        return {"state": st, "events": []}
    if len(ships) >= len(fleet):
        return {"error": "every ship is placed: ready, undo, clear or random"}
    ship = parse_place(word, st["n"])
    if ship is None:
        return {"error": "a ship is placed from one end to the other, like a1-e1"}
    length = fleet[len(ships)]
    if ship[3] != length:
        return {"error": "the next ship is %d square%s long" % (length, "" if length == 1 else "s")}
    if not fits(ships, ship, st["n"], NO_TOUCH[st["rules"]]):
        if NO_TOUCH[st["rules"]]:
            return {"error": "that overlaps or touches a ship (ships may not touch, even at a corner)"}
        return {"error": "that overlaps a ship"}
    if check:
        return {"info": ""}
    st = json.loads(json.dumps(st))
    st["ships"][seat].append(list(ship))
    return {"state": st, "events": []}


def ready(st, seat):
    st["ready"][seat] = True
    events = ["READY %d" % seat]
    if all(st["ready"]):
        st["phase"], st["turn"] = "shoot", 0
        events.append("TURN 0")
    elif seat == 0:
        events.append("TURN 1")         # seat 1 is still placing: its clock runs
    return {"state": st, "events": events}


def shoot(st, seat, tok, check):
    if seat != st["turn"]:
        return {"error": "not your turn"}
    pos = parse_sq(tok, st["n"])
    if pos is None:
        return {"error": "a shot is one square, like e5"}
    n, other = st["n"], 1 - seat
    if st["shots"][other][pos[1] * n + pos[0]] != ".":
        return {"error": "you have already fired at %s" % sq_name(*pos)}
    if check:
        return {"info": ""}             # never says hit or miss
    st = json.loads(json.dumps(st))
    own = owner_at(st, other)
    i = pos[1] * n + pos[0]
    ship = own.get(pos)
    st["shots"][other] = st["shots"][other][:i] + ("o" if ship is None else "x") + st["shots"][other][i + 1:]
    st["last"] = [seat, sq_name(*pos)]
    if ship is None:
        ev = "SHOT %d %s miss" % (seat, sq_name(*pos))
    elif is_sunk(st, other, st["ships"][other][ship]):
        sh = st["ships"][other][ship]
        ev = "SHOT %d %s sunk %d %s" % (seat, sq_name(*pos), sh[3], ship_token(sh))
    else:
        ev = "SHOT %d %s hit" % (seat, sq_name(*pos))
    out = {"state": st, "events": [ev]}
    if not afloat(st, other):
        st["phase"], st["winner"] = "over", seat
        out["end"] = result_text(st)
        return out
    if "fired" in st:                   # salvo: the same seat shoots again until its salvo is fired
        st["fired"] += 1
        if st["fired"] < salvo_size(st) and "." in st["shots"][other]:
            out["events"].append("TURN %d" % seat)
            return out
        st["fired"] = 0
    st["turn"] = other
    out["events"].append("TURN %d" % other)
    return out


def result_text(st):
    return "win %d %d-%d" % (st["winner"], hits(st, 0), hits(st, 1))


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["phase"] == "over":
        return {"error": "the game is over"}
    if seat not in (0, 1):
        return {"error": "not a player"}
    if len(move) != 1:
        return {"error": "one move at a time"}
    if st["phase"] == "place":
        return place(st, seat, move[0], req.get("check"))
    return shoot(st, seat, move[0], req.get("check"))


def op_result(req):
    st = req["state"]
    if st["phase"] != "over":
        return {"over": False}
    return {"over": True, "scores": [1 if st["winner"] == s else 0 for s in (0, 1)]}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "fired at b4: a hit", "readied the fleet"."""
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != str(req.get("seat")):
            continue
        if p[0] == "READY":
            return {"say": "readied the fleet"}
        if p[0] == "SHOT" and len(p) >= 4:
            what = {"hit": "a hit", "miss": "a miss"}.get(p[3])
            if p[3] == "sunk":
                what = "sank a %s-square ship" % p[4] if len(p) > 4 else "sank a ship"
            return {"say": "fired at %s: %s" % (p[2], what or p[3])}
    return {"say": ""}


# ---- a typed command line (the core's typed request; docs/plugins.md) ----------------------------------

INFO["typed"] = True
TYPED_SQUARE_RE = re.compile(r"^[a-z][0-9]{1,2}\Z")
TYPED_PLACE_RE = re.compile(r"^[a-z][0-9]{1,2}-[a-z][0-9]{1,2}\Z")
ACROSS_WORDS = ("across", "a", "right", "h", "horizontal")
DOWN_WORDS = ("down", "d", "v", "vertical")
TYPED_WORDS = {"random": ("random", "place your ships at random"), "ready": ("ready", "ready your fleet"),
               "undo": ("undo", "take back the last ship"), "reset": ("clear", "take back every ship"),
               "clear": ("clear", "take back every ship")}


def typed_placement(st, seat, words):
    """A start square and a direction (across when none is typed) -> the reply that places the next ship
    of the fleet there, or {} when the direction is not one."""
    across = True
    if len(words) == 2:
        if words[1] in DOWN_WORDS:
            across = False
        elif words[1] not in ACROSS_WORDS:
            return {}
    pos = parse_sq(words[0], st["n"])
    if pos is None:
        return {"error": "There is no square %s on the grid." % words[0]}
    ships, fleet = st["ships"][seat], st["fleet"]
    if st["ready"][seat]:
        return {"error": "Your fleet is ready, so it can no longer change."}
    if len(ships) >= len(fleet):
        return {"error": "Every ship is placed. Type /ready when your fleet is ready, or /undo to move the last one."}
    length = fleet[len(ships)]
    x, y = pos
    ex, ey = (x + length - 1, y) if across or length == 1 else (x, y + length - 1)
    if ex >= st["n"] or ey >= st["n"]:
        return {"error": "From %s %s, the next ship runs off the grid because it is %d squares long." % (
            words[0], "across" if across else "down", length)}
    token = "%s-%s" % (sq_name(x, y), sq_name(ex, ey))
    return {"move": [token], "what": "place a ship at %s" % token}


def op_typed(req):
    """The words typed after a slash at the table -> what the core does with them. While the fleets are
    placed, /a1 across or /a1 down (across when no direction is typed) places the next ship from a1, and
    /a1-e1 places it from end to end. /random, /ready and /undo do what they say, and /reset or /clear
    takes back every ship. Placing goes on at the same time for both players. While shooting, a square
    such as /e5 fires there."""
    st, seat = req["state"], req.get("seat", -1)
    words = [w.lower() for w in req.get("words") or []]
    if not words:
        return {}
    placing = st["phase"] == "place"
    if len(words) == 1 and words[0] in TYPED_WORDS:
        token, what = TYPED_WORDS[words[0]]
        reply = {"move": [token], "what": what}
    elif len(words) == 1 and TYPED_PLACE_RE.match(words[0]):
        reply = {"move": [words[0]], "what": "place a ship at %s" % words[0]}
    elif len(words) <= 2 and TYPED_SQUARE_RE.match(words[0]):
        if placing:
            return typed_placement(st, seat, words)
        if len(words) != 1:
            return {}
        reply = {"move": [words[0]], "what": "fire at %s" % words[0]}
    else:
        return {}
    if not placing and seat != st["turn"]:
        return {"error": "It's not your turn."}
    return reply


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "say": op_say, "typed": op_typed}


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
