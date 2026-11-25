"""R10: sign-up under sign-in password hashes every chosen password (scrypt,
16 MiB) and checks every confirmation (scrypt again), with no lockout and no
limit but the 10 requests/second per connection. The work runs in the default
thread pool while holding one of 64 name-stripe locks; requests for names in
one stripe pile up in the pool's few workers, so everyone's sign-in waits.

    python3.13 review/r10_scrypt_stripe.py
"""
import asyncio
import hashlib
import time

from harness import Client, Stack

CONNS = 100


def stripe(name):
    return hashlib.sha256(name.lower().encode()).digest()[0] % 64


async def attacker(port, names, stop):
    c = await Client.connect(port)
    i = 0
    while not stop.is_set():
        try:
            await c.req("HELO %s" % names[i % len(names)], show=False)
            await c.req("AUT password aaaaaaaa", show=False)
            await c.req("AUT confirm bbbbbbbb", show=False)
        except Exception:
            return
        i += 1


async def timed_signin(port, who):
    c = await Client.connect(port)
    t = time.time()
    await c.req("HELO %s" % who, show=False)
    await c.req("AUT password carolpass1", show=False)
    r = await c.req("AUT confirm carolpass1", show=False)
    c.close()
    return time.time() - t, r[-1]


async def main():
    s = Stack("password").start()
    try:
        print("legit sign-up, idle server: %.2f s %s" % await timed_signin(s.core, "carol0"))
        for label, names in (("names in one stripe", [n for n in ("s%d" % i for i in range(5000))
                                                       if stripe(n) == 0][:50]),
                             ("names spread over stripes", ["t%d" % i for i in range(50)])):
            stop = asyncio.Event()
            tasks = [asyncio.create_task(attacker(s.core, names, stop)) for _ in range(CONNS)]
            await asyncio.sleep(3)
            for k in range(3):
                print("legit sign-up during %d attacking connections (%s): %.2f s %s"
                      % ((CONNS, label) + await timed_signin(s.core, "c%s%d" % (label.split()[1][:3], k))))
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        s.stop()


asyncio.run(main())
