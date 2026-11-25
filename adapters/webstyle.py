"""The one page frame and the HTML 3.2 helpers of the web site and the docs site (docs/site.md).

Every page of the web extension, and of the docs site (tools/build_docs.py), is a masthead (the emblem, the ParlorNet name, a one-line tagline and the
navigation bar), then the page's own content, then a footer with the server's name and a link to the
message of the day. No CSS: the look comes from FONT FACE, table cell colors and one small GIF, the
emblem. Each color and face list is named once, here.

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

# ---- the palette: web-safe colors (the 216 that 256-color screens show without dithering) ----
PAPER = "#FFFFFF"   # the page
INK = "#000000"     # body text
FELT = "#006633"    # card-table green: the emblem's column and the callout frame; never behind text
DEEP = "#003300"    # dark felt: the name and the headings
SAGE = "#99CC99"    # light felt: the navigation bar, the footer and table header rows (dark text on it)
IVORY = "#FFFFCC"   # the masthead plate, callouts and every other row of a data table
LINK, VLINK, ALINK = "#003399", "#663366", "#CC0000"

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

# The game server's navigation bar: (path, label). The page being shown is in bold instead of a link. Docs is
# the docs site, another site, last (left out when the operator cleared site.docs_url). A signed-in player's
# bar adds Sign out after it (adapters/web.py, site_nav); sign-in and sign-up are on the welcome page.
PUBLIC_NAV = [("/welcome.htm", "Welcome"), ("/lobby.htm", "Lobby"), ("/games.htm", "Games")]


def public_nav():
    return PUBLIC_NAV + ([(DOCS[0], "Docs")] if DOCS[0] else [])


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
    """html in a face (and size and color): FONT must be repeated in every table cell."""
    return '<font face="%s"%s%s>%s</font>' % (face, ' size="%s"' % size if size else "",
                                               ' color="%s"' % color if color else "", html)


def p(html, size=None):
    return "<p>%s</p>\n" % font(html, size)


def small(html):
    return font(html, 2)


def h1(html):
    return "<h1>%s</h1>\n" % font(html, 5, HEAD_FACE, DEEP)


def _heading(level, size, anchor, html, aliases):
    old = "".join('<a name="%s"></a>' % n for n in aliases)
    text = font(html, size, HEAD_FACE, DEEP)
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
            "</td></tr></table>\n</td></tr></table>\n" % (width, FELT, IVORY, html))


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
    body = ['<tr bgcolor="%s" valign="top">%s</tr>\n' % (SAGE, cells("th", head))]
    for n, r in enumerate(rows):
        body.append('<tr%s valign="top">%s</tr>\n' % (' bgcolor="%s"' % IVORY if n % 2 else "", cells("td", r)))
    return ('<table%s border="0" cellpadding="4" cellspacing="0">\n%s</table>\n'
            % (' width="%s"' % width if width else "", "".join(body)))


def plain_table(head, rows):
    """A lean table for long lists (the frontends tables): the same header row and alternating rows as
    data_table, but in the browser's own text face, as the docs' tables are, with no FONT per cell, so a
    row costs little more than its text. The header cells are TH (bold in every browser)."""
    body = ['<table border="0" cellpadding="3" cellspacing="0">\n<tr bgcolor="%s">%s</tr>\n'
            % (SAGE, "".join('<th align="left">%s</th>' % h for h in head))]
    for n, r in enumerate(rows):
        body.append("<tr%s>%s</tr>\n" % (' bgcolor="%s"' % IVORY if n % 2 else "",
                                         "".join("<td>%s</td>" % c for c in r)))
    return "".join(body) + "</table>\n"


def nav_html(items, current=None):
    """The navigation bar's links: items are (href, label); the current page's label is in bold."""
    return " | ".join("<b>%s</b>" % esc(label) if href == current else a(href, esc(label)) for href, label in items)


def masthead(nav, compact=False):
    """The top of every page. Full: the emblem on felt beside an ivory plate with the name and tagline,
    the navigation bar under the plate. Compact (lobby and table pages): one bar with the name and the
    navigation."""
    if compact:
        return ('<table width="100%%" border="0" cellpadding="4" cellspacing="0"><tr>'
                '<td bgcolor="%s" width="%d">&nbsp;</td><td bgcolor="%s">%s &nbsp; %s</td></tr></table>\n'
                % (FELT, EMBLEM_SIZE, SAGE, font("<b>ParlorNet</b>", 3, HEAD_FACE, DEEP), font(nav, 2)))
    return ('<table width="100%%" border="0" cellpadding="6" cellspacing="0">\n'
            '<tr><td bgcolor="%s" rowspan="2" width="%d" valign="top">'
            '<img src="%s" width="%d" height="%d" alt=""></td>\n'
            '<td bgcolor="%s">%s<br>\n%s</td></tr>\n<tr><td bgcolor="%s">%s</td></tr>\n</table>\n'
            % (FELT, EMBLEM_SIZE, EMBLEM, EMBLEM_SIZE, EMBLEM_SIZE, IVORY, font("ParlorNet", 6, HEAD_FACE, DEEP),
               font(TAGLINE, 2), SAGE, font(nav, 2)))


def footer(html=None):
    """The footer bar: the server's name and the message of the day's link, or html (the docs site's)."""
    if html is not None:
        return ('<br>\n<table width="100%%" border="0" cellpadding="6" cellspacing="0"><tr>'
                '<td bgcolor="%s" width="%d">&nbsp;</td><td bgcolor="%s">%s</td></tr></table>\n'
                % (FELT, EMBLEM_SIZE, SAGE, font(html, 2)))
    return ('<br>\n<table width="100%%" border="0" cellpadding="6" cellspacing="0"><tr>'
            '<td bgcolor="%s" width="%d">&nbsp;</td><td bgcolor="%s">%s</td></tr></table>\n'
            % (FELT, EMBLEM_SIZE, SAGE,
               font("Server: <b>%s</b> | %sParlorNet is free software." % (
                   esc(LABEL[0]), a(MOTD_LINK, "Message of the day") + " | " if MOTD[0] else ""), 2)))


def frame(title, body, current=None, nav=None, compact=False, refresh=None, foot=None, head_extra=""):
    """A whole page: title, then the masthead, body and footer. current: the nav entry being shown;
    nav: the navigation bar's HTML (default: the public bar); refresh: (seconds, url) for a meta refresh;
    foot: the footer's own HTML (the docs site's); head_extra: more of the head (a script)."""
    head = "<title>%s</title>%s" % (esc(title), head_extra)
    if refresh:
        head += '\n<meta http-equiv="refresh" content="%d;URL=%s">' % (refresh[0], esc(refresh[1]))
    if nav is None:
        nav = nav_html(public_nav(), current)
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">\n<html>\n<head>\n%s\n</head>\n'
            '<body bgcolor="%s" text="%s" link="%s" vlink="%s" alink="%s">\n%s%s%s</body>\n</html>\n'
            % (head, PAPER, INK, LINK, VLINK, ALINK, masthead(nav, compact), body, footer(foot)))


def emblem_gif(make_gif):
    """The emblem: Reversi's opening four discs on a two-by-two felt board, 33 pixels square. Black discs
    have a light rim so they still show where a 1-bit screen dithers the felt nearly black."""
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
    return make_gif(n, n, [(0x00, 0x66, 0x33), (0, 0, 0), (255, 255, 255), (0x99, 0xCC, 0x99)], px)
