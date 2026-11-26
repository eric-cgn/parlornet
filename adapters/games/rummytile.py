"""Rummy Tile Classic (rummytile/1, docs/types/rummytile-1.md): the web table and the telnet screens.

A staged game: a turn is any number of steps that rearrange the table (each
sent with MOV, taken back with UND), ended with END. The view carries the
table's sets in a fixed order, so a tile is named by its set and position
(3.2) or by its set and face (3.R7); rack tiles are named by their face (R7).
Colors are never the only difference: every tile shows its color letter
(R red, B blue, K black, Y yellow) and number, and the joker shows J.
"""
import re

from ..common import err_text
from ..telnet import (A_AXIS, A_BORDER, A_BRIGHT, A_DIM, A_HEAD, A_RED, A_SEL, A_TEXT, A_YELLOW, COLS, ROWS,
                      AnsiGame, Canvas, GameBase, PlainGame, clock_text, fmt_secs, rule, status_bar, title_bar,
                      turn_text, wrap)
from ..telnet import box, describe_event as describe_common
from ..web import a, esc, hidden_fields

# ---- what the game is ------------------------------------------------------------

KEY = "rummytile/1"
NAME = "Rummy Tile Classic"
ORDER = 10
PLAYERS = "2-4"
ABOUT = (
    "Two to four players each hold a rack of numbered tiles in four colors and take turns laying them "
    "on a shared table in sets: runs of one color in sequence and groups of one number in different "
    "colors. Once a player has made a first meld, the whole table is theirs to rearrange on their turn, "
    "splitting and joining sets, as long as every set is sound when the turn ends. The first to empty "
    "the rack wins.",
    ["Standard rules: 106 tiles (1 to 13 in red, blue, black and yellow, two of each, and two jokers); 14 "
     "tiles each; a run is three or more tiles of one color in sequence (no wrapping past 13), a group "
     "three or four tiles of one number in different colors; a joker stands for any tile.",
     "A turn is a series of steps that may take sets apart and put them together in any way; at the end "
     "of the turn every set on the table must be valid and at least one tile must have come from the "
     "rack, or the turn is undone and the player draws a tile. A player who plays nothing draws a tile.",
     "The first meld must be worth at least 30 points, made only of the player's own tiles; until it is "
     "made, the sets already on the table cannot be touched.",
     "A joker on the table can be taken back into play by a player who has made the first meld, by "
     "playing the tile it stood for from the rack in the same turn; the joker must be used again in the "
     "same turn and never returns to a rack.",
     "The game ends when a player empties the rack, or when the pool is empty and every player in turn "
     "has passed. Remaining tiles count their numbers, a joker 30: the lowest rack wins and each other "
     "player loses the difference, which the winner gains.",
     "Seats: two to four, chosen when a table is started (on the web site and telnet, or players=3 or 4 "
     "over the native protocol). When one of three or four players resigns or runs out of time, the "
     "others play on. Time per turn: chosen when a table is started."])

COLOUR_NAMES = {"R": "red", "B": "blue", "K": "black", "Y": "yellow", "J": "joker"}
WEB_COLOURS = {"R": "#C00000", "B": "#0000C0", "K": "#000000", "Y": "#A06000", "J": "#800080"}
TILE = r"(?:[RBKY](?:1[0-3]|[1-9])|J)"
TILE_RE = re.compile(TILE, re.I)
REASON_TEXT = {"none": "nothing was played from the rack", "sets": "a set was not valid",
               "meld": "the first meld was under 30 points",
               "joker": "a joker was taken without its tile from the rack"}


def tile_key(t):
    return (4, 0) if t == "J" else ("RBKY".index(t[0]), int(t[1:]))


def num_key(t):
    return (99, 0) if t == "J" else (int(t[1:]), "RBKY".index(t[0]))


# ---- the view and the events (docs/types/rummytile-1.md) ------------------------------

def parse_view(lines):
    """VUE lines for rummytile/1 -> dict. rows is True once the game has started."""
    v = {"seq": 0, "rows": False, "turn": -1, "moves": [], "status": "", "clock": None, "pool": 0,
         "counts": [], "melded": [], "gone": [], "sets": [], "rack": [], "played": None, "scores": {}, "over": False}
    for l in lines:
        f = l.split()
        if not f:
            continue
        tag = f[0]
        if tag == "V" and len(f) >= 3:
            v["seq"] = int(f[2])
        elif tag == "P" and len(f) == 2:
            v["pool"], v["rows"] = int(f[1]), True
        elif tag == "C":
            v["counts"] = [int(x) for x in f[1:]]
        elif tag == "I":
            v["melded"] = [x == "1" for x in f[1:]]
        elif tag == "G":
            v["gone"] = [x == "1" for x in f[1:]]
        elif tag == "W" and len(f) == 2:
            v["turn"] = -1 if f[1] == "-" else int(f[1])
            v["over"] = f[1] == "-"
        elif tag == "ST" and len(f) >= 3:
            v["sets"].append({"kind": f[2][0], "new": f[2].endswith("*"), "tiles": f[3:]})
        elif tag == "H":
            v["rack"] += f[1:]
        elif tag == "TP" and len(f) == 4:
            v["played"] = (int(f[1]), None if f[2] == "-" else int(f[2]), f[3])
        elif tag == "T" and len(f) == 3:
            v["scores"][int(f[1])] = int(f[2])
        elif tag == "M" and len(f) > 1:
            v["moves"].append(f[1])
        elif tag == "CLK" and len(f) == 4:
            v["clock"] = (int(f[1]), int(f[2]), int(f[3]))
        elif tag == "S":
            v["status"] = l[2:]
    return v


def _nm(names, i):
    i = int(i)
    n = names[i] if 0 <= i < len(names) else "-"
    return n if n != "-" else "seat %d" % (i + 1)


def _tiles(text):
    return " ".join(TILE_RE.findall(text))


def _where(t):
    return "a new set" if t == "n" else "set %s" % t


def describe_event(text, names):
    """Rummy Tile Classic's events -> a line for the log (None for events that are not the game's)."""
    p = text.split()
    k = p[0]
    if k == "PUT" and len(p) == 4:
        return "%s puts %s into %s" % (_nm(names, p[1]), _tiles(p[2]), _where(p[3]))
    if k == "MOVE" and len(p) == 5:
        return "%s moves %s from set %s to %s" % (_nm(names, p[1]), p[3], p[2], _where(p[4]))
    if k == "BACK" and len(p) == 4:
        return "%s takes %s from set %s back to the rack" % (_nm(names, p[1]), p[3], p[2])
    if k == "MERGE" and len(p) == 4:
        return "%s joins set %s onto set %s" % (_nm(names, p[1]), p[2], p[3])
    if k == "SPLIT" and len(p) == 4:
        return "%s splits set %s before its tile %s" % (_nm(names, p[1]), p[2], p[3])
    if k == "RESET" and len(p) == 2:
        return "%s starts the turn over" % _nm(names, p[1])
    if k == "UNDO" and len(p) == 2:
        return "%s takes back a step" % _nm(names, p[1])
    if k == "UNDONE" and len(p) == 3:
        if p[2] == "none":
            return ""
        return "%s's turn is undone: %s" % (_nm(names, p[1]), REASON_TEXT.get(p[2], p[2]))
    if k == "DONE" and len(p) == 3:
        return "%s plays %s tile%s" % (_nm(names, p[1]), p[2], "" if p[2] == "1" else "s")
    if k == "MELD" and len(p) == 3:
        return "%s makes the first meld (%s points)" % (_nm(names, p[1]), p[2])
    if k == "DRAW" and len(p) == 3:
        return "%s draws a tile (%s left in the pool)" % (_nm(names, p[1]), p[2])
    if k == "PASS" and len(p) == 2:
        return "%s passes (the pool is empty)" % _nm(names, p[1])
    if k == "GONE" and len(p) == 3:
        return "%s %s and leaves the game; the others play on" % (
            _nm(names, p[1]), "ran out of time" if p[2] == "timeout" else "resigned")
    if k == "OUT" and len(p) == 2:
        return "%s has played every tile" % _nm(names, p[1])
    if k == "DEAL" and len(p) == 3:
        return "%s tiles each; %s in the pool" % (p[1], p[2])
    if k in ("SET", "DEL"):
        return ""
    if k == "END" and len(p) >= 3 and p[1] in ("win", "draw"):
        scores = p[3:] if p[1] == "win" else p[2:]
        if not all(re.match(r"^-?\d+\Z", x) for x in scores):
            return None
        sc = ", ".join("%s %s%s" % (_nm(names, i), "+" if int(x) > 0 else "", x) for i, x in enumerate(scores))
        if p[1] == "win":
            return "Game over: %s wins (%s)" % (_nm(names, p[2]), sc)
        return "Game over: a draw (%s)" % sc
    return None


def turn_info(v):
    """What the turn so far would do at END, for the player on turn: (text, ok?)."""
    if not v["played"]:
        return "", False
    n, pts, code = v["played"]
    tiles = "%d tile%s" % (n, "" if n == 1 else "s")
    meld = "" if pts is None else "; first meld %d of 30 points" % pts
    if code == "ok":
        return "Ready: %s played%s. END plays them." % (tiles, meld), True
    if code == "none":
        return "Nothing played yet: END draws a tile.", False
    if code == "sets":
        bad = [str(i + 1) for i, s in enumerate(v["sets"]) if s["kind"] == "x"]
        return "%s played%s. Not valid yet: set %s." % (tiles, meld, ", ".join(bad[:6])), False
    if code == "meld":
        return "%s played%s. The first meld needs 30." % (tiles, meld), False
    return "%s played. A joker taken needs its tile from your rack." % tiles, False


# ---- the web page (HTML 3.2) -----------------------------------------------------------

MOVE_RE = re.compile(r"^(?:%s{1,13}-(?:[0-9]{1,2}|n)|[0-9]{1,2}\.(?:[0-9]{1,2}|%s)-(?:[0-9]{1,2}|n|r)"
                     r"|[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}/[0-9]{1,2}|reset)\Z" % (TILE, TILE), re.I)
SEL_RE = re.compile(r"^(?:%s{1,13}|[0-9]{1,2}(?:\.[0-9]{1,2})?)\Z" % TILE, re.I)


def images():
    """None: tiles are table cells with their color letter and number, so the page works with images off."""
    return {}


def tile_html(t, picked=False):
    text = "[%s]" % t if picked else t
    return '<font color="%s"><b>%s</b></font>' % (WEB_COLOURS.get(t[0], "#000000"), esc(text))


def parse_sel(sel):
    """sel= -> ("rack", [tiles]), ("tile", set, pos), ("set", set) or (None,)."""
    if not sel or not SEL_RE.match(sel):
        return (None,)
    if sel[0].isdigit():
        if "." in sel:
            k, p = sel.split(".")
            return ("tile", int(k), int(p))
        return ("set", int(sel))
    return ("rack", [t.upper() for t in TILE_RE.findall(sel)])


def web_target(sel, k):
    """The move that puts the picked tiles (or set) into set k, or None."""
    if sel[0] == "rack":
        return "%s-%d" % ("".join(sel[1]), k)
    if sel[0] == "tile" and sel[1] != k:
        return "%d.%d-%d" % (sel[1], sel[2], k)
    if sel[0] == "set" and sel[1] != k:
        return "%d-%d" % (sel[1], k)
    return None


def table_html(v, tid, link, mine, sel):
    if not v["sets"]:
        return "<p><i>The table is empty.</i></p>"
    out = ['<table border="0" cellpadding="2">']
    for i, s in enumerate(v["sets"]):
        k = i + 1
        note = "not valid" if s["kind"] == "x" else "changed" if s["new"] else ""
        label = "<b>%d</b>%s" % (k, "<br><font size=\"1\">%s</font>" % note if note else "")
        target = web_target(sel, k) if mine else None
        if mine and target:
            label = a(link("/move.htm", t=tid, mv=target), label)
        elif mine and sel[0] is None:
            label = a(link("/table.htm", t=tid, sel=k), label)
        cells = []
        for p, t in enumerate(s["tiles"], 1):
            picked = (sel[0] == "tile" and sel[1:] == (k, p)) or (sel[0] == "set" and sel[1] == k)
            html = tile_html(t, picked)
            if mine:
                if target:
                    html = a(link("/move.htm", t=tid, mv=target), html)
                elif not (sel[0] == "set" and sel[1] == k):
                    html = a(link("/table.htm", t=tid, sel="%d.%d" % (k, p)), html)
            cells.append('<td align="center" width="28"%s>%s</td>' % (' bgcolor="#FFE080"' if picked else "", html))
        put = "<td>%s</td>" % a(link("/move.htm", t=tid, mv=target), "&lt;- here") if mine and target else ""
        out.append('<tr><td align="right" width="40">%s</td><td><table border="1" cellspacing="0" cellpadding="3" '
                   'bgcolor="%s"><tr>%s</tr></table></td>%s</tr>'
                   % (label, "#FFF0F0" if s["kind"] == "x" else "#FFF8E0", "".join(cells), put))
    out.append("</table>")
    return "\n".join(out)


def rack_html(v, tid, link, mine, sel):
    rack = v["rack"]
    if not rack:
        return "<p><i>Your rack is empty.</i></p>"
    chosen = list(sel[1]) if sel[0] == "rack" else []
    left = list(chosen)
    rows, cells = [], []
    for t in rack:
        picked = t in left
        if picked:
            left.remove(t)
        html = tile_html(t, picked)
        if mine:
            nxt = list(chosen)
            if picked:
                nxt.remove(t)
            else:
                nxt.append(t)
            html = a(link("/table.htm", t=tid, sel="".join(sorted(nxt, key=tile_key)) or None), html)
        cells.append('<td align="center" width="28"%s>%s</td>' % (' bgcolor="#FFE080"' if picked else "", html))
        if len(cells) == 14:
            rows.append("<tr>%s</tr>" % "".join(cells))
            cells = []
    if cells:
        rows.append("<tr>%s</tr>" % "".join(cells))
    return ('<table border="1" cellspacing="0" cellpadding="3" bgcolor="#FFF8E0">%s</table>' % "".join(rows))


def web_parts(v, tid, link, seat, names, sel):
    """-> (board, side panel, hint after the status line, action links)."""
    mine = seat >= 0 and v["turn"] == seat and not v["over"]
    pick = parse_sel(sel) if mine else (None,)
    if pick[0] == "rack" and len(pick[1]) > 13:
        pick = (None,)
    board = "<p><b>Table</b></p>\n" + table_html(v, tid, link, mine, pick)
    if 0 <= seat < len(v["counts"]):
        board += "\n<p><b>Your rack</b> (%d tile%s)</p>\n" % (len(v["rack"]), "" if len(v["rack"]) == 1 else "s")
        board += rack_html(v, tid, link, mine, pick)
    rows = []
    for i, c in enumerate(v["counts"]):
        here = v["turn"] == i and not v["over"]
        n = esc(names[i] if i < len(names) and names[i] != "-" else "(empty seat)")
        if v["over"]:
            extra = "%+d" % v["scores"].get(i, 0)
        elif i < len(v["gone"]) and v["gone"][i]:
            extra = "left the game"
        else:
            extra = "%d tile%s%s" % (c, "" if c == 1 else "s", ", melded" if i < len(v["melded"]) and v["melded"][i]
                                     else "")
        rows.append("<tr><td>%s</td><td>%s%s</td><td>%s</td></tr>"
                    % ("<b>&gt;</b>" if here else "&nbsp;", n, " (you)" if i == seat else "", extra))
    side = ('<table border="0" cellpadding="2">%s</table>\n<p>Pool: %d tile%s</p>'
            % ("".join(rows), v["pool"], "" if v["pool"] == 1 else "s"))
    if v["played"] and not v["over"] and v["played"][0]:
        side += "\n<p><font size=\"2\">%s</font></p>" % esc(turn_info(v)[0])
    hint, actions = "", []
    if mine:
        if pick[0] == "rack":
            hint = " - click a set to put %s there, or New set" % " ".join(pick[1])
            actions.append(a(link("/move.htm", t=tid, mv="%s-n" % "".join(pick[1])), "<b>New set</b>"))
        elif pick[0] == "tile":
            k, p = pick[1], pick[2]
            tiles = v["sets"][k - 1]["tiles"] if k <= len(v["sets"]) else []
            tile = tiles[p - 1] if p <= len(tiles) else "?"
            hint = " - click another set to move %s there" % tile
            actions.append(a(link("/move.htm", t=tid, mv="%d.%d-n" % (k, p)), "<b>New set</b>"))
            if p > 1:
                actions.append(a(link("/move.htm", t=tid, mv="%d/%d" % (k, p)), "Split before %s" % esc(tile)))
            actions.append(a(link("/move.htm", t=tid, mv="%d.%d-r" % (k, p)), "Back to rack"))
        elif pick[0] == "set":
            hint = " - click another set to join set %d onto it" % pick[1]
        else:
            hint = " - click tiles on your rack, then a set or New set; click a table tile to move it"
        if pick[0]:
            actions.append(a(link("/table.htm", t=tid), "Clear"))
        code = v["played"][2] if v["played"] else "none"
        if v["played"] and (v["played"][0] or any(s["new"] for s in v["sets"])):
            actions.append(a(link("/und.htm", t=tid), "Undo"))
            actions.append(a(link("/move.htm", t=tid, mv="reset"), "Start over"))
        end = {"ok": "<b>End turn</b>", "none": "<b>Draw a tile</b>"}.get(
            code, "End turn (undoes it and draws: %s)" % esc(turn_info(v)[0]))
        actions.append(a(link("/end.htm", t=tid), end))
        hidden = hidden_fields(link("/move.htm", t=tid))
        actions.append('</p>\n<form action="/move.htm" method="get">%s\nType a step: <input name="mv" size="14" '
                       'maxlength="40"> <input type="submit" value="Play"><br>\n<font size="2">R7-3 puts R7 into '
                       'set 3, R7B7K7-n makes a new set, 3.2-5 moves tile 2 of set 3 to set 5, 3-5 joins set 3 '
                       'onto set 5, 3/4 splits set 3 before its tile 4, 3.2-r takes a tile back.</font>\n'
                       '</form>\n<p>' % hidden)
    return board, side, hint, " | ".join(actions)


# ---- telnet --------------------------------------------------------------------------
#
# The screen: the table's sets flow over rows 2-14 of the left area (columns
# 1-55; a 13-tile run is exactly 55 wide) and scroll when they need more
# rows; the rack is on rows 16-19 (14 tiles a row); row 20 says what END
# would do. The panel (players, pool, turn, clock, log) is at columns 57-78.
#
# A tile is 3 columns ("R7 ", "B12", "J  ") after a 1-column gap that shows
# a picked tile's marker. A set starts with its number and a mark: ":" a
# valid set as it was, "*" a valid set changed this turn, "!" not valid.

LEFT_W = 55
TABLE_TOP, TABLE_ROWS = 2, 13
RACK_TOP, RACK_ROWS, RACK_PER_ROW = 16, 4, 14
PX, PW = 57, 22
FG = {"R": "31", "B": "34", "K": "30", "Y": "33", "J": "35"}
HELP = [
    "Arrows  move over the table and your rack",
    "Enter   on rack tiles: pick them (several);",
    "        on a set: put what you picked there;",
    "        on a table tile: pick it up to move it",
    "N new set    S split before the cursor tile",
    "D back to the rack (a tile played this turn)",
    "U undo a step   Z start over   C check",
    "E end the turn (nothing played: draw a tile)",
    "O sort the rack by color or by number",
    "Typed: R7-3, R7B7K7-n, 3.2-5, 3.R7-n, 3-5, 3/4,",
    "3.2-r, undo, end, check, reset, say <text>.",
    "Set marks: ':' valid, '*' changed, '!' not",
    "valid. T talks, R resigns, Esc: menu.",
]
WORDS = {"undo": "undo", "und": "undo", "end": "end", "done": "end", "draw": "end", "pass": "end",
         "check": "check", "chk": "check", "reset": "reset", "over": "reset"}
TOKEN_RE = re.compile(r"^(?:%s{1,13}-(?:[0-9]{1,2}|n)|[0-9]{1,2}\.(?:[0-9]{1,2}|%s)-(?:[0-9]{1,2}|n|r)"
                      r"|[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}/[0-9]{1,2})\Z" % (TILE, TILE), re.I)


def tile_glyphs(mode):
    """The picked-tile marker and the melded mark per mode (CP437 shapes in pc and utf8, ASCII otherwise)."""
    if mode in ("pc", "utf8"):
        return {"pick": "»", "meld": "√", "more": "▓"}
    return {"pick": ">", "meld": "+", "more": "#"}


def tile_text(t):
    return t.ljust(3)


def tile_attr(t, bg=None, mode="pc"):
    """A tile's colors in a display mode, on its own background or on bg (the cursor's 46, a picked
    tile's 43). In utf8, for xterm-class terminals, a tile is bright white (107) and a yellow tile's
    number is dark gold from the 256 colors (38;5;136), since yellow does not read on white. The other
    modes are for terminals with only the eight colors, where white is light grey (47), and no yellow
    reads on it, so a yellow tile there is bright yellow on black. On the cursor or a picked tile a
    yellow tile's number is black."""
    own = "107" if mode == "utf8" else "47"
    fg = FG.get(t[0], "30")
    if t[0] == "Y":
        if bg not in (None, own):
            fg = "30"
        elif mode == "utf8":
            fg = "38;5;136"
        else:
            fg, own = "1;33", "40"
    return "0;%s;%s" % (fg, bg or own)


class RtBase(GameBase):
    """What both Rummy Tile Classic screens share: the view, the events, typed steps and words."""

    BACKLOG = 16
    parse = staticmethod(parse_view)

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.by_number = False      # the rack shown by number instead of by color
        self.tg = tile_glyphs(sess.mode)

    def describe(self, text):
        d = describe_event(text, self.names)
        return d if d is not None else describe_common(text, self.names)

    def move_word(self, cmd, words=()):
        if cmd in WORDS:
            return WORDS[cmd]
        if words and all(TOKEN_RE.match(w) for w in words):
            return " ".join(words)
        return None

    def playing(self):
        return bool(self.v and self.v["rows"]) and not self.over and not self.v["over"]

    def my_turn(self):
        return self.me >= 0 and self.playing() and self.v["turn"] == self.me

    def rack(self):
        return sorted(self.v["rack"], key=num_key if self.by_number else tile_key)

    def code(self):
        return self.v["played"][2] if self.v and self.v["played"] else "none"

    async def check(self):
        """CHK the turn as it stands -> (text, ok?)."""
        _, final = await self.core.req("CHK %s !end" % self.tid)
        if final.startswith("OK"):
            return "Ready: END plays this turn (%s)." % (final[3:].strip() or "ok"), True
        return "END now would undo the turn and draw a tile: %s." % err_text(final), False

    async def play_word(self, word):
        if self.me < 0:
            return "You are watching this table."
        if not self.playing():
            return self.over or "The game has not started yet."
        if not self.my_turn():
            return "It is %s's turn." % self.name(self.v["turn"])
        if word == "undo":
            return await self.staged("UND", "take that back")
        if word == "end":
            return await self.staged("END", "end the turn")
        if word == "check":
            return (await self.check())[0]
        return await self.move(word)

    def seat_label(self, i):
        empty = i >= len(self.names) or self.names[i] == "-"
        return "(empty seat)" if empty else self.names[i], empty

    def state_text(self):
        v = self.v
        if not v["rows"]:
            return "Waiting for an opponent", A_YELLOW
        if self.over or v["over"]:
            return self.over or "Game over", A_YELLOW
        if self.my_turn():
            return "Your turn", A_YELLOW
        return "%s to play" % self.name(v["turn"]), A_TEXT


class AnsiRummy(RtBase, AnsiGame):
    """Rummy Tile Classic, full screen: the table's sets and your rack on the left (a cursor over the
    tiles, picks marked), the players, pool, clock and log on the right."""

    HOTKEYS = "nsduezcort?"
    LOG_TOP, LOG_ROWS = 11, 9
    TITLE, HELP = "Rummy Tile Classic", HELP

    def __init__(self, sess, tid, key, me):
        super().__init__(sess, tid, key, me)
        self.cur = ("rack", 0)
        self.picks = []             # rack indices (in the rack as shown)
        self.tpick = None           # ("tile", set, pos) or ("set", set): a table pick
        self.scroll = 0             # the first table row shown
        self.rscroll = 0            # the first rack row shown

    # ---- layout --------------------------------------------------------------
    def layout(self):
        """-> (items, table rows): items are (key, x, row, width), rows counted over the table's rows and
        then the rack's (the rack's first row is table rows), in reading order."""
        items, x, row = [], 1, 0
        sets = self.v["sets"] if self.v else []
        for i, s in enumerate(sets):
            w = 3 + 4 * len(s["tiles"])
            if x > 1 and x + w - 1 > LEFT_W:
                x, row = 1, row + 1
            items.append((("set", i + 1), x, row, 3))
            for p in range(len(s["tiles"])):
                items.append((("tile", i + 1, p + 1), x + 3 + 4 * p, row, 4))
            x += w + 2
        if self.my_turn():
            if x > 1 and x + 5 > LEFT_W:
                x, row = 1, row + 1
            items.append((("new",), x, row, 5))
        trows = row + 1 if items else 0
        if self.me >= 0:
            for i in range(len(self.v["rack"]) if self.v else 0):
                items.append((("rack", i), 1 + 4 * (i % RACK_PER_ROW), trows + i // RACK_PER_ROW, 4))
        return items, trows

    def find(self, items, key):
        return next((it for it in items if it[0] == key), None)

    def keep_visible(self, items, trows):
        it = self.find(items, self.cur)
        if not it:
            return
        if it[0][0] == "rack":
            r = it[2] - trows
            if r < self.rscroll:
                self.rscroll = r
            elif r >= self.rscroll + RACK_ROWS:
                self.rscroll = r - RACK_ROWS + 1
        else:
            if it[2] < self.scroll:
                self.scroll = it[2]
            elif it[2] >= self.scroll + TABLE_ROWS:
                self.scroll = it[2] - TABLE_ROWS + 1
        self.scroll = max(0, min(self.scroll, max(0, trows - TABLE_ROWS)))

    def screen_y(self, it, trows):
        """The screen row of an item, or None when it is scrolled out of sight."""
        if it[0][0] == "rack":
            r = it[2] - trows - self.rscroll
            return RACK_TOP + r if 0 <= r < RACK_ROWS else None
        r = it[2] - self.scroll
        return TABLE_TOP + r if 0 <= r < TABLE_ROWS else None

    def snap(self):
        """Keep the cursor on something that is there; when the turn becomes yours, put it on your rack."""
        if not (self.v and self.v["rows"]):
            return
        items, trows = self.layout()
        mine = self.my_turn()
        if mine and not self.was_my_turn and self.v["rack"]:
            self.cur = ("rack", 0)
        if not self.find(items, self.cur) and items:
            want = self.cur
            if want[0] == "rack" and self.v["rack"]:
                self.cur = ("rack", min(want[1], len(self.v["rack"]) - 1))
            elif want[0] == "tile" and self.find(items, ("set", want[1])):
                k = want[1]
                n = len(self.v["sets"][k - 1]["tiles"])
                self.cur = ("tile", k, min(want[2], n))
            else:
                self.cur = items[0][0]
        if self.tpick and not self.find(items, self.tpick if self.tpick[0] == "tile" else ("set", self.tpick[1])):
            self.tpick = None
        self.picks = [i for i in self.picks if i < len(self.v["rack"])]
        self.keep_visible(items, trows)
        self.was_my_turn = mine

    # ---- drawing -------------------------------------------------------------
    def compose(self):
        cv = Canvas()
        g, v = self.g, self.v
        if self.me < 0:
            right = "watching"
        else:
            right = "you: %s" % self.name(self.me)
        title_bar(cv, g, "Rummy Tile Classic %s table %s" % (g["v"], self.tid), right)
        self.draw_table(cv)
        self.draw_panel(cv)
        text, attr = (self.msg, self.msg_attr) if self.msg else self.hint()
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        tail = [("T", "talk"), ("?", "help"), ("Esc", "menu")]
        if self.buf:
            keys = [("Enter", "send"), ("Esc", "cancel"), ("Backspace", "erase")]
        elif self.my_turn():
            keys = [("Enter", "pick/put"), ("N", "new set"), ("S", "split"), ("U", "undo"), ("E", "end"),
                    ("C", "check")] + tail
        elif self.me >= 0 and self.playing():
            keys = [("Arrows", "look"), ("O", "sort")] + tail
        else:
            keys = tail
        status_bar(cv, keys)
        return cv

    def draw_tile(self, cv, x, y, t, key):
        """A tile at x (its gap) and y, with the cursor and pick shown."""
        picked = (key[0] == "rack" and key[1] in self.picks) or (self.tpick is not None and (
            (self.tpick[0] == "tile" and key == self.tpick) or
            (self.tpick[0] == "set" and key[0] == "tile" and key[1] == self.tpick[1])))
        cur = key == self.cur and self.me >= 0 and self.playing()
        cv.put(x, y, self.tg["pick"] if picked else " ", A_YELLOW)
        cv.put(x + 1, y, tile_text(t), tile_attr(t, "46" if cur else "43" if picked else None, self.s.mode))
        cv.hot_area(x, y, 4, 1, ("it", key))

    def draw_table(self, cv):
        g, v = self.g, self.v
        rule(cv, g, 0, 1, LEFT_W + 2, A_BORDER, "Table", tattr=A_HEAD)
        if not v["rows"]:
            self.draw_waiting(cv, 14, 6)
            rule(cv, g, 0, RACK_TOP - 1, LEFT_W + 2, A_BORDER)
            return
        items, trows = self.layout()
        self.keep_visible(items, trows)
        if trows > TABLE_ROWS:
            more = []
            if self.scroll > 0:
                more.append("more above")
            if self.scroll + TABLE_ROWS < trows:
                more.append("more below")
            tag = " %s " % ", ".join(more)
            cv.put(LEFT_W + 1 - len(tag), 1, tag, A_YELLOW)
        if not v["sets"]:
            cv.put(2, TABLE_TOP + 1, "The table is empty."[:LEFT_W - 2], A_DIM)
        for it in items:
            key, x, _, w = it
            y = self.screen_y(it, trows)
            if y is None:
                continue
            if key[0] == "set":
                s = v["sets"][key[1] - 1]
                mark = "!" if s["kind"] == "x" else "*" if s["new"] else ":"
                cur = key == self.cur and self.me >= 0 and self.playing()
                sel = self.tpick == ("set", key[1])
                attr = A_SEL if cur else A_YELLOW if sel else A_RED if s["kind"] == "x" else A_AXIS
                cv.put(x, y, "%2d%s" % (key[1], mark), attr)
                cv.hot_area(x, y, 3, 1, ("it", key))
            elif key[0] == "tile":
                self.draw_tile(cv, x, y, v["sets"][key[1] - 1]["tiles"][key[2] - 1], key)
            elif key[0] == "new":
                cur = key == self.cur
                cv.put(x, y, "[new]", A_SEL if cur else A_DIM)
                cv.hot_area(x, y, 5, 1, ("it", key))
            else:
                self.draw_tile(cv, x, y, self.rack()[key[1]], key)
        if self.me >= 0:
            n = len(v["rack"])
            title = "Your rack: %d tile%s, by %s" % (n, "" if n == 1 else "s", "number" if self.by_number else "color")
            rule(cv, g, 0, RACK_TOP - 1, LEFT_W + 2, A_BORDER, title, tattr=A_HEAD)
            rrows = (n + RACK_PER_ROW - 1) // RACK_PER_ROW
            if rrows > RACK_ROWS:
                tag = " rows %d-%d of %d " % (self.rscroll + 1, self.rscroll + RACK_ROWS, rrows)
                cv.put(LEFT_W + 1 - len(tag), RACK_TOP - 1, tag, A_YELLOW)
        else:
            rule(cv, g, 0, RACK_TOP - 1, LEFT_W + 2, A_BORDER, "Racks", tattr=A_HEAD)
            cv.put(2, RACK_TOP, "Watching: every rack is hidden.", A_DIM)
        info, ok = self.info_line()
        cv.put(1, 20, info[:LEFT_W], A_YELLOW if ok else A_DIM)

    def info_line(self):
        v = self.v
        if self.over or v["over"]:
            return "", False
        if self.my_turn():
            return turn_info(v)
        if v["played"] and v["played"][0]:
            n = v["played"][0]
            return "%s has played %d tile%s so far this turn." % (self.name(v["turn"]), n, "" if n == 1 else "s"), False
        return "", False

    def draw_panel(self, cv):
        g, v = self.g, self.v
        inner = PW - 4
        box(cv, g, PX, 1, PW, 20, A_BORDER, "Players", tattr=A_HEAD)
        n = len(v["counts"]) if v["rows"] else max(2, len(self.names))
        for i in range(min(n, 4)):
            y = 2 + i
            tomove = self.playing() and v["turn"] == i
            label, empty = self.seat_label(i)
            cv.put(PX + 2, y, g["ptr"] if tomove else " ", A_YELLOW)
            attr = A_BRIGHT if tomove or i == self.me else A_DIM if empty else A_TEXT
            cv.put(PX + 3, y, label[:12], attr)
            if v["rows"] and i < len(v["counts"]):
                if v["over"]:
                    cv.put(PX + 15, y, ("%+d" % v["scores"].get(i, 0)).rjust(5)[-5:], A_BRIGHT)
                elif i < len(v["gone"]) and v["gone"][i]:
                    cv.put(PX + 15, y, "left", A_DIM)
                else:
                    cv.put(PX + 16, y, "%2d" % v["counts"][i], attr)
                    if i < len(v["melded"]) and v["melded"][i]:
                        cv.put(PX + 18, y, self.tg["meld"], A_AXIS)
        rule(cv, g, PX, 6, PW, A_BORDER)
        if v["rows"]:
            cv.put(PX + 2, 7, ("Pool: %d tile%s" % (v["pool"], "" if v["pool"] == 1 else "s"))[:inner], A_TEXT)
        text, attr = self.state_text()
        cv.put(PX + 2, 8, text[:inner], attr)
        c = self.clock()
        if c:
            seat, left, limit = c
            urgent = seat == self.me and left < 60
            cv.put(PX + 2, 9, ("Clock: %s left" % fmt_secs(left))[:inner], A_RED if urgent else A_TEXT)
        elif self.turn_secs and not self.over:
            cv.put(PX + 2, 9, ("Turn: %s" % turn_text(self.turn_secs))[:inner], A_DIM)
        rule(cv, g, PX, 10, PW, A_BORDER, "Log", tattr=A_HEAD)
        self.draw_log(cv, PX + 2, inner)

    def hint(self):
        v = self.v
        if not v["rows"]:
            return ("Table %s starts when the seats are filled: telnet, the web (port 6500) or another client."
                    % self.tid, A_DIM)
        if self.over or v["over"]:
            return "%s. Esc goes back to the menu." % (self.over or "Game over"), A_YELLOW
        if self.me < 0:
            return "Watching. T talks to the table, Esc goes back to the menu.", A_DIM
        if not self.my_turn():
            return "Waiting for %s. You can look around; O sorts your rack." % self.name(v["turn"]), A_DIM
        if self.picks:
            tiles = " ".join(self.rack()[i] for i in self.picks)
            return "Picked %s: Enter on a set puts them there, N makes a new set, Esc drops." % tiles, A_TEXT
        if self.tpick and self.tpick[0] == "tile":
            k, p = self.tpick[1], self.tpick[2]
            return ("Picked tile %d of set %d: Enter on another set moves it, N new set, %s back."
                    % (p, k, self.kn("D")), A_TEXT)
        if self.tpick:
            return "Picked set %d: Enter on another set joins it there. Esc drops it." % self.tpick[1], A_TEXT
        key = self.cur
        if key[0] == "rack":
            return "Enter picks this tile (pick several for a new set), then go to a set.", A_TEXT
        if key[0] == "tile":
            return "Enter picks up this tile; %s splits the set before it." % self.kn("S"), A_TEXT
        if key[0] == "set":
            return "Enter picks up the whole set, to join it onto another.", A_TEXT
        return "Pick tiles first, then Enter here (or N) makes them a new set.", A_TEXT

    def cursor_at(self):
        if not (self.me >= 0 and self.playing()):
            return None
        items, trows = self.layout()
        it = self.find(items, self.cur)
        if not it:
            return None
        y = self.screen_y(it, trows)
        if y is None:
            return None
        return it[1] + (1 if it[0][0] in ("tile", "rack") else 0), y

    # ---- keys ----------------------------------------------------------------
    def on_arrow(self, k):
        if not (self.v and self.v["rows"]):
            return
        items, trows = self.layout()
        if not items:
            return
        it = self.find(items, self.cur) or items[0]
        i = items.index(it)
        if k in ("LEFT", "RIGHT"):
            self.cur = items[(i + (1 if k == "RIGHT" else -1)) % len(items)][0]
        else:
            step = 1 if k == "DOWN" else -1
            rows = sorted({x[2] for x in items})
            r = rows.index(it[2]) + step
            if 0 <= r < len(rows):
                row = [x for x in items if x[2] == rows[r]]
                self.cur = min(row, key=lambda x: abs(x[1] - it[1]))[0]
        self.keep_visible(items, trows)

    def on_escape(self):
        if self.picks or self.tpick:
            self.picks, self.tpick = [], None
            return None
        return "menu"

    async def on_click(self, value):
        if value[0] == "it" and self.me >= 0 and self.playing():
            self.cur = value[1]
            return await self.act()
        return None

    async def on_enter(self):
        return await self.act()

    def ready(self):
        if self.me < 0:
            self.say("You are watching this table.")
        elif not self.playing():
            self.say(self.over or "The game has not started yet.")
        elif not self.my_turn():
            self.say("It is %s's turn. Arrows look around." % self.name(self.v["turn"]))
        else:
            return True
        return False

    def target(self, k):
        """The step that puts what is picked into set k (0 for a new set), or None."""
        if self.picks:
            rack = self.rack()
            tiles = "".join(sorted((rack[i] for i in self.picks), key=tile_key))
            return "%s-%s" % (tiles, k or "n")
        if self.tpick and self.tpick[0] == "tile":
            if self.tpick[1] == k:
                return None
            return "%d.%d-%s" % (self.tpick[1], self.tpick[2], k or "n")
        if self.tpick and k and self.tpick[1] != k:
            return "%d-%d" % (self.tpick[1], k)
        return None

    async def act(self):
        """Enter (or a click) on the cursor: pick or drop a rack tile, pick a table tile or set, or put what
        is picked into the set under the cursor."""
        if not self.ready():
            return None
        key = self.cur
        if key[0] == "rack":
            self.tpick = None
            if key[1] in self.picks:
                self.picks.remove(key[1])
            elif len(self.picks) < 13:
                self.picks.append(key[1])
            return None
        if key[0] == "new":
            return await self.new_set()
        k = key[1]
        step = self.target(k)
        if step:
            return await self.step(step)
        if key[0] == "tile":
            self.tpick = None if self.tpick == key else key
        else:
            self.tpick = None if self.tpick == ("set", k) else ("set", k)
        return None

    async def new_set(self):
        step = self.target(0)
        if not step:
            if self.tpick:
                return self.say("A whole set is a set already: pick tiles to make a new one.")
            return self.say("Pick tiles on your rack (or a table tile) first, then N or Enter on [new].")
        return await self.step(step)

    async def step(self, word):
        self.picks, self.tpick = [], None
        err = await self.play_word(word)
        if err:
            self.say(err, A_RED)
        self.snap()

    async def typed_move(self, word):
        self.picks, self.tpick = [], None
        if word == "check":
            return await self.hotkey("c")
        await self.step(word)

    async def hotkey(self, k):
        if k in ("r", "t", "?"):
            return await super().hotkey(k)
        if k == "o":
            self.by_number = not self.by_number
            self.picks = []
            return self.say("Your rack is shown by %s." % ("number" if self.by_number else "color"))
        if not self.ready():
            return None
        if k == "n":
            return await self.new_set()
        if k == "s":
            key = self.tpick if self.tpick and self.tpick[0] == "tile" else self.cur
            if key[0] != "tile":
                return self.say("Put the cursor on a table tile: %s splits its set before it." % self.kn("S"))
            if key[2] < 2:
                return self.say("That is the first tile of set %d: split before a later one." % key[1])
            return await self.step("%d/%d" % (key[1], key[2]))
        if k == "d":
            key = self.tpick if self.tpick and self.tpick[0] == "tile" else self.cur
            if key[0] != "tile":
                return self.say("Put the cursor on a tile you played this turn: %s takes it back." % self.kn("D"))
            return await self.step("%d.%d-r" % (key[1], key[2]))
        if k == "u":
            return await self.step("undo")
        if k == "z":
            return await self.step("reset")
        if k == "c":
            text, ok = await self.check()
            return self.say(text, A_YELLOW if ok else A_RED)
        if k == "e":
            code = self.code()
            if code not in ("ok", "none"):
                if not await self.confirm("End the turn", ["The table is not ready (%s)." % REASON_TEXT[code][:30],
                                                           "Ending now undoes the turn and you draw a",
                                                           "tile. End anyway?"]):
                    return None
            return await self.step("end")
        return None


class PlainRummy(RtBase, PlainGame):
    """Rummy Tile Classic for plain terminals: the sets and the rack printed as text, typed steps."""

    BOARD_KINDS = ("PUT", "MOVE", "BACK", "MERGE", "SPLIT", "RESET", "UNDO", "UNDONE", "DONE", "DRAW", "PASS",
                   "TURN", "JOIN", "LEAVE", "START", "END")
    HELP_TEXT = (" Steps (several on one line make one step):\n"
                 "  R7-3 puts R7 from your rack into set 3; R7B7K7-n makes a new set.\n"
                 "  3.2-5 moves tile 2 of set 3 to set 5 (3.R7-5 names it); 3.2-n a new set.\n"
                 "  3-5 joins set 3 onto set 5; 3/4 splits set 3 before its tile 4.\n"
                 "  3.2-r takes a tile you played this turn back to your rack.\n"
                 " undo takes back a step, reset starts the turn over, check says whether\n"
                 " end would play your turn; end finishes it (nothing played: you draw).\n"
                 " Set marks: ':' valid, '*' changed this turn, '!' not valid.\n"
                 " sort shows your rack by number or color. say <text>, resign, menu.")

    async def command(self, line):
        if line.strip().lower() == "sort":
            self.by_number = not self.by_number
            return await self.draw()
        return await super().command(line)

    def lines(self):
        v, me = self.v, self.me
        you = "you are watching" if me < 0 else "you are %s" % self.name(me)
        out = ["", "== Rummy Tile Classic, table %s: %s ==" % (self.tid, you)]
        if not v["rows"]:
            return out + ["", "Waiting for an opponent.",
                          "The game starts when the seats are filled from telnet, the web (port 6500)",
                          "or another client. Type menu to go back; the table stays open."]
        out.append("Table:" if v["sets"] else "Table: empty.")
        line = ""
        for i, s in enumerate(v["sets"]):
            mark = "!" if s["kind"] == "x" else "*" if s["new"] else ":"
            item = "%2d%s %s" % (i + 1, mark, " ".join(s["tiles"]))
            if line and len(line) + 3 + len(item) > 78:
                out.append(" " + line)
                line = ""
            line = (line + "   " + item) if line else item
        if line:
            out.append(" " + line)
        if me >= 0:
            rack = self.rack()
            out += [" " + l for l in wrap("Your rack (%d, by %s): %s" % (
                len(rack), "number" if self.by_number else "color", " ".join(rack) or "empty"), 78)]
        side = []
        for i, c in enumerate(v["counts"]):
            label, _ = self.seat_label(i)
            mark = ">" if self.playing() and v["turn"] == i else " "
            if v["over"]:
                side.append("%s%-12s %+d" % (mark, label[:12], v["scores"].get(i, 0)))
            elif i < len(v["gone"]) and v["gone"][i]:
                side.append("%s%-12s left" % (mark, label[:12]))
            else:
                side.append("%s%-12s %2d%s" % (mark, label[:12], c, "+" if i < len(v["melded"]) and v["melded"][i]
                                                else " "))
        if not v["over"]:
            side.append(" pool %d" % v["pool"])
        for i in range(0, len(side), 3):            # three to a line: 4 seats and the pool fit in 80 columns
            out.append(" " + "  ".join(side[i:i + 3]))
        if not v["over"] and not self.over:
            out.append(" " + ("+ = first meld made. " if any(v["melded"]) else "") + self.state_text()[0] + ".")
            info = turn_info(v)[0] if self.my_turn() else ""
            if info:
                out.append(" " + info)
            out.append(" " + clock_text(v, self.names))
        else:
            out.append(" " + (self.over or "Game over"))
        if self.my_turn():
            out.append(" Steps: R7-3, R7B7K7-n, 3.2-5, 3-5, 3/4, 3.2-r; undo, check, end; help.")
        for l in self.log[-4:]:
            out.append(" " + l[:78])
        return [l[:80] for l in out]

    def prompt(self):
        if self.my_turn():
            return "Your step (like %s-n), or end > " % (self.rack()[0] if self.v["rack"] else "R7")
        return "> "


AnsiScreen, PlainScreen = AnsiRummy, PlainRummy
