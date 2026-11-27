"""The game server's web site as a whole (docs/site.md): every page is HTML 3.2 in the one frame, fits its
byte budget, reads in order as plain text, and every old address still answers (the documentation's with a
redirect to the docs site, which tests/test_docs_site.py checks).

    python3 -m unittest discover -s tests -p "test_site.py"
"""
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser

from test_adapters import Browser, Stack, plain
from test_adapters import query_of  # noqa: E402

# HTML 3.2's elements and attributes, with the extensions the site allows: BGCOLOR on TR, TD and TH (and on
# TABLE, which only a game's own board markup uses), FONT FACE and COLOR. No STYLE, CLASS or ID anywhere.
COMMON_CELL = {"bgcolor", "align", "valign", "width", "height", "colspan", "rowspan", "nowrap"}
ALLOWED = {
    "html": set(), "head": set(), "title": set(), "meta": {"http-equiv", "content"},
    "script": {"language"},
    "body": {"bgcolor", "text", "link", "vlink", "alink"},
    "h1": {"align"}, "h2": {"align"}, "h3": {"align"}, "h4": {"align"}, "h5": {"align"}, "h6": {"align"},
    "p": {"align"}, "br": {"clear"}, "hr": {"size", "width", "noshade", "align"}, "center": set(),
    "pre": {"width"}, "dl": {"compact"}, "dt": set(), "dd": set(), "ul": {"type", "compact"},
    "ol": {"type", "start", "compact"}, "li": {"type", "value"},
    "a": {"href", "name"}, "img": {"src", "alt", "width", "height", "border", "align", "ismap", "usemap",
                                   "hspace", "vspace"},
    "map": {"name"}, "area": {"shape", "coords", "href", "alt", "nohref"},
    "table": {"width", "border", "cellpadding", "cellspacing", "align", "bgcolor"},
    "tr": {"bgcolor", "align", "valign"}, "td": COMMON_CELL, "th": COMMON_CELL,
    "font": {"size", "color", "face"},
    "b": set(), "i": set(), "u": set(), "tt": set(), "em": set(), "strong": set(), "code": set(),
    "small": set(), "big": set(), "sub": set(), "sup": set(),
    "form": {"action", "method"}, "input": {"type", "name", "value", "size", "maxlength", "checked"},
    "select": {"name", "size"}, "option": {"value", "selected"}, "textarea": {"name", "rows", "cols"},
}
FACES = {"Geneva,Helvetica,Arial", "Chicago,Charcoal,Arial Black"}
# The navigation bar, exactly: these entries in this order, with Sign In last, or Sign Out while a player is
# signed in.
PUBLIC_NAV = ["Welcome", "Lobby", "Docs", "Sign In"]
# The icon after a link to the docs site, which leaves the site.
EXTERNAL_IMG = '<img src="/img/external.gif" width="9" height="9" alt="(external)">'
SIGNED_NAV = ["Welcome", "Lobby", "Docs", "Sign Out"]

# Byte budgets: the target modems run at 2400 to 14,400 bit/s. (page, most bytes of HTML)
# welcome: the forms and the guide by machine, with each machine's picture; credits: the pictures' sources
BUDGETS = {"/welcome.htm": 24 * 1024, "/credits.htm": 6 * 1024}
GAME_PAGE_BUDGET = 8 * 1024         # a game's page
SMALL_BUDGET = 3 * 1024             # sign-in steps, notices, the resign question
LOBBY_BUDGET = (18 * 1024, 600)     # the lobby, with a form for each game: bytes, plus this for each table it lists
TABLE_FRAME_BUDGET = 3 * 1024       # a table page less the game's own parts
PAGE_BUDGETS = {"/activity.htm": 9 * 1024, "/player.htm": 6 * 1024}     # the activity, a profile

# The test server's message of the day: characters HTML must escape, an indented line, the contribute marker.
MOTD = ("Welcome to the <test> parlor: Ann & Bob's \"club\".\n"
        "   indented  by three\n"
        "Run your own server or contribute: {contribute}\n")

# Every address the site has published; each must answer 200 or a redirect. The documentation's (MOVED)
# redirect to the docs site, where the page and its anchors are (tests/test_docs_site.py).
OLD_URLS = ["/", "/index.htm", "/index.html", "/welcome.htm", "/login.htm", "/lobby.htm",
            "/table.htm?t=1", "/welcome.htm#clients"]
MOVED = ["/docs/index.htm", "/eclipse-68k-fix.htm", "/download/eclipse-68k-fix.hqx",
         "/download/eclipse-68k-fix.bin", "/download/eclipse-68k-fix.dsk", "/download/eclipse-68k-fix-readme.txt",
         "/img/eclipse-68k-fix.gif", "/docs/frontends.htm", "/docs/protocols.htm", "/docs/protocol.htm",
         "/docs/pnp.htm", "/docs/clients.htm", "/docs/accounts.htm", "/frontends.htm", "/frontends.htm#web",
         "/frontends.htm#telnet", "/frontends.htm#period", "/frontends.htm#reversi", "/frontends.htm#backgammon",
         "/frontends.htm#native", "/frontends.htm#frontends", "/frontends.htm#clients", "/browsers.htm",
         "/telnet.htm", "/setup/eclipse.htm", "/setup/macfibs.htm", "/setup/outland.htm", "/protocols.htm"]


class Checker(HTMLParser):
    """Collects every problem with a page's markup, and its text in reading order."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.problems, self.text, self.tags = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in ALLOWED:
            self.problems.append("element <%s>" % tag)
            return
        for name, value in attrs:
            if name not in ALLOWED[tag]:
                self.problems.append("<%s %s=>" % (tag, name))
            if tag == "font" and name == "face" and value not in FACES:
                self.problems.append("face %r" % value)

    def handle_data(self, data):
        self.text.append(data)

    def handle_entityref(self, name):
        if name not in ("amp", "lt", "gt", "quot", "nbsp"):
            self.problems.append("&%s;" % name)
        self.text.append(" ")

    def handle_charref(self, name):
        if not name.isdigit():
            self.problems.append("&#%s;" % name)
        self.text.append(" ")


def check(page):
    c = Checker()
    c.feed(page)
    c.close()
    return c


class Site(Stack):
    """One server with a message of the day, two players and a table of every game."""

    @classmethod
    def setUpClass(cls):
        cls.motd = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        cls.motd.write(MOTD)
        cls.motd.close()
        cls.core_extra = ("--motd", cls.motd.name)
        super().setUpClass()
        cls.ann, cls.bob = Browser(cls.web_port), Browser(cls.web_port)
        cls.ann.login("ann", "ann-Pass-word")
        cls.bob.login("bob", "bob-Pass-word")
        lobby = cls.ann.get("/lobby.htm")[3]
        cls.keep = cls.ann.keep()
        cls.tables = {}
        for key in re.findall(r'<input type="hidden" name="g" value="([^"]+)">', lobby):
            st, loc, _, _ = cls.act("/new.htm?g=%s&turn=1h" % key.replace("/", "%2F"))
            tid = query_of(loc)["t"][0]
            cls.bob.act("/join.htm?t=%s" % tid)
            cls.tables[key] = tid

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.unlink(cls.motd.name)

    @classmethod
    def act(cls, path):
        """An action from ann's browser, with her session's x=."""
        return cls.ann.get(path + ("&" if "?" in path else "?") + cls.keep)

    def public_pages(self):
        from adapters import web as W
        paths = ["/welcome.htm", "/credits.htm"] + ["/games/%s.htm" % r["type"] for r in W.game_rows()]
        out = {}
        for path in paths:
            st, _, ct, body = self.get(path)
            self.assertEqual((st, ct), (200, "text/html"), path)
            out[path] = body
        return out

    def signed_in_pages(self):
        out = {"/lobby.htm": self.ann.get("/lobby.htm")[3]}
        for key, tid in self.tables.items():
            out["/table.htm %s" % key] = self.ann.get("/table.htm?t=%s&r=0" % tid)[3]
        tid = self.tables["reversi/1"]
        out["/resign.htm"] = self.act("/resign.htm?t=%s" % tid)[3]
        out["/password.htm"] = self.act("/password.htm")[3]
        out["/activity.htm"] = self.ann.get("/activity.htm")[3]
        out["/player.htm"] = self.ann.get("/player.htm?who=bob")[3]
        out["/profile.htm"] = self.act("/profile.htm")[3]
        out["error"] = self.act("/join.htm?t=99999")[3]
        out["out of date"] = self.ann.get("/join.htm?t=1")[3]
        return out

    def other_pages(self):
        out = {}
        st, _, _, out["sign-up step"] = self.browser().post("/signup.htm", {"n": "newcomer"})
        out["sign-in refused"] = self.browser().post("/login.htm", {"n": "ann", "k": "wrong-password"})[3]
        out["no such account"] = self.browser().post("/login.htm", {"n": "nobody", "k": "new-Pass-word"})[3]
        out["sign-in page"] = self.get("/login.htm")[3]
        out["new account page"] = self.get("/signup.htm")[3]
        out["session ended"] = self.get("/lobby.htm?s=AAAAAAAAAAAAAAAAAAAAAA")[3]
        return out

    def test_every_page_is_html32_in_the_frame(self):
        pages = {**self.public_pages(), **self.signed_in_pages(), **self.other_pages()}
        self.assertGreater(len(pages), 25)
        for name, page in pages.items():
            c = check(page)
            self.assertEqual(c.problems, [], name)
            low = page.lower()
            for bad in ("<style", "&#x", "<div", "<span"):    # style=, class= and id= the checker refuses
                self.assertNotIn(bad, low, name)
            if "<script" in low:      # only the table page's optional refresh, and never needed
                self.assertTrue(name.startswith("/table.htm"), name)
            self.assertTrue(all(ord(ch) < 128 for ch in page), name)
            self.assertTrue(page.startswith('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">'), name)
            if not name.startswith("/table.htm"):     # a game's board may color a table; the frame never does
                self.assertFalse([t for t, a in c.tags if t == "table" and "bgcolor" in a], name)
            # the frame: the masthead's name first, the footer's server name last
            self.assertRegex(page[:1200], r">(?:<b>)?ParlorNet(?:</b>)?</font>", name)
            self.assertIn("Server: <b>parlornet</b>", page[-700:], name)
            self.assertIn('<a href="/welcome.htm#motd">Message of the day</a>', page[-700:], name)

    @staticmethod
    def nav_labels(page):
        """The navigation bar's entries, in order: a link's text, or the current page's in bold."""
        text = plain(page)
        top = text[:text.index("</table>")]
        nav = top[top.rindex("<td"):] if "</td></tr>\n<tr><td" in top else top[top.rindex("&nbsp; "):]
        return re.findall(r">([^<>|]+)</(?:a|b)>", nav)

    def test_nav_on_every_page(self):
        """Exactly Welcome, Lobby, Docs and Sign In on every page, Docs leading to the docs site and Sign In to the
        welcome page's Sign-In section; Sign Out in its place while a player is signed in, on the signed-in pages
        and on the public ones alike."""
        from parlord import DOCS_URL
        for name, page in {**self.public_pages(), **self.other_pages()}.items():
            self.assertEqual(self.nav_labels(page), PUBLIC_NAV, name)
            self.assertIn('<a href="%s">Docs</a>&nbsp;%s' % (DOCS_URL, EXTERNAL_IMG), page[:page.index("</table>")],
                          name)                                     # Docs leaves the site: the icon after it
            self.assertIn('<a href="/welcome.htm#signin">Sign In</a></font></td></tr>', page[:page.index("</table>")
                                                                                              + 200], name)
        for name, page in self.signed_in_pages().items():
            if name in ("error", "out of date", "/password.htm"):    # notices and sign-in steps: the public bar
                continue
            self.assertEqual(self.nav_labels(page), SIGNED_NAV, name)
            nav = plain(page)[:plain(page).index("</table>")]
            self.assertIn('href="/lobby.htm?x=', nav)                # the signed-in links carry the action check
            self.assertRegex(nav, r'href="/out\.htm\?x=[0-9a-f]+[^"]*">Sign Out</a></td></tr>$')
        for path in ("/welcome.htm", "/credits.htm", "/games/reversi.htm"):     # public pages, signed in
            page = self.ann.get(path)[3]
            self.assertEqual(self.nav_labels(page), SIGNED_NAV, path)
            self.assertEqual(self.nav_labels(self.get(path)[3]), PUBLIC_NAV, path)   # nobody signed in

    def test_reading_order(self):
        """With the tags stripped, as Lynx reads a page: the name and the navigation, then the page's own
        heading before anything else, its section headings in order, and the footer last."""
        def text(html):
            return re.sub(r"\s+", " ", "".join(check(html).text)).strip()
        pages = {**self.public_pages(), **self.signed_in_pages(), **self.other_pages()}
        for name, page in pages.items():
            top = page.index("</table>")
            bottom = page.rindex("<br>\n<table")
            masthead, content, footer = text(page[page.index("<body"):top]), text(page[top:bottom]), text(page[bottom:])
            self.assertTrue(masthead.startswith("ParlorNet"), name)
            self.assertTrue(masthead.endswith(("Sign In", "Sign Out")), (name, masthead))
            crumbs = re.match(r"</table>\n<p>(.*?)</p>\n", page[top:], re.S)      # the breadcrumb line first
            if crumbs:
                content = content[len(text(crumbs.group(1))):].strip()
            h1 = text(re.search(r"<h1>(.*?)</h1>", page, re.S).group(1))
            self.assertTrue(content.startswith(h1), (name, content[:80]))
            at = len(h1)
            for h in re.findall(r"<h2>(.*?)</h2>", page, re.S):
                at = content.index(text(h), at)
            self.assertTrue(footer.startswith("Server:"), name)

    @staticmethod
    def crumbs(page):
        """The breadcrumb line under the masthead: [(href, label)], href None for the page itself; None
        without one."""
        page = plain(page)
        m = re.match(r"<p>(.*?)</p>\n", page[page.index("</table>\n") + len("</table>\n"):], re.S)
        if not m:
            return None
        return [(a.group(1).replace("&amp;", "&"), a.group(2)) if a else (None, step)
                for step in m.group(1).split(" &gt; ") for a in [re.match(r'<a href="([^"]*)">(.*)</a>\Z', step)]]

    def test_breadcrumbs(self):
        """Every page but the welcome page has the breadcrumb line under the navigation bar: the pages above it,
        each a link, then its own name."""
        pages = {**self.public_pages(), **self.signed_in_pages(), **self.other_pages()}
        for name, page in pages.items():
            steps = self.crumbs(page)
            if name == "/welcome.htm":
                self.assertIsNone(steps)
                continue
            self.assertTrue(steps and len(steps) >= 2, name)
            self.assertEqual([h is None for h, _ in steps], [False] * (len(steps) - 1) + [True], name)
            self.assertIn(steps[0][1], ("Welcome", "Lobby"), name)
            self.assertTrue(steps[0][0].startswith(("/welcome.htm", "/lobby.htm")), name)
        self.assertEqual(self.crumbs(pages["/games/reversi.htm"]),
                         [("/welcome.htm", "Welcome"), ("/welcome.htm#games", "Games"), (None, "Reversi")])
        tid = self.tables["reversi/1"]
        steps = self.crumbs(pages["/table.htm reversi/1"])
        self.assertEqual(steps[1:], [(None, "Table %s" % tid)])
        self.assertEqual(steps[0][1], "Lobby")
        self.assertRegex(steps[0][0], r"^/lobby\.htm\?x=[0-9a-f]+")
        self.assertEqual([l for _, l in self.crumbs(self.ann.get("/player.htm")[3])], ["Lobby", "Your profile"])
        self.assertEqual([l for _, l in self.crumbs(pages["/player.htm"])], ["Lobby", "bob"])
        self.assertEqual([l for _, l in self.crumbs(pages["/lobby.htm"])], ["Welcome", "Lobby"])
        self.assertEqual([l for _, l in self.crumbs(pages["/resign.htm"])], ["Lobby", "Table %s" % tid, "Resign"])
        self.assertEqual(self.crumbs(pages["/credits.htm"])[1:],
                         [("/welcome.htm#machines", "Play from your Computer"), (None, "Picture credits")])
        self.assertEqual(self.crumbs(pages["sign-in page"]), [("/welcome.htm", "Welcome"), (None, "Sign in")])

    def test_links_work_in_a_browser_that_sends_them_as_written(self):
        """Mosaic 2.0.1 sends a link's &amp; as it is written. The site's links separate their fields with ;,
        so they hold no &amp;, and following one as written works."""
        page = self.ann.get("/lobby.htm")[3]
        hrefs = re.findall(r'href="(/[^"]+)"', page)
        self.assertTrue([h for h in hrefs if ";" in h])
        self.assertFalse([h for h in hrefs if "&" in h])
        tid = self.tables["reversi/1"]
        watch = [h for h in re.findall(r'href="(/table\.htm[^"]+)"', self.bob.get("/lobby.htm")[3])
                 if "t=%s;" % tid in h + ";"][0]
        st, _, _, body = self.bob.get(watch)
        self.assertEqual(st, 200)
        self.assertIn("table %s" % tid, body)
        st, _, _, body = self.bob.get(watch.replace(";", "&"))        # & works as well
        self.assertIn("table %s" % tid, body)

    def test_byte_budgets(self):
        for path, page in self.public_pages().items():
            size = len(page.encode("latin-1"))
            self.assertLessEqual(size, BUDGETS.get(path, GAME_PAGE_BUDGET), path)
        for name, page in self.signed_in_pages().items():
            if name == "/lobby.htm":
                rows = len(self.tables)
                self.assertLessEqual(len(page), LOBBY_BUDGET[0] + LOBBY_BUDGET[1] * rows)
            elif not name.startswith("/table.htm"):
                self.assertLessEqual(len(page), PAGE_BUDGETS.get(name, SMALL_BUDGET), name)
        for name, page in self.other_pages().items():       # sign-in and new-account pages and their steps
            self.assertLessEqual(len(page), SMALL_BUDGET, name)
        # a table page: the frame and the page's own text, less the game's board and panels (the game module
        # draws those: one board image per page, which the browser keeps)
        for key, tid in self.tables.items():
            page = self.ann.get("/table.htm?t=%s&r=0" % tid)[3]
            start = page.index('<table border="0" cellpadding="8"><tr valign="top"><td>')
            end = page.rindex("</td></tr></table>", 0, page.rindex("<br>\n<table"))
            self.assertLessEqual(len(page) - (end - start), TABLE_FRAME_BUDGET, key)
            self.assertNotIn('src="/img/parlor.gif"', page, key)    # the compact frame has no emblem

    def test_old_urls_answer(self):
        from adapters import web as W
        urls = OLD_URLS + list(W.IMAGES)[:5]
        for u in urls:
            st, loc, _, _ = self.get(u.split("#")[0])
            self.assertIn(st, (200, 302), u)
        from parlord import DOCS_URL
        for u in MOVED:                               # the documentation: on the docs site
            st, loc, _, body = self.get(u.split("#")[0])
            self.assertEqual(st, 301, u)
            self.assertEqual(loc, DOCS_URL + W.moved_to(u.split("#")[0]), u)
            self.assertEqual(check(body).problems, [], u)
        welcome = self.get("/welcome.htm")[3]
        for r in W.game_rows():                       # a link to a game's row in the Games section lands on it
            self.assertIn('<a name="%s"></a>' % r["type"], welcome)
        for anchor in ("welcome", "games", "play", "frontends", "clients", "protocols", "ways-in", "motd",
                       "machines", "signin", "new"):
            self.assertIn('<a name="%s">' % anchor, welcome)
        self.assertNotIn("<h1>Games</h1>", plain(self.get("/games.htm")[3]))      # the old Games page is gone
        self.assertNotIn("/games.htm", welcome)

    def test_game_pages_link_the_docs(self):
        """A game's page links to its type spec, its protocol guide chapter and its programs on the docs site
        (tests/test_docs_site.py checks they resolve there), and to no list of protocols here."""
        from adapters import web as W
        from parlord import DOCS_URL
        for r in W.game_rows():
            page = plain(self.get("/games/%s.htm" % r["type"])[3])
            part = page[page.index('<a name="frontends">'):]
            self.assertIn('<a href="%sindex.html#%s">Clients page</a>&nbsp;%s' % (DOCS_URL, r["type"], EXTERNAL_IMG),
                          part)
            facts = page[page.index("<b>For programmers:</b>"):]
            self.assertEqual(facts[:facts.index("</p>")].count(EXTERNAL_IMG), 2)    # type spec, protocol guide
            self.assertNotIn("protocols.htm", page)

    def test_welcome_is_the_motd_then_the_two_ways_in(self):
        """The welcome text is the message of the day, in <pre>, escaped, with its address a link; then Sign
        in and New account, each its own form with its own heading and button."""
        from parlord import PROJECT_URL
        page = self.get("/welcome.htm")[3]
        pre = re.search(r"<pre>(.*?)</pre>", page, re.S).group(1)
        self.assertEqual(pre, "Welcome to the &lt;test&gt; parlor: Ann &amp; Bob&#39;s &quot;club&quot;.\n"
                              "   indented  by three\n"
                              'Run your own server or contribute: <a href="%s">%s</a>' % (PROJECT_URL, PROJECT_URL))
        self.assertNotIn("{contribute}", page)
        box = page[page.index('<a name="motd">'):page.index("</pre>")]                # a yellow box, a green header
        self.assertRegex(box, r'<td bgcolor="#99CC99"><font [^>]*color="#003300"><b>Message of the Day</b></font></td>')
        self.assertIn('<td bgcolor="#FFFFCC"><pre>', box)
        self.assertLess(box.index("Message of the Day"), box.index("<pre>"))
        body = plain(page)
        self.assertLess(body.index("<h1>Welcome</h1>"), body.index("<pre>"))
        self.assertLess(body.index("</pre>"), body.index('<a name="signin">'))
        self.assertNotIn("ParlorNet is a game server for turn-based games", page)     # no prose of its own
        signin, new = body.index('<a name="signin">Sign-In</a></h2>'), body.index('<a name="new">New account</a></h3>')
        self.assertLess(signin, new)
        games = body.index('<a name="games">Games</a></h2>')
        self.assertLess(new, games)                                                       # then the games
        self.assertLess(games, body.index('<a name="machines">Play from your Computer</a></h2>'))   # then by machine
        # the contents line under the heading links each section
        self.assertIn('<h1>Welcome</h1>\n<p><a href="#welcome">Welcome</a> | <a href="#signin">Sign-In</a> | '
                      '<a href="#games">Games</a> | <a href="#machines">Play from your Computer</a></p>', body)
        self.assertTrue(body[body.index("<body"):].split("</table>\n", 1)[1].startswith(
            '<a name="welcome"></a><h1>Welcome</h1>'))
        forms = re.findall(r'<form action="([^"]+)" method="post">(.*?)</form>', page, re.S)
        self.assertEqual([f for f, _ in forms], ["/login.htm", "/signup.htm"])
        self.assertIn('value="Sign in"', forms[0][1])
        self.assertIn('type="password" name="k"', forms[0][1])
        self.assertIn('value="Make the account"', forms[1][1])
        self.assertNotIn('name="k"', forms[1][1])                           # the sign-up steps ask for it
        self.assertIn('<a href="%s">' % PROJECT_URL, page)                 # run your own server

    def test_welcome_by_machine(self):
        """Play from your computer, after Sign in and New account: the machines' names first, each a jump to its
        row, then a row per machine of adapters/clients.py (PLAY_MACHINES) in order, then Any old computer and
        Any new computer. Each row names its clients as machine_clients gives them, best first, each linking to
        its page on the docs site, with its medal; then a browser and a telnet program. No ports, no protocols."""
        from adapters import clients as C, siteimages as PIC, web as W
        from parlord import DOCS_URL
        page = plain(self.get("/welcome.htm")[3])
        sec = page[page.index('<a name="machines">'):page.rindex("<br>\n<table")]
        machines = [(m[0], m[1]) for m in C.PLAY_MACHINES] + [W.ANY_OLD[:2], W.ANY_NEW[:2]]
        jumps = re.findall(r'<a href="#([a-z0-9-]+)">([^<]+)</a>', sec)
        self.assertEqual(jumps, [(anchor, C.esc(name)) for anchor, name in machines])
        rows = re.findall(r'<tr[^>]*><td align="center">(<img [^>]+>)</td><td><a name="([a-z0-9-]+)"></a>(.+?) '
                          r'((?:\([^)]*\))?<br>.*?)</td></tr>', sec, re.S)
        self.assertEqual([(r[1], r[2]) for r in rows], [(anchor, C.esc(name)) for anchor, name in machines])
        for img, anchor, _, _ in rows:          # each row's picture first, with its size and ALT text
            self.assertEqual(img, PIC.machine_img(anchor))
        cells = {r[1]: r[3] for r in rows}
        for anchor, _, _, targets in C.PLAY_MACHINES:
            cell, at = cells[anchor], 0
            for c, groups in C.machine_clients(targets):         # best first, each linking to its page there
                link = '<a href="%sclients/%s.html">%s</a>' % (DOCS_URL, c.id, C.esc(c.name))
                at = cell.index(link, at) if groups[0][0] else cell.index(link)
                if groups[0][0]:
                    self.assertIn(C.medal_img(groups[0][0]) + " " + link, cell)
            browsers, telnets = C.machine_generic(targets)
            for found in (browsers, telnets):
                if found:
                    self.assertIn('<a href="%sclients/%s.html">' % (DOCS_URL, found[0].id), cell, anchor)
        for anchor in ("apple-ii", "c64"):                       # ParlorNet's own 8-bit programs, Gold there
            self.assertIn(C.medal_img(C.GOLD) + ' <a href="%sclients/parlornet-8bit.html">' % DOCS_URL, cells[anchor])
        self.assertIn('<a href="%stelnet.html">' % DOCS_URL, cells["any-old"])
        self.assertIn('<a href="%sbrowsers.html">' % DOCS_URL, cells["any-old"])
        self.assertIn('<a href="%sdocs/serial.html">' % DOCS_URL, cells["any-old"])
        self.assertIn('<a href="%stelnet.html">' % DOCS_URL, cells["any-new"])
        # dozens of links to the docs site: the line above them says so once, with the icon, and they have none
        self.assertIn('The links in it go to <a href="%sindex.html">ParlorNet Docs</a>&nbsp;%s, where each '
                      'program&#39;s page says how to get it and set it up.' % (DOCS_URL, EXTERNAL_IMG), sec)
        self.assertNotIn(EXTERNAL_IMG, "".join(cells.values()))
        # nothing technical: no ports, no protocol keys, no list of protocols
        for word in ("port", "protocol", "PARLORNET/1", "HTTP", "protocols.htm", str(self.tel_port),
                     str(self.web_port), str(self.core)):
            self.assertNotIn(word, re.sub(r"<[^>]+>", "", page), word)
        self.assertNotIn("protocols.htm", page)

    def test_sign_out_from_the_bar(self):
        """Sign Out, last in the bar while signed in, ends this session and lands on Welcome, whose bar then
        has Sign In; the welcome page of a signed-in player has the lobby's link in place of the forms."""
        b = Browser(self.web_port)
        b.login("cyd", "cyd-Pass-word")
        page = b.get("/welcome.htm")[3]
        self.assertEqual(self.nav_labels(page), SIGNED_NAV)
        self.assertNotIn('<form action="/login.htm"', page)
        self.assertIn("You are signed in.", page)
        st, _, _, page = b.get(b.link(r"/out\.htm\?x=", page[:page.index("</table>")]))
        self.assertEqual(st, 200)
        self.assertIn("Signed out.", page)
        self.assertIn("<h1>Welcome</h1>", plain(page))
        self.assertEqual(self.nav_labels(page), PUBLIC_NAV)
        self.assertNotIn("pn", b.jar)
        self.assertEqual(self.nav_labels(b.get("/welcome.htm")[3]), PUBLIC_NAV)
        self.assertIn("Sign in to play.", b.get("/lobby.htm?a=1")[3])

    def test_the_same_motd_on_the_web_telnet_and_the_protocol(self):
        import html
        import socket
        from test_adapters import Client
        page = self.get("/welcome.htm")[3]
        web = html.unescape(re.sub(r"<[^>]+>", "", re.search(r"<pre>(.*?)</pre>", page, re.S).group(1))).split("\n")
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        f = s.makefile("rb")
        f.readline()
        s.sendall(b"MOTD\r\n")
        native = []
        while True:
            l = f.readline().decode("ascii").rstrip("\r\n")
            if l == "OK":
                break
            native.append(l[5:])
        s.close()
        c = Client(self.tel_port)
        out = c.until("Login (")
        c.close()
        self.assertEqual(web, native)
        self.assertEqual(len(web), 3)
        self.assertIn("\n" + "\n".join(native) + "\n", out.replace("\r\n", "\n"))    # telnet: line for line

    def test_sign_in_never_becomes_a_new_account(self):
        b = self.browser()
        st, _, _, page = b.post("/login.htm", {"n": "stranger", "k": "stranger-pass"})
        self.assertEqual(st, 200)
        self.assertIn("<h1>Sign in</h1>", plain(page))
        self.assertIn("There is no account named stranger here.", page)
        self.assertIn('<a href="/signup.htm">New account</a>', page)
        self.assertNotIn('name="lid"', page)
        self.assertNotIn("pn", b.jar)
        st, _, _, page = b.post("/signup.htm", {"n": "ann"})               # a taken name: no sign-in from here
        self.assertIn("The name ann is taken: choose another, or", page)
        self.assertNotIn('name="lid"', page)
        st, _, _, page = b.post("/signup.htm", {"n": "stranger"})           # New account: the sign-up steps
        self.assertIn("<h1>New account</h1>", plain(page))
        self.assertIn("New account stranger: choose a password.", page)
        self.assertIn('name="f" value="password"', page)

    def test_emblem(self):
        st, _, ct, body = self.get("/img/parlor.gif")
        self.assertEqual((st, ct), (200, "image/gif"))
        self.assertTrue(body.startswith("GIF89a"))
        self.assertLess(len(body), 1024)
        self.assertEqual((ord(body[6]), ord(body[8])), (33, 33))
        welcome = self.get("/welcome.htm")[3]
        self.assertEqual(welcome.count('<img src="/img/parlor.gif" width="33" height="33" alt="">'), 1)
        for src in set(re.findall(r'<img src="([^"]+)"', welcome)) - {"/img/parlor.gif"}:    # the rating marks
            self.assertRegex(src, r"^/img/(medal-(gold|silver|bronze)|[mg]/[a-z0-9-]+|external)\.gif$")   # and the rest
            st, _, ct, body = self.get(src)
            self.assertEqual((st, ct), (200, "image/gif"), src)
            self.assertTrue(body.startswith("GIF89a"), src)

    def test_external_icon(self):
        """The icon after a link to the docs site: ParlorNet's own 9-pixel GIF, drawn from webstyle.EXTERNAL_ROWS
        in the link color on a transparent background, with (external) as its ALT text for text browsers."""
        from adapters import board, webstyle as S
        st, _, ct, body = self.get(S.EXTERNAL)
        self.assertEqual((st, ct), (200, "image/gif"))
        data = body.encode("latin-1")
        self.assertEqual(data, S.external_gif())
        self.assertTrue(data.startswith(b"GIF89a"))
        self.assertEqual((data[6], data[8]), (S.EXTERNAL_SIZE, S.EXTERNAL_SIZE))
        self.assertIn(b"\x21\xf9\x04\x01", data)                              # index 0 is transparent
        self.assertEqual(data[13:19], bytes([255, 255, 255, 0x00, 0x33, 0x99]))  # white behind, the navy link color
        img = board.decode_gif(data)
        self.assertEqual("".join("#" if v else "." for v in img.pixels), "".join(S.EXTERNAL_ROWS))
        self.assertEqual([len(r) for r in S.EXTERNAL_ROWS], [S.EXTERNAL_SIZE] * S.EXTERNAL_SIZE)
        self.assertEqual(S.external_img(), EXTERNAL_IMG)

    def test_pictures(self):
        """Every machine's row on the welcome page and every game on the Games page, its own page and the lobby's
        Start a table has its picture (adapters/siteimages.py), an <img> with its size and ALT text, served as
        the same GIF; the welcome page links the credits page, which names each photo's source, author and
        license."""
        from adapters import clients as C, siteimages as PIC, web as W
        tags = {}

        def imgs(page):
            found = re.findall(r'<img src="(/img/[mg]/[a-z0-9-]+\.gif)" width="(\d+)" height="(\d+)"'
                               r'(?: align="middle")? alt="([^"]+)">', page)
            for src, w, h, alt in found:
                tags[src] = (int(w), int(h), alt)
            return [f[0] for f in found]
        welcome = self.get("/welcome.htm")[3]
        anchors = [m[0] for m in C.PLAY_MACHINES] + [W.ANY_OLD[0], W.ANY_NEW[0]]
        self.assertEqual(imgs(welcome), [PIC.GAME_URL % r["type"] for r in W.game_rows()] +
                         [PIC.MACHINE_URL % m for m in anchors])
        self.assertIn('<a href="/credits.htm">picture credits</a>', welcome)
        rows = W.game_rows()
        games = welcome[welcome.index('<a name="games">'):welcome.index('<a name="machines">')]
        self.assertEqual(imgs(games), [PIC.GAME_URL % r["type"] for r in rows])
        for r in rows:
            self.assertEqual(imgs(self.get("/games/%s.htm" % r["type"])[3]), [PIC.GAME_URL % r["type"]])
        lobby = self.ann.get("/lobby.htm")[3]
        start = lobby[lobby.index("Start a table"):]
        keys = re.findall(r'<input type="hidden" name="g" value="([a-z0-9]+)/\d+">', start)
        self.assertEqual(imgs(start), [PIC.GAME_URL % k for k in keys])
        self.assertEqual(len(keys), len(rows))
        for src, (w, h, alt) in tags.items():        # the same bytes as the file, at the size the tag gives
            st, _, ct, body = self.get(src)
            self.assertEqual((st, ct), (200, "image/gif"), src)
            data = body.encode("latin-1")
            self.assertEqual(data, PIC.IMAGES[src], src)
            self.assertEqual(PIC.gif_size(data), (w, h), src)
            self.assertTrue(alt.strip(), src)
        st, _, ct, page = self.get("/credits.htm")
        self.assertEqual((st, ct), (200, "text/html"))
        self.assertIn("<h1>Picture credits</h1>", plain(page))
        for c in PIC.credits():
            anchor = c["picture"].split("/")[1][:-len(".gif")]
            self.assertIn(PIC.machine_img(anchor), page)
            self.assertIn('<a href="%s">%s</a>' % (C.esc(c["source"]), C.esc(c["shows"])), page)
            self.assertIn(W.html_text(c["author"]), page)
            self.assertIn('<a href="%s">%s</a>' % (c["license_url"], c["license"]) if c["license_url"]
                          else c["license"], page)
        self.assertIn("Milo&#353; Juri&#353;i&#263;", page)         # beyond ASCII: a character reference


class MotdGap(unittest.TestCase):
    """The welcome page leaves a line between the message of the day's box and what follows it in the Welcome
    section: a note, or the Sign-In section with the signed-in callout. Without a message of the day the page has
    no extra line."""

    def welcome(self, motd=(), message=""):
        from adapters import web as W
        req = {"headers": {"host": "parlornet.org:6500"}, "path": "/welcome.htm", "query": {}}
        raw = W.welcome(req, message, motd=motd, nav="", lobby="/lobby.htm")
        return raw.decode("latin-1").split("\r\n\r\n", 1)[1]

    def test_gap(self):
        box_end = "</pre></td></tr>\n</table>\n</td></tr></table>\n"
        callout = '<table width="100%" border="0" cellpadding="1" cellspacing="0">'
        page = self.welcome(["Hello."])
        self.assertIn(box_end + "<br>\n<h2>", page)                     # then Sign-In
        self.assertIn("You are signed in.", page[page.index('<a name="signin">'):])
        page = self.welcome(["Hello."], "Signed in as ann.")
        self.assertIn(box_end + "<br>\n" + callout, page)                # then the note's box
        self.assertLess(page.index("Signed in as ann."), page.index('<a name="signin">'))
        page = self.welcome()
        self.assertNotIn('<a name="motd">', page)
        self.assertNotIn("<br>\n<h2>", page[:page.index('<a name="signin">') + 30])   # no message of the day: no extra line


class Palette(unittest.TestCase):
    """The frame's colors are web-safe, and no text in a colored cell is light (Netscape 2 and IE 2 show
    font colors but not cell colors, so light text there would be white on white). The docs site's palette is
    greyscale, so a reader can tell the two sites apart."""

    def test_colours(self):
        from adapters import webstyle as S
        for palette in (S.GREEN, S.GREY):
            for c in palette:
                self.assertRegex(c, r"^#(?:[0369CF]{2}){3}$")
                self.assertTrue(all(int(c[i:i + 2], 16) % 0x33 == 0 for i in (1, 3, 5)), c)
            with S.using(palette):
                page = S.frame("T", S.h1("H") + S.callout(S.p("x")) + S.data_table(["a"], [["b"], ["c"]]),
                               current="/welcome.htm")
            self.assertIn('<body bgcolor="%s" text="%s" link="%s" vlink="%s" alink="%s">' % (
                palette.paper, palette.ink, palette.link, palette.vlink, palette.alink), page)
            for colour in re.findall(r'<font[^>]*color="(#[0-9A-F]{6})"', page):
                r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
                self.assertLess(0.2126 * r + 0.7152 * g + 0.0722 * b, 96, colour)   # dark: reads on white too
            for colour in re.findall(r'<td[^>]*bgcolor="(#[0-9A-F]{6})"[^>]*>[^<&]*<font', page):
                self.assertNotEqual(colour, palette.edge)       # no text on the edge color
            self.assertEqual(set(re.findall(r'(?:bgcolor|color)="(#[0-9A-F]{6})"', page)) - set(palette), set())
        self.assertTrue(all(c[1:3] == c[3:5] == c[5:7] for c in S.GREY))     # every grey is a grey
        self.assertIs(S.colors(), S.GREEN)                                    # using() puts it back
        self.assertEqual(S.emblem_gif(__import__("adapters.web", fromlist=["make_gif"]).make_gif)[13:16],
                         bytes([0x00, 0x66, 0x33]))                           # the game server's emblem is green
