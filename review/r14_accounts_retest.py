"""Re-test of R3-R5 and R7 after the fixes, and the new pieces (SRC, the
owner notice, the reset by mail, lockout decay). Local only; other client
addresses come from a test extension's SRC. AUT values are never printed.

    python3.13 r14_accounts_retest.py        (takes about 130 s: one wait for the 60 s mail spacing)
"""
import asyncio
import os
import time

from harness import Client, Stack, auth, frontends, masked
from adapters.common import CoreClient  # noqa: E402

PASSWORD = "Seeded pass 1"


class Ext:
    """The native protocol on the extension channel with the 'probe' key, as a
    server extension would speak it for a client at address src."""

    def __init__(self, s):
        self.s = s

    async def open(self, src=None):
        c = await CoreClient.connect("127.0.0.1", self.s.fe, os.path.join(self.s.keys, "probe.key"))
        if src:
            d, f = await c.req("SRC %s" % src)
            assert f == "OK", f
        return c


async def say(c, line):
    d, f = await c.req(line)
    print("  > %s\n%s  < %s" % (masked(line), "".join("  < %s\n" % x for x in d), f))
    return f


def simulate(days=7):
    """A guesser who tries a name again as soon as its lock ends: guesses a day."""
    t, fails, missed, n, per_day = 0.0, 0, 0.0, 0, []
    end_day = 86400
    while t < days * 86400:
        fails, missed = auth.decay(fails, missed, t + 1e9)  # decay uses absolute times; shift away from 0
        fails += 1
        missed = t + 1e9
        n += 1
        t += auth.lock_secs(fails)
        if t >= end_day:
            per_day.append(n)
            n, end_day = 0, end_day + 86400
    return per_day


async def main():
    print("== lockout arithmetic: guesses a day at one name by a patient guesser ==")
    print("  ", simulate(), "(before the fix: 7 on day 1, then 1 a day)")

    s = Stack("password", "email", settings={"email.limit.per_minute": "10"})
    frontends.write_key(s.keys, "probe", "frontend")
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    for i in range(40):
        s.seed("p%02d" % i, password=PASSWORD, email="p%02d@example.org" % i)
    s.start()
    ext = Ext(s)
    try:
        print("\n== SRC: who may send it ==")
        c = await Client.connect(s.core)
        await c.req("SRC 203.0.113.9")
        c.close()
        e = await ext.open()
        await say(e, "SRC not-an-address")
        await say(e, "SRC 203.0.113.9")
        await say(e, "SRC 198.51.100.1")
        await say(e, "HELO zz1")
        await say(e, "SRC 203.0.113.9")
        e.close()

        print("\n== F5: owned and free address, the same answer ==")
        for addr in ("alice@example.org", "free1@example.org"):
            e = await ext.open("192.0.2.%d" % (1 if addr.startswith("a") else 2))
            await say(e, "HELO nn%s" % addr[:3])
            await say(e, "AUT confirm y")
            await say(e, "AUT email %s" % addr)
            e.close()
        print("  mails logged:", [l for l in s.core_output().splitlines() if l.startswith("mail to")])

        print("\n== breadth guessing: one public connection, 40 names, one wrong password each ==")
        c = await Client.connect(s.core)
        out = []
        for i in range(40):
            await c.req("HELO p%02d" % i, show=False)
            out.append((await c.req("AUT password Wrong one 1", show=False))[-1])
            await asyncio.sleep(0.25)          # under the 10 requests/second per connection
        c.close()
        print("  replies:", sorted(set(o[:40] for o in out)), "x%d" % len(out))
        print("  the client address's budget (12 a day) stops it after 12 names")

        print("\n== residual: lock a name and use up its reset mail (attacker 198.51.100.7) ==")
        c = await Client.connect(s.core)
        await c.req("HELO alice")
        await c.req("AUT password Wrong one 1")
        c.close()
        for k in range(2):
            if k:
                print("  (waiting 61 s: mails to one address are 60 s apart)")
                await asyncio.sleep(61)
            e = await ext.open("198.51.100.7")
            await say(e, "HELO alice")
            await say(e, "AUT password reset")
            e.close()
        print("  (waiting 61 s again)")
        await asyncio.sleep(61)
        print("  alice herself, from 203.0.113.50:")
        e = await ext.open("203.0.113.50")
        await say(e, "HELO alice")
        await say(e, "AUT password reset")
        await say(e, "AUT password abcd-efgh-jkmn")
        e.close()
    finally:
        s.stop()


asyncio.run(main())
