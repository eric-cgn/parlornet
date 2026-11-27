"""The operator's site directory (deploy/README.md, A site of your own): placeholders (parlord/sitetext.py), the
files the web extension reads (adapters/sitefiles.py), the welcome page's fragment and the extra pages on a
live server, the message of the day's placeholders, and deploy/start.py's handling of the directory. Also the
texts that name the server's host: the web's fallback host, and where the FIBS and Outland extensions send
players.

    python3 -m unittest discover -s tests -p "test_site_dir.py"
"""
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

from test_adapters import Stack, plain
from test_site import check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import sitefiles, telnet as T, web as W  # noqa: E402
from adapters.common import Where  # noqa: E402
from parlord import PROJECT_URL, sitetext  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


class Placeholders(unittest.TestCase):
    def setUp(self):
        sitetext._logged.clear()
        self.log = []

    def test_values(self):
        v = sitetext.values("games.example.org", [("HTTP", 6500), ("TELNET", 6502), ("PARLORNET/1", 6501)],
                            docs="http://docs.example.org/", contribute="https://example.org/src")
        self.assertEqual(v, {"name": "games.example.org", "host": "games.example.org",
                             "web": "http://games.example.org:6500/", "docs": "http://docs.example.org/",
                             "contribute": "https://example.org/src", "port:HTTP": "6500", "port:TELNET": "6502",
                             "port:PARLORNET/1": "6501"})
        v = sitetext.values("My Parlor", [("HTTP", 6500), ("HTTPS", 6513)], web="https://play.example.org/",
                            play="https://play.example.org/play/")
        self.assertEqual((v["name"], v["host"], v["web"], v["play"]),
                         ("My Parlor", "play.example.org", "https://play.example.org/", "https://play.example.org/play/"))
        v = sitetext.values("example.org", [("HTTP", 80), ("HTTPS", 6513)])
        self.assertEqual((v["web"], v["play"]), ("http://example.org/", "http://example.org:6513/play/"))
        v = sitetext.values("example.org", [], docs="", contribute="bad url")
        self.assertNotIn("web", v)          # no web extension running: no address
        self.assertNotIn("docs", v)         # cleared
        self.assertNotIn("contribute", v)   # never put in as it is

    def test_expand(self):
        v = sitetext.values("example.org", [("TELNET", 6502)], contribute="https://example.org/?a=1&b=2")
        out = sitetext.expand("{name} {port:TELNET} {port:telnet} {{literal}} }} {contribute}", v, "x",
                              log=self.log.append)
        self.assertEqual(out, "example.org 6502 6502 {literal} } https://example.org/?a=1&b=2")
        self.assertEqual(sitetext.expand("<a href=\"{contribute}\">", v, "x", W.esc),
                         '<a href="https://example.org/?a=1&amp;b=2">')
        self.assertEqual(self.log, [])
        for _ in range(2):          # an unknown placeholder, and one with no value: as written, logged once
            out = sitetext.expand("{nosuch} {play} {port:FIBS} { not one } {}", v, "welcome.htm",
                                  log=self.log.append)
            self.assertEqual(out, "{nosuch} {play} {port:FIBS} { not one } {}")
        self.assertEqual(self.log, [
            "site: welcome.htm has {nosuch}, which is not a placeholder, so it is shown as written",
            "site: welcome.htm has {play}, which has no value now, so it is shown as written",
            "site: welcome.htm has {port:FIBS}, which has no value now, so it is shown as written"])

    def test_expand_lines(self):
        v = sitetext.values("example.org", [("TELNET", 6502)])
        lines = ["Welcome to {name}.", "Play: {play}", "Telnet port {port:TELNET}.", "{{x}} {nosuch}", "{contribute}"]
        self.assertEqual(sitetext.expand_lines(lines, v, "motd", log=self.log.append),
                         ["Welcome to example.org.", "Telnet port 6502.", "{x} {nosuch}"])
        self.assertEqual(len(self.log), 1)


class Files(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="pnsite")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.log = []

    def load(self):
        return sitefiles.load(self.d, W.builtin_pages(), self.log.append)

    def test_nothing(self):
        site = sitefiles.load("", W.builtin_pages(), self.log.append)
        self.assertEqual((site.welcome, site.pages, self.log), ("", {}, []))
        site = sitefiles.load(os.path.join(self.d, "missing"), W.builtin_pages(), self.log.append)
        self.assertEqual(site.pages, {})
        self.assertIn("is not a directory", self.log[0])

    def test_welcome_and_pages(self):
        write(os.path.join(self.d, "welcome.htm"), "<p>Café {name}</p>\r\n")
        write(os.path.join(self.d, "pages", "dial-in.htm"), "<title>Dial-in access</title>\n<p>Call us.</p>\n")
        write(os.path.join(self.d, "pages", "lobby.htm"), "<title>Mine</title>\n")
        write(os.path.join(self.d, "pages", "Big_Name.htm"), "<title>Bad</title>\n")
        write(os.path.join(self.d, "pages", "notes.txt"), "not a page")
        write(os.path.join(self.d, "pages", "untitled.htm"), "<p>No title.</p>\n")
        write(os.path.join(self.d, "pages", "huge.htm"), "<title>Huge</title>\n" + "x" * 40000)
        write(os.path.join(self.d, "pages", "latin.htm"), b"<title>Caf\xe9</title>\n", "wb")
        write(os.path.join(self.d, "pages", ".hidden.htm"), "<title>Hidden</title>\n")
        site = self.load()
        self.assertEqual(site.welcome, "<p>Caf&#233; {name}</p>\n")
        self.assertEqual(site.pages, {"/dial-in.htm": ("Dial-in access", "<p>Call us.</p>\n")})
        said = "\n".join(self.log)
        self.assertIn("site: pages/lobby.htm would take the place of the site's own page /lobby.htm, so it is not "
                      "served", said)
        self.assertIn("site: pages/Big_Name.htm is not named <name>.htm", said)
        self.assertIn("site: pages/notes.txt is not named <name>.htm", said)
        self.assertIn("site: pages/untitled.htm does not start with its title", said)
        self.assertIn("site: pages/huge.htm is larger than 32 KB", said)
        self.assertIn("site: pages/latin.htm is not UTF-8 text", said)
        self.assertNotIn("hidden", said)
        self.assertEqual(self.log[-1], "site: %s has welcome.htm, /dial-in.htm" % self.d)

    def test_no_page_takes_a_built_in_address(self):
        for path in ("/welcome.htm", "/index.htm", "/login.htm", "/signup.htm", "/lobby.htm", "/table.htm",
                     "/move.htm", "/out.htm", "/leaderboard.htm", "/credits.htm", "/player.htm", "/profile.htm",
                     "/activity.htm", "/colours.htm", "/bghome.htm", "/games.htm", "/frontends.htm",
                     "/telnet.htm"):
            self.assertIn(path, W.builtin_pages())


class Wording(unittest.TestCase):
    def test_game_over_in_words(self):
        """A finished table's status says how the game ended in words, not as the game's result code."""
        events = ["JOIN 0 ann", "JOIN 1 bob", "START", "PUT 0 d3 4 4", "END win 1 25-39"]
        self.assertEqual(W.end_words(events, ["ann", "bob"], "reversi/1"), "Game over: bob wins 25-39")
        self.assertEqual(W.end_words(["START", "END draw 32-32"], ["ann", "bob"], "reversi/1"),
                         "Game over: a draw, 32-32")
        self.assertEqual(W.end_words(["START", "END resign 0"], ["ann", "bob"], "reversi/1"),
                         "Game over: ann resigned")
        self.assertIsNone(W.end_words(["START"], ["ann", "bob"], "reversi/1"))

    def test_login_lines_break_at_spaces(self):
        """Before the login, a long line is cut at spaces to the terminal's width less one."""
        class IO:
            size = None

            def __init__(self):
                self.out = ""

            def write(self, text):
                self.out += text
        io = IO()
        T.say(io, "A new account. %s" % T.NEW_NAME_RULE)
        lines = io.out.rstrip("\n").split("\n")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(l) <= 79 for l in lines), lines)
        self.assertEqual(" ".join(lines), "A new account. %s" % T.NEW_NAME_RULE)
        io = IO()
        io.size = (40, 24)
        T.say(io, "A new account. %s" % T.NEW_NAME_RULE)
        self.assertTrue(all(len(l) <= 39 for l in io.out.rstrip("\n").split("\n")))

    def test_where_to_play(self):
        self.assertEqual(Where.words("games.example.org", {"HTTP": 6500, "TELNET": 6502}),
                         "on the web (http://games.example.org:6500/) or by telnet (games.example.org port 6502)")
        self.assertEqual(Where.words("games.example.org", {"HTTP": 80, "TELNET": 23}, new=True),
                         "on the web (http://games.example.org/, New account) or by telnet (games.example.org "
                         "port 23, log in as new)")
        self.assertEqual(Where.words("games.example.org", {}), "on the web or by telnet")
        self.assertEqual(Where().text, "on the web or by telnet")

    def test_the_host_for_a_browser_that_sends_none(self):
        """Mosaic sends no Host header: the site's own name stands in, from its configuration."""
        req = {"headers": {}}
        with mock.patch.object(W, "HOSTS", []), mock.patch.object(W, "PUBLIC_URL", [""]), \
                mock.patch.object(W.S, "LABEL", ["games.example.org"]):
            self.assertEqual(W.host_of(req), "games.example.org")
            W.PUBLIC_URL[0] = "https://play.example.org/"
            self.assertEqual(W.host_of(req), "play.example.org")
            W.HOSTS[:] = ["www.example.org:6500"]
            self.assertEqual(W.host_of(req), "www.example.org")
            self.assertEqual(W.host_of({"headers": {"host": "other.example:6500"}}), "www.example.org")
        self.assertNotIn('"parlornet.org"', read("adapters", "web.py"))


class StartScript(unittest.TestCase):
    """deploy/start.py: the site directory's motd.txt comes before PARLOR_MOTD_FILE, the web gets --site when
    the directory is there, and --contribute-url as the core has it."""

    def started(self, env):
        import importlib.util
        d = tempfile.mkdtemp(prefix="pndata")
        self.addCleanup(shutil.rmtree, d, True)
        env = dict(env, PARLOR_DB=os.path.join(d, "parlord.db"), PARLOR_SETTINGS=os.path.join(d, "none.conf"),
                   PARLOR_ADAPTERS="web")
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("pnstart_site_%d" % id(env),
                                                          os.path.join(ROOT, "deploy", "start.py"))
            st = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(st)
        started = []
        with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                mock.patch("builtins.print"), mock.patch.object(st.signal, "signal"), \
                mock.patch.object(st.sys, "argv", ["start.py"]):
            with self.assertRaises(KeyboardInterrupt):
                st.main()
        return dict(started)

    def test_site_directory(self):
        site = tempfile.mkdtemp(prefix="pnsite")
        self.addCleanup(shutil.rmtree, site, True)
        got = self.started({"PARLOR_SITE": site, "PARLOR_MOTD_FILE": "/app/deploy/motd.txt"})
        self.assertEqual(got["web"][got["web"].index("--site") + 1], site)
        self.assertEqual(got["web"][got["web"].index("--contribute-url") + 1], PROJECT_URL)
        self.assertEqual(got["parlord"][got["parlord"].index("--motd") + 1], "/app/deploy/motd.txt")
        write(os.path.join(site, "motd.txt"), "Hello from {name}.\n")
        got = self.started({"PARLOR_SITE": site, "PARLOR_MOTD_FILE": "/app/deploy/motd.txt",
                            "PARLOR_CONTRIBUTE_URL": ""})
        self.assertEqual(got["parlord"][got["parlord"].index("--motd") + 1], os.path.join(site, "motd.txt"))
        self.assertEqual(got["web"][got["web"].index("--contribute-url") + 1], "")     # cleared
        got = self.started({"PARLOR_SITE": os.path.join(site, "missing"), "PARLOR_MOTD_FILE": "/m.txt"})
        self.assertNotIn("--site", got["web"])
        self.assertEqual(got["parlord"][got["parlord"].index("--motd") + 1], "/m.txt")

    def test_install_scripts_mount_it_read_only(self):
        for name in ("install.sh", "install-extensions.sh", "install-split.sh"):
            text = read("deploy", name)
            self.assertIn('SITE="-v $PARLOR_SITE_DIR:/run/parlornet/site:ro"', text, name)
        self.assertNotIn("parlornet.org", read("deploy", "install-split.sh"))


SITE_MOTD = "Welcome to {name}.\nTelnet to {host} port {port:TELNET}.\nPlay at {play}\n"
SITE_WELCOME = ('<p>Telnet to <tt>{host}</tt> port {port:TELNET}, or browse <a href="{web}">{web}</a>. '
                '{{Braces}} stay. {nosuch} and {play} show as written.</p>\n')
SITE_PAGE = "<title>Dial-in access</title>\n<p>{name} answers modem calls. Telnet port {port:TELNET}.</p>\n"


class Live(Stack):
    """A server with a site directory: its message of the day, the welcome page's fragment and an extra page."""

    @classmethod
    def setUpClass(cls):
        cls.site = tempfile.mkdtemp(prefix="pnsite")
        write(os.path.join(cls.site, "motd.txt"), SITE_MOTD)
        write(os.path.join(cls.site, "welcome.htm"), SITE_WELCOME)
        write(os.path.join(cls.site, "pages", "dial-in.htm"), SITE_PAGE)
        write(os.path.join(cls.site, "pages", "login.htm"), "<title>Mine</title>\n<p>Not served.</p>\n")
        cls.core_extra = ("--motd", os.path.join(cls.site, "motd.txt"))
        with mock.patch.dict(os.environ, {"PARLOR_SITE": cls.site}):
            super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.site, True)

    def until(self, fetch, want, secs=12):
        """fetch() again until want is in it (the extensions declare their ports a moment after they start)."""
        end = time.time() + secs
        while True:
            got = fetch()
            if want in got or time.time() > end:
                return got
            time.sleep(0.5)

    def motd(self):
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        try:
            s.sendall(b"PARLORNET/1\r\n")
            f = s.makefile("rb")
            f.readline()
            s.sendall(b"MOTD\r\n")
            out = []
            while True:
                l = f.readline().decode().rstrip("\r\n")
                if l.startswith(("OK", "ERR")):
                    return out
                out.append(l[5:])
        finally:
            s.close()

    def test_message_of_the_day(self):
        lines = self.until(lambda: "\n".join(self.motd()), "port %d" % self.tel_port).split("\n")
        self.assertEqual(lines, ["Welcome to parlornet.", "Telnet to parlornet port %d." % self.tel_port])

    def test_welcome_fragment(self):
        page = self.until(lambda: self.get("/welcome.htm")[3], "port %d" % self.tel_port)
        self.assertEqual(check(page).problems, [])
        body = plain(page)
        want = ('<p>Telnet to <tt>parlornet</tt> port %d, or browse <a href="http://parlornet:%d/">'
                'http://parlornet:%d/</a>. {Braces} stay. {nosuch} and {play} show as written.</p>'
                % (self.tel_port, self.web_port, self.web_port))
        self.assertIn(want, body)
        self.assertLess(body.index("</pre>"), body.index(want))                  # after the message of the day
        self.assertLess(body.index(want), body.index('<a name="signin">'))       # in the Welcome section
        self.get("/welcome.htm")
        said = [l for l in self.output if "{nosuch}" in l]
        self.assertEqual(said, ["site: welcome.htm has {nosuch}, which is not a placeholder, so it is shown as "
                                "written"])                                       # once

    def test_extra_page(self):
        st, _, ct, page = self.get("/dial-in.htm")
        self.assertEqual((st, ct), (200, "text/html"))
        self.assertEqual(check(page).problems, [])
        body = plain(page)
        self.assertIn("<title>ParlorNet: Dial-in access</title>", body)
        self.assertIn('</table>\n<p><a href="/welcome.htm">Welcome</a> &gt; Dial-in access</p>\n'
                      '<h1>Dial-in access</h1>\n<p>parlornet answers modem calls.', body)
        page = self.until(lambda: self.get("/dial-in.htm")[3], "port %d" % self.tel_port)
        self.assertIn("Telnet port %d." % self.tel_port, page)
        self.assertNotIn("Not served", self.get("/login.htm")[3])               # the site's own sign-in page
        self.assertTrue([l for l in self.output if "pages/login.htm would take the place" in l])
        self.assertEqual(self.get("/nosuch.htm")[0], 200)                       # anything else: sign in
        self.assertNotIn("Dial-in access", self.get("/nosuch.htm")[3])


if __name__ == "__main__":
    unittest.main()
