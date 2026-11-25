"""Profiles, the activity and what each turn was played with, on the 1997 pages and by telnet
(docs/protocol.md, Clients and machines; docs/site.md): the browser named from its User-Agent with each move,
the profile and its edit page, the Activity page, the names that link to profiles, and the telnet extension's
terminal type, profile and preferred machine."""
import re
import socket
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from test_adapters import Browser, Stack
from test_core import ROOT  # noqa: F401  (puts the repository on the path)
from test_site import check

NETSCAPE = "Mozilla/3.0 (Macintosh; I; PPC)"
PROFILE_BUDGET = 6 * 1024           # a profile page (docs/site.md)
ACTIVITY_BUDGET = 9 * 1024          # the Activity page, with its 20 turns


class BrowserNames(unittest.TestCase):
    def test_user_agents(self):
        from adapters.web import browser_claim
        cases = {
            NETSCAPE: ("Netscape Navigator 3.0", "Power Macintosh"),
            "Mozilla/2.02 (Macintosh; I; 68K)": ("Netscape Navigator 2.02", "Macintosh"),
            "Mozilla/4.0 (compatible; MSIE 4.01; Windows 98)": ("Internet Explorer 4.01", "Windows 98"),
            "NCSA Mosaic/2.0.0b4 (Macintosh)": ("NCSA Mosaic 2.0.0b4", "Macintosh"),
            "MacWeb/2.0": ("MacWeb 2.0", "Macintosh"),
            "iCab/2.9.8 (Macintosh; U; 68K)": ("iCab 2.9.8", "Macintosh"),
            "Amiga-AWeb/3.4.167SE": ("AWeb 3.4.167SE", "Amiga"),
            "Lynx/2.8.9rel.1 libwww-FM/2.14 SSL-MM/1.4.1": ("Lynx 2.8.9", ""),
            "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0": ("Firefox 132", "Linux"),
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.1 Safari/605.1.15": ("Safari 18.1", "Macintosh"),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 "
            "Safari/537.36 Edg/131.0.0.0": ("Edge 131", "Windows"),
            "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile "
            "Safari/537.36": ("Chrome 131", "Android"),
            "Wget/1.21.4": ("Wget 1.21.4", ""),
            "": ("", ""),
            "\x01" + "Z" * 80: ("", ""),
        }
        for ua, want in cases.items():
            self.assertEqual(browser_claim(ua), want, ua)
            self.assertLessEqual(len(browser_claim(ua)[0]), 40)

    def test_what_each_extension_reports(self):
        from adapters import fibs, nsp_netboxes, outland, telnet
        self.assertEqual(nsp_netboxes.CLIENT_NAME, "NetBoxes 1.1")
        self.assertEqual(outland.CLIENT, "Outland Gateway")
        self.assertEqual(fibs.client_name("clip", "JavaFIBS2001"), "JavaFIBS2001")
        self.assertEqual(fibs.client_name("client", "MacFIBS_3.0.6"), "MacFIBS 3.0.6")
        self.assertEqual(fibs.client_name("client"), "", "a connect login could be any client")
        self.assertEqual(fibs.client_name("telnet"), "", "a login by name and password could be any program")
        self.assertEqual(telnet.terminal_name("VT100"), "telnet (VT100)")
        self.assertEqual(telnet.terminal_name(None), "telnet")
        self.assertLessEqual(len(telnet.terminal_name("x" * 60)), 40)

    def test_sentences(self):
        from adapters.common import on_machine, turn_sentence, turn_time, with_program
        t = {"name": "eric", "move": "played FRAULEIN for 65", "prog": "Outland Gateway", "mach": "Macintosh"}
        self.assertEqual(turn_sentence(t, "Grid Words"),
                         "eric played FRAULEIN for 65 in Grid Words with the Outland Gateway on a Macintosh.")
        self.assertEqual(turn_sentence(dict(t, move="", prog="", mach=""), "Reversi"), "eric played a turn in Reversi.")
        self.assertEqual(on_machine("Apple IIe (enhanced)"), "on an Apple IIe (enhanced)")
        self.assertEqual(on_machine("macOS"), "on macOS")
        self.assertEqual(on_machine("Windows 95"), "on Windows 95")
        self.assertEqual(with_program("Netscape Navigator 3.0"), "with Netscape Navigator 3.0")
        self.assertEqual(turn_time("2026-11-23T14:05:09Z"), "Nov 23, 14:05 UTC")


class JoinLog(unittest.TestCase):
    def test_event_log(self):
        """CLIENT after a JOIN finishes its line; later it is a line of its own; with no text it says nothing."""
        from adapters.web import event_log
        names = ["ann", "bob"]
        events = ["JOIN 0 ann", "CLIENT 0 with GameMaster 1.0.0 on a Macintosh Plus", "JOIN 1 bob", "CLIENT 1",
                  "START", "CLIENT 1 with Lynx 2.8.9", "CLIENT 1"]
        log = event_log(events, list(range(1, 8)), names, "reversi/1")
        self.assertEqual([d for _, d in log], ["ann sat down with GameMaster 1.0.0 on a Macintosh Plus",
                                               "bob sat down", "Game started", "bob now plays with Lynx 2.8.9"])
        self.assertEqual([n for n, _ in log], [1, 3, 5, 6])


class WebProfiles(Stack):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ann, cls.bob = Browser(cls.web_port, ua=NETSCAPE), Browser(cls.web_port)
        cls.ann.login("ann", "ann-Pass-word")
        cls.bob.login("bob", "bob-Pass-word")
        cls.ann_keep = cls.ann.keep()
        st, loc, _, _ = cls.ann.act("/new.htm?g=reversi%2F1&turn=1h")
        cls.tid = parse_qs(urlsplit(loc).query)["t"][0]
        cls.bob.act("/join.htm?t=%s" % cls.tid)
        st, loc, _, body = cls.ann.act("/move.htm?t=%s&mv=d3" % cls.tid)
        assert st == 302, body
        cls.bob.act("/move.htm?t=%s&mv=c3" % cls.tid)
        cls.private = parse_qs(urlsplit(cls.ann.act("/new.htm?g=reversi%2F1&turn=1h&vis=private")[1]).query)["t"][0]
        cls.ann.act("/invite.htm?t=%s&who=bob" % cls.private)
        cls.bob.act("/join.htm?t=%s" % cls.private)
        cls.ann.act("/move.htm?t=%s&mv=d3" % cls.private)

    def test_activity_page(self):
        st, _, _, page = self.bob.get("/activity.htm")
        self.assertEqual(st, 200)
        self.assertEqual(check(page).problems, [])
        self.assertLessEqual(len(page), ACTIVITY_BUDGET)
        text = re.sub(r"<[^>]+>", "", page)
        self.assertIn("ann played d3, turning 1 in Reversi with Netscape Navigator 3.0 on a Power Macintosh.", text)
        self.assertIn("bob played c3, turning 1 in Reversi. (", text, "bob's browser sent no User-Agent")
        self.assertEqual(text.count("ann played d3"), 1, "the private table's turn is not there")
        self.assertIn('<a href="/player.htm?who=ann">ann</a>', page, "names link to profiles")

    def test_profile_pages(self):
        st, _, _, page = self.bob.get("/player.htm?who=ann")
        self.assertEqual(st, 200)
        self.assertEqual(check(page).problems, [])
        self.assertLessEqual(len(page), PROFILE_BUDGET)
        self.assertIn("No preferred machine given yet.", page)
        self.assertIn("Netscape Navigator 3.0", page)
        self.assertIn("Power Macintosh", page)
        self.assertNotIn("Change your preferred machine", page, "only on your own profile")
        st, _, _, page = self.bob.get("/player.htm?who=nobody")
        self.assertIn("There is no player named nobody here.", page)
        # the edit page: an action, with x=
        st, _, _, page = self.ann.get("/profile.htm?" + self.ann_keep)
        self.assertEqual(check(page).problems, [])
        self.assertIn('name="m" size="40" maxlength="40"', page)
        st, _, _, page = self.ann.get("/profile.htm?save=1&m=Macintosh+SE%2F30&" + self.ann_keep)
        self.assertIn("Saved.", page)
        self.assertIn('value="Macintosh SE/30"', page)
        page = self.ann.get("/player.htm")[3]
        self.assertIn("Preferred machine: <b>Macintosh SE/30</b>", page)
        self.assertIn("Change your preferred machine", page)
        self.assertIn("Macintosh SE/30", self.bob.get("/player.htm?who=ann")[3])
        st, _, _, page = self.ann.get("/profile.htm?save=1&m=junk")
        self.assertIn("That link is out of date.", page, "a change needs the action check")
        self.ann.get("/profile.htm?save=1&m=&" + self.ann_keep)
        self.assertIn("No preferred machine given yet.", self.ann.get("/player.htm")[3])

    def test_names_link_to_profiles(self):
        lobby = self.bob.get("/lobby.htm")[3]
        self.assertIn('<a href="/player.htm?who=ann">ann</a>', lobby)
        self.assertIn(">Activity</a>", lobby)
        self.assertIn(">Your profile</a>", lobby)
        table = self.bob.get("/table.htm?t=%s&r=0" % self.tid)[3]
        self.assertIn('<a href="/player.htm?who=ann">ann</a>', table)

    def test_the_table_log_says_with_what(self):
        via = "ann plays d3 (flips 1) (with Netscape Navigator 3.0 on a Power Macintosh)"
        self.assertIn(via, self.bob.get("/table.htm?t=%s&r=0" % self.tid)[3])
        carl = Browser(self.web_port)
        carl.login("carl", "carl-Pass-word")
        self.assertIn(via, carl.get("/table.htm?t=%s&r=0&w=1" % self.tid)[3], "a public table: anyone watching")
        page = self.bob.get("/table.htm?t=%s&r=0" % self.private)[3]
        self.assertIn(via, page, "a private table: its seated players")
        self.assertNotIn("Netscape", carl.get("/table.htm?t=%s&r=0&w=1" % self.private)[3])
        self.assertIn("bob plays c3 (flips 1)</font>", self.bob.get("/table.htm?t=%s&r=0" % self.tid)[3],
                      "bob's browser named nothing")

    def test_who_sat_down_with_what(self):
        """The join in the log, the seat list and the lobby say what each player plays with (CLIENT, VUE C, LST
        clients): to anyone at a public table, to the seated players at a private one."""
        via = "with Netscape Navigator 3.0 on a Power Macintosh"
        page = self.bob.get("/table.htm?t=%s&r=0" % self.tid)[3]
        self.assertIn("ann sat down %s" % via, page)
        self.assertIn('<a href="/player.htm?who=ann">ann</a> <font face', page)
        self.assertIn(">%s</font>" % via, page, "next to ann's name under the board")
        self.assertIn("bob sat down</font>", page.replace("<br>\n", "</font>"), "bob's browser named nothing")
        self.assertEqual(check(page).problems, [])
        lobby = self.bob.get("/lobby.htm")[3]
        self.assertIn('<a href="/player.htm?who=ann">ann</a> <font face', lobby)
        self.assertIn(via, lobby)
        self.assertEqual(check(lobby).problems, [])
        self.assertIn("ann sat down %s" % via, self.bob.get("/table.htm?t=%s&r=0" % self.private)[3],
                      "a private table: its seated players")

    def test_private_tables_keep_their_clients(self):
        # the turn at the private table counts on no profile, and bob, at the table, still sees it with TRN
        page = self.bob.get("/player.htm?who=ann")[3]
        self.assertRegex(page, r"Netscape Navigator 3\.0</font></td><td align=\"right\"><font[^>]*>1<")


class TelnetProfiles(Stack):
    """The telnet extension names the terminal type with each turn, and shows profiles."""

    def connect(self, ttype):
        s = socket.create_connection(("127.0.0.1", self.tel_port), timeout=10)
        buf, deadline = b"", time.time() + 5
        while time.time() < deadline:
            data = s.recv(4096)
            buf += data
            if b"\xff\xfd\x18" in buf:       # IAC DO TTYPE
                s.sendall(b"\xff\xfb\x18")   # IAC WILL TTYPE
            if b"\xff\xfa\x18\x01\xff\xf0" in buf:      # SB TTYPE SEND
                s.sendall(b"\xff\xfa\x18\x00" + ttype.encode() + b"\xff\xf0")
                buf = b""
                break
        return s

    def until(self, s, want, secs=10):
        buf, deadline = b"", time.time() + secs
        while want.encode() not in buf and time.time() < deadline:
            try:
                data = s.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            buf += data
        return buf.decode("latin-1")

    def test_ttype_profile_and_machine(self):
        s = self.connect("VT100")
        self.assertIn("Your terminal says it is VT100.", self.until(s, "Login"))
        for send, want in ((b"new", "Name you want"), (b"tess", "Choose a password"), (b"tess-Pass-word", "again"),
                           (b"tess-Pass-word", "Choice"), (b"4", "Press Enter"), (b"", "Commands:")):
            s.sendall(send + b"\r\n")
            self.assertIn(want, self.until(s, want))
        s.sendall(b"machine Atari Mega ST\r\n")
        self.assertIn("Your preferred machine is now Atari Mega ST.", self.until(s, "Mega ST."))
        s.sendall(b"profile\r\n")
        out = self.until(s, "Atari Mega ST\r\n")
        self.assertIn("Profile of tess:", out)
        self.assertIn("Preferred machine: Atari Mega ST", out)
        # a game against ann on the web: tess's turn is recorded with the terminal type
        ann = Browser(self.web_port)
        ann.login("ann", "ann-Pass-word")
        tid = parse_qs(urlsplit(ann.act("/new.htm?g=reversi%2F1&turn=1h")[1]).query)["t"][0]
        s.sendall(b"join %s\r\n" % tid.encode())
        self.until(s, ">", 5)
        ann.act("/move.htm?t=%s&mv=d3" % tid)
        self.assertIn("ann plays d3", self.until(s, "ann plays d3"))     # tess's turn now
        s.sendall(b"c3\r\n")
        deadline = time.time() + 10
        while time.time() < deadline:
            page = ann.get("/activity.htm")[3]
            if "tess" in page:
                break
            time.sleep(0.5)
        s.close()
        self.assertIn("tess played c3, turning 1 in Reversi with telnet (VT100).", re.sub(r"<[^>]+>", "", page))


if __name__ == "__main__":
    unittest.main()
