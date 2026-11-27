"""The docs site (docsite/build.py, tools/build_docs.py): every page is HTML 3.2 in the frame, every internal
link resolves, every client's protocol key has its entry in the protocols reference, the finders only hide
rows, and the clients data (adapters/clients.py) is whole.

    python3 -m unittest discover -s tests -p "test_docs_site.py"
"""
import hashlib
import html as htmllib
import os
import posixpath
import re
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from test_core import ROOT
from test_site import check

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# Byte budgets (the target modems run at 2400 to 14,400 bit/s): page -> most bytes. The Clients page lists
# every native client under its game and every browser and telnet program once, with the finder's script. The
# game rulebooks page has a table of downloads for each game.
BUDGETS = {"index.html": 60 * 1024, "browsers.html": 20 * 1024, "telnet.html": 18 * 1024,
           "protocols.html": 22 * 1024, "docs.html": 16 * 1024, "eclipse-68k-fix.html": 11 * 1024,
           "parlornet-rulebook.html": 9 * 1024, "gamemaster-rulebooks.html": 22 * 1024,
           "running.html": 37 * 1024}
CLIENT_BUDGET = 12 * 1024 + 512    # a client's page with its ratings (its screenshots come on top), an old setup address
GAME_GROWTH = 768                   # more for each game a client plays beyond four: its links and its ratings' lines
DOC_GROWTH = (1.5, 2048)        # a rendered doc: at most its Markdown times this, plus this


def plain(html):
    return re.sub(r"</?font[^>]*>", "", html)


def downloads_fixture(folder):
    """A local stand-in for the downloads (no network in the tests): for each file docsite/downloads.json pins, a
    small file of its own in folder, and a manifest like the real one with their SHA-256 (folder/downloads.json).
    Returns (manifest path, {file name: bytes})."""
    import json
    from docsite import downloads
    with open(downloads.MANIFEST) as f:
        manifest = json.load(f)
    data = {}
    for e in manifest["files"]:
        data[e["file"]] = ("fixture for %s, %s %s\n" % (e["file"], e["repo"], e["tag"])).encode("ascii") * 40
        with open(os.path.join(folder, e["file"]), "wb") as f:
            f.write(data[e["file"]])
        e["sha256"] = hashlib.sha256(data[e["file"]]).hexdigest()
    path = os.path.join(folder, "downloads.json")
    with open(path, "w") as f:
        json.dump(manifest, f)
    return path, data


class Fixture:
    """PARLOR_DOWNLOADS and PARLOR_DOWNLOADS_MANIFEST set to a fixture (or to an empty folder) for a while."""

    def __init__(self, empty=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.manifest, self.data = downloads_fixture(self.tmp.name)
        self.folder = os.path.join(self.tmp.name, "empty") if empty else self.tmp.name
        os.makedirs(self.folder, exist_ok=True)

    def __enter__(self):
        from docsite import downloads
        self.old = {k: os.environ.get(k) for k in ("PARLOR_DOWNLOADS", "PARLOR_DOWNLOADS_MANIFEST")}
        os.environ["PARLOR_DOWNLOADS"], os.environ["PARLOR_DOWNLOADS_MANIFEST"] = self.folder, self.manifest
        downloads._cache.clear()
        return self

    def __exit__(self, *exc):
        from docsite import downloads
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        downloads._cache.clear()
        self.tmp.cleanup()


class Built(unittest.TestCase):
    """The site, built once into a temporary folder by the tool itself, with the downloads from a local fixture
    (downloads_fixture) as tools/fetch_downloads.py would have fetched them."""

    @classmethod
    def setUpClass(cls):
        import build_docs
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "site")
        cls.fixture = Fixture().__enter__()
        with open(os.devnull, "w") as null:
            old, sys.stdout = sys.stdout, null
            try:
                assert build_docs.main(["--out", cls.out, "--require-downloads"]) == 0
            finally:
                sys.stdout = old
        cls.files = {}
        for d, _, fs in os.walk(cls.out):
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), cls.out).replace(os.sep, "/")
                with open(os.path.join(d, f), "rb") as fh:
                    cls.files[rel] = fh.read()
        cls.pages = {k: v.decode("ascii") for k, v in cls.files.items() if k.endswith(".html")}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.__exit__(None, None, None)
        cls.tmp.cleanup()

    @staticmethod
    def anchors(html):
        return set(re.findall(r'<a name="([^"]+)"', html))

    @staticmethod
    def content(html):
        """A page between its masthead and its footer, without FONT tags."""
        html = plain(html)
        return html[html.index("</table>") + 8:html.rindex("<br>\n<table")]


class SiteWide(Built):

    def test_the_pages_are_there(self):
        from adapters import clients as C
        from docsite import build
        from docsite import downloads
        for name in ("index.html", "protocols.html", "browsers.html", "telnet.html", "docs.html",
                     "eclipse-68k-fix.html", "404.html", "img/parlor.gif",
                     "img/eclipse-68k-fix.gif", "download/eclipse-68k-fix-readme.txt", ".nojekyll",
                     "parlornet-rulebook.html", "download/parlornet-rulebook-readme.txt",
                     "gamemaster-rulebooks.html", "download/gamemaster-rulebooks-readme.txt", "running.html",
                     "img/diagram-parlor.gif", "img/diagram-request-path.gif", "img/diagram-serial.gif",
                     "img/diagram-networdz.gif"):
            self.assertIn(name, self.files)
        for name, data in self.fixture.data.items():    # every pinned download, as fetched
            self.assertEqual(self.files["download/" + name], data)
            self.assertIn(name, downloads.FILES)
        self.assertEqual(set(self.fixture.data), set(downloads.FILES))
        self.assertEqual(self.files["CNAME"], b"docs.parlornet.org\n")
        for c in C.EVERY:
            self.assertIn(c.href().lstrip("/"), self.pages)
        for path in C.OLD_SETUP_PAGES:
            self.assertIn(path.lstrip("/"), self.pages)
        for path in build.doc_files():                  # every Markdown file under docs/, and nothing else
            self.assertIn(path.lstrip("/"), self.pages)
        docs = [p for p in self.pages if p.startswith("docs/")]
        self.assertEqual(len(docs), len(build.doc_files()))
        for p in self.files:                            # nothing from deploy/ (or anywhere but docs/ and static/)
            self.assertNotIn("deploy", p)
            self.assertFalse(p.endswith((".md", ".patch", ".py")), p)
        st = os.stat(os.path.join(self.out, "index.html"))
        self.assertGreater(st.st_size, 0)

    def test_every_internal_link_resolves(self):
        """Every href and src that is not an absolute URL names a file of the site, and its fragment an
        anchor on that page; links are relative, so the site works from any address or a folder."""
        bad = []
        names = {p: self.anchors(h) for p, h in self.pages.items()}
        checked = 0
        for page, html in self.pages.items():
            if page == "404.html":          # shown at any address: its links are from the root
                for u in re.findall(r'href="([^"]+)"', html):
                    self.assertTrue(u.startswith(("/", "http")), u)
                continue
            for u in re.findall(r'(?:href|src)="([^"]+)"', html):
                u = htmllib.unescape(u)
                if re.match(r"^[a-z]+:", u):
                    continue
                self.assertFalse(u.startswith("/"), (page, u))
                path, _, frag = u.partition("#")
                path = path.split("?")[0]
                target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path)) if path else page
                checked += 1
                if target not in self.files:
                    bad.append((page, u))
                elif frag and frag not in names.get(target, ()):
                    bad.append((page, u))
        self.assertEqual(bad, [])
        self.assertGreater(checked, 500)

    def test_every_page_is_html32_in_the_frame(self):
        for name, page in self.pages.items():
            c = check(page)
            self.assertEqual(c.problems, [], name)
            low = page.lower()
            for bad in ("<style", "&#x", "<div", "<span"):
                self.assertNotIn(bad, low, name)
            if "<script" in low:                        # only the finders
                self.assertIn(name, ("index.html", "docs.html"))
            self.assertTrue(page.startswith('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">'), name)
            self.assertRegex(page[:1500], r">ParlorNet Docs</font>", name)
            self.assertRegex(page, r"<title>ParlorNet Docs: [^<]+</title>", name)
            self.assertIn("ParlorNet Docs covers every ParlorNet server.", page[-900:], name)
            nav = plain(page)[:plain(page).index("</table>")]
            bar = nav[nav.rindex('<td bgcolor="#999999">'):]
            labels = re.findall(r">([^<>|]+)</(?:a|b)>", bar)
            self.assertEqual(labels, ["ParlorNet", "Clients", "Protocols", "Browsers", "Telnet programs", "Docs",
                                      "Run a server", "Source"], name)
            self.assertTrue(bar.startswith('<td bgcolor="#999999"><a href="http://parlornet.org/">ParlorNet</a> | '),
                            name)       # the main site first, over plain HTTP
            self.assertNotIn("Server: <b>", page, name)              # no server's name: it serves them all

    def test_breadcrumbs(self):
        """Every page but the home page starts, under the navigation bar and above its title, with the chain of
        menus back up to it: its menu's entry as a link that lands on a page of the site, then its own name as
        plain text, from the page's own title. A client's page is under Clients, a doc under Docs, and a page
        of the bar shows its name alone. The main site is never part of it."""
        from adapters import clients as C
        from docsite import build
        for name, page in self.pages.items():
            body = self.content(page).lstrip("\n")
            if name == "index.html":
                self.assertTrue(body.startswith("<h1>"), name)
                continue
            m = re.match(r"<p>(.*?)</p>\n<h1>", body, re.S)
            self.assertTrue(m, name)
            parts = m.group(1).split(" &gt; ")
            title = re.search(r"<title>ParlorNet Docs: ([^<]+)</title>", page).group(1)
            self.assertEqual(parts[-1], title, name)
            self.assertNotIn("<a ", parts[-1], name)
            for part in parts[:-1]:
                link = re.fullmatch(r'<a href="([^"#]+)">([^<]+)</a>', part)
                self.assertTrue(link, (name, part))
                self.assertNotIn(":", link.group(1), name)          # inside the site, never the main site
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link.group(1)))
                self.assertIn(target, self.pages, (name, link.group(1)))
            menus = [re.sub(r"<[^>]+>", "", x) for x in parts[:-1]]
            if name.startswith(("clients/", "setup/")):
                self.assertEqual(menus, ["Clients"], name)
            elif name.startswith("docs/") or name in ("eclipse-68k-fix.html", "parlornet-rulebook.html",
                                                      "gamemaster-rulebooks.html"):
                self.assertEqual(menus, ["Docs"], name)
            else:
                self.assertEqual(menus, [], name)                   # a page of the bar, or Not found
        self.assertIn('<p><font face="Geneva,Helvetica,Arial" size="2"><a href="../index.html">Clients</a> &gt; '
                      'GameMaster</font></p>\n<h1>', self.pages["clients/gamemaster.html"])

    def test_byte_budgets(self):
        from adapters import clients as C
        from docsite import build
        docs = build.doc_files()
        for name, page in self.pages.items():
            size = len(page)
            if name in BUDGETS:
                self.assertLessEqual(size, BUDGETS[name], name)
            elif name.startswith(("setup/", "clients/")):
                # the screenshots come on top: their pictures and the paragraphs that hold them
                shots = sum(len(s) for s in re.findall(r"<p><img .*?</p>\n", page, re.S))
                c = C.BY_ID[name.split("/")[1][:-len(".html")]]
                self.assertLessEqual(size - shots, CLIENT_BUDGET + GAME_GROWTH * max(0, len(c.games) - 4), name)
            elif name.startswith("docs/"):
                md = os.path.getsize(docs["/" + name])
                self.assertLessEqual(size, md * DOC_GROWTH[0] + DOC_GROWTH[1], name)

    def test_no_server_is_named(self):
        """The docs serve every server: setup steps say host (in italics), and no page marks a sign-in
        method as this server's."""
        from adapters import clients as C
        for name in ["browsers.html", "telnet.html", "eclipse-68k-fix.html"] + [c.href().lstrip("/") for c in C.ALL]:
            page = self.pages[name]
            self.assertIn("<i>host</i>", page, name)
            self.assertFalse("(this server)" in page, name)
            if name != "eclipse-68k-fix.html":            # its bug report shows what happens to a name
                # the bar links the main site, and ParlorNet's FTP site serves downloads, not games
                self.assertFalse("parlornet.org" in self.content(page).replace("ftp.parlornet.org", ""), name)
        self.assertIn("<tt>telnet <i>host</i> 6502</tt>", self.pages["telnet.html"])


class Diagrams(Built):
    """The docs' diagrams: an SVG source in docs/diagrams/ and the GIF tools/diagrams.py draws from it, small and
    web-safe, shown by at least one doc, and on the site with its size and caption."""

    @staticmethod
    def gif_colors(data):
        """The colors of a GIF's global color table, as hex strings."""
        flags = data[10]
        if not flags & 0x80:
            return []
        n = 2 << (flags & 7)
        table = data[13:13 + 3 * n]
        return ["#%02X%02X%02X" % tuple(table[i:i + 3]) for i in range(0, len(table), 3)]

    def markdown(self):
        """{repository path: Markdown} of every file a diagram may be shown in."""
        out = {}
        for d, _, fs in os.walk(os.path.join(ROOT, "docs")):
            for f in fs:
                if f.endswith(".md"):
                    full = os.path.join(d, f)
                    with open(full, encoding="utf-8") as fh:
                        out[os.path.relpath(full, ROOT).replace(os.sep, "/")] = fh.read()
        for rel in ("README.md", "deploy/README.md"):
            with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as fh:
                out[rel] = fh.read()
        return out

    def test_sources_and_gifs(self):
        import diagrams
        names = diagrams.sources()
        self.assertEqual(set(names), {"parlor", "request-path", "serial", "networdz"})
        drawn = {f for f in os.listdir(os.path.join(ROOT, "static", "img")) if f.startswith("diagram-")}
        self.assertEqual(drawn, {diagrams.gif_name(n) for n in names})          # no GIF without its source
        for n in names:
            svg = os.path.join(diagrams.SRC, n + ".svg")
            with open(svg, encoding="utf-8") as f:
                text = f.read()
            self.assertRegex(text, r"<title>[^<]{20,}</title>", n)
            with open(os.path.join(diagrams.OUT, diagrams.gif_name(n)), "rb") as f:
                data = f.read()
            self.assertIn(data[:6], (b"GIF87a", b"GIF89a"), n)
            from docsite import clientpages
            self.assertEqual(clientpages.gif_size(data), diagrams.svg_size(svg), n)   # drawn from this source
            self.assertLessEqual(clientpages.gif_size(data)[0], diagrams.MAX_WIDTH, n)
            self.assertLessEqual(len(data), 21 * 1024, n)
            colors = self.gif_colors(data)
            self.assertTrue(colors, n)
            for c in colors:                    # web-safe: each part a multiple of 0x33
                self.assertTrue(all(int(c[i:i + 2], 16) % 0x33 == 0 for i in (1, 3, 5)), (n, c))
            self.assertTrue(set(colors) <= set(diagrams.PALETTE) | {"#000000"}, n)

    def test_each_is_shown_with_its_size_and_caption(self):
        import diagrams
        from docsite import clientpages
        md = self.markdown()
        for n in diagrams.sources():
            name = diagrams.gif_name(n)
            shown = [rel for rel, text in md.items()
                     if re.search(r"^!\[[^\]]+\]\((?:\.\./)*static/img/%s\)$" % re.escape(name), text, re.M)]
            self.assertTrue(shown, n)
            with open(os.path.join(diagrams.OUT, name), "rb") as f:
                data = f.read()
            self.assertEqual(self.files["img/" + name], data)
            w, h = clientpages.gif_size(data)
            pages = [pg for pg, html in self.pages.items() if "img/" + name in html]
            self.assertTrue(pages, n)
            for pg in pages:
                self.assertRegex(self.pages[pg], r'<img src="(?:\.\./)*img/%s" width="%d" height="%d" alt="\[[^"\]]+\]">'
                                 % (re.escape(name), w, h), (n, pg))
        for page in ("docs/architecture.html", "docs/serial.html", "clients/networdz.html", "running.html"):
            self.assertIn("img/diagram-", self.pages[page], page)

    def test_pictures_in_markdown(self):
        """A picture on a line of its own: drawn by the caller, or plainly; on the docs site only a GIF from
        static/img/."""
        from adapters.mdhtml import to_html
        from docsite import clientpages
        self.assertEqual(to_html("Text.\n![A cat](cat.gif)\nMore.", image=lambda c, u: "<%s|%s>" % (c, u)),
                         "<p>Text.</p>\n<A cat|cat.gif>\n<p>More.</p>")
        self.assertIn('<img src="cat.gif" alt="[A cat]">', to_html("![A cat](cat.gif)"))
        self.assertIn('<img src="/img/diagram-serial.gif" width="480"',
                      clientpages.figure("Serial", "../static/img/diagram-serial.gif"))
        for bad in ("../static/img/x.png", "http://example.org/x.gif", "../docs/x.gif"):
            with self.assertRaises(ValueError):
                clientpages.figure("X", bad)


class RunningPage(Built):
    """running.html: running a server of your own, made from the README's "Run your own parlor" section and
    deploy/README.md as they are, and easy to find: in the navigation, on the Clients page and the docs index,
    and pointed to by the README."""

    def test_made_from_the_repository(self):
        from adapters.mdhtml import esc
        from docsite import running
        page = self.pages["running.html"]
        body = plain(page)
        for rel, _, md in running.sources():
            for block in re.findall(r"```[a-z]*\n(.*?)```", md, re.S):     # every command and config, as it is
                self.assertIn("<pre>%s</pre>" % esc(block.rstrip("\n")), body, rel)
        self.assertEqual(len(re.findall(r"<pre>", page)),
                         sum(len(re.findall(r"^```", md, re.M)) // 2 for _, _, md in running.sources()))
        self.assertIn('<h2><a name="run-your-own-parlor">', body)
        self.assertIn('<h2><a name="installing-on-a-docker-host">', body)
        for name in ("a-lan-a-club-night-or-a-festival", "the-open-internet", "parlornet-play-over-https",
                     "administration", "more-separation", "memory"):
            self.assertIn('<h3><a name="%s">' % name, body)
        self.assertIn('href="https://github.com/eric-cgn/parlornet"', page)
        self.assertIn('href="https://github.com/eric-cgn/parlornet/blob/main/deploy/README.md"', page)

    def test_links_between_the_two_stay_on_the_page(self):
        page = self.pages["running.html"]
        sources = {"https://github.com/eric-cgn/parlornet/blob/main/README.md#run-your-own-parlor",
                   "https://github.com/eric-cgn/parlornet/blob/main/deploy/README.md"}     # the closing line
        for u in re.findall(r'href="([^"]+)"', page):
            if "README.md" in u:
                self.assertIn(u, sources)
        for anchor in ("#installing-on-a-docker-host", "#the-open-internet", "#run-your-own-parlor",
                       "#parlornet-play-over-https"):
            self.assertIn('href="%s"' % anchor, page)

    def test_names_no_host_of_ours(self):
        """Examples use example.org; the only parlornet.org name in the page's content is the docs site's."""
        page = self.content(self.pages["running.html"])
        self.assertEqual(set(re.findall(r"[\w.-]*parlornet\.org", page)), {"docs.parlornet.org"})
        self.assertIn("games.example.org", page)

    def test_easy_to_find(self):
        from docsite import running
        self.assertIn('href="running.html"', self.pages["docs.html"])
        self.assertIn('<a name="server">', self.pages["index.html"])
        self.assertIn('href="running.html">Run a server</a>', self.pages["index.html"])
        self.assertIn('href="../running.html">Run a server</a>', self.pages["docs/architecture.html"])
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("](deploy/README.md)", readme)
        self.assertIn("](http://docs.parlornet.org%s)" % running.PAGE, readme)
        self.assertIn("## " + running.README_SECTION + "\n", readme)


class ClientsPage(Built):
    """index.html: the summary grid, each game's native clients, Your own program, then Any Game (Generic)
    Clients once at the bottom; each row's Protocol cell links to its key's entry."""

    @staticmethod
    def sections(html):
        html = plain(html)
        html = html[:html.index('<a name="native">')]
        parts = re.split(r'<h2><a name="([a-z]+)">', html)
        return dict(zip(parts[1::2], parts[2::2]))

    @staticmethod
    def rows(section):
        return re.findall(r'<tr(?: bgcolor="#CCCCCC")?><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td>'
                          r'<td>(.*?)</td></tr>', section)

    def test_the_retro_window(self):
        """The home page states the retro window, about 25 to 50 years ago, and the policy has its own part of
        docs/clients.md, which the frontends doc links to."""
        intro = self.content(self.pages["index.html"])
        intro = intro[:intro.index("<table")]
        self.assertIn("first released about 25 to 50 years ago", intro)
        for words in ("1977", "2027", "native-client work"):
            self.assertNotIn(words, intro)
        self.assertNotIn("2010", intro)
        policy = self.pages["docs/clients.html"]
        self.assertIn('<a name="the-retro-window">', policy)
        part = policy[policy.index('<a name="the-retro-window">'):policy.index('<a name="native-clients">')]
        for words in ("25 to 50 years ago", "rule of thumb", "any computer gets in, old or new"):
            self.assertIn(words, part)
        self.assertIn('clients.html#the-retro-window"', self.pages["docs/frontends.html"])

    def test_every_game_has_its_table(self):
        from adapters import clients as C
        from docsite import build
        page = self.content(self.pages["index.html"])
        sections = self.sections(page)
        games = build.games()
        self.assertEqual(list(sections), [t for _, t in games])
        html = plain(page)
        generic = html[html.index('<a name="generic">'):]
        self.assertGreater(html.index('<a name="generic">'), html.index('<a name="native">'))     # at the bottom
        self.assertTrue(generic.startswith('<a name="generic">%s</a></h2>' % C.GENERIC_HEAD))
        self.assertEqual(generic.count("<table"), 1)
        self.assertLess(generic.index('<a name="web">'), generic.index("<table"))
        self.assertLess(generic.index('<a name="telnet">'), generic.index("<table"))
        checks = [(generic, C.BROWSERS + C.TELNETS)]
        for name, gtype in games:
            sec = sections[gtype]
            self.assertTrue(sec.startswith("%s</a></h2>" % name), gtype)
            native = [c for c in C.PERIOD if gtype in c.games]
            self.assertEqual(sec.count("<table"), 1 if native else 0, gtype)
            self.assertEqual(C.NO_NATIVE in sec, not native, gtype)
            self.assertIn('<a href="index.html#generic">%s</a>' % C.WEB_OR_TELNET, sec)
            for c in C.BROWSERS + C.TELNETS:
                self.assertNotIn('"%s"' % c.href().lstrip("/"), sec, gtype)
            checks.append((sec, native))
        for sec, want in checks:
            if want:
                self.assertEqual(re.findall(r"<th[^>]*>([^<]*)</th>", sec), ["Client", "Year", "OS", "Protocol", "Setup"])
            rows = self.rows(sec)
            self.assertEqual(len(rows), len(want))
            for (client, year, os_, proto, setup), c in zip(rows, want):
                self.assertIn(C.esc(c.name), client)
                self.assertEqual(year, c.year)
                self.assertEqual(os_, C.esc(c.os))
                self.assertEqual(proto, '<a href="protocols.html#%s">%s</a>' % (C.protocol_anchor(c.key()), c.key()))
                self.assertEqual(setup, '<a href="%s">setup</a>' % c.href().lstrip("/"))
        for anchor in ("period", "frontends", "clients", "native", "generic", "web", "telnet"):
            self.assertIn('<a name="%s">' % anchor, page)

    def test_every_protocol_key_has_its_entry(self):
        """Every client's key, every key a server extension declares, and the core's own are in the
        protocols reference, anchored as the server's Protocols page links them."""
        from adapters import clients as C, fibs, ios, outland, playserver, telnet, web
        from adapters.webstyle import protocol_anchor
        from docsite import protocols
        from parlord import core
        ref = self.pages["protocols.html"]
        names = self.anchors(ref)
        declared = [m.PROTOCOL[0] for m in (playserver, web, telnet, ios, fibs, outland)] + [core.NATIVE_PROTOCOL[0]]
        for key in {c.key() for c in C.ALL} | set(declared):
            self.assertIn(key, protocols.KEYS)
            self.assertIn(protocol_anchor(key), names, key)
            self.assertRegex(key, core.PROTO_KEY_RE)
        self.assertEqual(protocol_anchor("PARLORNET/1"), "PARLORNET-1")
        self.assertEqual(len(set(protocols.KEYS)), len(protocols.KEYS))
        for m in (playserver, web, telnet, ios, fibs, outland):     # each fits the PRO and PRD lines
            key, name, desc = m.PROTOCOL
            self.assertLessEqual(len(name), core.PROTO_NAME_LEN, key)
            self.assertLessEqual(len(desc), core.PROTO_DESC_LEN, key)
        # the reference's entries in the usual port order, each with its guide's part in docs/protocols.md
        guide = self.anchors(self.pages["docs/protocols.html"])
        for _, _, _, _, _, _, _, anchor, _ in protocols.PROTOCOLS:
            self.assertIn(anchor, guide)
        self.assertIn("ParlorNet is not FIBS and is not affiliated with it", ref)

    def test_protocols_reference_agrees_with_the_doc(self):
        """The table at the top of docs/protocols.md has the same keys, ports, protocols, origins and years."""
        from docsite import protocols
        with open(os.path.join(ROOT, "docs", "protocols.md"), encoding="utf-8") as f:
            md = f.read()
        table = re.findall(r"^\| `([^`]+)` \| (\d+) \| \[([^\]]+)\]\(#([^)]+)\) \| (.*) \|$", md, re.M)
        self.assertEqual([(k, int(port), name, anchor) for k, port, name, anchor, _ in table],
                         [(k, port, name, anchor) for k, name, port, _, _, _, _, anchor, _ in protocols.PROTOCOLS])
        for (key, _, _, _, cells), row in zip(table, protocols.PROTOCOLS):
            origin, years, _ = cells.split(" | ")
            self.assertEqual((origin, years), (row[3], row[4]), key)

    def test_summary_grid(self):
        """Machines down, games across, one symbol per cell (native over telnet over web), each linking to the
        native client's setup page when it is the only one, else to the Clients page narrowed to the machine
        (?plat=, ?arch=, which the finder reads) at the game or at Any Game (Generic) Clients."""
        from adapters import clients as C
        from docsite import build
        html = self.content(self.pages["index.html"])
        grid = html[html.index("<table"):html.index("</table>")]
        games = build.games()
        self.assertEqual(re.findall(r'<th><a href="#([a-z]+)">([^<]+)</a></th>', grid),
                         [(t, C.abbr(t)) for _, t in games])
        rows = re.findall(r'<tr align="center"><td align="left"><a href="([^"]+)">([^<]+)</a></td>(.*?)</tr>', grid)
        self.assertEqual([r[1] for r in rows], [C.esc(m[0]) for m in C.MACHINES])
        for (href, _, cells), (label, plat, arch) in zip(rows, C.MACHINES):
            u = urlsplit(href.replace("&amp;", "&"))
            self.assertEqual(u.path, "index.html")
            self.assertEqual(C.filter_values(parse_qs(u.query)), (plat, arch), label)
            got = re.findall(r'<td(?: bgcolor="#999999")?><a href="([^"]+)">([NTW-])</a>((?:<img [^>]+>)*)</td>',
                             cells)
            self.assertEqual(len(got), len(games), label)
            for (link, sym, icons), (_, gtype) in zip(got, games):
                # a medal for each native client rated on this machine for this game, its word as the ALT text
                rated = [c.medals(gtype, plat, arch)[0].medal for c in C.native_for(gtype, plat, arch)
                         if c.medals(gtype, plat, arch)]
                self.assertEqual(re.findall(r'alt="([^"]+)"', icons), [C.MEDALS[m][0] for m in rated],
                                 (label, gtype))
                link = link.replace("&amp;", "&")
                found = C.clients_for(gtype, plat, arch)
                native = [c for c in found if c.kind == "period"]
                want = ("N" if native else "T" if any(c.kind == "telnet" for c in found)
                        else "W" if any(c.kind == "browser" for c in found) else "-")
                self.assertEqual(sym, want, (label, gtype))
                if link.startswith("clients/"):
                    self.assertEqual([c.href().lstrip("/") for c in native], [link], (label, gtype))
                    continue
                u = urlsplit(link)
                self.assertEqual((u.path, u.fragment), ("index.html", C.GENERIC if sym in "TW" else gtype))
                self.assertEqual(C.filter_values(parse_qs(u.query)), (plat, arch))
        for sym, meaning in C.SYMBOLS:
            self.assertIn("<b>%s</b> %s" % (sym, meaning), html)
        self.assertIn(C.GRID_MEDALS, html)
        # a 68040 Mac has rated native clients; a modern computer has none, so no medals in its row
        quadra = rows[[m[0] for m in C.MACHINES].index("Mac Quadra 800 (68040, System 7.1-8.1)")][2]
        self.assertIn('alt="Gold"', quadra)
        self.assertNotIn("<img", rows[-1][2])

    def test_finders_only_hide_rows(self):
        """The finder is written by its script only where the DOM is there to hide rows, so without it there is
        no box and every row shows; the page's content is the same with or without it. The Clients page's
        script knows where each client runs, as the summary grid does."""
        import json
        from adapters import clients as C
        for name in ("index.html", "docs.html"):
            page = self.pages[name]
            script = re.search(r'<script language="JavaScript"><!--\n(.*?)//--></script>', page, re.S)
            self.assertTrue(script, name)
            outside = page[:script.start()] + page[script.end():]
            self.assertNotIn("<form", outside.lower(), name)         # no box without the script
            self.assertNotIn("<input", outside.lower(), name)
            js = script.group(1)
            self.assertIn("if (document.getElementsByTagName && document.createTextNode && document.forms)", js)
            self.assertIn("document.write(", js)
            self.assertNotIn("</", js.replace("<\\/", ""))           # nothing that would end the script early
            self.assertNotIn("innerHTML", js)                          # it hides; it writes no content
            self.assertEqual(js.count('style.display = '), 4)          # rows, their tables, the "none" notes
        # a client's row is one whose Setup link goes to it; the summary grid links to setup pages too, and stays
        self.assertIn('if (pnRuns[h] && pnText(links[j]) == "setup")', self.pages["index.html"])
        runs = json.loads(re.search(r"var pnRuns = (\{.*?\}), pnRows", self.pages["index.html"]).group(1))
        self.assertEqual(set(runs), {c.href().lstrip("/") for c in C.ALL})
        for c in C.ALL:
            for plat, arch in [("mac", "68000"), ("win", ""), ("", "ppc"), ("amiga", "68020")]:
                js_match = any((not plat or t.split(":")[0] == plat) and (not arch or t.split(":")[1] == arch)
                               for t in runs[c.href().lstrip("/")])
                self.assertEqual(js_match, c.runs_on(plat, arch), (c.id, plat, arch))
        self.assertIsNone(json.loads(re.search(r"var pnRuns = (null|\{.*?\}), pnRows",
                                               self.pages["docs.html"]).group(1)))
        for v, _ in C.PLATFORMS + C.ARCHS:                          # the menus offer every filter value
            self.assertIn('<option value=\\"%s\\">' % v, self.pages["index.html"])


class DocsPages(Built):

    def test_docs_index_lists_every_doc(self):
        from docsite import build
        index = self.pages["docs.html"]
        for path in build.doc_files():
            if path not in build.SKIP_IN_INDEX:
                self.assertIn('href="%s"' % path.lstrip("/"), index, path)
        for folder in ("types", "design"):
            self.assertIn('<a name="%s">' % folder, index)
        self.assertIn('href="https://github.com/eric-cgn/parlornet"', index)

    def test_frontend_design_page(self):
        """docs/design.md builds into its page, which the docs index and the frontends doc link to."""
        page = plain(self.pages["docs/design.html"])
        self.assertIn('<h1><a name="frontend-design">Frontend design</a></h1>', page)
        for anchor in ("the-principles", "the-shared-words", "the-program-flow", "each-games-actions",
                       "the-tutorial", "commands-the-client-handles-itself", "how-each-kind-of-frontend-carries-it", "what-the-server-provides"):
            self.assertIn('<a name="%s">' % anchor, page)
        self.assertIn('href="docs/design.html"', self.pages["docs.html"])
        self.assertIn('href="design.html"', self.pages["docs/frontends.html"])

    def test_doc_links(self):
        """A doc's link to another doc goes to its page; to another file of the repository, to GitHub; to the
        docs site, within the site."""
        from docsite import build
        fix = build.link_fixer("docs/types")
        self.assertEqual(fix("../protocol.md#views"), "/docs/protocol.html#views")
        self.assertEqual(fix("reversi-1.md"), "/docs/types/reversi-1.html")
        self.assertEqual(fix("../../adapters/web.py"), build.GITHUB_BLOB + "adapters/web.py")
        self.assertEqual(fix("../types/"), build.GITHUB_TREE + "docs/types")
        self.assertEqual(fix("http://docs.parlornet.org/browsers.html#mill"), "/browsers.html#mill")
        self.assertEqual(fix("http://docs.parlornet.org/"), "/index.html")
        self.assertEqual(fix("https://example.org/x.md"), "https://example.org/x.md")
        page = self.pages["docs/protocol.html"]
        self.assertIn('<h1><a name="parlornet-client-protocol-draft-1">', plain(page))
        self.assertIn('href="https://github.com/eric-cgn/parlornet/blob/main/docs/protocol.md">This doc on GitHub', page)

    def test_doc_headings_have_github_anchors(self):
        from adapters.mdhtml import to_html
        html = plain(to_html("# Front ends\n\n## Web site\n\n## IOS: the `Othello` server\n\n## Web site\n\n"
                             "## See [this](x.md)\n"))
        self.assertIn('<h1><a name="front-ends">Front ends</a></h1>', html)
        self.assertIn('<h2><a name="web-site">Web site</a></h2>', html)
        self.assertIn('<a name="ios-the-othello-server">', html)
        self.assertIn('<h2><a name="web-site-1">Web site</a></h2>', html)
        self.assertIn('<h2><a name="see-this"></a>See <a href="x.md">this</a></h2>', html)

    def test_the_game_server_links_resolve_here(self):
        """The game server's pages link into this site: each game's type spec, protocol guide chapter and
        Clients section, each protocol key, and every old documentation address it redirects."""
        from adapters import web as W
        names = {p: self.anchors(h) for p, h in self.pages.items()}

        def resolves(u):
            path, _, frag = u.partition("#")
            path = path.split("?")[0] or "index.html"
            return path in self.files and (not frag or frag in names.get(path, ()))
        base = W.S.DOCS[0]
        for r in W.game_rows():
            page = W.game_page(r["type"]).decode("ascii")
            links = [h for h in re.findall(r'href="([^"]+)"', page) if h.startswith(base) and h != base]
            self.assertEqual(len(links), 3, r["type"])         # type spec, protocol guide, Clients
            for h in links:
                self.assertTrue(resolves(htmllib.unescape(h)[len(base):]), h)
        from docsite import protocols
        for key in protocols.KEYS:
            self.assertTrue(resolves("protocols.html#" + W.S.protocol_anchor(key)), key)
        old = ["/frontends.htm", "/browsers.htm", "/telnet.htm", "/eclipse-68k-fix.htm", "/docs/index.htm",
               "/img/eclipse-68k-fix.gif", "/download/eclipse-68k-fix-readme.txt", "/docs/frontends.htm"]
        from docsite import downloads
        if downloads.fetched("eclipse-68k-fix"):    # the downloads are on the site only when they were fetched
            old += ["/download/eclipse-68k-fix.hqx", "/download/eclipse-68k-fix.bin", "/download/eclipse-68k-fix.dsk"]
        old += ["/setup/%s.htm" % c for c in ("eclipse", "olios", "macfibs", "fibsw", "xfibs", "xibc", "outland")]
        from docsite import build
        old += [p[:-len("l")] for p in build.doc_files()]        # /docs/x.html -> /docs/x.htm
        for u in old:
            target = W.moved_to(u)
            self.assertIsNotNone(target, u)
            self.assertTrue(resolves(target), (u, target))
        self.assertEqual(W.moved_to("/frontends.htm", {"plat": ["mac"], "arch": ["68000"]}),
                         "index.html?plat=mac&arch=68000")
        self.assertEqual(W.moved_to("/frontends.htm", {"plat": ["<b>"]}), "index.html")
        for u in ("/docs/../LICENSE.htm", "/setup/../x.htm", "/download/other.bin", "/welcome.htm"):
            self.assertIsNone(W.moved_to(u), u)


class EclipsePage(Built):

    def test_eclipse_fix_page_and_downloads(self):
        from docsite import downloads, eclipse
        body = self.pages["eclipse-68k-fix.html"]
        for words in ("32 characters", "PowerPC Macs are not affected", "A real 68k Mac", "<tt>CODE</tt> 1",
                      "System 7 or later", "Choose Eclipse...", "already fixed", "locked", "running",
                      "MIT license", "Lars Graulund", "No later version or patch was ever published",
                      "http://ftp.funet.fi/pub/mac/info-mac/game/reversi-the-eclipse-12.hqx", "<tt><i>host</i></tt>"):
            self.assertIn(words, body)
        game = re.search(r"Don&#39;t have the game\?</b>.*?<a href=\"([^\"]+)\"><b>([^<]+)</b></a>\n"
                         r"\(([\d,]+ KB), MD5 <tt>([0-9a-f]{32})</tt>\)", body, re.S)
        self.assertTrue(game)
        self.assertLess(game.start(), body.index('<a name="bug">'))
        self.assertEqual((game.group(2), game.group(3), game.group(4)),
                         ("reversi-the-eclipse-12.hqx", downloads.kb(eclipse.ECLIPSE_GAME_FACTS[0]),
                          eclipse.ECLIPSE_GAME_FACTS[1]))
        self.assertNotIn("SHA-256", body)
        for credit in ("Eric", "Claude", "Anthropic", "credits"):
            self.assertNotIn(credit, body)
        self.check_downloads(body, "eclipse-68k-fix")
        self.assertIn('the full text is in the <a href="download/eclipse-68k-fix-readme.txt">README.txt</a>',
                      plain(body))
        self.assertIn('<a href="https://github.com/eric-cgn/parlornet-eclipse-fix">eric-cgn/parlornet-eclipse-fix</a>',
                      body)
        self.assertEqual(self.files["download/eclipse-68k-fix-readme.txt"],
                         self.fixture.data["eclipse-68k-fix-readme.txt"])
        gif = self.files["img/eclipse-68k-fix.gif"]
        w, h = gif[6] | gif[7] << 8, gif[8] | gif[9] << 8
        self.assertIn('<img src="img/eclipse-68k-fix.gif" width="%d" height="%d"' % (w, h), body)
        self.assertIn('<a href="../eclipse-68k-fix.html">Eclipse 68k Fix</a>', self.pages["clients/eclipse.html"])

    def check_downloads(self, body, slug):
        """The Download part, with the downloads fetched (as the docs workflow does, tools/fetch_downloads.py):
        a row per file with the size and MD5 of the file on the site, which is the file fetched."""
        from docsite import downloads
        links = re.findall(r'<td><a href="(download/%s\.(hqx|bin|dsk))"><b>[^<]+</b></a></td>'
                           r'<td align="right">([\d,]+ KB)</td><td><tt>([0-9a-f]{32})</tt></td>'
                           % re.escape(slug), plain(body))
        self.assertEqual([ext for _, ext, _, _ in links], list(downloads.extensions(slug)))
        self.assertNotIn("SHA-256", body)
        self.assertNotIn("built without them", body)
        for path, ext, size, md5 in links:
            data = self.files[path]
            self.assertEqual(size, downloads.kb(len(data)))
            self.assertEqual(hashlib.md5(data).hexdigest(), md5, path)
            self.assertEqual(data, self.fixture.data["%s.%s" % (slug, ext)], path)

    def test_parlornet_rulebook_page_and_downloads(self):
        """parlornet-rulebook.html: what the rulebook does, the Mac Plus and System 6.0 it needs, its use and
        that it comes in Install ParlorNet Rulebooks, with no download of its own; its source in the rulebooks'
        repository."""
        body = self.pages["parlornet-rulebook.html"]
        for text in ("A Mac Plus or later with System 6.0 or later", "File &gt; New Game &gt; ParlorNet",
                     "Othello vs the bot", "Nothing is written to GameMaster or to any file",
                     "Neither your Mac&#39;s address nor its name counts", "MIT license",
                     '<a href="https://github.com/eric-cgn/parlornet-gamemaster/tree/main/rulebooks/parlornet">'
                     'rulebooks/parlornet</a>'):
            self.assertIn(text, body)
        self.assertIn('href="gamemaster-rulebooks.html#download"', body)
        self.assertIn('<a href="download/parlornet-rulebook-readme.txt">README.txt</a>', body)
        self.assertFalse([f for f in self.files if f.startswith("download/parlornet-rulebook.")])
        self.assertEqual(self.files["download/parlornet-rulebook-readme.txt"],
                         self.fixture.data["parlornet-rulebook-readme.txt"])
        self.assertIn('<a href="parlornet-rulebook.html"><b>ParlorNet rulebook</b></a>', self.pages["docs.html"])

    def test_game_rulebooks_page_and_downloads(self):
        """gamemaster-rulebooks.html: the games, what they need, the installer, how to play, and the one
        download, Install ParlorNet Rulebooks (an application made by EUP) as BinHex, MacBinary and an 800K disk
        image, with a sentence on which to pick and a link to its GitHub release. No rulebook is offered on its
        own."""
        from docsite import gamerulebooks as G, downloads
        body = self.pages["gamemaster-rulebooks.html"]
        for text in ("A Mac Plus or later with System 6.0 or later", "The installer needs System 6.0.3 or later",
                     "the folder that holds GameMaster", "Checkers vs the bot", "Offer draw",
                     "GameMaster&#39;s own rulebooks of those names", "MIT license", "contain none of GameMaster",
                     '<a href="https://github.com/eric-cgn/parlornet-gamemaster">eric-cgn/parlornet-gamemaster</a>',
                     "EUP&#39;s licenses folder"):
            self.assertIn(text, body)
        for r in G.RULEBOOKS:
            self.assertIn(r.game, body)
            self.assertFalse([f for f in self.files if f.startswith("download/%s." % r.slug)], r.slug)
        self.check_downloads(body, downloads.INSTALLER)
        self.assertEqual(downloads.extensions(downloads.INSTALLER), ("hqx", "bin", "dsk"))
        for text in ("You need only one of these files", "StuffIt Expander or BinHex 4.0",
                     "reads MacBinary", "for an emulator or for writing a floppy disk"):
            self.assertIn(text, body)
        tag = downloads.entry(downloads.INSTALLER + ".hqx")["tag"]
        self.assertIn('<a href="https://github.com/eric-cgn/parlornet-gamemaster/releases/tag/%s">' % tag, body)
        self.assertEqual(self.files["download/gamemaster-rulebooks-readme.txt"],
                         self.fixture.data["gamemaster-rulebooks-readme.txt"])
        self.assertIn('<a href="gamemaster-rulebooks.html"><b>Game rulebooks</b></a>', self.pages["docs.html"])


class Downloads(unittest.TestCase):
    """The downloads: pinned in docsite/downloads.json, fetched from the GitHub Releases of the repositories that
    build them by tools/fetch_downloads.py (run here with a stand-in for the network), checked by SHA-256, and
    left out of a site built without them."""

    def test_the_manifest(self):
        """Every download the pages offer is pinned once, to a release of its own repository, with a SHA-256;
        each repository has one tag. A README, which a release need not carry, comes from its repository's
        source at that tag, from its path there."""
        from docsite import downloads
        entries = downloads.entries(downloads.MANIFEST)
        self.assertEqual(sorted(e["file"] for e in entries), sorted(downloads.FILES))
        tags = {}
        for e in entries:
            self.assertEqual(e["repo"], downloads.FILES[e["file"]][0], e)
            self.assertRegex(e["tag"], r"^v\d+(\.\d+)*$")
            self.assertRegex(e["sha256"], r"^[0-9a-f]{64}$")
            tags.setdefault(e["repo"], set()).add(e["tag"])
            self.assertEqual(downloads.release_url(e), "https://github.com/%s/releases/download/%s/%s"
                             % (e["repo"], e["tag"], e["file"]))
            path = downloads.FILES[e["file"]][1]
            self.assertEqual(e.get("path"), path if e["repo"] == downloads.GAMEMASTER_REPO else None, e)
            if path and e["repo"] == downloads.GAMEMASTER_REPO:
                self.assertEqual(downloads.source_url(e), "https://raw.githubusercontent.com/%s/%s/%s"
                                 % (e["repo"], e["tag"], path))
            else:
                self.assertEqual(downloads.source_url(e), downloads.release_url(e))
        self.assertEqual(set(tags), set(downloads.REPOS))
        self.assertTrue(all(len(t) == 1 for t in tags.values()), tags)

    def test_the_workflow(self):
        """The docs workflow fetches the pinned downloads, then tests, then builds the site, which needs every
        download; it builds no Mac software (that is done in the programs' own repositories)."""
        with open(os.path.join(ROOT, ".github", "workflows", "docs.yml")) as f:
            workflow = f.read()
        self.assertLess(workflow.index("python3 tools/fetch_downloads.py"), workflow.index("test_docs_site.py"))
        self.assertLess(workflow.index("test_docs_site.py"),
                        workflow.index("tools/build_docs.py --out _site --require-downloads"))
        for word in ("retro68", "Retro68", "EUP", "eup", "cmake", "docker", "build_patchers"):
            self.assertNotIn(word, workflow)
        for path in ("patchers", "rulebooks", "static/downloads", "tools/build_patchers.sh", "tools/binhex4.py",
                     "tools/patcher_dates.py"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, path)), path)

    def fake_opener(self, served):
        """An opener for tools/fetch_downloads.py: {url: bytes}; any other address is a 404."""
        import io
        import urllib.error

        def opener(url, timeout=None):
            if url not in served:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
            return io.BytesIO(served[url])
        return opener

    def test_fetch(self):
        """A file with the pinned SHA-256 is written; a missing release is named with its repository and tag; a
        file with another SHA-256 is not written."""
        import fetch_downloads
        from docsite import downloads
        good, bad = b"the installer", b"something else"
        entries = [{"repo": "eric-cgn/parlornet-gamemaster", "tag": "v1.3", "file": "a.bin",
                    "sha256": hashlib.sha256(good).hexdigest()},
                   {"repo": "eric-cgn/parlornet-eclipse-fix", "tag": "v1.0", "file": "b.bin",
                    "sha256": hashlib.sha256(good).hexdigest()},
                   {"repo": "eric-cgn/parlornet-gamemaster", "tag": "v1.3", "file": "c.bin",
                    "sha256": hashlib.sha256(good).hexdigest()}]
        entries.append({"repo": "eric-cgn/parlornet-gamemaster", "tag": "v1.3", "file": "d.txt",
                        "path": "release/d.txt", "sha256": hashlib.sha256(good).hexdigest()})
        served = {downloads.release_url(entries[0]): good, downloads.release_url(entries[2]): bad,
                  "https://raw.githubusercontent.com/eric-cgn/parlornet-gamemaster/v1.3/release/d.txt": good}
        with tempfile.TemporaryDirectory() as tmp:
            problems = fetch_downloads.fetch(entries, tmp, self.fake_opener(served))
            with open(os.path.join(tmp, "a.bin"), "rb") as f:
                self.assertEqual(f.read(), good)
            self.assertEqual(sorted(os.listdir(tmp)), ["a.bin", "d.txt"])
            self.assertEqual(len(problems), 2)
            self.assertIn("The release v1.0 of eric-cgn/parlornet-eclipse-fix is not published", problems[0])
            self.assertIn("https://github.com/eric-cgn/parlornet-eclipse-fix/releases/download/v1.0/b.bin",
                          problems[0])
            self.assertIn("SHA-256 %s" % hashlib.sha256(bad).hexdigest(), problems[1])
            self.assertIn("not written", problems[1])
            # fetched already: nothing is asked of the network
            self.assertEqual(fetch_downloads.fetch(entries[:1] + entries[3:], tmp, self.fake_opener({})), [])
            missing = dict(entries[3], file="e.txt", path="release/e.txt")
            problems = fetch_downloads.fetch([missing], tmp, self.fake_opener({}))
            self.assertEqual(len(problems), 1)
            self.assertIn("The tag v1.3 of eric-cgn/parlornet-gamemaster has no file release/e.txt", problems[0])

    def test_build_needs_the_downloads(self):
        """With --require-downloads (as the workflow builds the site) a missing download stops the build and is
        named with its release; a file whose SHA-256 is not the manifest's stops any build."""
        import io
        import build_docs
        from docsite import downloads
        with Fixture(empty=True) as fx, tempfile.TemporaryDirectory() as tmp:
            err, old = io.StringIO(), sys.stderr
            sys.stderr = err
            try:
                self.assertEqual(build_docs.main(["--out", os.path.join(tmp, "site"), "--require-downloads"]), 1)
            finally:
                sys.stderr = old
            self.assertIn("missing download: eclipse-68k-fix.hqx from the v1.0 release of "
                          "eric-cgn/parlornet-eclipse-fix (https://github.com/eric-cgn/parlornet-eclipse-fix/"
                          "releases/download/v1.0/eclipse-68k-fix.hqx)", err.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "site")))
            with open(os.path.join(fx.folder, "eclipse-68k-fix.bin"), "wb") as f:
                f.write(b"not the release's file")
            downloads._cache.clear()
            err, out, old_out = io.StringIO(), io.StringIO(), sys.stdout
            sys.stderr, sys.stdout = err, out
            try:
                self.assertEqual(build_docs.main(["--out", os.path.join(tmp, "site")]), 1)
            finally:
                sys.stderr, sys.stdout = old, old_out
            self.assertIn("eclipse-68k-fix.bin has SHA-256", err.getvalue())

    def test_pages_without_the_downloads(self):
        """A site built without the downloads says so on each page, offers no download and links the READMEs in
        their repositories."""
        from docsite import eclipse, gamerulebooks, rulebook
        with Fixture(empty=True):
            self.assertEqual(eclipse.files(),
                             {"img/eclipse-68k-fix.gif": eclipse.static_file("/img/eclipse-68k-fix.gif")})
            self.assertEqual(rulebook.files(), {})
            self.assertEqual(gamerulebooks.files(), {})
            for body, repo, readme in (
                    (eclipse.eclipse_fix_body(), "parlornet-eclipse-fix", "eclipse-68k-fix-readme.txt"),
                    (gamerulebooks.body(), "parlornet-gamemaster", "gamemaster-rulebooks-readme.txt"),
                    (rulebook.body(), "parlornet-gamemaster", "parlornet-rulebook-readme.txt")):
                self.assertNotIn("/download/", body)
                self.assertIn("https://github.com/eric-cgn/%s/blob/main/release/%s" % (repo, readme), body)
                if readme != "parlornet-rulebook-readme.txt":       # the ParlorNet rulebook has no download table
                    self.assertIn("This copy of the site was built without them", body)
                    self.assertIn("tools/fetch_downloads.py", body)


class ClientsData(unittest.TestCase):
    """The clients data (adapters/clients.py) and the pages built from it."""

    YEAR_RE = r"^(?:c\. )?(?:19[89]\d|20[0-2]\d)$"

    def test_data(self):
        from adapters import clients as C
        from parlord import auth
        self.assertEqual(len(C.BY_ID), len(C.EVERY))
        for c in C.EVERY:
            self.assertRegex(c.id, r"^[a-z0-9-]+$")
            self.assertIn(c.kind, ("browser", "telnet", "period"))
            self.assertRegex(c.year, self.YEAR_RE, c.id)
            self.assertTrue(c.year_note.strip(), c.id)
            self.assertTrue(c.os.strip() and len(c.os) <= 48, c.id)
            self.assertTrue(c.runs, c.id)
            for plat, archs in c.runs:
                self.assertIn(plat, C.PLATFORM_NAMES, c.id)
                self.assertTrue((archs or c.unsupported()) and set(archs) <= set(C.ARCH_NAMES), c.id)
            self.assertTrue(c.get, c.id)
            for kind, source, url, note in c.get:
                self.assertIn(kind, C.KINDS, c.id)
                self.assertTrue(source.strip() and note.strip(), c.id)
                if url:
                    self.assertNotRegex(url, r'[\s"<>]', url)
                    u = urlsplit(url)
                    # ParlorNet's own FTP site is the one FTP address: it serves the files players need
                    self.assertIn(u.scheme, ("http", "https", "ftp") if url.startswith(C.FTP) else ("http", "https"),
                                  url)
                    self.assertEqual(kind == C.PARLORNET_FTP, url.startswith(C.FTP), url)
                    self.assertTrue(u.netloc, url)
            # the prose is on the client's page (docs/clients/<id>.md), not here
            self.assertFalse(c.setup or c.problems or c.works or c.lacks or c.extras, c.id)
            if c.kind == "period" and not c.planned() and not c.unsupported():
                self.assertTrue(c.games and c.port and c.style in C.SIGNIN, c.id)
            if c.planned():
                self.assertEqual(c.kind, "period", c.id)
                self.assertTrue(c.protocol and c.status_note and c.communities(), c.id)
                self.assertNotIn(c, C.ALL)
            if c.kind == "telnet":
                self.assertIn(c.has.get("arrows"), ("yes", "no", "not confirmed"), c.id)
                self.assertTrue(c.has.get("colour") and c.has.get("glyphs"), c.id)
        for style in C.SIGNIN.values():
            self.assertEqual(set(style) - {"new"}, {m for m, _ in C.SIGNIN_METHODS})
        self.assertEqual({m for m, _ in C.SIGNIN_METHODS}, set(auth.SIGNIN))

    def test_ratings(self):
        """Every rating is for one platform, its processors or build and a version, with an explanation, and a
        medal only from a real test: a date, and a client not marked untested or planned. A client marked
        untested or planned has no medal; every other native client has at least one, or is unsupported. Every
        place a client runs on has a rating or is "not yet rated" with what was tested instead."""
        from adapters import clients as C
        for c in C.EVERY:
            self.assertTrue(c.all_ratings(), c.id)
            runs = dict(c.runs)
            for r in c.all_ratings():
                self.assertIn(r.medal, list(C.MEDALS) + [C.NOT_RATED], c.id)
                self.assertIn(r.plat, runs, c.id)
                self.assertTrue(set(r.archs) <= set(runs[r.plat]), (c.id, r.archs))
                self.assertTrue(set(r.games) <= set(c.games), c.id)
                self.assertTrue(r.note.strip().endswith((".", ")", ".)")), (c.id, r.note))
                self.assertGreater(len(r.note), 60, c.id)               # an explanation, not a word
                if r.medal:
                    self.assertRegex(r.date, r"^2\d{3}-[01]\d-[0-3]\d$", c.id)
                    self.assertTrue(r.version, c.id)
                else:
                    self.assertEqual(r.date, "", c.id)
                if r.medal == C.SILVER:                                   # which patch, with a link to it
                    self.assertRegex(r.note, r"\[[^\]]*(Fix|Patch)[^\]]*\]\(/[^)]+\.html\)", c.id)
            covered = {(r.plat, x) for r in c.all_ratings() for x in r.archs}
            for plat, archs in c.runs:
                for x in archs:
                    self.assertIn((plat, x), covered, (c.id, plat, x))
            medals = [r for r in c.ratings if r.medal in (C.GOLD, C.SILVER, C.BRONZE)]
            if c.status in ("untested", "planned"):
                self.assertEqual(medals, [], c.id)
                for r in c.all_ratings():
                    self.assertTrue(r.note.endswith(C.A_REAL_TEST) or c.planned(), c.id)
            elif c.kind == "period":
                self.assertTrue(medals or c.unsupported(), c.id)
            if c.kind in ("browser", "telnet") and not medals:
                self.assertIn(C.A_REAL_TEST, c.unrated_note(), c.id)

    def test_unsupported_clients(self):
        """A program examined and ruled out has the no sign on every platform it runs on and an explanation of
        why, in plain words, and counts nowhere as a native client: not in the tables, the summary grid, the
        finder or the protocols."""
        from adapters import clients as C
        from docsite import build
        self.assertEqual({c.id for c in C.UNSUPPORTED_CLIENTS},
                         {"koth", "domewars", "xscrabble", "blackcat-rummytile"})
        for c in C.UNSUPPORTED_CLIENTS:
            self.assertTrue(c.unsupported(), c.id)
            self.assertTrue(all(r.medal == C.UNSUPPORTED for r in c.all_ratings()), c.id)
            self.assertEqual({r.plat for r in c.ratings}, {p for p, _ in c.runs}, c.id)
            for r in c.ratings:
                self.assertGreaterEqual(len(r.note.split()), 20, c.id)
            self.assertNotIn(c, C.ALL)
            self.assertFalse(c.port or c.protocol, c.id)
            self.assertEqual(c.label(), C.esc(c.name) + " (unsupported)")
        for c in C.EVERY:
            if c not in C.UNSUPPORTED_CLIENTS:
                self.assertFalse(c.unsupported(), c.id)
        for _, gtype in build.games():
            for _, plat, arch in C.MACHINES:
                self.assertFalse(set(C.clients_for(gtype, plat, arch)) & set(C.UNSUPPORTED_CLIENTS))

    def test_author_first_then_info_mac(self):
        """The author's own page comes before any archive; with no author's page, Info-Mac comes first."""
        from adapters import clients as C
        kind, source, url, _ = C.BY_ID["eclipse"].get[0]
        self.assertEqual((kind, source), (C.ARCHIVE, "Info-Mac"))
        self.assertTrue(url.startswith(C.INFO_MAC))
        macfibs = C.BY_ID["macfibs"].get           # MacFIBS 3 from its author's page, and nothing else
        self.assertEqual([(k, u) for k, _, u, _ in macfibs], [(C.AUTHOR, "http://www.fibs.com/macfibs/Download.html")])
        for c in C.EVERY:
            kinds = [k for k, _, _, _ in c.get]
            if C.AUTHOR in kinds and C.ARCHIVE in kinds:
                self.assertLess(kinds.index(C.AUTHOR), kinds.index(C.ARCHIVE), c.id)

    def test_period_ports_are_the_usual_ones(self):
        """Each period client's port is its server extension's usual one (adapters/catalog.py; the published
        port where the container port differs) and its protocol's in the reference."""
        from adapters import catalog, clients as C
        from docsite import clientpages, protocols
        usual = {k: port for k, _, port, *_ in protocols.PROTOCOLS}
        native = {"parlornet": 6501}        # the core's own protocol (parlord --port), served by no extension
        for c in C.PERIOD:
            port = (native.get(c.protocol) or catalog.PUBLISHED.get(c.protocol) or
                    dict((n, p) for n, _, p, _ in catalog.ADAPTERS)[c.protocol])
            self.assertEqual(c.port, port, c.id)
            self.assertEqual(c.port, usual[c.key()], c.id)
            page = clientpages.client_body(c)
            steps = page[page.index('<a name="setup">'):page.index('<a name="signin">')]
            self.assertRegex(steps, r"<tt>(?:[^<]|<i>host</i>)*\b%d</tt>" % c.port, c.id)
            for method, label in C.SIGNIN_METHODS:
                self.assertIn("<b>%s:</b>" % label, page)
            if c.protocol == "fibs":
                self.assertIn("<tt>guest</tt>", page)

    def test_getting_it_on_every_client_page(self):
        from adapters import clients as C
        from docsite import clientpages
        for c in C.EVERY:
            part = clientpages.client_body(c)
            self.assertIn(C.esc(c.os), part, c.id)
            self.assertIn("Getting it", part, c.id)
            gets = re.findall(r"<b>([^<:]+):</b> ", part)
            for kind, _, _, _ in c.get:
                self.assertIn(C.esc(kind[0].upper() + kind[1:]), gets, c.id)
            if c.kind == "telnet":
                self.assertIn("Sends arrow keys:", part, c.id)

    def test_a_server_marks_its_own_method(self):
        """signin_html marks a server's method when it is given one; the docs site gives none."""
        from adapters import clients as C
        self.assertIn("<b>Password (this server):</b>", C.signin_html("web", ("password", "Password", "password")))
        self.assertNotIn("(this server)", C.signin_html("web"))

    def test_original_servers_are_promoted(self):
        """Where a protocol's original server is still running, its entry, the setup page of each client that
        speaks it and each such client's row mention it, in one line."""
        from adapters import clients as C
        from docsite import build, protocols
        fibs = C.ORIGINALS["FIBS"]
        self.assertEqual((fibs.host, fibs.port, fibs.url), ("fibs.com", 4321, "http://fibs.com/"))
        from docsite import clientpages
        for c in C.ALL:
            row = C.client_rows([c])[0][0]
            if c.original():
                self.assertIn("FIBS is still running at %s." % C.a(c.original().url, C.esc(c.original().host)),
                              row, c.id)
                page = clientpages.client_body(c)
                community = page[page.index('<a name="community">'):]
                self.assertIn("<b>%s</b>." % C.a(c.original().url, C.esc(c.original().name)), community, c.id)
            else:
                self.assertNotIn("still running", row, c.id)
        self.assertTrue([c for c in C.PERIOD if c.key() == "FIBS" and c.original() is fibs])
        ref = protocols.protocols_body(build.games())
        entry = ref[ref.index('<a name="FIBS">'):ref.index('<a name="OUTLAND">')]
        self.assertLess(entry.index("FIBS is still running at"), entry.index("<b>Usual port:</b>"))   # first
        own = C.Client("x", "X", "period", "1999", "n", "Mac", (("mac", ("ppc",)),), ((C.ARCHIVE, "a", None, "n"),),
                       protocol="ios", original=C.Original("IOS", "http://example.org/", "example.org", 23))
        self.assertIn("IOS is still running at", C.original_html(own.original()))
        for page in (ref, C.frontends_body(build.games()), clientpages.client_body(C.BY_ID["macfibs"])):
            for word in ("public play", "private and event", "instead"):    # a mention, nothing more
                self.assertNotIn(word, page.lower())


class ClientPages(Built):
    """Each client's page: its facts from adapters/clients.py and its prose from docs/clients/<id>.md, with its
    known quirks, the groups still running for it, its screenshots and the link to edit it."""

    def test_every_client_has_a_page_and_every_page_a_client(self):
        from adapters import clients as C
        from docsite import clientpages
        self.assertEqual(clientpages.page_ids(), sorted(C.BY_ID))
        for cid in clientpages.page_ids():
            self.assertEqual(clientpages.outline_problems(cid), [], cid)

    def test_section_links_under_the_title(self):
        """Under every client page's title, a row links to each of its section headings in order, Getting it
        always among them, and each link lands on its heading's anchor."""
        from adapters import clients as C
        for c in C.EVERY:
            page = self.pages[c.href().lstrip("/")]
            after = page[page.index("</h1>") + len("</h1>\n"):]
            row = after[:after.index("</p>")]
            self.assertTrue(row.startswith("<p>"), c.id)
            links = re.findall(r'<a href="#([a-z]+)">([^<]+)</a>', row)
            heads = re.findall(r'<h2>(?:<a name="[a-z]+"></a>)*<a name="([a-z]+)">.*?>([^<>]+)</font></a></h2>', page)
            self.assertEqual(links, heads, c.id)
            self.assertIn(("get", "Getting it"), links, c.id)

    def test_every_page_has_known_quirks(self):
        """Every client says what is known to go wrong, or that nothing is known yet; the period clients that
        have been studied have real ones."""
        from adapters import clients as C
        from docsite import clientpages
        for c in C.EVERY:
            quirks = clientpages.section(c.id, "Known quirks")
            self.assertTrue(quirks == clientpages.NOTHING_KNOWN or quirks.startswith("- "), c.id)
            page = self.pages[c.href().lstrip("/")]
            self.assertIn('<a name="quirks">', page, c.id)
        for cid in ("eclipse", "macfibs", "outland", "olios", "fibsw", "xfibs", "qlink"):
            self.assertNotEqual(clientpages.section(cid, "Known quirks"), clientpages.NOTHING_KNOWN, cid)
        mf = clientpages.section("macfibs", "Known quirks")
        for words in ("Game > Refresh Board", "File > Disconnect", "3 sessions out of 10", "Use CLIP"):
            self.assertIn(words, mf)
        ecl = clientpages.section("eclipse", "Known quirks")
        for words in ("Eclipse 68k Fix", "8 letters and digits"):
            self.assertIn(words, ecl)
        self.assertIn("Your own double is refused", clientpages.section("outland", "Known quirks"))

    def test_screenshots(self):
        """Every screenshot is a small GIF in static/img/, under 200 pixels across, shown at its own size with its
        ALT text and its caption under it. A page has three or four of them, or says "None yet.", and none
        shows Grid Words."""
        from adapters import clients as C
        from docsite import clientpages
        seen = 0
        for c in C.EVERY:
            page = self.pages[c.href().lstrip("/")]
            lines = [l.strip() for l in clientpages.section(c.id, "Screenshots").split("\n") if l.strip()]
            shots = clientpages.screenshots(c.id)
            if lines == ["None yet."]:
                self.assertEqual(shots, [], c.id)
                self.assertNotIn('<a name="screenshots">', page, c.id)
                continue
            self.assertEqual(len(shots), len(lines), c.id)          # every line is a picture
            self.assertIn(len(shots), clientpages.SHOTS, c.id)
            for alt, caption, rel, site in shots:
                seen += 1
                self.assertTrue(rel.startswith("static/img/") and rel.endswith(".gif"), rel)
                # a client's own pictures, or a set the telnet programs, the period browsers or the current
                # browsers share
                self.assertRegex(rel, r"^static/img/(?:%s|telnet|web-1997|play)-[a-z0-9-]+\.gif$" % re.escape(c.id),
                                 rel)
                self.assertNotIn("gridwords", rel)
                self.assertNotIn("grid words", (alt + caption).lower(), rel)
                with open(os.path.join(ROOT, *rel.split("/")), "rb") as f:
                    data = f.read()
                self.assertIn(data[:6], (b"GIF87a", b"GIF89a"), rel)
                w, h = clientpages.gif_size(data)
                self.assertLess(w, 200, rel)
                self.assertLessEqual(w, clientpages.SHOT_WIDTH, rel)
                self.assertLessEqual(len(data), clientpages.SHOT_BYTES, rel)
                self.assertTrue(alt.strip() and caption.strip(), rel)
                self.assertEqual(self.files[site.lstrip("/")], data)
                self.assertIn('<img src="../%s" width="%d" height="%d" alt="[%s]"><br>\n' % (
                    site.lstrip("/"), w, h, C.esc(alt)), page, rel)
                self.assertIn(">%s</font></p>" % C.esc(caption), page, rel)
        self.assertGreaterEqual(seen, 6)

    def test_every_page_shows_and_explains_its_ratings(self):
        """Near the top of every client's page, before its facts and History: each rating with its icon (ALT
        its word, with its size), its word, where and which version, its explanation and its date; Silver
        links its patch, Unsupported says why, and "not yet rated" says what was tested instead."""
        from adapters import clients as C
        from docsite import clientpages
        for c in C.EVERY:
            page = plain(self.pages[c.href().lstrip("/")])
            top = page[page.index('<a name="rating">'):page.index("<b>Runs on:</b>")]
            self.assertLess(page.index('<a name="rating">'), page.index('<a name="history">'), c.id)
            self.assertIn('href="../index.html#medals"', top, c.id)
            self.assertEqual(top.count("<li>"), len(clientpages.rating_items(c)), c.id)
            for r in c.all_ratings():
                self.assertIn(C.esc(r.where()), top, c.id)
                self.assertIn(C.text(r.note).split("<a ")[0][:40], top, c.id)
                if r.medal:
                    word, src, _ = C.MEDALS[r.medal]
                    self.assertIn('<img src="..%s" width="%d" height="%d" alt="%s"> <b>%s</b>'
                                  % (src, C.ICON_SIZE, C.ICON_SIZE, word, word), top, c.id)
                    self.assertIn(r.date, top, c.id)
                else:
                    self.assertIn("<b>%s</b>" % C.NOT_RATED_WORD, top, c.id)
                if r.medal == C.SILVER:
                    self.assertRegex(top, r'<a href="\.\./[a-z0-9-]+\.html">[^<]*(Fix|Patch)', c.id)
                if r.medal == C.UNSUPPORTED:
                    self.assertTrue(r.note.strip(), c.id)
                    self.assertIn(C.text(r.note).split("<a ")[0], top, c.id)
        page = plain(self.pages["clients/eclipse.html"])
        self.assertIn('alt="Silver"> <b>Silver</b>, Mac OS (68020+), version 1.2', page)
        self.assertIn("<b>Not yet rated</b>, Mac OS (PowerPC), version 1.2", page)
        self.assertNotIn("<b>Gold</b>", page[:page.index('<a name="history">')])

    def test_images_have_alt_and_size(self):
        """Every picture on the docs site has ALT text and its size, so HTML 3.2 and text browsers lay it out
        and read it; a rating icon's ALT is its word, so color is never the only sign."""
        from adapters import clients as C
        words = {w for w, _, _ in C.MEDALS.values()}
        seen = 0
        for name, page in self.pages.items():
            for tag in re.findall(r"<img [^>]*>", page):
                self.assertRegex(tag, r' width="\d+" height="\d+"', (name, tag))
                self.assertRegex(tag, r' alt="[^"]*"', (name, tag))        # empty only for the emblem
                self.assertTrue('alt=""' not in tag or "/parlor.gif" in tag, (name, tag))
                if "/medal-" in tag or "unsupported.gif" in tag:
                    seen += 1
                    self.assertIn(re.search(r'alt="([^"]+)"', tag).group(1), words, (name, tag))
        self.assertGreater(seen, 30)

    def test_rating_icons(self):
        """The rating icons are ParlorNet's own: small GIFs with a transparent background, drawn by
        tools/medal_icons.py, committed as drawn and copied to img/."""
        import medal_icons
        from adapters import clients as C
        for medal, (word, src, means) in C.MEDALS.items():
            rel = "static" + src
            with open(os.path.join(ROOT, *rel.split("/")), "rb") as f:
                data = f.read()
            self.assertEqual(data[:6], b"GIF89a", rel)
            self.assertEqual((data[6] | data[7] << 8, data[8] | data[9] << 8), (C.ICON_SIZE, C.ICON_SIZE))
            self.assertIn(b"\x21\xf9\x04\x01", data, rel)              # a transparent color
            self.assertLess(len(data), 400, rel)
            self.assertEqual(medal_icons.ICONS[src.rsplit("/", 1)[1]](), data, rel)
            self.assertEqual(self.files[src.lstrip("/")], data, rel)

    def test_the_legend(self):
        """The Clients page and the contributors' note explain the four marks and "not yet rated"; the policy
        is in docs/clients.md, Ratings. The unsupported programs are listed apart, each with the reason it
        was ruled out, and never with a Setup link."""
        from adapters import clients as C
        index = self.content(self.pages["index.html"])
        legend = index[index.index('<a name="medals">'):index.index("<table")]
        for medal, (word, src, means) in C.MEDALS.items():
            self.assertIn('alt="%s"> <b>%s</b>: %s.' % (word, word, C.esc(means)), legend)
        self.assertIn("<b>%s</b>" % C.NOT_RATED_WORD, legend)
        self.assertIn('href="docs/clients.html#ratings"', legend)
        with open(os.path.join(ROOT, "docs", "clients", "README.md"), encoding="utf-8") as f:
            readme = f.read()
        with open(os.path.join(ROOT, "docs", "clients.md"), encoding="utf-8") as f:
            policy = f.read()
        for words in ("Gold", "Silver", "Bronze", "Unsupported", "not yet rated", "platform", "version"):
            self.assertIn(words, readme)
            self.assertIn(words, policy)
        self.assertIn("## Ratings", policy)
        unsupported = index[index.index('<a name="unsupported">'):index.index('<a name="generic">')]
        for c in C.UNSUPPORTED_CLIENTS:
            self.assertIn('<a href="%s"><b>%s</b></a>' % (c.href().lstrip("/"), C.esc(c.name)), unsupported)
            self.assertIn(C.text(c.ratings[0].note).split("<a ")[0], unsupported, c.id)
            self.assertNotIn('<a href="%s">setup</a>' % c.href().lstrip("/"), index)

    def test_edit_link(self):
        from adapters import clients as C
        from docsite import clientpages
        from parlord import PROJECT_URL
        for c in C.EVERY:
            url = "%s/edit/main/docs/clients/%s.md" % (PROJECT_URL, c.id)
            self.assertIn('<a href="%s">%s</a>' % (url, clientpages.EDIT_TEXT), self.pages[c.href().lstrip("/")])

    def test_communities(self):
        """A group still running is promoted on the page of each client its members use, and nowhere names
        ParlorNet; clients with none have no Community section."""
        from adapters import clients as C
        names = {g.name: g for g in C.COMMUNITIES}
        self.assertEqual(set(names), {"FIBS", "QuantumLink Reloaded", "NeoHabitat"})
        for g in C.COMMUNITIES:
            self.assertNotIn("ParlorNet", g.about)
            self.assertNotIn("instead", g.about)
            self.assertTrue([c for c in C.EVERY if g.serves(c)], g.name)
        want = {"macfibs": ["FIBS"], "fibsw": ["FIBS"], "xfibs": ["FIBS"], "xibc": ["FIBS"],
                "qlink": ["QuantumLink Reloaded", "NeoHabitat"]}
        for c in C.EVERY:
            page = self.pages[c.href().lstrip("/")]
            self.assertEqual([g.name for g in c.communities()], want.get(c.id, []), c.id)
            self.assertEqual('<a name="community">' in page, c.id in want, c.id)
            for g in c.communities():
                self.assertIn("<b>%s</b>." % C.a(g.url, C.esc(g.name)), page)
            if c.id in want:
                self.assertIn("Checked running on %s." % C.COMMUNITIES_CHECKED, page)
        self.assertIn('<tt>ATD&quot;q-link.net:5190&quot;</tt>', self.pages["clients/qlink.html"])

    def test_planned_clients(self):
        """Planned clients have pages and their own short part of the Clients page, not rows in the tables."""
        from adapters import clients as C
        index = plain(self.pages["index.html"])
        planned = index[index.index('<a name="planned">'):index.index('<a name="generic">')]
        self.assertGreater(index.index('<a name="planned">'), index.index('<a name="native">'))
        self.assertEqual({c.id for c in C.PLANNED}, {"qlink"})
        for c in C.PLANNED:
            self.assertIn('<a href="%s"><b>%s</b></a>' % (c.href().lstrip("/"), C.esc(c.name)), planned)
            self.assertNotIn('<a href="%s">setup</a>' % c.href().lstrip("/"), index)
            self.assertIn("<b>Status:</b> planned.", self.pages[c.href().lstrip("/")])

    def test_shared_pages_link_each_program(self):
        """Browsers and Telnet programs keep what every program needs and link to each one's page, under its
        old anchor."""
        from adapters import clients as C
        for name, clients in (("browsers.html", C.BROWSERS), ("telnet.html", C.TELNETS)):
            page = self.pages[name]
            for c in clients:
                link = '<a name="%s"></a><a href="%s"><b>%s</b></a>' % (c.id, c.href().lstrip("/"), C.esc(c.name))
                self.assertIn(link, page, c.id)
            for anchor in ("needs", "setup", "signin", "programs"):
                self.assertIn('<a name="%s">' % anchor, page, name)

    def test_old_setup_pages_point_to_the_new_ones(self):
        from adapters import clients as C
        for path, c in C.OLD_SETUP_PAGES.items():
            page = self.pages[path.lstrip("/")]
            self.assertIn('<meta http-equiv="refresh" content="0;URL=../clients/%s.html">' % c.id, page)
            self.assertIn('<a href="../clients/%s.html">' % c.id, page)

    def test_macfibs_3_is_the_one(self):
        """MacFIBS 3 is the recommendation: its name, version, year and only source agree everywhere."""
        from adapters import clients as C
        c = C.BY_ID["macfibs"]
        self.assertEqual((c.name, c.version, c.year), ("MacFIBS 3", "3 r6", "1998"))
        page = self.pages["clients/macfibs.html"]
        self.assertNotIn("Info-Mac", page[page.index('<a name="get">'):page.index('<a name="setup">')])
        self.assertNotIn("2.0.3", c.status_note + " ".join(g[3] for g in c.get))
        with open(os.path.join(ROOT, "docs", "clients.md"), encoding="utf-8") as f:
            self.assertIn("| MacFIBS 3 r6 (Paul D. Ferguson, 1998) |", f.read())

    def test_outland_gateway_steps(self):
        """The Outland Gateway's setup, as confirmed on a real Gateway 1.3.2."""
        page = htmllib.unescape(self.pages["clients/outland.html"])
        steps = page[page.index('<a name="setup">'):page.index('<a name="signin">')]
        for words in ("Settings > Connection", "Outland TCP Tool", "Extensions", "6506", "Registered user",
                      "File > Quit"):
            self.assertIn(words, steps)


if __name__ == "__main__":
    unittest.main()
