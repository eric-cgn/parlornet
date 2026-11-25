"""Round 2: the server-wide recovery budget is shared with the owners' own
recovery. Scaled down (email.limit.recovery_per_hour=8 instead of 20) so it runs
in seconds; the default needs 5 addresses x 4 an hour, or 6 x 10 for the day's 60.
    python3.13 r17b_recovery_budget.py"""
import asyncio
from r17_round2_accounts import one, sign_in, PASSWORD
from harness import Stack, frontends


async def main():
    s = Stack("password", "email", settings={"email.limit.recovery_per_hour": "8", "email.limit.recovery_per_minute": "10"})
    frontends.write_key(s.keys, "probe", "frontend")
    for i in range(10):
        s.seed("v%02d" % i, password=PASSWORD, email="v%02d@example.org" % i)
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.start()
    try:
        await one(s, "192.0.2.10", *sign_in("alice", PASSWORD))        # alice trusted at home
        sent = 0
        for k in range(8):
            f = await one(s, "198.18.0.%d" % (k // 4 + 1), "HELO v%02d" % k, "AUT password reset")
            sent += f.startswith("ASK")
        print("strangers' recovery mails to 8 players, from 2 addresses:", sent)
        print("alice at home (trusted) asks for her reset:", (await one(s, "192.0.2.10", "HELO alice", "AUT password reset"))[:75])
    finally:
        s.stop()

asyncio.run(main())
