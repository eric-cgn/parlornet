"""Stay signed in (docs/accounts.md, Staying signed in; docs/protocol.md, KEY; docs/security-design.md, the
decision record): a sign-in key for a computer, asked for right after the password (KEY NEW) and given once;
signing in with it (AUT key) and the password when it is refused (ERR NOKEY); expiry after keys.idle_secs
unused, with a fake clock; forgetting one, all, and the one of this session; signing out, a password change and
the operator's reset forgetting them; wrong keys counting toward the lockout and the budgets; the comparison in
constant time; keys never in a log; the web's Stay signed in checkbox and its cookie, the profile's list, and
Play's cookie; the database gaining the table and the column. No test prints a key."""
import inspect
import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord import accounts as A, auth  # noqa: E402
from parlord.core import Session  # noqa: E402
from parlord.store import Store  # noqa: E402

from test_auth import first_sign_in, email_signup, frontend, public, say, server  # noqa: E402

PW, NEW_PW = "Swordfish 1997", "Swordfish 1998"
KEY = r"^OK [A-Za-z0-9_-]{22}$"


async def account(srv, name, conn=None):
    """A new account under sign-up password, signed in on conn (the public port unless given)."""
    c = conn or public()
    assert (await say(srv, c, "REG %s" % name)).startswith("ASK password")
    assert (await say(srv, c, "AUT password %s" % PW)).startswith("ASK confirm")
    assert (await say(srv, c, "AUT confirm %s" % PW)).startswith("OK")
    return c


async def key_for(srv, conn, label=""):
    r = await say(srv, conn, ("KEY NEW %s" % label).strip())
    assert re.match(KEY, r), r[:12]
    return r.split()[1]


async def key_in(srv, name, key, conn=None):
    """The last line of a sign-in with a key: USER, then AUT key."""
    c = conn or public()
    r = await say(srv, c, "USER %s" % name)
    assert r.startswith("ASK password"), r
    return await say(srv, c, "AUT key %s" % key), c


class Issuing(unittest.IsolatedAsyncioTestCase):
    async def test_a_key_right_after_the_password_signs_in_next_time(self):
        srv = server("password", "password")
        a = await account(srv, "bo")
        self.assertEqual(await say(srv, a, "CLI MACH Apple IIe"), "OK")
        key = await key_for(srv, a)
        self.assertEqual(len(key), 22)
        self.assertEqual(await say(srv, a, "KEY NEW"),
                         "ERR STATE a key is given right after signing in with your password")     # once
        self.assertTrue((await srv.dispatch(a, "KEY LST"))[0].endswith(" here Apple IIe"))     # the machine's name
        rows = srv.store.db.execute("SELECT * FROM signin_keys").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(key, repr(rows))                                      # only its SHA-256 is kept
        self.assertEqual(rows[0][2], A.sid_hash(key))
        r, b = await key_in(srv, "BO", key)
        self.assertEqual(r, "OK -")                                            # nothing else is asked
        self.assertEqual(await say(srv, b, "LST"), "OK")
        self.assertEqual(await say(srv, b, "KEY NEW"),
                         "ERR STATE a key is given right after signing in with your password")
        self.assertEqual(await srv.dispatch(b, "KEY LST"), [(await srv.dispatch(a, "KEY LST"))[0], "OK 1"])

    async def test_a_label_and_the_lines(self):
        srv = server("password", "password")
        a = await account(srv, "cy")
        await key_for(srv, a, "Commodore  64\x01 with a very long name past it")
        line = (await srv.dispatch(a, "KEY LST"))[0]
        self.assertRegex(line, r"^KEY \d+ \d{4}-\d\d-\d\d \d{4}-\d\d-\d\d here Commodore 64 with a very long na$")
        self.assertLessEqual(len(line), 78)
        b = await account(srv, "dee")
        await key_for(srv, b)
        self.assertTrue((await srv.dispatch(b, "KEY LST"))[0].endswith(" here unnamed"))

    async def test_only_after_a_password_on_this_connection(self):
        srv = server("password", "password")
        w = await account(srv, "eve", frontend())
        self.assertEqual(await srv.dispatch(w, "KEY LST"), ["OK 0"])
        # a resumed session (SES) is no sign-in with the password: it gets no key
        c = frontend()
        await say(srv, c, "USER eve")
        r = await say(srv, c, "AUT password %s" % PW)
        self.assertRegex(r, r"^OK [A-Za-z0-9_-]{22}$")
        sess = frontend()
        self.assertEqual(await say(srv, sess, "SES " + r.split()[1]), "OK eve")
        self.assertTrue((await say(srv, sess, "KEY NEW")).startswith("ERR STATE"))
        self.assertRegex(await say(srv, c, "KEY NEW"), KEY)
        # under sign-in none there is nothing to keep, and a bot gets none
        none = server("none")
        n = public()
        self.assertEqual(await say(none, n, "HELO fay"), "OK -")
        self.assertTrue((await say(none, n, "KEY NEW")).startswith("ERR NOAUTH"))
        self.assertTrue((await say(srv, public(), "KEY LST")).startswith("ERR NOHELO"))

    async def test_at_most_so_many(self):
        srv = server("password", "password")
        await account(srv, "gus")
        for _ in range(A.KEYS_MAX):
            c = public()
            await say(srv, c, "USER gus")
            await say(srv, c, "AUT password %s" % PW)
            await key_for(srv, c)
        c = public()
        await say(srv, c, "USER gus")
        await say(srv, c, "AUT password %s" % PW)
        self.assertTrue((await say(srv, c, "KEY NEW")).startswith("ERR LIMIT"))

    async def test_a_key_works_only_where_it_was_given(self):
        srv = server("password", "password")
        w = await account(srv, "hal", frontend("web"))
        key = await key_for(srv, w)
        self.assertTrue((await key_in(srv, "hal", key, public()))[0].startswith("ERR NOKEY"))
        self.assertTrue((await key_in(srv, "hal", key, frontend("play")))[0].startswith("ERR NOKEY"))
        self.assertRegex((await key_in(srv, "hal", key, frontend("web")))[0], r"^OK [A-Za-z0-9_-]{22}$")


class Refusal(unittest.IsolatedAsyncioTestCase):
    async def test_a_refused_key_falls_back_to_the_password(self):
        srv = server("password", "password")
        a = await account(srv, "ida")
        key = await key_for(srv, a)
        self.assertEqual(await say(srv, a, "OUT"), "OK 1")             # signing out forgets it
        self.assertEqual(srv.store.keys_of("ida"), [])
        r, c = await key_in(srv, "ida", key)
        self.assertEqual(r, "ERR NOKEY " + auth.NOKEY_TEXT)
        self.assertEqual(await say(srv, c, "AUT password %s" % PW), "ERR STATE nothing was asked")
        self.assertEqual(await say(srv, c, "USER ida"), "ASK password Password secret cred")
        self.assertEqual(await say(srv, c, "AUT password %s" % PW), "OK -")
        for bad in ("short", "x" * 22, key[:-1] + ("A" if key[-1] != "A" else "B")):
            r, _ = await key_in(srv, "ida", bad)
            self.assertTrue(r.startswith(("ERR NOKEY", "ERR LOCKED")), r)

    async def test_after_an_email_sign_up(self):
        """A pending account has no key to sign in with; the first sign-in, once the player chose a password
        of their own, may ask for one."""
        srv = server("password", "email")
        srv.settings["email.transport"] = "test"
        s, pw = await email_signup(srv, "jo", "jo@example.org")
        self.assertTrue((await say(srv, s, "AUT key " + "A" * 22)).startswith("ERR NOKEY"))
        self.assertEqual(await say(srv, s, "USER jo"), "ASK password Password secret cred")
        self.assertEqual(await first_sign_in(srv, s, pw), "OK -")
        self.assertRegex(await say(srv, s, "KEY NEW"), KEY)


class TimeSignedIn(unittest.IsolatedAsyncioTestCase):
    async def test_a_key_starts_the_time_signed_in_and_signing_out_ends_it(self):
        """A sign-in with a key starts the time signed in (TIM) and ends the sign-in limit's wait, exactly as
        the password does. OUT forgets the key and ends the time signed in."""
        srv = server("password", "password")
        a = await account(srv, "max")
        key = await key_for(srv, a)
        c = public()
        self.assertIsNone(c.signed_in)
        before = time.monotonic()
        r, c = await key_in(srv, "max", key, c)
        self.assertEqual(r, "OK -")
        self.assertIsNotNone(c.signed_in)
        self.assertGreaterEqual(c.signed_in, before)
        c.signed_in -= 100                                  # the sign-in was 100 seconds ago
        self.assertRegex((await srv.dispatch(c, "TIM"))[0], r"^TIM 10[01] ")
        self.assertEqual(await say(srv, c, "OUT"), "OK 1")
        self.assertIsNone(c.signed_in)
        self.assertGreaterEqual(c.signed_out, before)       # the sign-in limit counts from here again
        self.assertEqual(srv.store.keys_of("max"), [])
        self.assertTrue((await key_in(srv, "max", key))[0].startswith("ERR NOKEY"))


class Expiry(unittest.IsolatedAsyncioTestCase):
    async def test_unused_for_ninety_days(self):
        srv = server("password", "password")
        a = await account(srv, "kim")
        key = await key_for(srv, a)
        self.assertEqual(srv.accounts.key_idle(), 90 * 86400)
        start = time.time()
        with mock.patch("time.time", return_value=start + 89 * 86400):
            self.assertEqual((await key_in(srv, "kim", key))[0], "OK -")      # used: its 90 days start again
        with mock.patch("time.time", return_value=start + 178 * 86400):
            self.assertEqual((await key_in(srv, "kim", key))[0], "OK -")
        with mock.patch("time.time", return_value=start + 268 * 86400 + 60):
            self.assertTrue((await key_in(srv, "kim", key))[0].startswith("ERR NOKEY"))
        self.assertEqual(srv.store.keys_of("kim"), [])                          # forgotten on the way

    async def test_the_setting_and_the_sweep(self):
        srv = server("password", "password")
        srv.settings["keys.idle_secs"] = "3600"
        a = await account(srv, "lee")
        await key_for(srv, a)
        self.assertEqual(len(srv.store.keys_of("lee")), 1)
        srv.accounts.sweep(time.time() + 1800)
        self.assertEqual(len(srv.store.keys_of("lee")), 1)
        srv.accounts.trust_swept = 0
        srv.accounts.sweep(time.time() + 3700)
        self.assertEqual(srv.store.keys_of("lee"), [])


class Forgetting(unittest.IsolatedAsyncioTestCase):
    async def computers(self, srv, name, n):
        """n computers (public-port connections) signed in with the password, each with a key."""
        out = []
        for _ in range(n):
            c = public()
            await say(srv, c, "USER %s" % name)
            await say(srv, c, "AUT password %s" % PW)
            out.append((c, await key_for(srv, c)))
        return out

    async def test_one_all_and_this_one(self):
        srv = server("password", "password")
        await account(srv, "max")
        (a, ka), (b, kb), (c, kc) = await self.computers(srv, "max", 3)
        r, used_b = await key_in(srv, "max", kb)                    # someone signed in with b's key
        self.assertEqual(r, "OK -")
        nums = [int(l.split()[1]) for l in (await srv.dispatch(a, "KEY LST"))[:-1]]
        self.assertEqual(len(nums), 3)
        self.assertEqual(await say(srv, a, "KEY DEL %d" % nums[1]), "OK 1")    # forget b from a's profile
        self.assertEqual(await say(srv, used_b, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertEqual(await say(srv, b, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertTrue((await key_in(srv, "max", kb))[0].startswith("ERR NOKEY"))
        self.assertTrue((await say(srv, a, "KEY DEL %d" % nums[1])).startswith("ERR NOKEY"))
        self.assertEqual(await say(srv, a, "KEY DEL x"), "ERR SYNTAX KEY DEL, KEY DEL n or KEY DEL all")
        # KEY DEL alone: this computer's key, and the connection stays signed in (the setting turned off)
        self.assertEqual(await say(srv, c, "KEY DEL"), "OK 1")
        self.assertEqual(await say(srv, c, "LST"), "OK")
        self.assertTrue((await say(srv, c, "KEY DEL")).startswith("ERR NOKEY"))
        self.assertEqual(await srv.dispatch(a, "KEY LST"), [(await srv.dispatch(a, "KEY LST"))[0], "OK 1"])
        # all of them: the computers they were given to are signed out, this connection is not
        self.assertEqual(await say(srv, c, "KEY DEL all"), "OK 1")
        self.assertEqual(await say(srv, a, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        self.assertEqual(await srv.dispatch(c, "KEY LST"), ["OK 0"])
        self.assertTrue((await key_in(srv, "max", ka))[0].startswith("ERR NOKEY"))

    async def test_signing_out_and_out_all(self):
        srv = server("password", "password")
        await account(srv, "ned")
        (a, ka), (b, kb) = await self.computers(srv, "ned", 2)
        r, a2 = await key_in(srv, "ned", ka)
        self.assertEqual(await say(srv, a2, "OUT"), "OK 1")         # the key it signed in with is forgotten
        self.assertTrue((await key_in(srv, "ned", ka))[0].startswith("ERR NOKEY"))
        self.assertEqual(len(srv.store.keys_of("ned")), 1)
        self.assertEqual(await say(srv, b, "OUT all"), "OK 3")        # every key
        self.assertEqual(srv.store.keys_of("ned"), [])
        # a web session keeps its key across a core restart, and signing out still forgets it
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "p.db")
            one = server("password", "password", db)
            await account(one, "olu", frontend())
            c = frontend()
            await say(one, c, "USER olu")
            sid = (await say(one, c, "AUT password %s" % PW)).split()[1]
            await key_for(one, c)
            two = server("password", "password", db)
            two.load_state()
            again = frontend()
            self.assertEqual(await say(two, again, "SES " + sid), "OK olu")
            self.assertEqual((await two.dispatch(again, "KEY LST"))[0].split()[4], "here")
            self.assertEqual(await say(two, again, "OUT"), "OK 1")
            self.assertEqual(two.store.keys_of("olu"), [])
            for s in (one, two):
                s.store.db.close()

    async def test_a_password_change_forgets_them_all(self):
        srv = server("password", "password")
        a = await account(srv, "pat")
        (b, kb), (c, kc) = await self.computers(srv, "pat", 2)
        r, used = await key_in(srv, "pat", kb)
        self.assertEqual(await say(srv, c, "PWD"), "ASK current Current_password secret")
        await say(srv, c, "AUT current %s" % PW)
        await say(srv, c, "AUT password %s" % NEW_PW)
        self.assertEqual(await say(srv, c, "AUT confirm %s" % NEW_PW), "OK")
        self.assertEqual(srv.store.keys_of("pat"), [])
        self.assertEqual(await say(srv, used, "LST"), "ERR SIGNEDOUT your session has ended; sign in again")
        for k in (kb, kc):
            self.assertTrue((await key_in(srv, "pat", k))[0].startswith("ERR NOKEY"))
        self.assertRegex(await say(srv, c, "KEY NEW"), KEY)          # the new password was just typed here

    async def test_a_recovery_and_the_operator_reset_forget_them_all(self):
        srv = server("password", "email")
        srv.settings["email.transport"] = "test"
        s, pw = await email_signup(srv, "quin", "quin@example.org")
        self.assertEqual(await first_sign_in(srv, s, pw), "OK -")
        k1 = await key_for(srv, s)
        c = public()
        await say(srv, c, "USER quin")
        await say(srv, c, "AUT password reset")                      # a forgotten password, by mail
        from test_auth import mail_password
        self.assertEqual(await first_sign_in(srv, c, mail_password(srv), "Another-pass9"), "OK -")
        self.assertTrue((await key_in(srv, "quin", k1))[0].startswith("ERR NOKEY"))
        k2 = await key_for(srv, c)
        ok, _, _ = await srv.accounts.reset("quin")
        self.assertTrue(ok)
        self.assertEqual(srv.store.keys_of("quin"), [])
        self.assertTrue((await key_in(srv, "quin", k2))[0].startswith("ERR NOKEY"))

    async def test_deleting_the_account_deletes_its_keys(self):
        srv = server("password", "password")
        await key_for(srv, await account(srv, "rae"))
        self.assertTrue(srv.accounts.delete("rae"))
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM signin_keys").fetchone()[0], 0)


class Lockout(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_keys_count_like_wrong_passwords(self):
        srv = server("password", "password")
        a = await account(srv, "sam")
        key = await key_for(srv, a)
        stranger = Session(None, source="198.51.100.7")
        self.assertEqual((await key_in(srv, "sam", "A" * 22, stranger))[0], "ERR NOKEY " + auth.NOKEY_TEXT)
        self.assertEqual((await key_in(srv, "sam", "B" * 22, stranger))[0], "ERR NOKEY " + auth.NOKEY_TEXT)
        r = (await key_in(srv, "sam", "C" * 22, stranger))[0]
        self.assertEqual(r, "ERR NOKEY wait=60 %s in 60 seconds" % auth.NOKEY_TEXT)       # the third locks
        p = srv.players["sam"]
        self.assertEqual(p.fails, 3)
        self.assertEqual(len(srv.accounts.misses["198.51.100.7"]), 3)       # the address's budget counts them
        self.assertEqual(len(srv.accounts.global_misses), 3)                 # and the server's
        # while locked: a wrong key is refused with the wait and not counted again, the password unchecked
        r = (await key_in(srv, "sam", "D" * 22, stranger))[0]
        self.assertTrue(r.startswith("ERR NOKEY wait="), r)
        self.assertEqual(p.fails, 3)
        c = public()
        await say(srv, c, "USER sam")
        self.assertTrue((await say(srv, c, "AUT password %s" % PW)).startswith("ERR LOCKED"))
        # the right key still signs in, as a temporary password would, and clears the count
        self.assertEqual((await key_in(srv, "sam", key))[0], "OK -")
        self.assertEqual((p.fails, p.locked_until), (0, 0.0))

    async def test_wrong_passwords_and_wrong_keys_share_the_count(self):
        srv = server("password", "password")
        await account(srv, "tia")
        c = public()
        await say(srv, c, "USER tia")
        self.assertEqual(await say(srv, c, "AUT password nope"), "ERR WRONG Wrong password; try again")
        self.assertEqual((await key_in(srv, "tia", "A" * 22))[0], "ERR NOKEY " + auth.NOKEY_TEXT)
        await say(srv, c, "USER tia")
        self.assertEqual(await say(srv, c, "AUT password nope"), "ERR LOCKED wait=60 Wrong password; try again "
                                                                   "in 60 seconds")

    async def test_a_spent_address_budget_holds_wrong_keys(self):
        srv = server("password", "password")
        await account(srv, "uma")
        src = "203.0.113.9"
        srv.accounts.misses[src] = [time.time()] * auth.SOURCE_MISSES[0]
        r = (await key_in(srv, "uma", "A" * 22, Session(None, source=src)))[0]
        self.assertTrue(r.startswith("ERR NOKEY wait="), r)
        self.assertEqual(srv.players["uma"].fails, 0)


class ConstantTime(unittest.TestCase):
    def test_by_inspection(self):
        """The key is compared by its SHA-256 with hmac.compare_digest, against every key of the player and
        with no early return, and the channel the same way; nothing compares a key or its hash with ==."""
        src = inspect.getsource(A.Accounts.key_check)
        self.assertIn("hmac.compare_digest(kh.encode", src)
        self.assertIn("hmac.compare_digest(kfe.encode", src)
        loop = src[src.index("for n, kh"):src.index("if found:")]
        self.assertNotIn("return", loop)
        self.assertNotIn("break", loop)
        self.assertNotRegex(src, r"(kh|h|value)\s*==")
        self.assertIn("sid_hash(", src)


class NeverLogged(unittest.IsolatedAsyncioTestCase):
    async def test_the_core_logs_no_key(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            srv = server("password", "password")
            a = await account(srv, "vic")
            key = await key_for(srv, a)
            await key_in(srv, "vic", key)
            await key_in(srv, "vic", key[::-1])
            await srv.dispatch(a, "KEY NEW " + key)          # a key typed where a label goes is refused here
            await srv.dispatch(a, "KEY DEL " + key)
            await srv.dispatch(a, "OUT")
        text = out.getvalue() + err.getvalue()
        self.assertNotIn(key, text)
        self.assertNotIn(key[::-1], text)


class Migration(unittest.TestCase):
    def test_a_database_from_before_gains_the_table_and_the_column(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "parlord.db")
            st = Store(db)
            st.db.execute("DROP TABLE signin_keys")
            st.db.close()
            con = sqlite3.connect(db)                       # sessions as they were before: no devkey column
            con.executescript("ALTER TABLE sessions RENAME TO s2;"
                              "CREATE TABLE sessions (hash TEXT PRIMARY KEY, player TEXT NOT NULL, frontend TEXT NOT"
                              " NULL, method TEXT NOT NULL, created REAL NOT NULL, used REAL NOT NULL, fp TEXT NOT NULL"
                              " DEFAULT ''); DROP TABLE s2;"
                              "INSERT INTO players (key, name, cred, created, last_seen, temp) VALUES"
                              " ('wes', 'wes', '', 1, 1, '');"
                              "INSERT INTO sessions VALUES ('h', 'wes', 'web', 'password+password', 1, 1, 'f');")
            con.commit()
            con.close()
            st = Store(db)
            self.assertFalse(getattr(st, "set_aside", None))                 # migrated, not set aside
            self.assertEqual(st.keys_of("wes"), [])
            self.assertEqual(st.add_key("wes", "x" * 64, "web", "Mac", 5.0), 1)
            self.assertEqual(st.load_sessions(), [("h", "wes", "web", "password+password", 1.0, 1.0, "f", 0)])
            self.assertEqual([r[0] for r in st.load_players()], ["wes"])
            st.db.close()


# ---- the web and Play, as processes --------------------------------------------------------------------------

import procs  # noqa: E402
from test_adapters import Stack  # noqa: E402
from test_play import PlayStack  # noqa: E402


def stop_and_read(case):
    """Stop the stack's processes and return everything they printed."""
    procs.stop_all(case.procs)
    return "\n".join(case.output)


def post_raw(port, path, form, ua=None):
    """A POST, and every Set-Cookie of its answer: (status, Location, [Set-Cookie values])."""
    import http.client
    from urllib.parse import urlencode
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": "parlornet.org:6500", "Content-Type": "application/x-www-form-urlencoded"}
    if ua:
        headers["User-Agent"] = ua
    c.request("POST", path, body=urlencode(form), headers=headers)
    r = c.getresponse()
    r.read()
    c.close()
    return r.status, r.getheader("Location"), [v for k, v in r.getheaders() if k.lower() == "set-cookie"]


def key_rows(body):
    """The profile's Stay signed in rows: [(this browser, the forget link)]."""
    out = []
    for row in body.split("<tr")[1:]:
        row = row.split("</tr>")[0]
        m = re.search(r'href="(/forget\.htm\?[^"]+)"', row)
        if m:
            out.append(("(this browser)" in row, m.group(1).replace("&amp;", "&")))
    return out


class Web(Stack):
    ua = "Mozilla/3.0 (Macintosh; I; PPC)"

    def test_stay_signed_in_on_the_web(self):
        b = self.browser()
        b.ua = self.ua
        b.login("wen", "Wen-pass-1")                        # the account
        b.get(b.link(r"/out\.htm\?(?!.*all=1)"))
        st, _, _, body = b.get("/login.htm")
        self.assertIn('<input type="checkbox" name="stay" value="1"> Stay signed in', body)
        self.assertIn('name="stay"', self.browser().get("/welcome.htm")[3])        # the welcome page's form too
        # signing in with the box checked: the key in a long-lived cookie of its own
        st, loc, cookies = post_raw(self.web_port, "/login.htm", {"n": "wen", "k": "Wen-pass-1", "stay": "1"},
                                    self.ua)
        self.assertEqual(st, 302)
        key_cookie = [v for v in cookies if v.startswith("pnkey=")][0]
        self.assertRegex(key_cookie, r"^pnkey=wen\.[A-Za-z0-9_-]{22}; Path=/; Max-Age=34560000; HttpOnly; "
                                     r"SameSite=Lax$")       # no Secure: this page came by http
        value = key_cookie.split(";")[0].split("=", 1)[1]
        key = value.split(".")[1]
        self.assertNotIn(key, loc)
        b.jar = {"pnkey": value}                            # a browser that kept only the key cookie
        # a visit with no session signs in with the key, and asks nothing
        st, loc, _, _ = b.get("/lobby.htm")
        self.assertEqual(st, 302)
        self.assertIn("pn", b.jar)
        st, _, _, body = b.go(loc)
        self.assertIn("You are <b>wen</b>", body)
        self.assertEqual(b.get("/login.htm")[0], 302)              # nothing to sign in to: the lobby
        # the profile lists the computer, marked as this browser, with a link to forget it
        st, _, _, body = b.get("/player.htm")
        self.assertIn("Stay signed in", body)
        self.assertIn("Netscape Navigator 3.0 <b>(this browser)</b>", body)
        self.assertIn("Forget them all", body)
        # a second browser stays signed in too; forgetting it from here signs it out there
        b2 = self.browser()
        b2.post("/login.htm", {"n": "wen", "k": "Wen-pass-1", "stay": "1"})
        self.assertIn("pnkey", b2.jar)
        rows = key_rows(b.get("/player.htm")[3])
        self.assertEqual(sorted(here for here, _ in rows), [False, True])
        theirs = [link for here, link in rows if not here][0]
        st, loc, _, _ = b.get(theirs)
        self.assertIn("note=forgot", loc)
        self.assertIn("pnkey", b.jar)                                   # not this browser's
        self.assertIn("That computer will sign in with the password next time.", b.go(loc)[3])
        self.assertEqual(len(key_rows(b.body)), 1)
        st, _, _, body = b2.get("/lobby.htm")
        self.assertIn("This computer was signed out. Sign in again", body)
        self.assertNotIn("pnkey", b2.jar)                                # a refused key's cookie is cleared
        # forgetting this browser's own key clears its cookie
        b4 = self.browser()
        b4.post("/login.htm", {"n": "wen", "k": "Wen-pass-1", "stay": "1"})
        b4.get("/lobby.htm")
        mine = [link for here, link in key_rows(b4.get("/player.htm")[3]) if here][0]
        b4.get(mine)
        self.assertNotIn("pnkey", b4.jar)
        self.assertIn("You are <b>wen</b>", b4.get("/lobby.htm")[3])     # and leaves it signed in
        # signing out forgets the key here and on the server
        b.get("/lobby.htm")
        st, _, _, body = b.get(b.link(r"/out\.htm\?(?!.*all=1)"))
        self.assertIn("Signed out.", body)
        self.assertNotIn("pnkey", b.jar)
        b.jar["pnkey"] = value
        st, _, _, body = b.get("/lobby.htm")
        self.assertIn("This computer was signed out", body)
        self.assertNotIn("pnkey", b.jar)
        # without the box, no key
        b3 = self.browser()
        b3.login("wen", "Wen-pass-1")
        self.assertNotIn("pnkey", b3.jar)
        # over https (a trusted proxy would say so) the cookie is Secure
        from adapters.web import set_key_cookie
        self.assertIn("; Secure", set_key_cookie({"proto": "https"}, "wen", key))
        self.assertNotIn("Secure", set_key_cookie({"proto": "http"}, "wen", key))
        for x in (b, b2, b3, b4):
            for u in x.urls:
                self.assertNotIn(key, u)
            for page_ in x.pages:
                self.assertNotIn(key, page_)
        logs = stop_and_read(self)
        self.assertNotIn(key, logs)


class PlayKeys(PlayStack):
    def test_stay_signed_in_on_play(self):
        w, h = self.signed_in("plo", "Plo-pass-word", first=("CLI PROG Firefox 132", "CLI MACH Linux"))
        r = w.req("KEY NEW")
        self.assertEqual(r[-1], "OK -")                                  # the key never reaches the page
        hnd = [l.split()[1] for l in r if l.startswith("HND ")]
        self.assertEqual(len(hnd), 1)
        resp, _ = self.http("POST", "/play/session", "h=" + hnd[0], {"Origin": self.origin})
        cookie = resp.getheader("Set-Cookie")
        self.assertRegex(cookie, r"^pnplaykey=plo\.[A-Za-z0-9_-]{22}; Max-Age=34560000; Path=/play/; HttpOnly; "
                                 r"SameSite=Strict\Z")
        value = re.match(r"pnplaykey=([^;]+);", cookie).group(1)
        key = value.split(".")[1]
        self.assertTrue(w.req("KEY LST")[0].endswith(" here Linux"))
        # a new WebSocket with only the key cookie: SES alone signs in with it, and gives a session handle
        w2 = self.ws(cookie="pnplaykey=" + value)
        w2.recv()
        r = w2.req("SES")
        self.assertEqual(r[-1], "OK plo")
        self.assertEqual(len([l for l in r if l.startswith("HND ")]), 1)
        self.assertTrue(w2.req("LST")[-1].startswith("OK"))
        self.assertFalse(re.search(r"\b%s\b" % re.escape(key), " ".join(r)))
        # signing out forgets it on the server; the page clears both cookies (out=1)
        self.assertTrue(w2.req("OUT")[-1].startswith("OK"))
        w3 = self.ws(cookie="pnplaykey=" + value)
        w3.recv()
        self.assertTrue(w3.req("SES")[-1].startswith("ERR NOKEY"))
        resp, _ = self.http("POST", "/play/session", "out=1")
        cleared = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
        self.assertTrue(any(v.startswith("pnplaykey=;") and "Max-Age=0" in v for v in cleared), cleared)
        self.assertTrue(any(v.startswith("pnplay=;") for v in cleared), cleared)
        logs = stop_and_read(self)
        self.assertNotIn(key, logs)


if __name__ == "__main__":
    unittest.main()
