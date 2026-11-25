"""R7: more than 3 addresses blocked in 24 hours (SMTP 5xx at submission, or
lines added to email.blocked_file by the operator's bounce-list job) holds
every new email sign-up until the operator resumes sending. An attacker who
signs up with 4 undeliverable addresses (4 mails, well inside every cap)
causes 4 hard bounces at the relay; once the bounce job copies them into the
blocked file, nobody can sign up. Repeatable after each resume.

    python3.13 review/r7_bounce_hold.py
"""
import asyncio
import os

from harness import Client, Stack


async def main():
    s = Stack("password", "email")
    blocked = os.path.join(s.dir, "blocked.txt")
    open(blocked, "w").close()
    s.settings = {"email.blocked_file": blocked, "email.limit.per_minute": "10"}   # only to keep the per-minute cap out of the way
    s.start()
    try:
        # the attacker's four sign-ups (transport log: the mails "go out")
        for i in range(4):
            c = await Client.connect(s.core)
            await c.req("HELO bnc%d" % i, show=False)
            await c.req("AUT confirm y", show=False)
            r = await c.req("AUT email nobody%d@bounce.example" % i, show=False)
            print("attacker sign-up %d: %s" % (i, r[-1]))
            c.close()
        # ...they bounce; the operator's job writes them to the blocked file
        with open(blocked, "w") as f:
            f.write("".join("nobody%d@bounce.example\n" % i for i in range(4)))
        c = await Client.connect(s.core)
        print("\nan honest newcomer:")
        await c.req("HELO newbie")
        await c.req("AUT confirm y")
        await c.req("AUT email newbie@example.org")
        c.close()
    finally:
        s.stop()


async def caps():
    """The server-wide caps (3 a minute, 20 an hour, 60 a day) are shared by
    sign-up and recovery for everyone; made-up names and addresses use them up."""
    s = Stack("password", "email")
    s.seed("alice", password="Alice's own 1", email="alice@example.org")
    s.start()
    try:
        print("\n== the server-wide caps ==")
        for i in range(3):
            c = await Client.connect(s.core)
            await c.req("HELO fill%d" % i, show=False)
            await c.req("AUT confirm y", show=False)
            r = await c.req("AUT email fill%d@example.net" % i, show=False)
            print("attacker sign-up %d: %s" % (i, r[-1]))
            c.close()
        print("alice, who forgot her password:")
        c = await Client.connect(s.core)
        await c.req("HELO alice")
        await c.req("AUT password reset")
        c.close()
    finally:
        s.stop()


asyncio.run(main())
asyncio.run(caps())
