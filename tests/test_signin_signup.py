"""Sign in and New account as separate requests (USER, REG; HELO keeps doing both), reserved names, and the
message of the day: deploy/motd.txt's layout and the {contribute} marker (docs/accounts.md, docs/protocol.md).
Driven through the core's request dispatch, as tests/test_auth.py.

    python3 -m unittest discover -s tests -p "test_signin_signup.py"
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord import PROJECT_URL, admin, auth, core as pcore  # noqa: E402
from test_auth import inf, public, say, server  # noqa: E402

MOTD_FILE = os.path.join(ROOT, "deploy", "motd.txt")
TAGLINE = "Turn-based game server for any computer, old or new"


def motd_server(text=None):
    srv = server("none")
    if text is None:
        with open(MOTD_FILE, encoding="ascii") as f:
            text = f.read()
    srv.default_motd = text
    return srv


def motd(srv):
    out = asyncio.run(srv.dispatch(public(), "MOTD"))
    assert out[-1] == "OK", out
    return [l[5:] for l in out[:-1]]


class MessageOfTheDay(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop("PARLOR_CONTRIBUTE_URL", None)

    def tearDown(self):
        self.env.stop()

    def test_the_shipped_file(self):
        """An ASCII box under 40 columns (a 40-column screen shows it whole, without the cursor wrapping onto
        a blank line), then the contribute address below it, where any length of address leaves the box whole,
        then where the text lives and when it last changed: plain text an operator deletes or rewrites."""
        with open(MOTD_FILE, encoding="ascii") as f:
            lines = f.read().rstrip("\n").split("\n")
        self.assertTrue(all(len(l) <= 39 and l == l.rstrip() for l in lines), [len(l) for l in lines])
        self.assertTrue(all(" " <= c <= "~" for l in lines for c in l))
        # room to spare under the protocol's line count, so a longer address that wraps still fits
        self.assertLess(len(lines), pcore.MOTD_LINES)
        rule = lines[0]
        self.assertRegex(rule, r"^\+-+\+$")
        bottom = lines.index(rule, 1)
        box = lines[1:bottom]
        self.assertTrue(all(len(l) == len(rule) and l.startswith("| ") and l.endswith(" |") for l in box))
        self.assertEqual(lines[bottom + 1:bottom + 3], ["Run your own server or contribute:", pcore.MOTD_MARKER])
        self.assertEqual(lines[bottom + 3], "Server setting: deploy/motd.txt,")
        self.assertRegex(lines[bottom + 4], r"^last changed \d{4}-\d\d-\d\d\.$")
        self.assertEqual(len(lines), bottom + 5)
        inner = [l[2:-2].strip() for l in box]
        text = " ".join(inner)
        self.assertTrue(text.startswith("ParlorNet: %s." % TAGLINE), text)
        for fact in ("game programs from 25 to 50 years ago play online again", "Reversi The Eclipse",
                     "Outland's Backgammon", "GameMaster",
                     "Telnet, the web and your own client work too.", "Ten games. Alone? Invite the bot.",
                     'For a new account, log in as "new" by telnet, or use New account on the web.',
                     "We email you a password."):
            self.assertIn(fact, text)

    def test_as_served_the_project_home_by_default(self):
        with open(MOTD_FILE, encoding="ascii") as f:
            want = f.read().rstrip("\n").split("\n")
        got = motd(motd_server())
        self.assertEqual(got, [l.replace("{contribute}", PROJECT_URL) for l in want])   # nothing rewrapped
        self.assertEqual(got[-4:-2], ["Run your own server or contribute:", "https://github.com/eric-cgn/parlornet"])
        self.assertTrue(all(len("MOTD " + l) + 2 <= pcore.MAXLINE for l in got))
        self.assertTrue(all(len(l) <= 39 for l in got))

    def test_marker_set_cleared_and_from_the_environment(self):
        srv = motd_server("Top\n  two spaces kept\nSee {contribute} now\nEnd")
        self.assertEqual(motd(srv), ["Top", "  two spaces kept", "See %s now" % PROJECT_URL, "End"])
        os.environ["PARLOR_CONTRIBUTE_URL"] = "https://example.org/p"
        self.assertEqual(motd(srv)[2], "See https://example.org/p now")
        srv.set_setting("site.contribute_url", "https://example.org/club")      # the setting comes first
        self.assertEqual(motd(srv)[2], "See https://example.org/club now")
        srv.set_setting("site.contribute_url", "")                              # cleared: the line goes
        self.assertEqual(motd(srv), ["Top", "  two spaces kept", "End"])
        srv.set_setting("site.contribute_url", None)                            # unset: the environment's
        self.assertEqual(motd(srv)[2], "See https://example.org/p now")
        os.environ["PARLOR_CONTRIBUTE_URL"] = ""                                # cleared there too
        self.assertEqual(len(motd(srv)), 3)
        srv.set_setting("site.contribute_url", "bad url")                       # never put in as is
        self.assertEqual(len(motd(srv)), 3)

    def test_a_long_line_wraps_and_the_count_is_capped(self):
        srv = motd_server("word " * 40 + "\n" + "\n".join("l%d" % i for i in range(40)))
        got = motd(srv)
        self.assertEqual(len(got), pcore.MOTD_LINES)
        self.assertTrue(all(len(l) <= pcore.MOTD_WIDTH for l in got))

    def test_the_admin_setting(self):
        check = admin.SETTINGS["site.contribute_url"]
        self.assertEqual(check(" https://example.org/x "), "https://example.org/x")
        self.assertEqual(check(""), "")
        for bad in ("not a url", "https://example.org/" + "x" * 60, "example.org"):
            with self.assertRaises(ValueError):
                check(bad)


class SignInAndNewAccount(unittest.IsolatedAsyncioTestCase):
    async def test_user_signs_in_only(self):
        for signin, signup in (("none", None), ("password", None), ("password", "email")):
            srv = server(signin, signup)
            s = public()
            self.assertEqual(await say(srv, s, "USER zed"), "ERR NOPLAYER no account named zed here", signin)
            self.assertIsNone(srv.accounts.player("zed"))                       # nothing was made
            self.assertIsNone(s.pending)

    async def test_reg_signs_up_only(self):
        srv = server("password")
        s = public()
        reply = await srv.dispatch(s, "REG bo")
        self.assertEqual(reply, ["INF New account bo: choose a password."] + inf(auth.PW_RULE) +
                         ["ASK password Choose_a_password secret cred new"])
        await say(srv, s, "AUT password Bo pass word")
        self.assertEqual(await say(srv, s, "AUT confirm Bo pass word"), "OK -")
        t = public()
        self.assertEqual(await say(srv, t, "REG BO"), "ERR NAMEINUSE the name BO is taken; choose another")
        self.assertEqual(await say(srv, t, "USER bo"), "ASK password Password secret cred")
        self.assertEqual(await say(srv, t, "AUT password Bo pass word"), "OK -")
        u = public()
        self.assertTrue((await say(srv, u, "REG bo x")).startswith("ERR SYNTAX REG name"))

    async def test_reg_under_each_sign_up(self):
        srv = server("none")
        self.assertEqual(await say(srv, public(), "REG nia"), "OK -")
        srv = server("password", "email")
        out = await srv.dispatch(public(), "REG tia")                           # straight to the address
        self.assertEqual(out, ["INF New account: tia.",
                               "INF We email you a password to sign in with; then you choose your own.",
                               "ASK email Email_address"])

    async def test_helo_still_does_both(self):
        srv = server("password")
        self.assertEqual(await say(srv, public(), "HELO cy"), "ASK password Choose_a_password secret cred new")
        srv = server("password", "email")
        self.assertEqual(await say(srv, public(), "HELO tia"), "ASK confirm Sign_up_as_tia?_(y/n) yn")

    async def test_reserved_names_are_refused_at_the_core(self):
        for signin, signup in (("none", None), ("password", None), ("password", "email")):
            srv = server(signin, signup)
            for line in ("REG new", "REG Guest", "HELO guest", "HELO NEW"):
                word = line.split()[1].lower()
                self.assertEqual(await say(srv, public(), line),
                                 "ERR RESERVED %s is a reserved word here; choose another name" % word, line)
            self.assertIsNone(srv.accounts.player("new"))
            self.assertIsNone(srv.accounts.player("guest"))
            self.assertEqual(auth.create(srv.accounts, "guest")[1], "RESERVED")  # the last check too

    async def test_unknown_names_count_like_sign_ups(self):
        """USER for names with no account is limited per client address as sign-ups are (HELO for a free
        name starts a sign-up, which tells as much), so it lists names no faster than HELO could."""
        srv = server("password")
        n, _ = auth.SIGNUPS_PER_SOURCE
        for i in range(n):
            self.assertTrue((await say(srv, public(), "USER x%d" % i)).startswith("ERR NOPLAYER"))
        self.assertTrue((await say(srv, public(), "USER y")).startswith("ERR BUSY wait="))
        srv.accounts.player("known", create=True)
        self.assertTrue((await say(srv, public(), "USER known")).startswith("ASK password"))   # still signs in

    async def test_allowed_before_signing_in_and_not_after(self):
        srv = server("none")
        s = public()
        self.assertEqual(await say(srv, s, "REG ann"), "OK -")
        self.assertEqual(await say(srv, s, "USER ann"), "ERR STATE already said HELO")
        self.assertEqual(await say(srv, s, "REG bob"), "ERR STATE already said HELO")


class NoPlaceholders(unittest.TestCase):
    def test_no_owner_placeholder_left(self):
        """The project's address is written out everywhere; no github.com/<placeholder>/ is left."""
        placeholder = "github.com/" + "OWNER"
        found = []
        for root, dirs, files in os.walk(ROOT):
            # .claude holds tool working copies (old worktrees), not the project
            dirs[:] = [d for d in dirs if d not in (".git", ".claude", "__pycache__", "node_modules")]
            for f in files:
                path = os.path.join(root, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        if placeholder in fh.read():
                            found.append(os.path.relpath(path, ROOT))
                except (UnicodeDecodeError, OSError):
                    continue
        self.assertEqual(found, [])
        self.assertEqual(PROJECT_URL, "https://github.com/eric-cgn/parlornet")


class NoAppCodes(unittest.TestCase):
    def test_nothing_left_of_the_app_code_sign_in(self):
        """Sign-in by an app's one-time codes was removed (2026-11-22): no file names it any more, but
        Grid Words' English word list, where it is a playable word. The words are put together here so this file does not hold them."""
        words = ["to" + "tp", "authen" + "ticator", "otp" + "auth"]
        skip = {os.path.join("plugins", "gridwords", "words.txt")}
        found = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", ".claude", "__pycache__", "node_modules", "build")]
            for f in files:
                path = os.path.join(root, f)
                if os.path.relpath(path, ROOT) in skip:
                    continue
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read().lower()
                except (UnicodeDecodeError, OSError):
                    continue
                found += ["%s: %s" % (os.path.relpath(path, ROOT), w) for w in words if w in text]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
