"""Re-test round 2: handshake slots, the adapters' pending gate, the web's
dialog eviction. Local only.

 a) default mode residual: idle sockets to the extension port from the same
    address as the extensions (127.0.0.1): how often does a real extension
    still get through?
 b) the gate (adapters/common.Gate) and the web's make_room: with many
    addresses holding one pending each, whose connection is evicted?

    python3.13 r18_round2_slots.py
"""
import asyncio
import os
import time

from harness import Stack, http, post
from adapters.common import CoreClient, Gate  # noqa: E402


async def flood(port, stop, counter):
    while not stop.is_set():
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            counter[0] += 1
        except OSError:
            await asyncio.sleep(0.01)


async def part_a():
    s = Stack("none", adapters=[("web", "adapters.web")]).start()
    try:
        for label, tasks_n in (("no flood", 0), ("1 flooding task", 1), ("8 flooding tasks", 8)):
            stop, counter = asyncio.Event(), [0]
            tasks = [asyncio.create_task(flood(s.fe, stop, counter)) for _ in range(tasks_n)]
            await asyncio.sleep(1)
            ok = t0 = 0
            start = time.time()
            for i in range(20):
                r = await http(s.ports["web"], post("/login.htm", "n=u%d" % i), timeout=15)
                ok += " 302 " in r.split("\r\n")[0]
            el = time.time() - start
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            print("a) %-17s web sign-ins that got through: %2d/20 in %.1f s (idle sockets opened: %d)"
                  % (label, ok, el, counter[0]))
    finally:
        s.stop()


class W:
    """A stand-in writer that records being closed."""
    def __init__(self, who):
        self.who, self.closed = who, False

        class T:
            def abort(t):
                self.closed = True
        self.transport = T()

    def write(self, b):
        pass


def part_b():
    for addrs, legit_n in ((10, 1), (40, 1), (20, 2)):
        g = Gate(40)            # IOS: max_signed 40, so pending_max 40
        legit = [W("legit") for _ in range(legit_n)]
        for w in legit:
            g.admit("192.0.2.50", w)            # players behind one address, signing in (waiting for a mail)
            time.sleep(0.001)
        att = []
        for rnd in range(3):                    # the attacker keeps connecting, one per address per round
            for a in range(addrs):
                w = W("att")
                g.admit("10.%d.%d.1" % (a // 250, a % 250), w)
                att.append(w)
        print("b) gate of 40: %d attacker addresses x 3 rounds, %d player(s) behind one address: players closed: %d/%d"
              % (addrs, legit_n, sum(w.closed for w in legit), legit_n))


asyncio.run(part_a())
part_b()
