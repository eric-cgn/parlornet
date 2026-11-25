#!/usr/bin/env python3
"""mancala/1 game plugin (docs/types/mancala-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

Two rulesets on one board of two rows of six pits and two stores:
  kalah  store rules: sowing passes the mover's own store, a last seed in it
         earns another turn, a last seed in an empty own pit captures the pit
         opposite; the game ends when a row is empty.
  oware  abapa: the stores only hold captures, a sowing of 12 or more skips its
         own pit, 2s and 3s on the opponent's row are captured backwards, a
         grand slam captures nothing, a player must feed an empty opponent;
         25 seeds wins.

State: {"rules": "kalah"|"oware", "seeds": seeds per pit at the start,
        "b": 14 counts (seat 0's pits 1-6, seat 0's store, seat 1's pits 1-6,
        seat 1's store: the sowing order), "turn": seat to move, "over": bool,
        "last": [seat, pit] or None, "seen": oware positions since the last
        capture (to end endless cycles)}
Seat 0 moves first. A move is a pit number, 1 to 6, counted from the mover's
left along the mover's own row.
"""
import json
import sys

PITS = 6
STORE = (PITS, 2 * PITS + 1)            # 6 and 13
SIZE = 2 * PITS + 2                     # 14 places in sowing order
RULES = ("kalah", "oware")
WIN_OWARE = 25                          # more than half of 48
NO_CAPTURE_LIMIT = 200                  # oware: moves in a row without a capture end the game
INFO = {
    "type": "mancala", "ver": 1, "name": "Mancala",
    "rules": list(RULES), "min": 2, "max": 2, "flags": [],
    "options": ["seeds", "rules"],
}


def pit_index(seat, pit):
    """A seat's pit number (1-6) -> its place in the board list."""
    return seat * (PITS + 1) + pit - 1


def own_row(seat):
    return range(seat * (PITS + 1), seat * (PITS + 1) + PITS)


def owner(i):
    """The seat whose row place i is on (stores count as their owner's)."""
    return 0 if i <= STORE[0] else 1


def row_sum(b, seat):
    return sum(b[i] for i in own_row(seat))


def opposite(i):
    return 2 * PITS - i


def sow(st, seat, pit):
    """Play a pit (assumed non-empty) -> (board, events, captured, again, grand). Pure."""
    b = list(st["b"])
    start = pit_index(seat, pit)
    n = b[start]
    b[start] = 0
    pos = start
    oware = st["rules"] == "oware"
    left = n
    while left:
        pos = (pos + 1) % SIZE
        if oware and (pos in STORE or pos == start):
            continue                    # stores take no seeds; a lap skips the pit it started from
        if not oware and pos == STORE[1 - seat]:
            continue                    # the opponent's store is skipped
        b[pos] += 1
        left -= 1
    captured, again, grand = 0, False, False
    if oware:
        if owner(pos) != seat and pos not in STORE and b[pos] in (2, 3):
            take = []
            i = pos
            while owner(i) != seat and i not in STORE and b[i] in (2, 3):
                take.append(i)
                i -= 1
            total = sum(b[i] for i in take)
            if total == row_sum(b, 1 - seat):
                grand = True            # a grand slam would leave the opponent nothing: no capture
            else:
                for i in take:
                    b[i] = 0
                b[STORE[seat]] += total
                captured = total
    else:
        if pos == STORE[seat]:
            again = True
        elif pos in own_row(seat) and b[pos] == 1 and b[opposite(pos)] > 0:
            captured = b[pos] + b[opposite(pos)]
            b[pos] = b[opposite(pos)] = 0
            b[STORE[seat]] += captured
    return b, n, captured, again, grand


def legal(st, seat):
    """Pit numbers seat may play (empty when the game is over or seat cannot move)."""
    if st["over"]:
        return []
    b = st["b"]
    pits = [p for p in range(1, PITS + 1) if b[pit_index(seat, p)] > 0]
    if st["rules"] == "oware" and row_sum(b, 1 - seat) == 0:
        # must feed: only moves that leave the opponent seeds to play
        pits = [p for p in pits if row_sum(sow(st, seat, p)[0], 1 - seat) > 0]
    return pits


def position(b, turn):
    return "%s/%d" % (".".join(str(c) for c in b[:STORE[0]] + b[STORE[0] + 1:STORE[1]]), turn)


def result_text(b):
    s0, s1 = b[STORE[0]], b[STORE[1]]
    if s0 == s1:
        return "draw %d-%d" % (s0, s1)
    return "win %d %d-%d" % (0 if s0 > s1 else 1, s0, s1)


def sweep(b, seats, why):
    """Each of seats takes the seeds left on its row into its store -> events."""
    events = []
    for s in seats:
        n = row_sum(b, s)
        if n:
            for i in own_row(s):
                b[i] = 0
            b[STORE[s]] += n
            events.append("SWEEP %d %d %s" % (s, n, why))
    return events


def op_info(req):
    return INFO


def op_new(req):
    opts = req.get("options") or {}
    rules = req.get("rules")             # the table's ruleset, from the core (NEW mancala oware)
    asked = opts.get("rules")            # the option, for a core that does not pass the ruleset: ignored if it does
    if not rules and asked is not None:
        asked = asked.lower()
        if asked not in RULES:
            return {"error": "rules is kalah or oware"}
    rules = rules or asked or RULES[0]
    if rules not in RULES:
        return {"error": "unknown ruleset"}
    seeds = 4
    if "seeds" in opts:
        try:
            seeds = int(opts["seeds"])
        except ValueError:
            return {"error": "seeds must be a number"}
        if rules == "oware" and seeds != 4:
            return {"error": "oware starts with 4 seeds a pit"}
        if not 3 <= seeds <= 6:
            return {"error": "seeds must be 3 to 6"}
    if req.get("players") != 2:
        return {"error": "two players"}
    b = ([seeds] * PITS + [0]) * 2
    st = {"rules": rules, "seeds": seeds, "b": b, "turn": 0, "over": False, "last": None,
          "seen": [position(b, 0)] if rules == "oware" else []}
    return {"state": st, "events": ["TURN 0"]}


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    b = st["b"]
    wire = ["RU %s %d" % (st["rules"], st["seeds"]), "B %s" % " ".join(str(c) for c in b)]
    if st.get("last"):
        wire.append("LM %d %d" % tuple(st["last"]))
    wire.append("W %d" % (-1 if st["over"] else st["turn"]))
    if st["over"]:
        status = "Game over: " + result_text(b)
    elif seat == st["turn"]:
        status = "Your move"
    elif seat < 0:
        status = "Seat %d to move" % st["turn"]
    else:
        status = "Waiting for the other player"
    return {"wire": wire, "geometry": ["BRD %d" % PITS], "status": status,
            "data": {"rules": st["rules"], "seeds": st["seeds"], "board": b, "turn": st["turn"],
                     "last": st.get("last"), "over": st["over"]}}


def op_moves(req):
    st, seat = req["state"], req.get("seat", -1)
    if st["over"] or seat != st["turn"]:
        return {"moves": []}
    return {"moves": [str(p) for p in legal(st, seat)]}


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if len(move) != 1:
        return {"error": "a move is one pit, 1 to 6"}
    if not (move[0].isdigit() and 1 <= int(move[0]) <= PITS):
        return {"error": "no such pit: pits are 1 to 6"}
    pit = int(move[0])
    if st["b"][pit_index(seat, pit)] == 0:
        return {"error": "pit %d is empty" % pit}
    if pit not in legal(st, seat):
        return {"error": "the other side is empty: you must play a pit that feeds it"}
    b, n, captured, again, grand = sow(st, seat, pit)
    if req.get("check"):
        return {"info": "captures %d" % captured}
    events = ["SOW %d %d %d %s" % (seat, pit, n, " ".join(str(c) for c in b))]
    if captured:
        events.append("CAP %d %d" % (seat, captured))
    if grand:
        events.append("GRAND %d" % seat)
    new = {"rules": st["rules"], "seeds": st["seeds"], "b": b, "turn": 1 - seat, "over": False,
           "last": [seat, pit], "seen": []}
    if st["rules"] == "kalah":
        if row_sum(b, 0) == 0 or row_sum(b, 1) == 0:
            events += sweep(b, (0, 1), "empty")
            new["over"] = True
        elif again:
            events.append("AGAIN %d" % seat)
            new["turn"] = seat
    else:
        seen = [] if captured else list(st.get("seen") or [])
        pos = position(b, 1 - seat)
        if max(b[STORE[0]], b[STORE[1]]) >= WIN_OWARE:
            new["over"] = True
        elif pos in seen or len(seen) >= NO_CAPTURE_LIMIT:
            events += sweep(b, (0, 1), "cycle")     # an endless cycle: each takes its own side
            new["over"] = True
        elif not legal(new, 1 - seat):
            events += sweep(b, (1 - seat, seat), "nofeed")  # cannot move or feed: takes its own side
            new["over"] = True
        else:
            new["seen"] = seen + [pos]
    out = {"state": new, "events": events}
    if new["over"]:
        out["end"] = result_text(b)
    else:
        events.append("TURN %d" % new["turn"])
    return out


def op_result(req):
    st = req["state"]
    if not st["over"]:
        return {"over": False}
    return {"over": True, "scores": [st["b"][STORE[0]], st["b"][STORE[1]]]}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "sowed pit 3, capturing 4"."""
    seat, out = str(req.get("seat")), []
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != seat:
            continue
        if p[0] == "SOW" and len(p) >= 3:
            out.append("sowed pit %s" % p[2])
        elif p[0] == "CAP" and len(p) >= 3:
            out.append("capturing %s" % p[2])
        elif p[0] == "AGAIN":
            out.append("moving again")
    return {"say": ", ".join(out)}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "say": op_say}


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
