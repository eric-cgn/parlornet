#!/usr/bin/env python3
"""checkers/1 game plugin (docs/types/checkers-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

Ruleset american (English draughts): an 8 by 8 board, the 32 dark squares
numbered 1-32 in the standard way (1-4 on the top row, seen from White's
side). Seat 0 is Black: its men start on 1-12, move towards 32 and move
first. Seat 1 is White, on 21-32. Pieces in the state: x (Black man),
X (Black king), o (White man), O (White king), . (empty).

Turns are staged: each MOV is one step (11-15, or a jump 15x22); a jump
that can go on must go on, and a man crowned on the far row ends the turn
there. "!undo" takes back the last step and "!end" commits the turn (the
core sends these for UND and END). "draw" offers a draw; the other player
answers "accept" or "reject".
"""
import json
import re
import sys

INFO = {
    "type": "checkers", "ver": 1, "name": "Checkers",
    "rules": ["american"], "min": 2, "max": 2, "flags": ["staged"],
    "options": ["limit"],
}
N = 8                       # board size
LIMIT = 40                  # moves each without a capture or a man moving: a draw
MAN, KING = "xo", "XO"      # by seat
FORWARD = (1, -1)           # the row direction of each seat's men (row 0 is the top)
CROWN_ROW = (N - 1, 0)
STEP_RE = re.compile(r"^\d{1,2}([-x]\d{1,2})+\Z")


# ---- squares ---------------------------------------------------------------------

def rc(s):
    """Square number -> (row, column), row 0 at the top (Black's side)."""
    per = N // 2
    r = (s - 1) // per
    return r, 2 * ((s - 1) % per) + (1 if r % 2 == 0 else 0)


def sq(r, c):
    """(row, column) -> square number, or 0 off the board or on a light square."""
    if 0 <= r < N and 0 <= c < N and (r + c) % 2 == 1:
        return r * (N // 2) + c // 2 + 1
    return 0


def owner(ch):
    return 0 if ch in "xX" else 1 if ch in "oO" else -1


def dirs(ch):
    if ch in KING:
        return [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    f = FORWARD[owner(ch)]
    return [(f, -1), (f, 1)]


def jumps_from(cells, s):
    """[(from, to, captured square)] for the piece on s."""
    ch = cells[s - 1]
    seat = owner(ch)
    r, c = rc(s)
    out = []
    for dr, dc in dirs(ch):
        m, t = sq(r + dr, c + dc), sq(r + 2 * dr, c + 2 * dc)
        if m and t and owner(cells[m - 1]) == 1 - seat and cells[t - 1] == ".":
            out.append((s, t, m))
    return out


def slides_from(cells, s):
    r, c = rc(s)
    out = []
    for dr, dc in dirs(cells[s - 1]):
        t = sq(r + dr, c + dc)
        if t and cells[t - 1] == ".":
            out.append((s, t, 0))
    return out


def squares_of(cells, seat):
    return [i + 1 for i, ch in enumerate(cells) if owner(ch) == seat]


def all_jumps(cells, seat):
    return [j for s in squares_of(cells, seat) for j in jumps_from(cells, s)]


def has_move(cells, seat):
    return any(jumps_from(cells, s) or slides_from(cells, s) for s in squares_of(cells, seat))


def tok(a, b, cap):
    return "%d%s%d" % (a, "x" if cap else "-", b)


def legal(st):
    """{(from, to): captured square or 0}: the steps the side to move may play now."""
    if st["over"] or st["phase"] != "move":
        return {}
    cells = st["cells"]
    if st["steps"]:
        last = st["steps"][-1]
        if not last["cap"] or last["crowned"]:
            return {}                            # the turn is complete
        return {(a, b): m for a, b, m in jumps_from(cells, last["to"])}
    moves = all_jumps(cells, st["turn"])         # captures are compulsory
    if not moves:
        moves = [m for s in squares_of(cells, st["turn"]) for m in slides_from(cells, s)]
    return {(a, b): m for a, b, m in moves}


def counts(cells):
    return [cells.count(c) for c in ("x", "X", "o", "O")]


# ---- state ------------------------------------------------------------------------

def new_state(limit=LIMIT):
    cells = MAN[0] * 12 + "." * 8 + MAN[1] * 12
    return {"cells": cells, "turn": 0, "phase": "move", "steps": [], "quiet": 0, "limit": 2 * limit,
            "offer": None, "no_offer": -1, "over": False, "result": None}


def result_text(result):
    p = result.split()
    if p[0] == "win":
        w = ("Black", "White")[int(p[1])]
        l = ("Black", "White")[1 - int(p[1])]
        return "%s wins: %s %s" % (w, l, "has no pieces left" if p[2] == "captured" else "cannot move")
    if p[1] == "agreed":
        return "a draw by agreement"
    return "a draw: no capture and no man moved in the move limit"


def finish(st, events, result):
    st["over"], st["phase"], st["result"] = True, "over", result
    st["offer"] = None
    return {"state": st, "events": events, "end": result}


def won_by(st, seat):
    """The result if seat's completed turn leaves the other side without a move, else None."""
    other = 1 - seat
    if not squares_of(st["cells"], other):
        return "win %d captured" % seat
    if not has_move(st["cells"], other):
        return "win %d blocked" % seat
    return None


# ---- ops ----------------------------------------------------------------------------

def op_info(req):
    return INFO


def op_new(req):
    if req.get("players") != 2:
        return {"error": "two players"}
    opts = req.get("options") or {}
    limit = opts.get("limit", str(LIMIT))
    if not limit.isdigit() or not (int(limit) == 0 or 10 <= int(limit) <= 200):
        return {"error": "limit is moves each without a capture or a man moving: 10 to 200, or 0 for none"}
    return {"state": new_state(int(limit)), "events": ["TURN 0"]}


def path_of(st):
    if not st["steps"]:
        return []
    return [st["steps"][0]["from"]] + [s["to"] for s in st["steps"]]


def phase_word(st):
    if st["over"]:
        return "over"
    if st["phase"] == "move" and st["steps"] and not legal(st):
        return "done"
    return st["phase"]


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    c = counts(st["cells"])
    q = phase_word(st)
    wire = ["B " + st["cells"], "C %d %d %d %d" % tuple(c), "W %s" % ("-" if st["over"] else st["turn"]),
            "Q " + q, "L %d %d" % (st["quiet"], st["limit"])]
    path = path_of(st)
    if path and not st["over"]:
        wire.append("J " + " ".join(str(s) for s in path))
    off = st.get("offer")
    if off and not st["over"]:
        wire.append("D %d" % off["seat"])
    side = ("Black", "White")
    if st["over"]:
        status = "Game over: " + result_text(st["result"])
    elif q == "draw":
        if seat == st["turn"]:
            status = "Your opponent offers a draw: accept or reject"
        elif seat == off["seat"]:
            status = "Waiting for the answer to your draw offer"
        else:
            status = "%s offers a draw" % side[off["seat"]]
    elif seat == st["turn"]:
        if q == "done":
            status = "Turn complete: END to finish, UND to take back"
        elif path:
            status = "Keep jumping"
        elif all_jumps(st["cells"], seat):
            status = "Your move: you must capture"
        else:
            status = "Your move"
    elif seat < 0:
        status = "%s to move" % side[st["turn"]]
    else:
        status = "Waiting for the other player"
    return {"wire": wire, "geometry": ["BRD %d" % N], "status": status,
            "data": {"cells": st["cells"], "turn": st["turn"], "phase": q, "path": path, "quiet": st["quiet"],
                     "limit": st["limit"], "offer": off["seat"] if off else None, "result": st["result"]}}


def op_moves(req):
    st, seat = req["state"], req.get("seat", -1)
    if st["over"] or seat != st["turn"]:
        return {"moves": []}
    if st["phase"] == "draw":
        return {"moves": ["accept", "reject"]}
    return {"moves": [tok(a, b, m) for (a, b), m in sorted(legal(st).items())]}


def parse_steps(word):
    """'15x22x31' -> [(15, 22), (22, 31)]; '11-15' -> [(11, 15)]; None if not steps."""
    if not STEP_RE.match(word):
        return None
    sqs = [int(x) for x in re.split(r"[-x]", word)]
    return list(zip(sqs, sqs[1:]))


def play_step(st, seat, a, b, events):
    """One step on st (changed in place) -> an error text or None."""
    lg = legal(st)
    if (a, b) not in lg:
        if lg and all(m for m in lg.values()) and not st["steps"] and abs(rc(a)[0] - rc(b)[0]) == 1:
            return "a capture is compulsory: %s" % " ".join(tok(x, y, m) for (x, y), m in sorted(lg.items()))
        if st["steps"] and lg:
            return "the jump must go on from %d" % st["steps"][-1]["to"]
        return "%d-%d is not a legal step now" % (a, b)
    m = lg[(a, b)]
    cells = list(st["cells"])
    piece = cells[a - 1]
    capp = cells[m - 1] if m else ""
    cells[a - 1] = "."
    if m:
        cells[m - 1] = "."
    crowned = piece in MAN and rc(b)[0] == CROWN_ROW[seat]
    cells[b - 1] = KING[seat] if crowned else piece
    st["cells"] = "".join(cells)
    st["steps"].append({"from": a, "to": b, "cap": m, "capp": capp, "piece": piece, "crowned": crowned})
    events.append("JUMP %d %d %d %d" % (seat, a, b, m) if m else "STEP %d %d %d" % (seat, a, b))
    if crowned:
        events.append("CROWN %d %d" % (seat, b))
    return None


def offer_draw(st, seat, check):
    if seat not in (0, 1):
        return {"error": "only a player can offer a draw"}
    if st["phase"] == "draw":
        return {"error": "a draw offer is waiting for an answer"}
    if st["steps"]:
        return {"error": "finish the turn first" if seat == st["turn"] else
                "wait until the other player has finished the turn"}
    if st.get("no_offer") == seat:
        return {"error": "you can offer a draw again after your next move"}
    if check:
        return {"info": "draw"}
    st["offer"] = {"seat": seat, "turn": st["turn"]}
    st["phase"] = "draw"
    events = ["DRAW %d" % seat]
    if st["turn"] == seat:
        st["turn"] = 1 - seat
        events.append("TURN %d" % (1 - seat))
    return {"state": st, "events": events}


def op_apply(req):
    st = json.loads(json.dumps(req["state"]))
    seat, move = req.get("seat", -1), [m.lower() for m in (req.get("move") or [])]
    check = req.get("check")
    if st["over"]:
        return {"error": "the game is over"}
    if not move:
        return {"error": "a step is from-to, like 11-15 or 15x22"}
    if move == ["draw"]:
        return offer_draw(st, seat, check)
    if seat != st["turn"]:
        return {"error": "not your turn"}
    events = []
    if st["phase"] == "draw":
        if move not in (["accept"], ["reject"]):
            return {"error": "accept or reject the draw offer first"}
        if check:
            return {"info": move[0]}
        off, st["offer"] = st["offer"], None
        if move == ["accept"]:
            return finish(st, ["ACCEPT %d" % seat], "draw agreed")
        events.append("REJECT %d" % seat)
        st["phase"], st["no_offer"] = "move", off["seat"]
        if off["turn"] != st["turn"]:
            st["turn"] = off["turn"]
            events.append("TURN %d" % st["turn"])
        return {"state": st, "events": events}
    if move == ["!undo"]:
        if not st["steps"]:
            return {"error": "nothing to take back"}
        if check:
            return {"info": "undo"}
        s = st["steps"].pop()
        cells = list(st["cells"])
        cells[s["to"] - 1] = "."
        cells[s["from"] - 1] = s["piece"]
        text = "UNDO %d %d %d %s" % (seat, s["from"], s["to"], s["piece"])
        if s["cap"]:
            cells[s["cap"] - 1] = s["capp"]
            text += " %d %s" % (s["cap"], s["capp"])
        st["cells"] = "".join(cells)
        return {"state": st, "events": [text], "staged": "more"}
    if move == ["!end"]:
        if not st["steps"]:
            return {"error": "make a move first"}
        if legal(st):
            return {"error": "the jump must go on from %d" % st["steps"][-1]["to"]}
        if check:
            return {"info": "end"}
        steps, st["steps"] = st["steps"], []
        progress = any(s["cap"] for s in steps) or steps[0]["piece"] in MAN
        st["quiet"] = 0 if progress else st["quiet"] + 1
        if st.get("no_offer") == seat:
            st["no_offer"] = -1
        if st["limit"] and st["quiet"] >= st["limit"]:
            return finish(st, events, "draw limit")
        st["turn"] = 1 - seat
        events.append("TURN %d" % st["turn"])
        return {"state": st, "events": events}
    steps = []
    for word in move:
        p = parse_steps(word)
        if p is None:
            return {"error": "a step is from-to, like 11-15 or 15x22"}
        steps += p
    for a, b in steps:
        if not (1 <= a <= N * N // 2 and 1 <= b <= N * N // 2):
            return {"error": "squares are numbered 1 to %d" % (N * N // 2)}
        err = play_step(st, seat, a, b, events)
        if err:
            return {"error": err}
    if check:
        return {"info": "ok"}
    if not legal(st):
        result = won_by(st, seat)        # a completed turn that leaves no move ends the game at once
        if result:
            st["steps"] = []
            return finish(st, events, result)
        return {"state": st, "events": events, "staged": "done"}
    return {"state": st, "events": events, "staged": "more"}


def op_result(req):
    st = req["state"]
    if not st["over"]:
        return {"over": False}
    p = st["result"].split()
    if p[0] == "win":
        return {"over": True, "scores": [1, 0] if p[1] == "0" else [0, 1]}
    return {"over": True, "scores": [0.5, 0.5]}

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events (the steps taken back left out): "moved 11-15", "jumped
    15x22x29", "offered a draw"."""
    seat, path, jumped, crowned, other = str(req.get("seat")), [], False, False, ""
    for e in req.get("events") or []:
        p = e.split()
        if len(p) < 2 or p[1] != seat:
            continue
        if p[0] in ("STEP", "JUMP") and len(p) >= 4:
            path = (path or [p[2]]) + [p[3]]
            jumped = jumped or p[0] == "JUMP"
        elif p[0] == "UNDO" and path:
            path.pop()
            if len(path) == 1:
                path, jumped = [], False
        elif p[0] == "CROWN":
            crowned = True
        elif p[0] in ("DRAW", "ACCEPT", "REJECT"):
            other = {"DRAW": "offered a draw", "ACCEPT": "accepted the draw", "REJECT": "declined the draw"}[p[0]]
    if path:
        return {"say": "%s %s%s" % ("jumped" if jumped else "moved", ("x" if jumped else "-").join(path),
                                    ", crowned" if crowned else "")}
    return {"say": other}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply, "result": op_result,
       "say": op_say}


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
