#!/usr/bin/env python3
"""fiveinarow/1 game plugin (docs/types/fiveinarow-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

State: {"n": size, "rules": "freestyle" or "standard",
        "cells": n*n chars of '.', '0', '1' (row-major, row 1 first),
        "hist": [point index, ...] in the order played (seat 0 played the even ones),
        "turn": seat to move, "over": bool, "five": [point index, ...] (the winning line)}
Seat 0 moves first. Points are a column letter and a row number, rows counted
from the top: h8 is the center of the 15 by 15 board.
"""
import json
import sys

RULES = ["freestyle", "standard"]       # five or more wins; exactly five wins
INFO = {
    "type": "fiveinarow", "ver": 1, "name": "Five in a Row",
    "rules": RULES, "min": 2, "max": 2, "flags": ["nomoves"],
    "options": ["size"],
}
SIZE_MIN, SIZE_MAX, SIZE_DEFAULT = 9, 20, 15        # 20: the board of NetLuff (Mac, 1998)
DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]
LINE_MAX = 78                           # a wire line, before CR LF (protocol.md: 80 bytes with it)


def sq_name(n, i):
    return "%s%d" % (chr(ord("a") + i % n), i // n + 1)


def parse_sq(tok, n):
    tok = tok.lower()
    if len(tok) < 2 or not "a" <= tok[0] <= "z" or not tok[1:].isdigit() or len(tok) > 3:
        return None
    x, y = ord(tok[0]) - ord("a"), int(tok[1:]) - 1
    return y * n + x if 0 <= x < n and 0 <= y < n else None


def line_through(st, i, seat):
    """The longest run of seat's stones through point i, in each direction that
    wins under the table's rules -> the winning run's points, or []."""
    n, cells, me = st["n"], st["cells"], str(seat)
    x0, y0 = i % n, i // n
    for dx, dy in DIRS:
        run = [i]
        for sgn in (1, -1):
            x, y = x0 + sgn * dx, y0 + sgn * dy
            while 0 <= x < n and 0 <= y < n and cells[y * n + x] == me:
                run.append(y * n + x)
                x, y = x + sgn * dx, y + sgn * dy
        if len(run) == 5 or (len(run) > 5 and st["rules"] == "freestyle"):
            return sorted(run)
    return []


def counts(st):
    return st["cells"].count("0"), st["cells"].count("1")


def result_text(st):
    if st["five"]:
        n = st["n"]
        seat = int(st["cells"][st["five"][0]])
        return "win %d %s-%s" % (seat, sq_name(n, st["five"][0]), sq_name(n, st["five"][-1]))
    return "draw %d-%d" % counts(st)


def op_info(req):
    return INFO


def op_new(req):
    opts = req.get("options") or {}
    n = SIZE_DEFAULT
    if "size" in opts:
        try:
            n = int(opts["size"])
        except ValueError:
            return {"error": "size must be a number"}
        if not SIZE_MIN <= n <= SIZE_MAX:
            return {"error": "size must be %d to %d" % (SIZE_MIN, SIZE_MAX)}
    rules = req.get("rules") or RULES[0]
    if rules not in RULES:
        return {"error": "rules are %s" % " or ".join(RULES)}
    if req.get("players") != 2:
        return {"error": "two players"}
    st = {"n": n, "rules": rules, "cells": "." * (n * n), "hist": [], "turn": 0, "over": False, "five": []}
    return {"state": st, "events": ["TURN 0"]}


def wrapped(tag, toks):
    """tag tok tok ... over as many lines as needed to keep each within LINE_MAX."""
    out, line = [], tag
    for t in toks:
        if len(line) + 1 + len(t) > LINE_MAX:
            out.append(line)
            line = tag
        line += " " + t
    if line != tag:
        out.append(line)
    return out


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    n, hist = st["n"], st["hist"]
    wire = []
    for s in (0, 1):
        wire += wrapped("P %d" % s, [sq_name(n, i) for k, i in enumerate(hist) if k % 2 == s])
    c0, c1 = counts(st)
    wire.append("C %d %d" % (c0, c1))
    if hist:
        wire.append("L %d %s" % ((len(hist) - 1) % 2, sq_name(n, hist[-1])))
    if st["five"]:
        wire.append("F " + " ".join(sq_name(n, i) for i in st["five"]))
    wire.append("W %d" % (-1 if st["over"] else st["turn"]))
    if st["over"]:
        status = "Game over: " + result_text(st)
    elif seat == st["turn"]:
        status = "Your move"
    elif seat < 0:
        status = "Seat %d to move" % st["turn"]
    else:
        status = "Waiting for the other player"
    return {"wire": wire, "geometry": ["BRD %d %d" % (n, n), "RUL %s" % st["rules"]], "status": status,
            "data": {"n": n, "rules": st["rules"], "moves": [sq_name(n, i) for i in hist], "turn": st["turn"],
                     "five": [sq_name(n, i) for i in st["five"]], "over": st["over"]}}


def op_moves(req):
    # every empty point is legal: a list would be up to 361 lines, so clients use CHK (flag nomoves)
    return {"check": True}


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if len(move) != 1:
        return {"error": "a move is one point, like h8"}
    n = st["n"]
    i = parse_sq(move[0], n)
    if i is None:
        return {"error": "no such point"}
    if st["cells"][i] != ".":
        return {"error": "that point is taken"}
    cells = st["cells"][:i] + str(seat) + st["cells"][i + 1:]
    st = dict(st, cells=cells, hist=st["hist"] + [i], turn=1 - seat)
    five = line_through(st, i, seat)
    if req.get("check"):
        return {"info": "wins" if five else ""}
    events = ["PUT %d %s" % (seat, sq_name(n, i))]
    if five or "." not in cells:
        st["five"], st["over"] = five, True
        return {"state": st, "events": events, "end": result_text(st)}
    events.append("TURN %d" % (1 - seat))
    return {"state": st, "events": events}


def op_result(req):
    st = req["state"]
    if not st["over"]:
        return {"over": False}
    if st["five"]:
        winner = int(st["cells"][st["five"][0]])
        return {"over": True, "scores": [1 if s == winner else 0 for s in (0, 1)]}
    return {"over": True, "scores": [0, 0]}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "played h8"."""
    for e in req.get("events") or []:
        p = e.split()
        if p[0] == "PUT" and len(p) >= 3 and p[1] == str(req.get("seat")):
            return {"say": "played %s" % p[2]}
    return {"say": ""}


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
