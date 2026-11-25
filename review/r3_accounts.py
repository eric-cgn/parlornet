"""R3-R5 on the public port (6501), the live pair password+email.

R3  the email sign-up step names the account that owns an address (EMAILINUSE)
R4  a locked name cannot reach recovery; once fails >= 7, one wrong answer a
    day (by anyone) keeps it locked indefinitely; fails never decay
R5  wrong answers at the reset steps did not count toward the name's lockout;
    each new mail gave 5 more guesses (now: a reset mails a temporary password,
    and every wrong answer after it counts like any other)

    python3.13 review/r3_accounts.py
"""
import asyncio
import time

from harness import Client, Stack, auth

PASSWORD = "Seeded pass 1"


async def main():
    s = Stack("password", "email")
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.seed("carol", password=PASSWORD, email="carol@example.org", fails=6)   # six earlier misses (~20 h of attempts)
    s.start()
    try:
        print("== R3: which name owns an address (no mail is sent) ==")
        c = await Client.connect(s.core)
        print(c.greeting)
        await c.req("HELO probe1")
        await c.req("AUT confirm y")
        await c.req("AUT email alice@example.org")
        c.close()

        print("\n== R4: carol has 6 misses; one more wrong password ==")
        c = await Client.connect(s.core)
        await c.req("HELO carol")
        await c.req("AUT password Wrong one 1")
        await c.req("HELO carol")
        print("carol herself, wanting a new password by mail, gets the question with a note on reset:")
        print("  fails/locked_until in the database:", s.player_row("carol")[1:3], "now", int(time.time()))
        c.close()
        print("lock schedule (seconds) after n misses:", [(n, auth.lock_secs(n)) for n in range(1, 9)])
        print("time to reach the 1-day plateau: %.1f h" % (sum(auth.lock_secs(n) for n in range(1, 7)) / 3600))

        print("\n== R5: a reset, then wrong guesses at the mailed temporary password ==")
        c = await Client.connect(s.core)
        await c.req("HELO alice")
        await c.req("AUT password reset")
        await c.req("AUT password abcd-efgh-jkmn")
        for g in ("abcd-efgh-jkmp", "abcd-efgh-jkmq"):
            await c.req("HELO alice", show=False)
            await c.req("AUT password %s" % g)
        print("alice fails/locked_until after 3 wrong answers (the first counted, the rest refused while locked):",
              s.player_row("alice")[1:3])
        c.close()
    finally:
        s.stop()


asyncio.run(main())
