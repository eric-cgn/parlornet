"""Re-test of R11 (web per-address resume limit) and R13 (scrypt work).

 a) after 10 junk cookies from an address, someone there who signs in fresh:
    the new session id has never resumed here, so it is not let through
 b) password sign-in: 100 public connections sign in with their own correct
    password over and over (no lockout on success; OUT and again); a player
    at another address (SRC from a test extension) times a sign-in

    python3.13 r16_web_and_cpu_retest.py
"""
import asyncio
import os
import re
import time

from harness import Client, Stack, frontends, http, post
from adapters.common import CoreClient, NeedAuth  # noqa: E402


def cookie_of(resp):
    m = re.search(r"Set-Cookie: pn=([A-Za-z0-9_-]{22})", resp)
    return m.group(1) if m else None


async def part_a():
    s = Stack("none", adapters=[("web", "adapters.web")]).start()
    w = s.ports["web"]
    get = "GET /lobby.htm HTTP/1.0\r\nHost: 127.0.0.1\r\nCookie: pn=%s\r\n\r\n"
    try:
        for i in range(10):
            await http(w, get % ("A" * 21 + chr(ord("A") + i)))
        sid = cookie_of(await http(w, post("/login.htm", "n=newcomer")))
        r = await http(w, get % sid)
        print("a) newcomer signs in after a neighbor's 10 junk cookies; lobby shown:", "You are <b>newcomer</b>" in r)
    finally:
        s.stop()


async def attacker(port, i, stop):
    c = await Client.connect(port)
    for l in ("HELO bot%03d" % i, "AUT password botpass123", "AUT confirm botpass123", "OUT"):
        await c.req(l, show=False)
    while not stop.is_set():
        try:
            await c.req("HELO bot%03d" % i, show=False)
            await c.req("AUT password botpass123", show=False)
            await c.req("OUT", show=False)
        except Exception:
            return


async def timed(s):
    c = await CoreClient.connect("127.0.0.1", s.fe, os.path.join(s.keys, "probe.key"))
    await c.req("SRC 203.0.113.77")
    t = time.time()
    try:
        await c.helo("carol")
    except NeedAuth:
        pass
    await c.aut("password", "carolpass1")
    c.close()
    return time.time() - t


async def part_b():
    s = Stack("password")
    frontends.write_key(s.keys, "probe", "frontend")
    s.seed("carol", password="carolpass1")
    s.start()
    try:
        print("b) carol's sign-in, idle server: %.2f s" % await timed(s))
        stop = asyncio.Event()
        tasks = []
        for i in range(100):     # sign-ups from one address: 20 per 10 minutes, so 5 rounds would be needed;
            tasks.append(asyncio.create_task(attacker(s.core, i, stop)))   # here they share 127.0.0.1
        await asyncio.sleep(4)
        for _ in range(3):
            print("   carol's sign-in during the flood: %.2f s" % await timed(s))
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        s.stop()


asyncio.run(part_a())
asyncio.run(part_b())
