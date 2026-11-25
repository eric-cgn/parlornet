"""R2: the web keeps at most MAX_SIGNINS (100) sign-in dialogs, each for 15
minutes. 100 unauthenticated POSTs with made-up names fill them; every
player's sign-in, sign-up and password change on the web then gets 503 for
15 minutes.

    python3.13 review/r2_web_signin_slots.py
"""
import asyncio

from harness import Stack, http, post


async def main():
    s = Stack("password", "email", adapters=[("web", "adapters.web")])
    s.seed("alice", password="Alice's own 1", email="alice@example.org")
    s.start()
    try:
        for i in range(100):
            r = await http(s.ports["web"], post("/login.htm", "n=x%05d" % i))
            assert "200 OK" in r.split("\r\n")[0], r[:200]
        print("100 POST /login.htm with made-up names: each got a sign-up question page")
        r = await http(s.ports["web"], post("/login.htm", "n=alice&k=Alice%27s+own+1"))
        print("alice signing in now:", r.split("\r\n")[0], "|", "Busy, try again in a minute." in r)
    finally:
        s.stop()


asyncio.run(main())
