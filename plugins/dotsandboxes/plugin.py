#!/usr/bin/env python3
"""dotsandboxes/1 game plugin (docs/types/dotsandboxes-1.md).

Speaks the plugin protocol of docs/plugins.md: one JSON object per line on
stdin, one JSON reply per line on stdout. Pure functions of their arguments;
the state is opaque to the server.

State: {"w", "h": boxes across and down, "n": players,
        "hl": (h+1)*w chars, the horizontal lines row by row ('.' open, '-' drawn),
        "vl": h*(w+1) chars, the vertical lines row by row ('.' open, '|' drawn),
        "bx": h*w chars, the boxes ('.' open, '0'-'3' the seat that closed it),
        "turn": seat to move, "again": the turn is an extra one (a box was closed),
        "last": the last line drawn ('' before the first), "over": bool,
        "gone": [bool per seat], seats that left the game (op drop; absent in older states),
        "rules": the ruleset (absent in older states, which are standard)}
Dots are a column letter and a row number from the top, a1 to j10 (to n12 on
the majority board); a line is its two dots, the left or upper one first:
a1-b1 (across), a1-a2 (down). Boxes are named by their upper left dot. Seat 0
moves first.

Rulesets: standard (3 to 9 boxes each way, and every box is played) and
majority (13 boxes across and 11 down, two players, and the game ends as soon
as one player holds more than half the boxes). The majority ruleset is the
board and the end of GameMaster's Dots&Boxes rulebook.

The extra op "drop" (flag "drop") lets one seat leave a game of three or four
(it resigned or ran out of time) while the others play on: its turn is
skipped, its boxes stay its own, and it cannot win. With one seat left, that
seat wins. The core calls it for RES and turn timeouts while more than two
seats are still playing.
"""
import json
import re
import sys

INFO = {
    "type": "dotsandboxes", "ver": 1, "name": "Dots and Boxes",
    "rules": ["standard", "majority"], "min": 2, "max": 4, "flags": ["drop"],
    "options": ["size"],
}
MIN_SIDE, MAX_SIDE, DEFAULT_SIDE = 3, 9, 5
MAJORITY_SIZE = (13, 11)        # the majority ruleset's board, boxes across and down
SIZE_RE = re.compile(r"^([0-9]{1,2})(?:x([0-9]{1,2}))?\Z")
DOT_RE = re.compile(r"^([a-z])([0-9]{1,2})\Z")


def dot_name(x, y):
    return "%s%d" % (chr(ord("a") + x), y + 1)


def parse_dot(tok, st):
    m = DOT_RE.match(tok)
    if not m:
        return None
    x, y = ord(m.group(1)) - ord("a"), int(m.group(2)) - 1
    return (x, y) if 0 <= x <= st["w"] and 0 <= y <= st["h"] else None


def parse_line(tok, st):
    """'a1-b1' (either order, any case, the dash optional) -> ('h', x, y) or ('v', x, y), the line's left
    or upper dot; or an error text."""
    tok = tok.lower()
    m = re.match(r"^([a-z][0-9]{1,2})-?([a-z][0-9]{1,2})\Z", tok)
    if not m:
        return "a line is its two dots, like a1-b1"
    d1, d2 = parse_dot(m.group(1), st), parse_dot(m.group(2), st)
    if d1 is None or d2 is None:
        return "no such dot on this board"
    (x1, y1), (x2, y2) = sorted([d1, d2], key=lambda d: (d[1], d[0]))
    if y1 == y2 and x2 == x1 + 1:
        return ("h", x1, y1)
    if x1 == x2 and y2 == y1 + 1:
        return ("v", x1, y1)
    return "those dots are not neighbors"


def line_name(kind, x, y):
    return "%s-%s" % (dot_name(x, y), dot_name(x + 1, y) if kind == "h" else dot_name(x, y + 1))


def is_open(st, kind, x, y):
    if kind == "h":
        return st["hl"][y * st["w"] + x] == "."
    return st["vl"][y * (st["w"] + 1) + x] == "."


def open_lines(st):
    w, h = st["w"], st["h"]
    out = []
    for y in range(h + 1):
        out += [line_name("h", x, y) for x in range(w) if st["hl"][y * w + x] == "."]
        if y < h:
            out += [line_name("v", x, y) for x in range(w + 1) if st["vl"][y * (w + 1) + x] == "."]
    return out


def closed(st, bx, by):
    """True when all four sides of box bx,by are drawn."""
    w = st["w"]
    return (st["hl"][by * w + bx] == "-" and st["hl"][(by + 1) * w + bx] == "-"
            and st["vl"][by * (w + 1) + bx] == "|" and st["vl"][by * (w + 1) + bx + 1] == "|")


def counts(st):
    return [st["bx"].count(str(i)) for i in range(st["n"])]


def live_seats(st):
    """The seats still playing (a seat that resigned or ran out of time is gone; see op_drop)."""
    gone = st.get("gone") or []
    return [i for i in range(st["n"]) if not (i < len(gone) and gone[i])]


def next_seat(st, seat):
    """The next seat still playing after seat, in seat order."""
    live = live_seats(st)
    return next((i for i in live if i > seat), live[0])


def result_text(st):
    """win seat n0-n1-... or draw n0-n1-...: the most boxes among the seats still playing wins (a seat that
    left keeps its boxes but cannot win); the last seat playing wins whatever its count."""
    c = counts(st)
    score = "-".join(str(n) for n in c)
    live = live_seats(st)
    best = max(c[i] for i in live)
    top = [i for i in live if c[i] == best]
    if len(top) > 1:
        return "draw %s" % score
    return "win %d %s" % (top[0], score)


def op_info(req):
    return INFO


def majority(st):
    """True under the majority ruleset, where the game ends when one seat holds more than half the boxes."""
    return st.get("rules") == "majority"


def op_new(req):
    opts = req.get("options") or {}
    rules = req.get("rules") or "standard"
    if rules not in INFO["rules"]:
        return {"error": "unknown ruleset"}
    w = h = DEFAULT_SIDE
    if rules == "majority":
        w, h = MAJORITY_SIZE
        if "size" in opts and str(opts["size"]).lower() != "%dx%d" % MAJORITY_SIZE:
            return {"error": "the majority board is 13 boxes across and 11 down"}
        if req.get("players") != 2:
            return {"error": "the majority rules are for two players"}
    elif "size" in opts:
        m = SIZE_RE.match(str(opts["size"]).lower())
        if not m:
            return {"error": "size is boxes across, or across x down: 5 or 6x4"}
        w = int(m.group(1))
        h = int(m.group(2) or m.group(1))
        if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
            return {"error": "size is %d to %d boxes each way" % (MIN_SIDE, MAX_SIDE)}
    n = req.get("players")
    if not isinstance(n, int) or not 2 <= n <= 4:
        return {"error": "two to four players"}
    st = {"w": w, "h": h, "n": n, "hl": "." * ((h + 1) * w), "vl": "." * (h * (w + 1)), "bx": "." * (w * h),
          "turn": 0, "again": False, "last": "", "over": False, "gone": [False] * n, "rules": rules}
    return {"state": st, "events": ["TURN 0"]}


def op_view(req):
    st, seat = req["state"], req.get("seat", -1)
    w, h = st["w"], st["h"]
    wire = []
    for y in range(h + 1):
        wire.append("HL %d %s" % (y + 1, st["hl"][y * w:(y + 1) * w]))
    for y in range(h):
        wire.append("VL %d %s" % (y + 1, st["vl"][y * (w + 1):(y + 1) * (w + 1)]))
    for y in range(h):
        wire.append("BX %d %s" % (y + 1, st["bx"][y * w:(y + 1) * w]))
    c = counts(st)
    wire.append("C " + " ".join(str(n) for n in c))
    gone = [i not in live_seats(st) for i in range(st["n"])]
    if any(gone):
        wire.append("G " + " ".join("1" if g else "0" for g in gone))
    if st["last"]:
        wire.append("LL " + st["last"])
    wire.append("W " + ("-" if st["over"] else str(st["turn"])))
    if st["over"]:
        status = "Game over: " + result_text(st)
    elif seat == st["turn"]:
        status = "Your move again: you closed a box" if st["again"] else "Your move"
    elif seat < 0 or seat >= st["n"]:
        status = "Seat %d to move" % st["turn"]
    elif gone[seat]:
        status = "You have left this game; seat %d to move" % st["turn"]
    elif st["n"] == 2:
        status = "Waiting for the other player"
    else:
        status = "Waiting for seat %d" % st["turn"]
    return {"wire": wire, "geometry": ["BRD %d %d" % (w, h)], "status": status,
            "data": {"w": w, "h": h, "hl": st["hl"], "vl": st["vl"], "boxes": st["bx"], "counts": c,
                     "turn": -1 if st["over"] else st["turn"], "last": st["last"], "gone": gone}}


def op_moves(req):
    st, seat = req["state"], req.get("seat", -1)
    if st["over"] or seat != st["turn"]:
        return {"moves": []}
    return {"moves": open_lines(st)}


def op_apply(req):
    st, seat, move = req["state"], req.get("seat", -1), req.get("move") or []
    if st["over"]:
        return {"error": "the game is over"}
    if seat != st["turn"]:
        return {"error": "not your turn"}
    if len(move) == 2:                      # "a1 b1", the two dots as two tokens
        move = [move[0] + "-" + move[1]]
    if len(move) != 1:
        return {"error": "a move is one line, like a1-b1"}
    ln = parse_line(move[0], st)
    if isinstance(ln, str):
        return {"error": ln}
    kind, x, y = ln
    if not is_open(st, kind, x, y):
        return {"error": "that line is drawn already"}
    w, h = st["w"], st["h"]
    st = dict(st)
    if kind == "h":
        i = y * w + x
        st["hl"] = st["hl"][:i] + "-" + st["hl"][i + 1:]
        near = [(x, y - 1), (x, y)]          # the boxes above and below
    else:
        i = y * (w + 1) + x
        st["vl"] = st["vl"][:i] + "|" + st["vl"][i + 1:]
        near = [(x - 1, y), (x, y)]          # the boxes left and right
    took = [(bx, by) for bx, by in near if 0 <= bx < w and 0 <= by < h and st["bx"][by * w + bx] == "."
            and closed(st, bx, by)]
    if req.get("check"):
        return {"info": "boxes %d" % len(took)}
    boxes = list(st["bx"])
    for bx, by in took:
        boxes[by * w + bx] = str(seat)
    st["bx"] = "".join(boxes)
    st["last"] = line_name(kind, x, y)
    ev = "LINE %d %s" % (seat, st["last"])
    if took:
        ev += " BOX " + " ".join(dot_name(bx, by) for bx, by in took)
    events = [ev]
    out = {"state": st, "events": events}
    if "." not in st["bx"] or (majority(st) and took and 2 * counts(st)[seat] > w * h):
        st["over"], st["again"] = True, False
        out["end"] = result_text(st)
        return out
    st["again"] = bool(took)
    st["turn"] = seat if took else next_seat(st, seat)
    events.append("TURN %d" % st["turn"])      # also for the same seat: its turn clock starts again
    return out


def op_result(req):
    st = req["state"]
    if not st["over"]:
        return {"over": False}
    return {"over": True, "scores": counts(st)}


def op_drop(req):
    """A seat leaves a running game (it resigned or ran out of time) and the others play on. Its turn is
    skipped from now on; if it was its turn, the next seat still playing moves. Its boxes stay its own, but
    it cannot win. With one seat left, that seat wins."""
    st = dict(req["state"])
    seat, why = req.get("seat", -1), str(req.get("why") or "resign")
    if st["over"]:
        return {"error": "the game is over"}
    gone = [i not in live_seats(st) for i in range(st["n"])]
    if not isinstance(seat, int) or not 0 <= seat < st["n"] or gone[seat]:
        return {"error": "that seat is not playing"}
    gone[seat] = True
    st["gone"] = gone
    events = ["GONE %d %s" % (seat, why if why in ("resign", "timeout") else "resign")]
    out = {"state": st, "events": events}
    if len(live_seats(st)) == 1:
        st["over"], st["again"] = True, False
        out["end"] = result_text(st)
        return out
    if seat == st["turn"]:
        st["turn"], st["again"] = next_seat(st, seat), False
        events.append("TURN %d" % st["turn"])
    return out

# ---- a turn in words (the core's say request; docs/plugins.md) ----------------------------------------

INFO["say"] = True


def op_say(req):
    """A turn in a few words, from its events: "drew a1-b1 and took 2 boxes"."""
    for e in req.get("events") or []:
        p = e.split()
        if p[0] == "LINE" and len(p) >= 3 and p[1] == str(req.get("seat")):
            boxes = len(p) - 4 if len(p) > 4 and p[3] == "BOX" else 0
            return {"say": "drew %s%s" % (p[2], " and took %d box%s" % (boxes, "" if boxes == 1 else "es")
                                           if boxes else "")}
    return {"say": ""}


OPS = {"info": op_info, "new": op_new, "view": op_view, "moves": op_moves, "apply": op_apply,
       "result": op_result, "drop": op_drop, "say": op_say}


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
