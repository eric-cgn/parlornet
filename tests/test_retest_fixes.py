"""The fixes for the review's re-test (docs/security-design.md, "Review findings
and fixes", round 2): N1 to N9 and F14. The re-test's own scripts are in
review/ (r14_accounts_retest.py, r1b_gate_retest.py, r16_web_and_cpu_retest.py,
split_probe.py)."""
import asyncio
import os
import re
import sys
import time
import unittest
from unittest import mock

from test_core import ROOT, make_keys
from test_review_fixes import SECRET, load_start, password_player, public, say, server

sys.path.insert(0, ROOT)
from adapters.common import CoreClient, CoreError  # noqa: E402
from parlord import auth, frontends  # noqa: E402
from parlord.core import Server  # noqa: E402


WRONG = "Wrong guess 1"      # never SECRET
RESET_ASKED = "ASK password Password secret"     # after a reset: the temporary password went by mail


async def guess(srv, source, name, pw):
    """A sign-in from a client address: the name, then pw at the password question; the last line."""
    s = public(source)
    await say(srv, s, "HELO %s" % name)
    return await say(srv, s, "AUT password %s" % pw)


class N1WrongAnswerBudgets(unittest.IsolatedAsyncioTestCase):
    async def test_per_address_budget_across_names(self):
        srv = server("password", "email")
        n, _ = auth.SOURCE_MISSES
        for i in range(n + 2):
            await password_player(srv, "p%02d" % i, "p%02d@example.org" % i)
        for i in range(n):
            r = await guess(srv, "198.51.100.7", "p%02d" % i, WRONG)
            self.assertTrue(r.startswith("ERR LOCKED wait=60 Wrong password"), r)
        r = await guess(srv, "198.51.100.7", "p%02d" % n, WRONG)
        self.assertTrue(r.startswith("ERR LOCKED wait=") and "from your address" in r, r)
        self.assertEqual(srv.players["p%02d" % n].fails, 0)               # not even checked
        r = await guess(srv, "198.51.100.7", "p%02d" % (n + 1), SECRET)
        self.assertIn("from your address", r)                             # the right one is not checked either
        r = await guess(srv, "203.0.113.1", "p%02d" % n, WRONG)
        self.assertTrue(r.startswith("ERR LOCKED wait=60 Wrong password"), r)  # another address is not held up

    async def test_server_budget_and_trusted_addresses(self):
        srv = server("password", "email")
        g, _ = auth.GLOBAL_MISSES
        per = auth.SOURCE_MISSES[0]
        names = ["q%02d" % i for i in range(g + 2)]
        for nm in names:
            await password_player(srv, nm, nm + "@example.org")
        # the owner of q00 signs in from 192.0.2.50: that address is trusted for q00
        self.assertEqual(await guess(srv, "192.0.2.50", "q00", SECRET), "OK -")
        k = 0
        for src in range(g // per + 1):
            for _ in range(per):
                if k >= g:
                    break
                await guess(srv, "203.0.113.%d" % src, names[1 + k], WRONG)
                k += 1
        r = await guess(srv, "198.51.100.99", names[-1], WRONG)
        self.assertTrue(r.startswith("ERR BUSY wait=") and "on this server" in r, r)
        # the trusted owner still gets checked (and in)
        self.assertEqual(await guess(srv, "192.0.2.50", "q00", SECRET), "OK -")
        # and recovery by mail still goes ahead for anyone, and its temporary password signs in
        s = public("198.51.100.99")
        await say(srv, s, "HELO %s" % names[-1])
        self.assertEqual(await say(srv, s, "AUT password reset"), RESET_ASKED)
        pw = re.search(r"Password:\s+(\S+)", srv.auth.mailer.last["body"]).group(1)
        self.assertEqual(await say(srv, s, "AUT password " + pw), "ASK password New_password secret new")

    def test_the_arithmetic(self):
        """docs/security-design.md: the worst case across many names is the server budget, 48 untrusted
        guesses a day whatever the number of names or addresses, under one guess per name per day once
        there are more players than the budget; one address alone gets 12."""
        g, gsecs = auth.GLOBAL_MISSES
        self.assertEqual(gsecs, 86400)
        self.assertLessEqual(g * 86400 / gsecs, 48)
        n, secs = auth.SOURCE_MISSES
        self.assertLessEqual(n * 86400 / secs, 12)


class N2RecoveryPerRequester(unittest.IsolatedAsyncioTestCase):
    async def reset(self, srv, source, name="alice"):
        s = public(source)
        await say(srv, s, "HELO %s" % name)
        r = await say(srv, s, "AUT password reset")
        srv.store.db.execute("UPDATE email_log SET at = at - 61")        # the 60 s spacing
        return r

    async def test_strangers_cannot_use_up_the_owners_recovery(self):
        srv = server("password", "email")
        await password_player(srv, "alice", "alice@example.org")
        srv.accounts.trust("alice", "203.0.113.50")                      # alice signed in from there
        ask = RESET_ASKED
        self.assertEqual(await self.reset(srv, "198.51.100.7"), ask)
        self.assertEqual(await self.reset(srv, "198.51.100.7"), ask)
        r = await self.reset(srv, "198.51.100.7")
        self.assertTrue(r.startswith("ERR BUSY wait=86400 You asked for this name's recovery twice today"), r)
        self.assertEqual(await self.reset(srv, "192.0.2.77"), ask)     # one stranger cannot keep out a newcomer
        srv.store.db.execute("UPDATE email_log SET at = at - 3600")    # an hour later (3 an hour to one address)
        self.assertEqual(await self.reset(srv, "198.51.100.8"), ask)    # four for the name: its day
        r = await self.reset(srv, "198.51.100.9")
        self.assertTrue(r.startswith("ERR BUSY wait=3600 Too many emails for this name"), r)
        # alice, from an address she has signed in from, has her own allowance
        self.assertEqual(await self.reset(srv, "203.0.113.50"), ask)
        rows = srv.store.db.execute("SELECT kind, source FROM email_log WHERE name='alice'").fetchall()
        self.assertIn(("recovery-own", "203.0.113.50"), rows)
        # from yet another new address she shares the name's day with everyone (the trade-off)
        r = await self.reset(srv, "192.0.2.78")
        self.assertTrue(r.startswith("ERR BUSY"), r)

    async def test_operator_reset_is_not_a_requester(self):
        """The operator's reset gives a temporary password to the operator, not by mail: it is never held
        up by a requester's caps, and uses none."""
        srv = server("password", "email")
        await password_player(srv, "bea", "bea@example.org")
        for _ in range(3):
            good, text, code = await srv.accounts.reset("bea")
            self.assertTrue(good and code, "reset")
        self.assertEqual(srv.store.email_count(0, name="bea")[0], 0)

    async def test_a_sign_in_trusts_the_address(self):
        srv = server("none")
        await say(srv, public("192.0.2.5"), "HELO cal")
        self.assertTrue(srv.accounts.is_trusted("cal", "192.0.2.5"))
        self.assertFalse(srv.accounts.is_trusted("cal", "192.0.2.6"))
        self.assertFalse(srv.accounts.is_trusted("cal", None))
        srv.accounts.delete("cal")
        self.assertFalse(srv.accounts.is_trusted("cal", "192.0.2.5"))


class R21OwnRecoveryBudget(unittest.IsolatedAsyncioTestCase):
    """Round 2, R2-1, from r17b_recovery_budget.py: strangers' resets for other
    players use up the strangers' recovery budget, never the owners'."""

    async def reset(self, srv, source, name, field="password"):
        s = public(source)
        await say(srv, s, "HELO %s" % name)
        r = await say(srv, s, "AUT %s reset" % field)
        srv.store.db.execute("UPDATE email_log SET at = at - 61")
        return s, r

    async def test_strangers_cannot_use_up_owners_recovery(self):
        srv = server("password", "email")
        srv.settings.update({"email.limit.recovery_per_hour": "8", "email.limit.recovery_per_minute": "10"})
        for i in range(10):
            await password_player(srv, "v%02d" % i, "v%02d@example.org" % i)
        await password_player(srv, "alice", "alice@example.org")
        self.assertEqual(await guess(srv, "192.0.2.10", "alice", SECRET), "OK -")
        sent = 0
        for k in range(8):
            _, r = await self.reset(srv, "198.18.0.%d" % (k // 4 + 1), "v%02d" % k)
            sent += r.startswith("ASK")
        self.assertEqual(sent, 8)
        _, r = await self.reset(srv, "198.18.0.9", "v09")
        self.assertTrue(r.startswith("ERR BUSY wait=300 Email is busy"), r)       # strangers' budget spent
        _, r = await self.reset(srv, "192.0.2.10", "alice")
        self.assertEqual(r, RESET_ASKED)                                        # alice at home: her own budget

    async def test_a_reset_used_by_signing_in_with_it_is_the_owners(self):
        srv = server("password", "email")
        await password_player(srv, "dave", "dave@example.org")
        s, r = await self.reset(srv, "203.0.113.77", "dave")
        pw = re.search(r"Password:\s+(\S+)", srv.auth.mailer.last["body"]).group(1)
        kinds = lambda: [k for (k,) in srv.store.db.execute("SELECT kind FROM email_log")]  # noqa: E731
        self.assertEqual(kinds(), ["recovery"])
        self.assertEqual(await say(srv, s, "AUT password " + pw), "ASK password New_password secret new")
        self.assertEqual(kinds(), ["recovery-own"])

    async def test_the_operator_sees_and_clears_the_budgets(self):
        from parlord import admin as PA
        srv = server("password", "email")
        adm = PA.Admin(srv)
        conn = PA.AdminConn()
        conn.state = "admin"
        b = (await adm.dispatch(conn, {"op": "budgets"}))["budgets"]
        self.assertEqual((b["wrong_server"], b["wrong_server_limit"], b["alarm"]), (0, 48, False))
        names = ["w%02d" % i for i in range(50)]
        for nm in names:
            await password_player(srv, nm, nm + "@example.org")
        for i in range(48):
            await guess(srv, "203.0.113.%d" % (i // 12), names[i], WRONG)
        r = await guess(srv, "198.51.100.1", names[49], SECRET)
        self.assertTrue(r.startswith("ERR BUSY"), r)
        b = (await adm.dispatch(conn, {"op": "status"}))["budgets"]
        self.assertEqual((b["wrong_server"], b["addresses_spent"], b["alarm"]), (48, 4, True))
        b = (await adm.dispatch(conn, {"op": "clear_budgets"}))["budgets"]
        self.assertEqual((b["wrong_server"], b["alarm"]), (0, False))
        self.assertEqual(await guess(srv, "198.51.100.1", names[49], SECRET), "OK -")
        self.assertEqual(srv.players["w00"].fails, 1)                          # each name's lockout stays

    async def test_clearing_frees_the_strangers_recovery_budget(self):
        from parlord import admin as PA
        srv = server("password", "email")
        srv.settings.update({"email.limit.recovery_per_hour": "2"})
        for i in range(3):
            await password_player(srv, "u%d" % i, "u%d@example.org" % i)
        await self.reset(srv, "198.18.1.1", "u0")
        await self.reset(srv, "198.18.1.2", "u1")
        _, r = await self.reset(srv, "198.18.1.3", "u2")
        self.assertTrue(r.startswith("ERR BUSY"), r)
        adm = PA.Admin(srv)
        conn = PA.AdminConn()
        conn.state = "admin"
        await adm.dispatch(conn, {"op": "clear_budgets"})
        _, r = await self.reset(srv, "198.18.1.3", "u2")
        self.assertEqual(r, RESET_ASKED)


class N3HandshakeSlots(unittest.IsolatedAsyncioTestCase):
    async def test_idle_sockets_cannot_hold_the_handshake_slots(self):
        keys = make_keys("web")
        srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        srv.keyring = frontends.Keyring(keys)
        listener = await asyncio.start_server(srv.handle_frontend, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        idle = []
        try:
            for _ in range(40):         # more than both the per-address and the overall slots
                idle.append(await asyncio.open_connection("127.0.0.1", port))
            await asyncio.sleep(0.2)
            self.assertLessEqual(len(srv.handshakes), srv.handshakes.per_source)
            t = time.time()
            web = await CoreClient.connect("127.0.0.1", port, os.path.join(keys, "web.key"))
            self.assertLess(time.time() - t, 2)
            self.assertEqual((await web.req("HELO wes"))[1][:3], "OK ")
            web.close()
        finally:
            for _, w in idle:
                w.close()
            listener.close()

    def test_eviction_order(self):
        class W:
            def __init__(self, ip):
                self.ip, self.aborted = ip, False
                me = self

                class T:
                    def abort(self):
                        me.aborted = True
                self.transport = T()

            def get_extra_info(self, k):
                return (self.ip, 1)
        h = frontends.Handshakes(per_source=2, total=3)
        a = [W("192.0.2.1") for _ in range(2)]
        for w in a:
            h.enter(w)
        b = W("192.0.2.2")
        h.enter(b)
        c = W("192.0.2.3")
        h.enter(c)                      # over the total: the busiest address's oldest goes
        self.assertTrue(a[0].aborted)
        self.assertFalse(b.aborted or c.aborted)
        self.assertEqual(frontends.HANDSHAKE_SECS, 3)

    async def test_admin_socket_handshakes_are_capped(self):
        from parlord import admin as PA
        srv = server("none")
        srv.keyring = frontends.Keyring(make_keys("admin", role="admin"))
        adm = PA.Admin(srv)
        self.assertEqual((adm.handshakes.per_source, adm.handshakes.total), (2, 8))


class N4N6WebLimits(unittest.TestCase):
    def web(self):
        from adapters import web
        return web, web.Web("127.0.0.1", 1, os.path.join(make_keys("web"), "web.key"))

    def test_n6_a_full_house_ends_the_busiest_addresss_dialogue(self):
        web, w = self.web()

        class Core:
            closed = False

            def close(self):
                self.closed = True
        victim = Core()
        w.signins["victim"] = web.Dialogue(victim, "amy", time.time() + 10, None, "192.0.2.200")  # the oldest
        i = 0
        while len(w.signins) < web.MAX_SIGNINS:
            addr = "198.51.100.%d" % (i // 3)
            w.signins["d%d" % i] = web.Dialogue(Core(), "x", time.time() + 100 + i, None, addr)
            i += 1
        w.signins["d0"].addr = w.signins["d1"].addr = w.signins["d2"].addr = w.signins["d3"].addr = "198.51.100.0"
        w.signins["d3"].born += 1                                       # the newest of them
        self.assertTrue(w.make_room("203.0.113.9"))
        self.assertIn("victim", w.signins)
        self.assertIn("d0", w.signins)                                  # not the oldest: the newest arrival
        self.assertNotIn("d3", w.signins)

    def test_r2_2_many_addresses_with_one_each_do_not_push_out_a_player(self):
        web, w = self.web()

        class Core:
            closed = False

            def close(self):
                self.closed = True
        player = Core()
        w.signins["player"] = web.Dialogue(player, "amy", time.time() + 300, None, "192.0.2.200")
        for rnd in range(3):
            for i in range(web.MAX_SIGNINS + 20):       # attackers, one dialog each, again and again
                addr = "2001:db8:%x::1" % (i + 1)        # every /48 a different address
                if w.make_room(addr):
                    w.signins["a%d-%d" % (rnd, i)] = web.Dialogue(Core(), "x", time.time() + 300, None, addr)
        self.assertIn("player", w.signins)
        self.assertFalse(player.closed)

    def test_n5_a_new_sign_in_is_known_good(self):
        web, w = self.web()
        sid = "N" * 22
        w.signed_in({"headers": {"host": "127.0.0.1:6500"}}, sid)
        w.ses_fails["203.0.113.1"] = [time.time()] * 10
        self.assertTrue(w.resume_allowed("203.0.113.1", sid))

    def test_n4_ipv6_by_its_48(self):
        from adapters import common
        self.assertEqual(common.addr_key("2001:db8:1:2::5"), common.addr_key("2001:db8:1:2:ffff::9"))
        self.assertEqual(common.addr_key("::ffff:192.0.2.1"), "192.0.2.1")


class R3FutureClearTimes(unittest.IsolatedAsyncioTestCase):
    """Final review (informational): a budget-clear time in the future, written
    into the settings file by hand, does not switch off the server-wide mail
    limits; the same holds for the time the operator last resumed sign-up mail."""

    def test_past_time(self):
        now = time.time()
        for value, want in (("", 0.0), (None, 0.0), ("x", 0.0), ("-5", 0.0), ("nan", 0.0), ("inf", 0.0),
                            (repr(now + 3600), 0.0), (repr(now - 60), now - 60), (repr(now), now)):
            self.assertEqual(auth.past_time(value, now), want, value)

    async def test_a_future_clear_time_keeps_the_recovery_budget(self):
        srv = server("password", "email")
        srv.settings.update({"email.limit.recovery_per_hour": "2",
                             "budgets.cleared": repr(time.time() + 10 * 365 * 86400)})
        for i in range(3):
            await password_player(srv, "f%d" % i, "f%d@example.org" % i)
        await R21OwnRecoveryBudget.reset(None, srv, "198.18.2.1", "f0")
        await R21OwnRecoveryBudget.reset(None, srv, "198.18.2.2", "f1")
        _, r = await R21OwnRecoveryBudget.reset(None, srv, "198.18.2.3", "f2")
        self.assertTrue(r.startswith("ERR BUSY wait=300 Email is busy"), r)    # the hour's 2 are still counted
        srv.settings["budgets.cleared"] = repr(time.time())                       # a real clear frees it
        _, r = await R21OwnRecoveryBudget.reset(None, srv, "198.18.2.3", "f2")
        self.assertEqual(r, RESET_ASKED)

    def test_a_future_resume_time_keeps_the_sign_up_hold(self):
        srv = server("password", "email")
        for i in range(4):
            srv.store.email_sent("gone%d@example.org" % i, "g%d" % i, True, "signup", "198.51.100.%d" % i)
            srv.store.block_email("gone%d@example.org" % i, "refused by the mail server")
        self.assertTrue(srv.new_signups_held())
        srv.settings["email.new_ok_since"] = repr(time.time() + 86400 * 365)
        self.assertTrue(srv.new_signups_held())                     # ignored: the hold stays
        srv.settings["email.new_ok_since"] = repr(time.time())
        self.assertFalse(srv.new_signups_held())                    # the operator's resume


class N8BounceOrigins(unittest.TestCase):
    def test_only_addresses_parlord_mailed_count(self):
        srv = server("password", "email")
        for i in range(5):
            srv.store.block_email("other%d@example.org" % i, "listed in the blocked file")
        self.assertFalse(srv.new_signups_held())                    # another sender's bounces, a first import
        for i in range(4):
            srv.store.email_sent("mine%d@example.org" % i, "m%d" % i, True, "signup", "")
            srv.store.block_email("mine%d@example.org" % i, "refused by the mail server")
        self.assertTrue(srv.new_signups_held())


class N9PasswordChecksPerAddress(unittest.IsolatedAsyncioTestCase):
    async def test_checks_are_limited_per_address(self):
        srv = server("password")
        p = srv.accounts.player("dot", create=True)
        p.cred = auth.hash_password("dot secret 1")
        with mock.patch.object(auth, "CHECKS_PER_SOURCE", (3, 60)):
            for _ in range(3):
                s = public("192.0.2.8")
                await say(srv, s, "HELO dot")
                self.assertEqual(await say(srv, s, "AUT password dot secret 1"), "OK -")
            s = public("192.0.2.8")
            await say(srv, s, "HELO dot")
            r = await say(srv, s, "AUT password dot secret 1")
            self.assertTrue(r.startswith("ERR BUSY wait=") and "from your address" in r, r)
            s = public("192.0.2.9")
            await say(srv, s, "HELO dot")
            self.assertEqual(await say(srv, s, "AUT password dot secret 1"), "OK -")


class F14WebHosts(unittest.TestCase):
    def test_listed_names_only(self):
        from adapters import web
        old = list(web.HOSTS)
        try:
            web.HOSTS[:] = ["parlornet.org:6500", "www.parlornet.org"]
            req = lambda h: {"headers": {"host": h}}  # noqa: E731
            self.assertEqual(web.location_host(req("evil.example")), "parlornet.org:6500")
            self.assertEqual(web.location_host(req("parlornet.org:6500")), "parlornet.org:6500")
            self.assertEqual(web.location_host(req("www.parlornet.org:6500")), "www.parlornet.org:6500")
            self.assertEqual(web.location_host(req("")), "parlornet.org:6500")
            self.assertEqual(web.host_of(req("evil.example")), "parlornet.org")
            web.HOSTS[:] = []
            self.assertEqual(web.location_host(req("evil.example")), "evil.example")    # the default: unset
            a = web.parse_args(["--hosts", "a.example:6500, b.example"])
            self.assertEqual(web.HOSTS, ["a.example:6500", "b.example"])
            self.assertTrue(a)
        finally:
            web.HOSTS[:] = old

    def test_setting_and_start(self):
        from parlord import admin as PA
        self.assertEqual(PA.SETTINGS["web.hosts"]("parlornet.org:6500,www.parlornet.org"),
                         "parlornet.org:6500,www.parlornet.org")
        import tempfile
        import shutil
        d = tempfile.mkdtemp(prefix="pndata")
        try:
            env = {"PARLOR_DB": os.path.join(d, "parlord.db"), "PARLOR_ADAPTERS": "web",
                   "PARLOR_SETTINGS": os.path.join(d, "none.conf"), "PARLOR_WEB_HOSTS": "play.example:6500"}
            st = load_start(env)
            started = []
            with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                    mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                    mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                    mock.patch("builtins.print"), mock.patch.object(st.signal, "signal"), \
                    mock.patch.object(st.sys, "argv", ["start.py"]):
                with self.assertRaises(KeyboardInterrupt):
                    st.main()
            web_args = dict(started)["web"]
            self.assertEqual(web_args[web_args.index("--hosts") + 1], "play.example:6500")
        finally:
            shutil.rmtree(d, True)


class N7SplitNetworks(unittest.TestCase):
    def test_internal_channel_and_no_way_out(self):
        with open(os.path.join(ROOT, "deploy", "install-split.sh")) as f:
            text = f.read()
        self.assertIn('net "$NET-int" "$BASE.5.0/24" --internal', text)
        self.assertIn("enable_ip_masquerade=false", text)              # extension networks: no NAT
        self.assertIn('--frontend-host "$CORE_INT"', text)
        self.assertIn('--admin-host "$CORE_INT"', text)
        self.assertIn('--host "$CORE_PUB"', text)
        self.assertIn('--host "$addr"', text)                           # each extension on its own address
        self.assertNotIn("0.0.0.0", text)
        self.assertNotIn("--cap-add", text)
        self.assertNotIn("--network host", text)
        self.assertIn('SAFE="--cap-drop ALL --security-opt no-new-privileges --read-only', text)   # used by every container
        self.assertEqual(text.count("$SAFE"), 4)


class R2WebHostsPassThrough(unittest.TestCase):
    def test_install_passes_the_operators_values_and_hard_codes_none(self):
        """install.sh passes PARLOR_NAME, PARLOR_AUTH, PARLOR_SIGNUP and PARLOR_WEB_HOSTS through from the
        operator's environment, each only when set, and names no server of its own."""
        import subprocess
        with open(os.path.join(ROOT, "deploy", "install.sh")) as f:
            text = f.read()
        self.assertNotIn("parlornet.org", text)
        line = text[text.index("$(for v in"):text.index("done)") + 5]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARLOR_NAME": "games.example.org",
               "PARLOR_AUTH": "password", "PARLOR_WEB_HOSTS": "games.example.org,www.games.example.org"}
        out = subprocess.run(["sh", "-eu", "-c", "echo " + line], env=env, capture_output=True, text=True).stdout
        self.assertEqual(out.split(), ["-e", "PARLOR_NAME=games.example.org", "-e", "PARLOR_AUTH=password",
                                       "-e", "PARLOR_WEB_HOSTS=games.example.org,www.games.example.org"])

    def test_start_reads_web_hosts_from_the_settings_file(self):
        import tempfile
        import shutil
        import subprocess
        import json
        d = tempfile.mkdtemp(prefix="pndata")
        try:
            conf = os.path.join(d, "settings.conf")
            with open(conf, "w") as f:
                f.write("email.from=x@example.org\nweb.hosts=parlornet.org,parlornet.org:6500\n")
            env = dict(os.environ, PARLOR_DB=os.path.join(d, "parlord.db"), PARLOR_ADAPTERS="web",
                       PARLOR_SETTINGS=conf)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "deploy", "start.py"), "--prepare"],
                               env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["web_hosts"],
                             "parlornet.org,parlornet.org:6500")
        finally:
            shutil.rmtree(d, True)


class InfoKeys(unittest.IsolatedAsyncioTestCase):
    async def test_an_unreadable_key_does_not_end_sessions(self):
        keys = make_keys("web")
        srv = server("none")
        srv.keyring = frontends.Keyring(keys)
        srv.keyring.load()
        from test_review_fixes import frontend
        fp = srv.keyring.fingerprint("web")
        sid = (await say(srv, frontend("web", fp), "HELO ann")).split()[1]
        path = os.path.join(keys, "web.key")
        with open(path) as f:
            good = f.read()
        time.sleep(0.01)
        with open(path, "w") as f:
            f.write("half a key\n")             # a hand edit
        await srv.sweep()
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        with open(path, "w") as f:
            f.write(good)
        srv.keyring.load()
        self.assertEqual(await say(srv, frontend("web", fp), "SES " + sid), "OK ann")
        os.unlink(path)
        await srv.sweep()
        self.assertEqual(srv.store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
