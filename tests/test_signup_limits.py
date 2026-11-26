"""The sign-up limits (docs/accounts.md, Sign-up limits; docs/security-design.md): one sign-up email per
address in signup.address_cooldown, at most signup.max_pending pending accounts (sign-ups nobody has signed
into yet), each deleted after auth.PENDING_SECS; addresses in the mail tables kept only as keyed hashes.
Driven through the core's request dispatch as tests/test_auth.py, and through the web, telnet and FIBS
adapters for the refusal when sign-up is full. No test prints a password or a mail's body.

    python3 -m unittest discover -s tests -p "test_signup_limits.py"
"""
import json
import os
import re
import tempfile
import time
import unittest

from test_auth import first_sign_in, mail_password, say, server
from test_core import ROOT, adapter_args, free_port, make_keys, stack_args

import sys
sys.path.insert(0, ROOT)
from parlord import admin as PA, auth  # noqa: E402
from parlord.core import Session  # noqa: E402
from parlord.store import Store  # noqa: E402

FULL = "Too many sign-ups in progress"
ASKED = "ASK password Password_from_the_email secret"      # the step after the address, whatever happened
RESET_ASKED = "ASK password Password secret"


def public(source=None):
    return Session(None, source=source)


def mails(srv):
    """The last mail that went out (the mailer's last one changes with each)."""
    return srv.auth.mailer.__dict__.get("last")


async def reach_password(srv, name, addr, source=None):
    """REG name, then the address: (connection, the reply's lines)."""
    s = public(source)
    out = await srv.dispatch(s, "REG %s" % name)
    if not out[-1].startswith("ASK email"):
        return s, out
    return s, await srv.dispatch(s, "AUT email %s" % addr)


def admin(srv):
    adm, conn = PA.Admin(srv), PA.AdminConn()
    conn.state = "admin"
    return adm, conn


def shift_log(srv, secs=61):
    srv.store.db.execute("UPDATE email_log SET at = at - ?", (secs,))   # past the minute between mails


def owner(srv, name, addr):
    """An account with a password and an address on file (made directly)."""
    p = srv.accounts.player(name, create=True)
    p.cred, p.email, p.verified = auth.hash_password("Owner's own 1"), addr, True
    srv.accounts.save_player(p)
    return p


def same_lines(lines, name):
    """The reply with the name taken out (the lines after the address name the account)."""
    return [l.replace(name, "NAME") for l in lines]


class OneEmailPerAddress(unittest.IsolatedAsyncioTestCase):
    async def test_a_second_sign_up_sends_nothing_and_looks_the_same(self):
        srv = server("password", "email")
        _, first = await reach_password(srv, "ann", "same@example.org", "203.0.113.1")
        sent = mails(srv)
        self.assertEqual(sent["to"], "same@example.org")
        pw = mail_password(srv)
        shift_log(srv)
        # another name, another client address, the address typed otherwise: no mail, the same lines
        s, second = await reach_password(srv, "bob", "  Same@Example.ORG ", "198.51.100.2")
        self.assertIs(mails(srv), sent)
        self.assertEqual(same_lines(second, "bob"), same_lines(first, "ann"))
        self.assertIn("INF If you haven't received the email, it may take a while;", second)
        self.assertIn("INF you can ask again after 7 days.", second)
        # counted against the caps as if it had been sent (the caps cannot tell them apart)
        self.assertEqual(srv.store.email_count(time.time() - 3600, addr="same@example.org")[0], 2)
        # nothing signs in to the held one, not even the password mailed for ann
        r = await say(srv, s, "AUT password " + pw)
        self.assertTrue(r.startswith("ERR WRONG") and "Wrong password" in r, r)
        bob = srv.accounts.player("bob")
        self.assertTrue(bob.pending and bob.temp and bob.email is None, "a pending account like any")

    async def test_the_cooldown_ends(self):
        srv = server("password", "email")
        await reach_password(srv, "ann", "a@example.org")
        sent = mails(srv)
        srv.store.db.execute("UPDATE signup_cooldown SET until = ?", (time.time() - 1,))
        shift_log(srv)
        await reach_password(srv, "bob", "a@example.org")
        self.assertIsNot(mails(srv), sent)                                  # past the cooldown: a mail again
        self.assertEqual(srv.accounts.signups.status()["cooldowns"], 1)

    async def test_an_owner_notice_is_the_addresss_one_email(self):
        srv = server("password", "email")
        owner(srv, "alice", "alice@example.org")
        _, first = await reach_password(srv, "probe1", "alice@example.org")
        notice = mails(srv)
        self.assertTrue("already has a\nname there: alice" in notice["body"], "the notice")
        shift_log(srv)
        _, second = await reach_password(srv, "probe2", "alice@example.org")
        self.assertIs(mails(srv), notice)                                   # no second notice
        self.assertEqual(same_lines(second, "probe2"), same_lines(first, "probe1"))
        shift_log(srv)
        _, free = await reach_password(srv, "probe3", "free@example.org")   # a free address: the same lines
        self.assertEqual([l.replace("free@", "alice@") for l in same_lines(free, "probe3")],
                         same_lines(first, "probe1"))

    async def test_the_operator_clears_one_address_or_all(self):
        srv = server("password", "email")
        adm, conn = admin(srv)
        for i, addr in enumerate(("a@example.org", "b@example.org", "c@example.org")):
            await reach_password(srv, "n%d" % i, addr)
            shift_log(srv)
        st = (await adm.dispatch(conn, {"op": "signups"}))["signups"]
        self.assertEqual((st["cooldowns"], st["pending"], st["max_pending"], st["pending_ttl"],
                          st["address_cooldown"]), (3, 3, 60, 86400, 7 * 86400))
        r = await adm.dispatch(conn, {"op": "clear_cooldown", "address": " A@Example.org"})
        self.assertEqual((r["ok"], r["cleared"], r["signups"]["cooldowns"]), (True, 1, 2))
        self.assertFalse((await adm.dispatch(conn, {"op": "clear_cooldown", "address": "nope"}))["ok"])
        before = mails(srv)
        await reach_password(srv, "m1", "a@example.org")                     # cleared: it gets a mail again
        self.assertIsNot(mails(srv), before)
        shift_log(srv)
        before = mails(srv)
        await reach_password(srv, "m2", "b@example.org")                     # not cleared: nothing
        self.assertIs(mails(srv), before)
        r = await adm.dispatch(conn, {"op": "clear_cooldowns"})
        self.assertEqual((r["cleared"], r["signups"]["cooldowns"]), (3, 0))
        shift_log(srv)
        await reach_password(srv, "m3", "b@example.org")
        self.assertEqual(mails(srv)["to"], "b@example.org")
        status = (await adm.dispatch(conn, {"op": "status"}))["signups"]
        self.assertEqual(status["cooldowns"], 1)
        # the address typed in the admin menus is not kept: the mail tables hold hashes only, and the
        # address is on no row but the pending accounts its passwords went to
        dump = "\n".join(l for l in srv.store.db.conn.iterdump() if not l.startswith('INSERT INTO "players"'))
        self.assertTrue("a@example.org" not in dump.lower(), "an address outside the players table")
        rows = srv.store.db.execute("SELECT key FROM players WHERE email='a@example.org'").fetchall()
        self.assertEqual(sorted(rows), [("m1",), ("n0",)])

    async def test_recovery_mail_is_unaffected(self):
        srv = server("password", "email")
        s, _ = await reach_password(srv, "cy", "cy@example.org")
        self.assertEqual(await first_sign_in(srv, s, mail_password(srv), "Cy's own 1"), "OK -")
        self.assertEqual(srv.accounts.signups.status()["cooldowns"], 1)       # cy's address is in its cooldown
        for _ in range(2):                                                  # and recovery mail still goes, twice
            shift_log(srv)
            s = public("192.0.2.5")
            await say(srv, s, "HELO cy")
            before = mails(srv)
            self.assertEqual(await say(srv, s, "AUT password reset"), RESET_ASKED)
            self.assertIsNot(mails(srv), before)
            self.assertEqual(mails(srv)["to"], "cy@example.org")


class PendingSignUps(unittest.IsolatedAsyncioTestCase):
    WAIT = r"^ERR BUSY wait=(\d+) Too many sign-ups in progress; try again in [\d.]+ hours$"

    async def test_the_cap_refuses_one_more(self):
        srv = server("password", "email")
        srv.settings["signup.max_pending"] = "3"
        for i in range(3):
            _, out = await reach_password(srv, "p%d" % i, "p%d@example.org" % i)
            self.assertEqual(out[-1], ASKED)
            shift_log(srv)
        late = public()                                     # started before the cap filled: refused at the address
        srv.settings["signup.max_pending"] = "4"
        await say(srv, late, "REG late")
        srv.settings["signup.max_pending"] = "3"
        r = await say(srv, late, "AUT email late@example.org")
        self.assertRegex(r, self.WAIT)
        self.assertLessEqual(int(re.search(r"wait=(\d+)", r).group(1)), auth.PENDING_SECS + 1)
        self.assertIsNone(srv.accounts.player("late"))
        before = mails(srv)
        for line in ("REG p9", "HELO p9"):                  # number max_pending + 1: refused at once, no mail
            r = await say(srv, public(), line)
            self.assertTrue(r.startswith("ERR BUSY wait=") and FULL in r, r)
        self.assertIs(mails(srv), before)
        self.assertEqual(srv.accounts.signups.count(), 3)
        # a sign-in with the mailed password confirms the account and frees its place
        srv2 = server("password", "email")
        srv2.settings["signup.max_pending"] = "1"
        s, _ = await reach_password(srv2, "q0", "q0@example.org")
        self.assertTrue(FULL in await say(srv2, public(), "REG q1"))
        self.assertEqual(await say(srv2, s, "AUT password " + mail_password(srv2)),
                         "ASK password New_password secret new")
        self.assertEqual(await say(srv2, public(), "REG q1"), "ASK email Email_address")

    async def test_expiry_frees_the_place_and_the_name_and_keeps_the_cooldown(self):
        srv = server("password", "email")
        srv.settings["signup.max_pending"] = "1"
        s, _ = await reach_password(srv, "ann", "ann@example.org")
        first = mails(srv)
        pw = mail_password(srv)
        shift_log(srv)
        # while it is pending, the name is held: another address cannot take it
        _, out = await reach_password(srv, "ann", "other@example.org")
        self.assertEqual(out, ["ERR NAMEINUSE the name ann is taken; choose another"])
        self.assertTrue(FULL in await say(srv, public(), "REG bob"))
        # a day on, the core's sweep deletes it
        srv.accounts.sweep(time.time() + auth.PENDING_SECS + 1)
        self.assertEqual(srv.accounts.signups.count(), 0)
        self.assertIsNone(srv.accounts.player("ann"))
        self.assertEqual(await say(srv, s, "AUT password " + pw), "ERR STATE no such player")
        shift_log(srv)
        s2, out = await reach_password(srv, "ann", "ann2@example.org")          # the name is free again
        self.assertEqual(out[-1], ASKED)
        self.assertEqual(mails(srv)["to"], "ann2@example.org")
        self.assertEqual(await first_sign_in(srv, s2, mail_password(srv)), "OK -")
        shift_log(srv)
        before = mails(srv)
        await reach_password(srv, "cat", "ann@example.org")                     # the address's cooldown stayed
        self.assertIs(mails(srv), before)
        self.assertIsNot(before, first)

    async def test_a_pending_name_is_taken_and_signs_in_later(self):
        """A dialog that ended (a web page left too long) is not taken up again by signing up anew: the
        name is taken. The mailed password signs in to it later, from anywhere that can ask."""
        srv = server("password", "email")
        s, first = await reach_password(srv, "ann", "ann@example.org")
        sent = mails(srv)
        pw = mail_password(srv)
        s.pending = None
        shift_log(srv)
        _, again = await reach_password(srv, "ANN", "ann@example.org")
        self.assertEqual(again, ["ERR NAMEINUSE the name ANN is taken; choose another"])
        self.assertIs(mails(srv), sent)
        c = public()
        self.assertEqual((await srv.dispatch(c, "USER ann"))[-1], "ASK password Password secret cred")
        self.assertEqual(await first_sign_in(srv, c, pw), "OK -")
        self.assertEqual(srv.accounts.signups.count(), 0)
        self.assertTrue("It works for 24 hours." in sent["body"], "says how long")

    async def test_the_sign_up_mail_says_nothing_is_kept_yet(self):
        """A sign-up's mail says nothing is kept until its password signs in, and that it expires; a
        forgotten password's mail does not (the account is there)."""
        srv = server("password", "email")
        await reach_password(srv, "ann", "ann@example.org")
        sent = mails(srv)
        self.assertTrue(sent["body"].endswith(auth.SIGNUP_LAST + "\n"), "ends with SIGNUP_LAST")
        self.assertEqual(auth.SIGNUP_LAST, "Nothing is kept until you sign in with this password. If you did "
                                           "not sign up for an account, you can ignore this message and it "
                                           "will expire shortly.")
        p = owner(srv, "bea", "bea@example.org")
        self.assertIsNone(srv.auth.mail.forgot(public(), p, auth.temp_password()))
        self.assertTrue("Nothing is kept" not in mails(srv)["body"], "no sign-up words in a reset")

    async def test_wrong_passwords_at_a_pending_account_count(self):
        """A pending account's sign-in is like any: a wrong password counts toward its lockout; the mailed
        password still signs in while it is locked."""
        srv = server("password", "email")
        s, _ = await reach_password(srv, "ann", "ann@example.org")
        pw = mail_password(srv)
        self.assertEqual(await say(srv, s, "AUT password abcd-efgh-jkmn"),
                         "ERR WRONG Wrong password; try again")
        p = srv.accounts.player("ann")
        self.assertEqual(p.fails, 1)
        for want in ("ERR WRONG ", "ERR LOCKED wait=60 "):                  # the third locks it
            c = public()
            await say(srv, c, "USER ann")
            self.assertTrue((await say(srv, c, "AUT password abcd-efgh-jkmq")).startswith(want))
        self.assertEqual(p.fails, 3)
        c = public()
        await say(srv, c, "USER ann")
        self.assertTrue((await say(srv, c, "AUT password abcd-efgh-jkmp")).startswith("ERR LOCKED wait="))
        self.assertEqual(p.fails, 3)                                        # refused while locked, not counted
        c = public()
        await say(srv, c, "USER ann")
        self.assertEqual(await first_sign_in(srv, c, pw), "OK -")

    async def test_a_refused_mail_makes_nothing_and_starts_no_cooldown(self):
        srv = server("password", "email")
        srv.settings["email.paused"] = "1"
        _, out = await reach_password(srv, "ann", "ann@example.org")
        self.assertTrue(out[-1].startswith("ERR PAUSED"))
        self.assertEqual(srv.accounts.signups.status()["pending"], 0)
        self.assertEqual(srv.accounts.signups.status()["cooldowns"], 0)
        self.assertIsNone(srv.accounts.player("ann"))
        del srv.settings["email.paused"]
        _, out = await reach_password(srv, "ann", "ann@example.org")
        self.assertEqual(mails(srv)["to"], "ann@example.org")

    def test_settings(self):
        check = PA.SETTINGS["signup.address_cooldown"]
        self.assertEqual(check("7d"), "604800")
        self.assertEqual(check("86400"), "86400")
        self.assertEqual(PA.SETTINGS["signup.max_pending"](" 100 "), "100")
        self.assertNotIn("signup.pending_ttl", PA.SETTINGS)                 # fixed: a day
        self.assertEqual(auth.PENDING_SECS, 86400)
        for key, bad in (("signup.address_cooldown", "10m"), ("signup.max_pending", "0"),
                         ("signup.max_pending", "1d")):
            with self.assertRaises(ValueError):
                PA.SETTINGS[key](bad)
        self.assertEqual(auth.period_text(604800), "7 days")
        self.assertEqual(auth.period_text(3600), "1 hour")
        srv = server("password", "email")
        srv.settings["signup.max_pending"] = "junk"                         # a hand-edited file: the default
        self.assertEqual(srv.accounts.signups.setting("signup.max_pending"), 60)


class AddressesAsHashes(unittest.IsolatedAsyncioTestCase):
    async def test_the_database_keeps_no_address_that_is_not_an_account(self):
        """The mail tables keep addresses only as keyed hashes. A pending account keeps the address its
        password went to on its own row, until it is confirmed or deleted a day on."""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            srv = server("password", "email", db=db)
            s, _ = await reach_password(srv, "keep", "keeper@example.org")      # becomes an account
            await first_sign_in(srv, s, mail_password(srv))
            shift_log(srv)
            await reach_password(srv, "gone", "neverjoined@example.org")       # a sign-up never signed into
            shift_log(srv)
            await reach_password(srv, "held", "neverjoined@example.org")       # held by the cooldown
            srv.store.block_email("bounced@example.org", "refused by the mail server")
            _, out = await reach_password(srv, "bnc", "bounced@example.org")
            self.assertTrue(out[-1].startswith("ERR NOMAIL"))
            rows = lambda a: srv.store.db.execute("SELECT key FROM players WHERE email=?", (a,)).fetchall()  # noqa
            self.assertEqual(rows("neverjoined@example.org"), [("gone",)])      # the pending row only
            srv.accounts.sweep(time.time() + auth.PENDING_SECS + 1)
            self.assertEqual(rows("neverjoined@example.org"), [])
            self.assertEqual(rows("keeper@example.org"), [("keep",)])          # the account's own address
            srv.store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            data = b""
            for f in os.listdir(d):
                with open(os.path.join(d, f), "rb") as fh:
                    data += fh.read()
            data = data.lower()
            self.assertNotIn(b"bounced@example.org", data)
            self.assertIn(b"keeper@example.org", data)
            for table in ("email_log", "email_blocked", "signup_cooldown"):
                col = "h" if table == "signup_cooldown" else "addr"
                vals = [v for (v,) in srv.store.db.execute("SELECT %s FROM %s" % (col, table)).fetchall()]
                self.assertTrue(vals and all(re.fullmatch(r"[0-9a-f]{64}", v) for v in vals), table)
            h = srv.store.addr_hash("NeverJoined@example.org ")
            self.assertRegex(h, r"^[0-9a-f]{64}$")
            self.assertGreater(srv.store.cooldown_until(h), time.time())
            self.assertNotEqual(h, Store().addr_hash("neverjoined@example.org"))   # keyed per database


# ---- every frontend shows the refusal when sign-up is full -------------------------------------------

def spawn(*args):
    import subprocess
    p = subprocess.Popen([sys.executable, "-m"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    p.stdout.readline()
    return p


class FrontendsWhenFull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        conf = os.path.join(cls.dir.name, "settings.conf")
        cls.mail_file = os.path.join(cls.dir.name, "mail.jsonl")
        with open(conf, "w") as f:
            f.write("email.transport=test\nemail.test_file=%s\nsignup.max_pending=1\n" % cls.mail_file)
        cls.core, cls.fe = free_port(), free_port()
        cls.web, cls.tel, cls.fibs = free_port(), free_port(), free_port()
        cls.keys = make_keys("web", "telnet", "fibs")
        cls.procs = [spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password", "--signup", "email",
                           "--settings-file", conf)]
        for mod, port, name in (("adapters.web", cls.web, "web"), ("adapters.telnet", cls.tel, "telnet"),
                                ("adapters.fibs", cls.fibs, "fibs")):
            cls.procs.append(spawn(*adapter_args(mod, port, cls.fe, cls.keys, name)))

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.kill()
            p.wait()
            p.stdout.close()
        cls.dir.cleanup()

    def test_web_telnet_and_fibs(self):
        from test_adapters import Browser, Client
        from test_fibs import FibsW
        b = Browser(self.web)
        st, _, _, body = b.post("/signup.htm", {"n": "ann"})               # the one place: ann, not signed into
        lid = re.search(r'name="lid" value="([^"]+)"', body).group(1)
        st, _, _, body = b.post("/login.htm", {"lid": lid, "f": "email", "v": "ann@example.org"})
        self.assertIn("you can ask again after 7 days", body)
        with open(self.mail_file, encoding="utf-8") as f:                   # the mail, from the test capture
            sent = [json.loads(l) for l in f]
        self.assertEqual([(m["to"], m["subject"]) for m in sent], [("ann@example.org", "Your ParlorNet password")])
        st, _, _, body = Browser(self.web).post("/signup.htm", {"n": "bob"})  # web: New account
        self.assertEqual(st, 200)
        self.assertIn(FULL, body)
        c = Client(self.tel)                                                # telnet: new
        c.until("Login")
        c.send("new\r\n")
        c.until("Name you want")
        c.send("cyd\r\n")
        self.assertIn("Can't sign up: %s; try again in" % FULL, c.until("Login"))
        c.close()
        g = FibsW(self.fibs)                                                # FIBS: guest
        g.until_raw("login: ")
        g.send("guest")
        g.until_raw("\n>")
        g.send("name dora")
        self.assertIn("** %s; try again in" % FULL, g.until_raw("\n>"))
        g.close()


class NativeWhenFull(unittest.IsolatedAsyncioTestCase):
    async def test_reg_and_helo(self):
        srv = server("password", "email")
        srv.settings["signup.max_pending"] = "1"
        await reach_password(srv, "ann", "ann@example.org")
        out = await srv.dispatch(public(), "REG bob")
        self.assertEqual(len(out), 1)
        self.assertRegex(out[0], PendingSignUps.WAIT)
        self.assertLessEqual(len(out[0]) + 2, 80)
        self.assertTrue(FULL in await say(srv, public(), "HELO bob"))
        self.assertEqual(await say(srv, public(), "USER bob"), "ERR NOPLAYER no account named bob here")


if __name__ == "__main__":
    unittest.main()
