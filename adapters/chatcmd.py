"""Chat commands for every chat-style console (docs/chat-commands.md).

A chat-style console is a place where a player types lines to the server: a lobby chat (Outland's
Backgammon lobby, Outland Reversi's public talk), a game's chat box. Every such console takes the same
commands, with one meaning and one help text each. The commands themselves are parlord/commands.py, the one
implementation that the core also runs for any frontend with the request CMD (docs/protocol.md, Commands).
Its docstring describes the interface: the command set, parse, help_lines, Console and its hooks. A console
says which commands it offers and supplies its own way of doing the rest.

This module adds what only the consoles need: the lines that tell a console's player what each player plays
with (the CLIENT event that follows JOIN, and LST clients; docs/protocol.md, Clients and machines).

    join_says(lines), client_of(args), change_of(kind, args, says, mine), parse_clients(lines),
    joined(name, say, verb, width), client_change(name, say, width)
"""
from parlord.commands import (ALIASES, COMMANDS, IDLE_SECS, NAME_RE, NAMES, NEEDS_NAME, NO_SCORES,  # noqa: F401
                              NOT_HERE, RESIGN_SECS, TABLE_RE, TOP_ROWS, USAGE, Command, Console, clock_text,
                              commands_line, framed_motd, game_lines, help_lines, names_with, note_lines,
                              open_lines, parse, time_text, who_plays, wrap)
from parlord.replies import parse_client, parse_clients, parse_tables  # noqa: F401

from .common import client_change_sentence, client_sentence


# ---- what each player plays with (the CLIENT event and LST clients; docs/protocol.md, Clients and machines) --

def client_of(args):
    """The words after an event's kind -> (seat, what its player plays with) for a CLIENT event, or None (a
    bare CLIENT seat, at a private table the reader may not see, says nothing)."""
    return parse_client("CLIENT " + " ".join(args)) if args else None


def join_says(lines):
    """EVT lines (or their payloads, "JOIN 1 ann") in order -> {seat: text} for each CLIENT that comes right
    after its seat's JOIN: what the one who sat down plays with, for the line that tells of the JOIN. The
    core sends the two together, so they are in one reply."""
    out, last = {}, None
    for l in lines:
        p = l.split(" ", 3) if l.startswith("EVT ") else ["EVT", "", ""] + [l]
        if len(p) < 4:
            continue
        payload = p[3]
        f = payload.split()
        if f and f[0] == "CLIENT" and last is not None and f[1:2] == [last]:
            c = parse_client(payload)
            if c:
                out[c[0]] = c[1]
        last = f[1] if len(f) > 2 and f[0] == "JOIN" else None
    return out


def change_of(kind, args, says, mine=-1):
    """An event (kind and the words after it) -> (seat, text) when it is a CLIENT that tells of a seat's
    program changing: not one that follows its seat's JOIN in the same reply (says, from join_says), not the
    console's own seat (mine), and not a bare one. None otherwise."""
    c = client_of(args) if kind == "CLIENT" else None
    if c is None or c[0] == mine or says.get(c[0]) == c[1]:
        return None
    return c


def joined(name, say, verb="sits down", width=0):
    """'ann sits down with GameMaster 1.0.0 on a Macintosh Plus.' ('ann sits down.' when say is ""), cut to
    width (adapters/common.py client_sentence)."""
    return client_sentence(name, say, verb, width)


def client_change(name, say, width=0):
    """'ann now plays with RSCARDS 5.02 on an IBM PC.', cut to width."""
    return client_change_sentence(name, say, width)
