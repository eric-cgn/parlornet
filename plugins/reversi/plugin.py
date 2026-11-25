#!/usr/bin/env python3
"""reversi/1 game plugin (docs/types/reversi-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

State: {"n": size, "cells": n*n chars of '.', '0', '1' (row-major, row 1 first),
        "turn": seat to move, "over": bool}
Seat 0 moves first. Squares are a column letter and a row number: d3.
"""
import json
import sys

DIRS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
INFO = {
    "type": "reversi", "ver": 1, "name": "Reversi",
    "rules": ["standard"], "min": 2, "max": 2, "flags": [],
    "options": ["size"],
}


def sq_name(x, y):
    return "%s%d" % (chr(ord("a") + x), y + 1)


def parse_sq(tok, n):
    tok = tok.lower()
    if len(tok) < 2 or not tok[0].isalpha() or not tok[1:].isdigit():
        return None
    x, y = ord(tok[0]) - ord("a"), int(tok[1:]) - 1
    return (x, y) if 0 <= x < n and 0 <= y < n else None


def flips(st, x, y, seat):
    """Squares flipped by seat playing at x,y (empty list if illegal)."""
    n, cells = st["n"], st["cells"]
    if cells[y * n + x] != ".":
        return []
    me, opp = str(seat), str(1 - seat)
    out = []
    for dx, dy in DIRS:
        run = []
        cx, cy = x + dx, y + dy
        while 0 <= cx < n and 0 <= cy < n and cells[cy * n + cx] == opp:
            run.append((cx, cy))
            cx, cy = cx + dx, cy + dy
        if run and 0 <= cx < n and 0 <= cy < n and cells[cy * n + cx] == me:
            out.extend(run)
    return out


def legal(st, seat):
    n = st["n"]
    return [(x, y) for y in range(n) for x in range(n) if flips(st, x, y, seat)]


def counts(st):
    return st["cells"].count("0"), st["cells"].count("1")


def result_text(st):
    c0, c1 = counts(st)
    if c0 == c1:
        return "draw %d-%d" % (c0, c1)
    return "win %d %d-%d" % (0 if c0 > c1 else 1, c0, c1)


def op_info(req):
    return INFO


def op_new(req):
    opts = req.get("options") or {}
    n = 8
    if "size" in opts:
        try:
            n = int(opts["size"])
        except ValueError:
            return {"error": "size must be a number"}
        if n < 4 or n > 10 or n % 2:
            return {"error": "size must be even, 4 to 10"}
    if req.get("players") != 2:
        return {"error": "two players"}
    cells = ["."] * (n * n)
    m = n // 2
    cells[(m - 1) * n + (m - 1)] = "1"
    cells[m * n + m] = "1"
    cells[(m - 1) * n + m] = "0"
    cells[m * n + (m - 1)] = "0"
    st = {"n": n, "cells": "".join(cells), "turn": 0, "over": False}
    return {"state": st, "events": ["TURN 0"]}


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    n = st["n"]
    c0, c1 = counts(st)
    wire = ["R %d %s" % (y + 1, st["cells"][y * n:(y + 1) * n]) for y in range(n)]
    wire.append("C %d %d" % (c0, c1))
    wire.append("W %d" % (-1 if st["over"] else st["turn"]))
    if st["over"]:
        status = "Game over: " + result_text(st)
    elif seat == st["turn"]:
        status = "Your move"
    elif seat < 0:
        status = "Seat %d to move" % st["turn"]
    else:
        status = "Waiting for the other player"
    return {"wire": wire, "geometry": ["BRD %d %d" % (n, n)], "status": status,
            "data": {"n": n, "cells": st["cells"], "counts": [c0, c1], "turn": st["turn"]}}


def op_moves(req):
    st, seat = req["state"], req.get("seat", -1)
    if st["over"] or seat != st["turn"]:
        return {"moves": []}
    return {"moves": [sq_name(x, y) for x, y in legal(st, seat)]}


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if len(move) != 1:
        return {"error": "a move is one square, like d3"}
    pos = parse_sq(move[0], st["n"])
    if pos is None:
        return {"error": "no such square"}
    fl = flips(st, pos[0], pos[1], seat)
    if not fl:
        return {"error": "that square flips nothing"}
    if req.get("check"):
        return {"info": "flips %d" % len(fl)}
    n = st["n"]
    cells = list(st["cells"])
    cells[pos[1] * n + pos[0]] = str(seat)
    for fx, fy in fl:
        cells[fy * n + fx] = str(seat)
    st = {"n": n, "cells": "".join(cells), "turn": 1 - seat, "over": False}
    events = ["PUT %d %s FLP %s" % (seat, sq_name(*pos), " ".join(sq_name(x, y) for x, y in fl))]
    if legal(st, 1 - seat):
        events.append("TURN %d" % (1 - seat))
    elif legal(st, seat):
        events.append("PASS %d" % (1 - seat))
        st["turn"] = seat
        events.append("TURN %d" % seat)
    else:
        st["over"] = True
    out = {"state": st, "events": events}
    if st["over"]:
        out["end"] = result_text(st)
    return out

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "played d3, turning 4", "passed"."""
    seat = req.get("seat")
    for e in req.get("events") or []:
        p = e.split()
        if p[0] == "PUT" and len(p) >= 3 and p[1] == str(seat):
            n = len(p) - 4 if len(p) > 3 and p[3] == "FLP" else 0
            return {"say": "played %s, turning %d" % (p[2], n) if n else "played %s" % p[2]}
        if p[0] == "PASS" and p[1:] == [str(seat)]:
            return {"say": "passed"}
    return {"say": ""}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply, "say": op_say}


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
