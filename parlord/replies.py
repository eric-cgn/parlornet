"""Reading the core's replies (docs/protocol.md): the data lines of LST, IVL, WHO, GAM, PFL, TOP and TIM, the
CLIENT lines and the invitation events, as the shared commands (parlord/commands.py) and the server
extensions (adapters/) read them. Pure functions of the lines, with nothing of a connection."""
import re


def err_text(final):
    """'ERR CODE text' -> 'text'."""
    parts = final.split(" ", 2)
    return parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "error")


def parse_tim(lines):
    """TIM's line -> (seconds signed in, the server's time "2026-11-24T21:05:33Z", seconds left, the call's
    seconds left, the player's month's seconds left), each of the last three None for no limit; or None when
    there is no well-formed TIM line."""
    for l in lines:
        m = re.match(r"^TIM (\d+) (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) left=(\d+|unl) call=(\d+|unl) "
                     r"month=(\d+|unl)\Z", l)
        if m:
            return (int(m.group(1)), m.group(2)) + tuple(None if g == "unl" else int(g) for g in m.groups()[2:])
    return None


def parse_games(lines):
    """GAM lines -> [{key: type/ver, rules, name, min, max}] (min and max are None if absent)."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "GAM" and len(p) >= 4:
            lo, hi = (int(p[4]), int(p[5])) if len(p) >= 6 and p[4].isdigit() and p[5].isdigit() else (None, None)
            out.append({"key": p[1], "rules": p[2], "name": p[3].replace("_", " "), "min": lo, "max": hi})
    return out


def parse_tables(lines):
    """TAB lines -> list of dicts ("vis" is "public" or "private"). "clients": {seat: what its player plays
    with} from the CLIENT lines after a TAB line (LST clients), {} without them."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "CLIENT" and out:
            c = parse_client(l)
            if c:
                out[-1]["clients"][c[0]] = c[1]
        if p and p[0] == "TAB" and len(p) >= 7:
            filled, seats = p[4].split("/")
            seated = p[6].split(",")
            turn = next((int(f[5:]) for f in p[7:] if f.startswith("turn=") and f[5:].isdigit()), 0)
            vis = next((f[4:] for f in p[7:] if f.startswith("vis=")), "public")
            out.append({"id": p[1], "key": p[2], "rules": p[3], "filled": int(filled),
                        "seats": int(seats), "status": p[5], "seated": seated,
                        "names": [n for n in seated if n != "-"],
                        "seat_of": {n: i for i, n in enumerate(seated) if n != "-"}, "turn": turn, "vis": vis,
                        "clients": {}})
    return out


def parse_invites(lines):
    """IVT lines (the reply to IVL) -> list of dicts; "seat" is -1 when no seat is held."""
    out = []
    for l in lines:
        p = l.split()
        if p and p[0] == "IVT" and len(p) >= 6:
            seat = next((int(f[5:]) for f in p[6:] if f.startswith("seat=") and f[5:].isdigit()), -1)
            out.append({"id": p[1], "key": p[2], "rules": p[3], "from": p[4], "status": p[5], "seat": seat})
    return out


def parse_players(lines):
    """PLR lines (the reply to WHO) -> [(name, is a bot)], in the core's order."""
    return [(p[1], "bot" in p[2:]) for p in (l.split() for l in lines) if len(p) >= 2 and p[0] == "PLR"]


def parse_profile(lines):
    """PFL lines (the reply to PFL name) -> {"name", "pref", "games": [(type/ver, n)], "progs": [(text, n)],
    "machs": [(text, n)], "score"}: score is parse_score's dict, or None when the server sent no score."""
    out = {"name": "", "pref": "", "games": [], "progs": [], "machs": [], "score": None}
    for l in lines:
        p = l.split(" ", 3)
        if p[0] != "PFL" or len(p) < 3:
            continue
        if p[1] == "NAME":
            out["name"] = p[2]
        elif p[1] == "PREF":
            out["pref"] = l.split(" ", 2)[2]
        elif p[1] == "GAME" and len(p) == 4 and p[3].isdigit():
            out["games"].append((p[2], int(p[3])))
        elif p[1] in ("PROG", "MACH") and len(p) == 4 and p[2].isdigit():
            out["progs" if p[1] == "PROG" else "machs"].append((p[3], int(p[2])))
        elif p[1] == "SCORE":
            out["score"] = parse_score(l.split()[2:])
    return out


SCORE_FIELDS = ("points", "wins", "draws", "losses", "resigns")


def parse_score(words):
    """points wins draws losses resigns [rank] -> {"points", "wins", "draws", "losses", "resigns", "rank"
    (None when unranked)}, or None."""
    if len(words) < 5 or not all(w.isdigit() for w in words[:5]):
        return None
    out = dict(zip(SCORE_FIELDS, (int(w) for w in words[:5])))
    out["rank"] = int(words[5]) if len(words) > 5 and words[5].isdigit() else None
    return out


def parse_top(lines):
    """TOP lines (the reply to TOP) -> [{"rank", "name", "points", "wins", "draws", "losses", "resigns"}]."""
    out = []
    for l in lines:
        p = l.split()
        if len(p) == 8 and p[0] == "TOP" and p[1].isdigit():
            row = parse_score(p[3:])
            if row:
                row.update(rank=int(p[1]), name=p[2])
                out.append(row)
    return out


def parse_client(text):
    """'CLIENT seat text' (an EVT payload, or a line of VUE C or LST clients) -> (seat, text), text being
    what the seat's player plays with in words ("with GameMaster 1.0.0 on a Macintosh Plus"); None for any
    other line, and for a CLIENT with nothing to say (a private table's, for someone who may not see it)."""
    p = text.split(" ", 2)
    if len(p) == 3 and p[0] == "CLIENT" and p[1].isdigit() and p[2].strip():
        return int(p[1]), p[2].strip()
    return None


def parse_clients(lines):
    """The CLIENT lines of a view (VUE C) -> {seat: text}."""
    return dict(c for c in (parse_client(l) for l in lines) if c)


def pending_invites(events):
    """EVT payloads in order -> names invited and not yet seated or withdrawn."""
    out = []
    for text in events:
        p = text.split()
        if len(p) >= 3 and p[0] == "INVITE" and p[2] not in out:
            out.append(p[2])
        elif len(p) >= 3 and p[0] in ("UNINVITE", "JOIN") and p[2] in out:
            out.remove(p[2])
    return out
