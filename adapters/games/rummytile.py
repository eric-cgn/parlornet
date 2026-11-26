"""Rummy Tile Classic (rummytile/1, docs/types/rummytile-1.md): the web table and the telnet screens.

A staged game: a turn is any number of steps that rearrange the table (each
sent with MOV, taken back with UND), ended with END. The view carries the
table's sets in a fixed order, so a tile is named by its set and position
(3.2) or by its set and face (3.R7); rack tiles are named by their face (R7).
Colors are never the only difference: every tile shows its color letter
(R red, B blue, K black, Y yellow) and number, and the joker shows J.
"""
import re
import textwrap

from ..common import err_text
from ..telnet import (A_AXIS, A_BORDER, A_DIM, A_HEAD, A_RED, A_SEL, A_TEXT, A_YELLOW, AnsiGame, COLS, Canvas,
                      GameBase, PlainGame, ROWS, and_list, err_text, or_list, rule, sentence, title_bar)
from ..telnet import describe_event as describe_common
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
# "draw" is not here: it means a drawn game. "pass" ends the turn only when nothing is played (the server asks).
WORDS = {"undo": "undo", "end": "end", "pass": "pass", "check": "check", "reset": "reset"}
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

    HELP_MOVING = ["The arrows move over the sets and your rack. Enter picks rack tiles,",
                   "and Enter on a set puts them there. Enter on a table tile picks it",
                   "up, and Enter on your rack takes it back. Esc drops what you picked."]
    HELP_KEYS = ["M makes a new set, and B splits a set before the cursor's tile.",
                 "U takes back a step, Z starts the turn over and C checks the turn.",
                 "E ends the turn, P passes with nothing played and O sorts the rack."]
    HELP_COMMANDS = ["Press / to type one. /R7-3 puts R7 into set 3. /split, /undo,",
                     "/reset, /check, /end, /pass and /sort do what their keys do."]
    HELP_REFERENCE = ["Steps",
                      "R7-3 puts R7 into set 3, and R7B7K7-n makes a new set. 3.2-5 moves",
                      "tile 2 of set 3 to set 5, and 3-5 joins set 3 onto set 5. 3/4",
                      "splits set 3 before its tile 4, and 3.2-r takes a tile back.",
                      "",
                      "The sets",
                      "A : after a set's number means the set is sound, * means it changed",
                      "this turn and ! means it isn't valid yet.",
                      "",
                      "The first meld",
                      "Your first turn that counts must lay sets worth 30 points."]

    BACKLOG = 16
    GAME = NAME
    MOVE_EXAMPLE = "/R7-3"
    COMMANDS = {"split": "/split breaks the set in two before the tile under the cursor.",
                "undo": "/undo takes back your last step.", "reset": "/reset starts your turn over.",
                "check": "/check tells you whether your turn would count as it stands.",
                "end": "/end ends your turn, and with nothing played you take a tile.",
                "pass": "/pass ends your turn without playing, and you take a tile.",
                "sort": "/sort shows your rack by color or by number."}
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
            return "Your turn counts as it stands.", True
        return "Your turn doesn't count yet. %s" % sentence(err_text(final)), False

    async def play_word(self, word):
        if not self.my_turn():
            return self.not_yours()
        if word == "undo":
            return await self.staged("UND", "take that back")
        if word == "end":
            return await self.staged("END", "end the turn")
        if word == "check":
            return (await self.check())[0]
        return await self.move(word)

    def last_move(self, text):
        p = text.split()
        k = p[0]
        if k == "DONE" and len(p) == 3:
            return "%s played %s tile%s." % (self.actor(p[1]), p[2], "" if p[2] == "1" else "s")
        if k in ("DRAW", "UNDONE") and len(p) == 3 and (k == "DRAW" or p[2] != "none"):
            return "%s ended the turn and took a tile." % self.actor(p[1])
        if k == "PASS" and len(p) == 2:
            return "%s passed, because the pool is empty." % self.actor(p[1])
        return None

    def info_words(self):
        """Row 20 on your turn: what the turn would do if it ended now, as sentences (docs/design.md)."""
        v = self.v
        if not v["played"]:
            return "", False
        n, pts, code = v["played"]
        tiles = "You played %d tile%s" % (n, "" if n == 1 else "s")
        meld = "" if pts is None else ", worth %d of the 30 points a first meld needs" % pts
        if code == "ok":
            return "%s%s, and the turn counts." % (tiles, meld), True
        if code == "none":
            return "You have played nothing yet. Ending the turn takes a tile.", False
        if code == "sets":
            bad = [str(i + 1) for i, s in enumerate(v["sets"]) if s["kind"] == "x"]
            return "%s%s. Set %s is not valid yet." % (tiles, meld, or_list(bad[:4])), False
        if code == "meld":
            return "%s%s. The first meld needs 30 points." % (tiles, meld), False
        return "%s. A joker you take needs its tile from your rack." % tiles, False

class AnsiRummy(RtBase, AnsiGame):
    """Rummy Tile Classic, full screen: the table's sets and your rack on the left (a cursor over the
    tiles, picks marked), the players, pool, clock and log on the right."""

    HOTKEYS = "mbuzcepoert?"
    LOG_TOP, LOG_ROWS = 11, 9

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
        title_bar(cv, g, self.title_text())
        self.draw_table(cv)
        self.draw_panel(cv)
        text, attr = self.sentence(self.hint)
        cv.put(1, ROWS - 3, text[:COLS - 2], attr)
        self.input_line(cv)
        if self.my_turn():
            picked = bool(self.picks or self.tpick)
            acts = [("e", "end turn", "end")]
            acts += [("u", "undo", "undo"), ("z", "reset", "reset")] if self.code() != "none" else []
            acts += [("m", "new set", "new set")] if picked else []
            acts += [("b", "split", "split")] if self.cur[0] == "tile" else []
            acts += [("c", "check", "check"), ("o", "sort", "sort")]
            acts += [("p", "pass", "pass")] if self.code() == "none" else []
            keys = self.keys("move", "put" if picked and self.cur[0] != "rack" else "pick", acts, cancel=picked)
        elif self.me >= 0 and self.playing():
            keys = self.keys("look", None, [("o", "sort", "sort")])
        else:
            keys = self.keys()
        self.keys_line(cv, keys)
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
        cv.put(1, 20, info[:COLS - 2], A_YELLOW if ok else A_DIM)

    def info_line(self):
        v = self.v
        if self.over or v["over"]:
            return "", False
        if self.my_turn():
            return self.info_words()
        if v["played"] and v["played"][0]:
            n = v["played"][0]
            return "%s has played %d tile%s so far this turn." % (self.who(self.name(v["turn"])), n,
                                                                  "" if n == 1 else "s"), False
        return "", False

    def draw_panel(self, cv):
        v = self.v
        n = len(v["counts"]) if v["rows"] else max(2, len(self.names))
        players = []
        for i in range(min(n, 4)):
            if not v["rows"] or i >= len(v["counts"]):
                score = ""
            elif v["over"]:
                score = "%+d" % v["scores"].get(i, 0)
            elif i < len(v["gone"]) and v["gone"][i]:
                score = "left"
            else:
                score = "%d%s" % (v["counts"][i], self.tg["meld"] if i < len(v["melded"]) and v["melded"][i] else " ")
            players.append((i, None, score))
        facts = ["The pool holds %d tile%s." % (v["pool"], "" if v["pool"] == 1 else "s")] if v["rows"] else []
        self.draw_panel_parts(cv, PX, 1, PW, players, facts)

    def hint(self):
        v = self.v
        if not v["rows"]:
            return self.waiting_hint()
        if self.over or v["over"]:
            return self.over_hint()
        if self.me < 0:
            return self.watch_hint()
        if not self.my_turn():
            return self.their_hint(v["turn"])
        if self.picks:
            tiles = and_list([self.rack()[i] for i in self.picks])
            return "You picked %s. Press Enter on a set, or M to make a new set." % tiles, A_TEXT
        if self.tpick and self.tpick[0] == "tile":
            k, p = self.tpick[1], self.tpick[2]
            tile = v["sets"][k - 1]["tiles"][p - 1] if k <= len(v["sets"]) and p <= len(v["sets"][k - 1]["tiles"]) \
                else "a tile"
            return "You picked %s from set %d. Press Enter on another set, or on your rack to take it back." % (
                tile, k), A_TEXT
        if self.tpick:
            return "You picked set %d. Press Enter on another set to join it there." % self.tpick[1], A_TEXT
        key = self.cur
        if key[0] == "rack" and self.code() == "none":
            return ("Your turn. Pick tiles with Enter, or %s to end your turn and take a tile."
                    % self.kp("e", "end"), A_TEXT)
        if key[0] == "rack":
            return "Your turn. Press Enter to pick %s, or %s to end your turn." % (
                self.rack()[key[1]], self.kn("e", "end")), A_TEXT
        if key[0] == "tile":
            return "Your turn. Press Enter to pick up this tile, or B to split the set before it.", A_TEXT
        if key[0] == "set":
            return "Your turn. Press Enter to pick up set %d and join it to another." % key[1], A_TEXT
        return "Pick tiles first, then press Enter here to make a new set.", A_TEXT

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
        if not self.my_turn():
            self.say(self.not_yours())
            return False
        return True

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
        if key[0] == "rack" and self.tpick and self.tpick[0] == "tile":     # a table tile back to the rack
            return await self.step("%d.%d-r" % self.tpick[1:])
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
                return self.say("A whole set is a set already, so pick tiles to make a new one.")
            return self.say("Pick tiles on your rack or a tile on the table first, then press M.")
        return await self.step(step)

    async def step(self, word):
        self.picks, self.tpick = [], None
        err = await self.play_word(word)
        if err:
            self.say(err, A_RED)
        self.snap()

    def typed_reset(self):
        self.picks, self.tpick = [], None

    async def game_command(self, cmd, args):
        if cmd in ("sort", "split") and not args:
            await self.hotkey("o" if cmd == "sort" else "b")
            return True
        return False

    async def hotkey(self, k):
        if k in ("r", "t", "?"):
            return await super().hotkey(k)
        if k == "o":
            self.by_number = not self.by_number
            self.picks = []
            return self.say("Your rack is shown by %s." % ("number" if self.by_number else "color"))
        if not self.ready():
            return None
        if k == "m":
            return await self.new_set()
        if k in ("p", "e"):                  # the server asks first where the turn would pass or not count
            self.picks, self.tpick = [], None
            return await self.shared("pass" if k == "p" else "end")
        if k == "b":
            key = self.tpick if self.tpick and self.tpick[0] == "tile" else self.cur
            if key[0] != "tile":
                return self.say("Put the cursor on a tile on the table, and B splits its set before it.")
            if key[2] < 2:
                return self.say("That is the first tile of set %d, so split before a later one." % key[1])
            return await self.step("%d/%d" % (key[1], key[2]))
        if k == "u":
            return await self.step("undo")
        if k == "z":
            return await self.step("reset")
        if k == "c":
            text, ok = await self.check()
            return self.say(text, A_YELLOW if ok else A_RED)
        return None


class PlainRummy(RtBase, PlainGame):
    """Rummy Tile Classic for plain terminals: the sets and the rack printed as text, typed steps."""

    BOARD_KINDS = ("PUT", "MOVE", "BACK", "MERGE", "SPLIT", "RESET", "UNDO", "UNDONE", "DONE", "DRAW", "PASS",
                   "TURN", "JOIN", "LEAVE", "START", "END")

    async def game_command(self, cmd, args):
        if cmd == "sort":
            self.by_number = not self.by_number
            await self.draw()
            return True
        if cmd == "split":
            self.tell("Type a split as /3/4, which breaks set 3 before its tile 4.")
            return True
        return False

    def lines(self):
        v, me = self.v, self.me
        out = [""] + self.plain_header()
        if not v["rows"]:
            return out + self.plain_waiting()
        out.append("The table:" if v["sets"] else "The table is empty.")
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
            out += [" " + l for l in textwrap.wrap("Your rack has %d tile%s, by %s: %s." % (
                len(rack), "" if len(rack) == 1 else "s", "number" if self.by_number else "color",
                " ".join(rack) or "none"), 78)]
        players = []
        for i, c in enumerate(v["counts"]):
            if v["over"]:
                score = "%+d" % v["scores"].get(i, 0)
            elif i < len(v["gone"]) and v["gone"][i]:
                score = "left"
            else:
                score = "%d%s" % (c, "+" if i < len(v["melded"]) and v["melded"][i] else " ")
            players.append((i, None, score))
        facts = ["The pool holds %d tiles." % v["pool"]] if not v["over"] else []
        if any(v["melded"]):
            facts.append("A + marks a player who made the first meld.")
        if self.my_turn():
            facts.append(self.info_words()[0])
        out += [" " + l for l in self.side_text(players, facts, 60)]
        return out + self.plain_log()

    def turn_prompt(self):
        return "Your turn. Type a step such as /%s-n, or /end." % (self.rack()[0] if self.v["rack"] else "R7")

AnsiScreen, PlainScreen = AnsiRummy, PlainRummy
