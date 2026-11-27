"""The one page frame and the HTML 3.2 helpers of the web site and the docs site (docs/site.md).

Every page of the web extension, and of the docs site (tools/build_docs.py), is a masthead (the emblem, the
site's name, a one-line tagline and the navigation bar), then the page's own content, then a footer with the
server's name and a link to the message of the day. No CSS: the look comes from FONT FACE, table cell colors
and one small GIF, the emblem. Each color and face list is named once, here.

The two sites share the frame but not the colors. The game server's pages are card-table green (GREEN), and the
docs site's pages are greyscale (GREY), so a reader can tell at a glance which site they are on when they move
between the two. The helpers read the palette in use (colors()), which is GREEN unless a build switches it
with using().

How the palette reads in each class of browser:
- Netscape 3+ and Internet Explorer 3+: everything, faces and cell colors included.
- Netscape 2 and Internet Explorer 2: page and font colors, but no cell colors, so no text sits in a
  dark cell with a light font color (it would be white on white there): text in a colored cell is
  always dark, on a light or mid-tone cell.
- Mill (68000 Macs, 1-bit screens): turns dark backgrounds into white text on black.
- Lynx, Mosaic and MacWeb: no color at all; black on white, tables without borders.
The content never sits inside one big table: Netscape shows a table only once all of it has arrived,
and the target modems run at 2400 to 14,400 bit/s.
"""
from collections import namedtuple
from contextlib import contextmanager

# ---- the palettes: web-safe colors (the 216 that 256-color screens show without dithering) ----
# paper: the page. ink: body text. edge: the emblem's column and the callout frame, never behind text.
# heading: the name and the headings. bar: the navigation bar, the footer and table header rows, with dark text
# on it. tint: the masthead plate, callouts and every other row of a data table. link, vlink, alink: links.
Palette = namedtuple("Palette", "paper ink edge heading bar tint link vlink alink")
GREEN = Palette(paper="#FFFFFF", ink="#000000",
                edge="#006633",         # card-table green
                heading="#003300",      # dark felt
                bar="#99CC99",          # light felt
                tint="#FFFFCC",         # ivory
                link="#003399", vlink="#663366", alink="#CC0000")
GREY = Palette(paper="#FFFFFF", ink="#000000", edge="#333333", heading="#000000", bar="#999999",
               tint="#CCCCCC", link="#000000", vlink="#333333", alink="#666666")
_IN_USE = [GREEN]


def colors():
    """The palette pages are being built in."""
    return _IN_USE[0]


@contextmanager
def using(palette):
    """Build pages in palette for a while (the docs site's build uses GREY)."""
    old = _IN_USE[0]
    _IN_USE[0] = palette
    try:
        yield palette
    finally:
        _IN_USE[0] = old


# ---- faces, with fallbacks: the Mac's own fonts first, then their Windows and Unix stand-ins ----
TEXT_FACE = "Geneva,Helvetica,Arial"
HEAD_FACE = "Chicago,Charcoal,Arial Black"

TAGLINE = "Turn-based game server for any computer, old or new"
EMBLEM = "/img/parlor.gif"
EMBLEM_SIZE = 33
LABEL = ["parlornet"]       # the server's name from the core's greeting; web.py keeps it current
MOTD_LINK = "/welcome.htm#motd"
MOTD = [False]              # whether the server has a message of the day (web.py keeps it current)

DOCS = ["http://docs.parlornet.org/"]     # site.docs_url, the docs site ("" when cleared; web.py keeps it current)

# The icon after every link from the game server's pages to the docs site, which shows that the link leaves the
# site: a box with an arrow out of its corner, drawn by external_gif(). Text browsers show its ALT text.
EXTERNAL = "/img/external.gif"
EXTERNAL_SIZE = 9
EXTERNAL_ALT = "(external)"
EXTERNAL_ROWS = [".....####",
                 ".......##",
                 "###...#.#",
                 "#....#..#",
                 "#...#....",
                 "#..#...#.",
                 "#......#.",
                 "#......#.",
                 "########."]

# The game server's navigation bar: (path, label). The page being shown is in bold instead of a link. Docs is
# the docs site, another site (left out when the operator cleared site.docs_url). Last comes Sign In, the
# welcome page's Sign-In section; a signed-in player's bar has Sign Out there instead (adapters/web.py,
# site_nav).
PUBLIC_NAV = [("/welcome.htm", "Welcome"), ("/lobby.htm", "Lobby")]
SIGN_IN = ("/welcome.htm#signin", "Sign In")


def public_nav():
    return PUBLIC_NAV + ([(DOCS[0], "Docs")] if DOCS[0] else []) + [SIGN_IN]


def external_img():
    """The external-link icon, with its size and ALT text."""
    return '<img src="%s" width="%d" height="%d" alt="%s">' % (EXTERNAL, EXTERNAL_SIZE, EXTERNAL_SIZE, EXTERNAL_ALT)


def away(html):
    """A link that leaves the site (html), with the external-link icon after it. The no-break space keeps the
    icon on the link's line."""
    return html + "&nbsp;" + external_img()


def external_gif():
    """The external-link icon: EXTERNAL_ROWS in the game server's link color, on a transparent background."""
    from .board import transparent_gif
    ink = tuple(int(GREEN.link[i:i + 2], 16) for i in (1, 3, 5))
    px = [1 if ch == "#" else 0 for row in EXTERNAL_ROWS for ch in row]
    return transparent_gif(EXTERNAL_SIZE, EXTERNAL_SIZE, [(255, 255, 255), ink], px)


def protocol_anchor(key):
    """The anchor of a protocol's entry in the docs site's protocols reference (protocols.html#IOS): its key,
    with any character but letters, digits and - as - (PARLORNET/1: PARLORNET-1). The docs site's clients tables
    link to it."""
    return "".join(c if c.isascii() and (c.isalnum() or c == "-") else "-" for c in key)


def esc(s):
    """HTML 3.2 safe: named entities for & < > ", decimal for the apostrophe."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def a(href, text):
    return '<a href="%s">%s</a>' % (esc(href), text)


def font(html, size=None, face=TEXT_FACE, color=None):
    """html in a face (and size and color): FONT must be repeated in every table cell. A color that is the
    palette's text color is left out, since the text already has it."""
    return '<font face="%s"%s%s>%s</font>' % (face, ' size="%s"' % size if size else "",
                                               ' color="%s"' % color if color and color != colors().ink else "",
                                               html)


def p(html, size=None):
    return "<p>%s</p>\n" % font(html, size)


def small(html):
    return font(html, 2)


def h1(html):
    return "<h1>%s</h1>\n" % font(html, 5, HEAD_FACE, colors().heading)


def _heading(level, size, anchor, html, aliases):
    old = "".join('<a name="%s"></a>' % n for n in aliases)
    text = font(html, size, HEAD_FACE, colors().heading)
    if anchor:
        text = '<a name="%s">%s</a>' % (anchor, text)
    return "<h%d>%s%s</h%d>\n" % (level, old, text, level)


def h2(anchor, html, aliases=()):
    """A section heading with its anchor; aliases: old anchor names, each an empty <a name> (anchors may
    not nest in HTML 3.2)."""
    return _heading(2, 4, anchor, html, aliases)


def h3(anchor, html, aliases=()):
    return _heading(3, 3, anchor, html, aliases)


def ul(items):
    return "<ul>\n%s</ul>\n" % "".join("<li>%s\n" % font(i) for i in items)


def callout(html, width="100%"):
    """A box: a thin felt frame round an ivory panel (in browsers without cell colors, just the text).
    html is block content (paragraphs, forms); give it its own fonts."""
    return ('<table width="%s" border="0" cellpadding="1" cellspacing="0"><tr><td bgcolor="%s">\n'
            '<table width="100%%" border="0" cellpadding="8" cellspacing="0"><tr><td bgcolor="%s">\n%s'
            "</td></tr></table>\n</td></tr></table>\n" % (width, colors().edge, colors().tint, html))


def data_table(head, rows, width=None, aligns=()):
    """A table of data: a header row on light felt, then rows alternating white and ivory, all in the text
    face at size 2. head: column titles; rows: lists of cell HTML; aligns: per column, None or "right" or
    "center"."""
    def cells(tag, items):
        out = []
        for i, c in enumerate(items):
            al = aligns[i] if i < len(aligns) else None
            out.append("<%s%s%s>%s</%s>" % (tag, ' align="%s"' % al if al else "",
                                            ' align="left"' if tag == "th" and not al else "", font(c, 2), tag))
        return "".join(out)
    c = colors()
    body = ['<tr bgcolor="%s" valign="top">%s</tr>\n' % (c.bar, cells("th", head))]
    for n, r in enumerate(rows):
        body.append('<tr%s valign="top">%s</tr>\n' % (' bgcolor="%s"' % c.tint if n % 2 else "", cells("td", r)))
    return ('<table%s border="0" cellpadding="4" cellspacing="0">\n%s</table>\n'
            % (' width="%s"' % width if width else "", "".join(body)))


def plain_table(head, rows):
    """A lean table for long lists (the frontends tables): the same header row and alternating rows as
    data_table, but in the browser's own text face, as the docs' tables are, with no FONT per cell, so a
    row costs little more than its text. The header cells are TH (bold in every browser)."""
    c = colors()
    body = ['<table border="0" cellpadding="3" cellspacing="0">\n<tr bgcolor="%s">%s</tr>\n'
            % (c.bar, "".join('<th align="left">%s</th>' % h for h in head))]
    for n, r in enumerate(rows):
        body.append("<tr%s>%s</tr>\n" % (' bgcolor="%s"' % c.tint if n % 2 else "",
                                         "".join("<td>%s</td>" % c for c in r)))
    return "".join(body) + "</table>\n"


def nav_html(items, current=None, away_hrefs=()):
    """The navigation bar's links: items are (href, label); the current page's label is in bold. A link to an
    address in away_hrefs leaves the site and has the external-link icon after it."""
    def item(href, label):
        if href == current:
            return "<b>%s</b>" % esc(label)
        return away(a(href, esc(label))) if href in away_hrefs else a(href, esc(label))
    return " | ".join(item(href, label) for href, label in items)


def masthead(nav, compact=False, name="ParlorNet"):
    """The top of every page. Full: the emblem on the edge color beside the tinted plate with the site's name
    and the tagline, the navigation bar under the plate. Compact (lobby and table pages): one bar with the name
    and the navigation."""
    c = colors()
    if compact:
        return ('<table width="100%%" border="0" cellpadding="4" cellspacing="0"><tr>'
                '<td bgcolor="%s" width="%d">&nbsp;</td><td bgcolor="%s">%s &nbsp; %s</td></tr></table>\n'
                % (c.edge, EMBLEM_SIZE, c.bar, font("<b>%s</b>" % esc(name), 3, HEAD_FACE, c.heading),
                   font(nav, 2)))
    return ('<table width="100%%" border="0" cellpadding="6" cellspacing="0">\n'
            '<tr><td bgcolor="%s" rowspan="2" width="%d" valign="top">'
            '<img src="%s" width="%d" height="%d" alt=""></td>\n'
            '<td bgcolor="%s">%s<br>\n%s</td></tr>\n<tr><td bgcolor="%s">%s</td></tr>\n</table>\n'
            % (c.edge, EMBLEM_SIZE, EMBLEM, EMBLEM_SIZE, EMBLEM_SIZE, c.tint,
               font(esc(name), 6, HEAD_FACE, c.heading), font(TAGLINE, 2), c.bar, font(nav, 2)))


def footer(html=None):
    """The footer bar: the server's name and the message of the day's link, or html (the docs site's)."""
    c = colors()
    if html is None:
        html = "Server: <b>%s</b> | %sParlorNet is free software." % (
            esc(LABEL[0]), a(MOTD_LINK, "Message of the day") + " | " if MOTD[0] else "")
    return ('<br>\n<table width="100%%" border="0" cellpadding="6" cellspacing="0"><tr>'
            '<td bgcolor="%s" width="%d">&nbsp;</td><td bgcolor="%s">%s</td></tr></table>\n'
            % (c.edge, EMBLEM_SIZE, c.bar, font(html, 2)))


def frame(title, body, current=None, nav=None, compact=False, refresh=None, foot=None, head_extra="",
          name="ParlorNet"):
    """A whole page: title, then the masthead, body and footer. current: the nav entry being shown;
    nav: the navigation bar's HTML (default: the public bar); refresh: (seconds, url) for a meta refresh;
    foot: the footer's own HTML (the docs site's); head_extra: more of the head (a script); name: the site's
    name in the masthead (the docs site's is ParlorNet Docs)."""
    head = "<title>%s</title>%s" % (esc(title), head_extra)
    if refresh:
        head += '\n<meta http-equiv="refresh" content="%d;URL=%s">' % (refresh[0], esc(refresh[1]))
    if nav is None:
        nav = nav_html(public_nav(), current, (DOCS[0],))
    c = colors()
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">\n<html>\n<head>\n%s\n</head>\n'
            '<body bgcolor="%s" text="%s" link="%s" vlink="%s" alink="%s">\n%s%s%s</body>\n</html>\n'
            % (head, c.paper, c.ink, c.link, c.vlink, c.alink, masthead(nav, compact, name), body, footer(foot)))


def emblem_gif(make_gif, palette=GREEN):
    """The emblem: Reversi's opening four discs on a two-by-two board in the palette's edge color, 33 pixels
    square. Black discs have a rim in its bar color so they still show where a 1-bit screen dithers the board
    nearly black."""
    n, cell = EMBLEM_SIZE, 16
    felt, black, white, rim = 0, 1, 2, 3
    px = []
    for y in range(n):
        for x in range(n):
            if x % cell == 0 or y % cell == 0:
                px.append(black)
                continue
            cx, cy = x // cell, y // cell
            dx, dy = x - (cx * cell + 8), y - (cy * cell + 8)
            d = dx * dx + dy * dy
            dark = (cx + cy) % 2 == 1
            if d <= 30:
                px.append(black if dark else white)
            elif d <= 44:
                px.append(rim if dark else black)
            else:
                px.append(felt)
    rgb = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in (palette.edge, palette.bar)]
    return make_gif(n, n, [rgb[0], (0, 0, 0), (255, 255, 255), rgb[1]], px)
