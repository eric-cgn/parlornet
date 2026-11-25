"""Re-test of R7 (email availability) with several client addresses, given by
a test extension's SRC. Local only.

 a) bounces from one address: refused for that address only
 b) bounces from 4 addresses: every new sign-up held (the stated residual)
 c) sign-up budget used up from 5 addresses; recovery still goes out

    python3.13 r15_email_retest.py
"""
import asyncio
import os

from harness import Stack, frontends
from adapters.common import CoreClient  # noqa: E402

PASSWORD = "Alice's own 1"


async def ext(s, src):
    c = await CoreClient.connect("127.0.0.1", s.fe, os.path.join(s.keys, "probe.key"))
    await c.req("SRC %s" % src)
    return c


async def signup(s, src, name, addr, show=False):
    c = await ext(s, src)
    for line in ("HELO %s" % name, "AUT confirm y", "AUT email %s" % addr):
        d, f = await c.req(line)
    c.close()
    if show:
        print("  %s from %s: %s" % (addr, src, f))
    return f


async def main():
    s = Stack("password", "email")
    blocked = os.path.join(s.dir, "blocked.txt")
    open(blocked, "w").close()
    s.settings = {"email.blocked_file": blocked, "email.limit.per_minute": "10"}
    frontends.write_key(s.keys, "probe", "frontend")
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.start()
    try:
        print("== a) two bounces from 198.51.100.1 ==")
        for i in range(2):
            await signup(s, "198.51.100.1", "ba%d" % i, "nobody%d@bounce.example" % i)
        with open(blocked, "a") as f:
            f.write("nobody0@bounce.example\nnobody1@bounce.example\n")
        await signup(s, "198.51.100.1", "ba9", "real1@example.org", show=True)
        await signup(s, "203.0.113.5", "newbie1", "newbie1@example.org", show=True)

        print("== b) one bounce each from three more addresses (4 requesters in all) ==")
        for i, src in enumerate(("198.51.100.2", "198.51.100.3", "198.51.100.4")):
            await signup(s, src, "bb%d" % i, "x%d@bounce.example" % i)
        with open(blocked, "a") as f:
            f.write("".join("x%d@bounce.example\n" % i for i in range(3)))
        await signup(s, "203.0.113.6", "newbie2", "newbie2@example.org", show=True)
    finally:
        s.stop()

    s = Stack("password", "email", settings={"email.limit.per_minute": "10"})
    frontends.write_key(s.keys, "probe", "frontend")
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.start()
    try:
        print("== c) 20 sign-up mails from 5 addresses (4 each, the per-address hourly cap) ==")
        n = 0
        for a in range(5):
            for k in range(4):
                f = await signup(s, "192.0.2.%d" % (10 + a), "f%d%d" % (a, k), "f%d%d@example.net" % (a, k))
                n += f.startswith("ASK")
        print("  sent: %d" % n)
        await signup(s, "203.0.113.7", "newbie3", "newbie3@example.org", show=True)
        c = await ext(s, "203.0.113.8")
        await c.req("HELO alice")
        d, f = await c.req("AUT password reset")
        print("  alice's reset from 203.0.113.8:", f)
        c.close()
    finally:
        s.stop()


asyncio.run(main())
