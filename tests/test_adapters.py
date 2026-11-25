"""Adapter tests against a live parlord: python3 -m unittest discover -s tests"""
import asyncio
import codecs
import http.client
import os
import re
import socket
import subprocess
import sys
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

sys.path.insert(0, ROOT)
from adapters import telnet as T  # noqa: E402
from speed import slow  # noqa: E402


def plain(html):
    """A page without its FONT tags (the site's faces and sizes), for checks on the markup around the text."""
    return re.sub(r"</?font[^>]*>", "", html)


def spawn(*args):
    p = subprocess.Popen([sys.executable, "-m"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    p.stdout.readline()
    return p


class Stack(unittest.TestCase):
    auth = "password"
    core_extra = ()

    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe, cls.web_port, cls.tel_port = free_port(), free_port(), free_port(), free_port()
        cls.keys = make_keys("web", "telnet")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", cls.auth, *cls.core_extra)]
        cls.procs.append(spawn(*adapter_args("adapters.web", cls.web_port, cls.fe, cls.keys, "web")))
        cls.procs.append(spawn(*adapter_args("adapters.telnet", cls.tel_port, cls.fe, cls.keys, "telnet")))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()

    def get(self, path, host="parlornet.org:6500"):
        c = http.client.HTTPConnection("127.0.0.1", self.web_port, timeout=5)
        c.request("GET", path, headers={"Host": host})
        r = c.getresponse()
        body = r.read()
        return r.status, r.getheader("Location"), r.getheader("Content-Type"), body.decode("latin-1")

    def follow(self, loc):
        u = urlsplit(loc)
        return self.get(u.path + ("?" + u.query if u.query else ""))

    def browser(self, cookies=True):
        return Browser(self.web_port, cookies)


class Browser:
    """An HTTP/1.0 browser with (or without) a cookie jar. It remembers every
    URL it saw (requests, redirects, links) and every page, to check that no
    credential ever shows up in them."""

    def __init__(self, port, cookies=True, ua=None):
        self.port, self.cookies, self.ua = port, cookies, ua     # ua: the User-Agent it sends, if any
        self.jar = {}
        self.urls, self.pages = [], []
        self.body = ""

    def request(self, method, path, form=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": "parlornet.org:6500"}
        if self.ua:
            headers["User-Agent"] = self.ua
        if self.cookies and self.jar:
            headers["Cookie"] = "; ".join("%s=%s" % kv for kv in self.jar.items())
        body = None
        if form is not None:
            from urllib.parse import urlencode
            body = urlencode(form)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        text = r.read().decode("latin-1")
        self.urls.append(path)
        for h, v in r.getheaders():
            if h.lower() == "set-cookie":
                k, _, rest = v.partition("=")
                val = rest.split(";")[0]
                self.last_cookie = v
                if "Max-Age=0" in v or not val:
                    self.jar.pop(k, None)
                else:
                    self.jar[k] = val
        loc = r.getheader("Location")
        if loc:
            self.urls.append(loc)
        self.pages.append(text)
        self.urls += [h.replace("&amp;", "&") for h in re.findall(r'href="([^"]+)"', text)]
        self.body = text
        return r.status, loc, r.getheader("Content-Type"), text

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, form):
        return self.request("POST", path, form)

    def go(self, loc):
        u = urlsplit(loc)
        return self.get(u.path + ("?" + u.query if u.query else ""))

    def login(self, name, password=None, follow=True):
        """The sign-in form (the password answers the server's credential
        question if it asks one). A name with no account is told so; then
        this makes the account (New account, signup()) and is signed in."""
        form = {"n": name}
        if password is not None:
            form["k"] = password
        st, loc, _, body = self.post("/login.htm", form)
        if st == 200 and "There is no account named" in body:
            return self.signup(name, password, follow)
        return self.steps(st, loc, body, password, follow)

    def signup(self, name, password=None, follow=True):
        """The New account form, then any password step (choose it, type it again) with password."""
        st, loc, _, body = self.post("/signup.htm", {"n": name})
        return self.steps(st, loc, body, password, follow)

    def steps(self, st, loc, body, password, follow):
        for _ in range(3):
            m = re.search(r'name="lid" value="([^"]+)"><input type="hidden" name="f" value="(password|confirm)"',
                          body)
            if st != 200 or not m or password is None:
                break
            st, loc, _, body = self.post("/login.htm", {"lid": m.group(1), "f": m.group(2), "v": password})
        if st == 302 and follow:
            return self.go(loc)
        return st, loc, None, body

    def link(self, pattern, body=None):
        """The first href matching pattern (a regex on the unescaped URL)."""
        for h in re.findall(r'href="([^"]+)"', body if body is not None else self.body):
            h = h.replace("&amp;", "&")
            if re.search(pattern, h):
                return h
        raise AssertionError("no link like %r in:\n%s" % (pattern, (body or self.body)[-1500:]))

    def keep(self):
        """The session and action fields this browser's links carry (x=, and s= without cookies)."""
        q = parse_qs(urlsplit(self.link(r"[?&]x=")).query)
        return "&".join("%s=%s" % (k, q[k][0]) for k in ("x", "s") if k in q)

    def act(self, path):
        return self.get(path + ("&" if "?" in path else "?") + self.keep())



class Native:
    """A plain-protocol connection to the core's public port, signed in by password: the name, then the
    password when the server asks for it (twice for a name that is new: sign-up). For tests that need
    the core's own view of a game a browser is playing."""

    def __init__(self, port, name, password):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        self.f = self.s.makefile("rb")
        self.f.readline()
        r = self.req("HELO %s" % name)[-1]
        if r.startswith("ASK password"):
            r = self.req("AUT password %s" % password)[-1]
        if r.startswith("ASK confirm"):
            r = self.req("AUT confirm %s" % password)[-1]
        assert r == "OK -", r

    def req(self, text):
        self.s.sendall(text.encode() + b"\r\n")
        out = []
        while True:
            l = self.f.readline().decode().rstrip("\r\n")
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def close(self):
        self.f.close()
        self.s.close()


class WebPlayers:
    """For adapter tests that need a web player: web_login(name) signs a
    browser in (with the password name-Pass-word) and returns that password;
    web_get(path) sends a path from the browser of the player named by n=
    (n= only picks the browser and is not sent), with its session and x=; a
    path without n=, such as a link from a page, goes from the last player's
    browser."""

    def web_login(self, name):
        if not hasattr(self, "browsers"):
            self.browsers = {}
        b = Browser(self.web)
        b.login(name, name + "-Pass-word")
        b.x = parse_qs(urlsplit(b.link(r"[?&]x=")).query)["x"][0]
        self.browsers[name] = b
        return name + "-Pass-word"

    def web_get(self, path):
        u = urlsplit(path)
        q = parse_qs(u.query)
        name = q.pop("n", [None])[0]
        if name:                    # a player's request; a link from their last page goes with them
            b = self.last_browser = self.browsers[name]
            q["x"] = [b.x]
        else:
            b = getattr(self, "last_browser", None) or Browser(self.web)
        from urllib.parse import urlencode
        st, loc, _, body = b.get(u.path + "?" + urlencode(q, doseq=True))
        return st, loc, body


class Web(Stack):
    def test_root_and_welcome(self):
        st, loc, _, _ = self.get("/")
        self.assertEqual(st, 302)
        self.assertTrue(loc.endswith("/welcome.htm"))
        st, _, ct, body = self.get("/welcome.htm")
        self.assertEqual((st, ct), (200, "text/html"))
        self.assertNotIn("6505", body)          # only what this server speaks now (test_protocols_list.py)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("<style", body.lower())

    def test_old_documentation_addresses(self):
        """The documentation is on the docs site now: each old address here answers with a 301 to its page
        there (the Frontends page keeping its filter), its body a plain HTML 3.2 page with the link; with
        site.docs_url cleared, a page that says the documentation is not served here."""
        from adapters import web as W
        from parlord import DOCS_URL
        for old, new in (("/frontends.htm", "index.html"), ("/frontends.htm?plat=mac&arch=68000",
                                                             "index.html?plat=mac&arch=68000"),
                         ("/browsers.htm", "browsers.html"), ("/telnet.htm", "telnet.html"),
                         ("/setup/eclipse.htm", "setup/eclipse.html"), ("/docs/index.htm", "docs.html"),
                         ("/docs/protocol.htm", "docs/protocol.html"), ("/docs/types/reversi-1.htm",
                                                                         "docs/types/reversi-1.html"),
                         ("/eclipse-68k-fix.htm", "eclipse-68k-fix.html"),
                         ("/download/eclipse-68k-fix.hqx", "download/eclipse-68k-fix.hqx"),
                         ("/img/eclipse-68k-fix.gif", "img/eclipse-68k-fix.gif")):
            st, loc, ct, body = self.get(old)
            self.assertEqual((st, loc, ct), (301, DOCS_URL + new, "text/html"), old)
            self.assertTrue(body.startswith('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">'), old)
            self.assertIn('<a href="%s">' % (DOCS_URL + new).replace("&", "&amp;"), body)
        for path in ("/docs/../LICENSE", "/docs/../LICENSE.htm", "/setup/../x.htm", "/download/x.bin"):
            self.assertNotEqual(self.get(path)[0], 301, path)
        self.assertNotIn("Permission is hereby granted", self.get("/docs/../LICENSE")[3])
        old = W.S.DOCS[0]
        try:
            W.S.DOCS[0] = ""
            page = W.moved_page({"headers": {}}, "/browsers.htm").decode("ascii")
            self.assertTrue(page.startswith("HTTP/1.0 404 "))
            self.assertIn("The documentation is not served by this server.", page)
            self.assertNotIn(">Docs</a>", page)
        finally:
            W.S.DOCS[0] = old

    def test_welcome_games_list(self):
        st, _, _, body = self.get("/welcome.htm")
        self.assertEqual(st, 200)
        low = body.lower()
        for bad in ("<script", "<style", "style=", "javascript:", "&#x"):
            self.assertNotIn(bad, low)
        b = plain(body)
        self.assertLess(b.index('<a name="play">'), b.index('<a name="games">'))
        self.assertEqual(b.count(">Games</a></h2>"), 1)
        section = b[b.index('<a name="games">'):]
        rows = re.findall(r'<a href="/games/([a-z0-9]+)\.htm"><b>([^<]+)</b></a><br>([^<]*)</td>', section)
        self.assertEqual([r[1] for r in rows],
                         ["Reversi", "Backgammon", "Grid Words", "Cannon Fire", "Dots and Boxes", "Five in a Row",
                          "Mancala", "Checkers", "Naval Combat", "Rummy Tile Classic"])
        by = {r[1]: r for r in rows}
        # Reversi and Backgammon are playable because the core lists them in GAM, with its player counts
        self.assertEqual(by["Reversi"], ("reversi", "Reversi", "2 players"))
        self.assertEqual(by["Backgammon"], ("backgammon", "Backgammon", "2 players"))
        from adapters import games
        for name in list(by)[2:]:
            if by[name][0] in games.by_type():  # built: a frontend module and its plugin, so the core hosts it
                self.assertNotIn(",", by[name][2])
            else:
                self.assertTrue(by[name][2].endswith(", planned"), by[name])
        self.assertIn('<a href="/games.htm">Games</a>', section)

    def test_games_table_follows_gam(self):
        from adapters import games, web as W
        from adapters.common import parse_games
        built = games.by_type()         # games with a frontend module count as built until the core says not

        def statuses(gam):
            html = plain(W.games_table_html(parse_games(gam) if gam is not None else None))
            return {name: (note.split(", ", 1) + ["playable"])[1]
                    for name, note in re.findall(r"<b>([^<]+)</b>(?:</a>)?<br>([^<]*)</td>", html)}
        self.assertEqual(statuses(None)["Reversi"], "playable")      # core not reached yet: static list
        for name, gtype, _, _ in W.GAMES[2:]:
            self.assertEqual(statuses(None)[name], "playable" if gtype in built else "planned")
        s = statuses(["GAM reversi/1 standard Reversi 2 2", "GAM gridwords/1 english Grid_Words 2 4 nomoves",
                      "GAM go/1 standard Go 2 2"])
        self.assertEqual((s["Reversi"], s["Grid Words"], s["Go"]), ("playable", "playable", "playable"))
        self.assertEqual(s["Backgammon"], "not on this server")
        for name, gtype, _, _ in W.GAMES[3:]:
            self.assertEqual(s[name], "not on this server" if gtype in built else "planned")
        page = plain(W.games_page(parse_games(["GAM gridwords/1 english Grid_Words 2 4 nomoves"])).decode("ascii"))
        self.assertIn('<td align="center">2-4</td><td>playable</td>', page)

    def test_games_page(self):
        """/games.htm: a row per game in build order with an anchor each (old links to a game's section land
        on its row), linking to the game's own page; status as on the welcome page. Each game's page: players,
        status, frontends, the rules; Backgammon's says where FIBS is and that ParlorNet is not FIBS. HTML 3.2
        only, and generic names only."""
        from adapters import web as W
        from parlord import DOCS_URL
        st, _, ct, page = self.get("/games.htm")
        self.assertEqual((st, ct), (200, "text/html"))
        order = ["Reversi", "Backgammon", "Grid Words", "Cannon Fire", "Dots and Boxes", "Five in a Row",
                 "Mancala", "Checkers", "Naval Combat", "Rummy Tile Classic"]
        rows = re.findall(r'<a name="([a-z0-9]+)"></a><a href="/games/\1\.htm"><b>([^<]+)</b></a></td>'
                          r'<td align="center">([^<]*)</td><td>([^<]*)</td>', plain(page))
        self.assertEqual([r[1] for r in rows], order)
        self.assertEqual(len(set(r[0] for r in rows)), 10)
        welcome = plain(self.get("/welcome.htm")[3])
        links = re.findall(r'<a href="/games/([a-z0-9]+)\.htm"><b>([^<]+)</b></a>', welcome)
        self.assertEqual(links, [(r[0], r[1]) for r in rows])     # the welcome list links each game's page
        pages = {}
        for anchor, name, players, status in rows:
            st, _, _, part = self.get("/games/%s.htm" % anchor)
            self.assertEqual(st, 200, anchor)
            pages[anchor] = part = plain(part)
            self.assertIn("<h1>%s</h1>" % name, part)
            self.assertIn("<b>Status here:</b> %s<br>" % status, part)
            self.assertIn("<b>Players:</b> %s<br>" % players, part)
            self.assertIn('<a href="%sindex.html#%s">Clients page</a>' % (DOCS_URL, anchor), part)
            self.assertIn('<a href="/protocols.htm">Protocols</a>', part)
            self.assertIn('<a href="/games.htm#%s">' % anchor, part)
        self.assertEqual(self.get("/games/nosuch.htm")[0], 200)        # not a game: the welcome page
        self.assertNotIn("<b>Players:</b>", self.get("/games/nosuch.htm")[3])
        bg = pages["backgammon"]
        self.assertIn("Crawford", bg)
        self.assertIn("gammon", bg)
        self.assertIn('<a href="http://fibs.com/">fibs.com</a>', bg)
        self.assertIn("port 4321", bg)
        self.assertIn("ParlorNet is not FIBS and is not affiliated with it.", bg)
        for client in ("MacFIBS", "FIBS/W", "xfibs", "xibc"):
            self.assertIn(client, bg)
        self.assertIn("8 by 8", pages["reversi"])
        self.assertIn("10 by 10", pages["reversi"])
        self.assertIn('href="%sdocs/types/reversi-1.html"' % DOCS_URL, pages["reversi"])
        self.assertIn('href="%sdocs/pnp.html#reversi"' % DOCS_URL, pages["reversi"])
        self.assertIn('href="%sdocs/pnp.html#grid-words"' % DOCS_URL, pages["gridwords"])
        for html in [page] + list(pages.values()):
            for mark in ("Othello", "Scrabble", "Battleship", "Rummikub", "Kalah", "Scorched"):
                self.assertNotIn(mark, html)
            low = html.lower()
            for bad in ("<script", "<style", "style=", "class=", "javascript:", "&#x", "<div", "<span"):
                self.assertNotIn(bad, low)
            self.assertEqual(re.findall(r"&(?!(?:amp|lt|gt|quot|nbsp|#\d+);)", html), [])
            self.assertTrue(all(ord(ch) < 128 for ch in html))
        self.assertEqual(W.game_rows()[1]["type"], "backgammon")

    def test_gifs(self):
        for name in "ebwh":
            st, _, ct, body = self.get("/img/%s.gif" % name)
            self.assertEqual((st, ct), (200, "image/gif"))
            self.assertTrue(body.startswith("GIF89a"))
            self.assertTrue(body.endswith("\x00;"))

    def test_two_players_play_to_the_end(self):
        br = {who: self.browser() for who in ("wanda", "walt")}
        for who, b in br.items():
            st, _, _, body = b.login(who, who + "-Pass-word")
            self.assertEqual(st, 200)
            self.assertIn("You are <b>%s</b>" % who, body)
        st, _, _, body = self.browser().login("wanda", "not-hers")
        self.assertIn("Wrong password", body)                     # name taken -> welcome with the reason
        b = br["wanda"]
        st, loc, _, _ = b.act("/new.htm?g=reversi%2F1")
        self.assertEqual(st, 302)
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        st, loc, _, body = br["walt"].act("/join.htm?t=%s" % tid)
        self.assertEqual(st, 302)
        for _ in range(90):
            moved = False
            for name in ("wanda", "walt"):
                st, _, _, body = br[name].get("/table.htm?t=%s" % tid)
                self.assertEqual(st, 200)
                m = re.search(r'href="(/move\.htm\?[^"]+)"', body)
                if m:
                    st, loc, _, _ = br[name].get(m.group(1).replace("&amp;", "&"))
                    self.assertEqual(st, 302)
                    moved = True
                    break
            if not moved:
                break
        st, _, _, body = br["wanda"].get("/table.htm?t=%s" % tid)
        self.assertIn("Game over", body)
        self.assertNotIn('http-equiv="refresh"', body)  # finished: no auto refresh
        for name in ("wanda", "walt"):
            self.no_credentials(br[name], [name + "-Pass-word"])

    def no_credentials(self, b, secrets_=()):
        """Nothing a browser saw (URLs, redirects, links, pages) carries a
        credential or, with cookies, the session id."""
        sid = b.jar.get("pn")
        for u in b.urls:
            q = parse_qs(urlsplit(u).query)
            self.assertNotIn("k", q, u)
            self.assertNotIn("n", q, u)
            if b.cookies:
                self.assertNotIn("s", q, u)
            for x in secrets_:
                self.assertNotIn(x, u)
        for page_ in b.pages:
            for x in secrets_:
                self.assertNotIn(x, page_)
            if sid and b.cookies:
                self.assertNotIn(sid, page_)

    def test_session_cookie_and_sign_out(self):
        b = self.browser()
        st, _, _, body = b.post("/login.htm", {"n": "sol", "k": "Sol-secret"})   # a new name: sign-in only says so
        self.assertIn("There is no account named sol here", body)
        self.assertNotIn('name="lid"', body)                                     # no sign-up started
        self.assertIn('<a href="/signup.htm">New account</a>', body)
        st, _, _, body = b.post("/signup.htm", {"n": "sol"})                     # New account: its own steps
        self.assertIn("<h1>", body)
        self.assertIn("New account", body[body.index("<h1>"):])
        lid = re.search(r'name="lid" value="([^"]+)"', body).group(1)
        self.assertIn("Choose a password", body)
        st, _, _, body = b.post("/login.htm", {"lid": lid, "f": "password", "v": "Sol-secret"})
        lid = re.search(r'name="lid" value="([^"]+)"', body).group(1)
        self.assertIn("Type it again", body)
        st, loc, _, _ = b.post("/login.htm", {"lid": lid, "f": "confirm", "v": "Sol-secret"})
        self.assertEqual(st, 302)
        self.assertRegex(b.last_cookie, r"^pn=[A-Za-z0-9_-]{22}; Path=/; HttpOnly; SameSite=Lax$")
        self.assertNotIn("pn=", loc)
        self.assertRegex(loc, r"/lobby\.htm\?h=[A-Za-z0-9_-]+$")    # a one-time handle, not the session
        st, _, _, body = b.go(loc)
        self.assertIn("You are <b>sol</b>", body)
        self.assertNotIn("s=", b.link(r"/lobby\.htm"))                 # cookies work: links carry no session
        st, _, _, body = b.go(loc)                                      # the handle is used up; the cookie stands
        self.assertIn("You are <b>sol</b>", body)
        self.assertIn("Sign out everywhere", body)
        self.assertIn("Change your password", body)                      # a flow of the server's steps
        self.assertNotIn("token", body.lower())
        # the credential never goes in a URL: GET sign-in is ignored
        st, _, _, body = self.browser().get("/login.htm?n=sol&k=Sol-secret")
        self.assertNotIn("You are", body)
        self.assertIn("<h1>Sign in</h1>", plain(body))
        self.assertNotIn("sol", body)
        # actions need the session's x=: a bare link from another site does nothing
        st, _, _, body = b.get("/new.htm?g=reversi%2F1")
        self.assertIn("That link is out of date", body)
        st, _, _, body = b.get("/new.htm?g=reversi%2F1&x=0123456789abcdef")
        self.assertIn("That link is out of date", body)
        # a second browser signed in as sol; sign out here ends only this one
        b2 = self.browser()
        b2.login("sol", "Sol-secret")
        b.get("/lobby.htm")
        st, _, _, body = b.get(b.link(r"/out\.htm\?(?!.*all=1)"))
        self.assertIn("Signed out.", body)
        self.assertNotIn("pn", b.jar)
        old = b.last_cookie
        self.assertIn("Max-Age=0", old)
        self.assertIn("You are <b>sol</b>", b2.get("/lobby.htm")[3])
        # change the password: the server's steps as pages; the other browsers are signed out, this one stays
        b3 = self.browser()
        b3.login("sol", "Sol-secret")
        b2.get("/lobby.htm")
        st, _, _, body = b2.get(b2.link(r"/password\.htm"))
        self.assertIn("Current password", body)
        for field, value in (("current", "Sol-secret"), ("password", "Sol-new-1"), ("confirm", "Sol-new-1")):
            lid = re.search(r'name="lid" value="([^"]+)"><input type="hidden" name="f" value="%s"' % field,
                            body).group(1)
            st, loc, _, body = b2.post("/login.htm", {"lid": lid, "f": field, "v": value})
        self.assertEqual(st, 302)
        self.assertIn("Your password is changed", b2.go(loc)[3])
        self.assertIn("Your session has ended", b3.get("/lobby.htm")[3])
        self.assertIn("Wrong password", self.browser().login("sol", "Sol-secret")[3])
        # sign out everywhere
        b4 = self.browser()
        time.sleep(0)
        st, _, _, body = b2.get(b2.link(r"/out\.htm\?.*all=1"))
        self.assertIn("Signed out everywhere.", body)
        self.assertNotIn("You are", b4.get("/lobby.htm")[3])
        for x in (b, b2, b3):
            self.no_credentials(x, ["Sol-secret", "Sol-new-1"])

    def test_cookieless_browser(self):
        b = self.browser(cookies=False)
        st, _, _, body = b.login("cora", "Cora-secret")
        self.assertIn("You are <b>cora</b>", body)
        lobby = b.link(r"/lobby\.htm")
        sid = parse_qs(urlsplit(lobby).query)["s"][0]                  # the session rides in links
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{22}$")
        st, loc, _, _ = b.act("/new.htm?g=reversi%2F1")
        self.assertEqual(st, 302)
        self.assertIn("s=" + sid, loc)
        self.assertIn("Waiting for an opponent", b.go(loc)[3])
        self.no_credentials(b, ["Cora-secret"])
        # the handle after sign-in works once, and only briefly
        st, _, _, body = b.get(b.urls[1])
        self.assertNotIn("You are", body)
        # signing out ends it
        b.get(lobby)
        b.get(b.link(r"/out\.htm"))
        self.assertIn("Your session has ended", b.get(lobby)[3])

    def test_welcome_sections_and_protocols_page(self):
        """The welcome page's sections come in order, each anchored, with the old section names kept as
        anchors; the protocols page is one table of what this server speaks now (test_protocols_list.py); no
        page has CSS, scripts, hex character references or the admin port."""
        from adapters import catalog
        from adapters import web as W
        st, _, _, body = self.get("/welcome.htm")
        self.assertEqual(st, 200)
        names = re.findall(r'<a name="([^"]+)">', body)
        self.assertEqual(len(names), len(set(names)))
        for anchor in re.findall(r'<a href="#([^"]+)">', body):   # every in-page link on the page
            self.assertIn(anchor, names)
        b = plain(body)
        self.assertEqual([n for n, _, _ in W.SECTIONS], ["play", "ways-in", "games"])
        order = [b.index('<a name="%s">' % n) for n, _, _ in W.SECTIONS]
        self.assertEqual(order, sorted(order))
        self.assertLess(b.index("<h1>Welcome</h1>"), order[0])
        if '<a name="motd">' in b:                                     # the message of the day before Play now
            self.assertLess(b.index('<a name="motd">'), order[0])
        for anchor, title, _ in W.SECTIONS:
            self.assertRegex(b, r'<h2>(?:<a name="[a-z-]+"></a>)*<a name="%s">%s</a></h2>' % (anchor, title))
        # old section names land on Other ways in
        self.assertIn('<h2><a name="frontends"></a><a name="clients"></a><a name="protocols"></a>'
                      '<a name="ways-in">Other ways in</a></h2>', b)
        st, _, _, page = self.get("/protocols.htm")
        self.assertEqual(st, 200)
        pp = plain(page)
        self.assertEqual(pp.count("<table"), 3)                         # masthead, the list, footer
        self.assertIn('<th align="left">Key</th><th align="right">Port</th>', pp)
        admin = dict((n, p) for n, _, p, _ in catalog.ADAPTERS)["admin"]
        for page_ in (body, page):
            self.assertNotIn(str(admin), page_)    # operators' console: never its port
            low = page_.lower()
            for bad in ("<script", "<style", "style=", "javascript:", "&#x", "<div", "<span"):
                self.assertNotIn(bad, low)
            self.assertEqual(re.findall(r"&(?!(?:amp|lt|gt|quot|nbsp|#\d+);)", page_), [])

    def test_table_refresh(self):
        b = self.browser()
        b.login("rita", "rita-Pass-word")
        st, loc, _, _ = b.act("/new.htm?g=reversi%2F1")
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        base = "/table.htm?t=%s" % tid
        body = b.get(base)[3]
        self.assertIn('content="5;URL=', body)            # default, waiting for a player
        self.assertIn("Refresh every:", body)
        self.assertIn("<script", body)                    # optional enhancement
        body = b.get(base + "&r=30")[3]
        self.assertIn('content="30;URL=', body)
        self.assertIn("r=30", body)                       # links keep the choice
        self.assertNotIn("http-equiv", b.get(base + "&r=0")[3])
        body = b.get(base + "&js=1")[3]
        self.assertNotIn("http-equiv", body)              # script does the updating
        self.assertIn("Updates automatically", body)
        st, _, ct, seq = b.get("/seq.htm?t=%s" % tid)
        self.assertEqual(ct, "text/plain")
        self.assertTrue(seq.isdigit())
        self.no_credentials(b, ["rita-Pass-word"])

    def test_backgammon_on_the_web(self):
        br = {who: self.browser() for who in ("bea", "bob")}
        for who, b in br.items():
            b.login(who, who + "-Pass-word")
        self.assertIn("Start a game of Backgammon", br["bea"].body)
        st, loc, _, _ = br["bea"].act("/new.htm?g=backgammon%2F1&turn=1h")
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        br["bob"].act("/join.htm?t=%s" % tid)
        played = 0
        for _ in range(12):
            for who in ("bea", "bob"):
                body = br[who].get("/table.htm?t=%s&r=0" % tid)[3]
                self.assertIn('<img src="/board.gif?', body)               # the board: one image
                self.assertEqual(len(set(re.findall(r'<img src="([^"]+)"', body))), 1)
                self.assertNotIn("&#x", body)
                m = re.search(r'href="(/move\.htm\?[^"]+)"', body)
                if m:
                    st, loc, _, _ = br[who].get(m.group(1).replace("&amp;", "&"))
                    self.assertEqual(st, 302)
                    played += 1
                    break
                e = re.search(r'href="(/end\.htm\?[^"]+)"', body)
                if e:
                    self.assertEqual(br[who].get(e.group(1).replace("&amp;", "&"))[0], 302)
                    break
        self.assertGreater(played, 3)
        for k in ("/img/bg/trw0.gif", "/img/bg/bck4.gif", "/img/bg/d6.gif"):
            st, _, ct, data = self.get(k)
            self.assertEqual((st, ct), (200, "image/gif"))

    def test_private_table_and_invitation(self):
        br = {who: self.browser() for who in ("pia", "quin", "ros")}
        for who, b in br.items():
            b.login(who, who + "-Pass-word")
        body = br["pia"].get("/lobby.htm")[3]
        self.assertIn('<input type="radio" name="vis" value="private">', body)
        self.assertNotIn("Invitations for you", body)
        st, loc, _, _ = br["pia"].act("/new.htm?g=reversi%2F1&turn=1h&vis=private")
        self.assertEqual(st, 302)
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        body = br["pia"].get("/table.htm?t=%s&r=0" % tid)[3]
        self.assertIn("This table is private", body)
        self.assertIn('<form action="/invite.htm" method="get">', body)
        self.assertIn('<input type="hidden" name="t" value="%s">' % tid, body)
        # outsiders neither see the table nor get in
        self.assertNotIn("/join.htm?t=%s&" % tid, br["ros"].get("/lobby.htm")[3])
        body = br["ros"].act("/join.htm?t=%s" % tid)[3]
        self.assertIn("by invitation only", body)
        body = br["pia"].act("/invite.htm?t=%s&who=nosuch" % tid)[3]
        self.assertIn("No player by that name", body)
        st, loc, _, _ = br["pia"].act("/invite.htm?t=%s&who=quin&r=0" % tid)
        self.assertEqual(st, 302)
        body = br["pia"].go(loc)[3]
        self.assertIn("Invited: quin", body)
        self.assertIn("quin is invited", body)
        body = plain(br["quin"].get("/lobby.htm")[3])
        inv = body[body.index("<h2>Invitations for you</h2>"):body.index("<h2>Tables</h2>")]
        self.assertIn("<td>pia</td>", inv)
        self.assertIn("open, private", body)
        m = re.search(r'href="(/join\.htm\?[^"]+)"><b>Join</b>', inv)
        st, loc, _, _ = br["quin"].get(m.group(1).replace("&amp;", "&"))
        self.assertEqual(st, 302)
        self.assertEqual(parse_qs(urlsplit(loc).query)["t"], [tid])
        body = br["quin"].go(loc)[3]
        self.assertIn("/board.gif?", body)                            # the game has started
        self.assertNotIn("Invitations for you", br["quin"].get("/lobby.htm")[3])

    def test_bad_session_and_escaping(self):
        st, _, _, body = self.get("/lobby.htm?n=wanda&k=nope")
        self.assertNotIn("You are", body)
        self.assertNotIn("&#x", body)
        st, _, _, body = self.get("/lobby.htm?s=AAAAAAAAAAAAAAAAAAAAAA")
        self.assertIn("Your session has ended", body)
        st, _, _, body = self.browser().login("<script>", "x")
        self.assertNotIn("<script>", body)

    def fetch(self, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.web_port, timeout=5)
        c.request(method, path, headers={"Host": "parlornet.org:6500"})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.getheader("Content-Length"), r.read()

class NewTableWeb(Stack):
    """The lobby's one form per game, with a rules select for a game with several rulesets and a players
    select for one that takes more than two; /new.htm sends them on NEW."""

    def test_rules_and_players_in_the_lobby(self):
        nia, ozzy = self.browser(), self.browser()
        body = nia.login("nia", "nia-Pass-word")[3]
        for name in ("Mancala", "Five in a Row", "Naval Combat", "Reversi", "Rummy Tile Classic"):
            self.assertEqual(body.count('value="Start a game of %s"' % name), 1, name)
        forms = {re.search(r'value="Start a game of ([^"]+)"', f).group(1): f
                 for f in body.split("<li><form")[1:]}
        self.assertIn('<select name="ru"><option value="kalah" selected>Store rules<option value="oware">'
                      'Oware rules</select>', forms["Mancala"])
        self.assertIn('<option value="standard">Standard: exactly five wins', forms["Five in a Row"])
        self.assertIn('<option value="batnav">Batnav', forms["Naval Combat"])
        self.assertIn('<select name="np"><option value="2" selected>2<option value="3">3<option value="4">4'
                      '</select>', forms["Rummy Tile Classic"])
        self.assertIn('<option value="8">8</select>', forms["Cannon Fire"])
        for simple in ("Reversi", "Checkers", "Backgammon"):
            self.assertNotIn("<select name=\"ru\"", forms[simple])
            self.assertNotIn("<select name=\"np\"", forms[simple])
        for f in forms.values():                            # each form carries the action check, x=
            self.assertIn('<input type="hidden" name="x" value="', f)
        st, loc, _, _ = nia.act("/new.htm?g=mancala%2F1&ru=oware&turn=1h")
        self.assertEqual(st, 302)
        tid = parse_qs(urlsplit(loc).query)["t"][0]
        st, loc, _, _ = nia.act("/new.htm?g=rummytile%2F1&np=3")
        self.assertEqual(st, 302)
        st, loc, _, _ = nia.act("/new.htm?g=navalcombat%2F1&ru=batnav")
        self.assertEqual(st, 302)
        body = nia.get("/lobby.htm")[3]
        self.assertIn("<td>Mancala</td>", plain(body))
        self.assertIn("<td>1/3</td>", plain(body))                  # Rummy Tile with three seats, nia in one
        ozzy.login("ozzy", "ozzy-Pass-word")
        self.assertEqual(ozzy.act("/join.htm?t=%s" % tid)[0], 302)
        self.assertIn("Oware rules", nia.get("/table.htm?t=%s" % tid)[3])
        self.assertIn("those rules", nia.act("/new.htm?g=mancala%2F1&ru=nope")[3])
        self.assertIn("players is 2 to 4", nia.act("/new.htm?g=rummytile%2F1&np=9")[3])
        self.assertIn("out of date", nia.get("/new.htm?g=mancala%2F1&ru=oware")[3])    # no x=: nothing made
        self.assertEqual(plain(nia.get("/lobby.htm")[3]).count("<td>Mancala</td>"), 1)


@slow
class WebExpiry(Stack):
    """Sessions run out: a cookieless browser's s= stops working after the idle limit."""
    auth = "none"

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        settings = os.path.join(cls.tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("session.idle_secs=2\n")
        cls.core_extra = ("--settings-file", settings)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.tmp.cleanup()

    def test_cookieless_session_expires(self):
        b = self.browser(cookies=False)
        b.login("ezra")
        lobby = b.link(r"/lobby\.htm\?s=")
        self.assertIn("You are <b>ezra</b>", b.get(lobby)[3])
        time.sleep(3)
        self.assertIn("Your session has ended", b.get(lobby)[3])


class MailCapture:
    """For a stack whose core sends mail with email.transport=test: the mails, read back from the JSON lines
    of email.test_file. Nothing here prints a mail or a password."""

    @classmethod
    def mail_settings(cls, extra=""):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.mailfile = os.path.join(cls.tmp.name, "mail.jsonl")
        settings = os.path.join(cls.tmp.name, "settings")
        with open(settings, "w") as f:
            f.write("email.transport=test\nemail.test_file=%s\n%s" % (cls.mailfile, extra))
        return settings

    def mails(self, addr):
        import json
        try:
            with open(self.mailfile, encoding="utf-8") as f:
                rows = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            return []
        return [m for m in rows if m["to"] == addr]

    def mailed_password(self, addr, n=1):
        """The temporary password in the nth mail to addr (the core writes it before it answers)."""
        end = time.time() + 10
        while time.time() < end:
            got = self.mails(addr)
            if len(got) >= n:
                m = re.search(r"Password:\s+(\S+)", got[n - 1]["body"])
                if m:
                    return m.group(1)
                raise AssertionError("mail %d to %s holds no password" % (n, addr))
            time.sleep(0.05)
        raise AssertionError("no mail %d to %s" % (n, addr))


@slow
class PasswordEmail(MailCapture, Stack):
    """Sign-in password with sign-up email, through the web and telnet: sign-up mails a temporary password,
    the first sign-in with it asks for a password of the player's own before anything else, that password
    signs in later, and a forgotten password is mailed a new temporary one."""
    auth = "password"

    @classmethod
    def setUpClass(cls):
        # every test here asks from 127.0.0.1: room for all of their mails in the per-address and server caps
        settings = cls.mail_settings("email.limit.per_source_hour=50\nemail.limit.per_source_day=200\n"
                                     "email.limit.per_minute=10\n")
        cls.blocked = os.path.join(cls.tmp.name, "blocked")
        with open(settings, "a") as f:
            f.write("email.blocked_file=%s\n" % cls.blocked)
        with open(cls.blocked, "w") as f:
            f.write("# one address a line\nbounced@example.org\n")
        cls.core_extra = ("--signup", "email", "--settings-file", settings)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.tmp.cleanup()

    def step(self, b, body, field, value):
        lid = re.search(r'name="lid" value="([^"]+)"', body).group(1)
        return b.post("/login.htm", {"lid": lid, "f": field, "v": value})

    def assert_never_seen(self, browsers, secrets_):
        """No URL a browser saw holds a password (said without the password, if it fails)."""
        for b in browsers:
            for u in b.urls:
                self.assertTrue(not any(x in u or x.replace(" ", "+") in u or x.replace("'", "%27") in u
                                        for x in secrets_), "a password in a URL")
                self.assertNotIn("k=", u)

    def test_web(self):
        b = self.browser()
        st, _, _, body = b.login("pem", "Pem-Pass-word")     # the field is no answer to these steps
        self.assertIn("New account: pem.", body)               # (no account: New account, its own page)
        self.assertIn("We email you a password to sign in with; then you choose your own.", body)
        st, _, _, body = self.step(b, body, "email", "bounced@example.org")   # from the blocked file
        self.assertIn("Mail to that address cannot be delivered", body)
        self.assertIn("<h1>New account</h1>", plain(body))     # back to the New account page, not sign-in
        self.assertIn("and we email you a password. The first time you sign in with it, you choose a "
                      "password of your own.", body)
        st, _, _, body = b.signup("pem")
        st, _, _, body = self.step(b, body, "email", "pem@example.org")
        self.assertIn("We emailed a password for pem to pem@example.org.", body)
        self.assertIn("Password from the email:", body)
        self.assertIn('type="password"', body)
        temp = self.mailed_password("pem@example.org")
        mail = self.mails("pem@example.org")[0]
        self.assertEqual(mail["subject"], "Your ParlorNet password")
        self.assertTrue("Name:      pem" in mail["body"] and "It works for 24 hours." in mail["body"], "the mail")
        # the first sign-in with it: a password of pem's own, before anything else
        st, _, _, body = self.step(b, body, "password", temp)
        self.assertEqual(st, 200)
        self.assertIn("That was a temporary password. Choose a password of your own now.", body)
        self.assertIn("New password:", body)
        st, _, _, body = self.step(b, body, "password", "weakweak")
        self.assertIn("That password uses only one of the four kinds.", body)
        st, _, _, body = self.step(b, body, "password", "Pem's own 1")
        self.assertIn("Type it again:", body)
        st, loc, _, body = self.step(b, body, "confirm", "Pem's own 1")
        self.assertEqual(st, 302)
        self.assertIn("You are <b>pem</b>", b.go(loc)[3])
        c = self.browser()                                     # then a password sign-in
        self.assertIn("You are <b>pem</b>", c.login("pem", "Pem's own 1")[3])
        # forgotten: reset in the password box mails a new temporary password; the old one still works
        self.assertIn("Forgot your password? Type reset in the password box", self.get("/login.htm")[3])
        d = self.browser()
        st, _, _, body = d.post("/login.htm", {"n": "pem", "k": "reset"})
        self.assertIn("We emailed a new password to the address on file.", body)
        self.assertIn("your old password works until you use it.", body)
        again = self.mailed_password("pem@example.org", 2)
        self.assertEqual(self.mails("pem@example.org")[1]["subject"], "Your new ParlorNet password")
        self.assertIn("You are <b>pem</b>", self.browser().login("pem", "Pem's own 1")[3])
        st, _, _, body = self.step(d, body, "password", again)
        self.assertIn("Choose a password of your own now.", body)
        st, _, _, body = self.step(d, body, "password", "Pem's own 2")
        st, loc, _, body = self.step(d, body, "confirm", "Pem's own 2")
        self.assertEqual(st, 302)
        self.assertIn("You are <b>pem</b>", d.go(loc)[3])
        self.assertIn("You are <b>pem</b>", self.browser().login("pem", "Pem's own 2")[3])
        self.assert_never_seen((b, c, d), (temp, again, "Pem's own 1", "Pem's own 2"))

    def test_web_sign_in_later_with_the_mailed_password(self):
        """The sign-up page left at the mailed password's question; the Sign in form's password box takes it,
        and the next page asks for a password of the player's own."""
        b = self.browser()
        st, _, _, body = b.signup("pia")
        self.step(b, body, "email", "pia@example.org")
        temp = self.mailed_password("pia@example.org")
        e = self.browser()
        st, _, _, body = e.post("/login.htm", {"n": "pia", "k": temp})
        self.assertEqual(st, 200)
        self.assertIn("<h1>Sign in</h1>", plain(body))
        self.assertIn("Choose a password of your own now.", body)
        st, _, _, body = self.step(e, body, "password", "Pia's own 1")
        st, loc, _, body = self.step(e, body, "confirm", "Pia's own 1")
        self.assertEqual(st, 302)
        self.assertIn("You are <b>pia</b>", e.go(loc)[3])
        self.assert_never_seen((b, e), (temp, "Pia's own 1"))

    def test_telnet(self):
        a = Client(self.tel_port)
        a.until("Login")
        a.send("tem\r\n")                                        # no account: said so, never a sign-up
        a.until("There is no account named tem here. To make one, type new")
        a.send("NEW\r\n")                                        # new, any case: the sign-up
        a.until("Name you want")
        a.send("tem\r\n")
        a.until("New account: tem.")
        a.until("Email address")
        a.send("tem@example.org\r\n")
        a.until("We emailed a password for tem to tem@example.org.")
        a.until("Password from the email (or press Enter to stop): ")
        temp = self.mailed_password("tem@example.org")
        a.send(temp + "\r\n")
        a.until("That was a temporary password. Choose a password of your own now.")
        a.until("New password (or press Enter to stop): ")
        a.send("short\r\n")
        a.until("That password is too short.")
        a.until("New password")
        a.send("Tem pass 1\r\n")
        a.until("Type it again")
        a.send("Tem pass 1\r\n")
        a.until("Choice [")
        a.send("4\r\n")
        a.until("Press Enter")
        a.send("\r\n")
        a.until("Your tables")
        raw = a.raw.decode("latin-1")
        self.assertTrue(temp not in raw and "Tem pass 1" not in raw, "a password was echoed")
        a.close()
        b = Client(self.tel_port)                                # later: the name and the new password
        b.until("Login")
        b.send("tem\r\n")
        b.until("Password (or press Enter")
        b.send("Tem pass 1\r\n")
        b.until("Choice [")
        b.close()

    def test_telnet_stopping_before_the_new_password(self):
        a = Client(self.tel_port)
        a.until("Login")
        a.send("new\r\n")
        a.until("Name you want")
        a.send("tod\r\n")
        a.until("Email address")
        a.send("tod@example.org\r\n")
        a.until("Password from the email")
        a.send("\r\n")                                           # later, then
        a.until("Login")
        a.send("tod\r\n")
        a.until("Type the password we emailed you.")
        a.until("Password (or press Enter")
        temp = self.mailed_password("tod@example.org")
        a.send(temp + "\r\n")
        a.until("New password")
        a.send("\r\n")
        a.until("Your password is not chosen yet. Sign in again with the password from the email to choose it.")
        a.until("Login")
        a.send("tod\r\n")
        a.until("Password (or press Enter")
        a.send(temp + "\r\n")                                    # it still works until a password is chosen
        a.until("New password")
        a.send("Tod's own 1\r\n")
        a.until("Type it again")
        a.send("Tod's own 1\r\n")
        a.until("Choice [")
        a.close()


class WebNone(Stack):
    """--auth none: a name alone, but still a session in a cookie, sign-out, and nothing in links."""
    auth = "none"

    def test_name_alone_through_a_session(self):
        b = self.browser()
        st, _, _, body = b.post("/login.htm", {"n": "nia"})     # sign-in only, even with names alone
        self.assertIn("There is no account named nia here", body)
        self.assertNotIn("pn", b.jar)
        st, loc, _, _ = b.post("/signup.htm", {"n": "nia"})    # New account: a name alone is the account
        self.assertEqual(st, 302)
        self.assertRegex(b.last_cookie, r"^pn=[A-Za-z0-9_-]{22};")
        body = b.go(loc)[3]
        self.assertIn("You are <b>nia</b>", body)
        self.assertNotIn("token", body.lower())
        for u in b.urls:
            self.assertNotIn("n=nia", u)
        b.get(b.link(r"/out\.htm"))
        self.assertNotIn("You are", b.get("/lobby.htm")[3])
        st, loc, _, _ = b.post("/login.htm", {"n": "nia"})       # now the name has an account: it signs in
        self.assertEqual(st, 302)
        self.assertIn("You are <b>nia</b>", b.go(loc)[3])
        for word in ("new", "Guest"):                               # reserved: nobody can sign up as these
            body = b.post("/signup.htm", {"n": word})[3]
            self.assertIn("is a reserved word here; choose another name", body)


class VT:
    """A minimal ANSI terminal, 80x24: enough to replay what the telnet adapter
    sends. Once the screen has been cleared (full-screen mode) it records
    anything that would scroll or address a cell outside the screen."""

    def __init__(self, enc="cp437"):
        self.dec = codecs.getincrementaldecoder(enc)("replace")
        self.scr = [[" "] * 80 for _ in range(24)]
        self.x = self.y = 0
        self.armed = False
        self.errors = []
        self.esc = None
        self.iac = 0
        self.sb = False

    def feed(self, data):
        out = bytearray()
        for b in data:                      # telnet commands first
            if self.iac == 0:
                if b == 255:
                    self.iac = 1
                elif not self.sb:
                    out.append(b)
            elif self.iac == 1:
                self.iac = 2 if b in (251, 252, 253, 254) else 0
                if b == 255 and not self.sb:
                    out.append(b)
                elif b == 250:
                    self.sb = True
                elif b == 240:
                    self.sb = False
            else:
                self.iac = 0
        for c in self.dec.decode(bytes(out)):
            self.put(c)

    def newline(self):
        if self.y == 23:
            if self.armed:
                self.errors.append("scrolled")
            self.scr.pop(0)
            self.scr.append([" "] * 80)
        else:
            self.y += 1

    def put(self, c):
        if self.esc is not None:
            if self.esc == "":
                self.esc = "[" if c == "[" else None
            elif "@" <= c <= "~":
                self.csi(self.esc[1:], c)
                self.esc = None
            else:
                self.esc += c
            return
        if c == "\x1b":
            self.esc = ""
        elif c == "\r":
            self.x = 0
        elif c == "\n":
            self.newline()
        elif c == "\b":
            self.x = max(0, min(self.x, 79) - 1)
        elif c >= " " and c != "\x7f":
            if self.x >= 80:
                self.x = 0
                self.newline()
            self.scr[self.y][self.x] = c
            self.x += 1

    def csi(self, params, f):
        nums = [int(p) if p.isdigit() else 0 for p in params.split(";")] if params else []
        if f in "Hf":
            r = nums[0] if nums and nums[0] else 1
            col = nums[1] if len(nums) > 1 and nums[1] else 1
            if self.armed and not (1 <= r <= 24 and 1 <= col <= 80):
                self.errors.append("cursor to %d;%d" % (r, col))
            self.y, self.x = min(r, 24) - 1, min(col, 80) - 1
        elif f == "C":
            n = nums[0] if nums and nums[0] else 1
            if self.armed and self.x + n > 79:
                self.errors.append("cursor right past column 80")
            self.x = min(79, self.x + n)
        elif f == "J" and nums == [2]:
            self.scr = [[" "] * 80 for _ in range(24)]
            self.armed = True
        elif f == "K":
            for i in range(min(self.x, 80), 80):
                self.scr[self.y][i] = " "

    def lines(self):
        return ["".join(r).rstrip() for r in self.scr]

    def text(self):
        return "\n".join(self.lines())


class Client:
    """A raw socket to the telnet adapter (no telnet negotiation, so the
    adapter falls back to its default after the terminal-type wait). It
    presses Enter at the title page, as a player does (title=False leaves
    the page up)."""

    def __init__(self, port, enc="cp437", ttype=None, title=True):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.s.settimeout(0.2)
        self.s.sendall(bytes([255, 251, 1, 255, 253, 3]))  # IAC WILL ECHO, IAC DO SGA: must be ignored
        if ttype:                                           # IAC WILL TTYPE, IAC SB TTYPE IS <type> IAC SE
            self.s.sendall(bytes([255, 251, 24, 255, 250, 24, 0]) + ttype.encode() + bytes([255, 240]))
        self.raw = b""
        self.mark = 0
        self.vt = VT(enc)
        self.closed = False
        self.title = title          # Enter at the title page, once

    def send(self, data):
        self.s.sendall(data.encode("latin-1") if isinstance(data, str) else data)

    def pump(self):
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return
        if not d:
            self.closed = True
            return
        self.raw += d
        self.vt.feed(d)
        if self.title and b"Press Enter" in self.raw:
            self.title = False
            self.send("\r\n")

    def until(self, text, timeout=8, screen=False):
        """Wait for text in what arrived since the last match, or (screen=True)
        on the emulated screen. Returns what was searched."""
        end = time.time() + timeout
        while True:
            hay = self.vt.text() if screen else self.raw[self.mark:].decode("latin-1")
            i = hay.find(text)
            if i >= 0:
                self.mark = len(self.raw) if screen else self.mark + i + len(text)
                return hay
            if time.time() > end or self.closed:
                raise AssertionError("%r not seen; %s:\n%s" % (
                    text, "screen" if screen else "last output", hay if screen else hay[-600:]))
            self.pump()

    def gone(self, text, timeout=8):
        """Wait until text is no longer on the emulated screen."""
        end = time.time() + timeout
        while text in self.vt.text():
            if time.time() > end or self.closed:
                raise AssertionError("%r still on screen:\n%s" % (text, self.vt.text()))
            self.pump()

    def find(self, text):
        """(column, row), 1-based, of text on the emulated screen."""
        for y, line in enumerate(self.vt.lines()):
            x = line.find(text)
            if x >= 0:
                return x + 1, y + 1
        raise AssertionError("%r not on screen:\n%s" % (text, self.vt.text()))

    def click(self, x, y):
        """A left click at a 1-based cell, as an xterm reports it (SGR, 1006)."""
        self.send("\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x, y, x, y))

    def close(self):
        self.s.close()


class TelnetBase(Stack):
    def login(self, c, name, choice, new=True):
        """Sign in (or, new=True, make a new account: "new", then the name) by
        the server's steps; returns the output up to the menu."""
        c.until("Login")
        if new:
            c.send("new\r\n")
            c.until("Name you want")
        c.send(name + "\r\n")
        if self.auth == "password":
            pw = name + "-Pass-word"
            c.until("Choose a password" if new else "Password (or press Enter")
            c.send(pw + "\r\n")
            if new:
                c.until("Type it again")
                c.send(pw + "\r\n")
        c.until("Choice [")
        c.send(choice + "\r\n")
        out = c.until("Press Enter")
        c.send("\r\n")
        c.until("Your tables", screen=choice != "4")
        return out


@slow
class Telnet(TelnetBase):
    def test_play_by_telnet(self):
        a, b = Client(self.tel_port), Client(self.tel_port)
        self.login(a, "tess", "4")                       # plain text
        a.send("new reversi 5m\r\n")
        a.until("Waiting for an opponent")
        self.login(b, "tom", "")                        # default: PC characters, full screen
        b.send("join 1\r\n")
        b.until("\xbb")                                 # CP437 double-line corner
        self.assertIn("\x1b[", b.raw.decode("latin-1"))
        b.until("you play white", screen=True)
        out = a.until("Your move (like", timeout=8)
        self.assertIn("a  b  c  d  e  f  g  h", out)
        self.assertIn("tess", out)
        self.assertIn("left to move (limit 5:00 a turn)", out)
        a.send("d3\r\n")
        a.until("tess plays d3")
        b.send("a1\r\n")                                # typed moves work in full screen too
        b.until("Can't play a1", screen=True)
        b.send("say hi\r\n")
        a.until("<tom> hi", timeout=8)
        b.send("\x1b")                                  # Esc: back to the menu, game goes on
        b.until("Your tables", screen=True)
        b.send("quit\r\n")
        b.until("Log off?", screen=True)
        b.send("y\r\n")
        b.until("Bye.")
        b.close()
        self.assertEqual(b.vt.errors, [])
        a.send("redraw\r\n")
        out = a.until("> ")
        self.assertNotIn("resigned", out)
        c = Client(self.tel_port)                       # tom comes back with his password
        out = self.login(c, "tom", "4", new=False)
        self.assertNotIn("tom-Pass-word", out)          # never echoed
        c.send("join 1\r\n")
        c.until("you play white")
        a.send("resign\r\n")
        a.until("Resign this game?")
        a.send("y\r\n")
        a.until("tess resigned")
        c.until("tess resigned", timeout=8)
        for s in (a, c):
            s.close()


class TelnetNone(TelnetBase):
    """--auth none: a name alone; still no token anywhere."""
    auth = "none"

    def test_name_alone(self):
        a = Client(self.tel_port)
        out = self.login(a, "noel", "")
        a.send("?")
        a.until("this help", screen=True)
        self.assertNotIn("token", a.vt.text().lower())
        self.assertNotIn("token", a.raw.decode("latin-1").lower())
        self.assertNotIn("password", a.raw.decode("latin-1").lower())
        a.close()


@slow
class TelnetFullScreen(TelnetBase):
    """The ANSI menu and board, driven by raw key bytes."""

    UP, DOWN, RIGHT, LEFT, ESC = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D", "\x1b"

    def test_menu_and_board_by_keys(self):
        a, b = Client(self.tel_port), Client(self.tel_port, enc="utf-8")
        self.login(a, "ann", "")                         # no terminal type: PC characters
        a.until("Start a new Reversi table", screen=True)
        a.until("No other tables yet", screen=True)
        a.send("n")                                      # N: new table, time-per-turn picker
        a.until("Time per turn", screen=True)
        a.send(self.UP)                                  # 1 day (default) -> 1 hour
        a.send("\r")
        a.until("Waiting for an opponent", screen=True)
        a.until("Time per turn: 1 hour", screen=True)
        a.send(self.ESC)                                 # menu; the table stays open
        scr = a.until("waiting for an opponent", screen=True)
        self.assertIn("1 hour", scr)

        self.login(b, "bob", "2")                        # Unicode characters
        b.until("open: Enter joins", screen=True)
        b.send(self.DOWN * new_rows())                   # past the "new table" rows to ann's table
        b.until("Table 1: ann (with telnet) waits for an opponent", screen=True)   # with what ann plays
        b.send("\r")                                     # Enter: sit down
        b.until("you play white", screen=True)
        b.until("ann to move", screen=True)
        self.assertIn("\u2554", b.vt.text())             # Unicode board frame
        self.assertNotIn("\ufffd", b.vt.text())

        a.send("r")                                      # R: refresh the menu
        a.until("It is your move at table 1", screen=True)
        a.send(self.UP)                                  # select the table, Enter opens it
        a.until("Enter goes back to your game against bob (with telnet).", screen=True)
        a.send("\r")
        a.until("Your move. d3", screen=True)            # cursor on a legal square
        self.assertIn("bob sat down with telnet", a.vt.text(), "the JOIN and its CLIENT, one line in the log")
        a.send(self.RIGHT)
        a.until("e3 is not a legal square", screen=True)
        a.send("\r")
        a.until("Can't play e3", screen=True)
        a.send(self.LEFT)
        a.until("Your move. d3", screen=True)
        a.send(" ")                                      # Space places
        a.until("ann plays d3 (flips 1)", screen=True)
        b.until("ann plays d3 (flips 1)", screen=True)
        b.until("Your move", screen=True)

        b.send("say hello\r")                            # typed command
        a.until("<bob> hello", screen=True)
        b.send("r")                                      # R: resign asks first
        b.until("Resign this game?", screen=True)
        b.send("n")
        b.gone("Resign this game?")
        self.assertNotIn("resigned", b.vt.text())

        for c in (a, b):
            c.send(self.ESC)
            c.until("Your tables", screen=True)
            c.send("q")
            c.until("Log off?", screen=True)
            c.send("y")
            c.until("Bye.")
            c.close()
            self.assertEqual(c.vt.errors, [])


def wait_any(clients, texts, timeout=10):
    """Wait until one of texts is on one of the clients' screens -> (client, text)."""
    end = time.time() + timeout
    while True:
        for c in clients:
            scr = c.vt.text()
            for t in texts:
                if t in scr:
                    return c, t
        if time.time() > end:
            raise AssertionError("none of %r seen:\n%s" % (texts, "\n\n".join(c.vt.text() for c in clients)))
        for c in clients:
            c.pump()


def new_rows():
    """The telnet menu's "Start a new ... table" rows: one per game with a frontend module (and its plugin)."""
    from adapters import games
    return len(games.all_games())


@slow
class TelnetBackgammon(TelnetBase):
    """Backgammon by telnet: a table started from the menu, steps played
    with keys, the turn ended, the other player sees it."""

    DOWN, ESC = "\x1b[B", "\x1b"

    def test_backgammon_by_keys(self):
        a, b = Client(self.tel_port), Client(self.tel_port)
        self.login(a, "bea", "")                          # PC characters
        a.until("Start a new Backgammon table", screen=True)
        a.send(self.DOWN)                                 # from the Reversi row to the Backgammon row
        a.until("Enter starts a new Backgammon table", screen=True)
        a.send("\r")
        a.until("Time per turn", screen=True)
        a.send("\r")                                      # the default, 1 day
        a.until("Waiting for an opponent", screen=True)
        self.assertIn("Backgammon", a.vt.lines()[0])

        self.login(b, "bert", "3")                        # ASCII characters
        b.until("open: Enter joins", screen=True)
        b.send(self.DOWN * new_rows())
        b.until("bea (with telnet) waits for an opponent", screen=True)
        b.send("\r")
        b.until("you play black (X)", screen=True)
        self.assertIn("(O)", b.vt.text())                 # ASCII checkers and frame
        self.assertIn("+==================+===+", b.vt.text())

        m, _ = wait_any((a, b), ["Your move"])            # the opening roll decides who starts
        o = b if m is a else a
        who = "bea" if m is a else "bert"
        o.until("%s to move" % who, screen=True)
        steps = 0
        for _ in range(5):
            if "Nothing left to play" in m.vt.text():
                break
            m.send(" ")                                   # Space picks up a checker that can move
            m.until("Enter moves", screen=True)           # ... and the cursor is on its nearest target
            m.send("\r")                                  # Enter puts it down
            steps += 1
            m.mark = len(m.raw)
            wait_any((m,), ["Enter picks up", "Nothing left to play"])
        self.assertGreaterEqual(steps, 2)
        m.until("E end turn", screen=True)                # the status bar offers it now
        m.send("e")
        m.until("Waiting for", screen=True)
        o.until("%s moves" % who, screen=True)            # the other player sees the steps
        o.until("Your turn: Enter rolls the dice", screen=True)
        o.send("\r")                                      # Enter rolls
        other = "bert" if m is a else "bea"
        o.until("%s rolls" % other, screen=True)
        m.until("%s rolls" % other, screen=True)

        for c in (a, b):
            c.send(self.ESC)
            c.until("Your tables", screen=True)
            c.send("q")
            c.until("Log off?", screen=True)
            c.send("y")
            c.until("Bye.")
            c.close()
            self.assertEqual(c.vt.errors, [])
            self.assertNotIn("\x1b[?1000h", c.raw.decode("latin-1"))   # no terminal type: no mouse


@slow
class TelnetMouse(TelnetBase):
    """An xterm gets mouse reports switched on; clicks drive the menu, the
    pickers and the Reversi board."""

    DOWN = "\x1b[B"

    def test_clicks(self):
        a = Client(self.tel_port, enc="utf-8", ttype="xterm-256color")
        b = Client(self.tel_port)
        self.login(a, "mia", "")                          # xterm: Unicode by default
        self.assertIn("\x1b[?1000h\x1b[?1006h", a.raw.decode("latin-1"))
        a.until("Enter starts a new Reversi table", screen=True)
        a.click(*a.find("Start a new Reversi table"))    # selected already: a click opens it
        a.until("Time per turn", screen=True)
        a.click(*a.find("1 hour"))                        # a click picks an option
        a.until("Waiting for an opponent", screen=True)
        a.until("Time per turn: 1 hour", screen=True)

        self.login(b, "max", "")
        b.until("open: Enter joins", screen=True)
        b.send(self.DOWN * new_rows())
        b.until("Table 1: mia (with telnet (xterm-256color)) waits. Enter sits down", screen=True)
        b.send("\r")
        b.until("you play white", screen=True)

        a.until("Your move. d3", screen=True)
        # cells are 4 columns apart from column 7, rows 2 apart from row 5 (1-based)
        a.click(7 + 4 * 4, 5 + 2 * 2)                     # e3: not legal, the cursor goes there
        a.until("e3 is not a legal square", screen=True)
        a.click(7 + 4 * 3, 5 + 2 * 2)                     # d3: legal, a click plays it
        a.until("mia plays d3 (flips 1)", screen=True)
        b.until("mia plays d3 (flips 1)", screen=True)

        a.send("\x1b")
        a.until("Your tables", screen=True)
        a.click(*a.find("Q quit"))                        # status bar items are buttons
        a.until("Log off?", screen=True)
        a.click(*a.find("Y  yes"))
        a.until("Bye.")
        self.assertIn("\x1b[?1000l\x1b[?1006l", a.raw.decode("latin-1"))
        self.assertEqual(a.vt.errors, [])
        for c in (a, b):
            c.close()


# ---- offline: every full screen at 80x24 ---------------------------------------------

class FakeIO:
    """Stands in for TelnetIO: output goes to a VT, keys come from a list,
    and every screen shown while waiting for a key is kept."""

    def __init__(self, mode, keys=()):
        self.charset = T.CHARSET[mode]
        self.vt = VT(self.charset)
        self.keys = list(keys)
        self.screens = []
        self.server_echo = False

    def write(self, text):
        self.vt.feed(text.encode(self.charset))          # strict: must be in the charset

    async def flush(self):
        pass

    async def readkey(self, timeout=None):
        self.screens.append(self.vt.lines())
        while self.keys and self.keys[0] == "WAIT":     # a pause: the key before it was a hotkey
            self.keys.pop(0)
        if not self.keys:
            raise EOFError
        return self.keys.pop(0)

    async def poll(self, wait):
        return bool(self.keys)


class FakeCore:
    auth = "password"

    def __init__(self, tabs):
        self.tabs = tabs

    async def req(self, text):
        verb = text.split()[0]
        if verb == "GAM":
            return ["GAM reversi/1 standard Reversi 2 2"], "OK"
        if verb == "LST":
            return self.tabs, "OK"
        if verb == "VUE":
            return ["V reversi/1 9", "CLK 1 3599 86400"], "OK"
        return [], "OK"

    async def pwd(self):
        from adapters.common import NeedAuth
        raise NeedAuth("current", "Current password", True, ["Type your password first."])

    async def aut(self, field, value):
        return "sid"


LONG = "abcdefghijkl"
TABS = ["TAB 3 reversi/1 standard 2/2 playing %s,%s turn=300" % (LONG, LONG[::-1]),
        "TAB 5 reversi/1 standard 1/2 open %s,- turn=86400" % LONG] + [
        "TAB %d reversi/1 standard 2/2 playing wanda%d,%s turn=2592000" % (10 + i, i, LONG) for i in range(12)] + [
        "TAB 40 reversi/1 standard 1/2 open walt,- turn=60"]


def view(n, rows_filled=True, turn=0, over=False):
    rows = []
    for y in range(n):
        rows.append("R %d %s" % (y + 1, "".join("01."[(x * 3 + y) % 3] for x in range(n))))
    lines = ["V reversi/1 40", "BRD %d %d" % (n, n)]
    if rows_filled:
        lines += rows + ["C 30 29", "W %d" % (-1 if over else turn), "S x"]
        if not over:
            lines += ["CLK %d 45 300" % turn] + ["M %s%d" % (chr(97 + i), i + 1) for i in range(n)]
    return T.parse_reversi_view(lines)


class TelnetScreens(unittest.TestCase):
    """Render every full screen in every ANSI mode (and the plain board) and
    check it: 24 rows of at most 80 columns, nothing drawn off the canvas or
    in the bottom-right cell, nothing that scrolls, and only characters the
    mode's character set has."""

    def session(self, mode, keys=(), tabs=TABS):
        io = FakeIO(mode, keys)
        s = T.Session(io, FakeCore(tabs), mode, LONG)
        frame = s.painter.frame

        def checked(cv, cursor=None, full=False):
            self.assertFalse(cv.clipped, "drawn outside 80x24")
            self.assertEqual(len(cv.ch), 24)
            self.assertTrue(all(len(r) == 80 for r in cv.ch))
            out = frame(cv, cursor, full)
            self.assertNotIn("\n", out)
            return out
        s.painter.frame = checked
        return s, io

    def check(self, io):
        self.assertEqual(io.vt.errors, [])
        self.assertTrue(io.screens)
        for scr in io.screens:
            self.assertEqual(len(scr), 24)
            self.assertTrue(all(len(l) <= 80 for l in scr))
            self.assertLessEqual(len(scr[23]), 79)

    def run_menu(self, mode, keys, tabs=TABS):
        s, io = self.session(mode, keys, tabs)
        m = T.AnsiMenu(s)
        with self.assertRaises(EOFError):
            asyncio.run(m.run())
        self.check(io)
        return io

    def test_menus(self):
        keys = ["DOWN"] * 20 + ["UP"] * 3 + ["PGUP", "END", "HOME", "?", "WAIT", "x",
                                             "p", "WAIT", "a", "b", "BS", "ENTER", "n", "WAIT", "DOWN", "ESC",
                                             "q", "WAIT", "n", "x", "y", "z", "REDRAW"]
        for mode in ("pc", "utf8", "ansi"):
            io = self.run_menu(mode, keys)
            text = "\n".join("\n".join(s) for s in io.screens)
            for want in ("Your tables", "Other tables", "more", "Log off?", "Time per turn", "Current password",
                         "Type your password first.", "Your password is changed", "their move", "your move", "> xyz"):
                self.assertIn(want, text, mode)
            empty = self.run_menu(mode, ["DOWN"], tabs=[])
            self.assertIn("No other tables yet", "\n".join(empty.screens[-1]))

    def test_boards(self):
        for mode in ("pc", "utf8", "ansi"):
            for n in (4, 6, 8, 10):
                for me, filled, turn, over in ((0, True, 0, False), (1, True, 0, False), (-1, True, 1, False),
                                               (0, True, 0, True), (0, False, 0, False)):
                    s, io = self.session(mode, ["RIGHT", "DOWN", "LEFT", "UP", "t", "WAIT", "x", "ESC",
                                                        "?", "WAIT", "z", "r", "WAIT", "n"])
                    g = T.AnsiReversi(s, "3", "reversi/1", me)
                    g.names, g.turn_secs, g.v = [LONG, LONG.upper()], 300, view(n, filled, turn, over)
                    g.over = "Game over: %s wins 30-29" % LONG if over else ""
                    g.log = ["<%s> %s" % (LONG, "a long line of chat " * 4), "%s plays d3 (flips 3)" % LONG] * 8
                    g.snap()

                    async def drive():
                        await g.show()
                        while io.keys:
                            await g.on_key(await io.readkey())
                            await g.show()
                        await io.readkey()
                    with self.assertRaises(EOFError):
                        asyncio.run(drive())
                    self.check(io)
                    shown = "\n".join("\n".join(s) for s in io.screens)
                    if filled and not over and me == 0:
                        self.assertIn("Resign this game?", shown)
                        self.assertIn("Your move", shown)
                    if not filled:
                        self.assertIn("Waiting for an opponent", shown)

    def test_painter_matches_canvas(self):
        """Diff painting leaves the terminal showing exactly the last canvas."""
        s, io = self.session("pc")
        g = T.AnsiReversi(s, "3", "reversi/1", 0)
        g.names, g.turn_secs = ["ann", "bob"], 300
        for i, v in enumerate([view(8, False), view(8), view(8, turn=1), view(10), view(4), view(8, over=True)]):
            g.v = v
            for k in ("RIGHT", "DOWN", "DOWN"):
                asyncio.run(g.on_key(k))
                cv, pos = g.screen()
                io.write(s.painter.frame(cv, pos))
                want = cv.lines()
                want[23] = want[23][:79].rstrip()
                self.assertEqual(io.vt.lines(), want, "frame %d" % i)
        self.assertEqual(io.vt.errors, [])

    def test_plain_board(self):
        for n in (4, 8, 10):
            for me in (0, 1, -1):
                s, io = self.session("pc")
                g = T.PlainReversi(s, "3", "reversi/1", me)
                g.names, g.v = [LONG, LONG], view(n)
                g.log = ["x" * 120] * 6
                for l in g.lines() + [g.prompt()]:
                    self.assertLessEqual(len(l), 80)
                    l.encode("ascii")


BG_TABS = ["TAB 3 backgammon/1 standard 2/2 playing %s,%s turn=300" % (LONG, LONG[::-1])]
BG_MOVES = {0: ["13-8", "13-10", "24-19", "24-21", "8-3", "6-3", "6-1", "8-5"],
            1: ["1-4", "1-6", "12-15", "12-17", "17-20", "17-22", "19-22"]}


def bg_lines(phase="move", turn=0, crowded=False, waiting=False):
    """VUE lines for backgammon/1: the opening position, or (crowded) tall
    stacks, checkers on the bar and borne off."""
    if waiting:
        return ["V backgammon/1 2", "BRD 24", "S Waiting for players"]
    if crowded:
        p1, p13, bar, off = "-12 0 0 0 0 9 0 0 0 0 0 0", "0 0 0 0 0 0 0 0 0 0 0 1", "2 9", "4 3"
    else:
        p1, p13, bar, off = "-2 0 0 0 0 5 0 3 0 0 0 -5", "5 0 0 0 -3 0 -5 0 0 0 0 2", "0 0", "0 0"
    over = phase == "over"
    lines = ["V backgammon/1 40", "BRD 24", "P 1 " + p1, "P 13 " + p13, "B " + bar, "O " + off,
             "D 5 3" if phase in ("move", "done") else "D", "C 2 %d" % turn if crowded else "C 1 -",
             "W -" if over else "W %d" % turn, "Q " + ("move" if phase == "done" else phase)]
    if not over:
        lines.append("CLK %d 45 300" % turn)
    moves = {"roll": ["roll", "double"], "take": ["take", "drop"], "move": BG_MOVES[turn]}.get(phase, [])
    return lines + ["M " + m for m in moves]


class BgFakeCore(FakeCore):
    """Both games; VUE answers with the lines given; requests are kept."""

    def __init__(self, tabs, lines):
        super().__init__(tabs)
        self.lines, self.reqs = lines, []

    async def req(self, text):
        self.reqs.append(text)
        verb = text.split()[0]
        if verb == "GAM":
            return ["GAM reversi/1 standard Reversi 2 2", "GAM backgammon/1 standard Backgammon 2 2"], "OK"
        if verb == "VUE":
            return self.lines, "OK"
        return await super().req(text)


class TelnetBackgammonScreens(unittest.TestCase):
    """The Backgammon screen at 80x24 in every mode and phase, its keys and
    clicks, and the plain board."""

    SCENES = [("roll", 0, False), ("move", 0, False), ("move", 1, False), ("done", 0, False), ("take", 0, False),
              ("take", 1, False), ("over", 0, False), ("move", 0, True), ("move", 1, True), ("waiting", 0, False)]
    KEYS = ["RIGHT", "LEFT", "UP", "DOWN", "ENTER", "ENTER", " ", "u", "WAIT", "e", "WAIT", "d", "WAIT", "a", "WAIT",
            "p", "WAIT", "n", "ESC", "t", "WAIT", "x", "ESC", "?", "WAIT", "z", ("CLICK", 3, 5), ("CLICK", 15, 15),
            ("CLICK", 30, 23), ("CLICK", 60, 10), "r", "WAIT", "n", "1", "3", "-", "8", "ENTER", "ESC", "ESC"]

    def session(self, mode, keys, lines, tabs=BG_TABS):
        io = FakeIO(mode, keys)
        core = BgFakeCore(tabs, lines)
        s = T.Session(io, core, mode, LONG)
        if s.painter:
            frame = s.painter.frame

            def checked(cv, cursor=None, full=False):
                self.assertFalse(cv.clipped, "drawn outside 80x24")
                self.assertTrue(all(len(r) == 80 for r in cv.ch) and len(cv.ch) == 24)
                out = frame(cv, cursor, full)
                self.assertNotIn("\n", out)
                return out
            s.painter.frame = checked
        return s, io, core

    def screen(self, mode, me, phase, turn, crowded, keys=None):
        lines = bg_lines(phase, turn, crowded, phase == "waiting")
        names = [LONG, "other"] if me == 0 else ["other", LONG] if me == 1 else ["one", "two"]
        tabs = ["TAB 3 backgammon/1 standard 2/2 playing %s,%s turn=300" % tuple(names)]
        s, io, core = self.session(mode, self.KEYS if keys is None else keys, lines, tabs)
        g = T.AnsiBackgammon(s, "3", "backgammon/1", me)
        g.names, g.turn_secs, g.v = names, 300, g.parse(lines)
        g.rolled = (turn, 5, 3)
        g.over = "Game over: %s wins 2 points (gammon)" % LONG if phase == "over" else ""
        g.log = ["<%s> %s" % (LONG, "a long line of chat " * 4), "%s moves 13 to 8, hits" % LONG] * 8
        g.snap()

        async def drive():
            await g.show()
            while io.keys:
                await g.on_key(await io.readkey())
                await g.show()
            await io.readkey()
        with self.assertRaises(EOFError):
            asyncio.run(drive())
        self.assertEqual(io.vt.errors, [])
        for scr in io.screens:
            self.assertEqual(len(scr), 24)
            self.assertTrue(all(len(l) <= 80 for l in scr))
            self.assertLessEqual(len(scr[23]), 79)
        return "\n".join("\n".join(scr) for scr in io.screens), core

    def test_every_mode_and_phase(self):
        want = {"roll": "Enter rolls the dice", "move": "Steps: ", "done": "Nothing left to play",
                "take": "A takes the cube", "over": "Game over", "waiting": "Waiting for an opponent"}
        for mode in ("pc", "utf8", "ansi"):
            for phase, turn, crowded in self.SCENES:
                for me in (0, 1, -1):
                    shown, core = self.screen(mode, me, phase, turn, crowded)
                    what = (mode, phase, turn, crowded, me)
                    self.assertIn("Backgammon", shown, what)
                    if me == turn or phase in ("over", "waiting"):
                        self.assertIn(want[phase], shown, what)
                    if crowded:
                        self.assertIn(" +6", shown, what)          # 12 on a point: 6 drawn, +6 for the rest
                        self.assertIn(" +3", shown, what)          # 9 on the bar: 6 drawn, +3
                    if me == turn and phase == "move" and not crowded:
                        mv = [r for r in core.reqs if r.startswith("MOV")]
                        self.assertEqual(mv[0], "MOV 3 %s" % ("24-21" if me == 0 else "1-4"), what)
                        self.assertIn("MOV 3 13-8", core.reqs, what)   # typed
                    if me == turn and phase == "done":
                        self.assertIn("END 3", core.reqs, what)        # Enter (and E) end the turn
                    if me == turn and phase == "roll":
                        self.assertIn("MOV 3 roll", core.reqs, what)
                        self.assertIn("MOV 3 double", core.reqs, what)
                    if me == turn and phase == "take":
                        self.assertIn("MOV 3 take", core.reqs, what)
                        self.assertIn("Drop the double?", shown, what)
                        self.assertNotIn("MOV 3 drop", core.reqs, what)  # N said no
                    if me != turn:
                        self.assertFalse([r for r in core.reqs if r.split()[0] in ("MOV", "UND", "END")], what)
        shown, _ = self.screen("pc", 0, "move", 0, False, keys=[])
        for glyph in ("(█)", "(▒)", "▐█▌", "╔══", "[5] [3]", "BAR"):
            self.assertIn(glyph, shown)

    def test_clicks_pick_and_put(self):
        # point 13 is top left (x 2-4), point 8 bottom, fifth from the left (x 14-16)
        shown, core = self.screen("pc", 0, "move", 0, False, keys=[("CLICK", 3, 5), "WAIT", ("CLICK", 15, 15)])
        self.assertIn("Enter moves 13-10", shown)       # picked up: the cursor is on the nearest target
        self.assertIn("MOV 3 13-8", core.reqs)
        shown, core = self.screen("pc", 0, "move", 0, False, keys=[("CLICK", 15, 15)])
        self.assertIn("Enter moves 8-5", shown)         # a click on a checker only picks it up
        self.assertFalse([r for r in core.reqs if r.startswith("MOV")])

    def test_typed_words(self):
        for line, req in (("13-8", "MOV 3 13-8"), ("13/8", "MOV 3 13-8"), ("b-22", "MOV 3 bar-22"),
                          ("6-o", "MOV 3 6-off"), ("undo", "UND 3"), ("end", "END 3"), ("roll", "MOV 3 roll"),
                          ("double", "MOV 3 double"), ("take", "MOV 3 take"), ("drop", "MOV 3 drop")):
            for cls in (T.AnsiBackgammon, T.PlainBackgammon):
                lines = bg_lines("move", 0)
                s, io, core = self.session("pc" if cls is T.AnsiBackgammon else "plain", [], lines)
                g = cls(s, "3", "backgammon/1", 0)
                g.names, g.v = ["a", "b"], g.parse(lines)
                asyncio.run(g.command(line))
                self.assertIn(req, core.reqs, (cls, line))

    def test_menu_offers_both_games(self):
        keys = ["n", "WAIT", "DOWN", "ENTER", "ESC"]       # N on a table row
        s, io, core = self.session("pc", keys, bg_lines())
        with self.assertRaises(EOFError):
            asyncio.run(T.AnsiMenu(s).run())
        shown = "\n".join("\n".join(scr) for scr in io.screens)
        self.assertIn("Start a new Backgammon table", shown)
        self.assertIn("Which game?", shown)                 # N away from a new-table row: a game picker
        self.assertIn("New Backgammon table", shown)        # then the time per turn

    def test_plain_board(self):
        for phase, turn, crowded in self.SCENES:
            for me in (0, 1, -1):
                lines = bg_lines(phase, turn, crowded, phase == "waiting")
                s, io, core = self.session("plain", [], lines)
                g = T.PlainBackgammon(s, "3", "backgammon/1", me)
                g.names, g.v = [LONG, LONG], g.parse(lines)
                g.over = "Game over: x" if phase == "over" else ""
                g.log = ["x" * 120] * 6
                out = g.lines() + [g.prompt()]
                for l in out:
                    self.assertLessEqual(len(l), 80)
                    l.encode("ascii")
                text = "\n".join(out)
                if phase != "waiting":
                    self.assertIn("+==================+===+", text)
                    self.assertIn("(O)", text)
                    self.assertIn("(X)", text)
                if me == turn and phase == "move":
                    self.assertIn("Your move (like", text)


class LineIO(FakeIO):
    """FakeIO for the plain menu: typed lines come from the key list."""

    async def readline(self):
        return await self.readkey()


class NewTableTelnet(unittest.TestCase):
    """One "Start a new ..." entry per game; the rules picker for a game with several rulesets and the
    players picker for one that takes more than two, before the time per turn; NEW carries both."""

    GAM = ["GAM reversi/1 standard Reversi 2 2", "GAM mancala/1 kalah Mancala 2 2",
           "GAM mancala/1 oware Mancala 2 2", "GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves"]

    def core(self):
        test = self

        class Core:
            reqs = []
            auth = "password"

            async def req(self, text):
                self.reqs.append(text)
                if text == "GAM":
                    return test.GAM, "OK"
                if text.startswith("NEW "):
                    return [], "OK 7"
                return [], "OK"
        return Core()

    def run_new(self, mode, args, keys):
        io = (LineIO if mode == "plain" else FakeIO)(mode, keys)
        core = self.core()
        s = T.Session(io, core, mode, "ann")
        menu = (T.PlainMenu if mode == "plain" else T.AnsiMenu)(s)
        asyncio.run(s.load())
        try:
            asyncio.run(menu.new(args))
        except EOFError:
            pass
        return [r for r in core.reqs if r.startswith("NEW ")], "\n".join("\n".join(x) for x in io.screens)

    def test_one_entry_per_game(self):
        s = T.Session(FakeIO("pc"), self.core(), "pc", "ann")
        menu = T.AnsiMenu(s)
        asyncio.run(menu.load())
        new = [it[1] for it in menu.mine if it[0] == "new"]
        self.assertEqual([g["key"] for g in new], ["reversi/1", "mancala/1", "rummytile/1"])
        self.assertEqual(new[1]["rulesets"], ["kalah", "oware"])
        menu.sel = 1
        self.assertEqual("Enter starts a new Mancala table; you choose rules and time per turn.", menu.describe())
        menu.sel = 2
        self.assertEqual("Enter: a new Rummy Tile Classic table; you choose players and time per turn.",
                         menu.describe())
        menu.sel = 0
        self.assertEqual("Enter starts a new Reversi table; you choose the time per turn.", menu.describe())

    def test_pickers_full_screen(self):
        reqs, text = self.run_new("pc", ["mancala"], ["DOWN", "ENTER", "ENTER"])
        self.assertIn("Which rules?", text)
        self.assertIn("Store rules", text)
        self.assertEqual(reqs, ["NEW mancala/1 oware turn=1d"])
        reqs, text = self.run_new("ansi", ["rummytile"], ["DOWN", "ENTER", "ENTER"])
        self.assertIn("How many players?", text)
        self.assertIn("4 players", text)
        self.assertEqual(reqs, ["NEW rummytile/1 standard turn=1d players=3"])
        reqs, text = self.run_new("pc", ["reversi"], ["ENTER"])          # one ruleset, two players: no picker
        self.assertNotIn("Which rules?", text)
        self.assertNotIn("How many players?", text)
        self.assertEqual(reqs, ["NEW reversi/1 standard turn=1d"])
        reqs, _ = self.run_new("pc", ["mancala"], ["ESC"])               # Esc cancels
        self.assertEqual(reqs, [])

    def test_typed_and_plain(self):
        self.assertEqual(self.run_new("pc", ["mancala", "oware", "1h"], [])[0], ["NEW mancala/1 oware turn=1h"])
        self.assertEqual(self.run_new("pc", ["rummytile", "4p", "5m"], [])[0],
                         ["NEW rummytile/1 standard turn=5m players=4"])
        self.assertEqual(self.run_new("pc", ["rummytile", "9p", "5m"], [])[0], [])
        self.assertEqual(self.run_new("pc", ["mancala", "nope", "5m"], [])[0], [])
        reqs, _ = self.run_new("plain", ["mancala"], ["2", "1h"])
        self.assertEqual(reqs, ["NEW mancala/1 oware turn=1h"])
        reqs, _ = self.run_new("plain", ["rummytile"], ["3", ""])
        self.assertEqual(reqs, ["NEW rummytile/1 standard turn=1d players=3"])
        reqs, _ = self.run_new("plain", [], ["man", "", ""])
        self.assertEqual(reqs, ["NEW mancala/1 kalah turn=1d"])


if __name__ == "__main__":
    unittest.main()
