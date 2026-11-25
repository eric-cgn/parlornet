"""Re-test round 2 (5666b98): wrong-answer budgets, trusted addresses,
recovery per requester, password-check rate, bounce attribution. Client
addresses come from a test extension's SRC. Local only.

    python3.13 r17_round2_accounts.py      (about 3 minutes: recovery mail is paced)
"""
import asyncio
import os

from harness import Client, Stack, frontends
from adapters.common import CoreClient  # noqa: E402

PASSWORD = "Seeded pass 1"          # every seeded player's (a test value)
WRONG = "Wrong one 1"


async def ext(s, src):
    c = await CoreClient.connect("127.0.0.1", s.fe, os.path.join(s.keys, "probe.key"))
    d, f = await c.req("SRC %s" % src)
    assert f == "OK", f
    return c


async def one(s, src, *lines):
    c = await ext(s, src)
    out = None
    for l in lines:
        d, out = await c.req(l)
    c.close()
    return out


def sign_in(name, pw):
    return ("HELO %s" % name, "AUT password %s" % pw)


async def budgets():
    s = Stack("password", "email")
    frontends.write_key(s.keys, "probe", "frontend")
    for i in range(60):
        s.seed("p%02d" % i, password=PASSWORD, email="p%02d@example.org" % i)
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.seed("bob", password=PASSWORD, email="bob@example.org")
    s.start()
    try:
        print("== alice signs in from her home address 192.0.2.10 (becomes trusted there) ==")
        print("  ", await one(s, "192.0.2.10", *sign_in("alice", PASSWORD)))

        print("\n== per-address budget: 13 wrong passwords at 13 names from 198.51.100.1 ==")
        r = [await one(s, "198.51.100.1", *sign_in("p%02d" % i, WRONG)) for i in range(13)]
        print("   1-12:", sorted(set(x[:40] for x in r[:12])), "| 13th:", r[12][:75])

        print("\n== the server budget: 36 more from 198.51.100.2-4 (12 each) ==")
        n = 13
        for a in (2, 3, 4):
            for k in range(12):
                await one(s, "198.51.100.%d" % a, *sign_in("p%02d" % n, WRONG))
                n += 1
        print("   bob, from a cafe (203.0.113.5), with the RIGHT password:")
        print("    ", await one(s, "203.0.113.5", *sign_in("bob", PASSWORD)))
        print("   alice from her trusted home address, right password:",
              await one(s, "192.0.2.10", *sign_in("alice", PASSWORD)))

        print("\n== trusted-address abuse: a stranger behind alice's shared address (CGNAT, school) ==")
        r = [await one(s, "192.0.2.10", *sign_in("p%02d" % i, WRONG)) for i in range(50, 60)]
        print("   wrong passwords at 10 names from 192.0.2.10 after the server budget is spent:",
              sorted(set(x[:40] for x in r)))
        print("   (budgets exempt only the trusted name; the others are still refused)")
        r = [await one(s, "192.0.2.10", *sign_in("alice", WRONG)) for k in range(2)]
        print("   wrong passwords at alice from her trusted address:", [x[:45] for x in r])
    finally:
        s.stop()


async def recovery():
    s = Stack("password", "email", settings={"email.limit.recovery_per_minute": "10"})
    frontends.write_key(s.keys, "probe", "frontend")
    for i in range(25):
        s.seed("v%02d" % i, password=PASSWORD, email="v%02d@example.org" % i)
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.start()
    try:
        print("\n== recovery: alice trusted at 192.0.2.10; strangers ask for alice's reset ==")
        await one(s, "192.0.2.10", *sign_in("alice", PASSWORD))
        for a in (1, 2, 1):
            print("   stranger 198.51.100.%d:" % a, (await one(s, "198.51.100.%d" % a, "HELO alice", "AUT password reset"))[:70])
            await asyncio.sleep(61)
        print("   alice at home:", (await one(s, "192.0.2.10", "HELO alice", "AUT password reset"))[:70])

        print("\n== the server's recovery budget: 20 an hour, from 5 addresses x 4 ==")
        n, sent = 0, 0
        for a in range(5):
            for k in range(4):
                f = await one(s, "198.18.0.%d" % (a + 1), "HELO v%02d" % n, "AUT password reset")
                sent += f.startswith("ASK")
                n += 1
            await asyncio.sleep(1)
        print("   sent:", sent)
        await asyncio.sleep(61)
        print("   alice at home, trusted:", (await one(s, "192.0.2.10", "HELO alice", "AUT password reset"))[:75])
    finally:
        s.stop()


async def checks_and_bounces():
    s = Stack("password", "email")
    frontends.write_key(s.keys, "probe", "frontend")
    s.seed("carol", password="Carol pass 1", email="carol@example.org")
    blocked = os.path.join(s.dir, "blocked.txt")
    with open(blocked, "w") as f:      # a provider's list: addresses parlord never mailed
        f.write("".join("other%d@elsewhere.example\n" % i for i in range(10)))
    s.settings = {"email.blocked_file": blocked}
    s.start()
    try:
        print("\n== password checks per address: carol signs in and out with her right password ==")
        c = await Client.connect(s.core)
        res = []
        for i in range(32):
            await c.req("HELO carol", show=False)
            r = await c.req("AUT password Carol pass 1", show=False)
            res.append(r[-1][:50])
            if r[-1].startswith("OK"):
                await c.req("OUT", show=False)
            await asyncio.sleep(0.25)
        c.close()
        print("   OK:", sum(x.startswith("OK") for x in res), "| then:", res[-1])

        print("\n== bounce hold: 10 provider-list addresses parlord never mailed ==")
        print("  ", await one(s, "203.0.113.9", "HELO newbie", "AUT confirm y", "AUT email newbie@example.org"))
    finally:
        s.stop()


if __name__ == "__main__":
    asyncio.run(budgets())
    asyncio.run(recovery())
    asyncio.run(checks_and_bounces())
