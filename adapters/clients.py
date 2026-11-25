"""The frontends players use, game by game: one source of data for the docs site's Clients page (index.html,
its home), the Browsers page (browsers.html), the Telnet programs page (telnet.html) and each client's own page
(clients/<id>.html). The docs site is built by tools/build_docs.py (docsite/build.py) and serves every
ParlorNet server, so nothing here names one: {host} in the text is the reader's server (HOST), and the ports
are the usual ones.

Each client's page joins two sources. This file holds the structured facts that the summary grid, the tables,
the finder and the tests need: the name, kind, year (with a one-line year note), the OS text, the platforms and
processors it runs on, the version to use, its status, its ratings (a Rating for each platform, processor or
build and version, from a real test, with its explanation), games, port and protocol, where to get it (each
entry with its kind of source and a short note), and the groups still running for it (COMMUNITIES). The prose lives
in docs/clients/<id>.md, one Markdown file per client, under a fixed outline: History, Getting it, Setting it
up, Known quirks, Screenshots (docsite/clientpages.py reads it; docs/clients/README.md tells contributors how
to add a client or a quirk). A Client takes no prose: setup=, problems=, works=, lacks= and extras= are still
accepted so that older entries load, but a test fails until their text moves to the client's page.

A Client is one program: a web browser, a telnet program, or a game client of its day that reaches a server
extension (IOS, FIBS, Outland). Each has its protocol's key (HTTP, TELNET, IOS, ...: key()), which links to its
entry in the docs site's protocols reference. A period client with status "planned" is one ParlorNet does not
serve yet; it has a page and is listed under Planned on the Clients page, not in the game tables. A program
examined and ruled out is in UNSUPPORTED_CLIENTS, with a page and the Unsupported rating, listed under Not
supported and nowhere as a native client. ParlorNet
links to authors' pages first and to public archives second, and serves none of these files: the only download
the docs site carries is ParlorNet's own Eclipse 68k Fix. docs/clients.md has the sources, the evidence for each
listing, and the coverage gaps.

The text fields are plain text with two marks: `code` becomes <tt>, and [text](url) a link. {host} is
replaced by HOST, the placeholder for the server's name. Everything else is escaped. Paths are the docs
site's, from its root (/index.html); the build makes them relative to each page.
"""
import re

from .webstyle import IVORY, SAGE, a, callout, esc, h1, h2, p, plain_table, protocol_anchor, ul

FRONTENDS_PAGE = "/index.html"         # the Clients page: the docs site's home
BROWSERS_PAGE = "/browsers.html"
TELNET_PAGE = "/telnet.html"
CLIENT_DIR = "/clients/"               # each client's page: /clients/<id>.html
SETUP_DIR = "/setup/"                   # the old period setup pages, which now point to CLIENT_DIR
ECLIPSE_FIX = "/eclipse-68k-fix.html"
PROTOCOLS_DOC = "/docs/protocols.html"   # the guide to each protocol (docs/protocols.md)
PROTOCOLS_PAGE = "/protocols.html"       # the protocols reference: an entry per key
HOST = "<i>host</i>"                     # the server's name, in every setup step
INFO_MAC = "http://ftp.funet.fi/pub/mac/info-mac/"   # funet's mirror: plain HTTP with no redirect, so period browsers can follow it
DISCMASTER = "https://discmaster.textfiles.com/"
MILL = "http://mill.1in1e6.com/"

# The Clients page's finder (and the summary grid's links): (value in the address, label). Every platform and processor a player may
# ask about is listed, whether or not a client runs there yet: an empty answer shows a gap.
PLATFORMS = [("mac", "Mac OS"), ("win", "Windows"), ("dos", "MS-DOS"), ("unix", "Unix/X11"), ("linux", "Linux"),
             ("macos", "macOS"), ("amiga", "Amiga"), ("atari", "Atari ST"), ("c64", "Commodore 64"),
             ("apple2", "Apple II")]
ARCHS = [("68000", "68000"), ("68020", "68020+"), ("ppc", "PowerPC"), ("x86", "x86"), ("x64", "x86-64"),
         ("arm", "ARM"), ("sparc", "SPARC"), ("6502", "6502")]
PLATFORM_NAMES, ARCH_NAMES = dict(PLATFORMS), dict(ARCHS)

# Where a copy comes from: the kind of each "Getting it" entry.
AUTHOR = "author's site"
ARCHIVE = "archive"
BUILT_IN = "built into the operating system"
BUNDLE = "vendor or OS bundle"
OTHER = "other"
KINDS = (AUTHOR, ARCHIVE, BUILT_IN, BUNDLE, OTHER)

class Original:
    """An original server of a protocol that is still running, mentioned wherever its protocol and clients
    are. name: what it is called; url: its home page; host and port: where its programs connect; note:
    anything more, kept with the data (plain text, may be "")."""

    def __init__(self, name, url, host, port, note=""):
        self.name, self.url, self.host, self.port, self.note = name, url, host, port, note

    def where(self):
        return "%s %d" % (self.host, self.port)


class Community(Original):
    """A group or server still running for a client or its protocol, promoted in the Community section of the
    page of each client its members use. name, url, host and port as for an Original (the host and port may be
    None when there is nothing to dial); about: a few plain sentences on what it is, who runs it, what you need
    and how to connect (text marks as below); keys: the protocol keys whose clients it serves; clients: client
    ids it serves besides those; original_of: the protocol key it is the original server of, if it is one."""

    def __init__(self, name, url, host, port, note="", about="", keys=(), clients=(), original_of=None):
        super().__init__(name, url, host, port, note)
        self.about, self.keys, self.clients, self.original_of = about, keys, clients, original_of

    def serves(self, c):
        return c.key() in self.keys or c.id in self.clients


# Groups and servers still running, each checked by connecting to it (COMMUNITIES_CHECKED). Anything found down
# or unknown is left out. Add one here; the build puts it on the page of each client it serves.
COMMUNITIES_CHECKED = "22 November 2026"
COMMUNITIES = [
    Community("FIBS", "http://fibs.com/", "fibs.com", 4321,
              "The First Internet Backgammon Server, running since 1992, with its own community and ratings.",
              "The First Internet Backgammon Server has been running since July 1992, when Andreas Schneider "
              "started it, and Patti Beadles has looked after it since 1996. It has its own long-standing "
              "community and ratings, and the FIBS clients on these pages were written for it. Connect to "
              "`fibs.com` on port 4321 with a FIBS client or any telnet program, and log in as `guest` to make "
              "an account.",
              keys=("FIBS",), original_of="FIBS"),
    Community("QuantumLink Reloaded", "https://quantumlink.net/", "q-link.net", 5190, "",
              "QuantumLink Reloaded brings the Commodore 64's Quantum Link service back, on server software that "
              "began as Jim Brain's Q-Link Reloaded in 2005. Jasmaz runs it, and members meet in People "
              "Connection every Wednesday at 8 PM Eastern for the Wednesday Night Hangout. You need a C64 or "
              "C128 (or an emulator), the Q-Link client disk and a WiFi modem. Dial `ATD\"q-link.net:5190\"` at "
              "1200 baud, with plain `ATD` rather than `ATDT`, and use the host name rather than an address.",
              keys=("QLINK",)),
    Community("NeoHabitat", "https://github.com/frandallfarmer/neohabitat", "habitat.themade.org", 1986, "",
              "NeoHabitat brings back Habitat, the online world that ran on Quantum Link as Club Caribe. The "
              "NeoHabitat project, F. Randall Farmer among others, keeps it running and develops it in the open. "
              "A C64 reaches it with a WiFi modem (`at&z0=habitat.themade.org:1986`) or the VICE emulator, and "
              "the project's pages explain the rest.",
              keys=("QLINK",)),
]
# The original servers still running, by protocol key: one line on the Clients page, the protocol's entry and
# each client row.
ORIGINALS = {g.original_of: g for g in COMMUNITIES if g.original_of}


def original_html(orig, short=False):
    """The one line that mentions an original server still running: "FIBS is still running at fibs.com
    (telnet fibs.com 4321)." short: the line for a table row, without the command."""
    if short:
        return "%s is still running at %s." % (esc(orig.name), a(orig.url, esc(orig.host)))
    return ("<b>%s is still running at %s</b> (<tt>telnet %s %d</tt>)."
            % (esc(orig.name), a(orig.url, esc(orig.host)), esc(orig.host), orig.port))


def originals_html():
    """The original servers still running, in a sentence, or "" when none is listed."""
    if not ORIGINALS:
        return ""
    return " ".join(original_html(o, True) for o in ORIGINALS.values())


# The server's sign-in methods (parlord.auth.SIGNIN), in this order on every client's page.
SIGNIN_METHODS = [("none", "Name only"), ("password", "Password")]


# ---- ratings ----------------------------------------------------------------------------------------
# A rating says how well a client plays on a parlor, for one platform, processor (or build) and version, and
# only from a real test with the program itself (docs/clients.md, Ratings). Gold: it works fully. Silver: it
# works once the program itself is patched. Bronze: it works, but something in play goes wrong or behaves
# differently from one time to the next. Unsupported: ParlorNet examined it and ruled it out. Anything not yet
# tried with the program itself is "not yet rated", with what has been tested instead.
GOLD, SILVER, BRONZE, UNSUPPORTED, NOT_RATED = "gold", "silver", "bronze", "unsupported", ""
# medal -> (its word, which is the icon's ALT text, the icon on the docs site, what it means). The icons are
# ParlorNet's own, drawn by adapters/medals.py (tools/medal_icons.py writes them into static/img/).
MEDALS = {GOLD: ("Gold", "/img/medal-gold.gif", "works fully with ParlorNet, tested with the program itself"),
          SILVER: ("Silver", "/img/medal-silver.gif", "works once the program itself is patched, and the "
                   "rating says which patch and links to it"),
          BRONZE: ("Bronze", "/img/medal-bronze.gif", "works, but something in play goes wrong or behaves "
                   "differently from one time to the next"),
          UNSUPPORTED: ("Unsupported", "/img/unsupported.gif", "examined and ruled out: it cannot play on a "
                        "parlor, and its page says why")}
ICON_SIZE = 16                              # every rating icon is ICON_SIZE pixels square
NOT_RATED_WORD = "Not yet rated"
NOT_RATED_MEANS = "nobody has played a whole game with the program itself there yet"
TESTED_WORD = {UNSUPPORTED: "Examined"}     # the word before a rating's date ("Tested" for the medals)
A_REAL_TEST = "A rating needs a whole game played with the program itself on that system."
UNRATED_NOTES = {
    "browser": "Not tried with ParlorNet yet. Period documents show that this version has the tables, forms "
               "and image maps the site needs ([the evidence](/docs/clients.html#browsers)), and ParlorNet's "
               "tests check every page of the site for them. " + A_REAL_TEST,
    "telnet": "Not tried with ParlorNet yet. Its documents show the VT100 or ANSI cursor positioning the full "
              "screen needs, and ParlorNet's tests play the telnet screens with scripted terminals. " + A_REAL_TEST,
    "planned": "ParlorNet does not serve this program yet, so it cannot be played on a parlor or rated. Its page "
               "says what is planned for it.",
    "period": "Not tried on this system yet. " + A_REAL_TEST,
}


class Rating:
    """One rating of a client, for one (platform, processors or build, version). medal: GOLD, SILVER, BRONZE,
    UNSUPPORTED or NOT_RATED. plat: a PLATFORMS value; archs: the ARCHS values it covers (the build that ran,
    or the processors tried; () when they are not recorded). version: the version tested, in words. note: a
    few plain sentences with the text marks: for a medal, what was tested, on what and how; for Silver, which
    patch, why, and a link to it; for Bronze, what goes wrong; for Unsupported, why; for NOT_RATED, what has
    been tested instead. date: the test's date (YYYY-MM-DD), "" when not rated. games: the game types it
    covers, () for all of the client's. short: the words beside its icon in the Clients page's tables (by
    default the version and where)."""

    def __init__(self, medal, plat, archs, version, note, date="", games=(), short=""):
        self.medal, self.plat, self.archs, self.version, self.note = medal, plat, tuple(archs), version, note
        self.date, self.games, self._short = date, tuple(games), short

    def word(self):
        return MEDALS[self.medal][0] if self.medal else NOT_RATED_WORD

    def where(self):
        """'Mac OS (68020+)', or 'Mac OS' when the processors are not recorded."""
        archs = ", ".join(ARCH_NAMES[x] for x in self.archs)
        return PLATFORM_NAMES[self.plat] + (" (%s)" % archs if archs else "")

    def short(self):
        return self._short or (self.version + ", " if self.version else "") + self.where()

    def covers(self, gtype="", plat="", arch=""):
        return ((not gtype or not self.games or gtype in self.games) and (not plat or plat == self.plat)
                and (not arch or arch in self.archs))


def medal_img(medal, alt=None):
    """A rating's icon, with its size and its word (or alt) as the ALT text."""
    word, src, _ = MEDALS[medal]
    return '<img src="%s" width="%d" height="%d" alt="%s">' % (src, ICON_SIZE, ICON_SIZE, esc(alt or word))


def medals_legend():
    """The four marks and "not yet rated", for the Clients page (anchor medals)."""
    items = ["%s <b>%s</b>: %s." % (medal_img(m), w, esc(means)) for m, (w, _, means) in MEDALS.items()]
    items.append("<b>%s</b>: %s." % (NOT_RATED_WORD, NOT_RATED_MEANS))
    return (p('<a name="medals"></a>Each native client is rated for each platform and version it has been '
              "tested on, and only from a real test with the program itself. Its page explains every rating. "
              "%s has the rules." % a("/docs/clients.html#ratings", "Ratings"), 2) + ul(items))


class Client:
    """One frontend program. kind: "browser", "telnet" or "period". runs: ((platform, (arch, ...)), ...).
    get: ((source kind, source, url or None, note), ...). games: the game types a period client plays
    (browsers and telnet programs play every game). status: "" (no mark), "untested", "plain mode" or
    "planned" (not served yet). ratings: its Ratings, one per (platform, processors or build, version) with
    evidence; every other place it runs on is "not yet rated" (all_ratings). A client that was examined and
    ruled out (UNSUPPORTED) has an Unsupported rating whose explanation says why. Its prose is in docs/clients/<id>.md; setup=, problems=, works=, lacks= and extras= are kept only
    so that older entries still load (a test asks for them to be moved)."""

    def __init__(self, id, name, kind, year, year_note, os, runs, get, setup=(), version="", status="",
                 status_note="", games=(), port=None, protocol="", style="", problems=(), works=(), lacks=(),
                 extras=(), bold=False, has=None, original=None, ratings=()):
        self.id, self.name, self.kind, self.year, self.year_note = id, name, kind, year, year_note
        self.os, self.runs, self.get, self.setup, self.version = os, runs, get, setup, version
        self.status, self.status_note, self.games, self.port, self.protocol = status, status_note, games, port, protocol
        self.style, self.problems, self.works, self.lacks, self.extras, self.bold = (style, problems, works, lacks,
                                                                                    extras, bold)
        self.has = has or {}        # a telnet program: arrows "yes" / "no", color, glyphs (text)
        self._original = original   # an Original: the server it was made for, still running (else its key's)
        self.ratings = tuple(ratings)

    def unsupported(self):
        return any(r.medal == UNSUPPORTED for r in self.ratings)

    def medals(self, gtype="", plat="", arch=""):
        """Its ratings with a medal (Gold, Silver, Bronze) that cover a game, platform and processor."""
        return [r for r in self.ratings if r.medal in (GOLD, SILVER, BRONZE) and r.covers(gtype, plat, arch)]

    def unrated_note(self):
        """What a place it runs on without a rating says: what has been tested instead."""
        if self.status == "untested":
            return self.status_note + " " + A_REAL_TEST
        return UNRATED_NOTES["planned" if self.planned() else self.kind]

    def all_ratings(self):
        """Its ratings, then "not yet rated" for each platform it runs on with processors no rating covers."""
        out = list(self.ratings)
        for plat, archs in self.runs:
            left = tuple(x for x in archs if not any(r.plat == plat and x in r.archs for r in self.ratings))
            if left and not (self.unsupported() and any(r.plat == plat for r in self.ratings)):
                out.append(Rating(NOT_RATED, plat, left, self.version, self.unrated_note()))
        return out

    def href(self):
        """Its own page."""
        return CLIENT_DIR + self.id + ".html"

    def planned(self):
        return self.status == "planned"

    def key(self):
        """The key of the protocol it speaks: HTTP, TELNET, its server extension's (IOS, FIBS, OUTLAND), or the
        core's own, PARLORNET/1."""
        return ({"browser": "HTTP", "telnet": "TELNET"}.get(self.kind) or PROTOCOL_KEYS.get(self.protocol)
                or self.protocol.upper())

    def guide(self):
        """Its protocol's part of the guide to the protocols (docs/protocols.md): PROTOCOLS_DOC#anchor."""
        return "%s#%s" % (PROTOCOLS_DOC, GUIDE_ANCHORS.get(self.protocol, self.protocol))

    def original(self):
        """The original server of its protocol that is still running, or None (ORIGINALS)."""
        return self._original or ORIGINALS.get(self.key())

    def runs_on(self, plat="", arch=""):
        return any((not plat or plat == p) and (not arch or arch in archs) for p, archs in self.runs)

    def plays(self, gtype):
        return self.kind != "period" or gtype in self.games

    def communities(self):
        """The groups still running for it or its protocol (COMMUNITIES)."""
        return [g for g in COMMUNITIES if g.serves(self)]

    def label(self):
        """The Client cell's text: the name, and the status in brackets when there is one."""
        status = self.status or ("unsupported" if self.unsupported() else "")
        return esc(self.name) + (" (%s)" % esc(status) if status else "")

    def marks(self, gtype=""):
        """Its medals for a game in a table row, each icon with the version and where beside it; for a period
        client with none, "not yet rated" (an untested one says so already)."""
        rs = self.medals(gtype)
        if rs:
            return "; ".join("%s %s" % (medal_img(r.medal), esc(r.short())) for r in rs)
        return NOT_RATED_WORD.lower() if self.kind == "period" and self.status != "untested" else ""


def text(s, host=HOST):
    """Plain text with `code` and [text](url) marks -> HTML 3.2 (decimal references only); {host} becomes
    host, which is HTML (HOST: the placeholder)."""
    h = esc(s)
    h = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), h)
    h = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", h)
    return h.replace("{host}", host)


def key_link(key):
    """A protocol's key, linking to its entry in the protocols reference."""
    return a("%s#%s" % (PROTOCOLS_PAGE, protocol_anchor(key)), esc(key))


# ---- browsers ---------------------------------------------------------------------------------------
# Listed only when a named version has HTML 3.2 tables, forms with SELECT, and an image map (ISMAP or
# USEMAP): the evidence for each is on the Browsers page and in docs/clients.md.

BROWSERS = [
    Client("mill", "Mill", "browser", "2026",
           "Mill came out in 2026. The first version with all three needs is 1.5, which added image maps (tables "
           "came in 0.3, forms in 0.4).",
           "Mac System 6-7 (68000, 68020+)", (("mac", ("68000", "68020")),),
           ((AUTHOR, "OneInAMillion Software", MILL,
             "`Install-Mill-2.1.2.hqx`, `Install-Mill-2.1.2.bin` or `Mill-2.1.2.image`."),),
           version="2.1.2"),
    Client("netscape", "Netscape Navigator", "browser", "1995",
           "1.1 (March-April 1995) added tables; forms and server-side image maps were there from 1.0. "
           "Client-side maps came with 2.0 (1996), cell colors with 3.0.",
           "Mac System 7 (68020+, PowerPC), Windows 3.1-NT",
           (("mac", ("68020", "ppc")), ("win", ("x86",))),
           ((ARCHIVE, "Discmaster (ftp.mayn.de mirror)", DISCMASTER + "file/11929/ftp.mayn.de-pub.zip/ftp.mayn.de-pub/"
             "apple/macintosh/browser/netscape/navigator/netscape-3.01.hqx",
             "`netscape-3.01.hqx`, 3.01 for 68k and PowerPC Macs."),
            (ARCHIVE, "Discmaster (arwen.wcs.k12.va.us mirror)", DISCMASTER + "file/12666/arwen.wcs.k12.va.us.tar/"
             "arwen.wcs.k12.va.us/pub/Netscape/3.04/windows/windows95_or_nt/navigator_standard/n32e304.exe",
             "`n32e304.exe`, the 3.04 installer for Windows 95 and NT."),
            (ARCHIVE, "Discmaster (the same mirror)", DISCMASTER + "browse/12666/arwen.wcs.k12.va.us.tar/"
             "arwen.wcs.k12.va.us/pub/Netscape/3.04/windows/windows3.1/navigator_standard",
             "`n16e304.exe`, the 3.04 installer for Windows 3.1.")),
           version="3.01 (Mac), 3.04 (Windows)"),
    Client("msie", "Internet Explorer", "browser", "1995",
           "2.0 for Windows (November 1995) has tables, forms and image maps; 2.0 for the Mac followed in April "
           "1996.",
           "Mac System 7 (68030+, PowerPC), Windows 95",
           (("mac", ("68020", "ppc")), ("win", ("x86",))),
           ((ARCHIVE, "Discmaster (ftp.mayn.de mirror)", DISCMASTER + "browse/11929/ftp.mayn.de-pub.zip/"
             "ftp.mayn.de-pub/apple/macintosh/browser/internet_explorer",
             "`msie301-m68.hqx` (68k) and `msie301-pmn.hqx` (PowerPC), 3.01 for the Mac."),
            (ARCHIVE, "Discmaster (ftp.ecsis.net mirror)", DISCMASTER + "view/12681/ftp.ecsis.net.zip/ftp.ecsis.net/"
             "pub/netuser/win95/browsers/msie302.exe",
             "`msie302.exe`, the 3.02 installer for Windows 95."),
            (BUNDLE, "Windows 95 and later", None,
             "Windows came with it from Windows 95 OSR2 on.")),
           version="3.01 (Mac), 3.02 (Windows 95)"),
    Client("mosaic", "NCSA Mosaic (Mac)", "browser", "1995",
           "Mac Mosaic 2.0 (1995) has tables, forms and server-side image maps (ISMAP), no client-side ones.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Discmaster (ftp.mayn.de mirror)", DISCMASTER + "browse/11929/ftp.mayn.de-pub.zip/"
             "ftp.mayn.de-pub/apple/macintosh/browser/mosaic",
             "`NCSAMosaic201.68K.hqx` and `NCSAMosaic201.PPC.hqx`."),),
           version="2.0.1"),
    Client("macweb", "MacWeb", "browser", "1996",
           "MacWeb 2.0 (1996) added the Netscape table extensions; 1.x had no tables.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Discmaster (Classic Mac Pack CD)", DISCMASTER + "browse/41919/classicmacpack.iso/"
             "Applications/MacWeb%202.0",
             "The MacWeb 2.0 folder from the CD, with the application, its help and bookmarks."),),
           version="2.0"),
    Client("cyberdog", "Cyberdog", "browser", "1996",
           "Apple's Cyberdog 1.0 (May 1996) supports HTML 2.0, which has forms and server-side maps, plus tables.",
           "Mac System 7.5.3 (68030+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Discmaster (ftp.mayn.de mirror)", DISCMASTER + "browse/11929/ftp.mayn.de-pub.zip/"
             "ftp.mayn.de-pub/apple/macintosh/browser/cyberdog",
             "`Cyberdog2_68kInstall.sea.Bin` and `Cyberdog2_PPCInstall.sea.Bin`."),),
           version="2.0"),
    Client("icab", "iCab", "browser", "1999",
           "iCab's first preview (February 1999) supported HTML 4.0, which has tables, forms and image maps.",
           "Mac OS 7.5-9 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((AUTHOR, "iCab (Alexander Clauss)", "https://www.icab.de/download.html",
             "The classic versions, 2.9.9 and 3.0.5."),),
           version="2.9.9 (68k), 3.0.5 (PowerPC)"),
    Client("aweb", "AWeb-II", "browser", "c. 1996",
           "AWeb 2.1 (November 1996) brought the tables promised with 2.0; the 3.x manual lists tables and "
           "client-side image maps. 3.4 is the open-source release.",
           "Amiga OS 3 (68020+)", (("amiga", ("68020",)),),
           ((ARCHIVE, "Aminet", "https://aminet.net/comm/www/AWeb_APL34_full_020.lha",
             "`AWeb_APL34_full_020.lha`."),),
           version="3.4 APL"),
    Client("cab", "CAB", "browser", "c. 1996",
           "A review of CAB 1.41 lists forms, tables and image maps; 0.98 (1995) had no forms. The review is "
           "undated.",
           "Atari ST, STe, TT, Falcon (68000+)", (("atari", ("68000", "68020")),),
           ((ARCHIVE, "atariuptodate", "https://www.atariuptodate.de/en/873/cab",
             "Cover disks with CAB 1.5 and 2.0."),),
           version="1.5"),
    Client("firefox", "Firefox", "browser", "2002",
           "First released as Phoenix 0.1 in 2002; renamed Firefox for 1.0 in 2004.",
           "Windows, macOS, Linux (x86-64, ARM)",
           (("win", ("x86", "x64", "arm")), ("macos", ("x64", "arm")), ("linux", ("x64", "arm"))),
           ((AUTHOR, "Mozilla", "https://www.mozilla.org/firefox/", "Free and open source."),)),
    Client("safari", "Safari", "browser", "2003",
           "Safari 1.0 came out in 2003.",
           "macOS (x86-64, ARM)", (("macos", ("x64", "arm")),),
           ((BUNDLE, "macOS", None, "It comes with macOS."),)),
    Client("chrome", "Chrome", "browser", "2008",
           "Google Chrome was first released in 2008.",
           "Windows, macOS, Linux (x86-64, ARM)",
           (("win", ("x64", "arm")), ("macos", ("x64", "arm")), ("linux", ("x64",))),
           ((AUTHOR, "Google", "https://www.google.com/chrome/", "Free."),)),
    Client("edge", "Edge", "browser", "2015",
           "Microsoft Edge was first released in 2015 (rebuilt on Chromium in 2020).",
           "Windows, macOS (x86-64, ARM)", (("win", ("x64", "arm")), ("macos", ("x64", "arm"))),
           ((BUNDLE, "Windows 10 and later", None, "It comes with Windows."),
            (AUTHOR, "Microsoft", "https://www.microsoft.com/edge", "For macOS and older Windows."))),
]

# ---- telnet programs ----------------------------------------------------------------------------------
# Full screen needs VT100/ANSI cursor positioning; arrow keys, color and CP437 or UTF-8 glyphs are
# optional (letter keys stand in for the arrows). Every program listed has cursor positioning; programs
# without it would play in plain mode only and are not listed.

TELNETS = [
    Client("ncsa-telnet", "NCSA Telnet", "telnet", "1986",
           "NCSA Telnet came out for the Mac and MS-DOS in 1986; 2.7b4 (1996) is the last Mac version.",
           "Mac System 6-9 (68000+, PowerPC)", (("mac", ("68000", "68020", "ppc")),),
           ((ARCHIVE, "Info-Mac", INFO_MAC + "comm/inet/ncsa-telnet-27b4.hqx",
             "`ncsa-telnet-27b4.hqx`."),),
           version="2.7b4", has=dict(arrows="yes", colour="yes (most ANSI colors, 2.7)",
                                     glyphs="no CP437 without the BBS font patch")),
    Client("better-telnet", "BetterTelnet", "telnet", "c. 1997",
           "Rolf Braun's BetterTelnet grew out of NCSA Telnet 2.7; its 1.0 notes are undated, and Info-Mac's "
           "1.2.2 is from November 1997. 2.0fc1 is the last.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((AUTHOR, "Rolf Braun", "https://www.club.cc.cmu.edu/~rbraun/files/bettertelnet-2.0fc1.sit.bin",
             "`bettertelnet-2.0fc1.sit.bin`."),
            (ARCHIVE, "Info-Mac", INFO_MAC + "comm/inet/better-telnet-122-fat.hqx",
             "`better-telnet-122-fat.hqx`, version 1.2.2.")),
           version="2.0fc1", has=dict(arrows="yes", colour="yes", glyphs="not CP437; pick the ansi display")),
    Client("nifty-telnet", "NiftyTelnet", "telnet", "1996",
           "Chris Newman's NiftyTelnet 1.1 is from 1996.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Info-Mac", INFO_MAC + "comm/inet/nifty-telnet-11.hqx",
             "`nifty-telnet-11.hqx`."),),
           version="1.1", has=dict(arrows="yes", colour="not confirmed", glyphs="not confirmed; pick ansi")),
    Client("tera-term", "Tera Term", "telnet", "1994",
           "Takashi Teranishi's Tera Term came out in 1994 for Windows 3.1; Pro 2.3 (March 1998) is for "
           "Windows 95 and NT; the Tera Term Project continues it (5.x).",
           "Windows 3.1 and later (x86, x86-64)", (("win", ("x86", "x64")),),
           ((AUTHOR, "Tera Term Project", "https://teratermproject.github.io/index-en.html",
             "Current versions for current Windows, open source."),
            (ARCHIVE, "Discmaster (angus.ind.wpi.edu mirror)", DISCMASTER + "file/12665/angus.ind.wpi.edu.tar/"
             "angus.ind.wpi.edu/pub/Windows/TeraTerm/ttermp23.zip",
             "`ttermp23.zip`, Pro 2.3 for Windows 95 and NT.")),
           version="Pro 2.3, or 5.x", has=dict(arrows="yes", colour="yes (5.x; 2.3 not confirmed)",
                                               glyphs="UTF-8 in 5.x")),
    Client("ms-kermit", "MS-DOS Kermit", "telnet", "1982",
           "Columbia University's Kermit for the PC dates from 1982; TCP/IP is built in from 3.11 (1991), ANSI "
           "color and CP437 line drawing from 3.14 (1995). 3.15 (1997) is the last.",
           "MS-DOS (x86)", (("dos", ("x86",)),),
           ((AUTHOR, "The Kermit Project", "https://www.kermitproject.org/mskermit.html",
             "Its page, with the manual."),
            (ARCHIVE, "Discmaster (kermit.columbia.edu archive)", DISCMASTER + "file/17903/kermit.columbia.edu.tar/"
             "kermit.columbia.edu/archives/msk315.zip",
             "`msk315.zip`.")),
           version="3.15", has=dict(arrows="yes", colour="yes", glyphs="CP437")),
    Client("telnet-windows", "Telnet (Windows)", "telnet", "1995",
           "Windows 95 and NT came with Microsoft Telnet 1.0; Windows 2000 and later have a console telnet.",
           "Windows 95 and later (x86, x86-64, ARM)", (("win", ("x86", "x64", "arm")),),
           ((BUNDLE, "Windows", None,
             "Windows 95 to XP have it; from Windows Vista on it is an optional feature."),),
           has=dict(arrows="not confirmed", colour="no (95/NT); not confirmed (2000 and later)",
                    glyphs="no")),
    Client("telnet-unix", "telnet (Unix, Linux, macOS)", "telnet", "1983",
           "The BSD telnet client shipped with 4.2BSD in 1983.",
           "Unix, Linux, macOS (any processor)",
           (("unix", ("sparc", "x86", "68020")), ("linux", ("x86", "x64", "arm")),
            ("macos", ("ppc", "x86", "x64", "arm"))),
           ((BUILT_IN, "the operating system", None,
             "On Linux it is a package, `telnet` or `inetutils-telnet`. macOS 10.13 and later leave it out; install one with a package manager, such as Homebrew's "
             "`telnet`."),),
           has=dict(arrows="yes", colour="yes (from the terminal)", glyphs="UTF-8 in current terminals")),
    Client("putty", "PuTTY", "telnet", "1999",
           "Simon Tatham's PuTTY was first released in January 1999.",
           "Windows (x86, x86-64, ARM), Unix, Linux",
           (("win", ("x86", "x64", "arm")), ("unix", ("x86",)), ("linux", ("x86", "x64", "arm"))),
           ((AUTHOR, "Simon Tatham", "https://www.chiark.greenend.org.uk/~sgtatham/putty/",
             "Free."),),
           has=dict(arrows="yes", colour="yes", glyphs="UTF-8")),
    Client("minitelnet", "MiniTelnet", "telnet", "2026",
           "MiniTelnet 0.29 (Marcel Jaehne) is new, from 2026, for every Amiga back to OS 1.3.",
           "Amiga OS 1.3 and later (68000+)", (("amiga", ("68000", "68020")),),
           ((ARCHIVE, "Aminet", "https://aminet.net/package/comm/tcp/MiniTelnet",
             "The `.lha` package."),),
           version="0.29", has=dict(arrows="not confirmed", colour="yes", glyphs="CP437")),
    Client("telvt102", "TelVT102", "telnet", "c. 1998",
           "Boris Cahan's TelVT102 was called the best VT100 client for the ST in a 1998 comp.sys.atari.st "
           "post; its release date was not found.",
           "Atari ST, STe, TT, Falcon (68000+)", (("atari", ("68000", "68020")),),
           ((ARCHIVE, "atariuptodate", "https://atariuptodate.de/en/968/telvt102",
             "2.02r, freeware, on a FaST Club disk (`.st`)."),),
           version="2.02r", has=dict(arrows="not confirmed", colour="not confirmed",
                                     glyphs="VT100 line graphics, not CP437; pick ansi")),
]


# ---- period game clients ------------------------------------------------------------------------------

PERIOD = [
    Client("eclipse", "Reversi The Eclipse", "period", "c. 1997",
           "1.2, the last version, was uploaded to Info-Mac by Freeverse Software in November 1997; the dates of "
           "earlier versions were not found.",
           "Mac OS 7-9 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Info-Mac", INFO_MAC + "game/reversi-the-eclipse-12.hqx",
             "`reversi-the-eclipse-12.hqx`, 2,609,150 bytes."),),
           version="1.2", games=("reversi",), port=6503, protocol="ios", style="ios",
           status_note="Reversi The Eclipse 1.2 has been played on a PowerBook 165 with System 7.6.",
           ratings=(
               Rating(SILVER, "mac", ("68020",), "1.2",
                      "On a 68k Mac the program turns any host name shorter than 32 characters into repeats of its "
                      "first letter, so it cannot connect until your copy is patched with ParlorNet's "
                      "[Eclipse 68k Fix](/eclipse-68k-fix.html), which changes two bytes. Patched, it has been "
                      "played on a PowerBook 165 with System 7.6, and the player list, Match, Observe, Talk To and "
                      "challenges work.", "2026-11-22",
                      short="1.2 with the fix, Mac OS (68020+)"),
               Rating(NOT_RATED, "mac", ("ppc",), "1.2",
                      "The PowerPC code does not have the 68k bug and needs no patch, and the program has connected "
                      "to ParlorNet from Mac OS 9.2.2 on PowerPC, but no whole game on PowerPC has been recorded "
                      "yet. " + A_REAL_TEST))),
    Client("olios", "OLIOS", "period", "c. 1994",
           "The OLIOS 0.95 sources are dated 1994-95.",
           "Unix with X11, OpenLook (SPARC)", (("unix", ("sparc",)),),
           ((ARCHIVE, "Wayback Machine (the author's site, 1999)", "https://web.archive.org/web/19991109142356id_/"
             "http://external.nj.nec.com:80/homepages/mic/bin/olios.tgz",
             "`olios.tgz`, the 0.95 source."),
            (ARCHIVE, "Discmaster (ftp.update.uu.se)", DISCMASTER + "file/11758/ftp.update.uu.se.2014.03.zip/"
             "ftp.update.uu.se/pub/othello/source/olios.static.gz",
             "`olios.static.gz`, a SunOS 4 SPARC binary from 1996.")),
           version="0.95", status="untested", games=("reversi",), port=6503, protocol="ios", style="ios",
           status_note="The IOS service follows what the OLIOS source parses, but OLIOS itself has not been run "
                       "against it."),
    Client("macfibs", "MacFIBS 3", "period", "1998",
           "MacFIBS 3 came out in January 1998. Paul Ferguson's first MacFIBS came out in September 1994, and with "
           "FIBS/W it was one of the first graphical FIBS clients.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((AUTHOR, "Paul Ferguson's MacFIBS page", "http://www.fibs.com/macfibs/Download.html",
             "MacFIBS 3 for 68K Macs and for PowerPC Macs, and 4.5.3 for PowerPC Macs with Mac OS 8.6 or later. "
             "The page's links name the host www.newfibs.fibs.com, which no longer answers, so fetch the same "
             "files from www.fibs.com, at "
             "http://www.fibs.com/macfibs/download/MacFIBS3for68K.sit.hqx and "
             "http://www.fibs.com/macfibs/download/MacFIBS3forPowerPC.sit.hqx. They need StuffIt Expander 5 or "
             "later."),),
           version="3 r6", games=("backgammon",), port=6505, protocol="fibs", style="fibs",
           status_note="MacFIBS 3 r6 (68K) has been played on a PowerBook 165 with System 7.6. It was also "
                       "tested in an emulator, with and without its hidden CLIP setting, where signing in, the "
                       "player list, invitations, play, the cube, resigning, chat and the bot all worked.",
           ratings=(
               Rating(GOLD, "mac", ("68020",), "3 r6, 68K build",
                      "The 68K build has played Backgammon on a PowerBook 165 with System 7.6, and on an emulated "
                      "Mac with System 7.6.1, with and without its hidden CLIP setting. Signing in, the player "
                      "list, invitations both ways, moves, doubles, resigning, chat, a resumed match and matches "
                      "against the bot all worked. Its own slips, such as a player list that sometimes stays empty, "
                      "have the remedies given under Known quirks.", "2026-11-22",
                      short="3 r6 68K build, Mac OS (68020+)"),
               Rating(NOT_RATED, "mac", ("ppc",), "3 r6, PowerPC build",
                      "The PowerPC build has not been tried yet, only the 68K build. " + A_REAL_TEST))),
    Client("fibsw", "FIBS/W", "period", "1994",
           "Robin Davies' FIBS/W came in 1994; 1.30 is from January 1995.",
           "Windows 3.1 (x86)", (("win", ("x86",)),),
           ((ARCHIVE, "Discmaster (CICA archive)", DISCMASTER + "file/35794/cica.zip/cica/WIN3/GAMES/FIBSW130.ZIP",
             "`FIBSW130.ZIP`."),),
           version="1.30", status="untested", games=("backgammon",), port=6505, protocol="fibs", style="fibs",
           status_note="Tested with scripted clients that follow FIBS/W's login scripts, not yet with FIBS/W."),
    Client("xfibs", "xfibs", "period", "c. 1995",
           "The 0.8 manual page is dated 22 August 1995; no earlier release was found.",
           "Unix with X11 and Motif (source)", (("unix", ("sparc", "x86")),),
           ((ARCHIVE, "Discmaster (X.Org contrib)", DISCMASTER + "file/29292/2014.06.x.org.tar/x.org/contrib/games/"
             "xfibs08.tar.gz",
             "`xfibs08.tar.gz`, the 0.8 source."),),
           version="0.8", status="untested", games=("backgammon",), port=6505, protocol="fibs", style="fibs",
           status_note="Tested with scripted clients that behave as xfibs does, not yet with xfibs itself."),
    Client("xibc", "xibc", "period", "1994",
           "xibc 0.31 is dated March 1994.",
           "Unix with X11 and Expectk (script)", (("unix", ("sparc", "x86")),),
           ((ARCHIVE, "Discmaster (LANGUAGE OS CD)", DISCMASTER + "file/951/LANGUAGE%20OS.iso/tcl/code.zip/code/"
             "xibc-0.31.tar.gz",
             "`xibc-0.31.tar.gz`, the 0.31 script."),),
           version="0.31", status="untested", games=("backgammon",), port=6505, protocol="fibs", style="fibs",
           status_note="Tested with scripted clients that behave as xibc does, not yet with xibc itself."),
    Client("outland", "Outland Gateway, Backgammon and Reversi", "period", "1994",
           "Outland opened in 1994 with Reversi among its games, and Backgammon was part of it by early 1995.",
           "Mac System 7 (68020+, PowerPC)", (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Discmaster (Macworld CD, February 1998)", DISCMASTER + "file/23819/Macworld%20%281998-02%29.dmg/"
             "Games%20World/Shareware%20Games/Board/Outland%20Installer/Outland%20Installer",
             "`Outland Installer` installs Gateway 1.3.2, the Outland TCP Tool, Backgammon and Reversi."),
            (ARCHIVE, "Info-Mac", INFO_MAC + "game/com/outland-reversi-111.hqx",
             "`outland-reversi-111.hqx`, Reversi 1.1.1 alone, for the Outland Games folder."),
            (ARCHIVE, "Info-Mac", INFO_MAC + "game/com/outland-min-install.hqx",
             "`outland-min-install.hqx`, the Gateway alone with no games.")),
           version="Gateway 1.3.2", games=("backgammon", "reversi"), port=6506, protocol="outland",
           style="outland",
           status_note="The Gateway 1.3.2 and Reversi 1.1.1 have played a whole game against the bot on an emulated "
                       "Mac with System 7.6.1, and Backgammon has started one there.",
           ratings=(
               Rating(GOLD, "mac", ("68020",), "Gateway 1.3.2 with Reversi 1.1.1",
                      "The Gateway 1.3.2 and Reversi 1.1.1 have played a whole game against the bot on an emulated "
                      "Mac with System 7.6.1. Reversi needs working sound, as Known quirks says.", "2026-11-22",
                      games=("reversi",), short="Reversi 1.1.1, Mac OS (68020+)"),
               Rating(NOT_RATED, "mac", ("68020",), "Gateway 1.3.2 with Backgammon (August 1995)",
                      "Backgammon has signed in and started a game against the bot on the same emulated Mac, but no "
                      "whole game has been played with it yet. " + A_REAL_TEST, games=("backgammon",)))),
    Client("bobgrid", "BobGrid", "period", "1999",
           "Mark Watkins wrote BobGrid in 1999 as a graphical front end for DOoM; 2.3, from October 1999, is the "
           "last version.",
           "Windows 95 and later, Unix, Linux (Java 1.1)",
           (("win", ("x86",)), ("unix", ("sparc", "x86")), ("linux", ("x86",))),
           ((ARCHIVE, "Internet Archive (the author's download page)",
             "http://web.archive.org/web/20011028232225/http://www.math.uga.edu:80/~mwatkins/BobGrid.EXE",
             "`BobGrid.EXE`, a ZIP archive of the Java classes. Freeware."),),
           version="2.3", games=("gridwords",), port=6507, protocol="doom", style="doom",
           status_note="BobGrid 2.3 has played Grid Words against the bot under the Blackdown JDK 1.1.8 on Linux, "
                       "signed in by name alone and with a password: the board, rack, scores, logs and tile tracker "
                       "follow the game. Review stops one move short of the latest play.",
           ratings=(
               Rating(BRONZE, "linux", ("x86",), "2.3, Blackdown JDK 1.1.8",
                      "BobGrid 2.3 has played Grid Words against the bot under the Blackdown JDK 1.1.8 on Linux, "
                      "signing in by name alone and with a password, and the board, rack, scores, logs and tile "
                      "tracker follow the game. It is Bronze for what goes wrong around the play: Review stops one "
                      "move short of the latest play, the status box cuts off the second player's score, and "
                      "signing up from BobGrid froze it until the server was fixed and has not been tried again "
                      "since.", "2026-11-23", short="2.3, Linux (x86)"),)),
    Client("gamemaster", "GameMaster", "period", "1991",
           "GameMaster 1.0.0 and its User Kit rulebooks are dated December 1991.",
           "Mac Plus or later, System 6 or 7, with MacTCP", (("mac", ("68000", "68020")),),
           ((ARCHIVE, "Discmaster (BBS in a Box Trilogy II CD)", DISCMASTER + "browse/27148/"
             "BBS%20in%20a%20box%20-%20Trilogy%20II.iso/Files/Game/F-G/GameMaster",
             "The folder with `GameMaster` and its rulebooks. Put `GameMaster`, `Dots&Boxes`, `Othello` and "
             "`BattleShips` in one folder, with ParlorNet's rulebooks. Povertyware."),
            (ARCHIVE, "Macintosh Repository", "https://www.macintoshrepository.org/9523-gamemaster",
             "The same program and rulebooks.")),
           version="1.0.0", games=("dotsandboxes", "reversi", "navalcombat"), port=6508, protocol="gamemaster",
           style="gamemaster",
           status_note="Its Othello, Dots&Boxes and BattleShips have played against the bot on an emulated Mac with "
                       "System 7.6.1, through ParlorNet's rulebook.",
           ratings=(
               Rating(GOLD, "mac", ("68020",), "1.0.0",
                      "GameMaster 1.0.0 has played a whole game each of Othello, Dots&Boxes and BattleShips "
                      "against the bot on an emulated Mac with System 7.6.1. On the same Mac, ParlorNet's rulebook "
                      "signed in and started Othello and BattleShips, and Othello and Dots&Boxes moves went both "
                      "ways through it.",
                      "2026-11-22"),
               Rating(GOLD, "mac", ("68000",), "1.0.0",
                      "On an emulated Mac Plus (Mini vMac) on LocalTalk through a MacIP gateway, GameMaster 1.0.0 with "
                      "ParlorNet's rulebook played a whole game each of Othello, Dots&Boxes and BattleShips against "
                      "the bot under System 6.0.8 with 4 MB, Othello again with 1 MB at a real Plus's speed, and "
                      "Othello under System 7.5.5.",
                      "2026-11-24"))),
    Client("parlornet-rulebooks", "ParlorNet rulebooks for GameMaster", "period", "2026",
           "ParlorNet's Checkers, Mancala, Five in a Row, Grid Words, Rummy Tile and Cannon Fire rulebooks for "
           "GameMaster 1.0.0 came out in 2026, and its Backgammon rulebook later that year.",
           "Mac Plus or later, MacTCP, GameMaster 1.0.0", (("mac", ("68000", "68020")),),
           ((OTHER, "ParlorNet's docs site", None,
             "[Install ParlorNet Rulebooks](/gamemaster-rulebooks.html), which also installs ParlorNet's own "
             "rulebook, and each rulebook on its own. MIT license."),),
           version="1.1.1", games=("checkers", "mancala", "fiveinarow", "gridwords", "rummytile", "cannonfire",
                                   "backgammon"),
           port=6508, protocol="gamemaster", style="gamemaster",
           status_note="All seven have played against the bot on an emulated Mac with System 7.6.1, and they run "
                       "on an emulated Mac Plus.",
           ratings=(
               Rating(GOLD, "mac", ("68020",), "1.1.1",
                      "Checkers, Mancala and Five in a Row have each been played to the end against the bot on an "
                      "emulated Mac with System 7.6.1 and GameMaster 1.0.0, with an invitation accepted, a swap of "
                      "players, a resignation and a draw offer.", "2026-11-22",
                      games=("checkers", "mancala", "fiveinarow")),
               Rating(NOT_RATED, "mac", ("68020",), "1.1.1",
                      "Grid Words, Rummy Tile and Cannon Fire have played turns against the bot on the same emulated "
                      "Mac, a word scored and an exchange, tiles laid and drawn, and a shot each way, but no whole "
                      "game yet. " + A_REAL_TEST, games=("gridwords", "rummytile", "cannonfire")),
               Rating(GOLD, "mac", ("68000",), "1.1.1",
                      "On an emulated Mac Plus (Mini vMac) with System 6.0.8 on LocalTalk through a MacIP gateway, "
                      "Checkers, Mancala, Five in a Row, Rummy Tile and Cannon Fire have each been played to the end "
                      "against the bot with 4 MB, and Checkers again with 1 MB at a real Plus's speed.",
                      "2026-11-24", games=("checkers", "mancala", "fiveinarow", "rummytile", "cannonfire")),
               Rating(NOT_RATED, "mac", ("68000",), "1.1.1",
                      "On the same emulated Mac Plus, Grid Words scored a word and the bot replied, but no whole "
                      "game has been played yet. " + A_REAL_TEST, games=("gridwords",)),
               Rating(GOLD, "mac", ("68020",), "1.1.1",
                      "Backgammon has been played to the end three times on an emulated Mac with System 7.6.1 and "
                      "GameMaster 1.0.0, started from the ParlorNet rulebook 0.5: twice against the bot, which took "
                      "the Mac's double, and once against a player who took the Mac's double and redoubled, which "
                      "the Mac took. Undo, the mirrored board and bearing off were used along the way.",
                      "2026-11-24", games=("backgammon",)),
               Rating(GOLD, "mac", ("68000",), "1.1.1",
                      "On an emulated Mac Plus (Mini vMac) with System 6.0.8 and 4 MB on LocalTalk through a MacIP "
                      "gateway, Backgammon has been played to the end twice: against the bot, which took the Mac's "
                      "double, and against a player who doubled, which the Mac took.", "2026-11-24",
                      games=("backgammon",)))),
    Client("netboxes", "NetBoxes 1.1", "period", "2000",
           "Will Helling's NetBoxes 1.1 (Brainfreeze Entertainment) is dated 22 March 2000.",
           "Mac OS 8 and 9 (PowerPC), with NetSprocket", (("mac", ("ppc",)),),
           ((ARCHIVE, "Discmaster (Macworld June 2000 CD)", DISCMASTER + "browse/32003/Macworld_June_2000.iso/"
             "Games%20World/Top%2010%20Shareware%20games/NetBoxes%20v1.1",
             "The folder with `NetBoxes v1.1` and its ReadMe. US$10 shareware."),
            (ARCHIVE, "Internet Archive (Apple's NetSprocket SDK)",
             "https://archive.org/download/net-sprocket-sdk/NetSprocket_SDK.sit.hqx",
             "`NetSprocketLib`, for the Extensions folder of a Mac that has no NetSprocket yet.")),
           version="1.1", games=("dotsandboxes",), port=6512, protocol="netsprocket", style="netsprocket",
           status_note="NetBoxes 1.1 has played against the bot, and against a second copy, on emulated Macs "
                       "with Mac OS 9.2.2.",
           ratings=(
               Rating(GOLD, "mac", ("ppc",), "1.1",
                      "Played on emulated Power Macs with Mac OS 9.2.2: whole games against the bot at several board "
                      "sizes, Mac against Mac with a rematch, and a three-player game with a resignation. The Mac's "
                      "end screen agreed with the parlor's result every time.", "2026-11-23"),)),
    Client("batnav", "Batalla Naval (batnav): xbtclient, nbtclient and btwin", "period", "1995",
           "The X client's first public version, 0.28, is dated 28 December 1995. Version 0.50 (September 1997) "
           "brought the X client, the ncurses client and the Windows 3.x client btwin 0.48 together.",
           "Linux and Unix (X11 or a terminal), Windows 3.x",
           (("linux", ("x86",)), ("unix", ("x86",)), ("win", ("x86",))),
           ((ARCHIVE, "ibiblio (sunsite's Linux archive)",
             "https://www.ibiblio.org/pub/linux/games/multiplayer/batnav-0.50.tar.gz",
             "`batnav-0.50.tar.gz`: the source of the server and both Unix clients, their i386 Linux binaries, "
             "and the Windows 3.x client `btwin.exe` with its source. GPL."),
            (ARCHIVE, "ibiblio (sunsite as it was in September 1996)",
             "https://www.ibiblio.org/pub/historic-linux/ftp-archives/sunsite.unc.edu/Sep-29-1996/games/networked/"
             "batnav40.tgz",
             "`batnav40.tgz`, version 0.40 (April 1996), with the X and ncurses clients only.")),
           version="0.50 (btwin 0.48)", games=("navalcombat",), port=6514, protocol="batnav", style="batnav",
           status_note="xbtclient 0.50 (the 1997 binary, on an emulated 32-bit Debian 6), nbtclient 0.50 and 0.60's "
                       "GTK client have played whole games against the bot. btwin 0.48 has connected and signed in under "
                       "Wine; a game with it is still to be played.",
           ratings=(
               Rating(GOLD, "linux", ("x86",), "nbtclient 0.50",
                      "The ncurses client, built from the 0.50 source on Debian 8, played a whole game against the "
                      "bot on a local parlor.", "2026-11-23", short="nbtclient 0.50"),
               Rating(GOLD, "linux", ("x86",), "xbtclient 0.50",
                      "The X client, the 1997 i386 binary from the author's archive, played a whole game against the "
                      "bot on an emulated 32-bit Debian 6.", "2026-11-23", short="xbtclient 0.50"),
               Rating(GOLD, "linux", ("x86",), "gbnclient 0.60",
                      "The GTK client of March 1998, built against GTK 1.2 on Debian 5, played a whole game against "
                      "the bot.", "2026-11-23", short="gbnclient 0.60"),
           )),
    Client("awale", "Awale", "period", "1997",
           "Didier and Olivier Guillion's Awale 2.2.2, dated 1 December 1997, was the first version that plays "
           "live over the Internet, through IRC.",
           "Mac System 7 or 8, MacTCP or Open Transport", (("mac", ("68020", "ppc")),),
           ((AUTHOR, "Myriad", "https://www.myriad-online.com/en/products/awale.htm",
             "Myriad's page for Awale, which offers the current version (not one that plays here) and its history."),
            (ARCHIVE, "Info-Mac", INFO_MAC + "game/awale-222c.hqx",
             "`awale-222c.hqx`, Awale 2.2.2c for the Mac, with its installer. Shareware.")),
           version="2.2.2c", games=("mancala",), port=6515, protocol="awale", style="awale",
           status_note="Awale 2.2.2c has played whole games against the bot on an emulated Mac with System 7.6.1.",
           ratings=(
               Rating(GOLD, "mac", ("68020",), "2.2.2c",
                      "Awale 2.2.2c, running its 68k code, has played whole games against the bot on an emulated "
                      "Mac with System 7.6.1 and Open Transport, with the parlor's lines in its message box. Where "
                      "the parlor ends a game earlier than Awale would, Awale shows its own deadlock message, as "
                      "Known quirks says.", "2026-11-23"),)),
    Client("netluff", "NetLuff", "period", "1998",
           "Henrik Linder's NetLuff 1.4, from Sweden, is dated 10 October 1998.",
           "Mac System 7.5.1 to 9, Open Transport",
           (("mac", ("68020", "ppc")),),
           ((ARCHIVE, "Info-Mac", INFO_MAC + "game/net-luff-14.hqx",
             "`net-luff-14.hqx`, the program and its Read Me. US$10 shareware."),),
           version="1.4", games=("fiveinarow",), port=1744, protocol="netluff", style="netluff",
           status_note="NetLuff 1.4 has played against the bot, and against a second copy through the parlor, on "
                       "emulated Power Macs with Mac OS 9.2.2.",
           ratings=(
               Rating(GOLD, "mac", ("ppc",), "1.4",
                      "Played on emulated Power Macs with Mac OS 9.2.2: a whole game against the bot, won, and a "
                      "game between two copies of NetLuff through the parlor. NetLuff always connects to port 1744, "
                      "so the parlor must publish that port; the program itself is not changed.", "2026-11-23"),)),
    Client("xcheckers", "xcheckers", "period", "2000",
           "Peter Chiocchetti's README for xcheckers is dated 17 March 2000; 2.2.3 is the last version.",
           "Linux and Unix with X11 and a telnet program", (("linux", ("x86",)), ("unix", ("x86",))),
           ((AUTHOR, "Peter Chiocchetti", "http://arton.cunst.net/xcheckers/",
             "The author's page, with `xcheckers-2.2.3.tar.gz`: the source, with the simplech engine. GPL."),),
           version="2.2.3", games=("checkers",), port=6518, protocol="icds", style="icds",
           status_note="xcheckers 2.2.3, built from its source on Linux, has signed up and played a whole game "
                       "against the bot.",
           ratings=(
               Rating(GOLD, "linux", ("x86",), "2.2.3",
                      "Built unchanged from the 2.2.3 source on Debian 12 (x86-64, with -fcommon) and run under "
                      "Xvfb: signed up as guest in its terminal, challenged the bot, switched itself to the style 5 "
                      "board and played whole games by dragging pieces, with a double jump dragged in one go and "
                      "another a jump at a time.", "2026-11-24"),)),
    Client("worthy-opponent", "Worthy Opponent", "period", "1990",
           "Future Ideas Today's Worthy Opponent came out in 1988; version 2.0 is dated January 1990.",
           "MS-DOS with a Hayes modem (x86)", (("dos", ("x86",)),),
           ((ARCHIVE, "Discmaster (GameWare Collection CD, 1993)", DISCMASTER + "file/13114/"
             "GameWare%20Collection%20%28CMS%20Software%29%20%281993%29.iso/games/w_games/worthy.zip",
             "`worthy.zip`, version 2.0 with its manual. US$15 shareware."),),
           version="2.0", games=("reversi", "checkers", "fiveinarow"), port=6519, protocol="worthy",
           style="worthy",
           status_note="Worthy Opponent 2.0 has played whole games of CornerStone, Checkers and Quinte against the "
                       "bot in DOSBox, dialing the parlor with DOSBox's emulated modem.",
           ratings=(
               Rating(GOLD, "dos", ("x86",), "2.0",
                      "Played in DOSBox 0.74-3 with its emulated modem dialing `ATDT host:port` from the program's "
                      "phone book: a whole game each of CornerStone, Checkers and Quinte against the bot, the PC's "
                      "board ending as the server's every time. Backgammon and the eight action games are not "
                      "played here. The program itself is not changed.", "2026-11-24"),)),
    Client("networdz", "Networdz 2.0", "period", "1996",
           "Lance Frohman's Networdz 2.0, the first version that plays over a network, is dated October 1996.",
           "Windows 3.1 and later (Wine tested)", (("win", ("x86",)),),
           ((ARCHIVE, "Discmaster (Giga Games 5, Walnut Creek, June 1997)",
             DISCMASTER + "file/10566/Giga%20Games%205%20(Walnut%20Creek)%20(June%201997).iso/windows/puzzles/"
             "networdz.zip",
             "`networdz.zip`, the program, its help file and dictionaries. US$12 shareware."),
            (OTHER, "ParlorNet Link", "https://github.com/eric-cgn/parlornet-networdz-link/releases",
             "`PNLINK.ZIP`, ParlorNet's own program that connects Networdz to the parlor. MIT license.")),
           version="2.0", games=("gridwords",), port=6516, protocol="networdz", style="networdz",
           status_note="Networdz 2.0 has played whole games on the parlor through ParlorNet Link, under Wine.",
           ratings=(
               Rating(GOLD, "win", ("x86",), "2.0 with ParlorNet Link 1.0",
                      "Stock Networdz 2.0 under Wine 8.0 (32-bit Debian), with ParlorNet Link 1.0 beside it, played "
                      "a whole game against the bot to the end, its scores the parlor's, with a refused word and an "
                      "exchange, and a game with chat of two Networdz PCs and a web player. The Link is an add-on, "
                      "not a patch. Real Windows is not yet tried.",
                      "2026-11-25", short="2.0, Windows (x86)"),)),
    Client("rscards", "RSCARDS (GEnie)", "period", "1987",
           "J. Weaver Jr.'s front ends for GEnie's graphics games date from 1984 to 1994; GEnie ran the games from "
           "about 1987 to 1995. The Mac version 5.0 is dated January 1994 and the IBM PC version 5.02 October 1994.",
           "Mac, MS-DOS, Commodore 128, Amiga; serial line",
           (("mac", ("68000", "68020")), ("dos", ("x86",)), ("c64", ("6502",)), ("amiga", ("68000",))),
           ((ARCHIVE, "Discmaster", DISCMASTER + "file/22152/UltraMacGames1.iso/GEnie/RSCARDS/RSCARDS%20%28color%29"
             "%20Folder/RSCARDS%20%28color%29",
             "`RSCARDS (color)`, the Mac's color version 5.0, MacBinary; the mono version is in the folder beside "
             "it."),
            (ARCHIVE, "Discmaster", DISCMASTER + "file/18228/internet.zip/internet/INTERNET/ONLINE/GENIE/MISC/"
             "RSCARDS.EXE", "`RSCARDS.EXE`, the IBM PC version 5.02 for MS-DOS."),
            (ARCHIVE, "Discmaster", DISCMASTER + "file/17620/archives.tar/archives/genie-commodore-file-library/"
             "Wizard/RSCV50%28C%29.SFX", "`RSCV50(C).SFX`, the Commodore 128 color version 5.00, a self-extracting "
             "archive; `RSCARDS-V21M.SFX` beside it is the mono version 2.10."),
            (ARCHIVE, "Discmaster", DISCMASTER + "file/1310/Network%20CD%20-%20Volume%202.iso/legaltools/"
             "lsdlt043.dms/lsdlt043.adf/RSCARDS", "`RSCARDS`, the Amiga version 4.01, on the LSD Legal Tools 043 "
             "disk.")),
           version="5.02 (IBM PC), 5.0 (Mac)",
           games=("reversi", "backgammon", "checkers", "mancala", "fiveinarow", "dotsandboxes", "navalcombat",
                  "gridwords", "rummytile", "cannonfire"),
           port=6520, protocol="rscards", style="rscards",
           status_note="The IBM PC version 5.02 and the Mac's color and mono versions 5.0 have played whole games "
                       "of all ten against the bot under emulators.",
           ratings=(
               Rating(GOLD, "dos", ("x86",), "5.02",
                      "RSCARDS 5.02 for the IBM PC, unchanged, in DOSBox 0.74 (VGA) with its emulated modem dialing "
                      "the parlor: signed in and played a whole game of each of the ten against the bot by clicks "
                      "(Grid Words' words typed).", "2026-11-24"),
               Rating(GOLD, "mac", ("68020",), "5.0 (color)",
                      "The color version 5.0, unchanged but for a larger memory size in Get Info, in Basilisk II "
                      "(a Quadra 900 with System 7.6.1) with its modem port bridged to the parlor: signed in and "
                      "played a whole game of each of the ten against the bot by clicks (Grid Words' words typed).",
                      "2026-11-24", short="5.0 color, Mac OS"),
               Rating(GOLD, "mac", ("68020",), "5.0 (mono)",
                      "The mono version 5.0, unchanged, in the same Basilisk II: the same ten whole games in black "
                      "and white.", "2026-11-24", short="5.0 mono, Mac OS"),
               Rating(NOT_RATED, "c64", ("6502",), "5.00 (Commodore 128)",
                      "Tried in VICE: it draws discs in one size and colors whole 8 by 8 cells, a poor fit; on a "
                      "Commodore, play with ParlorNet's own C64 and C128 client."),
               Rating(NOT_RATED, "amiga", ("68000",), "4.01",
                      "Not tried: in an emulator it needs a Kickstart ROM, which AROS cannot stand in for."),)),
    Client("parlornet-8bit", "ParlorNet for the Apple IIe, IIgs and C64", "period", "2026",
           "Version 1.0, for the Apple IIe and IIgs, came out in November 2026, and 2.0 added the Commodore 64 the "
           "same month.",
           "Apple IIe and IIgs, Commodore 64 and 128", (("apple2", ("6502",)), ("c64", ("6502",))),
           ((AUTHOR, "ParlorNet's releases on GitHub", "https://github.com/eric-cgn/parlornet-8bit/releases/tag/v2.0",
             "`parlornet.dsk`, the Apple IIe's self-booting disk; `parlor-files.po`, the program for your own "
             "ProDOS; `parlornet-gs.po`, the Apple IIgs program for GS/OS with Marinetti; and `parlornet.d64`, "
             "the Commodore 64's disk, which a Commodore 128 boots too. MIT license."),),
           version="2.0",
           games=("reversi", "backgammon", "gridwords", "dotsandboxes", "navalcombat", "checkers", "mancala",
                  "fiveinarow", "rummytile", "cannonfire"),
           port=6501, protocol="parlornet", style="parlornet",
           status_note="Version 2.0 has played all ten games to the end against the bot on an emulated Apple IIe "
                       "and an emulated Commodore 64.",
           ratings=(
               Rating(GOLD, "apple2", ("6502",), "2.0 on the Apple IIe",
                      "On an emulated Apple IIe (MAME, with a Super Serial Card), version 2.0 played each of the "
                      "ten games to the end against the bot.", "2026-11-24", short="2.0, Apple IIe"),
               Rating(NOT_RATED, "apple2", (), "2.0 on the Apple IIgs",
                      "The GS/OS program for the Apple IIgs has not been through that test in an emulator yet. On a "
                      "IIgs, the Apple IIe disk also runs as it is, with a Super Serial Card or an Uthernet II "
                      "card. " + A_REAL_TEST),
               Rating(GOLD, "c64", ("6502",), "2.0 on the Commodore 64",
                      "On an emulated Commodore 64 (VICE, with its user-port RS-232), version 2.0 played each of "
                      "the ten games to the end against the bot. A Commodore 128 runs the same program in its "
                      "64 mode.", "2026-11-24", short="2.0, Commodore 64"),)),
]

# ---- planned clients ----------------------------------------------------------------------------------
# Period clients ParlorNet does not serve yet, with a group still running for them (COMMUNITIES): each has its
# page, and the Clients page lists them under Planned, not in the game tables.

PLANNED = [
    Client("qlink", "Q-Link (QuantumLink)", "period", "1985",
           "Quantum Link opened on 5 November 1985 and closed on 1 November 1994.",
           "Commodore 64 and 128 (6502)", (("c64", ("6502",)),),
           ((ARCHIVE, "Internet Archive", "https://archive.org/details/qlinkv4a",
             "`qlinkv4a.d64` and `qlinkv4b.d64`, the version 4 client disk."),
            (ARCHIVE, "funet", "https://ftp.funet.fi/pub/cbm/c64/comm/QuantumLink.d64.gz",
             "`QuantumLink.d64.gz`, the client disk.")),
           status="planned", games=("navalcombat",), protocol="qlink",
           status_note="A Q-Link extension is planned, starting with Sea Strike (Naval Combat), the game on "
                       "every client disk."),
]

# ---- unsupported programs -----------------------------------------------------------------------------
# Programs ParlorNet examined and ruled out: each has its page and an Unsupported rating saying why, and the
# Clients page lists them under Not supported. They are not native clients: they are
# in no game table, the summary grid or the finder.

UNSUPPORTED_CLIENTS = [
    Client("koth", "KOTH (King of the Hill)", "period", "1999",
           "KOTH's 0.3 pre-release came out in June 1999 and 0.7.6 in August 1999; 0.8.0, from February 2003, is "
           "the last.",
           "Unix and Linux with LibGGI (source)", (("unix", ("sparc", "x86")), ("linux", ("x86", "x64"))),
           ((AUTHOR, "KOTH's home page", "https://www.nongnu.org/koth/", "The project's page, with its news."),
            (ARCHIVE, "ibiblio", "https://ibiblio.org/pub/linux/games/multiplayer/koth-0.8.0.tar.bz2",
             "`koth-0.8.0.tar.bz2`, the 0.8.0 source. GPL.")),
           version="0.7.6 and 0.8.0", games=("cannonfire",),
           ratings=[Rating(UNSUPPORTED, plat, archs, "0.7.6 and 0.8.0",
                           "Every KOTH client flies each shell and digs each crater itself, and the server only "
                           "passes on the angle, the speed and the weapon. On a parlor the server decides where every "
                           "shell lands, by Cannon Fire's rules, so a KOTH player would see a different shot from the "
                           "one that counts. The work is tabled in the [backlog](/docs/backlog.html).", "2026-11-23")
                    for plat, archs in (("unix", ("sparc", "x86")), ("linux", ("x86", "x64")))]),
    Client("domewars", "Dome Wars", "period", "c. 1997",
           "Nathan Sturtevant's Dome Wars 1.0 was on a MacFormat CD in January 1996. 1.2b1 (copyright 1995-97) is "
           "the only version known to play over a network.",
           "Mac OS with NetSprocket (PowerPC)", (("mac", ("ppc",)),),
           ((ARCHIVE, "Discmaster (Inside Mac Games CD, volume 5 number 3)", DISCMASTER + "browse/11992/"
             "IMG%2046%20Vol%205-3.iso/Shareware/Dome%20Wars%201.2",
             "The Dome Wars 1.2 folder, with the program, its graphics and its Read Me."),
            (ARCHIVE, "Macintosh Garden", "http://macintoshgarden.org/games/dome-wars",
             "Other versions; the 1.5.1 Read Me says to use 1.2b1 for network games.")),
           version="1.2b1", games=("cannonfire",),
           ratings=(Rating(UNSUPPORTED, "mac", ("ppc",), "1.2b1",
                           "Every Mac in a Dome Wars game flies each shell, digs the craters and deals the damage "
                           "with its own code, and no message can say where a shell went. A server could only reset "
                           "the world after each shot, so a player would see the shell land in one place and the "
                           "world jump to another. A fair game would need Dome Wars' own physics on the server, "
                           "which ParlorNet does not plan.", "2026-11-23"),)),
    Client("xscrabble", "XScrabble", "period", "1994",
           "Matt Chapman's XScrabble 1.0 is from November 1994; Jean-Pierre Demailly's 2.x versions followed, 2.12 "
           "among them.",
           "Unix with X11 (source)", (("unix", ("sparc", "x86")), ("linux", ("x86", "x64"))),
           ((ARCHIVE, "NetBSD's source archive", "https://cdn.netbsd.org/pub/pkgsrc/distfiles/xscrabble-1.0.tar.gz",
             "`xscrabble-1.0.tar.gz`, the 1.0 source."),
            (ARCHIVE, "FreeBSD's source archive",
             "http://distcache.freebsd.org/ports-distfiles/xscrabble-2.12.tar.bz2",
             "`xscrabble-2.12.tar.bz2`, the 2.12 source.")),
           version="1.0 and 2.12", games=("gridwords",),
           ratings=[Rating(UNSUPPORTED, plat, archs, "1.0 and 2.12",
                           "XScrabble is one program that opens a window on each player's X display. It has no "
                           "network code and no game protocol of its own, only the X window system's, so there is "
                           "nothing a parlor could answer.",
                           "2026-11-22")
                    for plat, archs in (("unix", ("sparc", "x86")), ("linux", ("x86", "x64")))]),
    Client("blackcat-rummytile", "RummyTile (Black Cat Systems)", "period", "1999",
           "Chris Smolinski's RummyTile 1.0.1 came out on 24 July 1999 as freeware from Black Cat Systems.",
           "Mac OS", (("mac", ()),),
           ((AUTHOR, "Black Cat Systems", "https://www.blackcatsystems.com/software/rummy.html",
             "RummyTile's page, which still lists networked play as coming soon. Freeware."),
            (ARCHIVE, "Discmaster (Inside Mac Games CD, volume 7 number 5)", DISCMASTER + "view/12008/"
             "IMG%20Vol%207-5.iso/Shareware/RummyTile%20%C6%92/RummyTileDoc/RummyTileDoc.rsrc/TEXT_128.txt",
             "Its documentation, from the CD's RummyTile folder.")),
           version="1.0.1", games=("rummytile",),
           ratings=(Rating(UNSUPPORTED, "mac", (), "1.0.1",
                           "RummyTile plays up to four players at one Mac and was never networked. Its documentation "
                           "says \"We also hope to add network support for multiplayer games\", and its page still "
                           "lists \"Multi-player / networked play\" as coming soon. With no network play, there is "
                           "nothing for a parlor to connect to.", "2026-11-21"),)),
]

ALL = PERIOD + BROWSERS + TELNETS          # table order: the native clients first; web and telnet are fallbacks
EVERY = ALL + PLANNED + UNSUPPORTED_CLIENTS     # every client with a page
BY_ID = {c.id: c for c in EVERY}
# The old setup pages of the period clients, which now point to their pages.
OLD_SETUP_PAGES = {SETUP_DIR + c.id + ".html": c for c in PERIOD}


def clients_for(gtype, plat="", arch=""):
    """A game's clients, in table order: its native (period) clients, then browsers, then telnet programs."""
    return [c for c in ALL if c.plays(gtype) and c.runs_on(plat, arch)]


def native_for(gtype, plat="", arch=""):
    """A game's native (period) clients that run there: the rows of its own table."""
    return [c for c in PERIOD if c.plays(gtype) and c.runs_on(plat, arch)]


def generic_for(plat="", arch=""):
    """The browsers, then the telnet programs, that run there: they play every game, so they are listed once."""
    return [c for c in BROWSERS + TELNETS if c.runs_on(plat, arch)]


def filter_values(q):
    """The filter from a query dict (lists of values): only known values count; anything else is Any."""
    def one(name, known):
        v = (q.get(name) or [""])[0]
        return v if v in known else ""
    return one("plat", PLATFORM_NAMES), one("arch", ARCH_NAMES)


def runs_text(c):
    """Platforms and processors in words: 'Mac OS: 68000, 68020+; Windows: x86'."""
    return "; ".join("%s: %s" % (PLATFORM_NAMES[p], ", ".join(ARCH_NAMES[x] for x in archs)) for p, archs in c.runs)


# ---- the summary grid ---------------------------------------------------------------------------------
# Example machines, each tied to the filter's values: (label, platform, processor). The grid at the top of
# the Clients page has a row each; a machine nothing runs on yet still has its row when it is a common
# one (the Commodore 64, the Apple II), so the gap shows.
MACHINES = [
    ("Mac Plus (68000, System 6-7)", "mac", "68000"),
    ("Mac Quadra 800 (68040, System 7.1-8.1)", "mac", "68020"),
    ("Power Mac G3 (Mac OS 9)", "mac", "ppc"),
    ("IBM PC/AT 286, MS-DOS 4.01", "dos", "x86"),
    ("386 to Pentium, Windows 3.1 or 95", "win", "x86"),
    ("Commodore 64", "c64", "6502"),
    ("Apple IIe or IIgs", "apple2", "6502"),
    ("Amiga 500", "amiga", "68000"),
    ("Atari ST", "atari", "68000"),
    ("Unix workstation with X11 (SPARC)", "unix", "sparc"),
    ("Modern computer (x86-64)", "", "x64"),
]
# Two-letter column heads for the grid, by game type (a game not here takes its first two letters).
ABBR = {"reversi": "Rv", "backgammon": "Bg", "gridwords": "GW", "cannonfire": "CF", "dotsandboxes": "DB",
        "fiveinarow": "5R", "mancala": "Mn", "checkers": "Ck", "navalcombat": "NC", "rummytile": "RT"}
SYMBOLS = (("N", "native client"), ("T", "telnet only"), ("W", "web only"), ("-", "nothing yet"))
GRID_MEDALS = ("A medal beside N is the rating of a native client on that machine, one for each rated client; "
               "the client&#39;s page explains it.")


def abbr(gtype):
    return ABBR.get(gtype) or gtype[:2].capitalize()


def best(gtype, plat, arch):
    """The grid's one symbol for a game on a machine, native over telnet over web, and where it links: the
    native client's page when there is exactly one, the machine's filtered view at the game for several
    or none, and at Any Game (Generic) Clients for T and W."""
    found = clients_for(gtype, plat, arch)
    native = [c for c in found if c.kind == "period"]
    view = "%s%s#%s" % (FRONTENDS_PAGE, filter_query(plat, arch), gtype)
    generic = "%s%s#%s" % (FRONTENDS_PAGE, filter_query(plat, arch), GENERIC)
    if native:
        return "N", native[0].href() if len(native) == 1 else view
    if any(c.kind == "telnet" for c in found):
        return "T", generic
    if any(c.kind == "browser" for c in found):
        return "W", generic
    return "-", view


def grid_medals(gtype, plat, arch):
    """The medals of a game's native clients on a machine, in table order: one for each rated client."""
    out = []
    for c in native_for(gtype, plat, arch):
        rs = c.medals(gtype, plat, arch)
        if rs:
            out.append(rs[0])
    return out


def summary_grid(games):
    """Machines down, games across, one linked symbol per cell (N also on light felt; the letter carries the
    meaning), each machine's name linking to its filtered view; a key under it."""
    head = '<th align="left">Machine</th>' + "".join("<th>%s</th>" % a("#" + t, abbr(t)) for _, t in games)
    rows = []
    for label, plat, arch in MACHINES:
        cells = []
        for _, gtype in games:
            sym, href = best(gtype, plat, arch)
            icons = "".join(medal_img(r.medal) for r in grid_medals(gtype, plat, arch)) if sym == "N" else ""
            cells.append("<td%s>%s%s</td>" % (' bgcolor="%s"' % SAGE if sym == "N" else "", a(href, sym), icons))
        rows.append('<tr align="center"><td align="left">%s</td>%s</tr>\n'
                    % (a(FRONTENDS_PAGE + filter_query(plat, arch), esc(label)), "".join(cells)))
    key = "; ".join("<b>%s</b> %s" % sym for sym in SYMBOLS) + ". " + ", ".join(
        "%s %s" % (abbr(t), esc(n)) for n, t in games) + ". " + GRID_MEDALS
    return ('<table border="0" cellpadding="2" cellspacing="0">\n<tr bgcolor="%s">%s</tr>\n%s</table>\n'
            % (IVORY, head, "".join(rows)) + p(key, 2))


# ---- by machine: the game server's welcome page (adapters/web.py, machines_html) ----------------------------
# The machines a visitor may come with, under the names people know them by, so they find theirs without
# knowing its processor: (anchor, name, a short note, ((platform, processor), ...)). A processor of "" is any.
# Every client listed for a machine comes from the client data above, with its games and its medals there.
PLAY_MACHINES = [
    ("mac-plus", "Macintosh Plus, SE, Classic", "68000 Macs", (("mac", "68000"),)),
    ("mac-ii", "Macintosh II, LC, Quadra", "68020, 68030 and 68040 Macs", (("mac", "68020"),)),
    ("power-mac", "Power Macintosh", "PowerPC Macs", (("mac", "ppc"),)),
    ("apple-ii", "Apple IIe, IIgs", "", (("apple2", "6502"),)),
    ("c64", "Commodore 64, 128", "", (("c64", "6502"),)),
    ("ibm-pc", "IBM PC with MS-DOS", "and compatibles", (("dos", ""),)),
    ("windows", "Windows PC", "", (("win", ""),)),
    ("amiga", "Amiga", "", (("amiga", ""),)),
    ("atari-st", "Atari ST", "ST, STe, TT, Falcon", (("atari", ""),)),
    ("unix", "Unix and Linux", "workstations and PCs", (("unix", ""), ("linux", ""))),
]
MEDAL_ORDER = (GOLD, SILVER, BRONZE, NOT_RATED)


def best_medal(c, gtype, targets):
    """A client's best medal for a game on any of a machine's (platform, processor), or NOT_RATED."""
    found = {r.medal for plat, arch in targets for r in c.medals(gtype, plat, arch)}
    return next(m for m in MEDAL_ORDER if m in found or m == NOT_RATED)


def machine_clients(targets):
    """A machine's native clients, best first: [(client, [(medal, [game types]), ...])], each client's games
    grouped by its best medal there, the best group first, games in GAME_NAMES order. A client with a better
    first medal comes first, then one with more games under it. A client marked untested is left out: nobody
    has played it on a parlor yet, and its page on the docs site says so."""
    out = []
    for c in PERIOD:
        if c.status == "untested" or not any(c.runs_on(plat, arch) for plat, arch in targets):
            continue
        groups = {}
        for g in GAME_NAMES:
            if g in c.games:
                groups.setdefault(best_medal(c, g, targets), []).append(g)
        out.append((c, [(m, groups[m]) for m in MEDAL_ORDER if m in groups]))
    out.sort(key=lambda e: (MEDAL_ORDER.index(e[1][0][0]), -len(e[1][0][1])))
    return out


def machine_generic(targets):
    """The browsers and the telnet programs that run on a machine, each in table order: (browsers, telnets)."""
    found = []
    for plat, arch in targets:
        found += [c for c in generic_for(plat, arch) if c not in found]
    return [c for c in found if c.kind == "browser"], [c for c in found if c.kind == "telnet"]


def games_text(gtypes):
    """Game types in words: 'Reversi, Checkers', or 'every game'."""
    if set(gtypes) >= set(GAME_NAMES):
        return "every game"
    return ", ".join(GAME_NAMES.get(g, g) for g in gtypes)


# ---- the tables ---------------------------------------------------------------------------------------

HEAD = ["Client", "Year", "OS", "Protocol", "Setup"]


def client_rows(clients, gtype=""):
    """A table's rows; a native client's cell has its medals for the game (gtype) under its name."""
    rows = []
    for c in clients:
        marks = c.marks(gtype)
        rows.append([("<b>%s</b>" if c.bold else "%s") % c.label() + ("<br>" + marks if marks else "") +
                     ("<br>" + original_html(c.original(), True) if c.original() else ""),
                     esc(c.year), esc(c.os), key_link(c.key()), a(c.href(), "setup")])
    return rows


GENERIC = "generic"                                 # the anchor of the browsers and telnet programs, listed once
GENERIC_HEAD = "Any Game (Generic) Clients"
WEB_OR_TELNET = "Play by web or telnet"
NO_NATIVE = "No native client yet."
NONE_HERE = "No clients for this platform yet."
NO_NATIVE_HERE = "No native client for this platform yet."


def web_or_telnet():
    """The link from a game's native clients to the browsers and telnet programs."""
    return p(a("%s#%s" % (FRONTENDS_PAGE, GENERIC), WEB_OR_TELNET) +
             ": any browser or telnet program listed under %s plays this game too." % GENERIC_HEAD, 2)


def game_table(gtype):
    """A game's Client | Year | OS | Protocol | Setup table of its native clients (a game with none says so
    instead), then the link to the browsers and telnet programs, which are listed once."""
    rows = client_rows(native_for(gtype), gtype)
    listed = plain_table(HEAD, rows) if rows else p("<b>%s</b>" % NO_NATIVE, 2)
    return listed + web_or_telnet()


def generic_section():
    """Any Game (Generic) Clients: every browser, then every telnet program, once (they play every game),
    with their old anchors (web, telnet)."""
    return (h2(GENERIC, GENERIC_HEAD) +
            p('<a name="web"></a><a name="telnet"></a>Every browser and telnet program here plays every game. '
              "What they need is on the %s and %s pages.%s" % (
                  a(BROWSERS_PAGE, "Browsers"), a(TELNET_PAGE, "Telnet programs"),
                  "" if any(c.medals() for c in BROWSERS + TELNETS) else
                  " None of them has been rated yet, since a rating needs a whole game played with the program "
                  "itself; each one&#39;s page says what has been checked so far."), 2) +
            plain_table(HEAD, client_rows(generic_for())))


def filter_query(plat, arch):
    q = "&".join("%s=%s" % kv for kv in (("plat", plat), ("arch", arch)) if kv[1])
    return "?" + q if q else ""


def frontends_body(games, finder=""):
    """The Clients page (index.html): native clients are the intent, web and telnet the fallback. The summary
    grid, the finder (finder: the docs site's script, which writes a filter box and narrows the tables in
    place; without it every row shows), the games, then one Client | Year | OS | Protocol | Setup table of
    native clients per game (anchor: the game type), each followed by a link to Any Game (Generic) Clients;
    then PARLORNET/1 (native), and at the bottom the browsers and telnet programs, once (generic; old anchors web
    and telnet). games: [(name, type)] in build order. Old anchors period, frontends and clients are on the
    list of games."""
    out = [h1("Clients"),
           p("ParlorNet speaks the protocols of old game servers, so the original game programs can play on it. "
             "Its native clients are game programs first released about 25 to 50 years ago, which for the 2027 "
             "release means 1977 to 2000, and new native-client work goes to them. Each program connects with its own "
             "protocol, described on the %s page. If your computer has no such program, you can play in a web "
             "browser or a telnet program." % a(PROTOCOLS_PAGE, "Protocols")),
           p(originals_html(), 2) if ORIGINALS else "",
           p("Each game&#39;s table lists its native clients with the year of their first qualifying version and "
             "the systems they run on. Browsers and telnet programs play every game, so they are listed once, "
             "under %s at the bottom. Setup opens the program&#39;s own page, with its history, where to get it, "
             "how to set it up and sign in, its rating and its known quirks. ParlorNet links to the authors&#39; "
             "pages and to public archives and serves none of these files." % a("#" + GENERIC, GENERIC_HEAD), 2),
           medals_legend(),
           summary_grid(games),
           finder,
           p('<a name="period"></a><a name="frontends"></a><a name="clients"></a>Games: %s.'
             % " | ".join(a("#" + t, esc(n)) for n, t in games), 2)]
    for name, gtype in games:
        out.append(h2(gtype, esc(name)) + game_table(gtype))
    out.append(h2("native", "Your own program") +
               p("Programs written for ParlorNet speak its own protocol, %s, on port 6501. The %s covers "
                 "signing in, tables and every game, with sample sessions; the %s is the reference."
                 % (key_link("PARLORNET/1"), a("/docs/pnp.html", "protocol guide"),
                    a("/docs/protocol.html", "protocol"))))
    out.append(planned_section())
    out.append(unsupported_section())
    out.append(generic_section())
    return "".join(out)


def unsupported_section():
    """Not supported: the programs ParlorNet examined and ruled out, each with the no sign and the reason in
    short."""
    if not UNSUPPORTED_CLIENTS:
        return ""
    return (h2("unsupported", "Not supported") +
            p("ParlorNet examined these programs and ruled them out. Each has a page that says why.", 2) +
            ul("%s %s, %s: %s" % (medal_img(UNSUPPORTED), a(c.href(), "<b>%s</b>" % esc(c.name)), esc(c.os),
                                  text(c.ratings[0].note))
               for c in UNSUPPORTED_CLIENTS))


def planned_section():
    """Planned: the period clients ParlorNet does not serve yet, each linking to its page."""
    if not PLANNED:
        return ""
    return (h2("planned", "Planned") +
            p("These programs have pages but no server extension yet, so they cannot play here today.", 2) +
            ul("%s (%s): %s" % (a(c.href(), "<b>%s</b>" % esc(c.name)), esc(c.os), text(c.status_note))
               for c in PLANNED))


# ---- client pages and the shared pages ---------------------------------------------------------------

def getting_it(c, host):
    """The Getting it list: each source with its kind, the link and what the file is."""
    items = []
    for kind, source, url, note in c.get:
        where = a(url, esc(source)) if url else esc(source)
        items.append("<b>%s:</b> %s. %s" % (esc(kind[0].upper() + kind[1:]), where, text(note, host)))
    return items


# What a mailed temporary password asks of a client that cannot prompt: its first use is on the web or by
# telnet, where the new password is chosen.
FIRST_ELSEWHERE = ("A password the server emailed you (a new account's, or one asked for with `reset`) is used "
                   "once on the web or by telnet, where you choose your own; then sign in here with that.")
EMAILED = ("If the server emails passwords, the first sign-in with the emailed one asks you to choose your own "
           "password, typed twice.")

SIGNIN = {
    "web": {
        "none": "Sign in: type your name and press Sign in; there is no password.",
        "password": "Sign in: your name and your password. " + EMAILED,
        "new": "Choose New account in the navigation bar and type the name you want. Depending on the server, "
               "that is all, or the next pages ask for a password typed twice, or for an email address: the "
               "server emails you a password, and signing in with it asks you to choose your own.",
    },
    "telnet": {
        "none": "At `Login`, type your name; nothing else is asked.",
        "password": "At `Login`, your name, then your password (not shown as you type). " + EMAILED,
        "new": "Type `new` at `Login`, then the name you want. Depending on the server, that is all, or the "
               "prompts ask for a password typed twice, or for an email address: the server emails you a "
               "password, and signing in with it asks you to choose your own.",
    },
    "ios": {
        "none": "Your name as the login name; the password is not asked for and not sent.",
        "password": "Your name as the login name and your ParlorNet password as the password. With a password "
                    "the server emailed you, the client's window then asks for a password of your own, typed "
                    "twice.",
        "new": "A free name signs up in the client's own window: the server's questions appear there as "
               "prompts (`Sign up as tia? (y/n): `, a password, or `Email address: `) and what you type answers "
               "them. Or make the account on the web site (New account) or by telnet (`new`) first.",
    },
    "doom": {
        "none": "`connect name x`: your name; the password is not used.",
        "password": "`connect name password`, with your ParlorNet password. With a password the server emailed "
                    "you, the prompts then ask for a password of your own, typed twice.",
        "new": "The `connect` line only signs in. To make an account, type `request name` with the name you "
               "want and answer the prompts, or make it on the web site (New account) or by telnet (`new`).",
    },
    "fibs": {
        "none": "Your name; the password is not used.",
        "password": "Your name and your ParlorNet password. With a password the server emailed you, the prompts "
                    "then ask for a password of your own, typed twice.",
        "new": "As on FIBS, the login only signs in. To make an account, log in as `guest` (the name `guest`, "
               "no password), type `name` and the name you want, and answer the prompts. Or make it on the web "
               "site (New account) or by telnet (`new`).",
    },
    "gamemaster": {
        "none": "In the ParlorNet rulebook's window, type your name and click Sign in; nothing else is asked.",
        "password": "In the ParlorNet rulebook's window, type your name and click Sign in, then type your "
                    "password when the parlor asks and click OK. With a password the server emailed you, the "
                    "window then asks for a password of your own, typed twice.",
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. Sign out, "
               "or closing the ParlorNet window, ends the sign-in.",
    },
    "networdz": {
        "none": "Choose Options, Chat in Networdz and type `/signin name`; nothing else is asked.",
        "password": "Choose Options, Chat in Networdz, type `/signin name password` and press Enter. " +
                    FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. `/quit` "
               "signs out.",
    },
    "awale": {
        "none": "Your name in Internet > Network setup (Your name) signs you in when you connect; if it is not a "
                "ParlorNet name, type `!signin name` in Write to group.",
        "password": "After connecting, type `!signin name password` in Write to group and press Send. " +
                    FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. `!quit` "
               "signs out.",
    },
    "netsprocket": {
        "none": "In the game's Join dialog, your name as the player name; the password is not used.",
        "password": "In the game's Join dialog, your name as the player name and your ParlorNet password as the "
                    "password. " + FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. A wrong "
               "name or password: NetBoxes shows the reason in its chat window, then \"The game host has left!\"",
    },
    "batnav": {
        "none": "Your ParlorNet name as the client's player name (`-u name`, or Your name in Config); nothing else "
                "is asked.",
        "password": "Send the message `/signin name password` (in nbtclient, `\\signin name password`, since its "
                    "message field takes the slash key as a backspace). " + FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. The name "
               "the client sends when it connects signs nobody in on a password server.",
    },
    "rscards": {
        "none": "In graphics mode, type your name in the first field and press RETURN; nothing else is asked.",
        "password": "In graphics mode, type your name in the first field, then your ParlorNet password in the "
                    "second (it does not show). With a password the server emailed you, the next fields ask for "
                    "a password of your own, typed twice.",
        "new": "Type a new name in the first field: the server asks whether to make the account and for its "
               "password in the fields that follow.",
    },
    "netluff": {
        "none": "In the chat box, type `/signin`: the name in NetLuff's name box signs in; nothing else is asked.",
        "password": "In the chat box, type `/signin password` to sign in with the name in NetLuff's name box, or "
                    "`/signin name password`. " + FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. `/quit` "
               "signs out.",
    },
    "icds": {
        "none": "In the terminal you started xcheckers from, your name at `login:`; the password is not used.",
        "password": "In the terminal you started xcheckers from, your name at `login:` and your ParlorNet password "
                    "at `password:`. With a password the server emailed you, the prompts then ask for a password of "
                    "your own, typed twice.",
        "new": "The login only signs in. To make an account, type `guest` at `login:`, then the name you want, "
               "and answer the prompts. Or make it on the web site (New account) or by telnet (`new`).",
    },
    "worthy": {
        "none": "Open Talk (F6) and type `/signin`: the name from Worthy Opponent's Name menu signs in; nothing "
                "else is asked.",
        "password": "Open Talk (F6) and type `/signin password` to sign in with the name from Worthy Opponent's "
                    "Name menu, or `/signin name password`. " + FIRST_ELSEWHERE,
        "new": "Make your account on the web site (New account) or by telnet (log in as `new`) first. `/quit` "
               "signs out and hangs up.",
    },
    "parlornet": {
        "none": "At the sign-in screen, type your name; nothing else is asked.",
        "password": "At the sign-in screen, type your name, then your password when the parlor asks for it. With a "
                    "password the server emailed you, the program then asks for a password of your own, typed "
                    "twice.",
        "new": "Type `new` at the sign-in screen, then the name you want, and answer the parlor's questions: a "
               "password typed twice, or an email address, depending on the server.",
    },
    "outland": {
        "none": "Your name as the account name; the password is not used.",
        "password": "Your name as the account name and your ParlorNet password as the password. " +
                    FIRST_ELSEWHERE,
        "new": "The Gateway only signs in, and its own account forms are not used. Make your account on the web "
               "site (New account) or by telnet (log in as `new`) first. For a name with no account the Gateway "
               "says where to make one, then hangs up.",
    },
}


def signin_html(style, auth=None, host=HOST):
    """How to sign in under each method a server may run, then the new account. auth: a server's sign-in
    (method, label, sign-up), whose method is marked; None on the docs site, which serves every server."""
    method = auth[0] if auth else None
    items = []
    for m, label in SIGNIN_METHODS:
        here = " (this server)" if m == method else ""
        items.append("<b>%s%s:</b> %s" % (label, here, text(SIGNIN[style][m], host)))
    which = "" if auth else p("Each server uses one of these methods. Its Sign in page shows which one.", 2)
    return (which + ul(items) + p("<b>New account:</b> " + text(SIGNIN[style]["new"], host)) +
            p("%s covers signing up and signing in under each method." %
              a(PROTOCOLS_DOC + "#signing-up-and-signing-in", "Supported protocols"), 2))


def facts(c, host):
    lines = [("Runs on", esc(c.os)), ("Year", "%s. %s" % (esc(c.year), text(c.year_note, host)))]
    if c.version:
        lines.append(("Version examined" if c.unsupported() else "Version to use", esc(c.version)))
    if c.kind == "telnet":
        lines.append(("Sends arrow keys", esc(c.has.get("arrows", "not known")) +
                      ("" if c.has.get("arrows") == "yes" else "; if they do not move, choose %s"
                       % a(TELNET_PAGE + "#keys", "movement keys"))))
        lines.append(("Color", esc(c.has.get("colour", "not known"))))
        lines.append(("Line characters", esc(c.has.get("glyphs", "not known"))))
    return p("<br>\n".join("<b>%s:</b> %s" % kv for kv in lines))


def programs(anchor, title, clients):
    """The programs of a shared page, one line each linking to its own page, under its old anchor (#mill)."""
    return h2(anchor, title) + ul('<a name="%s"></a>%s: %s%s' % (
        c.id, a(c.href(), "<b>%s</b>" % esc(c.name)), esc(c.os),
        ", version %s" % esc(c.version) if c.version else "") for c in clients)


def contents(clients):
    return p(" | ".join(a(c.href(), esc(c.name)) for c in clients), 2)


HOST_NOTE = ("In these steps, %s means the name of the ParlorNet server you play on. The ports shown are the "
             "usual ones." % HOST)


def browsers_body(host=HOST, auth=None):
    """browsers.html: what a browser needs, setting one up, signing in, then each browser."""
    return (h1("Browsers") +
            p("Every game can be played in a web browser, and the browsers below have what the site needs. Each "
              "has its own page with its history, setup notes and known quirks, and the %s lists them all."
              % a(FRONTENDS_PAGE, "Clients page")) +
            p(HOST_NOTE, 2) +
            contents(BROWSERS) +
            h2("needs", "What a browser needs") +
            ul(["<b>Tables</b> (HTML 3.2), for the boards and the pages.",
                "<b>Forms with menus</b> (SELECT), for signing in, the lobby, moves and the filters.",
                "<b>Image maps</b>, server-side (ISMAP) or client-side (USEMAP), since a board is one picture you "
                "click on.",
                "Cookies, cell colors and JavaScript help but are optional."]) +
            h2("setup", "Setting up") +
            ul([text("Open `http://{host}:6500/`. It goes to the welcome page; bookmark the lobby once you are "
                     "in.", host),
                "Turn images on, since the boards are GIF pictures. With images off, a board&#39;s squares are "
                "listed as links under it.",
                "JavaScript is optional. Without it a table page reloads itself while you wait, at the rate you "
                "pick on the page (off, 3 to 60 seconds; 5 is the default).",
                "Cookies keep you signed in until you sign out or quit the browser. A browser that does not "
                "return cookies gets the session in each link (<tt>s=</tt>): do not pass those links on."]) +
            h2("signin", "Signing in") + signin_html("web", auth, host) +
            programs("programs", "The browsers", BROWSERS))


def telnet_body(host=HOST, auth=None):
    """telnet.html: what a telnet program needs, setting it up, signing in, then each program."""
    return (h1("Telnet programs") +
            p("Every game can be played by telnet, on a full-screen menu and board. Each program below has its "
              "own page with its history, setup notes and known quirks, and the %s lists them all."
              % a(FRONTENDS_PAGE, "Clients page")) +
            p(HOST_NOTE, 2) +
            contents(TELNETS) +
            h2("needs", "What a telnet program needs") +
            ul(["<b>Full screen</b> needs VT100 or ANSI cursor positioning and an 80 by 24 window.",
                "<b>Plain mode</b> needs neither. A program without cursor positioning plays line by line, with "
                "typed moves.",
                "Arrow keys, ANSI color and IBM PC (CP437) or UTF-8 line characters are optional."]) +
            h2("setup", "Setting up") +
            ul([text("Connect to `{host}`, port `6502` (`telnet {host} 6502`).", host),
                "Set the terminal type to ANSI or VT100 (the program reports it; xterm-class terminals get UTF-8 "
                "and the mouse). A menu then shows a sample line in each display, <tt>pc</tt> (color and CP437 "
                "characters), <tt>utf8</tt>, <tt>ansi</tt> (color, ASCII only) and <tt>plain</tt>. Pick the one "
                "that looks right.",
                "Make the window 80 columns by 24 rows. A smaller window is asked to grow.",
                "Backspace and Delete both erase, arrow keys move (or letters, below), and Ctrl-L redraws the "
                "screen.",
                "A terminal program on a serial line, with no network of its own, reaches the same port through "
                "a WiFi modem or a login on a Unix machine (%s)." % a("/docs/serial.html", "Serial connections")]) +
            h2("keys", "Movement keys") +
            p("If your telnet program sends no arrow keys, letters can move the cursor instead: IJKL, ESDF, WASD "
              "or HJKL (as in vi). Choose them with <tt>O</tt> in the menu, or with <tt>K</tt> at the login&#39;s "
              "last prompt (offered when your terminal type suggests no arrow keys). The arrows still work. Those "
              "four letters then always move; to type a command or word that starts with one of them, press "
              "<tt>:</tt> first (<tt>:join 3</tt>), and a key whose letter is one of them is <tt>:</tt> then the "
              "letter (<tt>:d</tt>). The choice lasts until you log off. Each program&#39;s page says whether "
              "it sends arrow keys.") +
            h2("signin", "Signing in") + signin_html("telnet", auth, host) +
            programs("programs", "The programs", TELNETS))


PROTOCOL_KEYS = {"parlornet": "PARLORNET/1"}       # a protocol whose key is not its name in capitals
GUIDE_ANCHORS = {"parlornet": "native-protocol"}    # a protocol whose part of the guide is not anchored by its name
PROTOCOL_NAMES = {"parlornet": "ParlorNet", "ios": "IOS", "fibs": "FIBS", "outland": "Outland", "doom": "DOoM", "qlink": "Q-Link", "gamemaster": "GameMaster",
                  "netsprocket": "NetSprocket", "batnav": "batnav",
                  "awale": "IRC", "netluff": "NetLuff", "icds": "ICDS", "worthy": "Worthy Opponent",
                  "networdz": "Networdz", "rscards": "RSCARDS"}
GAME_NAMES = {"reversi": "Reversi", "backgammon": "Backgammon", "gridwords": "Grid Words",
              "dotsandboxes": "Dots and Boxes", "navalcombat": "Naval Combat", "checkers": "Checkers",
              "mancala": "Mancala", "fiveinarow": "Five in a Row", "rummytile": "Rummy Tile",
              "cannonfire": "Cannon Fire"}
