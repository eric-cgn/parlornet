"""Each client's page on the docs site (clients/<id>.html), built from two sources.

adapters/clients.py holds the structured facts: what the tables, the summary grid, the finder and the tests
need, the download links and the groups still running (COMMUNITIES). docs/clients/<id>.md holds the prose, one
Markdown file per client, which anyone can edit on GitHub. It has a title line and five sections in this order:

    # Name
    ## History          who made it, when, its versions and what it was known for
    ## Getting it       anything to say beyond the links, which come from clients.py
    ## Setting it up    pointing it at a ParlorNet server; {host} stands for the server's name
    ## Known quirks     what a player runs into and what to do ("Nothing known yet." when nothing is)
    ## Screenshots      lines like ![Caption](../../static/img/name.gif), or "None yet."

The page shows its ratings first, one for each platform, processor (or build) and version, each with the
explanation from adapters/clients.py, then the facts, then the five sections, with the links under Getting it,
the sign-in steps after Setting it up, and the Community section (from COMMUNITIES) after Known quirks. It ends with a link to edit
its Markdown on GitHub. A screenshot is a GIF in static/img/, at most SHOT_WIDTH pixels wide and SHOT_BYTES
long; the build copies each one it finds to img/, with the rating icons (tools/medal_icons.py).
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
SHOT_RE = re.compile(r"^!\[([^\]]+)\]\(([^)\s]+)\)$")
SHOT_WIDTH, SHOT_BYTES = 320, 21 * 1024
EDIT_URL = PROJECT_URL + "/edit/main/" + PAGES_REL + "/%s.md"
EDIT_TEXT = "Edit this page on GitHub"


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
    """[(caption, file under the repository, site path)] of a page's Screenshots section."""
    out = []
    for line in section(cid, "Screenshots").split("\n"):
        m = SHOT_RE.match(line.strip())
        if m:
            rel = os.path.normpath(os.path.join(PAGES_REL, m.group(2))).replace(os.sep, "/")
            out.append((m.group(1), rel, "/img/" + rel.rsplit("/", 1)[1]))
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
    out = {site.lstrip("/"): read_file(rel) for cid in page_ids() for _, rel, site in screenshots(cid)}
    out.update(icon_files())
    return out


def md_html(md, link_fix, host):
    return to_html(md, link_fix).replace("{host}", host) + "\n"


def shot_html(caption, rel, site):
    w, h = gif_size(read_file(rel))
    return '<p><img src="%s" width="%d" height="%d" alt="[%s]"><br>\n%s</p>\n' % (
        site, w, h, esc(caption), font(esc(caption), 2))


def community_html(c, host):
    groups = c.communities()
    if not groups:
        return ""
    out = [h2("community", "Community")]
    for g in groups:
        out.append(p("<b>%s</b>. %s" % (a(g.url, esc(g.name)), C.text(g.about, host))))
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


def signin(c, host, auth=None):
    if c.kind == "period" and not c.planned() and not c.unsupported():
        return h2("signin", "Signing in") + C.signin_html(c.style, auth, host)
    if c.kind == "browser":
        return p("Signing in is the same in every browser, as %s says." % a(C.BROWSERS_PAGE + "#signin",
                                                                             "Signing in"))
    if c.kind == "telnet":
        return p("Signing in is the same in every telnet program, as %s says." % a(C.TELNET_PAGE + "#signin",
                                                                                    "Signing in"))
    return ""


def client_body(c, link_fix=lambda u: u, host=C.HOST):
    """clients/<id>.html: the title and status, the ratings, the facts, then History, Getting it (the links, then the
    page's text), Setting it up, Signing in, Known quirks, Community and Screenshots, and the edit link."""
    title, sections = read(c.id)
    text = dict(sections)
    shots = screenshots(c.id)
    parts = [("history", "History"), ("get", "Getting it"), ("setup", "Setting it up")]
    if c.kind == "period" and not c.planned() and not c.unsupported():
        parts.append(("signin", "Signing in"))
    parts.append(("quirks", "Known quirks"))
    if c.communities():
        parts.append(("community", "Community"))
    if shots:
        parts.append(("screenshots", "Screenshots"))
    out = [h1(c.label()),
           summary(c, host),
           ratings_html(c, host),
           "" if c.planned() or c.unsupported() else p(C.HOST_NOTE, 2),
           C.facts(c, host),
           p(" | ".join(a("#" + n, t) for n, t in parts), 2),
           h2("history", "History") + md_html(text["History"], link_fix, host),
           h2("get", "Getting it") + ul(C.getting_it(c, host)) + md_html(text["Getting it"], link_fix, host),
           h2("setup", "Setting it up") + md_html(text["Setting it up"], link_fix, host),
           signin(c, host),
           h2("quirks", "Known quirks", ("problems",)) + md_html(text["Known quirks"], link_fix, host),
           community_html(c, host)]
    if shots:
        out.append(h2("screenshots", "Screenshots") + "".join(shot_html(*s) for s in shots))
    links = [a(C.FRONTENDS_PAGE, "All clients")]
    if c.kind == "period" and not c.planned() and not c.unsupported():
        links.append(a(C.PROTOCOLS_DOC + "#" + c.protocol, "The %s guide" % C.PROTOCOL_NAMES[c.protocol]))
    links.append(a(EDIT_URL % c.id, EDIT_TEXT))
    out.append(p(" | ".join(links), 2))
    return "".join(out)


def moved_body(c):
    """setup/<id>.html, the old address of a period client's setup: a pointer to its page."""
    return h1("Moved") + p("The setup of %s is now on its own page, %s." % (esc(c.name), a(c.href(), esc(c.name))))
