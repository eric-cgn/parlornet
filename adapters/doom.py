"""DOoM extension: ParlorNet's Grid Words for the clients of DOoM, John Chew's crossword-game server.

ParlorNet is not DOoM and is not affiliated with it. DOoM (the "Distributed Object-oriented Mud", written in
Perl by John Chew from late 1993) ran public crossword-game servers that people reached by telnet, with MUD
clients, or with Mark Watkins' BobGrid, a graphical front end in Java (1999). This extension speaks DOoM's
command language and output so that those programs, and people typing DOoM commands at a terminal, can play
ParlorNet's Grid Words (gridwords/1) against anyone on the parlor. Its banner, help and about texts say what it
is; it never connects anywhere but the core.

What it follows: DOoM's help files and English message
catalog for the commands and the wording, and BobGrid 2.3's class files, read statically, for the lines a
client parses: the board drawing (a column guide starting "   a b c d e f g", a border, fifteen rows with the
square for column c at character 2c+1 and the game's state from character 38, a border and the guide
again), the tagged log and tile-tracking outputs between "[BEGIN]" and "[END]", and the lines that make it
redraw ("starts the clock.", "plays out.", "ends the game."). Where the sources do not settle a format, this
module keeps to the closest reading of them.

The world is small: one room, the Gaming Room, and a board for every Grid Words table that is open or being
played, called bd_<table id>. `join bd_12` (or `go bd_12`, or `join <player>`) goes to a board, which
watches it; `join` at a board sits down (JON). The game starts when every seat is taken. `place` checks a
play with the core (CHK) and shows its score; `commit` plays it (MOV). `newboard` makes a table, with the
classic ruleset (six scoreless turns in a row end the game, as on DOoM) when the server has it. Invitations
are ParlorNet's own addition, as on the FIBS extension: `invite <name>` (the bot too), `accept <name>`,
`decline <name>`; invitations made anywhere else arrive as a line of text.

Sign-in: `connect name password` (or `co`), as on DOoM. The name alone goes to the core; the password
answers its credential question (common.sign_in_lines). `request name` signs up, with the core's questions
as prompts. Before signing in a connection is a numbered connector that can read help and quit.

    python3 -m adapters.doom --core 127.0.0.1:6590 --port 6507 --host 0.0.0.0
"""
import argparse
import asyncio
import itertools
import random
import re
import sys
import time

from . import botflag, declare
from .common import (LOGIN_SECS, LOGIN_TOTAL_SECS, CoreClient, CoreError, Gate, keepalive, describe_invite_event,
                     client_change_sentence, client_sentence, err_text, parse_client, framed_motd, loggable, parse_invites, parse_players, parse_tables, peer_addr,
                     sign_in_lines)
from .games import gridwords as G
from .telnet import TelnetIO

GW = "gridwords/1"
RULES = ("classic", "english")      # newboard uses the first of these the server has
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
MAX_SESSIONS = 60
POLL_SECS = 1.0             # each session's board
HUB_SECS = 3.0              # the shared table and player lists (presence)
INVITE_POLLS = 3            # invitations made elsewhere: every third poll
MAX_BUFFER = 256 * 1024
MAX_LINE = 400
MAX_LOGIN_TRIES = 3
PROMPT_WAIT = 120.0         # a sign-up answer typed at a prompt
SERVER = "parlornet"        # the home server in object names, until the core's greeting names it
ROOM = "Gaming Room"
WHERE = "on the web or by telnet"
SIZE = 15
COLS = "abcdefghijklmno"

BANNER = ["",
          "This is ParlorNet's DOoM-protocol service for crossword-game clients. It is",
          "not DOoM: it speaks DOoM's commands so that BobGrid, MUD clients and telnet",
          "programs can play ParlorNet's Grid Words against anyone on this server.",
          "",
          "To log on:  connect name password",
          "New here?   request name   (the server then asks what it needs)",
          "Enter `help intro' for introductory help, or `quit' to leave.",
          ""]
BOT_HOWTO = "To play the bot, type invite bot, and the bot joins you on a new board within a few seconds."
ABOUT = ["This is ParlorNet's DOoM-protocol service. ParlorNet is not DOoM and is not affiliated with",
         "it; DOoM was John Chew's crossword-game server (1993 to about 2005). Games here are Grid",
         "Words, hosted by ParlorNet and unrated."]

HELP = {
    "intro": ["help intro", "",
              "This server speaks the commands of DOoM, a crossword-game server of the 1990s, so that",
              "its clients can play ParlorNet's Grid Words. There is one room, the Gaming Room, and a",
              "board for every Grid Words table on the server, named bd_ and the table's number.",
              "`look' lists the boards. `join bd_12' goes to a board, where you watch the game, and",
              "`join' there sits you down. A game starts when every seat is taken.",
              "", "See also: play1 play2 player1 invite"],
    "index": ["help index", "", "Help is available for:", "",
              "about    connect  intro    invite   place    play1    play2    player1  player2"],
    "connect": ["help connect", "",
                "Usage: connect <name> <password>",
                "       co <name> <password>", "",
                "The password is your ParlorNet password. With a password the server emailed you,",
                "you are then asked to choose your own. To make an account, type `request <name>' and",
                "answer the questions, or sign up on the web or by telnet."],
    "play1": ["help play1", "",
              "`newboard' makes a board for a new game and sits you at it (`newboard 3' for three",
              "players, `newboard 2 10m' for ten minutes a turn). `invite <name>' asks someone to it,",
              "and `invite bot' asks the server's bot. Anyone can `join bd_<number>' to watch, and",
              "`join' there to sit down in a free seat. The game starts when every seat is taken.",
              "", "(continued in `help play2')"],
    "play2": ["help play2 (continued from `help play1')", "",
              "On your turn, `place' a word (see `help place'): the server shows what it scores.",
              "`commit' plays it, and `undo' takes it back. Instead, `exchange <tiles>' (`?' is a",
              "blank) or `pass'. `b' shows the board, `track' the unseen tiles, `time' the scores and",
              "the clock, `log' and `log2' the game so far. `resign' gives the game up.",
              "Every word is checked when it is played, so there is nothing to challenge. Six",
              "scoreless turns in a row end a game (on boards made elsewhere, two full rounds).",
              "", "See also: place play1"],
    "place": ["help place", "",
              "Usage: place <starting-square> <word>",
              "       place <word> <starting-square>",
              "       place <word> a|d <starting-square>", "",
              "Rows are labeled 1-15, columns A-O.  In the first and second forms, give the row",
              "first (8D) for a word across and the column first (D8) for a word down.  In the",
              "third, say `a' for across or `d' for down.  Write out the whole word, letters",
              "already on the board included.  In a word in MiXeD CAse the lower-case letters are",
              "blanks; a letter after _ or -, or in ( ), is a blank too.",
              "E.g. place 8d MUZJiKS, place ill(i)quid i8"],
    "invite": ["help invite", "",
               "Invitations are ParlorNet's own addition to the DOoM commands.",
               "invite <name>     ask a player to your board (a new board if you are not at one)",
               "accept <name>     sit down at the board <name> invited you to",
               "decline <name>    decline an invitation", "",
               "`invite bot' asks the server's bot, which plays at once. Invitations made on the web",
               "or by telnet arrive here as a line of text."],
    "player1": ["help player1", "",
                "Command  Description",
                "\"        Speak to everyone in the room (at a board: the table's chat).",
                "'        Speak privately to a player: 'name text.",
                ":        Announce an action to everyone in the room.",
                "beep     Ring a player's bell.",
                "connect  Connect to the server with a name and password.",
                "flip     Flip a coin.",
                "go       Go to a board, or to the Gaming Room.",
                "help     List help files.",
                "home     Go back to the Gaming Room.",
                "join     Go to a board or player; at a board, sit down.",
                "", "(continued in `help player2')"],
    "player2": ["help player2 (continued from `help player1')", "",
                "Command  Description",
                "kibitz   Speak at a board without sitting down (k for short).",
                "log      Look at a board's log.  log2: the same in two columns.",
                "look     Look at the room or a board.",
                "ospd     Check words in this server's word list (also ods, osw, sowpods).",
                "option   Set or display an option.",
                "page     Speak privately to a player.",
                "quit     Disconnect from the server.",
                "say      Speak to everyone in the room.",
                "who      List the players who are online.",
                "about    What this server is."],
}
HELP["here"] = HELP["play2"]
HELP["about"] = ["help about", ""] + ABOUT

# option name -> (default, description), in DOoM's order (help options)
OPTIONS = [("beep", True, "allow server to beep at you"),
           ("dots", False, "display dots on blank board squares"),
           ("loginwatch", False, "notify you whenever someone logs onto the server"),
           ("rack", False, "display large-size rack of tiles"),
           ("rated", False, "play games for ratings (games here are unrated)"),
           ("sort", True, "automatically sort racks"),
           ("timed", False, "penalize overtime (each turn here has the table's time limit)")]

NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven"]

ids = itertools.count(1)


class Refused(Exception):
    pass


def log(sid, text):
    print("doom[%d] %s" % (sid, text), flush=True)


def board_name(tid):
    return "bd_%s" % tid


def number(n):
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)


def tiles_word(n):
    return "%s tile%s" % (number(n), "" if n == 1 else "s")


def doom_rack(rack):
    """A rack as DOoM shows it: '?' for a blank."""
    return (rack or "").replace("_", "?")


def square_text(x, y, d):
    """The start square in DOoM's form: the row first (8D) for a word across, the column first (D8) for a
    word down."""
    return ("%d%s" % (y, COLS[x - 1].upper())) if d == "H" else ("%s%d" % (COLS[x - 1].upper(), y))


# ---- place: DOoM's word syntax -> PLAY tokens ------------------------------------------------

SQUARE_RE = re.compile(r"^(?:(1[0-5]|[1-9])([a-oA-O])|([a-oA-O])(1[0-5]|[1-9]))\Z")


def parse_square(text):
    """'8d' -> (4, 8, 'H') (row first: across); 'd8' -> (4, 8, 'V') (column first: down); else None."""
    m = SQUARE_RE.match(text)
    if not m:
        return None
    if m.group(1):
        return COLS.index(m.group(2).lower()) + 1, int(m.group(1)), "H"
    return COLS.index(m.group(3).lower()) + 1, int(m.group(4)), "V"


def parse_word(text):
    """A word as DOoM's place takes it -> the letters in ParlorNet's form ('_' before a blank's letter), or
    None. In MiXeD case the lower-case letters are blanks; a letter after '_' or '-', or inside '(' ')', is
    a blank whatever its case."""
    letters = [c for c in text if c.isalpha()]
    if not letters or not all("a" <= c.lower() <= "z" for c in letters):
        return None
    mixed = any(c.islower() for c in letters) and any(c.isupper() for c in letters)
    out, mark, paren = [], False, False
    for c in text:
        if c in "_-":
            if mark:
                return None
            mark = True
        elif c == "(":
            if paren:
                return None
            paren = True
        elif c == ")":
            if not paren:
                return None
            paren = False
        elif c.isalpha():
            blank = mark or paren or (mixed and c.islower())
            out.append(("_" if blank else "") + c.upper())
            mark = False
        else:
            return None
    if mark or paren:
        return None
    return "".join(out)


def parse_place(args):
    """The words after place -> (x, y, 'H'|'V', letters) or None. The three forms of help place:
    <square> <word>, <word> <square>, <word> a|d <square>."""
    if len(args) == 2:
        sq, word = (args[0], args[1]) if parse_square(args[0]) else (args[1], args[0])
        where, letters = parse_square(sq), parse_word(word)
        if where and letters:
            return where + (letters,)
        return None
    if len(args) == 3 and args[1].lower() in ("a", "d", "across", "down"):
        where, letters = parse_square(args[2]), parse_word(args[0])
        if where and letters:
            return where[0], where[1], "H" if args[1].lower()[0] == "a" else "V", letters
    return None


def shown_word(letters):
    """'MUZJ_IKS' -> 'MUZJiKS' (the blanks in lower case, as DOoM writes them)."""
    out, blank = [], False
    for c in letters:
        if c == "_":
            blank = True
        else:
            out.append(c.lower() if blank else c)
            blank = False
    return "".join(out)


# What the core says about a refused play -> DOoM's message for it (eng-gcgbplac* in the catalog), where
# DOoM had one; else the core's reason as a sentence.
def refusal_text(reason, x=None, y=None, d=None):
    r = reason
    m = re.match(r"your rack has no (blank|[A-Z])", r)
    if m:
        return "You don't have that many blanks." if m.group(1) == "blank" else \
            "You would need another `%s' to play that word." % m.group(1)
    m = re.match(r"([a-o])(\d+) already holds ([A-Z])", r)
    if m:
        return "There is already a `%s' on %s%s." % (m.group(3), m.group(1).upper(), m.group(2))
    if r.startswith("the word runs off the board"):
        return "That would go off the %s edge of the board." % ("right" if d == "H" else "bottom")
    if r.startswith("the tiles just before"):
        return "There is a tile on the board before that word."
    if r.startswith("the tiles just after"):
        return "There is a tile on the board after that word."
    if r.startswith("the first word must cover"):
        return "Your first play must cross H8."
    if r.startswith("the first word needs two letters") or r.startswith("a play must make a word"):
        return "Words must be at least two letters long."
    if r.startswith("the word must join"):
        return "That word would not touch any others."
    if r.startswith("not in the word list: "):
        return "Not in this server's word list: %s." % ", ".join(r[22:].split())
    if r.startswith("not your turn"):
        return "It's not your turn."
    return r[:1].upper() + r[1:] + ("" if r.endswith(".") else ".")


# ---- the board, as DOoM drew it ------------------------------------------------------------
# BobGrid reads: a line starting "   a b c d e f g" (the column guide), then one line it skips (the top
# border), fifteen rows (the square for column c at character 2c+1; a letter is a tile, lower case a blank,
# anything else an empty square), and two more lines (the bottom border and the guide). On the rows, from
# character 38: row 2 and 3 the players (name, own rack, score, time), row 5 "Waiting for <name> to play.",
# row 6 (shown as it is), row 7 "... running." or not, row 9 "Time penalties are in effect" or not.

GUIDE = "   " + " ".join(COLS)
BORDER = "  +" + "-" * 29 + "+"
# The marks of empty squares: DOoM's own are UNVERIFIED (a board's premiums were set by its owner with
# tws, dws, tls and dls); these are the ones BobGrid's coloring reads.
PREMIUM_MARK = {"TW": "=", "DW": "-", "TL": '"', "DL": "'"}
INFO_AT = 38


def clock_field(secs):
    """Seconds -> DOoM's time field, five characters 'MM:SS' (at most 99:59)."""
    secs = max(0, min(int(secs), 99 * 60 + 59))
    return "%2d:%02d" % (secs // 60, secs % 60)


def board_lines(v, names, seat, title="", rack=None, dots=False, extra=()):
    """A gridwords/1 view (adapters/games/gridwords.parse_view) -> the board display: 19 lines. seat: the
    viewer's seat (-1: watching); rack: the viewer's rack in the order to show (else the view's)."""
    np = max(len(names), len(v["scores"]), 2)
    info = [""] * 16
    info[1] = title
    turn = v["turn"]
    clock = v.get("clock")
    for s in range(np):
        row = (2, 3, 10, 11)[s] if s < 4 else None
        if row is None:
            continue
        name = names[s] if s < len(names) and names[s] != "-" else "(empty)"
        score = v["scores"][s] if s < len(v["scores"]) else 0
        own = ""
        if s == seat and v["rows"]:
            own = doom_rack(rack if rack is not None else v["rack"])
        if clock and clock[0] == s:
            t = clock_field(clock[1])
        elif clock:
            t = clock_field(clock[2])
        else:
            t = clock_field(0)
        info[row] = "%-12s %-7s %4d %s" % (name, own, score, t)
    if not v["rows"]:
        info[5] = "Waiting for players to join."
    elif turn < 0:
        info[5] = "The game is over."
    else:
        info[5] = "Waiting for %s to play." % (names[turn] if turn < len(names) else "seat %d" % turn)
    if v["rows"]:
        info[6] = "The bag contains %d tile%s." % (v["bag"], "" if v["bag"] == 1 else "s")
        info[7] = "The clock is running." if turn >= 0 else "The clock is paused."
    info[8] = "This game does not count for ratings."
    info[9] = "Time penalties are not in effect."
    if v.get("last"):
        s, x, y, d, word, pts = v["last"]
        who = names[s] if s < len(names) else "seat %d" % s
        info[13] = ("Last: %s %s %s %d" % (who, word, square_text(x, y, d), pts))[:40]
    for i, text in enumerate(extra):
        if 14 + i < 16:
            info[14 + i] = text[:40]
    lines = [GUIDE, BORDER]
    for y in range(1, SIZE + 1):
        cells = []
        for x in range(1, SIZE + 1):
            c = v["tiles"].get((x, y))
            if c:
                cells.append(c)
            else:
                k = v["prm"].get((x, y))
                cells.append(PREMIUM_MARK.get(k, "." if dots else " "))
        row = "%2d|%s|%-2d" % (y, " ".join(cells), y)
        lines.append((row.ljust(INFO_AT) + info[y]).rstrip() if info[y] else row)
    lines += [BORDER, GUIDE]
    return lines


def unseen(v, seat):
    """The tiles the viewer has not seen: the whole set less the board and the viewer's own rack (a blank on
    the board counts as a blank), sorted, '?' for blanks."""
    left = {c: n for c, (_, n) in v["til"].items()}
    for c in v["tiles"].values():
        k = "_" if c.islower() else c
        left[k] = left.get(k, 0) - 1
    if seat >= 0 and v["rack"]:
        for c in v["rack"]:
            left[c] = left.get(c, 0) - 1
    return "".join(c * max(0, n) for c, n in sorted(left.items(), key=lambda e: (e[0] == "_", e[0]))
                   ).replace("_", "?")


TRACK_LABEL = "Unseen tiles: "     # 14 characters: BobGrid keeps what follows character 14
TRACK_WIDTH = 64                    # letters on a line (14 + 64 = 78)
TEXT_WIDTH = 78                     # a line of text, such as what a player plays with


def track_lines(v, seat, tagged=False):
    """track: 'Unseen tiles: ' and the letters (A-Z, '?' for blanks, nothing else: BobGrid counts every
    character after the label), with at most one more line indented to the same place."""
    letters = unseen(v, seat)
    out = [TRACK_LABEL + letters[:TRACK_WIDTH]]
    if len(letters) > TRACK_WIDTH:
        out.append(" " * len(TRACK_LABEL) + letters[TRACK_WIDTH:])
    return ["[BEGIN]"] + out + ["[END]"] if tagged else out


# ---- the game log ------------------------------------------------------------------------------

def game_moves(events):
    """A table's events (EVT payloads, in order) -> (names by seat, entries). An entry is a dict: seat,
    kind (play, exch, pass, adj), and for a play x, y, d, word, points; for an exchange n; for an
    adjustment points and the tiles left ('-' for a player who went out)."""
    names, out = {}, []
    for text in events:
        p = text.split()
        try:
            if p[0] == "JOIN" and len(p) >= 3:
                names[int(p[1])] = p[2]
            elif p[0] == "PLAY" and len(p) == 7:
                out.append({"seat": int(p[1]), "kind": "play", "x": int(p[2]), "y": int(p[3]), "d": p[4].upper(),
                            "word": p[5], "points": int(p[6])})
            elif p[0] == "EXCH" and len(p) == 3:
                out.append({"seat": int(p[1]), "kind": "exch", "n": int(p[2]), "points": 0})
            elif p[0] == "PASS" and len(p) == 2:
                out.append({"seat": int(p[1]), "kind": "pass", "points": 0})
            elif p[0] == "ADJ" and len(p) == 4:
                out.append({"seat": int(p[1]), "kind": "adj", "points": int(p[2]), "left": p[3]})
        except (ValueError, IndexError):
            continue
    np = max(list(names) + [e["seat"] for e in out] + [1]) + 1
    return [names.get(s, "seat%d" % (s + 1)) for s in range(np)], out


def coord_text(x, y, d):
    """log2's coordinate, in lower case: row first across (8d, 12c), column first down (h4, c12)."""
    return square_text(x, y, d).lower()


def log2_column(e, total):
    """One player's entry in log2, 38 characters, as BobGrid reads it: the rack [0:7] (not kept here: shown as
    dashes, which BobGrid takes for a hidden rack), the coordinate from 8, the word from 14 (a '-' there is an
    exchange or a pass), the score right-aligned in [29:33] and the running total in [34:38]. An end-of-game
    adjustment has its tiles in brackets at 14, which BobGrid's log display looks for."""
    rack = "-" * 7
    if e["kind"] == "play":
        return "%-7s %-3s   %-15s%4s %4d" % (rack, coord_text(e["x"], e["y"], e["d"]), e["word"],
                                             "+%d" % e["points"], total)
    if e["kind"] == "exch":
        return "%-7s %-3s   %-15s%4s %4d" % (rack, "", "-%d" % e["n"], "0", total)
    if e["kind"] == "pass":
        return "%-7s %-3s   %-15s%4s %4d" % (rack, "", "-", "0", total)
    left = "" if e["left"] == "-" else doom_rack(e["left"])
    return "%-7s %-3s   %-15s%4s %4d" % (rack, "-", ("(%s:%+d)" % (left, e["points"]))[:15], "", total)


def ordered_for_log2(entries, np):
    """The entries in the order of the log's slots: moves as played; then the end-of-game adjustments, the
    one for the seat whose slot comes next first, so the slots keep alternating seat by seat."""
    moves = [e for e in entries if e["kind"] != "adj"]
    adj = {e["seat"]: e for e in entries if e["kind"] == "adj"}
    nxt = (moves[-1]["seat"] + 1) % np if moves else 0
    return moves + [adj[(nxt + i) % np] for i in range(np) if (nxt + i) % np in adj]


def log2_lines(names, entries, tagged=False):
    """log2: a header with the two players' names (at 0 and 40), then a line per round, the first player's
    entry in the left column and the second's in the right; tagged puts it between [BEGIN] and [END]."""
    if len(names) > 2:
        out = ["This game has too many players for this log format."]
    else:
        out = ["%-40s%s" % (names[0], names[1] if len(names) > 1 else "")]
        totals = [0, 0]
        cols = []
        for e in ordered_for_log2(entries, 2):
            totals[e["seat"]] += e["points"]
            cols.append(log2_column(e, totals[e["seat"]]))
        for i in range(0, len(cols), 2):
            out.append(cols[i] + ("  " + cols[i + 1] if i + 1 < len(cols) else ""))
    return ["[BEGIN]"] + out + ["[END]"] if tagged else out


def log_lines(names, entries):
    """log: one line per turn, with the player's name and running total."""
    if not entries:
        return ["The log is empty."]
    totals = [0] * len(names)
    out = []
    for i, e in enumerate(entries, 1):
        s = e["seat"]
        totals[s] += e["points"]
        who = names[s] if s < len(names) else "seat%d" % (s + 1)
        if e["kind"] == "play":
            what = "%-4s %-15s %+4d" % (square_text(e["x"], e["y"], e["d"]), e["word"], e["points"])
        elif e["kind"] == "exch":
            what = "exchanges %s" % tiles_word(e["n"])
        elif e["kind"] == "pass":
            what = "passes"
        elif e["left"] == "-":
            what = "%-20s %+4d" % ("(from the other racks)", e["points"])
        else:
            what = "%-20s %+4d" % ("(%s left)" % doom_rack(e["left"]), e["points"])
        out.append("%3d %-12s %-26s %4d" % (i, who, what, totals[s]))
    return out


# ---- the word list, for ospd and the other dictionary commands ----------------------------------

_lexicon = {}


def word_list():
    """The server's Grid Words word list, read with the plugin's own reader (so GRIDWORDS_WORDS applies), or
    None where this copy of ParlorNet has no plugin to read it with."""
    if "module" not in _lexicon:
        try:
            import importlib.util
            import os
            root = os.environ.get("PARLOR_PLUGINS") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
            spec = importlib.util.spec_from_file_location("pndoom_gridwords",
                                                          os.path.join(root, "gridwords", "plugin.py"))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _lexicon["module"] = m
        except Exception:
            _lexicon["module"] = None
    m = _lexicon["module"]
    try:
        return m.load_words() if m else None
    except Exception:
        return None


# ---- everyone connected here, and the core's lists -------------------------------------------------

class Hub:
    def __init__(self):
        self.sessions = {}      # lower name -> Session (signed in)
        self.connectors = set()  # sessions not signed in yet
        self.tables = []
        self.online = []        # the last WHO: (name, idle secs, bot), with one bot (botflag.listed)
        self.bots = set()
        self.who_ok = False
        self.present = {}
        self.rules = None       # the Grid Words rulesets the server has (GAM), in its order
        self.task = None
        self.turn = 0

    def everyone(self):
        if self.who_ok:
            out = {n.lower(): n for n, _, _ in self.online}
        else:
            out = {n.lower(): n for t in self.tables for n in t["names"]}
        for k, s in self.sessions.items():
            out[k] = s.name
        return out

    def idle_of(self, name):
        return next((i for n, i, _ in self.online if n.lower() == name.lower()), 0)

    def sync_presence(self, silent=False):
        now = self.everyone()
        new = [n for k, n in now.items() if k not in self.present]
        gone = [n for k, n in self.present.items() if k not in now]
        self.present = now
        if silent:
            return
        for s in list(self.sessions.values()):
            s.presence(new, gone)

    async def poll(self, via, silent=False):
        data, final = await via.core_req("LST all")
        if final.startswith("OK"):
            self.tables = parse_tables(data)
            await self.who(via)
            self.sync_presence(silent=silent)

    async def who(self, via):
        data, final = await via.core_req("WHO")
        if final.startswith("OK"):
            players = parse_players(data)
            shown = set(botflag.listed(players))
            idle = {}
            for l in data:
                f = l.split()
                if len(f) >= 3 and f[0] == "PLR":
                    idle[f[1]] = next((int(x[5:]) for x in f[2:] if x.startswith("idle=") and x[5:].isdigit()), 0)
            self.online = [(n, idle.get(n, 0), bot) for n, bot in players if n in shown]
            self.bots = {n.lower() for n, bot in players if bot}
            self.who_ok = True

    async def rulesets(self, via):
        if self.rules is None:
            data, final = await via.core_req("GAM")
            if final.startswith("OK"):
                self.rules = [l.split()[2] for l in data if l.split()[:2] == ["GAM", GW]]
        return self.rules or []

    async def run(self):
        while self.sessions:
            await asyncio.sleep(HUB_SECS)
            ss = list(self.sessions.values())
            if not ss:
                break
            via = ss[self.turn % len(ss)]
            self.turn += 1
            try:
                await self.poll(via)
            except (CoreError, OSError, asyncio.TimeoutError):
                via.closing = True
            except Exception as e:      # the shared poll must keep running
                print("doom hub error: %r" % e, file=sys.stderr, flush=True)
        self.task = None

    async def enter(self, s):
        try:
            if not self.sessions:
                self.present = {}
                await self.poll(s, silent=True)
            else:
                await self.who(s)
        except (CoreError, OSError, asyncio.TimeoutError):
            pass
        old = self.sessions.get(s.name.lower())
        if old and old is not s:
            old.post(["Switching to new connection."])
            old.closing = True
            old.io.w.close()
        self.sessions[s.name.lower()] = s
        self.sync_presence()
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.run())

    def leave(self, s):
        k = s.name.lower()
        if self.sessions.get(k) is s:
            del self.sessions[k]
        self.sync_presence()

    def boards(self):
        return [t for t in self.tables if t["key"] == GW and t["status"] in ("open", "playing")]

    def board_of(self, name):
        """The Grid Words table a player is seated at: a game being played first, then an open one."""
        k = name.lower()
        mine = [t for t in self.boards() if k in [n.lower() for n in t["names"]]]
        mine.sort(key=lambda t: (t["status"] != "playing", -int(t["id"]) if t["id"].isdigit() else 0))
        return mine[0] if mine else None

    def table(self, tid):
        return next((t for t in self.tables if t["id"] == tid), None)

    def near(self, s):
        """The other signed-in sessions in the same place as s (the room, or the same board)."""
        return [o for o in self.sessions.values() if o is not s and o.here == s.here]


# ---- one connection -------------------------------------------------------------------------------

class Session:
    def __init__(self, hub, io, core_addr, ticket=None):
        self.hub, self.io, self.core_addr, self.ticket = hub, io, core_addr, ticket
        self.addr = peer_addr(io.w)
        self.sid = next(ids)
        self.name = None
        self.core = None
        self.core_lock = asyncio.Lock()
        self.ev_lock = asyncio.Lock()
        self.here = None            # the table id of the board I am at, or None: the Gaming Room
        self.seat = -1              # my seat there (-1: watching)
        self.watching = False       # I watch it with SEE (and leave it with LVE)
        self.names = []
        self.clients = {}           # seat -> what its player plays with, in words (CLIENT events, LST clients)
        self.cursor = 0
        self.pending = None         # a placed play: {"x", "y", "d", "letters", "points", "words"}
        self.order = ""             # my rack in the order I arranged it ('_' a blank)
        self.options = {k: d for k, d, _ in OPTIONS}
        self.doing = ""
        self.own_cht = 0
        self.announced = {}
        self.closing = False

    # ---- output ----------------------------------------------------------------------------
    def post(self, lines):
        w = self.io.w
        if w.is_closing() or not lines:
            return
        w.write("".join(l + "\r\n" for l in lines).encode("ascii", "replace"))
        if w.transport.get_write_buffer_size() > MAX_BUFFER:
            self.closing = True
            w.close()

    async def send(self, lines):
        self.post(lines)
        await self.io.w.drain()

    async def core_req(self, text):
        async with self.core_lock:
            return await self.core.req(text)

    def presence(self, new, gone):
        if not self.name or not self.options["loginwatch"]:
            return
        me = self.name.lower()
        self.post(["%s connects." % n for n in new if n.lower() != me] +
                  ["%s disconnects." % n for n in gone if n.lower() != me])

    def beep(self):
        return "\a" if self.options["beep"] else ""

    # ---- connecting ------------------------------------------------------------------------
    async def readline(self):
        return await self.io.readline(maxlen=MAX_LINE)

    async def login_line(self):
        try:
            line = await asyncio.wait_for(self.readline(), LOGIN_SECS)
        except asyncio.TimeoutError:
            raise Refused("no login")
        if self.ticket is not None:
            self.ticket.touch()
        return line

    async def login(self):
        """The connector's commands until a connect or request signs in."""
        self.hub.connectors.add(self)
        try:
            await self.send(BANNER + ["You are connector #%d." % self.sid])
            tries = 0
            while True:
                words = (await self.login_line()).split()
                if not words:
                    continue
                cmd = words[0].lower()
                if cmd in ("connect", "co"):
                    log(self.sid, "connect %r (password not logged)" % (words[1] if len(words) > 1 else ""))
                    if len(words) < 3:
                        await self.send(["Usage: connect name password"])
                        continue
                    refusal = await self.sign_in(words[1], " ".join(words[2:]), "signin")
                elif cmd == "request":
                    log(self.sid, "request %r" % (words[1] if len(words) > 1 else ""))
                    if len(words) < 2:
                        await self.send(["Usage: request online-name"])
                        continue
                    refusal = await self.sign_in(words[1], None, "signup")
                elif cmd in ("quit", "@quit"):
                    raise Refused("quit")
                elif cmd in ("help", "?"):
                    await self.send(self.help_lines(words[1:]))
                    continue
                elif cmd == "about":
                    await self.send(ABOUT)
                    continue
                elif cmd in ("who", "who1", "who2"):
                    await self.send(self.connector_who())
                    continue
                else:
                    await self.send(["Huh?  Connect first: `connect name password', or `request name' to sign up."])
                    continue
                if refusal is None:
                    if self.ticket is not None and not self.ticket.sign_in():
                        await self.send(["The server is full. Try again soon."])
                        raise Refused("full")
                    self.name = self.core.name
                    return
                tries += 1
                log(self.sid, "refused: %s" % refusal)
                await self.send([refusal])
                if tries >= MAX_LOGIN_TRIES:
                    raise Refused(refusal)
        finally:
            self.hub.connectors.discard(self)

    async def sign_in(self, name, password, mode):
        """None when signed in, else what to say. The core drives the steps (common.sign_in_lines)."""
        if not NAME_RE.match(name):
            return "`%s' is not an acceptable player name." % name[:20]
        if self.core is None:       # only now, with a login line from the client, a connection to the core
            try:
                self.core = await CoreClient.connect(*self.core_addr)
                await self.core.source(self.addr)
                self.server = (self.core.greeting.split()[1:2] or [SERVER])[0]
            except (OSError, asyncio.TimeoutError, CoreError):
                self.core = None
                return "The game server isn't answering. Try again soon."
        unknown = "There is no player named %s here. To make an account, enter `request %s'." % (name, name)
        r = await sign_in_lines(self.core, name, password, self.prompt, self.show, mode, unknown)
        if r and "wrong password" in r.lower():
            return "That password and that name do not match."
        return r

    async def show(self, lines):
        await self.send(lines)

    async def prompt(self, label, secret=False):
        # the question ends its line: BobGrid reads whole lines only, and froze on a question without one
        self.io.w.write(("%s:\r\n" % label).encode("ascii", "replace"))
        await self.io.w.drain()
        try:
            line = (await asyncio.wait_for(self.readline(), PROMPT_WAIT)).strip()
        except asyncio.TimeoutError:
            return ""
        log(self.sid, "sign-in answer received (not logged)")
        if self.ticket is not None:
            self.ticket.touch()
        return line

    def connector_who(self):
        rows = ["Who          Idle  Where in VR"]
        for n, idle, bot in sorted(self.hub.online, key=lambda e: e[0].lower()):
            rows.append("%-12s %5s  %s" % (n, idle_text(idle), "(the server's bot)" if bot else ""))
        for c in sorted(self.hub.connectors, key=lambda c: c.sid):
            rows.append("%-12s %5s  %s" % ("#%d" % c.sid, "-", "the login area"))
        return rows

    async def welcome(self):
        motd = []
        try:
            motd = await self.core.motd()
        except CoreError:
            pass
        self.io.on_input = self.core.user_input     # every command typed from now on is activity
        await self.hub.enter(self)
        await self.hub.rulesets(self)
        lines = ["", "Welcome, %s. This is ParlorNet's crossword room; games here are unrated." % self.name]
        lines += framed_motd(motd) + [BOT_HOWTO, "Enter `help intro' to begin, `look' to see the boards, or `who' "
                                                 "for the players."]
        await self.send(lines)
        t = self.hub.board_of(self.name)
        if t and t["status"] == "playing":
            await self.go_to(t["id"])
        else:
            await self.send(self.room_lines())
        await self.check_invitations()

    def help_lines(self, args):
        topic = args[0].lower() if args else "intro"
        if topic in HELP:
            return HELP[topic]
        return ["Can't open that help file.", "Enter `help index' for a list of help topics."]

    # ---- the room and the boards ------------------------------------------------------------
    def room_lines(self):
        out = ["%s [%s:gaming]" % (ROOM, getattr(self, "server", SERVER)),
               "The boards here are the server's Grid Words tables. `join bd_<number>' goes to one."]
        boards = self.hub.boards()
        if not boards:
            out.append("There are no boards here yet. `newboard' sets one up.")
        for t in sorted(boards, key=lambda t: int(t["id"]) if t["id"].isdigit() else 0):
            who = ", ".join(n for n in t["seated"] if n != "-") or "nobody yet"
            what = "playing" if t["status"] == "playing" else "waiting for players"
            out.append("  %-8s %-8s %d/%d  %-20s %s" % (board_name(t["id"]), t["rules"], t["filled"], t["seats"],
                                                       what, who))
        others = [o.name for o in self.hub.near(self)]
        if others:
            out.append("Also here: %s." % ", ".join(others))
        return out

    def resolve(self, word, finished=False):
        """A board named in a command: bd_<id>, bd_<player> or a player's name -> (table, None) or (None,
        the reply). finished: a bd_<id> not in the list of open and running tables is taken as a finished game
        (for its log), and the core says whether there is one."""
        w = word.strip()
        low = w.lower()
        if low.startswith("bd_"):
            ref = w[3:]
            t = self.hub.table(ref) if ref.isdigit() else self.hub.board_of(ref)
            if t and t["key"] == GW and t["status"] in ("open", "playing"):
                return t, None
            if finished and ref.isdigit() and t is None:
                return {"id": ref}, None
            return None, "I don't know anything called `%s'." % w
        t = self.hub.board_of(w)
        if t:
            return t, None
        if low in self.hub.everyone():
            return None, "%s is nowhere to be found." % self.hub.everyone()[low]
        return None, "I don't know anything called `%s'." % w

    async def view(self, tid, flags=""):
        data, final = await self.core_req("VUE %s %s" % (tid, flags) if flags else "VUE %s" % tid)
        return G.parse_view(data) if final.startswith("OK") else None

    async def names_of(self, tid):
        """The names by seat, from LST (a table not listed: from its JOIN events)."""
        t = self.hub.table(tid)
        if t:
            return list(t["seated"])
        data, _ = await self.core_req("SIN %s 0" % tid)
        names = {}
        for l in data:
            p = l.split()
            if len(p) >= 6 and p[0] == "EVT" and p[3] == "JOIN" and p[4].isdigit():
                names[int(p[4])] = p[5]
        return [names.get(i, "-") for i in range(max(names) + 1)] if names else []

    async def leave_here(self):
        if self.here is None:
            return
        if self.watching:
            try:
                await self.core_req("LVE %s" % self.here)
            except CoreError:
                pass
        for o in self.hub.near(self):
            o.post(["%s goes to the %s." % (self.name, ROOM)])
        self.here, self.seat, self.watching, self.pending, self.names = None, -1, False, None, []
        self.clients = {}

    async def go_to(self, tid, quiet=False):
        """Go to a board: watch it (SEE) unless seated there; show it."""
        await self.hub.poll(self, silent=True)
        if self.here == tid:
            if not quiet:
                await self.send(["You are already here."])
            return True
        await self.leave_here()
        t = self.hub.table(tid)
        names = list(t["seated"]) if t else await self.names_of(tid)
        seat = next((i for i, n in enumerate(names) if n.lower() == self.name.lower()), -1)
        watching = False
        if seat < 0:
            _, final = await self.core_req("SEE %s" % tid)
            if final.startswith("ERR PRIVATE"):
                await self.send(["The room is locked: that game is by invitation only."])
                return False
            if not final.startswith("OK"):
                await self.send(["I don't know anything called `%s'." % board_name(tid)])
                return False
            watching = True
        self.here, self.seat, self.watching, self.names, self.pending = tid, seat, watching, names, None
        data, final = await self.core_req("LST table=%s clients" % tid)
        t = next((t for t in parse_tables(data) if t["id"] == tid), None) if final.startswith("OK") else None
        self.clients = dict(t["clients"]) if t else {}
        v = await self.view(tid, "G")
        self.cursor = v["seq"] if v else 0
        for o in self.hub.near(self):
            o.post(["%s arrives." % self.name])
        if not quiet:
            lines = ["You go to %s." % board_name(tid)]
            if v:
                lines += self.board_lines(v)
            lines += self.client_lines()
            lines.append(self.place_hint(v))
            await self.send(lines)
        return True

    def client_lines(self):
        """What each other player at this board plays with, a line each, after the board (BobGrid reads the
        board's lines and leaves these): "nate plays with BobGrid 2.3 on a Sun SPARCstation."."""
        return [client_sentence(self.who_at(s), self.clients[s], "plays", TEXT_WIDTH) for s in sorted(self.clients)
                if s != self.seat and s < len(self.names) and self.names[s] != "-"]

    def place_hint(self, v):
        if self.seat >= 0:
            if v and not v["rows"]:
                return "You are seated. The game starts when every seat is taken (`invite <name>' asks someone)."
            return "You are playing in this game."
        if v and not v["rows"]:
            return "Enter `join' to sit down in this game."
        return "You are watching this game. `kibitz' speaks without disturbing the players."

    def shown_rack(self, rack):
        """My rack in the order I arranged it (new tiles at the end, sorted with the sort option)."""
        rack = rack or ""
        if sorted(self.order) != sorted(rack):
            kept, left = [], list(rack)
            for c in self.order:
                if c in left:
                    left.remove(c)
                    kept.append(c)
            self.order = "".join(kept) + "".join(sorted(left) if self.options["sort"] else left)
        return self.order

    def board_lines(self, v, title=None):
        """The board display for this session: my seat, my rack in my order, a placed play on the board (as
        DOoM showed a placed word until it was committed or undone)."""
        title = title or "%s  Grid Words" % board_name(self.here)
        rack = None
        extra = []
        if self.seat >= 0 and v["rack"] is not None:
            rack = self.shown_rack(v["rack"])
            if self.pending:
                v = dict(v, tiles=dict(v["tiles"]))
                left = list(rack)
                dx, dy = (1, 0) if self.pending["d"] == "H" else (0, 1)
                i, blank = 0, False
                for c in self.pending["letters"]:
                    if c == "_":
                        blank = True
                        continue
                    x, y = self.pending["x"] + dx * i, self.pending["y"] + dy * i
                    i += 1
                    if (x, y) not in v["tiles"]:
                        v["tiles"][(x, y)] = c.lower() if blank else c
                        need = "_" if blank else c
                        if need in left:
                            left.remove(need)
                    blank = False
                rack = "".join(left)
                extra.append("Placed: %s for %d" % (shown_word(self.pending["letters"]), self.pending["points"]))
        return board_lines(v, self.names, self.seat, title, rack, self.options["dots"], extra)

    # ---- commands ----------------------------------------------------------------------------
    async def command(self, line):
        s = line.strip()
        if not s:
            return
        if s[0] in "\"'`:-+":
            await self.talk(s)
            return
        words = s.split()
        handler = COMMANDS.get(words[0].lower())
        if handler is None:
            await self.send(["Huh?  (Enter `help' for help.)"])
            return
        await handler(self, words[1:], s)

    async def cmd_help(self, args, line):
        if args and args[0].lower() == "here":
            await self.send(HELP["play2"] if self.here else HELP["intro"])
        else:
            await self.send(self.help_lines(args))

    async def cmd_about(self, args, line):
        await self.send(ABOUT)

    async def cmd_quit(self, args, line):
        for o in self.hub.near(self):
            o.post(["%s logs off from %s." % (self.name, getattr(self, "server", SERVER))])
        await self.send(["Goodbye. Thanks for playing on ParlorNet."])
        self.closing = True

    async def cmd_who(self, args, line):
        await self.hub.poll(self, silent=True)
        rows = ["Who          Idle  NetR  Where in VR      Doing in VR"]
        only = {a.lower() for a in args if not a.startswith("-")}
        for k, n in sorted(self.hub.everyone().items(), key=lambda e: e[1].lower()):
            if only and k not in only:
                continue
            s = self.hub.sessions.get(k)
            if k in self.hub.bots:
                where, doing = "(the server's bot)", "ready to play"
            elif s:
                where = board_name(s.here) if s.here else ROOM
                doing = s.doing or ("playing" if s.here and s.seat >= 0 else "kibitzing" if s.here else "")
            else:
                t = self.hub.board_of(n)
                where = board_name(t["id"]) if t else "elsewhere"
                doing = "playing on the web or telnet" if t else "on the web or telnet"
            idle = 0 if s else self.hub.idle_of(n)
            rows.append(("%-12s %5s  %4s  %-16s %s" % (n, idle_text(idle), "-", where, doing[:28])).rstrip())
        if not only:
            for c in sorted(self.hub.connectors, key=lambda c: c.sid):
                rows.append("%-12s %5s  %4s  %s" % ("#%d" % c.sid, "-", "-", "the login area"))
        await self.send(rows)

    async def cmd_look(self, args, line):
        what = " ".join(args)
        if what.lower() in ("", "here"):
            if self.here:
                v = await self.view(self.here, "G")
                if v:
                    await self.send(self.board_lines(v))
                    return
            await self.send(self.room_lines())
            return
        if what.lower() == "me":
            await self.send(["You are %s, in %s." % (self.name, board_name(self.here) if self.here else
                                                      "the %s" % ROOM)])
            return
        await self.hub.poll(self, silent=True)
        t, why = self.resolve(what)
        if t is None:
            await self.send([why])
            return
        await self.show_board(t["id"])

    async def show_board(self, tid):
        """A board's display, wherever I am (watching it for a moment if I am not there)."""
        if tid == self.here:
            v = await self.view(tid, "G")
            await self.send(self.board_lines(v) if v else ["I don't know anything called `%s'." % board_name(tid)])
            return
        names = await self.names_of(tid)
        seat = next((i for i, n in enumerate(names) if n.lower() == self.name.lower()), -1)
        if seat < 0:
            _, final = await self.core_req("SEE %s" % tid)
            if not final.startswith("OK"):
                await self.send(["I don't know anything called `%s'." % board_name(tid)])
                return
        v = await self.view(tid, "G")
        if seat < 0:
            await self.core_req("LVE %s" % tid)
        if not v:
            await self.send(["I don't know anything called `%s'." % board_name(tid)])
            return
        await self.send(board_lines(v, names, seat, "%s  Grid Words" % board_name(tid), None, self.options["dots"]))

    async def cmd_board(self, args, line):
        if args:
            await self.cmd_look(args, line)
            return
        if not self.here:
            await self.send(["That's not a board: you are in the %s. `join bd_<number>' goes to one." % ROOM])
            return
        v = await self.view(self.here, "G")
        if v:
            await self.send(self.board_lines(v))

    async def cmd_go(self, args, line):
        what = " ".join(args)
        if not what:
            await self.send(["Go where? `go bd_<number>', or `home' for the %s." % ROOM])
            return
        if what.lower() in ("home", "gaming", "room", "lobby", "out", ROOM.lower()):
            await self.cmd_home([], line)
            return
        await self.hub.poll(self, silent=True)
        t, why = self.resolve(what)
        if t is None:
            await self.send([why.replace("anything called", "anywhere named")])
            return
        await self.go_to(t["id"])

    async def cmd_home(self, args, line):
        if self.here is None:
            await self.send(["You're already home, in the %s." % ROOM])
            return
        await self.leave_here()
        await self.send(["You go to the %s." % ROOM] + self.room_lines())

    async def cmd_join(self, args, line):
        if args:
            await self.cmd_go(args, line)
            return
        if not self.here:
            await self.send(["Join what? `join bd_<number>' goes to a board, and `join' there sits you down."])
            return
        if self.seat >= 0:
            await self.send(["You have already joined this game."])
            return
        _, final = await self.core_req("JON %s" % self.here)
        if not final.startswith("OK"):
            code = (final.split() + ["", ""])[1]
            await self.send([{"TAKEN": "This game is full.", "FULL": "This game is full.",
                              "STATE": "This game has already started.",
                              "PRIVATE": "This game is by invitation only."}.get(code, err_text(final) + ".")])
            return
        self.seat, self.watching = int(final.split()[1]), False
        await self.poll_here()

    async def cmd_leave(self, args, line):
        """ParlorNet's own: stand up from a board whose game has not started (and watch it)."""
        if not self.here or self.seat < 0:
            await self.send(["You can't do that unless you're playing."])
            return
        v = await self.view(self.here)
        if v and v["rows"]:
            await self.send(["The game has started: `resign' gives it up."])
            return
        await self.core_req("LVE %s" % self.here)
        self.seat = -1
        _, final = await self.core_req("SEE %s" % self.here)
        self.watching = final.startswith("OK")
        await self.poll_here()

    async def cmd_newboard(self, args, line):
        players, turn = 2, None
        for a in args:
            if a.isdigit() and 2 <= int(a) <= 4:
                players = int(a)
            elif re.match(r"^\d{1,4}[mhd]\Z", a.lower()):
                turn = a.lower()
            else:
                await self.send(["Usage: newboard [2|3|4] [time for a turn, like 10m, 1h or 1d]"])
                return
        rules = await self.rules()
        tid, why = await self.new_table(players, turn, rules)
        if tid is None:
            await self.send([why])
            return
        await self.send(["You set up %s for %d players (%s rules) and join the game." % (board_name(tid), players,
                                                                                         rules),
                         "Enter `invite <name>' to ask someone, or `invite bot' for the server's bot."])

    async def rules(self):
        have = await self.hub.rulesets(self)
        return next((r for r in RULES if r in have), have[0] if have else "english")

    async def new_table(self, players=2, turn=None, rules=None):
        """A new Grid Words table with me seated and at it: (table id, None) or (None, why)."""
        rules = rules or await self.rules()
        opts = " players=%d" % players + (" turn=%s" % turn if turn else "")
        _, final = await self.core_req("NEW %s %s%s" % (GW, rules, opts))
        if not final.startswith("OK"):
            return None, "Can't set up a board: %s." % err_text(final)
        tid = final.split()[1]
        await self.leave_here()
        _, final = await self.core_req("JON %s" % tid)
        if not final.startswith("OK"):
            return None, "Can't join the new board: %s." % err_text(final)
        await self.go_to(tid, quiet=True)
        return tid, None

    async def cmd_start(self, args, line):
        v = await self.view(self.here) if self.here else None
        if v and v["rows"]:
            await self.send(["This game has already started."])
        elif not self.here or self.seat < 0:
            await self.send(["You can't start the game unless you have joined it."])
        else:
            await self.send(["Waiting for players to join. The game starts by itself when every seat is taken."])

    async def cmd_reset(self, args, line):
        await self.send(["Boards here are not reset: a new game is a new board (`newboard')."])

    async def cmd_flip(self, args, line):
        lines = ["%s flips a coin." % self.name, "It lands %s." % random.choice(("heads", "tails"))]
        for o in self.hub.near(self):
            o.post(lines)
        await self.send(lines)

    # ---- playing ---------------------------------------------------------------------------
    async def my_turn(self):
        """The view when it is my turn at my board, else None (and the reason is sent)."""
        if not self.here or self.seat < 0:
            await self.send(["You can't do that unless you're playing."])
            return None
        v = await self.view(self.here)
        if not v or not v["rows"]:
            await self.send(["The game hasn't started yet."])
            return None
        if v["turn"] < 0:
            await self.send(["The game is over."])
            return None
        if v["turn"] != self.seat:
            await self.send(["It's not your turn."])
            return None
        return v

    async def cmd_place(self, args, line):
        p = parse_place(args)
        if p is None:
            await self.send(["Enter `help place' for instructions."])
            return
        if await self.my_turn() is None:
            return
        x, y, d, letters = p
        _, final = await self.core_req("CHK %s PLAY %d %d %s %s" % (self.here, x, y, d, letters))
        if not final.startswith("OK"):
            await self.send([refusal_text(err_text(final), x, y, d)])
            return
        f = final.split()
        points = int(f[1]) if len(f) > 1 and f[1].lstrip("-").isdigit() else 0
        words = f[2:]
        self.pending = {"x": x, "y": y, "d": d, "letters": letters, "points": points, "words": words}
        lines = ["You place %s at %s for %d point%s." % (shown_word(letters), square_text(x, y, d), points,
                                                       "" if points == 1 else "s")]
        if len(words) > 1:
            lines.append("That makes %s." % ", ".join(words))
        lines.append("Enter `commit' to play it, or `undo' to take it back.")
        await self.send(lines)

    async def cmd_undo(self, args, line):
        if not self.pending:
            await self.send(["You have to place a word before you can undo it."])
            return
        word = shown_word(self.pending["letters"])
        self.pending = None
        await self.send(["You take back %s." % word])

    async def cmd_commit(self, args, line):
        if not self.pending:
            await self.send(["You have to make a play first."])
            return
        p, self.pending = self.pending, None
        await self.move("PLAY %d %d %s %s" % (p["x"], p["y"], p["d"], p["letters"]), p["d"])

    async def move(self, token, d=None):
        _, final = await self.core_req("MOV %s %s" % (self.here, token))
        if not final.startswith("OK"):
            reason = err_text(final)
            if token.startswith("EXCH") and "needs" in reason:
                reason = "There are fewer than seven tiles in the bag."
            elif token.startswith("EXCH") and "rack has no" in reason:
                reason = "You can't discard tiles that you don't have."
            else:
                reason = refusal_text(reason, d=d)
            await self.send([reason])
            return
        await self.poll_here()

    async def cmd_exchange(self, args, line):
        tiles = "".join(args).upper().replace("?", "_")
        if not re.match(r"^[A-Z_]{1,7}\Z", tiles):
            await self.send(["Usage: exchange <tiles> (`?' for a blank)"])
            return
        if await self.my_turn() is None:
            return
        self.pending = None
        await self.move("EXCH %s" % tiles)

    async def cmd_pass(self, args, line):
        if await self.my_turn() is None:
            return
        self.pending = None
        await self.move("PASS")

    async def cmd_resign(self, args, line):
        if not self.here or self.seat < 0:
            await self.send(["You can't do that unless you're playing."])
            return
        _, final = await self.core_req("RES %s" % self.here)
        if not final.startswith("OK"):
            await self.send(["You can't resign now: %s." % err_text(final)])
            return
        await self.poll_here()

    async def cmd_end(self, args, line):
        v = await self.view(self.here) if self.here else None
        if v and v["rows"] and v["turn"] < 0:
            await self.send(["The game is over."])
        else:
            await self.send(["The game's not over yet. Games here end by themselves, after the last tile or "
                             "six scoreless turns in a row."])

    async def cmd_extend(self, args, line):
        await self.send(["Games here end by themselves, so there is nothing to extend."])

    async def cmd_withdraw(self, args, line):
        await self.send(["You can't withdraw that move: every play here is checked against the word list."])

    async def cmd_challenge(self, args, line):
        await self.send(["Every word is checked against this server's word list when it is played, so there is "
                         "nothing to challenge. `ospd <words>' looks words up."])

    async def cmd_pause(self, args, line):
        await self.send(["The clock can't be paused here: each turn has the table's time limit."])

    async def cmd_resume(self, args, line):
        await self.send(["The clock is running." if self.here else "You aren't at a board."])

    async def cmd_arrange(self, args, line):
        v = await self.view(self.here) if self.here and self.seat >= 0 else None
        if not v or v["rack"] is None:
            await self.send(["You can't do that unless you're playing."])
            return
        rack = self.shown_rack(v["rack"])
        if not args:
            self.order = "".join(sorted(rack, key=lambda c: (c == "_", c)))
            await self.send(["You sort the tiles on your rack: %s." % doom_rack(self.order)])
            return
        want = "".join(args).upper().replace("?", "_")
        left = list(rack)
        for c in want:
            if c not in left:
                await self.send(["Those aren't the tiles you have."])
                return
            left.remove(c)
        self.order = want + "".join(left)
        await self.send(["You rearrange your rack: %s." % doom_rack(self.order)])

    def board_arg(self, args):
        """The board a log or track command is about: the one named, else the one I am at."""
        named = [a for a in args if not a.startswith("-")]
        if named:
            t, why = self.resolve(named[0], finished=True)
            return (t["id"], None) if t else (None, why)
        if self.here:
            return self.here, None
        return None, "That's not a board: name one (`log2 bd_<number>') or go to one."

    async def cmd_track(self, args, line):
        await self.hub.poll(self, silent=True)
        tid, why = self.board_arg(args)
        if tid is None:
            await self.send([why])
            return
        away = tid != self.here and self.name.lower() not in [n.lower() for n in await self.names_of(tid)]
        if away:
            await self.core_req("SEE %s" % tid)
        v = await self.view(tid, "G")
        if away:
            await self.core_req("LVE %s" % tid)
        if not v or not v["rows"]:
            await self.send(["The game hasn't started yet."])
            return
        await self.send(track_lines(v, self.seat if tid == self.here else -1,
                                    "-tagged" in [a.lower() for a in args]))

    async def cmd_log(self, args, line, two=False):
        await self.hub.poll(self, silent=True)
        tid, why = self.board_arg(args)
        if tid is None:
            await self.send([why])
            return
        data, final = await self.core_req("SIN %s 0" % tid)
        if not final.startswith("OK"):
            await self.send(["I don't know anything called `%s'." % board_name(tid)])
            return
        events = [l.split(" ", 3)[3] for l in data if l.startswith("EVT ") and len(l.split(" ", 3)) == 4]
        names, entries = game_moves(events)
        if two:
            await self.send(log2_lines(names, entries, "-tagged" in [a.lower() for a in args]))
        else:
            await self.send(log_lines(names, entries))

    async def cmd_log2(self, args, line):
        await self.cmd_log(args, line, two=True)

    async def cmd_time(self, args, line):
        if not self.here:
            await self.send(["That's not a board: you are in the %s." % ROOM])
            return
        v = await self.view(self.here)
        if not v or not v["rows"]:
            await self.send(["Waiting for players to join."])
            return
        scores = ", ".join("%s %d" % (self.who_at(s), p) for s, p in enumerate(v["scores"]))
        if v["turn"] < 0:
            await self.send(["Score: %s. The game is over." % scores])
        elif v["clock"]:
            s, left, limit = v["clock"]
            await self.send(["Score: %s. %s has %s left of %s for this turn." % (
                scores, self.who_at(s), clock_field(left).strip(), limit_text(limit))])
        else:
            await self.send(["Score: %s." % scores])

    # ---- talking ---------------------------------------------------------------------------
    async def talk(self, s):
        c, rest = s[0], s[1:].strip()
        if c == '"':
            await self.say(rest)
        elif c in "'`+":
            who, _, text = rest.partition(" ")
            await self.page(who, text)
        elif c == ":":
            await self.emote(rest)
        else:                       # -player text: said in the room, to that player
            who, _, text = rest.partition(" ")
            await self.say(text, to=who)

    async def say(self, text, to=None, kind="says"):
        if not text:
            await self.send(["Say what?"])
            return
        if self.here:
            await self.core_req("CHT %s %s" % (self.here, (("[to %s] " % to) if to else "") + text[:56]))
            if self.seat < 0:
                self.own_cht += 1
        else:
            shown = '%s [to %s]: "%s"' % (self.name, to, text) if to else '%s %s "%s"' % (self.name, kind, text)
            for o in self.hub.near(self):
                o.post([shown])
        if to:
            await self.send(['You [to %s]: "%s"' % (to, text)])
        else:
            await self.send(['You %s "%s"' % ("say" if kind == "says" else "kibitz:", text)])

    async def page(self, who, text):
        if not who or not text:
            await self.send(["Usage: page <player> <message>"])
            return
        o = self.hub.sessions.get(who.lower())
        if o is None:
            await self.send(["%s is not something that can be paged: only players connected here can be." % who])
            return
        o.post(['%s%s pages you: "%s"' % (o.beep(), self.name, text)])
        await self.send(['You page %s: "%s"' % (o.name, text)])

    async def emote(self, text):
        if not text:
            return
        shown = "%s %s" % (self.name, text)
        for o in self.hub.near(self):
            o.post([shown])
        await self.send([shown])

    async def cmd_say(self, args, line):
        await self.say(" ".join(args))

    async def cmd_kibitz(self, args, line):
        await self.say(" ".join(args), kind="kibitzes")

    async def cmd_page(self, args, line):
        await self.page(args[0] if args else "", " ".join(args[1:]))

    async def cmd_beep(self, args, line):
        if not args:
            await self.send(["Usage: beep <player>"])
            return
        if args[0].lower() == self.name.lower():
            await self.send(["You beep yourself.  Feel better?"])
            return
        o = self.hub.sessions.get(args[0].lower())
        if o is None:
            await self.send(["%s is not something that can be paged: only players connected here can be."
                             % args[0]])
            return
        o.post(["%s%s beeps you." % (o.beep(), self.name)])
        await self.send(["You beep %s." % o.name])

    async def cmd_option(self, args, line):
        if args and args[0].lower() == "check":
            await self.send(["Players agree on game options."])
            return
        if len(args) >= 2:
            k, val = args[0].lower(), args[1].lower()
            if k not in self.options or val not in ("y", "n", "?", "yes", "no"):
                await self.send(["Enter `help option' for help."])
                return
            if k in ("rated", "timed") and val.startswith("y"):
                await self.send(["Games here are unrated, and each turn has the table's time limit instead of "
                                 "time penalties."])
                return
            if val != "?":
                self.options[k] = val.startswith("y")
        rows = ["  Option     Value  Description"]
        for k, _, desc in OPTIONS:
            rows.append("  %-10s %-5s  %s" % (k, "yes" if self.options[k] else "no", desc))
        await self.send(rows)

    async def cmd_dict(self, args, line):
        if not args:
            await self.send(["Usage: %s word1 word2 ..." % line.split()[0].lower()])
            return
        words = await asyncio.to_thread(word_list)
        if not words:
            await self.send(["This server's word list can't be looked at from here; words are checked when "
                             "they are played."])
            return
        bad = [w.upper() for w in args if w.upper() not in words]
        if bad:
            await self.send(["Not in this server's word list: %s." % ", ".join(bad)])
        else:
            await self.send(["%s %s in this server's word list." % (", ".join(w.upper() for w in args),
                                                                    "is" if len(args) == 1 else "are all")])

    async def cmd_doing(self, args, line):
        self.doing = " ".join(args)[:40]
        await self.send(["You are now doing: %s" % self.doing if self.doing else
                         "You are no longer doing anything."])

    async def cmd_vtwrap(self, args, line):
        await self.send(["\x1b[?7hVT100 automargin enabled."])

    async def cmd_date(self, args, line):
        await self.send([time.strftime("%a %b %d %H:%M:%S UTC %Y", time.gmtime())])

    async def cmd_connect(self, args, line):
        await self.send(["You are already connected as %s." % self.name])

    async def cmd_moves(self, args, line):
        await self.send(["There is only one room here. `go bd_<number>' goes to a board, and `home' back to "
                         "the %s." % ROOM])

    async def cmd_none(self, args, line):
        await self.send(["That isn't available on this server."])

    # ---- invitations (ParlorNet's own) -----------------------------------------------------------
    async def cmd_invite(self, args, line):
        if not args:
            await self.send(["Usage: invite <name>"])
            return
        who = args[0]
        if who.lower() == self.name.lower():
            await self.send(["You can't invite yourself."])
            return
        tid = None
        if self.here and self.seat >= 0:
            v = await self.view(self.here)
            if v and v["rows"]:
                await self.send(["You can't invite someone while you are playing."])
                return
            tid = self.here
        if tid is None:
            tid, why = await self.new_table()
            if tid is None:
                await self.send([why])
                return
            await self.send(["You set up %s and join the game." % board_name(tid)])
        _, final = await self.core_req("INV %s %s" % (tid, who))
        if not final.startswith("OK"):
            code = (final.split() + ["", ""])[1]
            await self.send(["I don't know anyone named `%s'." % who] if code == "NOPLAYER" else
                            ["You can't invite %s: %s." % (who, err_text(final))])
            return
        name = self.hub.everyone().get(who.lower(), who)
        lines = ["You invite %s to %s." % (name, board_name(tid))]
        if final == "OK offline":
            lines.append("%s is not online now. The invitation will wait for an hour." % name)
        elif who.lower() in self.hub.bots or botflag.is_bot_name(who):
            lines.append("%s is the server's bot. It will join you in a few seconds." % name)
        elif who.lower() not in self.hub.sessions:
            lines.append("%s is %s and will see the invitation there." % (name, WHERE))
        await self.send(lines)
        o = self.hub.sessions.get(who.lower())
        if o is not None:
            await o.check_invitations()

    async def invitation_from(self, name):
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return None
        return next((i for i in parse_invites(data) if i["from"].lower() == name.lower()), None)

    async def cmd_accept(self, args, line):
        if not args:
            await self.send(["Usage: accept <name>"])
            return
        inv = await self.invitation_from(args[0])
        if inv is None:
            await self.send(["%s didn't invite you." % args[0]])
            return
        if inv["key"] != GW:
            await self.send(["That invitation is to %s; you can play it %s." % (game_name(inv["key"]), WHERE)])
            return
        await self.leave_here()
        _, final = await self.core_req("JON %s" % inv["id"])
        if not final.startswith("OK"):
            await self.send(["You can't join %s: %s." % (board_name(inv["id"]), err_text(final))])
            return
        self.announced.pop(inv["id"], None)
        await self.go_to(inv["id"])

    async def cmd_decline(self, args, line):
        if not args:
            await self.send(["Usage: decline <name>"])
            return
        inv = await self.invitation_from(args[0])
        if inv is None:
            await self.send(["%s didn't invite you." % args[0]])
            return
        await self.core_req("UNV %s %s" % (inv["id"], self.name))
        self.announced.pop(inv["id"], None)
        await self.send(["You decline the invitation from %s." % inv["from"]])

    async def check_invitations(self):
        """Invitations from anywhere, announced once each as a line of text."""
        data, final = await self.core_req("IVL")
        if not final.startswith("OK"):
            return
        invs = parse_invites(data)
        live = {i["id"] for i in invs}
        for tid in [t for t in self.announced if t not in live]:
            del self.announced[tid]
        out = []
        for i in invs:
            if i["id"] in self.announced:
                continue
            self.announced[i["id"]] = i["from"]
            if i["key"] == GW:
                out.append("%s%s invites you to a game of Grid Words on %s. Enter `accept %s' to sit down there, "
                           "or `decline %s'." % (self.beep(), i["from"], board_name(i["id"]), i["from"], i["from"]))
            else:
                out.append("%s invites you to %s at table %s. You can play it %s." % (
                    i["from"], game_name(i["key"]), i["id"], WHERE))
        self.post(out)

    # ---- the board's events -------------------------------------------------------------------
    async def poll_here(self):
        async with self.ev_lock:
            if not self.here:
                return
            tid = self.here
            data, final = await self.core_req("SIN %s %d" % (tid, self.cursor))
            if final.startswith("ERR"):
                self.here, self.seat, self.watching, self.pending = None, -1, False, None
                self.post(["%s vanishes. You are in the %s." % (board_name(tid), ROOM)])
                return
            await self.handle_events(tid, data)

    def who_at(self, s):
        return self.names[s] if 0 <= s < len(self.names) and self.names[s] != "-" else "seat%d" % (s + 1)

    async def handle_events(self, tid, lines):
        """A board's new events -> DOoM's lines: the moves, the draws, "<name> starts the clock." and whose
        turn it is after each turn, "plays out." and "ends the game." at the end (BobGrid redraws on those
        three), the rack penalties and the final scores."""
        evs = []
        for l in lines:
            p = l.split(" ", 4)
            if len(p) < 4 or p[0] != "EVT" or p[1] != tid or not p[2].isdigit() or int(p[2]) <= self.cursor:
                continue
            evs.append((int(p[2]), p[3], p[4].split() if len(p) > 4 else [], p[4] if len(p) > 4 else ""))
        if not evs:
            return
        outs = {int(a[0]) for _, k, a, _ in evs if k == "ADJ" and len(a) == 3 and a[2] == "-" and a[0].isdigit()}
        out = []
        mover = None
        my_draw = False
        joined = set()          # seats that sat down in these events: their CLIENT follows the JOIN
        for seq, kind, a, rest in evs:
            self.cursor = seq
            try:
                if kind == "JOIN" and len(a) >= 2:
                    s = int(a[0])
                    while len(self.names) <= s:
                        self.names.append("-")
                    self.names[s] = a[1]
                    if a[1].lower() == self.name.lower():
                        self.seat, self.watching = s, False
                    joined.add(s)
                    out.append("%s joins the game." % a[1])
                elif kind == "CLIENT":
                    # what the player plays with, a line of its own after DOoM's own "joins the game.";
                    # nothing for my own seat, or where the core gives no text (a private table's)
                    c = parse_client("CLIENT " + rest)
                    known = self.clients.get(c[0]) if c and c[0] not in joined else None
                    if c:
                        self.clients[c[0]] = c[1]
                    if c and c[0] != self.seat:
                        who = self.who_at(c[0])
                        out.append(client_change_sentence(who, c[1], TEXT_WIDTH) if known else
                                   client_sentence(who, c[1], "plays", TEXT_WIDTH))
                elif kind == "LEAVE" and a:
                    s = int(a[0])
                    out.append("%s leaves the game." % self.who_at(s))
                    if s < len(self.names):
                        self.names[s] = "-"
                    self.clients.pop(s, None)
                elif kind == "START":
                    out.append("%s starts the game." % self.who_at(0))
                elif kind == "PLAY" and len(a) == 6:
                    s = mover = int(a[0])
                    out.append("%s plays %s at %s for %s points." % (
                        self.who_at(s), a[4], square_text(int(a[1]), int(a[2]), a[3].upper()), a[5]))
                    if s in outs:
                        out.append("%s plays out." % self.who_at(s))
                    if s == self.seat:
                        self.pending = None
                elif kind == "DRAW" and len(a) == 2:
                    s = int(a[0])
                    out.append("%s draws %s from the bag." % (self.who_at(s), tiles_word(int(a[1]))))
                    my_draw = my_draw or s == self.seat
                elif kind == "EXCH" and len(a) == 2:
                    s = mover = int(a[0])
                    n = int(a[1])
                    out += ["%s exchanges %s." % (self.who_at(s), tiles_word(n)),
                            "%s draws %s from the bag." % (self.who_at(s), tiles_word(n))]
                    my_draw = my_draw or s == self.seat
                elif kind == "PASS" and a:
                    mover = int(a[0])
                    out.append("%s passes." % self.who_at(mover))
                elif kind == "OVER" and a:
                    out.append("%s's own program ends the game by its rules." % self.who_at(int(a[0])))
                elif kind == "UNLISTED" and len(a) >= 2:
                    out.append(" ".join(G.unlisted_lines(a[1:], 999)))
                elif kind == "TURN" and a:
                    s = int(a[0])
                    out += ["%s starts the clock." % self.who_at(mover if mover is not None else s),
                            "%sIt is %s's turn." % (self.beep() if s == self.seat else "", self.who_at(s))]
                elif kind == "ADJ" and len(a) == 3:
                    s, n = int(a[0]), int(a[1])
                    if a[2] == "-":
                        out.append("%s gains %d points from the tiles left on the other racks." % (self.who_at(s), n))
                    else:
                        out.append("%s loses %d point%s for the tiles left on the rack (%s)." % (
                            self.who_at(s), -n, "" if n == -1 else "s", doom_rack(a[2])))
                elif kind == "END":
                    who = "The server"
                    if a[:1] == ["resign"] and len(a) > 1:
                        out.append("%s resigns." % self.who_at(int(a[1])))
                    elif a[:1] == ["timeout"] and len(a) > 1:
                        out.append("%s has run out of time." % self.who_at(int(a[1])))
                    elif a[:1] in (["win"], ["draw"]):
                        who = self.who_at(next(iter(outs))) if outs else (
                            self.who_at(mover) if mover is not None else who)
                    out.append("%s ends the game." % who)
                    my_draw = False         # an exchange that ends the game: the rack penalty line shows the rack
                    tally = a[-1].split("/") if a[:1] in (["win"], ["draw"]) else None
                    if tally is None:
                        v = await self.view(tid)
                        tally = [str(p) for p in v["scores"]] if v else []
                    out.append("> Final scores: %s." % ", ".join("%s %s" % (self.who_at(s), p)
                                                                 for s, p in enumerate(tally)))
                elif kind == "CHT" and a:
                    text = rest.split(" ", 1)[1] if " " in rest else ""
                    if a[0] == "-":
                        if self.own_cht:
                            self.own_cht -= 1
                        else:
                            out.append('A watcher kibitzes: "%s"' % text)
                    elif a[0].isdigit() and int(a[0]) != self.seat:
                        out.append('%s says "%s"' % (self.who_at(int(a[0])), text))
                elif kind == "INVITE" and len(a) >= 2:
                    out.append("%s is invited to this game." % a[1])
                elif kind == "UNINVITE":
                    text = describe_invite_event(" ".join([kind] + a))
                    if text:
                        out.append(text[:1].upper() + text[1:] + ".")
            except (ValueError, IndexError):
                continue
        if my_draw and self.seat >= 0:
            v = await self.view(tid)
            if v and v["rack"]:
                out.append("(You now hold %s.)" % doom_rack(self.shown_rack(v["rack"])))
        self.post(out)

    async def poller(self):
        n = 0
        while not self.closing:
            await asyncio.sleep(POLL_SECS)
            n += 1
            try:
                await self.poll_here()
                if n % INVITE_POLLS == 0:
                    await self.check_invitations()
            except (CoreError, OSError, asyncio.TimeoutError):
                self.closing = True
                return

    async def run(self):
        task = inp = None
        try:
            try:
                async with asyncio.timeout(LOGIN_TOTAL_SECS):
                    await self.login()
            except (Refused, TimeoutError):
                return
            await self.welcome()
            task = asyncio.ensure_future(self.poller())
            inp = asyncio.ensure_future(self.readline())
            while not self.closing:
                done, _ = await asyncio.wait({inp, task}, return_when=asyncio.FIRST_COMPLETED)
                if task in done:
                    break
                line = inp.result()
                words = line.split()
                verb = words[0].lower() if words else None
                if verb and verb[0] in "\"'`:-+":
                    verb = "say"
                elif verb in ("page", "to"):
                    verb = "tell"
                log(self.sid, "< %s" % loggable(line, verb if verb in COMMANDS or verb in ("say", "tell") else None))
                inp = asyncio.ensure_future(self.readline())
                await self.command(line)
        finally:
            for t in (inp, task):
                if t:
                    t.cancel()
            if self.name and self.hub.sessions.get(self.name.lower()) is self:
                if self.watching and self.here:
                    try:
                        await asyncio.wait_for(self.core_req("LVE %s" % self.here), 5)
                    except (CoreError, OSError, asyncio.TimeoutError):
                        pass
                self.hub.leave(self)
            if self.core is not None:
                await self.core.finish()


def idle_text(secs):
    secs = int(secs or 0)
    if secs < 60:
        return "%ds" % secs
    if secs < 3600:
        return "%dm" % (secs // 60)
    return "%dh" % (secs // 3600)


def limit_text(secs):
    if secs >= 86400 and secs % 86400 == 0:
        return "%d day%s" % (secs // 86400, "" if secs == 86400 else "s")
    if secs >= 3600 and secs % 3600 == 0:
        return "%d hour%s" % (secs // 3600, "" if secs == 3600 else "s")
    return "%d minute%s" % (secs // 60, "" if secs // 60 == 1 else "s")


def game_name(key):
    return key.split("/")[0].capitalize()


S = Session
COMMANDS = {
    "help": S.cmd_help, "?": S.cmd_help, "about": S.cmd_about, "quit": S.cmd_quit, "@quit": S.cmd_quit,
    "who": S.cmd_who, "who1": S.cmd_who, "who2": S.cmd_who,
    "look": S.cmd_look, "l": S.cmd_look, "b": S.cmd_board, "board": S.cmd_board,
    "go": S.cmd_go, "home": S.cmd_home, "out": S.cmd_home, "join": S.cmd_join, "j": S.cmd_join,
    "leave": S.cmd_leave, "newboard": S.cmd_newboard, "start": S.cmd_start, "s": S.cmd_start,
    "reset": S.cmd_reset, "flip": S.cmd_flip,
    "place": S.cmd_place, "p": S.cmd_place, "undo": S.cmd_undo, "u": S.cmd_undo,
    "commit": S.cmd_commit, "c": S.cmd_commit, "pick": S.cmd_commit,
    "exchange": S.cmd_exchange, "x": S.cmd_exchange, "discard": S.cmd_exchange, "dump": S.cmd_exchange,
    "pass": S.cmd_pass, "resign": S.cmd_resign, "end": S.cmd_end, "e": S.cmd_end, "extend": S.cmd_extend,
    "withdraw": S.cmd_withdraw, "challenge": S.cmd_challenge, "chall": S.cmd_challenge,
    "pause": S.cmd_pause, "resume": S.cmd_resume, "r": S.cmd_resume,
    "arrange": S.cmd_arrange, "a": S.cmd_arrange, "track": S.cmd_track, "time": S.cmd_time, "t": S.cmd_time,
    "log": S.cmd_log, "log2": S.cmd_log2,
    "say": S.cmd_say, "kibitz": S.cmd_kibitz, "k": S.cmd_kibitz, "page": S.cmd_page, "to": S.cmd_page,
    "beep": S.cmd_beep, "option": S.cmd_option, "o": S.cmd_option,
    "ospd": S.cmd_dict, "ods": S.cmd_dict, "osw": S.cmd_dict, "sowpods": S.cmd_dict, "dict": S.cmd_dict,
    "doing": S.cmd_doing, "vtwrap": S.cmd_vtwrap, "date": S.cmd_date,
    "connect": S.cmd_connect, "co": S.cmd_connect, "request": S.cmd_connect,
    "invite": S.cmd_invite, "accept": S.cmd_accept, "decline": S.cmd_decline,
    "north": S.cmd_moves, "south": S.cmd_moves, "east": S.cmd_moves, "west": S.cmd_moves, "up": S.cmd_moves,
    "down": S.cmd_moves, "in": S.cmd_moves, "n": S.cmd_moves, "w": S.cmd_moves,
    "certify": S.cmd_none, "tws": S.cmd_none, "dws": S.cmd_none, "tls": S.cmd_none, "dls": S.cmd_none,
    "drop": S.cmd_none, "take": S.cmd_none, "get": S.cmd_none, "lock": S.cmd_none, "unlock": S.cmd_none,
    "eject": S.cmd_none, "gag": S.cmd_none, "ungag": S.cmd_none, "mutter": S.cmd_none, "m": S.cmd_none,
    "rated": S.cmd_none, "rating": S.cmd_none, "password": S.cmd_none, "describe": S.cmd_none,
    "gripe": S.cmd_none, "gripes": S.cmd_none, "history": S.cmd_none, "todo": S.cmd_none,
}


# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("DOOM", "DOoM crossword-game server", "Grid Words for DOoM clients: BobGrid and telnet")


async def serve(args):
    global POLL_SECS
    POLL_SECS = args.poll
    hub = Hub()
    gate = Gate(MAX_SESSIONS)

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.write(b"The server is full. Try again soon.\r\n")
            writer.close()
            return
        keepalive(writer)
        io = TelnetIO(reader, writer)
        s = Session(hub, io, (args.core_host, args.core_port), ticket)
        log(s.sid, "connect")
        try:
            await s.run()
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError):
            pass
        except Exception as e:
            print("doom session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            hub.connectors.discard(s)
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("doom adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.doom")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6507)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--prompt-wait", type=float, default=PROMPT_WAIT, help=argparse.SUPPRESS)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)    # tests
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    globals()["PROMPT_WAIT"] = a.prompt_wait
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
