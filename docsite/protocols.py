"""The protocols reference (protocols.html): an entry for every protocol key a ParlorNet server can speak, in
the usual port order, each anchored by its key (protocols.html#KEY; webstyle.protocol_anchor). The game
server's own site has no list of protocols or ports: technical readers come here.
Each server extension declares its own key, name and description (adapters/declare.py, PROTOCOL in its
module); this table adds what the docs need: the usual port, origin, years, status and the games. The table
at the top of docs/protocols.md has the same keys, ports, protocols, origins and years (a test checks)."""
from adapters import clients as C
from adapters.webstyle import a, callout, data_table, esc, h1, h2, p, protocol_anchor

# key, protocol, usual port, origin, years in use, served by, status, the anchor of its part of
# docs/protocols.md, what it is (one line)
PROTOCOLS = [
    ("HTTPS", "ParlorNet Play", 443, "ParlorNet, 2026: PARLORNET/1 over a WebSocket (RFC 6455, 2011)", "2026 to now",
     "the Play extension (adapters/playserver.py; port 6513 behind a TLS proxy)", "running", "parlornet-play",
     "the client for today's browsers, phones and tablets"),
    ("NETLUFF", "NetLuff, for the Mac's NetLuff 1.4", 1744, "Henrik Linder, Sweden, 1998", "1998 to the early 2000s",
     "the NetLuff extension (adapters/netluff.py; it listens on 6517, published as 1744, NetLuff's own port)",
     "running", "netluff", "Five in a Row for NetLuff 1.4 on Macs"),
    ("HTTP", "HTTP and HTML 3.2", 6500, "HTML 3.2: W3C Recommendation, January 1997", "1997 to now",
     "the web extension (adapters/web.py)", "1997 side running; 2027 side planned", "web-site",
     "HTML 3.2 pages for any browser with tables, forms and image maps"),
    ("PARLORNET/1", "ParlorNet protocol (PARLORNET/1)", 6501, "ParlorNet, 2026", "2026 to now",
     "the core itself (parlord)", "running", "native-protocol",
     "ParlorNet's own protocol, for programs written for it"),
    ("TELNET", "Telnet with ANSI escape codes",
     6502, "Telnet: RFC 854, 1983; ANSI X3.64 / ECMA-48, 1970s; IBM PC characters (CP437), 1981", "1983 to now",
     "the telnet extension (adapters/telnet.py)", "running", "telnet",
     "full-screen menus and boards for terminal programs"),
    ("IOS", "Internet Othello Server (IOS)", 6503,
     "Igor Durdanovic, 1993, for the Waterloo computer Othello tournament", "1993 to about 2000",
     "the IOS extension (adapters/ios.py)", "running", "ios", "Reversi for clients of the Internet Othello Server"),
    ("FIBS", "First Internet Backgammon Server (FIBS)", 6505, "Andreas Schneider, 1992; the CLIP client mode, 1997",
     "1992 to now", "the FIBS extension (adapters/fibs.py)", "running", "fibs",
     "Backgammon for vintage FIBS clients"),
    ("OUTLAND", "Outland", 6506, "Outland, Inc., Palo Alto: a Mac online games service", "1994 to the late 1990s",
     "the Outland extension (adapters/outland.py; it listens on 4000, Outland's own port, published as 6506)",
     "running", "outland", "Backgammon and Reversi for the Outland Gateway on Macs"),
    ("DOOM", "DOoM crossword-game server", 6507, "John Chew, late 1993, to replace ScrabbleMOO", "1993 to about 2005",
     "the DOoM extension (adapters/doom.py)", "running", "doom", "Grid Words for DOoM clients: BobGrid and telnet"),
    ("GAMEMASTER", "GameMaster", 6508, "Rhys Hollow, Perth, December 1991", "1991 to the mid 1990s",
     "the GameMaster extension (adapters/gamemaster.py; through the ParlorNet rulebook)", "running", "gamemaster",
     "Nine games for GameMaster on 68k Macs"),
    ("NETSPROCKET", "NetSprocket, for NetBoxes", 6512,
     "Apple, 1996-99 (NetSprocket 1.7); NetBoxes: Will Helling, 2000", "2000 to the early 2000s",
     "the NetSprocket extension (adapters/netsprocket.py)", "running", "netsprocket",
     "Dots and Boxes for NetBoxes on PowerPC Macs"),
    ("BATNAV", "Batalla Naval (batnav)", 6514,
     "Ricardo Quesada, Buenos Aires, December 1995", "1995 to the late 1990s",
     "the batnav extension (adapters/batnav.py)", "running", "batnav",
     "Naval Combat for the batnav clients of 1995-98: X11, ncurses and Windows 3.x"),
    ("AWALE", "IRC, for Awale", 6515,
     "IRC: Jarkko Oikarinen, 1988 (RFC 1459, 1993); Awale's messages: Didier and Olivier Guillion, 1997",
     "1997 to 2002", "the Awale extension (adapters/awale.py)", "running", "awale",
     "Mancala with Oware rules for Awale 2.x on Macs"),
    ("NETWORDZ", "Networdz, for Networdz 2.0 on Windows", 6516, "Lance Frohman, San Francisco, 1996",
     "1996 to the late 1990s", "the Networdz extension (adapters/networdz.py)", "running", "networdz",
     "Grid Words for Networdz 2.0 on Windows"),
    ("ICDS", "Internet Checkers and Draughts Server (ICDS)", 6518,
     "Michael Rohrer, derived from FICS; release 6 in September 2000", "about 1999 to the early 2000s",
     "the ICDS extension (adapters/icds.py)", "running", "icds",
     "Checkers for xcheckers (X11) and telnet programs"),
    ("WORTHY", "Worthy Opponent, for DOS by modem", 6519, "Future Ideas Today, Inc., Lakewood, Colorado, 1988-90",
     "1988 to the early 1990s", "the Worthy Opponent extension (adapters/worthy.py; dialed with ATDT host:6519)",
     "running", "worthy", "Reversi, Checkers and Five in a Row for Worthy Opponent 2.0 on DOS"),
    ("RSCARDS", "RSCARDS, for GEnie's graphics front ends", 6520, "J. Weaver Jr., Factory Programming, 1984-94, for GEnie",
     "1987 to 1995", "the RSCARDS extension (adapters/rscards.py)", "running", "rscards",
     "All ten games for GEnie's RSCARDS front ends, by modem or serial line"),
]
KEYS = [p_[0] for p_ in PROTOCOLS]
# A note under an entry: key -> HTML. An original server still running (adapters/clients.py, COMMUNITIES) is
# mentioned first, in one line.
NOTES = {"FIBS": "ParlorNet is not FIBS and is not affiliated with it.",
         "DOOM": "ParlorNet is not DOoM and is not affiliated with it.",
         "GAMEMASTER": "A Mac needs ParlorNet's own GameMaster rulebook, which signs it in and carries its "
                       "games, GameMaster's own Othello, Dots&amp;Boxes and BattleShips among them. At a salvo "
                       "table, each player's own program receives the other fleet when shooting starts."}
EVERY_GAME = ("HTTPS", "HTTP", "PARLORNET/1", "TELNET")


def games_of(key, names):
    """The games a protocol reaches: every game for the web, telnet and PARLORNET/1; else the games of its period
    clients. names: {game type: name}."""
    if key in EVERY_GAME:
        return "every game"
    types = [t for c in C.PERIOD if c.key() == key for t in c.games]
    return ", ".join(dict.fromkeys(names.get(t, t) for t in types)) or "-"


def programs_html(key):
    """The clients that speak a protocol, each linking to its page; a planned one says so."""
    if key == "HTTPS":
        return "any current web browser"
    clients = [c for c in C.EVERY if c.key() == key]
    if key == "PARLORNET/1":
        return ", ".join([a(c.href(), esc(c.name)) for c in clients] + [a(C.FRONTENDS_PAGE + "#native",
                                                                        "your own program")])
    if not clients:
        return "none listed yet"
    return ", ".join(a(c.href(), esc(c.name)) + (" (planned)" if c.planned() else "") for c in clients)


def protocols_body(games):
    """protocols.html: a summary table (key, usual port, protocol, status), then an entry per key, anchored by
    its key: what it is, its origin and years, what serves it, its status, the games it reaches, the programs
    that speak it and its part of the guide. games: [(name, type)]."""
    names = {t: n for n, t in games}
    rows = [[a("#" + protocol_anchor(k), "<b>%s</b>" % esc(k)), "%d" % port, esc(name), esc(status)]
            for k, name, port, _, _, _, status, _, _ in PROTOCOLS]
    out = [h1("Protocols"),
           p("These are the protocols a ParlorNet server can speak. The ports are the usual ones, and %s "
             "explains how to connect, sign up and sign in with each one."
             % a(C.PROTOCOLS_DOC, "Supported protocols")),
           data_table(["Key", "Usual port", "Protocol", "Status"], rows, "100%", (None, "right"))]
    for k, name, port, origin, years, served, status, guide, what in PROTOCOLS:
        facts = [("Key", esc(k)), ("Usual port", "%d" % port), ("Origin", esc(origin)), ("In use", esc(years)),
                 ("Status in ParlorNet", esc(status)), ("Games", esc(games_of(k, names))),
                 ("Programs", programs_html(k))]
        out.append(h2(protocol_anchor(k), esc(name)) +
                   p("%s. Read %s." % (esc(what[0].upper() + what[1:]), a("%s#%s" % (C.PROTOCOLS_DOC, guide),
                                                                         "how to connect and sign in"))) +
                   (callout(p(C.original_html(C.ORIGINALS[k]), 2)) if k in C.ORIGINALS else "") +
                   p("<br>\n".join("<b>%s:</b> %s" % kv for kv in facts), 2) +
                   (p(NOTES[k], 2) if k in NOTES else ""))
    out.append(p("Ports 6511 and 6521 to 6800 are kept free for more protocols. To write your own program, read the %s."
                 % a("/docs/pnp.html", "protocol guide"), 2))
    return "".join(out)
