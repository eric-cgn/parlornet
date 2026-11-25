"""Sign-in and sign-up (docs/accounts.md, docs/security-design.md), driven
through the core's request dispatch: the shipped pairs none+none,
password+password and password+email; the password rule; a sign-up's mailed
temporary password, the first sign-in that asks for a password of the
player's own, a pending account's expiry; a forgotten password; lockouts with
reasons; sessions issued only on the frontend channel. No test prints a
password or a mail's body."""
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord import auth, core as pcore  # noqa: E402
from parlord.core import Server, Session  # noqa: E402

SID = r"^OK [A-Za-z0-9_-]{22}$"
TEMP_FORM = r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}"     # assertions on passwords never show them when they fail


def inf(*texts):
    """The INF lines the core sends for these lines of text (wrapped as it wraps them)."""
    return ["INF " + l for l in pcore.wrap(list(texts), pcore.MAXLINE - 6)]


RULE = inf(auth.PW_RULE)


def server(signin, signup=None, db=":memory:"):
    srv = Server(os.path.join(ROOT, "plugins"), "test", db, signin, None, signup)
    srv.settings["email.transport"] = "log"
    return srv


def public():
    return Session(None)


def frontend(name="web"):
    return Session(None, frontend=name, fingerprint="f")


async def say(srv, conn, line):
    """The final line of the reply."""
    return (await srv.dispatch(conn, line))[-1]


def mail_password(srv):
    """The temporary password in the last mail (sign-up or a forgotten password)."""
    return re.search(r"Password:\s+(\S+)", srv.auth.mailer.last["body"]).group(1)


async def email_signup(srv, name, addr, conn=None):
    """REG name and an address: the pending account and its mailed temporary password. Returns (conn,
    password); the dialog is at the question for the password from the mail."""
    s = conn or public()
    out = await srv.dispatch(s, "REG %s" % name)
    assert out[-1] == "ASK email Email_address", out
    out = await srv.dispatch(s, "AUT email %s" % addr)
    assert out[-1] == "ASK password Password_from_the_email secret", out
    return s, mail_password(srv)


async def first_sign_in(srv, s, pw, new="Fresh-start9"):
    """At a question for the password: the temporary one, then a password of the player's own. Returns
    the last line."""
    r = await say(srv, s, "AUT password %s" % pw)
    assert r == "ASK password New_password secret new", r
    r = await say(srv, s, "AUT password %s" % new)
    assert r == "ASK confirm Type_it_again secret new", r
    return await say(srv, s, "AUT confirm %s" % new)


class Helpers(unittest.TestCase):
    def test_schedule(self):
        self.assertEqual(auth.lock_secs(1), 60)
        self.assertEqual(auth.lock_secs(3), 900)
        self.assertTrue(auth.lock_secs(2) > 60 and auth.lock_secs(4) > 900)
        self.assertEqual(auth.lock_secs(20), 4 * 3600)       # at most 4 hours

    def test_password_hashing(self):
        h = auth.hash_password("correct horse")
        self.assertTrue(h.startswith("scrypt$16384$8$1$"))
        self.assertNotIn("correct", h)
        self.assertTrue(auth.check_password("correct horse", h))
        self.assertFalse(auth.check_password("correct horsf", h))
        self.assertFalse(auth.check_password("correct horse", ""))
        self.assertNotEqual(h, auth.hash_password("correct horse"))       # salted

    def test_the_password_rule(self):
        """At least 8 characters from at least three of the four kinds, said plainly when refused."""
        for good in ("Abcdefg1", "abcdefg1!", "ABCDEFG1!", "Abc defgh", "with Inner spaces!", "x" * 60 + "A1!z"):
            self.assertIsNone(auth.password_problem(good), good)
        refusals = {"Abc1!": "That password is too short. ",
                    "abcdefgh": "That password uses only one of the four kinds. ",
                    "abcdefgh12": "That password uses only two of the four kinds. ",
                    "ABCDEFGH!!": "That password uses only two of the four kinds. ",
                    "12345678": "That password uses only one of the four kinds. ",
                    "x" * 63 + "A1!": "That password is too long. "}
        for bad, start in refusals.items():
            self.assertEqual(auth.password_problem(bad), start + auth.PW_RULE, bad)
        self.assertEqual(auth.password_problem("café-Au-lait1"),
                         "A password is plain ASCII: letters, digits, symbols and spaces.")
        self.assertEqual(auth.password_problem(" Leading space1"), "A password cannot start or end with a space.")
        self.assertIn("three of these four kinds: lowercase letters, capital letters, digits and symbols",
                      auth.PW_RULE)

    def test_temporary_passwords(self):
        pw = auth.temp_password()
        self.assertTrue(re.fullmatch(TEMP_FORM, pw), "not three groups of four")
        self.assertFalse(set(pw) & set("01ilo"))                          # nothing to mistake for another
        stored = auth.temp_cred(pw, now=1000)
        self.assertTrue(pw.replace("-", "") not in stored, "the password is stored")
        self.assertTrue(stored.startswith("%d$scrypt$" % (1000 + auth.TEMP_SECS)))
        self.assertTrue(auth.check_temp(pw, stored, now=1000))
        self.assertTrue(auth.check_temp(pw.upper().replace("-", " "), stored, now=1000))   # as typed on any keyboard
        self.assertTrue(auth.check_temp(pw, stored, now=1000 + auth.TEMP_SECS))
        self.assertFalse(auth.check_temp(pw, stored, now=1001 + auth.TEMP_SECS))           # a day, then no more
        self.assertFalse(auth.check_temp("abcd-efgh-jkmn", stored, now=1000))
        self.assertFalse(auth.check_temp(pw, "", now=1000))

    def test_combinations(self):
        for pair in (("none", "none"), ("password", "password"), ("password", "email")):
            self.assertIsNone(auth.combo_problem(*pair), pair)
        for pair in (("password", "none"), ("none", "password"), ("none", "email"), ("token", "password"),
                     ("password", "token")):
            self.assertIsNotNone(auth.combo_problem(*pair), pair)
        self.assertEqual(sorted(auth.SIGNIN), ["none", "password"])
        self.assertEqual(sorted(auth.SIGNUP), ["email", "none", "password"])
        srv = server("password", "none")                 # a pair that cannot work falls back, with a warning
        self.assertEqual(srv.auth.greeting(), "AUTH=password SIGNUP=password")


class NoneNone(unittest.IsolatedAsyncioTestCase):
    async def test_name_alone_through_a_session(self):
        srv = server("none")
        self.assertEqual(srv.auth.greeting(), "AUTH=none SIGNUP=none")
        self.assertEqual(await say(srv, public(), "HELO ann"), "OK -")
        self.assertEqual(await say(srv, public(), "HELO ann whatever"), "OK -")     # a name alone
        r = await say(srv, frontend(), "HELO ann")                                 # a session, as for any method
        self.assertRegex(r, SID)
        c = frontend()
        self.assertEqual(await say(srv, c, "SES " + r.split()[1]), "OK ann")
        self.assertEqual(await say(srv, c, "PWD"), "ERR NOAUTH this server's sign-in has no password to change")
        self.assertEqual(await say(srv, c, "TOK abcd"), "ERR UNKNOWN no such request")
        self.assertEqual(await say(srv, c, "OUT"), "OK 1")
        self.assertEqual(await say(srv, frontend(), "SES " + r.split()[1]),
                         "ERR NOSESSION no such session; sign in again")


class PasswordPassword(unittest.IsolatedAsyncioTestCase):
    async def test_sign_up_sign_in_lockout_change_reset(self):
        srv = server("password")
        self.assertEqual(srv.auth.greeting(), "AUTH=password SIGNUP=password")
        s = public()
        out = await srv.dispatch(s, "HELO bo")
        self.assertEqual(out, ["INF bo is new here. To sign up, choose a password."] + RULE +
                         ["ASK password Choose_a_password secret cred new"])
        self.assertEqual(await srv.dispatch(s, "AUT password short"),
                         inf("That password is too short. " + auth.PW_RULE) +
                         ["ASK password Choose_a_password secret cred new"])
        self.assertEqual((await srv.dispatch(s, "AUT password only lowercase"))[:-1],
                         inf("That password uses only two of the four kinds. " + auth.PW_RULE))
        await say(srv, s, "AUT password Bo secret 1")                       # spaces are kept
        self.assertEqual(await srv.dispatch(s, "AUT confirm Bo secret 2"),
                         inf("The two did not match. " + auth.PW_RULE) +
                         ["ASK password Choose_a_password secret cred new"])
        await say(srv, s, "AUT password Bo secret 1")
        self.assertEqual(await say(srv, s, "AUT confirm Bo secret 1"), "OK -")
        row = srv.store.db.execute("SELECT cred, temp, pending FROM players WHERE key='bo'").fetchone()
        self.assertTrue(row[0].startswith("scrypt$") and "secret" not in row[0])
        self.assertEqual(row[1:], ("", 0))
        # sign in: the name, then the password when asked (never on the HELO line)
        self.assertEqual(await say(srv, public(), "HELO bo bo-secret-1"),
                         "ERR SYNTAX HELO name, then the password when it is asked")
        f = frontend()
        self.assertEqual(await say(srv, f, "HELO bo"), "ASK password Password secret cred")
        self.assertRegex(await say(srv, f, "AUT password Bo secret 1"), SID)
        # wrong password: locked a minute, even the right one waits
        c = public()
        await say(srv, c, "HELO bo")
        self.assertRegex(await say(srv, c, "AUT password nope nope"),
                         r"^ERR LOCKED wait=60 Wrong password; try again in 60 seconds$")
        self.assertTrue((await say(srv, public(), "HELO bo")).startswith("ERR LOCKED wait="))
        srv.players["bo"].locked_until = 0
        # change: current, new, again; other sessions end, this one stays
        other = frontend()
        await say(srv, other, "HELO bo")
        await say(srv, other, "AUT password Bo secret 1")
        self.assertEqual(await say(srv, f, "PWD"), "ASK current Current_password secret")
        self.assertEqual(await say(srv, f, "AUT current Bo secret 1"), "ASK password New_password secret new")
        self.assertTrue((await say(srv, f, "AUT password weakweak")).startswith("ASK password New_password"))
        self.assertEqual(await say(srv, f, "AUT password New secret 9"), "ASK confirm Type_it_again secret new")
        self.assertEqual(await say(srv, f, "AUT confirm New secret 9"), "OK")
        self.assertEqual(await say(srv, f, "LST"), "OK")
        self.assertEqual(await say(srv, other, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        c = public()
        await say(srv, c, "HELO bo")
        self.assertEqual(await say(srv, c, "AUT password New secret 9"), "OK -")
        # the operator's reset: a temporary password; the old one stops working, then a new one is chosen
        good, text, code = await srv.accounts.reset("bo")
        self.assertTrue(good)
        self.assertTrue(code in text, "the text gives the password")
        self.assertTrue(re.fullmatch(TEMP_FORM, code), "not a temporary password")
        self.assertEqual(await say(srv, f, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        c = public()
        await say(srv, c, "HELO bo")
        self.assertTrue((await say(srv, c, "AUT password New secret 9")).startswith("ERR LOCKED"))
        srv.players["bo"].locked_until = 0
        c = public()
        await say(srv, c, "HELO bo")
        self.assertEqual(await srv.dispatch(c, "AUT password " + code),
                         ["INF That was a temporary password. Choose a password of your own now."] + RULE +
                         ["ASK password New_password secret new"])
        await say(srv, c, "AUT password Third secret")
        self.assertEqual(await say(srv, c, "AUT confirm Third secret"), "OK -")
        c = public()
        await say(srv, c, "HELO bo")
        self.assertTrue((await say(srv, c, "AUT password " + code)).startswith("ERR LOCKED"))   # used up


class EmailSignUp(unittest.IsolatedAsyncioTestCase):
    async def test_a_password_by_mail_then_one_of_your_own(self):
        srv = server("password", "email")
        self.assertEqual(srv.auth.greeting(), "AUTH=password SIGNUP=email")
        s = public()
        self.assertEqual(await srv.dispatch(s, "HELO eli"), ["INF eli is not signed up on this server.",
                                                            "ASK confirm Sign_up_as_eli?_(y/n) yn"])
        self.assertEqual(await srv.dispatch(s, "AUT confirm y"),
                         ["INF We email you a password to sign in with; then you choose your own.",
                          "ASK email Email_address"])
        self.assertEqual(await srv.dispatch(s, "AUT email Eli@Example.org"),
                         ["INF We emailed a password for eli to eli@example.org.",
                          "INF It works for 24 hours. Type it here, or sign in with it later.",
                          "INF If you haven't received the email, it may take a while;",
                          "INF you can ask again after 7 days.",
                          "ASK password Password_from_the_email secret"])
        # the mail: the name, the password, where to sign in, and that it expires
        mail = srv.auth.mailer.last
        self.assertEqual((mail["to"], mail["subject"]), ("eli@example.org", "Your ParlorNet password"))
        pw = mail_password(srv)
        self.assertTrue(re.fullmatch(TEMP_FORM, pw), "not a temporary password")
        body = mail["body"]
        for want in ("Name:      eli", "It works for 24 hours.", auth.SIGNUP_LAST,
                     "The first time you sign in with it, you choose a password of your own.",
                     "Sign in as eli where you signed up."):                   # no web or telnet declared here
            self.assertTrue(want in body, want)
        self.assertTrue(auth.SIGNUP_LAST.startswith("Nothing is kept until you sign in with this password."))
        self.assertTrue(auth.SIGNUP_LAST.endswith("If you did not sign up for an account, you can ignore this "
                                                  "message and it will expire shortly."))
        # the pending account: the name is held, only a hash of the password is kept
        p = srv.players["eli"]
        self.assertGreater(p.pending, time.time() + auth.PENDING_SECS - 60)
        self.assertEqual((p.cred, p.email, p.verified), ("", "eli@example.org", False))
        row = srv.store.db.execute("SELECT temp FROM players WHERE key='eli'").fetchone()[0]
        self.assertTrue(row.startswith(str(int(p.pending))[:5]) and pw.replace("-", "") not in row, "stored as is")
        self.assertEqual(await say(srv, public(), "REG eli"), "ERR NAMEINUSE the name eli is taken; choose another")
        # the first sign-in with it confirms the account and asks at once for a password of the player's own
        self.assertEqual(await srv.dispatch(s, "AUT password " + pw.upper()),
                         ["INF That was a temporary password. Choose a password of your own now."] + RULE +
                         ["ASK password New_password secret new"])
        self.assertEqual((p.pending, p.verified), (0, True))
        self.assertEqual(await say(srv, s, "LST"), "ERR NOHELO say HELO first")      # not signed in yet
        self.assertEqual((await srv.dispatch(s, "AUT password " + pw))[:-1],
                         inf("Choose a password of your own, not the one we emailed. " + auth.PW_RULE))
        self.assertEqual((await srv.dispatch(s, "AUT password elielieli"))[:-1],
                         inf("That password uses only one of the four kinds. " + auth.PW_RULE))
        self.assertEqual(await say(srv, s, "AUT password Eli's own 1"), "ASK confirm Type_it_again secret new")
        self.assertEqual(await say(srv, s, "AUT confirm Eli's own 1"), "OK -")
        self.assertEqual(await say(srv, s, "LST"), "OK")
        self.assertEqual((p.temp, p.pending), ("", 0))
        self.assertTrue(auth.check_password("Eli's own 1", p.cred))
        c = public()
        self.assertEqual(await srv.dispatch(c, "USER eli"),
                         ["INF Forgot your password? Answer reset and we email you a new one.",
                          "ASK password Password secret cred"])
        self.assertEqual(await say(srv, c, "AUT password Eli's own 1"), "OK -")
        c = public()
        await say(srv, c, "USER eli")
        self.assertTrue((await say(srv, c, "AUT password " + pw)).startswith("ERR LOCKED"))   # used up

    async def test_signing_in_later_and_the_mail_names_where(self):
        srv = server("password", "email")
        srv.protocols[object()] = {"HTTP": [6500, "HTTP", ""], "TELNET": [6502, "Telnet", ""]}
        s, pw = await email_signup(srv, "fia", "fia@example.org")
        self.assertTrue("Sign in as fia on the web at http://test:6500/ or by telnet to test port 6502."
                        in srv.auth.mailer.last["body"], "where to sign in")
        s.closing = True                                     # the dialog ends; the account waits
        f = frontend()
        self.assertEqual(await srv.dispatch(f, "USER fia"),
                         ["INF Type the password we emailed you.", "ASK password Password secret cred"])
        self.assertEqual(await say(srv, f, "AUT password not-the-one"),
                         "ERR LOCKED wait=60 Wrong password; try again in 60 seconds")
        srv.players["fia"].locked_until = 0
        f = frontend()
        await say(srv, f, "USER fia")
        r = await say(srv, f, "AUT password reset")                 # no reset before it is confirmed: a wrong one
        self.assertTrue(r.startswith("ERR LOCKED wait=") and "Wrong password" in r, r)
        f = frontend()
        await say(srv, f, "USER fia")
        self.assertRegex(await first_sign_in(srv, f, pw), SID)    # a temporary password works while locked

    async def test_a_pending_account_expires_after_a_day(self):
        srv = server("password", "email")
        s, pw = await email_signup(srv, "gus", "gus@example.org")
        now = time.time()
        self.assertEqual(srv.accounts.expire(now + auth.PENDING_SECS - 60), [])
        self.assertIsNotNone(srv.players.get("gus"))
        self.assertEqual(srv.accounts.signups.status()["pending"], 1)
        srv.accounts.sweep(now + auth.PENDING_SECS + 1)                          # the core's sweep, a day on
        self.assertIsNone(srv.players.get("gus"))
        self.assertIsNone(srv.store.db.execute("SELECT key FROM players WHERE key='gus'").fetchone())
        self.assertEqual(await say(srv, public(), "USER gus"), "ERR NOPLAYER no account named gus here")
        self.assertEqual(await say(srv, public(), "REG gus"), "ASK email Email_address")   # the name is free
        # a confirmed account is never expired, even if no password was chosen yet
        s, pw = await email_signup(srv, "hal", "hal@example.org")
        self.assertEqual(await say(srv, s, "AUT password " + pw), "ASK password New_password secret new")
        srv.accounts.sweep(now + 2 * auth.PENDING_SECS)
        self.assertIsNotNone(srv.players.get("hal"))
        self.assertEqual(srv.players["hal"].temp, "")                           # its temporary password ended

    async def test_forgotten_password(self):
        srv = server("password", "email")
        s, pw = await email_signup(srv, "ida", "ida@example.org")
        self.assertEqual(await first_sign_in(srv, s, pw, "Ida's first 1"), "OK -")
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        s = public()
        await say(srv, s, "USER ida")
        self.assertEqual(await srv.dispatch(s, "AUT password reset"),
                         ["INF We emailed a new password to the address on file.",
                          "INF It works for 24 hours; your old password works until you use it.",
                          "INF Type the new one here, or sign in with it later.", "ASK password Password secret"])
        mail = srv.auth.mailer.last
        self.assertEqual((mail["to"], mail["subject"]), ("ida@example.org", "Your new ParlorNet password"))
        self.assertTrue("Until then your old password keeps working." in mail["body"], "the old one works")
        new = mail_password(srv)
        self.assertTrue(new != pw, "a new password")
        # the old password still works until the new one is used
        self.assertEqual(await say(srv, s, "AUT password Ida's first 1"), "OK -")
        c = public()
        await say(srv, c, "USER ida")
        self.assertEqual(await first_sign_in(srv, c, new, "Ida's second 2"), "OK -")
        self.assertEqual(srv.store.email_count(0, name="ida", kind="recovery-own")[0], 1)    # the owner's, then
        c = public()
        await say(srv, c, "USER ida")
        self.assertTrue((await say(srv, c, "AUT password Ida's first 1")).startswith("ERR LOCKED"))
        # the limits: two recovery mails for a name from one client address a day
        srv.players["ida"].locked_until = 0
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        c = public()
        await say(srv, c, "USER ida")
        self.assertEqual(await say(srv, c, "AUT password reset"), "ASK password Password secret")
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        c = public()
        await say(srv, c, "USER ida")
        self.assertTrue((await say(srv, c, "AUT password reset")).startswith(
            "ERR BUSY wait=86400 You asked for this name's recovery twice today"))
        # the mail log keeps a keyed hash of the address, never the address
        rows = srv.store.db.execute("SELECT addr FROM email_log").fetchall()
        self.assertTrue(rows and all("@" not in r[0] and len(r[0]) == 64 for r in rows))

    async def test_email_rules_and_caps(self):
        srv = server("password", "email")
        s, pw = await email_signup(srv, "dee", "dee@example.org")
        await first_sign_in(srv, s, pw)
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        s = public()
        await say(srv, s, "HELO eve")
        self.assertEqual(await say(srv, s, "AUT confirm n"), "ERR CANCELLED not signed up")
        await say(srv, s, "HELO eve")
        await say(srv, s, "AUT confirm y")
        self.assertTrue((await say(srv, s, "AUT email not-an-address")).startswith("ASK email"))
        # an address in use: the same answer as a free one, and nothing signs in to that account. dee's own
        # sign-up was that address's one sign-up email for the cooldown, so nothing goes now
        last = srv.auth.mailer.last
        self.assertEqual(await say(srv, s, "AUT email dee@example.org"), "ASK password Password_from_the_email secret")
        self.assertIs(srv.auth.mailer.last, last)
        self.assertEqual(srv.players["eve"].email, None)
        self.assertTrue(srv.players["eve"].pending and srv.players["eve"].temp)    # the same shape as any
        self.assertEqual(srv.accounts.signups.clear("dee@example.org"), 1)    # the operator clears it:
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        s = public()
        await say(srv, s, "HELO eve2")
        await say(srv, s, "AUT confirm y")
        self.assertEqual(await say(srv, s, "AUT email dee@example.org"), "ASK password Password_from_the_email secret")
        self.assertEqual(srv.auth.mailer.last["to"], "dee@example.org")      # its owner gets a notice
        self.assertTrue("It already has a\nname there: dee" in srv.auth.mailer.last["body"], "the notice")
        self.assertTrue("Password:" not in srv.auth.mailer.last["body"], "a notice holds no password")
        self.assertEqual(srv.accounts.player_by_email("dee@example.org").name, "dee")
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        for i in range(3):
            s = public()
            await say(srv, s, "HELO fay%d" % i)
            await say(srv, s, "AUT confirm y")
            r = await say(srv, s, "AUT email same@example.org")
        self.assertTrue(r.startswith("ERR BUSY wait="), r)               # a minute between mails
        srv.store.db.execute("UPDATE email_log SET at = at - 120")
        for i in range(2):
            s = public()
            await say(srv, s, "HELO gil%d" % i)
            await say(srv, s, "AUT confirm y")
            await say(srv, s, "AUT email same@example.org")
            srv.store.db.execute("UPDATE email_log SET at = at - 61")
        s = public()
        await say(srv, s, "HELO fay9")
        await say(srv, s, "AUT confirm y")
        self.assertEqual(await say(srv, s, "AUT email same@example.org"),
                         "ERR BUSY wait=3600 Too many emails to this address; try again in an hour")
        self.assertIsNone(srv.players.get("fay9"))                           # refused: nothing was made
        srv.store.block_email("bounced@example.org", "test")
        s = public()
        await say(srv, s, "HELO hal")
        await say(srv, s, "AUT confirm y")
        self.assertEqual(await say(srv, s, "AUT email bounced@example.org"),
                         "ERR NOMAIL mail to that address cannot be delivered")

    async def test_clients_that_cannot_prompt(self):
        """CAN: a client that can only answer a secret question (its password field) is refused a sign-up
        cleanly, and a pending account's sign-in with a plain pointer to the web or telnet, where the new
        password can be chosen; once chosen, it signs in there like any client."""
        srv = server("password", "email")
        s, pw = await email_signup(srv, "ola", "ola@example.org")
        for kinds in ("secret", "secret,info"):
            c = frontend("outland")
            self.assertEqual(await say(srv, c, "CAN " + kinds), "OK " + ",".join(sorted(kinds.split(","))))
            if kinds == "secret":
                self.assertEqual(await say(srv, c, "REG newbie"),
                                 "ERR CANNOT this client cannot show the next step; use the web or telnet")
            self.assertEqual(await say(srv, c, "USER ola"), "ASK password Password secret cred")   # the note is optional
            r = await srv.dispatch(c, "AUT password " + pw)
            if kinds == "secret":
                self.assertEqual(r, ["ERR NEWPASS Sign in on the web or by telnet first to choose your password."])
            else:                   # it can show lines: the step is sent, and the adapter cannot answer it
                self.assertEqual(r[-1], "ASK password New_password secret new")
            self.assertIsNone(c.pending if kinds == "secret" else None)
        self.assertEqual(srv.players["ola"].pending, 0)                         # confirmed all the same
        t = public()
        await say(srv, t, "USER ola")
        self.assertEqual(await first_sign_in(srv, t, pw, "Ola's own 1"), "OK -")
        c = frontend("outland")
        await say(srv, c, "CAN secret")
        await say(srv, c, "USER ola")
        self.assertRegex(await say(srv, c, "AUT password Ola's own 1"), SID)
        self.assertTrue((await say(srv, frontend(), "CAN pictures")).startswith("ERR SYNTAX"))
        out = await srv.dispatch(public(), "USER ola")                           # every line fits 80 bytes
        self.assertTrue(all(len(l) <= 78 for l in out), out)

    async def test_at_most_so_many_pending(self):
        srv = server("password", "email")
        srv.settings["signup.max_pending"] = "2"
        for i, name in enumerate(("pa", "pb")):
            srv.store.db.execute("UPDATE email_log SET at = at - 120")
            await email_signup(srv, name, "%s@example.org" % name, Session(None))
        r = await say(srv, public(), "REG pc")
        self.assertTrue(r.startswith("ERR BUSY wait=8640") and "Too many sign-ups in progress" in r, r)
        srv.accounts.expire(time.time() + auth.PENDING_SECS + 1)
        self.assertEqual(await say(srv, public(), "REG pc"), "ASK email Email_address")


class Sessions(unittest.IsolatedAsyncioTestCase):
    async def signed_in(self, srv, name, conn=None):
        conn = conn or frontend()
        r = await say(srv, conn, "HELO %s" % name)
        self.assertRegex(r, SID)
        return conn, r.split()[1]

    async def test_resume_only_on_the_frontend_channel_and_within_expiry(self):
        srv = server("none")
        _, sid = await self.signed_in(srv, "sam")
        self.assertEqual(await say(srv, public(), "SES " + sid), "ERR NOTFRONTEND SES is only for frontends")
        self.assertEqual(await say(srv, frontend(), "SES " + sid), "OK sam")
        self.assertTrue((await say(srv, frontend("telnet"), "SES " + sid)).startswith("ERR NOSESSION"))
        rows = srv.store.db.execute("SELECT * FROM sessions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(sid, repr(rows))                   # only the hash is stored
        srv.settings["session.idle_secs"] = "5"
        next(iter(srv.accounts.sessions.values())).used -= 6
        self.assertTrue((await say(srv, frontend(), "SES " + sid)).startswith("ERR NOSESSION"))
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        srv.settings["session.max_secs"] = "60"
        _, sid = await self.signed_in(srv, "sam")
        c = frontend()
        self.assertEqual(await say(srv, c, "SES " + sid), "OK sam")
        self.assertEqual(await say(srv, c, "LST"), "OK")
        next(iter(srv.accounts.sessions.values())).created -= 61
        self.assertEqual(await say(srv, c, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertEqual(await say(srv, c, "LST"), "ERR NOHELO say HELO first")
        self.assertTrue((await say(srv, frontend(), "SES " + sid)).startswith("ERR NOSESSION"))

    async def test_out_and_out_all(self):
        srv = server("none")
        a, sid_a = await self.signed_in(srv, "una")
        b, sid_b = await self.signed_in(srv, "una")
        p = public()
        self.assertEqual(await say(srv, p, "HELO una"), "OK -")
        other, sid_o = await self.signed_in(srv, "vic")
        self.assertEqual(await say(srv, a, "OUT"), "OK 1")
        self.assertEqual(await say(srv, a, "LST"), "ERR NOHELO say HELO first")
        self.assertTrue((await say(srv, frontend(), "SES " + sid_a)).startswith("ERR NOSESSION"))
        self.assertEqual(await say(srv, frontend(), "SES " + sid_b), "OK una")
        self.assertEqual(await say(srv, b, "OUT all"), "OK 2")
        self.assertEqual(await say(srv, p, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertTrue((await say(srv, frontend(), "SES " + sid_b)).startswith("ERR NOSESSION"))
        self.assertEqual(await say(srv, frontend(), "SES " + sid_o), "OK vic")
        self.assertEqual(await say(srv, public(), "OUT"), "ERR NOHELO say HELO first")
        self.assertEqual(await say(srv, other, "OUT everything"), "ERR SYNTAX OUT [all]")

    async def test_suspend_delete_and_method_change_end_sessions(self):
        srv = server("none")
        live, sid = await self.signed_in(srv, "wes")
        self.assertTrue(srv.accounts.suspend("wes"))
        self.assertEqual(await say(srv, live, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertTrue((await say(srv, frontend(), "SES " + sid)).startswith("ERR NOSESSION"))
        self.assertEqual(await say(srv, frontend(), "HELO wes"), "ERR SUSPENDED this name is suspended")
        srv.accounts.suspend("wes", False)
        live, sid = await self.signed_in(srv, "wes")
        self.assertTrue(srv.accounts.delete("wes"))
        self.assertEqual(await say(srv, live, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertTrue((await say(srv, frontend(), "SES " + sid)).startswith("ERR NOSESSION"))
        live, sid = await self.signed_in(srv, "xan")
        srv.set_setting("auth.signin", "password")       # sessions made under none do not carry over
        self.assertEqual(srv.auth.name, "password+password")
        self.assertEqual(await say(srv, live, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertTrue((await say(srv, frontend(), "SES " + sid)).startswith("ERR NOSESSION"))

    async def test_failed_resumes_are_refused_after_three(self):
        srv = server("none")
        _, sid = await self.signed_in(srv, "yul")
        c = frontend()
        for bad in ("x", "A" * 22, "B" * 22):
            self.assertTrue((await say(srv, c, "SES " + bad)).startswith("ERR NOSESSION"))
        self.assertEqual(await say(srv, c, "SES " + sid), "ERR RATE too many failed SES on this connection")
        self.assertEqual(await say(srv, frontend(), "SES " + sid), "OK yul")

    async def test_sessions_survive_a_restart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            srv = server("none", None, db)
            _, sid = await self.signed_in(srv, "zed")
            srv2 = server("none", None, db)
            srv2.load_state()
            self.assertEqual(await say(srv2, frontend(), "SES " + sid), "OK zed")


class OldDatabase(unittest.TestCase):
    def test_an_earlier_format_is_set_aside(self):
        """A database from before mailed passwords (players with the columns of the earlier format) is
        not migrated: it is renamed aside and a new one starts, with nothing of the old in it."""
        import sqlite3
        import tempfile
        from contextlib import redirect_stderr
        from io import StringIO
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "parlord.db")
            con = sqlite3.connect(db)
            con.executescript("CREATE TABLE players (key TEXT PRIMARY KEY, name TEXT NOT NULL, token TEXT NOT NULL,"
                              " created REAL NOT NULL, last_seen REAL NOT NULL, cred TEXT NOT NULL DEFAULT '');"
                              "INSERT INTO players VALUES ('old', 'old', '', 0, 0, '');"
                              "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                              "INSERT INTO meta VALUES ('set:server.name', 'before');")
            con.commit()
            con.close()
            err = StringIO()
            with redirect_stderr(err):
                srv = server("password", "email", db)
            self.assertIn("in an earlier format", err.getvalue())
            aside = [f for f in os.listdir(d) if f.startswith("parlord.db.old-")]
            self.assertEqual(len([f for f in aside if not f.endswith(("-wal", "-shm"))]), 1, aside)
            self.assertIsNone(srv.players.get("old"))
            self.assertNotEqual(srv.settings.get("server.name"), "before")
            srv.load_state()
            srv.store.db.close()
            con = sqlite3.connect(os.path.join(d, [f for f in aside if not f.endswith(("-wal", "-shm"))][0]))
            self.assertEqual(con.execute("SELECT name FROM players").fetchall(), [("old",)])   # kept as it was
            con.close()
            srv2 = server("password", "email", db)                 # the new one is opened as it is
            self.assertEqual(len([f for f in os.listdir(d) if f.startswith("parlord.db.old-")]), len(aside))
            srv2.store.db.close()


if __name__ == "__main__":
    unittest.main()
