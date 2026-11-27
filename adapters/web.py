"""Web server extension (the HTTP adapter): HTML 3.2 pages for any browser with tables, forms and image maps.

A client of the core's extension channel, like the telnet extension. No CSS:
tables, forms, links and GIFs, a game's board as one image with an image map
(adapters/board.py; a little optional JavaScript polls for changes). Sign in
(/login.htm, USER: never a sign-up) and New account (/signup.htm, REG) are
separate pages; each relays the core's dialog (ASK labels) through POST forms;
the session id the core issues goes in a cookie (pn), or for browsers that do
not return cookies in the links (s=). Every page resumes the session with SES.
With "Stay signed in" checked on the sign-in form, the core's sign-in key for
this browser goes in a long-lived cookie of its own (pnkey), and a visit with
no session signs in with it (docs/accounts.md, Staying signed in).
Actions carry x=, an HMAC of the session id, against cross-site requests.
Credentials never appear in a URL or a page (docs/security-design.md).
Pages end in .htm. Every page is built in one frame with one palette
(adapters/webstyle.py); the site map and the rules its pages keep are in
docs/site.md. The site is the game server's own: the welcome page with the message
of the day, sign-in and sign-up, the games this server hosts and the ways to play from
each kind of computer, then the lobby and tables, and a page for each game. Its bar is
Welcome, Lobby, Docs and Sign In, with Sign Out in its place while a player is signed in. The documentation (the clients
for every machine, their setup, the protocols reference and the docs) is a separate
static site, built by tools/build_docs.py, at site.docs_url (http://docs.parlornet.org/
by default); the old addresses of its pages here redirect there.

    python3 -m adapters.web --core 127.0.0.1:6590 --key /data/frontends/web.key --port 6500 --host 0.0.0.0
"""
import argparse
import asyncio
import email.utils
import hashlib
import hmac
import os
import time
import secrets
import re
import sys
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlsplit

from parlord import DOCS_URL, PROJECT_URL
from parlord.accounts import as_ip, proxy_list, trusted_proxy
from parlord.auth import NEW_NAME_RULE
from parlord.frontends import read_key

from .common import (BG_HOME_PREF, BG_HOMES, CoreClient, CoreError, NeedAuth, Refused, addr_key, claim_text,
                     describe_invite_event, err_text, on_machine, parse_profile, parse_turns, turn_sentence,
                     turn_time, with_program,
                     group_games, more_tables, parse_games, parse_invites, parse_players, parse_tables, pending_invites)
from .common import forwarded_client, parse_client, parse_clients
from .common import SCORES_NOTE, parse_top, score_text
from .chatcmd import note_lines
from .board import BoardCache, hit, view_digest
from .telnet import clock_text, describe_event
from . import games
from . import botflag
from . import clients as C
from . import declare
from .medals import ICONS as MEDAL_ICONS
from . import palette as PAL
from . import siteimages as PIC
from . import webstyle as S
from .games import rules_name
from .webstyle import a, away, callout, data_table, esc, font, h1, h2, h3, p, small, ul

RATES = (0, 3, 5, 10, 30, 60)
DEFAULT_RATE = 5
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,12}\Z")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{22}\Z")
GAME_KEY_RE = re.compile(r"^[a-z0-9]{1,24}(/[0-9]{1,3})?\Z")     # a game type, as the leaderboard's g= gives it
MAX_REQUESTS = 80
# A form field a game's MOVE_FIELDS names (/move.htm and /check.htm): printable ASCII, no control
# characters, at most 32 characters. Anything else is refused before the game's module sees it.
FIELD_RE = re.compile(r"^[\x20-\x7e]{0,32}\Z")
REQUESTS_PER_ADDR = 20      # requests in progress from one client address
MAX_SIGNINS = 100           # sign-in dialogs waiting for the next answer (each holds a core connection)
SIGNINS_PER_ADDR = 4        # ... from one client address (a new one ends its oldest)
SIGNIN_IDLE_SECS = 300      # a dialog waits this long for its next answer (time to read a mailed password)
GOOD_SECS = 86400           # how long a session id that resumed is remembered (session.max_secs)
HANDOFF_SECS = 60           # the one-time handle on the redirect after sign-in
SES_FAIL_LIMIT = (10, 600)  # failed resumes per client address in that many seconds, then no more tries
MAX_BODY = 2048
COOKIE = "pn"
KEY_COOKIE = "pnkey"        # Stay signed in: name.key, the core's sign-in key for this browser
KEY_COOKIE_SECS = 400 * 86400    # the longest a browser keeps a cookie (the core forgets a key unused 90 days)
KEY_COOKIE_RE = re.compile(r"^([A-Za-z0-9_-]{1,12})\.([A-Za-z0-9_-]{22})\Z")
KEY_LABEL_LEN = 32          # a key's label, as the core keeps it (parlord.accounts.KEY_LABEL_LEN)
AUTO_PATHS = ("/seq.htm", "/board.gif")    # what a page asks for on its own: not the player's activity
# requests that change something: they need x= (the session's HMAC), so another site cannot make them
ACTIONS = ("/new.htm", "/join.htm", "/invite.htm", "/uninvite.htm", "/leave.htm", "/resign.htm", "/und.htm",
           "/end.htm", "/move.htm", "/check.htm", "/password.htm", "/out.htm", "/click.htm", "/colours.htm",
           "/note.htm", "/bghome.htm", "/profile.htm", "/forget.htm")
MAX_FIELDS = 16             # query or form fields in one request (the colors form has the most)
# Keep-alive: a connection may carry this many requests, waiting this long between them; offered only while
# the adapter and the client's address are well under their limits (MAX_REQUESTS, REQUESTS_PER_ADDR).
KEEPALIVE_MAX = 16
KEEPALIVE_IDLE = 5
ISMAP_RE = re.compile(r"(?:\A|\?)(\d{1,5}),(\d{1,5})\Z")    # what an ISMAP click puts after the link: ?x,y
STARTED = email.utils.formatdate(time.time(), usegmt=True)    # Last-Modified of the static images


# Every game ParlorNet plans, in build order (README.md): (name, game type, players, status when this
# server does not host it). The Games list and page mark a game "playable" when the core lists it in GAM, so
# installing a plugin is enough; the static status covers the rest ("in progress" or "planned"; a
# "playable" here is the fallback while the core cannot be asked). A game with a frontend module in
# adapters/games/ takes its name and players from there, and counts as built ("playable" until the core
# says otherwise); a game with a module but no row here is listed after these, in the modules' ORDER.
GAMES = [
    ("Reversi", "reversi", "2", "playable"),
    ("Backgammon", "backgammon", "2", "playable"),
    ("Grid Words", "gridwords", "2-4", "planned"),
    ("Cannon Fire", "cannonfire", "2-8", "planned"),
    ("Dots and Boxes", "dotsandboxes", "2", "planned"),
    ("Five in a Row", "fiveinarow", "2", "planned"),
    ("Mancala", "mancala", "2", "planned"),
    ("Checkers", "checkers", "2", "planned"),
    ("Naval Combat", "navalcombat", "2", "planned"),
    ("Rummy Tile Classic", "rummytile", "2-4", "planned"),
]


GAMES_SECTION = "/welcome.htm#games"     # the welcome page's Games section, the list of every game
FIBS_HOME = "http://fibs.com/"

# What each planned game in GAMES is, for the Games section and each game's page: game type -> (about, rules). about
# is two or three sentences in ParlorNet's own words, with generic names only. rules are the rulesets and
# options this server supports, from the type specs in docs/types/ (for a planned game: what is planned).
# Plain text; the page escapes it. A game with a module in adapters/games/ has its own ABOUT there, which
# is used instead (Reversi's and Backgammon's are there).
GAME_ABOUT = {
    "gridwords": (
        "Two to four players build interlocking words on a square board from letter tiles drawn from a "
        "bag. Each tile has a value, and special squares multiply a letter or "
        "a whole word; the player with the highest score when the tiles run out wins.",
        ["Planned (a draft type spec exists): an English ruleset first, a 15 by 15 board, words checked "
         "against the server's word list as they are played, so there is no challenge round. The word list "
         "and tile set will come from freely licensed sources."]),
    "cannonfire": (
        "Two to eight players each command a cannon on a hilly landscape. In turn, each sets an angle and a "
        "power and fires; shells fly under gravity and the wind, and blast craters in the ground and in "
        "the other cannons. The last cannon standing wins.",
        ["Planned (a draft type spec exists): one shell type first, with options for the arena width, the "
         "terrain seed and wind. All flight is worked out on the server, so every player sees the same "
         "shot."]),
    "dotsandboxes": (
        "Players take turns drawing a line between two neighboring dots on a grid. A player who closes the "
        "fourth side of a box claims it and draws again; when every box is closed, the player with more "
        "boxes wins.",
        ["Planned: grid sizes and rules will be set when the game is built."]),
    "fiveinarow": (
        "Two players take turns placing stones on the points of a large grid. The first to make an unbroken "
        "line of five of their own stones, across, down or diagonally, wins.",
        ["Planned: board size and opening rules will be set when the game is built."]),
    "mancala": (
        "A board of two rows of small pits, with a store at each end, holds seeds or pebbles. A player picks "
        "up all the seeds in one of their pits and sows them one by one into the following pits; captures "
        "come from where the last seed lands. When one side runs out of seeds, the player with more seeds "
        "in their store wins.",
        ["Planned: a common two-row version from the mancala family first; the exact sowing and capture "
         "rules will be set when the game is built."]),
    "checkers": (
        "Two players move twelve pieces each diagonally forward across the dark squares of an 8 by 8 board, "
        "capturing by jumping over an opponent's piece. A piece that reaches the far side is crowned and may "
        "then move backwards too. A player who cannot move, or has no pieces left, loses.",
        ["Planned: standard 8 by 8 rules with compulsory captures; other variants may follow."]),
    "navalcombat": (
        "Each player secretly places a fleet of ships on their own grid. Players take turns calling a "
        "square on the other's grid and hear whether it is a hit or a miss; the first to sink every ship of "
        "the other fleet wins.",
        ["Planned: fleet and grid sizes will be set when the game is built. The server keeps each fleet "
         "hidden from the other player."]),
    "rummytile": (
        "Two to four players hold racks of numbered tiles in several colors. On a turn a player lays down "
        "runs of one color or groups of one number, and may rearrange the sets already on the table; a "
        "player who cannot play draws a tile. The first to empty their rack wins.",
        ["Planned last, as the most complex game here, with hidden racks and a table anyone can rearrange. "
         "Rules will be set when the game is built."]),
}


def _players(lo, hi):
    return "%d" % lo if lo == hi else "%d-%d" % (lo, hi)


def game_about(gtype):
    """(about, rules) for the Games section and the game's page: from the game's module in adapters/games/, else GAME_ABOUT; None
    when neither has it."""
    m = games.by_type().get(gtype)
    return m.ABOUT if m else GAME_ABOUT.get(gtype)


def game_rows(hosted=None):
    """Every planned game in build order, then any other game the core hosts: a dict per game with name,
    type, players and status. hosted is the core's GAM list (parse_games), or
    None when it is not known; then the static status in GAMES stands. The welcome page's Games section and
    each game's page are built from this."""
    by_type = {}
    for g in hosted or ():
        by_type.setdefault(g["key"].split("/")[0], g)
    modules = games.by_type()
    rows = [(modules[t].NAME, t, modules[t].PLAYERS, "playable") if t in modules else (name, t, players, status)
            for name, t, players, status in GAMES]
    known = {gtype for _, gtype, _, _ in GAMES}
    rows += [(m.NAME, t, m.PLAYERS, "playable") for t, m in modules.items() if t not in known]
    known.update(modules)
    rows += [(g["name"], t, "", "playable") for t, g in by_type.items() if t not in known]
    out = []
    for name, gtype, players, status in rows:
        g = by_type.get(gtype)
        if g is not None:
            status = "playable"
            if g["min"] is not None:
                players = _players(g["min"], g["max"])
        elif hosted is not None and status == "playable":
            status = "not on this server"
        out.append({"name": name, "type": gtype, "players": players or "-", "status": status})
    return out


def _fibs_note_html():
    """The Backgammon section's note on FIBS: ParlorNet speaks its protocol, and is not FIBS."""
    return callout(p("<b>FIBS.</b> The First Internet Backgammon Server (FIBS) has run since 1992 and is still at "
                     "%s (telnet to <tt>fibs.com</tt> port 4321), with its community and ratings. For serious "
                     "Backgammon, play there. ParlorNet speaks the FIBS protocol only so that vintage FIBS "
                     "clients (MacFIBS, FIBS/W, xfibs, xibc) can play ParlorNet&#39;s games, which are meant for "
                     "vintage machines, events and casual play. ParlorNet is not FIBS and is not affiliated "
                     "with it." % a(FIBS_HOME, "fibs.com"), 2))



def game_page_path(gtype):
    """A game's own page: /games/reversi.htm."""
    return "/games/%s.htm" % gtype


def first_sentence(text):
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return m.group(1) if m else text


def games_html(hosted=None, scores=False):
    """The welcome page's Games section (#games): every game in build order, one row each (anchor: the game
    type, so links to a game's row land on it): its icon (adapters/siteimages.py), its name linking to its own
    page, players, status here, and the first sentence of what it is. scores: the server shows its leaderboard
    (a link to it)."""
    rows = []
    for r in game_rows(hosted):
        about = game_about(r["type"])
        name = "<b>%s</b>" % esc(r["name"])
        if about:
            name = a(game_page_path(r["type"]), name)
        rows.append([PIC.game_img(r["type"], None) or "&nbsp;", '<a name="%s"></a>%s' % (r["type"], name),
                     esc(r["players"]), esc(r["status"]),
                     esc(first_sentence(about[0])) if about else "-"])
    return (h2("games", "Games") +
            p("These are the games ParlorNet plays, listed in the order they were added. ParlorNet wrote each "
              "one itself from the game&#39;s public rules and gives it a generic name. You can play the ones "
              "marked playable on this server now, and each game&#39;s page has its rules and the programs "
              "that play it.", 2) +
            data_table(["", "Game", "Players", "Status", "What it is"], rows, "100%", (None, None, "center")) +
            (p("Every finished game scores points: see who leads on the %s." % a(LEADERBOARD_PAGE, "leaderboard"), 2)
             if scores else ""))


def docs_link(path=""):
    """An address on the documentation site (site.docs_url, from the core's PRL), or "" when the operator
    cleared it."""
    return S.DOCS[0] + path if S.DOCS[0] else ""


def heading_anchor(text):
    """The anchor the docs site gives a heading (adapters/mdhtml.py, slug: as GitHub names it), for a heading
    that occurs once."""
    return re.sub(r"[^\w\- ]", "", text.lower(), flags=re.ASCII).strip().replace(" ", "-")


def game_page(gtype, hosted=None, nav=None):
    """/games/<type>.htm: one game: players, its status here, its type spec and protocol chapter on the docs
    site, what the game is, the rules and options played here, and where the docs list the programs that
    play it. None for an unknown game."""
    r = next((r for r in game_rows(hosted) if r["type"] == gtype), None)
    if r is None or not game_about(gtype):
        return None
    about, rules = game_about(gtype)
    m = games.by_type().get(gtype)
    facts = [("Players", esc(r["players"])), ("Status here", esc(r["status"]))]
    if docs_link():
        spec = "docs/types/%s.html" % (m.KEY.replace("/", "-") if m else gtype + "-1")
        facts.append(("For programmers", "%s, %s" % (
            docs_a(spec, "type spec"), docs_a("docs/pnp.html#" + heading_anchor(r["name"]), "protocol guide"))))
        programs = p("The %s on the docs site lists the programs that play %s on each kind of computer and "
                     "how to set them up." % (
                         docs_a("index.html#" + gtype, "Clients page"), esc(r["name"])), 2)
    else:
        programs = ""
    body = (h1((PIC.game_img(gtype) + " " if PIC.game_img(gtype) else "") + esc(r["name"])) +
            p("<br>\n".join("<b>%s:</b> %s" % kv for kv in facts), 2) +
            p(esc(about)) +
            h2("rules", "Rules and options here" if r["status"] == "playable" else "Rules and options") +
            ul(esc(x) for x in rules) +
            (_fibs_note_html() if gtype == "backgammon" else "") +
            h2("frontends", "Programs that play it") + programs +
            p("%s | %s" % (a("/welcome.htm#%s" % gtype, "All games"), a("/lobby.htm", "Play")), 2))
    return respond(200, page("ParlorNet: %s" % r["name"], body, nav=nav))


def credits_page(nav=None):
    """/credits.htm: where each photo of a computer on the welcome page comes from (adapters/img/CREDITS.tsv),
    each with the picture, what it shows linking to its file page on Wikimedia Commons, its author and its
    license, as the licenses ask; then that the drawn pictures are ParlorNet's own. A lean table
    (webstyle.plain_table), so the page stays small."""
    rows = []
    for c in PIC.credits():
        rows.append([PIC.img(PIC.MACHINE_URL % c["picture"].split("/")[1][:-len(".gif")]),
                     a(c["source"], esc(c["shows"])), html_text(c["author"]),
                     a(c["license_url"], esc(c["license"])) if c["license_url"] else esc(c["license"])])
    body = (h1("Picture credits") +
            p("The photos of the computers on the welcome page come from Wikimedia Commons. Each row names what "
              "the photo shows, which links to its page there, and its author and license.") +
            S.plain_table(["Picture", "Shows", "Author", "License"], rows) +
            p("Each picture is a changed copy of its photo: cropped, cut out from its background, scaled down to "
              "at most %d by %d pixels and reduced to 16 colors or fewer. Each is shared under the same license "
              "as its photo. The game icons and the picture for Any new computer are ParlorNet&#39;s own, drawn "
              "for this site, and come under ParlorNet&#39;s license." % PIC.MACHINE_BOX, 2) +
            p("%s | %s" % (a("/welcome.htm#%s" % MACHINES_ANCHOR, MACHINES_HEAD),
                           a(GAMES_SECTION, "Games")), 2))
    return respond(200, page("ParlorNet: picture credits", body, nav=nav))


def html_text(s):
    """Text for a page, escaped, with any character beyond ASCII as a decimal character reference."""
    return "".join(c if ord(c) < 128 else "&#%d;" % ord(c) for c in esc(s))


# ---- the protocols this server speaks now (the core's PRL) ---------------------------------------

def parse_protocols(lines):
    """PRL's lines -> (docs url or "", [(key, port, name, description)]) in the core's order."""
    url, rows, desc = "", [], {}
    for l in lines:
        f = l.split(" ", 3)
        if f[0] == "DOC" and len(f) == 2:
            url = f[1]
        elif f[0] == "PRO" and len(f) == 4 and f[2].isdigit():
            rows.append((f[1], int(f[2]), f[3]))
        elif f[0] == "PRD" and len(f) >= 3:
            desc[f[1]] = l.split(" ", 2)[2]
    return url, [(k, port, name, desc.get(k, "")) for k, port, name in rows]


# ---- play from your computer: the welcome page's guide, by machine -----------------------------------------
# Built from adapters/clients.py (PLAY_MACHINES, machine_clients, machine_generic): the same clients, games and
# ratings as the docs site's Clients page, each client linking to its own page there.

MACHINES_ANCHOR = "machines"
MACHINES_HEAD = "Play from your Computer"
OLD_ANCHORS = ("play", "ways-in", "frontends", "clients", "protocols")    # earlier sections' anchors
ANY_OLD = ("any-old", "Any old computer", "and any vintage machine not listed above")
ANY_NEW = ("any-new", "Any new computer", "a phone, a tablet or a desktop")
MEDAL_MEANS = {C.GOLD: "works fully", C.SILVER: "works once the program is patched",
               C.BRONZE: "works, with a problem in play"}
RATED_LINES = 4         # a machine's rated clients with a line each; the rest are named in one line after them


def docs_a(path, text, mark=True):
    """A link to a page of the docs site with the external-link icon after it, or just the text when the
    operator cleared site.docs_url. mark=False leaves the icon out, for Play from your Computer, which has
    dozens of these links and says once, above them, that they go to the docs site."""
    href = docs_link(path)
    if not href:
        return text
    return away(a(href, text)) if mark else a(href, text)


def medal_html(medal):
    """A rating's icon (served here, SITE_IMAGES), with its word as the ALT text; "" for not yet rated."""
    return C.medal_img(medal) if medal else ""


def client_a(c):
    return docs_a(c.href().lstrip("/"), esc(c.name), False)


def machine_ways(targets):
    """One machine's ways to play, best first, a line each: each rated client with its medal and its games
    (and the games not yet rated with it there), then the clients not yet rated there in one line, then a web
    browser and a telnet program for every game."""
    lines, more, unrated = [], [], []
    for c, groups in C.machine_clients(targets):
        medal, games_ = groups[0]
        if not medal:
            unrated.append("%s for %s" % (client_a(c), esc(C.games_text(games_))))
        elif len(lines) == RATED_LINES:
            more.append("%s %s for %s" % (medal_html(medal), client_a(c), esc(C.games_text(games_))))
        else:
            rest = "".join("; %s %s" % (esc(C.games_text(g)), C.MEDALS[m][0].lower() if m else "not yet rated")
                           for m, g in groups[1:])
            lines.append("%s %s: %s%s" % (medal_html(medal), client_a(c), esc(C.games_text(games_)), rest))
    if more:
        lines.append("Also %s." % ", ".join(more))
    if unrated:
        lines.append("Not yet rated here: %s." % ", ".join(unrated))
    browsers, telnets = C.machine_generic(targets)
    ways = (["a web browser, such as %s" % client_a(browsers[0])] if browsers else []) + (
        ["a telnet program, such as %s" % client_a(telnets[0])] if telnets else [])
    if ways:
        lines.append("Every game in %s." % ", or ".join(ways))
    return "<br>\n".join(lines)


def any_old_html():
    return "<br>\n".join([
        "Every game with a %s, or a terminal program on a %s." % (
            docs_a(C.TELNET_PAGE.lstrip("/"), "telnet program", False),
            docs_a("docs/serial.html", "modem or serial line", False)),
        "Every game on these pages, in a %s from 1995 on." % docs_a(C.BROWSERS_PAGE.lstrip("/"), "web browser",
                                                                     False)])


def any_new_html(play):
    if play:
        first = "%s in any current browser, with animated boards (%s)." % (
            a(play, "<b>ParlorNet Play</b>"), docs_a("docs/modern-client.html", "about Play", False))
    else:
        first = "These pages, in any current browser."
    return first + "<br>\n" + "%s from a terminal works too." % docs_a(C.TELNET_PAGE.lstrip("/"), "Telnet", False)


def machines_html(play=None):
    """Play from your Computer: the machines' names, each a jump to its row, then a row per machine (anchored)
    with its picture (adapters/siteimages.py) and its ways to play, best first, then Any old computer and Any new
    computer, and what the medals mean, with the link to the picture credits.
    play: ParlorNet Play's address (play_link), None while it does not run."""
    machines = [(anchor, name, note) for anchor, name, note, _ in C.PLAY_MACHINES] + [ANY_OLD, ANY_NEW]
    cells = {anchor: machine_ways(targets) for anchor, _, _, targets in C.PLAY_MACHINES}
    cells[ANY_OLD[0]], cells[ANY_NEW[0]] = any_old_html(), any_new_html(play)
    rows = ['<tr%s valign="top"><td align="center">%s</td><td>%s</td></tr>\n' % (
        ' bgcolor="%s"' % S.colors().tint if n % 2 else "", PIC.machine_img(anchor) or "&nbsp;",
        '<a name="%s"></a>%s %s' % (anchor, font(esc(name), 3, S.HEAD_FACE, S.colors().heading),
                                    font("%s<br>\n%s" % ("(%s)" % esc(note) if note else "", cells[anchor]), 2)))
            for n, (anchor, name, note) in enumerate(machines)]
    legend = " ".join("%s %s: %s." % (medal_html(m), C.MEDALS[m][0], MEDAL_MEANS[m]) for m in MEDAL_MEANS)
    return (h2(MACHINES_ANCHOR, MACHINES_HEAD, OLD_ANCHORS) +
            p(" | ".join(a("#" + anchor, esc(name)) for anchor, name, _ in machines), 2) +
            p("Find your computer: its row starts with the best way to play there.%s" % (
                " The links in it go to %s, where each program&#39;s page says how to get it and set it up."
                % docs_a("index.html", "ParlorNet Docs") if docs_link() else ""), 2) +
            '<table width="100%%" border="0" cellpadding="4" cellspacing="0">\n%s</table>\n' % "".join(rows) +
            p("%s Not yet rated: nobody has played a whole game with it there yet. The photos are from Wikimedia "
              "Commons: %s." % (legend, a(PIC.CREDITS_PAGE, "picture credits")), 1))


# ---- old addresses of the documentation, now on the docs site -------------------------------------
# Each answers with a 301 to its page on the docs site (site.docs_url), so bookmarks keep working.

MOVED = {"/frontends.htm": "index.html", "/protocols.htm": "protocols.html", "/browsers.htm": "browsers.html", "/telnet.htm": "telnet.html",
         "/eclipse-68k-fix.htm": "eclipse-68k-fix.html", "/docs/index.htm": "docs.html",
         "/docs/": "docs.html", "/docs": "docs.html", "/img/eclipse-68k-fix.gif": "img/eclipse-68k-fix.gif",
         "/download/eclipse-68k-fix-readme.txt": "download/eclipse-68k-fix-readme.txt"}
MOVED_RE = re.compile(r"^/(?:(docs/(?:[a-z0-9-]+/)?[A-Za-z0-9_-]+)\.htm|(setup/[a-z0-9-]+)\.htm|"
                      r"(download/eclipse-68k-fix\.(?:hqx|bin|dsk)))\Z")


def moved_to(path, q=None):
    """The docs site's address for an old documentation address here, or None. The Frontends page keeps its
    filter (?plat=, ?arch=), which the docs site's script reads."""
    target = MOVED.get(path)
    if target is None:
        m = MOVED_RE.match(path)
        if not m:
            return None
        target = (m.group(1) or m.group(2)) + ".html" if not m.group(3) else m.group(3)
    if path == "/frontends.htm" and q:
        kept = [(k, q[k][0]) for k in ("plat", "arch") if re.match(r"^[a-z0-9]{1,8}\Z", (q.get(k) or [""])[0])]
        target += "?" + urlencode(kept) if kept else ""
    return target


def moved_page(req, path, q=None):
    """A 301 to the docs site, its body a plain HTML 3.2 page with the link; when the operator cleared
    site.docs_url, a page saying the documentation is not served here."""
    target = moved_to(path, q)
    if target is None:
        return None
    if not docs_link():
        return notice("Not found", "Not here", "The documentation is not served by this server.",
                      [("/welcome.htm", "Welcome")], 404)
    loc = docs_link(target)
    return respond(301, bare_page("Moved", "<p>This page has moved to ParlorNet Docs, at %s.</p>"
                                  % a(loc, esc(loc))), extra="Location: %s\r\n" % loc)


# ---- a tiny GIF writer, for the game modules' board pieces ----------------------

def make_gif(w, h, palette, pixels):
    """Uncompressed-in-effect LZW GIF (a clear code every two pixels keeps the
    code width fixed). Palette of 4 or 8 colors."""
    depth = 2 if len(palette) <= 4 else 3
    palette = list(palette) + [(0, 0, 0)] * ((1 << depth) - len(palette))
    out = bytearray(b"GIF89a")
    out += bytes([w & 255, w >> 8, h & 255, h >> 8, 0x80 | 0x70 | (depth - 1), 0, 0])
    for r, g, b in palette:
        out += bytes([r, g, b])
    out += b"," + bytes([0, 0, 0, 0, w & 255, w >> 8, h & 255, h >> 8, 0])
    out.append(depth)  # LZW minimum code size
    bits, nbits, data = 0, 0, bytearray()
    width = depth + 1

    def put(code):
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += width
        while nbits >= 8:
            data.append(bits & 255)
            bits >>= 8
            nbits -= 8
    clear, eoi = 1 << depth, (1 << depth) + 1
    i = 0
    while i < len(pixels):
        put(clear)
        put(pixels[i])
        if i + 1 < len(pixels):
            put(pixels[i + 1])
        i += 2
    put(eoi)
    if nbits:
        data.append(bits & 255)
    for j in range(0, len(data), 255):
        chunk = data[j:j + 255]
        out.append(len(chunk))
        out += chunk
    out += b"\x00;"
    return bytes(out)


# Every game's images, from its module in adapters/games/ (images()): path -> GIF bytes, made on first use.
IMAGES = games.Images()
# The site's own images, the emblem on every page's masthead (webstyle.EMBLEM) and the icon after each link to
# the docs site (webstyle.EXTERNAL): served and cached like the games' images.
SITE_IMAGES = {S.EMBLEM: S.emblem_gif(make_gif), S.EXTERNAL: S.external_gif()}
SITE_IMAGES.update({C.MEDALS[m][1]: MEDAL_ICONS[C.MEDALS[m][1].rsplit("/", 1)[1]]() for m in (C.GOLD, C.SILVER, C.BRONZE)})
SITE_IMAGES.update(PIC.IMAGES)      # the computers' photos and the games' icons (adapters/siteimages.py)

# ---- one image per board (adapters/board.py) ------------------------------------
#
# A game page shows its board as one GIF (/board.gif) with an image map: USEMAP for browsers with
# client-side maps, ISMAP (/click.htm, the point looked up in the same areas) for the others. The image's
# URL names the table, the view's sequence number (q), the seat (st), the picked piece (sel) and a digest of
# the view (d), so a browser may keep it; /board.gif and /click.htm check the session and work the view
# out again, as /table.htm does, so a URL never shows more than its own seat may see.

BOARDS = BoardCache()
CLICK_TARGETS = ("/table.htm", "/move.htm", "/und.htm", "/end.htm")    # where a board's areas may lead
BOARD_TEXT_MAX = 40         # up to this many areas are also listed as text links under the image


def _drop(href, keys):
    """A link without some of its query fields."""
    u = urlsplit(href)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in keys]
    return url(u.path, **dict(q))


def board_key(draw, tid, seat, sel, digest):
    return (draw.__module__, str(tid), seat, sel or "", digest)


def board_html(draw, v, tid, link, seat, sel, alt, text=True):
    """A game module's board as one image with its map: draw is its board_image(v, seat, sel) -> Canvas.
    text: list the areas as text links under the image too (when there are at most BOARD_TEXT_MAX), for
    browsers with images off; a module with its own text list or form leaves it out."""
    digest = view_digest(v)
    b = BOARDS.render(board_key(draw, tid, seat, sel, digest), lambda: draw(v, seat, sel))
    keys = dict(t=tid, q=v.get("seq", 0), st=seat, sel=sel or None, d=digest)
    src = _drop(link("/board.gif", **keys), ("x", "r", "js"))
    img = '<img src="%s" width="%d" height="%d" border="0" alt="%s"' % (esc(src), b.w, b.h, esc(alt))
    if not b.areas and not b.anywhere:
        return img + ">"
    usemap = ' usemap="#board"' if b.areas else ""
    out = ['<a href="%s">%s ismap%s></a>' % (esc(link("/click.htm", **keys)), img, usemap)]
    if b.areas:
        out.append('<map name="board">')
        for shape, coords, area_alt, path, params in b.areas:
            out.append('<area shape="%s" coords="%s" href="%s" alt="%s">' % (
                shape, ",".join(str(c) for c in coords), esc(link(path, t=tid, **dict(params))), esc(area_alt)))
        out.append("</map>")
        if text and len(b.areas) <= BOARD_TEXT_MAX:
            seen, links = set(), []
            for _, _, area_alt, path, params in b.areas:
                if (path, params) not in seen:
                    seen.add((path, params))
                    links.append(a(link(path, t=tid, **dict(params)), esc(area_alt)))
            out.append('<br><font size="2">%s</font>' % " ".join(links))
    return "\n".join(out)


# ---- HTML helpers -------------------------------------------------------------

# esc() and a() come from webstyle (game modules import esc from here).

def page(title, body, refresh=None, current=None, nav=None, compact=False):
    """A whole page in the site's frame (webstyle.frame): the masthead and navigation bar, the body, the
    footer. current: the public nav entry this page is; nav, compact: the signed-in pages' bar."""
    return S.frame(title, body, current=current, nav=nav, compact=compact, refresh=refresh)


def bare_page(title, body):
    """A page with no frame: the body of a redirect, which browsers follow without showing it."""
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">\n<html>\n<head>\n<title>%s</title>\n'
            "</head>\n<body>\n%s\n</body>\n</html>\n" % (esc(title), body))


def url(path, **q):
    q = {k: v for k, v in q.items() if v is not None and v != ""}
    return path + ("?" + urlencode(q, quote_via=quote) if q else "")


S.DOCS[0] = DOCS_URL    # the docs site, until the core's PRL says (site.docs_url)
PROTOCOLS_SECS = 5      # the protocols (PRL) are asked again after this long
# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("HTTP", "HTTP and HTML 3.2", "HTML 3.2 pages for any browser with tables and forms")
HOSTS = []      # web.hosts: the names this site answers to ("name" or "name:port"); empty: any well-formed one
PROXIES = []    # web.proxies: the reverse proxies (ip_networks) whose X-Forwarded-For and -Proto are believed
PUBLIC_URL = [""]   # web.public_url: the site's own address (https://games.example.org/), for canonical links


def allowed_host(h):
    """The Host header if web.hosts lists it (or its name without the port),
    else the first listed one; without web.hosts, h itself."""
    if not HOSTS:
        return h
    hl = h.lower()
    for x in HOSTS:
        if hl == x.lower() or (":" not in x and hl.split(":")[0] == x.lower()):
            return h
    return HOSTS[0]


def host_of(req):
    h = allowed_host(req["headers"].get("host", ""))
    h = h.split(":")[0] if not h.startswith("[") else h
    return h if re.match(r"^[A-Za-z0-9.-]+\Z", h or "") else "parlornet.org"


HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*\.?|\[[0-9A-Fa-f:.]{2,45}\])"
                     r"(?::\d{1,5})?\Z")


def location_host(req):
    """The host (and port) for a Location header: the Host header only if it
    is a well-formed host name or address with an optional port, else the
    default name (as for a browser that sends no Host); never anything else
    the client sent. With web.hosts set, only a listed name (any other
    falls back to the first listed)."""
    h = req["headers"].get("host", "")
    if len(h) <= 260 and HOST_RE.match(h):
        return allowed_host(h)
    return HOSTS[0] if HOSTS else host_of(req)


def host_ok(h):
    """A Host header that names this site: well formed and, with web.hosts set, one of them."""
    return len(h) <= 260 and bool(HOST_RE.match(h)) and allowed_host(h) == h


def respond(status, body, ctype="text/html", extra=""):
    if isinstance(body, str):
        body = body.encode("ascii", "replace")
    if ctype == "text/html":     # pages are per player: never cached (and a cookieless page has s= in it)
        extra = "Cache-Control: no-store\r\nPragma: no-cache\r\n" + extra
    reasons = {200: "OK", 301: "Moved Permanently", 302: "Found", 304: "Not Modified", 400: "Bad Request",
               403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 426: "Upgrade Required",
               503: "Service Unavailable"}
    head = ("HTTP/1.0 %d %s\r\nServer: parlornet-web\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Connection: close\r\n%s\r\n" % (status, reasons.get(status, "OK"), ctype, len(body), extra))
    return head.encode("ascii") + body


def canonical(req, head):
    """The page's address under web.public_url, for a Link rel=canonical header: for a GET of a page (200,
    HTML) with no query, so the site's copies at other addresses (plain http, the port of its own, another
    name) point search engines at one. "/" and the welcome page are the address itself. None without the
    setting, or for anything else."""
    base = PUBLIC_URL[0]
    if not base or req["method"] not in ("GET", "HEAD") or req["query"] or req.get("ismap"):
        return None
    if not head.startswith(b"HTTP/1.0 200 ") or b"\r\nContent-Type: text/html" not in head:
        return None
    path = req["path"]
    if path in ("/", "/index.htm", "/index.html", "/welcome.htm"):
        path = "/"
    return base.rstrip("/") + path


def redirect(req, path, extra=""):
    loc = "%s://%s%s" % (req.get("proto") or "http", location_host(req), path)
    return respond(302, bare_page("Moved", "<p>Go to %s</p>" % a(path, "the next page")),
                   extra="Location: %s\r\n%s" % (loc, extra))


def set_cookie(sid):
    """The session cookie: no expiry (it ends with the browser), not readable by
    scripts. Old browsers ignore the attributes they do not know."""
    return "Set-Cookie: %s=%s; Path=/; HttpOnly; SameSite=Lax\r\n" % (COOKIE, sid)


CLEAR_COOKIE = "Set-Cookie: %s=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT\r\n" % COOKIE


def set_key_cookie(req, name, key):
    """Stay signed in: the sign-in key in a cookie of its own for a long time, never readable by scripts, sent
    with links from other sites only for a plain visit (Lax), and only over https when the page came that way."""
    return "Set-Cookie: %s=%s.%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s\r\n" % (
        KEY_COOKIE, name, key, KEY_COOKIE_SECS, "; Secure" if req.get("proto") == "https" else "")


CLEAR_KEY = "Set-Cookie: %s=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT\r\n" % KEY_COOKIE


def cookie_key(req):
    """(name, key) from the Stay signed in cookie, or None."""
    for part in req["headers"].get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        m = KEY_COOKIE_RE.match(v) if k == KEY_COOKIE else None
        if m:
            return m.group(1), m.group(2)
    return None


def cookie_sid(req):
    for part in req["headers"].get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and SID_RE.match(v):
            return v
    return None


def hidden_fields(href):
    """The query of a link as hidden form fields (session and display choices for GET forms)."""
    return "".join('<input type="hidden" name="%s" value="%s">' % (esc(k), esc(v[0]))
                   for k, v in parse_qs(urlsplit(href).query).items())


def err_reason(text):
    """'wait=60 Wrong code; ...' -> 'Wrong code; ...' (the wait is for programs). Only the first letter is
    made a capital, so a second sentence and a name keep theirs."""
    text = re.sub(r"^wait=\d+ ", "", text)
    return text[:1].upper() + text[1:]


# ---- pages --------------------------------------------------------------------

SIGNIN_PAGE = "/login.htm"
SIGNUP_PAGE = "/signup.htm"
NAMES_RULE = "Names are letters, digits, - and _, up to 12."


def signin_form(auth, name=""):
    """The sign-in form: the name, and the credential box unless the server signs in by name alone. The
    password answers the server's credential question only, if it asks one. It only signs in: a name with
    no account is told so, never signed up."""
    method, label = auth[0], auth[1]
    cred = ""
    if method != "none":
        cred = (' &nbsp; %s: <input type="password" name="k" size="16" maxlength="64">'
                ' &nbsp; <input type="checkbox" name="stay" value="1"> Stay signed in' % esc(label))
    return ('<form action="%s" method="post">\n<p>%s</p>\n</form>\n'
            % (SIGNIN_PAGE, font('Your name: <input name="n" value="%s" size="12" maxlength="12">%s '
                                 '<input type="submit" value="Sign in">' % (esc(name), cred))))


def signup_form(name=""):
    """The new-account form: the name wanted; the server's sign-up steps follow, a page each."""
    return ('<form action="%s" method="post">\n<p>%s</p>\n</form>\n'
            % (SIGNUP_PAGE, font('Name you want: <input name="n" value="%s" size="12" maxlength="12"> '
                                 '<input type="submit" value="Make the account">' % esc(name))))


def signup_steps(auth):
    """What the server's sign-up asks, in a sentence (from the greeting: sign-in and sign-up methods)."""
    signin, signup = auth[0], (auth[2] if len(auth) > 2 else "password")
    if signup == "none":
        return "Choose a name, and it is yours."
    if signup == "email":
        return ("Choose a name, then give an email address, and we email you a password. The first time you "
                "sign in with it, you choose a password of your own.")
    return "Choose a name, then a password, and type the password again."


def unknown_name_html(name):
    """A sign-in for a name with no account: said so, with the way to make one (names never go in links)."""
    return ("There is no account named %s here. Check the spelling, or make a %s."
            % (esc(name), a(SIGNUP_PAGE, "New account")))


def taken_name_html(name):
    return "The name %s is taken: choose another, or %s if it is yours." % (esc(name), a(SIGNIN_PAGE, "sign in"))


def note_html(message, raw=False):
    """A message at the top of a page: plain text (escaped), or raw HTML of our own (a link in it)."""
    if not message:
        return ""
    return callout(p("<b>%s</b>" % (message if raw else esc(message)))) + "<br>\n"


def signin_page(auth, message="", name="", raw=False):
    """/login.htm: sign in. Its own page, so a sign-in can never turn into a sign-up."""
    forgot = ("Forgot your password? Type reset in the password box, and we email you a new one. " if
              len(auth) > 2 and auth[2] == "email" else "")
    body = (h1("Sign in") + note_html(message, raw) +
            callout(signin_form(auth, name)) +
            p("%sNo account yet? %s. %s" % (forgot, a(SIGNUP_PAGE, "New account"), esc(NAMES_RULE)), 2))
    return respond(200, page("ParlorNet: sign in", body, current=SIGNIN_PAGE))


def signup_page(auth, message="", name="", raw=False):
    """/signup.htm: a new account, through the server's sign-up steps."""
    body = (h1("New account") + note_html(message, raw) + p(esc(signup_steps(auth))) +
            callout(signup_form(name)) +
            p("%s Already have an account? %s." % (esc(NEW_NAME_RULE), a(SIGNIN_PAGE, "Sign in")), 2))
    return respond(200, page("ParlorNet: new account", body, current=SIGNUP_PAGE))


URL_RE = re.compile(r"(https?://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+[A-Za-z0-9/_~=#-])")


MOTD_HEAD = "Message of the Day"


def motd_html(motd):
    """The message of the day as it is, in <pre> (an ASCII layout reads the same as on telnet), escaped,
    with its web addresses as links, in an ivory box under a light green header row, framed in felt; the box
    is as wide as the text. Dark text on both (browsers without cell colors show plain text). A line break
    after the box keeps what follows, a note or the signed-in callout, from touching it."""
    def line(l):
        parts = URL_RE.split(l)
        return "".join(a(x, esc(x)) if i % 2 else esc(x) for i, x in enumerate(parts))
    return ('<a name="motd"></a><table border="0" cellpadding="1" cellspacing="0"><tr><td bgcolor="%s">\n'
            '<table width="100%%" border="0" cellpadding="6" cellspacing="0">\n'
            '<tr><td bgcolor="%s">%s</td></tr>\n'
            '<tr><td bgcolor="%s"><pre>%s</pre></td></tr>\n'
            '</table>\n</td></tr></table>\n<br>\n'
            % (S.colors().edge, S.colors().bar, font("<b>%s</b>" % MOTD_HEAD, color=S.colors().heading),
               S.colors().tint, "\n".join(line(l) for l in motd)))


# The welcome page's sections, in order: (anchor, heading). Its contents line links each one.
WELCOME_SECTIONS = [("welcome", "Welcome"), ("signin", "Sign-In"), ("games", "Games"), (MACHINES_ANCHOR, MACHINES_HEAD)]


def welcome(req, message="", auth=("password", "Password", "password"), motd=(), protocols=None, nav=None,
            lobby=None, hosted=None, scores=False):
    """The welcome page: its heading and a contents line, then four sections. Welcome holds the message of
    the day (all of the welcome text is there, so the operator writes it) and any note. Sign-In holds the two
    ways to start, Sign in and New account, each its own form. Games lists every game (games_html), and Play
    from your Computer the ways to play from each machine (machines_html). protocols: the core's PRL, for
    ParlorNet Play's address. nav and lobby: for a signed-in player, the navigation bar (site_nav) and the
    lobby's link, which stands in for the two forms. hosted and scores: the core's games and whether it shows
    its leaderboard, for Games."""
    if lobby:
        start = callout(p("You are signed in. The %s has your tables and invitations." % a(lobby, "<b>Lobby</b>")))
    else:
        start = (callout(signin_form(auth)) +
                 h3("new", "New account") +
                 p("%s %s" % (esc(signup_steps(auth)), esc(NEW_NAME_RULE)), 2) +
                 callout(signup_form()))
    contents = p(" | ".join(a("#" + anchor, esc(head)) for anchor, head in WELCOME_SECTIONS), 2)
    body = ('<a name="welcome"></a>' + h1("Welcome") + contents + (motd_html(motd) if motd else "") +
            note_html(message) + h2("signin", "Sign-In") + start + games_html(hosted, scores) +
            machines_html(play_link(host_of(req), protocols)))
    return respond(200, page("ParlorNet", body, current="/welcome.htm", nav=nav))


FLOW_HEADINGS = {"signin": "Sign in", "signup": "New account", "change": "Change your password"}


def ask_page(lid, ask, message="", kind="signin"):
    """One step of the server's sign-in, sign-up or reset: its info lines, then
    its question (a text or password box, or yes/no). Nothing method-specific;
    the heading says which flow it is (kind: signin, signup, change)."""
    if ask.yn:
        field = ('<input type="radio" name="v" value="y" checked> Yes '
                 '<input type="radio" name="v" value="n"> No')
    else:
        field = '<input type="%s" name="v" size="24" maxlength="64">' % ("password" if ask.secret else "text")
    info = "".join(p(esc(l)) for l in ask.info if l)
    form = ('<form action="/login.htm" method="post">\n'
            '<input type="hidden" name="lid" value="%s"><input type="hidden" name="f" value="%s">\n'
            "<p>%s</p>\n</form>\n"
            % (esc(lid), esc(ask.field), font("%s %s <input type=\"submit\" value=\"Go\">" % (
                esc(ask.label) + ("" if ask.label.endswith(("?", ")", ":")) else ":"), field))))
    heading = FLOW_HEADINGS.get(kind, "Sign in")
    start = {"signup": (SIGNUP_PAGE, "Start over"), "change": ("/lobby.htm", "Back to the lobby")}.get(
        kind, (SIGNIN_PAGE, "Start over"))
    body = (h1(heading) + (p("<b>%s</b>" % esc(message)) if message else "") + info + callout(form) +
            p(a(*start), 2))
    return respond(200, page(heading, body))


def site_nav(link=None, current=None):
    """The navigation bar of every page: Welcome, Lobby, Docs (the docs site, left out when the operator
    cleared site.docs_url), and last Sign In, or Sign Out while a player is signed in. link: the signed-in link
    maker (Server.linker: the session for browsers without cookies, and the action check), None when nobody
    is; current: the path of the page shown, whose entry is in bold."""
    if link is None:
        return S.nav_html(S.public_nav(), current, (S.DOCS[0],))
    plain = getattr(link, "plain", link)
    items = [(plain("/welcome.htm"), "Welcome"), (link("/lobby.htm"), "Lobby")]
    here = dict(zip(("/welcome.htm", "/lobby.htm"), (h for h, _ in items))).get(current)
    docs = [(S.DOCS[0], "Docs")] if S.DOCS[0] else []
    return S.nav_html(items + docs + [(link("/out.htm"), "Sign Out")], here, (S.DOCS[0],))


def signed_in_nav(link):
    """The bar of the signed-in pages: site_nav with Sign Out. Lobby stays a link on the lobby too, where it
    is the way to look again."""
    return site_nav(link)


def lobby_page(me, link, games, tables, note="", auth="password", invites=(), bots=None, online=None, more=0,
               scores=False):
    """bots: {table id: the names of the bots seated there} (adapters/botflag.py), marked in the list.
    online: WHO's players [(name, is a bot)], the players online now (None: a core without WHO).
    more: how many tables LST left out (its MORE line). scores: the server shows its leaderboard."""
    names = {g["key"]: g["name"] for g in games}
    rows = []
    tables = sorted(tables, key=lambda t: (me not in t["names"], botflag.list_rank(t), int(t["id"])))
    for t in tables:
        here = (bots or {}).get(t["id"], ())
        uses = t.get("clients") or {}       # LST clients: what each seated player plays with, where shown to me
        who = ", ".join(player_link(link, n) + (" (bot)" if n.lower() in here else "") +
                        (" " + small(esc(uses[i])) if uses.get(i) else "")
                        for i, n in enumerate(t["seated"]) if n != "-") or "-"
        if t["status"] == "open" and me not in t["names"]:
            act = a(link("/join.htm", t=t["id"]), "Join")
        elif me in t["names"]:
            act = "<b>%s</b>" % a(link("/table.htm", t=t["id"]), "Your game: go")
        else:
            act = a(link("/table.htm", t=t["id"], w=1), "Watch")
        status = t["status"] + (", private" if t["vis"] == "private" else "")
        rows.append([esc(t["id"]), esc(names.get(t["key"], t["key"].split("/")[0])), esc(status),
                     "%d/%d" % (t["filled"], t["seats"]), who, act])
    tbl = (data_table(["#", "Game", "Status", "Seats", "Players", ""], rows, "100%", ("right",)) if rows else
           p("No tables yet. Start one below."))
    if more:
        tbl += p("%d more %s not listed." % (more, "table is" if more == 1 else "tables are"), 2)
    inv_html = ""
    if invites:
        inv_rows = [[esc(i["id"]), esc(names.get(i["key"], i["key"].split("/")[0])), esc(i["from"]),
                     "%s | %s" % (a(link("/join.htm", t=i["id"]), "<b>Join</b>"),
                                  a(link("/uninvite.htm", t=i["id"], who=me), "Decline"))] for i in invites]
        inv_html = h2(None, "Invitations for you") + data_table(["#", "Game", "From", ""], inv_rows, "100%",
                                                                 ("right",))
    turns = [("5m", "5 minutes"), ("15m", "15 minutes"), ("1h", "1 hour"), ("1d", "1 day"),
             ("3d", "3 days"), ("5d", "5 days")]

    def select(name, options, chosen):
        return '<select name="%s">%s</select>' % (name, "".join(
            '<option value="%s"%s>%s' % (v, " selected" if v == chosen else "", lbl) for v, lbl in options))

    def start_form(g):
        """One game's form to start a table: its icon in its own cell beside a small table, with the game's name
        on top, then a row for each choice, its label in the first column and the control in the second, so
        the choices read down a column, and Play the bot and the button last. Rules shows only for a game with
        several rulesets, and Players only for one that takes more than two."""
        rows = []
        rulesets = g.get("rulesets") or [g["rules"]]
        if len(rulesets) > 1:
            rows.append(("Rules:", select("ru", [(esc(r), esc(rules_name(g["key"], r))) for r in rulesets],
                                          esc(rulesets[0]))))
        lo, hi = g.get("min") or 2, g.get("max") or 2
        if hi > lo:
            rows.append(("Players:", select("np", [(str(n), str(n)) for n in range(lo, hi + 1)], str(lo))))
        rows += [("Time per turn:", select("turn", turns, "1d")),
                 ("Table:", small('<input type="radio" name="vis" value="public" checked> Public<br>\n'
                                  '<input type="radio" name="vis" value="private"> Private (by invitation)')),
                 ("", small('<input type="checkbox" name="bot" value="1"> Play the bot') +
                  '<br>\n<input type="submit" value="Start a game of %s">' % esc(g["name"]))]
        icon = PIC.game_img(g["key"].split("/")[0]) or "&nbsp;"
        return ('<form action="/new.htm" method="get">\n<input type="hidden" name="g" value="%s">%s\n'
                '<table border="0" cellpadding="2" cellspacing="0">\n'
                '<tr valign="top"><td rowspan="%d">%s</td><td colspan="2">%s</td></tr>\n%s'
                '</table>\n</form>\n'
                % (esc(g["key"]), hidden_fields(link("/new.htm")), len(rows) + 1, icon,
                   font("<b>%s</b>" % esc(g["name"]), 3, S.HEAD_FACE, S.colors().heading),
                   "".join('<tr><td align="right"%s nowrap>%s</td><td>%s</td></tr>\n'
                           % (' valign="top"' if "<br>" in control else "", small(label), control)
                           if label else "<tr><td></td><td>%s</td></tr>\n" % control for label, control in rows)))

    new = "".join(start_form(g) for g in group_games(games) if g["key"] in GAME_PAGES)
    account = [a(link(PLAYER_PAGE), "Your profile"), a(link(PROFILE_PAGE), "Preferred machine"),
               a(link(COLOURS_PAGE), "Grid Words colors"), a(link(BG_HOME_PAGE), "Backgammon board"),
               a(link("/out.htm"), "Sign out"),
               a(link("/out.htm", all=1), "Sign out everywhere")]
    if auth == "password":
        account.insert(0, a(link("/password.htm"), "Change your password") +
                       " (this signs you out everywhere else)")
    body = (h1("Lobby") +
            p("You are <b>%s</b> (%s). %s" % (esc(me), a("#account", "your account"), esc(note))) +
            (p("Online now: %s" % ", ".join(player_link(link, n) + (" (bot)" if bot else "")
                                            for n, bot in ((n, n.lower() in {b.lower() for b, x in online if x})
                                                           for n in botflag.listed(online))), 2)
             if online else "") +
            p("The latest turns at public tables: %s.%s" % (
                a(link(ACTIVITY_PAGE), "Activity"),
                " Who has the most points: the %s." % a(LEADERBOARD_PAGE, "leaderboard") if scores else ""), 2) +
            inv_html +
            h2(None, "Tables") + tbl +
            h2(None, "Start a table") +
            p("To play the bot, check Play the bot when you start a table. It sits down in a few seconds.", 2) +
            new +
            h2("account", "Your account") +
            p(" | ".join(account), 2))
    return respond(200, page("ParlorNet lobby", body, nav=signed_in_nav(link), compact=True))


COLOURS_PAGE = "/colours.htm"
BG_HOME_PAGE = "/bghome.htm"

# ---- ParlorNet Play (docs/modern-client.md): its own server extension (adapters/playserver.py) --------------

PLAY_URL = [""]         # play.public_url: where /play and /play/ send a browser (301); "" when it is not set
PLAY_KEY = "HTTPS"      # the Play extension's protocol key (playserver.PROTOCOL): in PRL while it runs


def play_link(host, protocols):
    """Where ParlorNet Play is, for the welcome page: the public URL, or this host at the port the Play
    extension declared; None while it does not run (not in the core's PRL)."""
    port = next((port for key, port, _, _ in protocols or () if key == PLAY_KEY), None)
    if port is None:
        return None
    return PLAY_URL[0] or "http://%s:%d/play/" % (host, port)


def play_moved(req, protocols):
    """/play and anything under it: ParlorNet Play is its own extension. A 301 to play.public_url when it is
    set; else a page linking to the Play extension's port while it runs; else a 404 saying it is not here."""
    if PLAY_URL[0]:
        return respond(301, bare_page("Moved", "<p>ParlorNet Play is at %s</p>" % a(PLAY_URL[0], esc(PLAY_URL[0]))),
                       extra="Location: %s\r\n" % PLAY_URL[0])
    link = play_link(host_of(req), protocols)
    if link:
        return notice("ParlorNet Play", "ParlorNet Play", "ParlorNet Play, for modern browsers and phones, has "
                      "its own port.", [(link, "ParlorNet Play"), ("/welcome.htm", "Welcome")])
    return notice("Not found", "Not found", "ParlorNet Play is not offered on this server.",
                  [("/welcome.htm", "Welcome")], 404)


def swatch_url(value):
    """The preview image of a kept palette value: a preset's own GIF, or /swatch.gif for a player's own."""
    v = PAL.encode(PAL.palette(value))
    if v in PAL.PRESETS:
        return "/img/gridwords/pal-%s.gif" % v
    return "/swatch.gif?" + urlencode({"c": v})


def colours_page(link, value, note="", tid=""):
    """Grid Words colors (/colours.htm): a preset, or the player's own color for each element, kept with the
    player (PRF gwcolors, the web's own: the modern client at /play/ shares it). tid: the table to go back to."""
    pal = PAL.palette(value)
    cur = PAL.preset_of(pal)
    radios = []
    for name in PAL.PRESETS:
        radios.append('<input type="radio" name="preset" value="%s"%s> <img src="/img/gridwords/pal-%s.gif" '
                      'width="145" height="25" align="middle" alt="%s colors"> %s<br>'
                      % (name, " checked" if cur == name else "", name, esc(PAL.PRESET_NAMES[name]),
                         esc(PAL.PRESET_NAMES[name])))
    fields = "".join('<tr><td>%s</td><td><input name="%s" size="8" maxlength="7" value="#%s"></td></tr>\n'
                     % (font(esc(PAL.ELEMENT_NAMES[k])), k, PAL.rgbhex(pal[k])) for k in PAL.ELEMENTS)
    radios.append('<input type="radio" name="preset" value="custom"%s> Your own colors, as #RRGGBB '
                  '(<img src="%s" width="145" height="25" align="middle" alt="Your colors now">):'
                  % (" checked" if cur == "custom" else "", esc(swatch_url(value))))
    back = link("/table.htm", t=tid) if tid else link("/lobby.htm")
    body = (h1("Grid Words colors") +
            (p("<b>%s</b>" % esc(note)) if note else "") +
            p("Choose the colors of your Grid Words board; other players don&#39;t see them. Premium squares "
              "keep their names (3W triple word, 2W double word, 3L triple letter, 2L double letter) and word "
              "squares keep a frame in any colors. Your choice is kept for your next visit, here and in the "
              "modern client.") +
            '<form action="%s" method="get">\n%s<input type="hidden" name="save" value="1">%s\n'
            % (COLOURS_PAGE, hidden_fields(link(COLOURS_PAGE, t=tid or None)), font("<br>\n".join(radios))) +
            '<table border="0" cellpadding="2">\n%s</table>\n<input type="submit" value="Save"></form>\n'
            % fields + p(a(back, "Back to the table" if tid else "Back to the lobby")))
    return respond(200, page("Grid Words colors", body, nav=signed_in_nav(link), compact=True))


def bghome_page(link, value, note=""):
    """Backgammon board (/bghome.htm): which side the player's home board is drawn on, kept with the player
    (PRF bghome, the web's own: ParlorNet Play shares it). Each Backgammon table links here with t= and h=,
    which switches at once and goes back to the table."""
    radios = "<br>\n".join(
        '<input type="radio" name="h" value="%s"%s> %s' % (side, " checked" if side == value else "", label)
        for side, label in (("right", "On the right, with points 1 to 6 at the bottom right (the usual way)"),
                            ("left", "On the left, with points 1 to 6 at the bottom left (the mirror image)")))
    body = (h1("Backgammon board") +
            (p("<b>%s</b>" % esc(note)) if note else "") +
            p("Choose which side your home board is drawn on. Only your own view changes: the points keep "
              "their numbers, and moves are written the same either way. Your choice is kept for your next "
              "visit, here and in the modern client.") +
            '<form action="%s" method="get">\n%s%s\n<br><input type="submit" value="Save"></form>\n'
            % (BG_HOME_PAGE, hidden_fields(link(BG_HOME_PAGE)), font(radios)) +
            p(a(link("/lobby.htm"), "Back to the lobby")))
    return respond(200, page("Backgammon board", body, nav=signed_in_nav(link), compact=True))


# ---- who is playing with what (CLI, TRN, FEED, PFL; docs/protocol.md) ----------------------------------------

# The browser, from its User-Agent, as the core records it with each turn played on these pages (CLI PROG): the
# first pattern that matches, with its version. A catalog id names the browser as adapters/clients.py does
# (without a bracketed note); anything else is the name itself. The order matters: modern browsers carry the
# others' tokens too ("like Gecko", "Safari/"), and Netscape 1 to 4 are a bare "Mozilla/n" with no "compatible".
UA_BROWSERS = [
    (r"\bMill/([0-9][\w.]*)", "mill"),
    (r"\bEdg(?:e|A|iOS)?/(\d+)", "edge"),
    (r"\b(?:OPR|Opera)[/ ](\d+)", "Opera"),
    (r"\biCab[/ ]([0-9][\w.]*)", "icab"),
    (r"\bNetscape\d?/([0-9][\w.]*)", "netscape"),
    (r"\b(?:Firefox|FxiOS)/(\d+)", "firefox"),
    (r"\b(?:Chrome|CriOS)/(\d+)", "chrome"),
    (r"\bVersion/(\d+(?:\.\d+)?)\b.*\bSafari/", "safari"),
    (r"\bMSIE (\d+\.\d+)", "msie"),
    (r"\bTrident/.*\brv:(\d+)", "msie"),
    (r"\bCyberdog/([0-9][\w.]*)", "cyberdog"),
    (r"\bMacWeb/([0-9][\w.]*)", "macweb"),
    (r"\bNCSA[ _]Mosaic/([0-9][\w.]*)", "mosaic"),
    (r"\bAWeb/([0-9][\w.]*)", "aweb"),
    (r"\bCAB/([0-9][\w.]*)", "cab"),
    (r"\bLynx/(\d+(?:\.\d+)*)", "Lynx"),
    (r"^Mozilla/([1-4]\.\d+)(?!.*compatible)", "netscape"),
]
UA_NAMES = {"aweb": "AWeb"}         # where the UA's name is not the catalog's
# The machine (CLI MACH), from the User-Agent's platform words: (pattern, name), the first that matches.
UA_MACHINES = [
    (r"\biPhone\b", "iPhone"), (r"\biPad\b", "iPad"), (r"\bAndroid\b", "Android"), (r"\bCrOS\b", "ChromeOS"),
    (r"\bWindows NT 5\.0\b", "Windows 2000"), (r"\bWindows (?:NT 5\.[12]|XP)\b", "Windows XP"),
    (r"\bWin(?:dows )?98\b", "Windows 98"), (r"\bWin(?:dows )?95\b", "Windows 95"),
    (r"\bWin(?:16|dows 3\.1|3\.1)\b|\b16bit\b", "Windows 3.1"), (r"\bWindows NT [6-9]|\bWindows NT 1\d", "Windows"),
    (r"\bWin(?:dows )?NT\b", "Windows NT"), (r"\bWin(?:dows|32|64)\b", "Windows"),
    (r"\b(?:Mac_PowerPC|Mac_PPC|PPC Mac)\b|\bMacintosh\b.*\bPPC\b", "Power Macintosh"),
    (r"\b(?:Macintosh|Mac_68000|Mac_68K|Mac OS)\b", "Macintosh"),
    (r"\bAmiga\b", "Amiga"), (r"\bAtari\b", "Atari ST"), (r"\bOS/2\b", "OS/2"), (r"\bBeOS\b", "BeOS"),
    (r"\b(?:MacWeb|Cyberdog)\b", "Macintosh"), (r"\bSunOS\b", "SunOS"), (r"\bLinux\b", "Linux"),
    (r"\bFreeBSD\b", "FreeBSD"), (r"\bX11\b", "Unix"),
]


def browser_claim(ua):
    """(program, machine) from a User-Agent, for the core to record with the turns played here (CLI):
    ("Netscape Navigator 3.0", "Power Macintosh") for "Mozilla/3.0 (Macintosh; I; PPC)". A browser not in
    UA_BROWSERS is its first word and version ("Wget 1.21"); no User-Agent, or one that is not a word, names
    nothing (""), and an unknown platform is ""."""
    ua = " ".join((ua or "").split())[:300]
    prog = ""
    for pat, who in UA_BROWSERS:
        m = re.search(pat, ua)
        if m:
            name = UA_NAMES.get(who) or (C.BY_ID[who].name.split(" (")[0] if who in C.BY_ID else who)
            prog = "%s %s" % (name, m.group(1).rstrip("."))
            break
    if not prog:
        m = re.match(r"([A-Za-z][A-Za-z0-9_.-]*)(?:/([0-9][0-9A-Za-z.]*))?", ua)
        prog = " ".join(x for x in (m.groups() if m else ()) if x)
    mach = next((name for pat, name in UA_MACHINES if re.search(pat, ua)), "")
    return claim_text(prog), mach


# The pages that play a move, and those that sit down (the table is told what the player plays with: CLIENT)
CLAIM_PATHS = ("/move.htm", "/und.htm", "/end.htm", "/click.htm", "/new.htm", "/join.htm")
PLAYER_PAGE = "/player.htm"         # a player's profile
PROFILE_PAGE = "/profile.htm"       # your own preferred machine
ACTIVITY_PAGE = "/activity.htm"     # the latest turns at public tables
ACTIVITY_TURNS = 20                 # how many it lists (FEED)
PLAYER_TURNS = 8                    # a profile's latest turns (FEED n name)


def game_name_of(key, gam):
    """The game's name for a type ('Grid Words' for gridwords/1): the core's GAM name, else its module's."""
    for g in gam or ():
        if g["key"] == key:
            return g["name"]
    m = games.get(key)
    return m.NAME if m else key.split("/")[0]


def player_link(link, name):
    """A player's name, linking to their profile (by the short form of link(), link.plain, when it has one)."""
    return a(getattr(link, "plain", link)(PLAYER_PAGE, who=name), esc(name))


def turn_html(t, link, gam):
    """A turn as a sentence, the player's name linking to their profile, with its time and table."""
    text = turn_sentence(t, game_name_of(t["key"], gam))
    rest = text[len(t["name"]):]
    return "%s%s <font size=\"1\">(%s, %s)</font>" % (
        player_link(link, t["name"]), esc(rest), esc(turn_time(t["at"])),
        a(link("/table.htm", t=t["table"], w=1), "table %s" % esc(t["table"])))


def with_clients(log, turns):
    """The table's log, [[event number, line]], with "(with X on Y)" after the line of each turn whose program
    or machine is known (TRN, which gives them only to those who may see them: everyone at a public table,
    the seated players at a private one). A turn's line is the last line that names its player among those of
    the events after the turn before it, up to its own number (its last event), else the last of them."""
    prev = 0
    for t in sorted(turns, key=lambda t: t["seq"]):
        via = " ".join(w for w in (with_program(t["prog"]), on_machine(t["mach"])) if w)
        mine = [l for l in log if prev < l[0] <= t["seq"]]
        prev = t["seq"]
        line = next((l for l in reversed(mine) if l[1].startswith(t["name"] + " ")), mine[-1] if mine else None)
        if via and line:
            line[1] += " (%s)" % via
    return log


def activity_page(link, turns, gam):
    """/activity.htm: the latest turns at public tables, each a sentence."""
    items = "".join("<li>%s\n" % turn_html(t, link, gam) for t in turns)
    body = (h1("Activity") +
            p("The latest turns at public tables, newest first: who played, what, and the program and machine "
              "they played with, as their program says. Turns at private tables are never shown here.", 2) +
            ("<ul>\n%s</ul>\n" % items if items else p("No turns yet.")) +
            p(a(link(ACTIVITY_PAGE), "Refresh") + " | " + a(link(PLAYER_PAGE), "Your profile")))
    return respond(200, page("ParlorNet activity", body, nav=signed_in_nav(link), compact=True))


STAY_NOTES = {"forgot": "That computer will sign in with the password next time.",
              "kept": "That computer was not on the list. It may have been forgotten already."}


def keys_html(link, keys):
    """Your own profile's Stay signed in list (KEY LST): each computer with when it was made and last used,
    and a link to forget it (KEY DEL n), then one to forget them all."""
    rows = [[esc(k["label"]) + (" <b>(this browser)</b>" if k["here"] else ""), esc(k["made"]), esc(k["used"]),
             a(link("/forget.htm", n=k["n"]), "Forget")] for k in keys]
    return (h2(None, "Stay signed in") +
            p("These computers stay signed in as you. Each one forgets you when you sign out there, and all of "
              "them do when you change your password. A computer you leave unused for a while (90 days unless "
              "this server says otherwise) is forgotten too. Forget any you do not know or no longer use.") +
            (data_table(["Computer", "Made", "Last used", ""], rows, None, (None, None, None, None)) +
             p(a(link("/forget.htm", n="all"), "Forget them all"), 2)
             if rows else p("No computer stays signed in as you. Check Stay signed in when you sign in.", 2)))


def player_page(me, link, prof, turns, gam, keys=None, note=""):
    """/player.htm?who=name: a player's profile, from PFL (what they entered, and what was counted at public
    tables) and their latest public turns (FEED n name). keys: on your own profile, the computers where you
    stay signed in (KEY LST); note: forgot or kept, after forgetting one."""
    name = prof["name"]
    mine = name.lower() == me.lower()
    pref = (p("Preferred machine: <b>%s</b>" % esc(prof["pref"])) if prof["pref"] else
            p("No preferred machine given yet."))
    if mine:
        pref += p(a(link(PROFILE_PAGE), "Change your preferred machine"), 2)
    sc = prof.get("score")
    if sc:
        pref += p("Score: <b>%s</b>%s. %s" % (
            esc(score_text(sc)), ", number %d on the leaderboard" % sc["rank"] if sc["rank"] else "",
            a(LEADERBOARD_PAGE, "The leaderboard")))
    rows = [[esc(game_name_of(k, gam)), str(n)] for k, n in prof["games"]]
    played = (data_table(["Game", "Games played"], rows, None, (None, "right")) if rows else
              p("No games played to the end at public tables yet.", 2))
    used = "<br>\n".join(data_table([title, "Turns"], [[esc(v), str(n)] for v, n in items], None, (None, "right"))
                         for title, items in (("Program", prof["progs"]), ("Machine", prof["machs"])) if items)
    body = (h1(esc(name)) + (p("<b>%s</b>" % esc(STAY_NOTES[note])) if mine and note in STAY_NOTES else "") +
            pref + h2(None, "Games") + played +
            h2(None, "Played with") +
            (used or p("No program or machine named yet: a program that says what it is (with CLI) shows up "
                       "here.", 2)) +
            h2(None, "Latest turns") +
            ("<ul>\n%s</ul>\n" % "".join("<li>%s\n" % turn_html(t, link, gam) for t in turns) if turns else
             p("No turns at public tables yet.", 2)) +
            p("The games and what they were played with are counted from public tables only." +
              (" The score counts every game." if sc else ""), 1) +
            (keys_html(link, keys) if mine and keys is not None else ""))
    return respond(200, page("ParlorNet: %s" % name, body, nav=signed_in_nav(link), compact=True))


def profile_page(link, pref, note=""):
    """/profile.htm: your own preferred machine (PFL SET PREF), shown on your profile."""
    body = (h1("Your preferred machine") +
            (p("<b>%s</b>" % esc(note)) if note else "") +
            p("Which computer do you like to play on? It shows on your profile for other players to see. Up to "
              "40 letters, digits and punctuation; leave it empty to clear it.") +
            '<form action="%s" method="get">\n%s<input type="hidden" name="save" value="1">\n'
            '%s <input name="m" size="40" maxlength="40" value="%s">\n<input type="submit" value="Save"></form>\n'
            % (PROFILE_PAGE, hidden_fields(link(PROFILE_PAGE)), font("Machine:"), esc(pref)) +
            p(a(link(PLAYER_PAGE), "Your profile") + " | " + a(link("/lobby.htm"), "Back to the lobby")))
    return respond(200, page("Your preferred machine", body, nav=signed_in_nav(link), compact=True))


LEADERBOARD_PAGE = "/leaderboard.htm"    # the scores (TOP), for everyone, signed in or not
LEADERBOARD_ROWS = 50                   # how many players it lists (TOP n; the core's most)
LEADERBOARD_REFRESH = 60                # seconds: the page reloads itself, for a screen at a festival


def leaderboard_page(rows, total, gam, gtype=None, nav=None):
    """/leaderboard.htm[?g=type/ver]: the players with the most points (TOP), by every game or by one, each
    name linking to the player's profile, then the note that scores are easy to get (SCORES_NOTE). gam: GAM's
    games, for the links to each game's own board."""
    name = game_name_of(gtype, gam) if gtype else None
    keys = sorted({g["key"] for g in gam or ()}, key=lambda k: game_name_of(k, gam))
    pick = " | ".join(["<b>All games</b>" if not gtype else a(LEADERBOARD_PAGE, "All games")] +
                      ["<b>%s</b>" % esc(game_name_of(k, gam)) if k == gtype else
                       a(url(LEADERBOARD_PAGE, g=k), esc(game_name_of(k, gam))) for k in keys])
    table = data_table(["Rank", "Player", "Points", "Wins", "Draws", "Losses", "Resigned"],
                       [[str(r["rank"]), a(url(PLAYER_PAGE, who=r["name"]), esc(r["name"]))] +
                        [str(r[k]) for k in ("points", "wins", "draws", "losses", "resigns")] for r in rows],
                       None, ("right", None, "right", "right", "right", "right", "right"))
    shown = ("%d of %d players." % (len(rows), total) if total > len(rows) else "")
    body = (h1("Leaderboard" + (": %s" % esc(name) if name else "")) +
            p("Every finished game scores points, games against the bot too. By default a win is 4, a draw 2, "
              "a loss 1, and resigning or running out of time 0." if not gtype else
              "The points earned in %s since the scores were last reset." % esc(name), 2) +
            p(pick, 2) +
            (table + (p(shown, 2) if shown else "") if rows else p("No scores yet: finish a game to get one.")) +
            callout(p(esc(SCORES_NOTE), 2)) +
            p(a(url(LEADERBOARD_PAGE, g=gtype) if gtype else LEADERBOARD_PAGE, "Refresh") + " | " +
              a("/lobby.htm", "Lobby")))
    here = url(LEADERBOARD_PAGE, g=gtype) if gtype else LEADERBOARD_PAGE
    return respond(200, page("ParlorNet leaderboard", body, refresh=(LEADERBOARD_REFRESH, here), nav=nav))


JS_POLL = """<script language="JavaScript"><!--
var pnSeq = %d, pnSeqUrl = "%s", pnJsUrl = "%s", pnJs = %d;
function pnXhr() { return window.XMLHttpRequest ? new XMLHttpRequest() : null; }
if (!pnJs && pnXhr()) {
  location.replace(pnJsUrl);
} else if (pnJs && pnXhr()) {
  setInterval(function () {
    var x = pnXhr();
    x.onreadystatechange = function () {
      if (x.readyState == 4 && x.status == 200 && parseInt(x.responseText, 10) != pnSeq) location.replace(pnJsUrl);
    };
    x.open("GET", pnSeqUrl + "&_=" + new Date().getTime(), true);
    x.send(null);
  }, 2000);
}
//--></script>"""


# Each game's web page, from its module in adapters/games/: game type -> (title, view parser, parts), where
# parts(v, tid, link, seat, names, sel) gives (board, side panel, hint, actions). Worked out from the
# registry when used, so a new game module needs no edit here.
GAME_PAGES = games.Lookup(lambda m: (m.NAME, m.parse_view, m.web_parts))


def describe(text, names, key=None):
    """An event -> a line for the log: the game's own events first (its module's describe_event)."""
    g = games.get(key)
    return (g and g.describe_event(text, names)) or describe_invite_event(text) or describe_event(text, names)


def event_log(events, seqs, names, key):
    """A table's events -> its log, [[event number, line]]. A CLIENT event, what a seat's player plays with,
    finishes the JOIN line just before it ("ann sat down with GameMaster 1.0.0 on a Macintosh Plus"); later it is
    a line of its own ("ann now plays with ..."); with no text (a private table's, which this player may not
    see) it says nothing."""
    log, join = [], None        # join: (the JOIN line just logged, its seat)
    for n, e in zip(seqs, events):
        if e.split(" ", 1)[0] == "CLIENT":
            c = parse_client(e)
            if c and join and join[1] == c[0]:
                join[0][1] += " " + c[1]
            elif c:
                who = names[c[0]] if c[0] < len(names) and names[c[0]] != "-" else "seat %d" % (c[0] + 1)
                log.append([n, "%s now plays %s" % (who, c[1])])
            join = None
            continue
        d = describe(e, names, key)
        f = e.split()
        join = None
        if d:
            log.append([n, d])
            if f[0] == "JOIN" and len(f) >= 3 and f[1].isdigit():
                join = (log[-1], int(f[1]))
    return log


def invite_html(tid, link, private, invited, bot=None):
    """For a seated player at an open table: who is invited, and a form to invite someone; bot: a bot name to
    offer with a link that invites it (docs/bot.md), or None."""
    hidden = hidden_fields(link("/invite.htm", t=tid))
    who = ", ".join("%s (%s)" % (esc(n), a(link("/uninvite.htm", t=tid, who=n), "withdraw")) for n in invited)
    bot_link = ("<br>\n%s: it sits down in a few seconds." % a(link("/invite.htm", t=tid, who=bot), "Invite the bot")
                if bot else "")
    return p("%s%s%s" % ("This table is private: only invited players can sit down.<br>\n" if private else "",
                         "Invited: " + who if who else "Nobody invited yet.", bot_link), 2) + """<form action="/invite.htm" method="get">
%s
%s
</form>""" % (hidden, font('Invite a player: <input name="who" size="12" maxlength="12"> '
                           '<input type="submit" value="Invite">', 2))


def notes_html(tid, link, notes, seat):
    """The table's notes (the latest last) and a form to add one for a seated player."""
    out = ""
    if notes:
        out += p("<b>Notes</b><br>\n" + "<br>\n".join(esc(n) for n in notes), 2)
    if seat >= 0:
        out += """<form action="/note.htm" method="get">
%s
%s
</form>""" % (hidden_fields(link("/note.htm", t=tid)),
              font('Add a note: <input name="text" size="30" maxlength="48"> <input type="submit" value="Note">', 2))
    return out


def table_page(me, tid, names, v, seat, log, watching, rate, js, link, key="reversi/1", sel="", invite="",
               still=False, notes="", uses=None):
    """The board. Without JavaScript the page refreshes itself at the player's
    chosen rate (meta refresh). Browsers with JavaScript and XMLHttpRequest
    switch to js=1: no meta refresh; a script asks /seq.htm every 2 s and
    reloads only when something changed. still: no meta refresh (a /check.htm
    answer stays on the page until the player moves on). names: the seated
    players ("-" for an empty seat), each linked to their profile after the
    Refresh link, with what they play with where the server says it (uses:
    {seat: text}, from VUE C)."""
    title, _, parts = GAME_PAGES[key]
    my_turn = seat >= 0 and v["turn"] == seat
    over = "Game over" in v["status"]
    started = bool(v["rows"])
    w = 1 if watching else None
    me_url = link("/table.htm", t=tid, w=w)
    names = names + ["-"] * (2 - len(names))
    if started:
        board, side, hint, actions = parts(v, tid, link, seat, names, sel)
    else:
        board, side, hint, actions = p("Waiting for an opponent."), "", "", ""
    status = (v["status"] or "") + hint
    clock = clock_text(v, names)
    if over:
        exit_link = a(link("/lobby.htm"), "Lobby")
    elif seat >= 0 and not started:
        exit_link = a(link("/leave.htm", t=tid), "Stand up")
    elif seat >= 0:
        exit_link = a(link("/resign.htm", t=tid), "Resign")
    else:
        exit_link = a(link("/leave.htm", t=tid), "Stop watching")
    if js:
        rate_line = "Updates automatically."
    else:
        opts = []
        for r in RATES:
            label = "off" if r == 0 else "%ds" % r
            opts.append("<b>%s</b>" % label if r == rate else a(link("/table.htm", t=tid, w=w, r=r), label))
        rate_line = "Refresh every: " + " ".join(opts)
    body = """%s<table border="0" cellpadding="8"><tr valign="top"><td>
%s
</td><td>
%s
%s%s
%s%s%s%s
</td></tr></table>
%s
""" % (h1("%s - table %s" % (esc(title), esc(tid))), board, side,
       p("<b>%s</b><br>%s" % (esc(status), esc(clock))), actions,
       p("<br>\n".join(esc(l) for l in log[-6:]) or "&nbsp;", 2),
       p("%s | %s%s" % (a(me_url, "Refresh"), exit_link, "".join(
           " | " + player_link(link, n) + (" " + small(esc((uses or {})[i])) if (uses or {}).get(i) else "")
           for i, n in enumerate(names) if n and n != "-" and names.index(n) == i))),
       p(rate_line, 2), invite + notes,
       JS_POLL % (v["seq"], link("/seq.htm", t=tid, w=w), link("/table.htm", t=tid, w=w, js=1, a=1), 1 if js else 0)
       if not over else "")
    # the page's own reloads (meta refresh, the script) carry a=1: they are not the player's activity
    refresh = None if (my_turn or over or js or rate == 0 or still) else (rate, link("/table.htm", t=tid, w=w, a=1))
    return respond(200, page("%s - table %s" % (title, tid), body, refresh=refresh, nav=signed_in_nav(link),
                             compact=True))


def field_values(q, names):
    """The values of a game's MOVE_FIELDS in a query, in order, spaces trimmed -> list, or None if any is
    given more than once, is longer than 32 characters, or holds anything but printable ASCII. A field left
    out is an empty string."""
    out = []
    for name in names:
        vals = q.get(name) or [""]
        if len(vals) != 1 or not FIELD_RE.match(vals[0]):
            return None
        out.append(vals[0].strip())
    return out


def move_from_fields(module, q, v=None):
    """A move built from the game's MOVE_FIELDS by its move_from_fields (given the table's parsed view v too
    when the game sets MOVE_FIELDS_VIEW), checked against its MOVE_RE -> the move, or None. A game's Refused
    (the reason, in the player's words) is passed on."""
    vals = field_values(q, module.MOVE_FIELDS)
    if vals is None:
        return None
    try:
        mv = module.move_from_fields(vals, v) if getattr(module, "MOVE_FIELDS_VIEW", False) else \
            module.move_from_fields(vals)
    except Refused:
        raise
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    return mv if isinstance(mv, str) and module.MOVE_RE.match(mv) else None


def notice(title, heading, text="", links=(), status=200):
    """A short page in the frame: a heading that says what happened, a line on it, and where to go."""
    body = h1(esc(heading)) + (p(text) if text else "") + (p(" | ".join(a(h, esc(t)) for h, t in links))
                                                          if links else "")
    return respond(status, page(title, body))


def error_page(msg, back=None):
    """Something asked for could not be done: the reason as the heading, and the way back."""
    return notice("ParlorNet: not done", msg, links=[(back or "/welcome.htm", "Back")])


BUSY = ("Busy", "Busy, try again in a minute.", "Too many sign-ins are waiting for an answer.")


# ---- request handling ---------------------------------------------------------

class Dialogue:
    """A sign-in, sign-up or password change waiting for its next answer;
    born: when its first page was made (kept across its steps); kind: signin,
    signup or change (the heading of its pages, and where a refusal goes)."""
    __slots__ = ("core", "name", "until", "done", "addr", "born", "kind", "stay")

    def __init__(self, core, name, until, done, addr, born=None, kind="signin", stay=False):
        self.core, self.name, self.until, self.done, self.addr = core, name, until, done, addr
        self.born = born or time.time()
        self.kind = kind
        self.stay = stay            # Stay signed in was checked: a sign-in key when the dialog ends


class Web:
    def __init__(self, core_host, core_port, key_file=None):
        self.core_host, self.core_port = core_host, core_port
        self.key_file = key_file or CoreClient.key_file
        _, _, key = read_key(self.key_file)
        self.csrf_key = hmac.new(key, b"parlornet-web-csrf", hashlib.sha256).digest()
        self.active = 0
        self.active_by = {}         # client address -> requests in progress
        self.signins = {}           # sign-in dialogs in progress: id -> Dialogue
        self.handoffs = {}          # one-time handle -> (sid, expires): the redirect after sign-in
        self.ses_fails = {}         # client address -> [times of failed resumes]
        self.good = {}              # SHA-256 of a session id that resumed here -> when it last did
        self.auth_seen = (("password", "Password", "password"), 0.0)
        self.motd_seen = []
        self.games_seen = None      # the core's GAM list, refreshed with the auth info; None until known
        self.protocols_seen = None  # the core's PRL list: [(key, port, name, description)]; None until known
        self.protocols_when = 0.0
        self.scores_seen = False    # the core shows its leaderboard (TOP answers OK; scores.show on)

    async def connect(self, req=None):
        """A connection to the core; with a request, the core is told the
        client's address (SRC) for its per-address limits."""
        core = await CoreClient.connect(self.core_host, self.core_port, self.key_file)
        if req is not None:
            await core.source(req.get("peer"))
        return core

    def csrf(self, sid):
        return hmac.new(self.csrf_key, sid.encode("ascii"), hashlib.sha256).hexdigest()[:16]

    async def auth_info(self):
        """The server's sign-in method, its label and the sign-up method (from its greeting), cached
        briefly."""
        await self.refresh_info()
        return self.auth_seen[0]

    async def motd(self):
        await self.refresh_info()
        return self.motd_seen

    @staticmethod
    def with_header(r, extra):
        if extra:
            head, body = r.split(b"\r\n\r\n", 1)
            r = head + b"\r\n" + extra.encode("ascii") + b"\r\n" + body
        return r

    async def welcome(self, req, message="", extra=""):
        link = self.visitor(req)
        r = welcome(req, message, await self.auth_info(), await self.motd(), await self.protocols(),
                    site_nav(link, "/welcome.htm") if link else None, link("/lobby.htm") if link else None,
                    self.games_seen, self.scores_seen)
        return self.with_header(r, extra)

    def visitor(self, req):
        """A public page's view of its visitor: the signed-in link maker (linker) when the request carries a
        session (the cookie, or s= for a browser without cookies) that resumed here within GOOD_SECS and was
        not signed out, so the page's bar has the lobby's link and Sign Out; else None. The core is not asked;
        a session that ended elsewhere is found out on the next signed-in page."""
        sid, cookieless = cookie_sid(req), False
        if not sid:
            s_ = (req["query"].get("s") or [""])[0]
            sid, cookieless = (s_, True) if SID_RE.match(s_) else (None, False)
        seen = self.good.get(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()) if sid else None
        if seen is None or time.time() - seen >= GOOD_SECS:
            return None
        return self.linker(sid, cookieless, DEFAULT_RATE, False)

    def visitor_nav(self, req, current):
        link = self.visitor(req)
        return site_nav(link, current) if link else None

    async def protocols(self):
        """The protocols this server speaks now (the core's PRL), asked again after PROTOCOLS_SECS; None when
        the core does not list them."""
        await self.refresh_info()
        if time.time() - self.protocols_when > PROTOCOLS_SECS:
            try:
                core = await self.connect()
                try:
                    await self.read_protocols(core)
                finally:
                    core.close()
            except (OSError, asyncio.TimeoutError, CoreError):
                pass
        return self.protocols_seen

    async def leaderboard(self, req, gtype):
        """/leaderboard.htm: TOP on a connection of its own (TOP needs no sign-in), by every game or one
        (gtype, from g=). Not found when the server shows no scores (scores.show off)."""
        await self.refresh_info()
        gtype = gtype if GAME_KEY_RE.match(gtype or "") else ""
        core = await self.connect()
        try:
            data, final = await core.req("TOP %d%s" % (LEADERBOARD_ROWS, " game=" + gtype if gtype else ""))
        finally:
            core.close()
        if final.startswith("ERR NOGAME"):
            return notice("Not found", "This server has no game %s." % gtype,
                          links=[(LEADERBOARD_PAGE, "The leaderboard")], status=404)
        if not final.startswith("OK"):
            self.scores_seen = False
            return notice("Not found", "This server shows no leaderboard.", links=[("/welcome.htm", "Welcome")],
                          status=404)
        self.scores_seen = True
        f = final.split()
        return leaderboard_page(parse_top(data), int(f[1]) if len(f) > 1 and f[1].isdigit() else 0,
                                self.games_seen, gtype or None, self.visitor_nav(req, None))

    async def read_protocols(self, core):
        """PRL on a connection: the protocols and the docs site's address (site.docs_url), kept here and in
        the frame (webstyle.DOCS)."""
        data, final = await core.req("PRL")
        if final.startswith("OK"):
            S.DOCS[0], self.protocols_seen = parse_protocols(data)
        self.protocols_when = time.time()

    async def signin_page(self, req, message="", name="", raw=False, extra=""):
        return self.with_header(signin_page(await self.auth_info(), message, name, raw), extra)

    async def signup_page(self, req, message="", name="", raw=False):
        return signup_page(await self.auth_info(), message, name, raw)

    async def flow_page(self, req, kind, message="", name=""):
        """Where a sign-in or a sign-up that was refused or ran out goes: its own page again."""
        if kind == "signup":
            return await self.signup_page(req, message, name)
        return await self.signin_page(req, message, name)

    async def refresh_info(self):
        info, when = self.auth_seen
        if time.time() - when > 30:
            try:
                core = await self.connect()
                info = (core.auth, core.label, core.signup)
                S.LABEL[0] = (core.greeting.split() + ["", "parlornet"])[1]     # the server's name, for the footer
                self.motd_seen = await core.motd()
                S.MOTD[0] = any(l.strip() for l in self.motd_seen)      # the footer links to it when there is one
                gdata, final = await core.req("GAM")
                if final.startswith("OK"):
                    self.games_seen = parse_games(gdata)
                await self.read_protocols(core)
                _, final = await core.req("TOP 1")      # the leaderboard is shown (scores.show): link to it
                self.scores_seen = final.startswith("OK")
                core.close()
            except (OSError, asyncio.TimeoutError, CoreError):
                pass
            self.auth_seen = (info, time.time())

    def sweep_signins(self):
        now = time.time()
        for lid, d in list(self.signins.items()):
            if d.until < now:
                d.core.close()
                del self.signins[lid]
        for h, (sid, until) in list(self.handoffs.items()):
            if until < now:
                del self.handoffs[h]
        if len(self.good) > 20000:
            self.good = {k: t for k, t in self.good.items() if now - t < GOOD_SECS}

    def make_room(self, addr):
        """Room for a new sign-in dialog from addr (an address key): True,
        or False when the newcomer must wait. At most SIGNINS_PER_ADDR from
        one address (its own least recently answered is ended) and MAX_SIGNINS
        in all. When all are taken, a dialog is ended only at an address
        holding more than the newcomer's would after it (the newest of those,
        on a tie); if none does, the newcomer, the newest arrival, is the one
        refused. So many addresses each holding one cannot push out a player
        waiting for a mailed password."""
        self.sweep_signins()
        mine = sorted((d.until, lid) for lid, d in self.signins.items() if d.addr == addr)
        while len(mine) >= SIGNINS_PER_ADDR:
            self.signins.pop(mine.pop(0)[1]).core.close()
        if len(self.signins) >= MAX_SIGNINS:
            count = {}
            for d in self.signins.values():
                count[d.addr] = count.get(d.addr, 0) + 1
            most = max(count.values())
            if most <= len(mine) + 1:
                return False
            lid = max((k for k, d in self.signins.items() if count[d.addr] == most),
                      key=lambda k: self.signins[k].born)
            self.signins.pop(lid).core.close()
        return True

    def resume_allowed(self, addr, sid=None):
        """Failed resumes are counted per client address; past the limit no more
        are tried for a while (nothing is locked: the player can sign in),
        except for a session id that has resumed here before: so junk from one
        address behind a shared NAT does not sign out the others there."""
        n, secs = SES_FAIL_LIMIT
        now = time.time()
        q = [t for t in self.ses_fails.get(addr, []) if now - t < secs]
        if q:
            self.ses_fails[addr] = q
        else:
            self.ses_fails.pop(addr, None)
        if len(q) < n:
            return True
        seen = self.good.get(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()) if sid else None
        return seen is not None and now - seen < GOOD_SECS

    def session_of(self, req, q):
        """(sid, cookieless) from the cookie, the one-time handle after sign-in, or s=."""
        sid = cookie_sid(req)
        h = (q.get("h") or [""])[0]
        if h:
            entry = self.handoffs.pop(h, None)
            if sid:                         # the browser returned the cookie: keep links clean
                return sid, False
            if entry and entry[1] >= time.time():
                return entry[0], True       # no cookie came back: carry the session in links
            return None, False
        if sid:
            return sid, False
        s_ = (q.get("s") or [""])[0]
        return (s_, True) if SID_RE.match(s_) else (None, False)

    async def route(self, req):
        path, q = req["path"], req["query"]
        if path in ("/", "/index.htm", "/index.html"):     # the welcome page itself, so the site's address
            return await self.welcome(req)                   # is a page (search engines index no redirect)
        if path == "/play" or path.startswith("/play/"):       # ParlorNet Play: its own extension now
            return play_moved(req, None if PLAY_URL[0] else await self.protocols())
        if path == "/welcome.htm":
            return await self.welcome(req)
        if path == "/swatch.gif":                       # the preview of a player's own colors
            gif = games.get("gridwords/1") and games.get("gridwords/1").swatch((q.get("c") or [""])[0])
            if gif:
                return respond(200, gif, "image/gif", extra="Cache-Control: max-age=86400\r\n")
            return notice("Not found", "Not found", status=404)
        if path in IMAGES or path in SITE_IMAGES:      # the same bytes as long as this adapter runs
            if req["headers"].get("if-modified-since") == STARTED:
                return respond(304, b"", "image/gif", extra="Cache-Control: max-age=86400\r\n")
            return respond(200, SITE_IMAGES.get(path) or IMAGES[path], "image/gif",
                           extra="Cache-Control: max-age=86400\r\nLast-Modified: %s\r\n" % STARTED)
        if path.startswith("/games/"):
            await self.refresh_info()
            got = (game_page(path[len("/games/"):-len(".htm")], self.games_seen, self.visitor_nav(req, None))
                   if path.endswith(".htm") else None)
            if got:
                return got
        if path == LEADERBOARD_PAGE:
            return await self.leaderboard(req, (q.get("g") or [""])[0])
        if path == PIC.CREDITS_PAGE:
            return credits_page(self.visitor_nav(req, None))
        if moved_to(path) is not None:          # the documentation's old addresses: the docs site
            await self.refresh_info()
            return moved_page(req, path, q)
        if path == SIGNIN_PAGE:
            if req["method"] == "POST":
                return await self.login(req, req["form"])
            if cookie_sid(req) or cookie_key(req):      # signed in already, most likely: the lobby (it asks
                return redirect(req, "/lobby.htm")      # again if not, or signs in with the key)
            return await self.signin_page(req)      # GET never signs in: credentials go by POST only
        if path == SIGNUP_PAGE:
            if req["method"] == "POST":
                return await self.signup(req, req["form"])
            return await self.signup_page(req)
        self.sweep_signins()
        sid, cookieless = self.session_of(req, q)
        if sid is None:
            if cookie_key(req):             # Stay signed in: sign in with the key, no question asked
                return await self.key_signin(req, "Sign in to play.")
            return await self.signin_page(req, "Sign in to play." if path != "/lobby.htm" or q else "")
        addr = req["src"]
        if not self.resume_allowed(addr, sid):
            return await self.signin_page(req, "Sign in to play.")     # the cookie is kept: it may be good
        core = await self.connect(req)
        try:
            try:
                me = await core.resume(sid)
            except CoreError:
                self.ses_fails.setdefault(addr, []).append(time.time())
                self.good.pop(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest(), None)
                if not cookieless and cookie_key(req):
                    return await self.key_signin(req, "Your session has ended; sign in again.", CLEAR_COOKIE)
                return await self.signin_page(req, "Your session has ended; sign in again.",
                                              extra="" if cookieless else CLEAR_COOKIE)
            self.good[hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()] = time.time()
            if req["method"] == "POST":
                q = dict(q)
                q.update(req["form"])
            if path in ACTIONS and not hmac.compare_digest((q.get("x") or [""])[0], self.csrf(sid)):
                return error_page("That link is out of date.", url("/lobby.htm", s=sid if cookieless else None))
            if path not in AUTO_PATHS and not q.get("a"):     # the player's own request: activity (who is online)
                await core.req("ACT")
            if path in CLAIM_PATHS:         # a move or a seat: told with the browser and its machine (CLI)
                await core.claim(*browser_claim(req["headers"].get("user-agent", "")))
            return await self.authed(req, path, q, core, me, sid, cookieless)
        finally:
            if not getattr(core, "keep", False):      # a change flow keeps its connection for the next page
                core.close()

    async def login(self, req, q):
        """Sign in, one step per request (POST forms: the answers are
        credentials and never go in a URL): the server's questions (ASK)
        become form pages; the connection waits between steps under a
        short-lived id. The first step signs in only (USER): a name with no
        account is told so, with a link to New account. The later steps
        (lid=) carry on any flow: a sign-in, a new account or a password
        change."""
        self.sweep_signins()
        lid = (q.get("lid") or [""])[0]
        if lid:
            entry = self.signins.pop(lid, None)
            if entry is None:
                return await self.signin_page(req, "That sign-in has expired; start again.")
            core, name, kind = entry.core, entry.name, entry.kind
            field = (q.get("f") or [""])[0]
            value = (q.get("v") or [""])[0].strip()
            done = entry.done      # a password change: where to go after
            if not re.match(r"^[a-z]{1,12}\Z", field) or not value or len(value) > 64:
                core.close()
                return redirect(req, done) if done else await self.flow_page(
                    req, kind, "New account canceled." if kind == "signup" else "Sign-in canceled.", name)
            try:
                sid = await core.aut(field, value)
            except NeedAuth as ask:
                return self.ask(req, core, name, ask, done, entry.born, kind, entry.stay)
            except CoreError as e:
                core.close()
                if done:
                    return error_page(err_reason(str(e)), done)
                return await self.flow_page(req, kind, err_reason(str(e)), name)
            extra = await self.stay_signed_in(req, core, name) if entry.stay and not done else ""
            core.close()
            if done:
                return redirect(req, done + ("&" if "?" in done else "?") + "note=password")
            return self.signed_in(req, sid, extra)
        name = (q.get("n") or [""])[0].strip()
        cred = (q.get("k") or [""])[0].strip() or None      # answers the credential question only, if asked
        stay = (q.get("stay") or [""])[0] == "1"
        if not NAME_RE.match(name):
            return await self.signin_page(req, NAMES_RULE)
        if not self.make_room(req["src"]):
            return notice(BUSY[0], BUSY[1], BUSY[2], status=503)
        core = await self.connect(req)
        try:
            try:
                sid = await core.user(name)                  # the name alone; sign-in only
            except NeedAuth as ask:
                if not (ask.cred and cred):
                    raise
                sid = await core.aut(ask.field, cred)
        except NeedAuth as ask:
            return self.ask(req, core, name, ask, stay=stay)
        except CoreError as e:
            core.close()
            if e.code == "NOPLAYER":
                return await self.signin_page(req, unknown_name_html(name), name, raw=True)
            return await self.signin_page(req, err_reason(str(e)), name)
        extra = await self.stay_signed_in(req, core, name) if stay else ""
        core.close()
        return self.signed_in(req, sid, extra)

    async def stay_signed_in(self, req, core, name):
        """Stay signed in was checked and the sign-in succeeded: a sign-in key for this browser, named after
        it (its program and machine), in the key cookie. "" when the core gives none."""
        prog, mach = browser_claim(req["headers"].get("user-agent", ""))
        label = " on ".join(x for x in (prog, mach) if x)
        label = (label if len(label) <= KEY_LABEL_LEN else prog or mach) or "a web browser"
        try:
            key = await core.key_new(label)
        except CoreError:
            key = None
        return set_key_cookie(req, core.name or name, key) if key else ""

    async def key_signin(self, req, message, extra=""):
        """A visit with no session and the Stay signed in cookie: sign in with its key (no question is asked)
        and go on to the lobby. A key the core refuses is forgotten here too (the cookie is cleared), and the
        sign-in page asks for the password. For anything else, such as a busy server, the cookie is kept."""
        name, key = cookie_key(req)
        core = await self.connect(req)
        try:
            sid = await core.key_signin(name, key)
        except CoreError as e:
            gone = e.code in ("NOKEY", "NOPLAYER", "SUSPENDED", "STATE")
            return await self.signin_page(req, message if gone and e.code != "NOKEY" else err_reason(str(e)), name,
                                          extra=extra + (CLEAR_KEY if gone else ""))
        finally:
            core.close()
        return self.signed_in(req, sid)

    async def signup(self, req, q):
        """A new account (POST /signup.htm): the name, then the server's sign-up steps (REG), a page each,
        carried on through /login.htm like a sign-in's. A taken name is told so; nothing signs in to it."""
        self.sweep_signins()
        name = (q.get("n") or [""])[0].strip()
        if not NAME_RE.match(name):
            return await self.signup_page(req, NEW_NAME_RULE)
        if not self.make_room(req["src"]):
            return notice(BUSY[0], BUSY[1], BUSY[2], status=503)
        core = await self.connect(req)
        try:
            sid = await core.reg(name)
        except NeedAuth as ask:
            return self.ask(req, core, name, ask, kind="signup")
        except CoreError as e:
            core.close()
            if e.code == "NAMEINUSE":
                return await self.signup_page(req, taken_name_html(name), raw=True)
            return await self.signup_page(req, err_reason(str(e)), "" if e.code == "RESERVED" else name)
        core.close()
        return self.signed_in(req, sid)

    def ask(self, req, core, name, ask, done=None, born=None, kind=None, stay=False):
        """Keep the dialog (and its core connection) for the next answer."""
        if not self.make_room(req["src"]):
            core.close()
            return notice(BUSY[0], BUSY[1], BUSY[2], status=503)
        kind = kind or ("change" if done else "signin")
        lid = secrets.token_urlsafe(16)
        self.signins[lid] = Dialogue(core, name, time.time() + SIGNIN_IDLE_SECS, done, req["src"], born, kind,
                                     stay)
        return ask_page(lid, ask, kind=kind)

    def signed_in(self, req, sid, extra=""):
        """The session goes in a cookie; the redirect carries a one-time handle
        (not the session) so that a browser that drops cookies can still be
        given its session in links. extra: more headers (the Stay signed in cookie)."""
        h = secrets.token_urlsafe(12)
        self.handoffs[h] = (sid, time.time() + HANDOFF_SECS)
        self.good[hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()] = time.time()   # it resumes even past the address's limit
        return redirect(req, url("/lobby.htm", h=h), set_cookie(sid) + extra)

    def linker(self, sid, cookieless, rate, js):
        """The link maker for a signed-in page: a link keeps the session (only for browsers without
        cookies), the action check (x) and the display choices."""
        csrf = self.csrf(sid)

        def link(p, **kw):
            kw.setdefault("r", rate if rate != DEFAULT_RATE else None)
            kw.setdefault("js", 1 if js else None)
            return url(p, s=sid if cookieless else None, x=csrf, **kw)

        def plain(p, **kw):
            """A page that only shows something (a profile): the session for a browser without cookies,
            nothing else, so the many links to it stay short."""
            return url(p, s=sid if cookieless else None, **kw)
        link.plain = plain
        return link

    async def authed(self, req, path, q, core, me, sid, cookieless):
        def arg(k):
            return (q.get(k) or [""])[0]
        try:
            rate = int(arg("r")) if arg("r") else DEFAULT_RATE
        except ValueError:
            rate = DEFAULT_RATE
        rate = rate if rate in RATES else DEFAULT_RATE
        js = arg("js") == "1"

        link = self.linker(sid, cookieless, rate, js)
        lobby = link("/lobby.htm")
        if path == "/out.htm":
            everywhere = arg("all") == "1"
            await core.out(everywhere)
            self.good.pop(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest(), None)    # the bar: signed out
            return await self.welcome(req, "Signed out%s." % (" everywhere" if everywhere else ""),
                                      CLEAR_COOKIE + CLEAR_KEY)     # signing out forgets the key, here and there
        if path == "/password.htm":
            # the server's change flow, as pages; this connection stays open between them
            try:
                await core.pwd()
                return error_page("Nothing to change.", lobby)
            except NeedAuth as ask:
                core.keep = True
                return self.ask(req, core, me, ask, url("/lobby.htm", s=sid if cookieless else None))
            except CoreError as e:
                return error_page(err_reason(str(e)), lobby)
        if path == "/seq.htm":
            tid = arg("t")
            if arg("w") == "1":
                await core.req("SEE %s" % tid)
            _, final = await core.req("SIN %s 999999999" % tid)
            return respond(200, final.split()[1] if final.startswith("OK") else "0", "text/plain")
        if path == "/lobby.htm":
            gdata, _ = await core.req("GAM")
            tdata, tfinal = await core.req("LST clients")
            if not tfinal.startswith("OK"):         # a core without the word
                tdata, _ = await core.req("LST")
            idata, _ = await core.req("IVL")
            wdata, wfinal = await core.req("WHO")
            note = ("Your password is changed; you are signed out everywhere else."
                    if arg("note") == "password" else "")
            return lobby_page(me, link, parse_games(gdata), parse_tables(tdata), note, auth=core.auth,
                              invites=parse_invites(idata), bots=botflag.bot_names(tdata),
                              online=parse_players(wdata) if wfinal.startswith("OK") else None,
                              more=more_tables(tdata), scores=self.scores_seen)
        if path == COLOURS_PAGE:
            tid = arg("t") if arg("t").isdigit() else ""
            note = ""
            if arg("save") == "1":
                pal = PAL.from_fields(arg)
                if pal is None:
                    note = "Colors are written #RRGGBB, like #2f343b; nothing was changed."
                else:
                    try:
                        await core.pref_set(PAL.PREF_KEY, PAL.encode(pal))
                        note = "Saved."
                    except CoreError:
                        note = "The server did not keep that; try again."
            return colours_page(link, await self.gw_colours(core), note, tid)
        if path == BG_HOME_PAGE:
            tid = arg("t") if arg("t").isdigit() else ""
            note = ""
            if arg("h") in BG_HOMES:
                try:
                    await core.pref_set(BG_HOME_PREF, arg("h"))
                    if tid:         # the switch on a table: straight back to it, drawn the new way
                        return redirect(req, link("/table.htm", t=tid))
                    note = "Saved."
                except CoreError:
                    note = "The server did not keep that; try again."
            return bghome_page(link, await self.bg_home(core), note)
        if path == ACTIVITY_PAGE:
            gdata, _ = await core.req("GAM")
            fdata, final = await core.req("FEED %d" % ACTIVITY_TURNS)
            return activity_page(link, parse_turns(fdata) if final.startswith("OK") else [], parse_games(gdata))
        if path == PLAYER_PAGE:
            who = arg("who") or me
            if not NAME_RE.match(who):
                return error_page(NAMES_RULE, lobby)
            pdata, final = await core.req("PFL %s" % who)
            if not final.startswith("OK"):
                return error_page("There is no player named %s here." % who if "NOPLAYER" in final
                                  else err_text(final), lobby)
            prof = parse_profile(pdata)
            gdata, _ = await core.req("GAM")
            fdata, final = await core.req("FEED %d %s" % (PLAYER_TURNS, prof["name"] or who))
            keys = None
            if (prof["name"] or who).lower() == me.lower():     # your own: the computers where you stay signed in
                try:
                    keys = await core.keys()
                except CoreError:
                    keys = None
            return player_page(me, link, prof, parse_turns(fdata) if final.startswith("OK") else [],
                               parse_games(gdata), keys, arg("note"))
        if path == "/forget.htm":
            which = arg("n")
            if which != "all" and not re.match(r"^[0-9]{1,9}\Z", which):
                return error_page("That link is out of date.", lobby)
            try:
                here = [k["n"] for k in await core.keys() if k["here"]]
            except CoreError:
                here = []
            done = await core.key_del(which)
            mine = which == "all" or (which.isdigit() and int(which) in here)
            r = redirect(req, link.plain(PLAYER_PAGE, note="forgot" if done else "kept"))
            return self.with_header(r, CLEAR_KEY) if done and mine else r
        if path == PROFILE_PAGE:
            note = ""
            if arg("save") == "1":
                _, final = await core.req(("PFL SET PREF %s" % claim_text(arg("m"))).rstrip())
                note = "Saved." if final.startswith("OK") else "The server did not keep that; try again."
            pdata, _ = await core.req("PFL")
            return profile_page(link, parse_profile(pdata)["pref"], note)
        if path == "/new.htm":
            key = arg("g")
            gdata, _ = await core.req("GAM")
            offered = [g for g in parse_games(gdata) if g["key"] == key]     # one per ruleset, the default first
            if not offered:
                return error_page("This server doesn't host that game.", lobby)
            g = next((g for g in offered if g["rules"] == arg("ru")), None) if arg("ru") else offered[0]
            if g is None:
                return error_page("This server doesn't have those rules for that game.",
                                  lobby)
            turn = arg("turn") if re.match(r"^\d{1,4}[smhd]?\Z", arg("turn")) else "1d"
            vis = " vis=private" if arg("vis") == "private" else ""
            players = " players=%s" % arg("np") if re.match(r"^[0-9]{1,2}\Z", arg("np")) else ""
            _, final = await core.req("NEW %s %s turn=%s%s%s" % (g["key"], g["rules"], turn, vis, players))
            if not final.startswith("OK"):
                return error_page(err_text(final), lobby)
            tid = final.split()[1]
            _, final = await core.req("JON %s" % tid)
            if arg("bot") == "1":           # Play the bot: a bot invited to every other seat (docs/bot.md)
                seats = int(arg("np")) if players else (g["min"] or 2)
                for name in botflag.NAMES[:max(0, seats - 1)]:
                    await core.req("INV %s %s" % (tid, name))
            return redirect(req, link("/table.htm", t=tid))
        tid = arg("t")
        if not tid.isdigit():
            return error_page("No such table.", lobby)
        if path == "/join.htm":
            _, final = await core.req("JON %s" % tid)
            if not final.startswith("OK"):
                return error_page(err_text(final), lobby)
            return redirect(req, link("/table.htm", t=tid))
        if path == "/invite.htm":
            who = arg("who").strip()
            if not NAME_RE.match(who):
                return error_page("Names are letters, digits, - and _, up to 12.", link("/table.htm", t=tid))
            _, final = await core.req("INV %s %s" % (tid, who))
            if not final.startswith("OK"):
                return error_page(err_text(final).capitalize(), link("/table.htm", t=tid))
            return redirect(req, link("/table.htm", t=tid))
        if path == "/uninvite.htm":
            who = arg("who")
            if NAME_RE.match(who):
                await core.req("UNV %s %s" % (tid, who))
            if who.lower() == me.lower():       # declined: back to the lobby
                return redirect(req, lobby)
            return redirect(req, link("/table.htm", t=tid))
        if path == "/leave.htm":
            await core.req("LVE %s" % tid)
            return redirect(req, lobby)
        if path == "/note.htm":
            text = arg("text").strip()
            if text:
                _, final = await core.req("NOTE %s %s" % (tid, text))
                if not final.startswith("OK"):
                    return error_page(err_text(final).capitalize(), link("/table.htm", t=tid))
            return redirect(req, link("/table.htm", t=tid))
        if path == "/resign.htm":
            if arg("sure") != "1":
                body = (h1("Resign table %s?" % esc(tid)) + p("Resigning ends the game and your opponent wins.") +
                        callout(p("<b>%s</b> | %s" % (a(link("/resign.htm", t=tid, sure=1), "Yes, resign"),
                                                      a(link("/table.htm", t=tid), "No, back to the game")))))
                return respond(200, page("Resign?", body, nav=signed_in_nav(link), compact=True))
            await core.req("RES %s" % tid)
            return redirect(req, link("/table.htm", t=tid))
        if path in ("/und.htm", "/end.htm"):
            _, final = await core.req("%s %s" % ("UND" if path == "/und.htm" else "END", tid))
            if not final.startswith("OK"):
                return error_page(err_text(final), link("/table.htm", t=tid))
            return redirect(req, link("/table.htm", t=tid))
        if path in ("/move.htm", "/check.htm"):
            # The same rules for both: the session (resumed and its x= checked in route, as for every path in
            # ACTIONS), a table number, and a move that is either mv= matching a game's MOVE_RE, or built from
            # the table's game's MOVE_FIELDS. /move.htm plays it (MOV); /check.htm asks the core about it (CHK)
            # and shows the answer on the table page.
            module = None
            if "mv" not in q or path == "/check.htm":
                key = await self.table_key(core, tid)
                module = games.get(key) if key else None
            if "mv" in q:
                mv = arg("mv")
                if len(q["mv"]) != 1 or not any(g.MOVE_RE.match(mv) for g in ([module] if module else
                                                                                 games.all_games())):
                    mv = None
            elif getattr(module, "MOVE_FIELDS", None):
                v = None
                if getattr(module, "MOVE_FIELDS_VIEW", False):      # the fields are read on the table's board
                    vdata, final = await core.req("VUE %s GMT" % tid)
                    if not final.startswith("OK"):
                        return error_page("That table isn't here any more.", link("/lobby.htm"))
                    v = module.parse_view(vdata)
                try:
                    mv = move_from_fields(module, q, v)
                except Refused as e:
                    return error_page("%s." % str(e).rstrip("."), link("/table.htm", t=tid))
            else:
                mv = None
            if mv is None:
                return error_page("That isn't a move this page can send.", link("/table.htm", t=tid))
            if path == "/check.htm":
                if not getattr(module, "WEB_CHECK", False):
                    return error_page("This game has no check on the web.", link("/table.htm", t=tid))
                _, final = await core.req("CHK %s %s" % (tid, mv))
                # the core's answer about the move (OK info, or ERR ILLEGAL reason); None when it said
                # something else (not seated, no game), so the module can fall back on what it knows
                reply = final if final.startswith("OK") or final.startswith("ERR ILLEGAL") else None
                return await self.show_table(core, me, sid, cookieless, tid, arg, rate, js, check=(mv, reply))
            _, final = await core.req("MOV %s %s" % (tid, mv))
            if not final.startswith("OK"):
                if module is None:
                    key = await self.table_key(core, tid)
                    module = games.get(key) if key else None
                say = getattr(module, "refusal", None)          # the game's own wording, naming the move
                return error_page(say(mv, err_text(final)) if say else err_text(final), link("/table.htm", t=tid))
            return redirect(req, link("/table.htm", t=tid))
        if path == "/table.htm":
            return await self.show_table(core, me, sid, cookieless, tid, arg, rate, js)
        if path == "/board.gif":
            return await self.board_gif(req, core, me, q, arg)
        if path == "/click.htm":
            return await self.click(req, core, me, sid, cookieless, q, arg, rate, js)
        return notice("Not found", "Not found", "There is no page at this address.",
                      [("/welcome.htm", "Welcome")], 404)

    async def table_key(self, core, tid):
        """The game type of a table ('gridwords/1'), from LST; None if it is not listed."""
        tdata, _ = await core.req("LST table=%s" % tid)
        tab = next((t for t in parse_tables(tdata) if t["id"] == tid), None)
        return tab["key"] if tab else None

    async def show_table(self, core, me, sid, cookieless, tid, arg, rate, js, check=None):
        """The table page (/table.htm), or the answer to /check.htm: check is (move, the core's reply or
        None), put in the parsed view as v["check"] for the game's web_parts to show; that page keeps still
        (no meta refresh) and offers the checked move as the picked one (sel) when the game's SEL_RE takes it.
        sid and cookieless are the resumed session, for the page's links (linker)."""
        link = self.linker(sid, cookieless, rate, js)
        lobby = link("/lobby.htm")
        watching = arg("w") == "1"
        t = await self.load_table(core, me, tid, watching)
        if isinstance(t, str):
            return error_page(t, lobby)
        tab, key, v, names, seat, events, seqs, uses = t
        log = event_log(events, seqs, names, key)
        tdata, tfinal = await core.req("TRN %s" % tid)
        log = [d for _, d in with_clients(log, parse_turns(tdata) if tfinal.startswith("OK") else [])]
        want = check[0] if check else arg("sel")
        sel = self.table_sel(key, want)
        if check:
            v["check"] = check
        invite = ""
        if tab and seat >= 0 and tab["status"] == "open":
            pending = pending_invites(events)
            free = tab["seats"] - tab["filled"] > len(pending)       # a seat nobody is invited to: offer a bot
            invite = invite_html(tid, link, tab["vis"] == "private", pending,
                                 botflag.next_name(tab["names"] + pending) if free else None)
        ndata, nfinal = await core.req("NOTE %s" % tid)
        notes = notes_html(tid, link, note_lines(ndata) if nfinal.startswith("OK") else [], seat)
        return table_page(me, tid, names, v, seat, log, watching, rate, js, link, key, sel, invite,
                          still=bool(check), notes=notes, uses=uses)

    @staticmethod
    def table_sel(key, want):
        """sel= as the game's SEL_RE takes it, else ""."""
        sel_re = games.get(key).SEL_RE
        return want if sel_re and sel_re.match(want or "") else ""

    async def load_table(self, core, me, tid, watching):
        """What every page of a table is made from, in one way (so a board image is drawn from the very
        view its page was): (table from LST or None, game type, parsed view, seated names, my seat or -1,
        the table's events, their numbers, what each seated player plays with {seat: text} from VUE C), or a
        message (str) when there is no such table or game page. The game's optional web_events(v, events) adds
        what its board shows from the events (Cannon Fire's last shot)."""
        tdata, _ = await core.req("LST table=%s" % tid)
        tab = next((t for t in parse_tables(tdata) if t["id"] == tid), None)
        seat = tab["seat_of"].get(me, -1) if tab else -1
        if watching or (watching is None and seat < 0):       # None: watch unless seated
            await core.req("SEE %s" % tid)
        vdata, final = await core.req("VUE %s GMTC" % tid)
        if not final.startswith("OK"):
            return "That table isn't here any more."
        uses = parse_clients(vdata)
        # a finished table is no longer listed: its view still names the type
        head = vdata[0].split() if vdata and vdata[0].startswith("V ") else []
        key = tab["key"] if tab else (head[1] if len(head) > 1 else "reversi/1")
        if key not in GAME_PAGES:
            return "This game has no web page yet."
        v = GAME_PAGES[key][1](vdata)
        if key == "backgammon/1":
            v["rows"] = bool(any(v["pts"]) or v["off"][0] or v["off"][1])
        ldata, _ = await core.req("SIN %s 0" % tid)
        got = [p for p in (l.split(" ", 3) for l in ldata) if len(p) == 4 and p[2].isdigit()]
        events, seqs = [p[3] for p in got], [int(p[2]) for p in got]
        if tab:
            names = tab["seated"]
        else:       # a finished table is no longer listed: who sat where is in its JOIN events
            names = []
            for f in (e.split() for e in events):
                if f[0] == "JOIN" and len(f) >= 3 and f[1].isdigit() and int(f[1]) < 16:
                    names += ["-"] * (int(f[1]) + 1 - len(names))
                    names[int(f[1])] = f[2]
            seat = names.index(me) if me in names else -1
        note = getattr(games.get(key), "web_events", None)
        if note:
            note(v, events)
        if key == "gridwords/1":        # the board in the player's own colors
            v["palette"] = await self.gw_colours(core)
        if key == "backgammon/1":       # the side the player's home board is drawn on
            v["home"] = await self.bg_home(core)
        return tab, key, v, names, seat, events, seqs, uses

    @staticmethod
    async def gw_colours(core):
        """The player's Grid Words colors, as kept (PRF gwcolors), or the default's name."""
        try:
            value = await core.pref_get(PAL.PREF_KEY)
        except CoreError:
            value = None
        return value.lower() if PAL.decode(value) else PAL.DEFAULT

    @staticmethod
    async def bg_home(core):
        """The side the player's Backgammon home board is drawn on, as kept (PRF bghome): "right" or "left"."""
        return await core.load_pref(BG_HOME_PREF, BG_HOMES, BG_HOMES[0])

    async def board_for(self, core, me, q, arg):
        """For /board.gif and /click.htm: (the game's module, the view, my seat, sel, the board, fresh), or
        None. fresh: the URL's view digest (d=) and seat (st=) are those of the view now, so the URL's image
        is this one. Not seated: watching (SEE first), as /table.htm?w=1 does."""
        tid = arg("t")
        t = await self.load_table(core, me, tid, None)
        if isinstance(t, str):
            return None
        _, key, v, _, seat, _, _, _ = t
        module = games.get(key)
        draw = getattr(module, "board_image", None)
        if draw is None or not v.get("rows"):
            return None
        sel = self.table_sel(key, arg("sel"))
        digest = view_digest(v)
        b = BOARDS.render(board_key(draw, tid, seat, sel, digest), lambda: draw(v, seat, sel))
        return module, v, seat, sel, b, arg("d") == digest and arg("st") == str(seat)

    async def board_gif(self, req, core, me, q, arg):
        """/board.gif: the board image of a table as this session's seat sees it."""
        got = await self.board_for(core, me, q, arg)
        if got is None:
            return notice("Not found", "Not found", status=404)
        b, fresh = got[4], got[5]
        if not fresh:       # the page that asked was made from another view: show this one, keep none
            return respond(200, b.gif, "image/gif", extra="Cache-Control: no-store\r\nPragma: no-cache\r\n")
        when = email.utils.formatdate(b.when, usegmt=True)
        keep = "Cache-Control: private, max-age=86400\r\n"
        since = req["headers"].get("if-modified-since")
        if since:
            try:
                if email.utils.parsedate_to_datetime(since).timestamp() >= b.when:
                    return respond(304, b"", "image/gif", extra=keep)
            except (TypeError, ValueError, OverflowError):
                pass
        return respond(200, b.gif, "image/gif", extra=keep + "Last-Modified: %s\r\n" % when)

    async def click(self, req, core, me, sid, cookieless, q, arg, rate, js):
        """/click.htm: a click on a board image by ISMAP (?x,y after the link). The point is looked up in
        the board's areas (or the game's board_click, for a board clickable anywhere), and that link's
        page is answered here, as if it had been followed; a point outside them, or a board that has
        changed since the page was made, shows the table again."""
        got = await self.board_for(core, me, q, arg)
        pt = req.get("ismap")
        if got and pt and got[5]:
            module, v, seat, sel, b, _ = got
            target = hit(b.areas, *pt)
            if target:
                path, params = target[3], dict(target[4])
            elif b.anywhere and getattr(module, "board_click", None):
                path, params = module.board_click(v, seat, sel, *pt) or (None, None)
            else:
                path = None
            if path in CLICK_TARGETS:
                link = self.linker(sid, cookieless, rate, js)
                nq = parse_qs(urlsplit(link(path, t=arg("t"), **params)).query)
                return await self.authed(req, path, nq, core, me, sid, cookieless)
        return await self.show_table(core, me, sid, cookieless, arg("t"), arg, rate, js)

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        addr = addr_key(peer[0] if peer else "?")       # IPv6 by its /48, as the core counts
        # a trusted proxy carries everyone's requests: only the whole adapter's limit holds for its
        # connections, and each request counts against its own client's address (req["src"])
        proxied = trusted_proxy(as_ip(peer[0]) if peer else None, PROXIES)
        if self.active >= MAX_REQUESTS or (not proxied and self.active_by.get(addr, 0) >= REQUESTS_PER_ADDR):
            writer.write(notice("Busy", "Busy, try again.", "This server has too many requests in progress.",
                                status=503))
            writer.close()
            return
        self.active += 1
        self.active_by[addr] = self.active_by.get(addr, 0) + 1
        try:
            await self.serve_request(reader, writer, addr)
        finally:
            self.active -= 1
            self.active_by[addr] -= 1
            if not self.active_by[addr]:
                del self.active_by[addr]

    def keep_alive(self, parts, headers, addr, served):
        """Whether this connection stays open for another request: the client asked (HTTP/1.1 unless it
        said close; HTTP/1.0 with Connection: keep-alive), it has carried fewer than KEEPALIVE_MAX, and
        the adapter and the client's address are at most half way to their limits (an idle connection
        holds a place until KEEPALIVE_IDLE runs out)."""
        conn = headers.get("connection", "").lower()
        if "close" in conn or served + 1 >= KEEPALIVE_MAX:
            return False
        if not ((len(parts) > 2 and parts[2] == "HTTP/1.1") or "keep-alive" in conn):
            return False
        return self.active <= MAX_REQUESTS // 2 and self.active_by.get(addr, 0) <= REQUESTS_PER_ADDR // 2

    async def serve_request(self, reader, writer, addr):
        """Requests on one connection: one, or more while keep_alive says so."""
        served = 0
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), KEEPALIVE_IDLE if served else 10)
                except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                        ConnectionError):
                    return
                out, keep = await self.one_request(raw, reader, writer, addr, served)
                writer.write(out)
                await writer.drain()
                served += 1
                if not keep:
                    return
        except Exception as e:      # the type and place only: a request may hold a session id or a credential
            tb = e.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            print("web error: %s at line %d" % (type(e).__name__, tb.tb_lineno), file=sys.stderr)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def one_request(self, raw, reader, writer, addr, served):
        """One request's head (and a POST's body, read here) -> (the response, keep the connection)."""
        lines = raw.decode("latin-1")[:-4].split("\r\n")
        parts = lines[0].split()
        headers = {}
        for l in lines[1:]:
            k, _, v = l.partition(":")
            if v:
                headers[k.strip().lower()] = v.strip()
        bad = any(c in l for l in lines for c in "\r\n\0")    # a bare CR or LF in a line: refused
        form = {}
        if len(parts) >= 2 and parts[0] == "POST":
            try:
                n = int(headers.get("content-length", "0"))
            except ValueError:
                n = -1
            if 0 <= n <= MAX_BODY:
                body = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
                form = parse_qs(body.decode("latin-1"), max_num_fields=MAX_FIELDS)
            else:
                parts = []
        if bad:
            return notice("No", "That request is not well formed.", status=400), False
        if len(parts) < 2 or parts[0] not in ("GET", "HEAD", "POST"):
            return notice("No", "GET or POST only.", status=405), False
        u = urlsplit(parts[1])
        query, ismap = u.query, None
        m = ISMAP_RE.search(query) if u.path == "/click.htm" else None
        if m:                       # ISMAP: the browser put ?x,y after the link's own query
            query, ismap = query[:m.start()], (int(m.group(1)), int(m.group(2)))
        peer = writer.get_extra_info("peername")
        client, proto = forwarded_client(peer[0] if peer else "?", headers, PROXIES)
        req = {"path": u.path or "/", "query": parse_qs(query, max_num_fields=MAX_FIELDS),
               "headers": headers, "method": parts[0], "form": form, "proto": proto,
               "peer": client, "src": addr_key(client), "ismap": ismap}
        try:
            out = await self.route(req)
        except (CoreError, OSError, asyncio.TimeoutError):
            out = notice("Busy", "The game server isn't answering.", links=[("/welcome.htm", "Try again")],
                         status=503)
        keep = self.keep_alive(parts, headers, addr, served)
        head, _, body = out.partition(b"\r\n\r\n")
        link = canonical(req, head)
        if link:
            head += b"\r\nLink: <" + link.encode("ascii") + b'>; rel="canonical"'
        if keep:
            head = head.replace(b"\r\nConnection: close", b"\r\nConnection: keep-alive\r\nKeep-Alive: timeout=%d, max=%d"
                                % (KEEPALIVE_IDLE, KEEPALIVE_MAX - served - 1), 1)
        if parts[0] == "HEAD":
            body = b""
        return head + b"\r\n\r\n" + body, keep


async def serve(args):
    w = Web(args.core_host, args.core_port, args.key)
    server = await asyncio.start_server(w.handle, args.host, args.port, limit=8192)
    declare.start(args, PROTOCOL, w.key_file)
    print("web adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6500)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--hosts", default=os.environ.get("PARLOR_WEB_HOSTS", ""),
                    help="web.hosts: comma-separated names (name or name:port) this site answers to; the first "
                         "is used for any other Host header (default: any well-formed Host)")
    ap.add_argument("--proxies", default=os.environ.get("PARLOR_WEB_PROXIES", ""),
                    help="web.proxies: comma-separated addresses or networks of the reverse proxies whose "
                         "X-Forwarded-For and X-Forwarded-Proto are believed (the visitor's address for the "
                         "per-address limits, and the scheme for redirects)")
    ap.add_argument("--public-url", default=os.environ.get("PARLOR_WEB_URL", ""),
                    help="web.public_url: the site's own address, such as https://games.example.org/; "
                         "pages name their address under it as canonical (a Link header)")
    ap.add_argument("--play-url", default=os.environ.get("PARLOR_PLAY_URL", ""),
                    help="play.public_url: where ParlorNet Play is (https://parlornet.org/play/); /play and "
                         "/play/ answer 301 there, and the welcome page links it while Play runs")
    a_ = ap.parse_args(argv)
    if a_.key:
        CoreClient.key_file = a_.key
    HOSTS[:] = [h.strip() for h in a_.hosts.split(",") if h.strip()]
    PROXIES[:] = proxy_list(a_.proxies, "web.proxies")
    PUBLIC_URL[0] = a_.public_url.strip()
    if PUBLIC_URL[0] and not re.match(r"^https?://[A-Za-z0-9.:\[\]-]+(/[^\s<>\"]*)?\Z", PUBLIC_URL[0]):
        raise ValueError("web.public_url is an http:// or https:// address: %r" % PUBLIC_URL[0])
    PLAY_URL[0] = a_.play_url.strip()
    a_.core_host, _, p = a_.core.rpartition(":")
    a_.core_port = int(p)
    return a_


if __name__ == "__main__":
    from adapters import web as _web     # run the importable module: the one the game modules import
    try:
        asyncio.run(_web.serve(_web.parse_args()))
    except KeyboardInterrupt:
        pass
