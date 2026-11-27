"""Each client's page on the docs site (clients/<id>.html), built from two sources.

adapters/clients.py holds the structured facts: what the tables, the summary grid, the finder and the tests
need, the download links and the groups still running (COMMUNITIES). docs/clients/<id>.md holds the prose, one
Markdown file per client, which anyone can edit on GitHub. It has a title line and five sections in this order:

    # Name
    ## History          who made it, when, its versions and what it was known for
    ## Getting it       anything to say beyond the links, which come from clients.py
    ## Setting it up    pointing it at a ParlorNet server; {host} stands for the server's name
    ## Known quirks     what a player runs into and what to do ("Nothing known yet." when nothing is)
    ## Screenshots      lines like ![What it shows](../../static/img/name.gif "Caption"), or "None yet."

Under its title the page has a row of links, one to each of its sections, made from the sections it shows. Then
come its ratings, one for each platform, processor (or build) and version, each with the explanation from
adapters/clients.py, then the facts, then the five sections, with the links under Getting it, the sign-in steps
after Setting it up, and the Community section (from COMMUNITIES) after Known quirks. It ends with a link to edit
its Markdown on GitHub. A screenshot is a GIF in static/img/, at most SHOT_WIDTH pixels wide and SHOT_BYTES
long, shown at its own size, and a page has SHOTS of them or none. Its ALT text says what it shows, and its
caption goes under it. The build copies each one it finds to img/, with the rating icons (tools/medal_icons.py).
"""
import os
import re

from adapters import clients as C
from adapters.mdhtml import to_html
from adapters.webstyle import a, esc, font, h1, h2, p, ul
from parlord import PROJECT_URL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.join(ROOT, "docs", "clients")
PAGES_REL = "docs/clients"
OUTLINE = [("history", "History"), ("get", "Getting it"), ("setup", "Setting it up"), ("quirks", "Known quirks"),
           ("screenshots", "Screenshots")]
NOTHING_KNOWN = "Nothing known yet."
SHOT_RE = re.compile(r'^!\[([^\]]+)\]\(([^)\s]+) "(.+)"\)$')
SHOT_WIDTH, SHOT_BYTES = 199, 21 * 1024    # under 200 pixels across, so a picture fits a small screen
SHOTS = (3, 4)                             # how many screenshots a page with any has
EDIT_URL = PROJECT_URL + "/edit/main/" + PAGES_REL + "/%s.md"
EDIT_TEXT = "Edit this page on GitHub"
OLD_ANCHORS = {"quirks": ("problems",)}      # a section's older anchor, kept beside its own


def page_ids():
    """The ids of the client pages in docs/clients/ (README.md is the note for contributors)."""
    return sorted(f[:-3] for f in os.listdir(PAGES_DIR) if f.endswith(".md") and f != "README.md")


def read(cid):
    """(title, [(heading, Markdown)]) of docs/clients/<cid>.md."""
    with open(os.path.join(PAGES_DIR, cid + ".md"), encoding="utf-8") as f:
        md = f.read()
    title, sections, cur = "", [], None
    for line in md.split("\n"):
        if line.startswith("# ") and not title and cur is None:
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = [line[3:].strip(), []]
            sections.append(cur)
        elif cur is not None:
            cur[1].append(line)
    return title, [(h, "\n".join(body).strip()) for h, body in sections]


def outline_problems(cid):
    """What is wrong with a page's outline (empty when it is right)."""
    title, sections = read(cid)
    out = []
    if not title:
        out.append("no title line")
    if [h for h, _ in sections] != [t for _, t in OUTLINE]:
        out.append("sections are %s, not %s" % ([h for h, _ in sections], [t for _, t in OUTLINE]))
    for h, body in sections:
        if not body:
            out.append("%s is empty" % h)
    return out


def section(cid, heading):
    return dict(read(cid)[1]).get(heading, "")


def screenshots(cid):
    """[(ALT text, caption, file under the repository, site path)] of a page's Screenshots section."""
    out = []
    for line in section(cid, "Screenshots").split("\n"):
        m = SHOT_RE.match(line.strip())
        if m:
            rel = os.path.normpath(os.path.join(PAGES_REL, m.group(2))).replace(os.sep, "/")
            out.append((m.group(1), m.group(3), rel, "/img/" + rel.rsplit("/", 1)[1]))
    return out


def gif_size(data):
    """(width, height) from a GIF's header."""
    return data[6] | data[7] << 8, data[8] | data[9] << 8


def read_file(rel):
    with open(os.path.join(ROOT, *rel.split("/")), "rb") as f:
        return f.read()


def icon_files():
    """{site path: bytes} of the rating icons, from static/img/."""
    return {src.lstrip("/"): read_file("static" + src) for _, src, _ in C.MEDALS.values()}


def files():
    """{site path: bytes} of every screenshot on the client pages, and the rating icons."""
    out = {site.lstrip("/"): read_file(rel) for cid in page_ids() for _, _, rel, site in screenshots(cid)}
    out.update(icon_files())
    return out


def md_html(md, link_fix, host):
    return to_html(md, link_fix, figure).replace("{host}", host) + "\n"


def shot_html(alt, caption, rel, site):
    """A picture at its own size, with its ALT text, and its caption under it."""
    w, h = gif_size(read_file(rel))
    return '<p><img src="%s" width="%d" height="%d" alt="[%s]"><br>\n%s</p>\n' % (
        site, w, h, esc(alt), font(esc(caption), 2))


def figure(caption, url):
    """A picture on a line of its own in the Markdown (adapters/mdhtml.py), such as a diagram: a GIF in
    static/img/, shown as a screenshot is, with its size, its caption as the ALT text and the caption under it.
    The site's build copies every picture its pages show into img/ (docsite/build.py, files)."""
    m = re.match(r"^(?:\.\./)*static/img/([a-z0-9-]+\.gif)$", url)
    if not m:
        raise ValueError("a picture in the docs is a GIF in static/img/: %s" % url)
    return shot_html(caption, caption, "static/img/" + m.group(1), "/img/" + m.group(1))


def community_html(c, host):
    """The Community section's text: each group still running for the client (C.COMMUNITIES), and the date."""
    out = [p("<b>%s</b>. %s" % (a(g.url, esc(g.name)), C.text(g.about, host))) for g in c.communities()]
    out.append(p("Checked running on %s." % C.COMMUNITIES_CHECKED, 2))
    return "".join(out)


def summary(c, host):
    """The line under the title: what kind of program it is, and its protocol and port."""
    if c.kind == "browser":
        return p("What a web browser needs, and how to sign in with one, are on the %s page."
                 % a(C.BROWSERS_PAGE, "Browsers"))
    if c.kind == "telnet":
        return p("What a telnet program needs, the movement keys and how to sign in are on the %s page."
                 % a(C.TELNET_PAGE, "Telnet programs"))
    games = ", ".join(a("%s#%s" % (C.FRONTENDS_PAGE, g), esc(C.GAME_NAMES.get(g, g))) for g in c.games)
    if c.unsupported():
        return p("A program for %s that ParlorNet examined and ruled out: it does not play on a parlor, for the "
                 "reason below." % games)
    name = C.PROTOCOL_NAMES[c.protocol]
    article = "An" if name[0] in "AEIOU" else "A"
    if c.planned():
        return p("%s %s client. <b>Status:</b> planned. %s ParlorNet does not serve it yet."
                 % (article, esc(name), C.text(c.status_note, host)))
    return p("%s %s client for %s, on port %d (%s)." % (
        article, esc(name), games, c.port,
        a("%s#%s" % (C.PROTOCOLS_PAGE, C.protocol_anchor(c.key())), "the %s protocol" % esc(name))))


def rating_items(c, host=C.HOST):
    """One line for each of its ratings (C.Client.all_ratings), ratings with the same word, version, reason and
    date joined: the icon, the word, where and which version, the explanation, and the test's date."""
    groups = []
    for r in c.all_ratings():
        key = (r.medal, r.version, r.note, r.date, r.games)
        if groups and groups[-1][0] == key:
            groups[-1][1].append(r)
        else:
            groups.append((key, [r]))
    items = []
    for (medal, version, note, date, games), rs in groups:
        where = " and ".join(esc(r.where()) for r in rs)
        what = where + (", version %s" % esc(version) if version else "")
        if games and set(games) != set(c.games):
            what += " (%s)" % esc(", ".join(C.GAME_NAMES.get(g, g) for g in games))
        when = " %s %s." % (C.TESTED_WORD.get(medal, "Tested"), esc(date)) if date else ""
        items.append("%s<b>%s</b>, %s: %s%s" % (C.medal_img(medal) + " " if medal else "", esc(rs[0].word()),
                                                 what, C.text(note, host), when))
    return items


def ratings_html(c, host):
    """The ratings near the top of the page, with a link to what the marks mean."""
    return (p('<a name="rating"></a><b>Rating</b> (%s):' % a(C.FRONTENDS_PAGE + "#medals", "what the marks mean"))
            + ul(rating_items(c, host)))


def serves(c):
    """A period client that plays on a parlor: its page has a Signing in section."""
    return c.kind == "period" and not c.planned() and not c.unsupported()


def signin_pointer(c):
    """For a browser or a telnet program, the line under Setting it up that points to its kind's sign-in."""
    page = {"browser": (C.BROWSERS_PAGE, "browser"), "telnet": (C.TELNET_PAGE, "telnet program")}.get(c.kind)
    if not page:
        return ""
    return p("Signing in is the same in every %s, as %s says." % (page[1], a(page[0] + "#signin", "Signing in")))


def sections(c, link_fix=lambda u: u, host=C.HOST):
    """[(anchor, heading, HTML)] of a client page's sections in order: History, Getting it (the links, then the
    page's text), Setting it up, Signing in, Known quirks, Community and Screenshots. Signing in comes only for a
    client that plays on a parlor, Community only with a group still running, Screenshots only with pictures."""
    text = dict(read(c.id)[1])
    out = [("history", "History", md_html(text["History"], link_fix, host)),
           ("get", "Getting it", ul(C.getting_it(c, host)) + md_html(text["Getting it"], link_fix, host)),
           ("setup", "Setting it up", md_html(text["Setting it up"], link_fix, host) + signin_pointer(c))]
    if serves(c):
        out.append(("signin", "Signing in", C.signin_html(c.style, None, host)))
    out.append(("quirks", "Known quirks", md_html(text["Known quirks"], link_fix, host)))
    if c.communities():
        out.append(("community", "Community", community_html(c, host)))
    shots = screenshots(c.id)
    if shots:
        out.append(("screenshots", "Screenshots", "".join(shot_html(*s) for s in shots)))
    return out


def contents(parts):
    """The row of links under a page's title, one to each of its sections' headings."""
    return p(" | ".join(a("#" + anchor, esc(heading)) for anchor, heading, _ in parts), 2)


def client_body(c, link_fix=lambda u: u, host=C.HOST):
    """clients/<id>.html: the title, a link to each section, the summary, the ratings and the facts, then the
    sections (sections()) and the links to the Clients page, the protocol's guide and the page's Markdown."""
    parts = sections(c, link_fix, host)
    out = [h1(c.label()),
           contents(parts),
           summary(c, host),
           ratings_html(c, host),
           "" if c.planned() or c.unsupported() else p(C.HOST_NOTE, 2),
           C.facts(c, host)]
    out += [h2(anchor, heading, OLD_ANCHORS.get(anchor, ())) + html for anchor, heading, html in parts]
    links = [a(C.FRONTENDS_PAGE, "All clients")]
    if serves(c):
        links.append(a(c.guide(), "The %s guide" % C.PROTOCOL_NAMES[c.protocol]))
    links.append(a(EDIT_URL % c.id, EDIT_TEXT))
    out.append(p(" | ".join(links), 2))
    return "".join(out)


def moved_body(c):
    """setup/<id>.html, the old address of a period client's setup: a pointer to its page."""
    return h1("Moved") + p("The setup of %s is now on its own page, %s." % (esc(c.name), a(c.href(), esc(c.name))))
