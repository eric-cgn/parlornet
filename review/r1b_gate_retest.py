"""Re-test of R1 and the new connection caps (local only).

 a) 410 idle connections from one address to IOS, FIBS and telnet: how many
    are admitted, and do web and telnet still work for others?
 b) The per-adapter pre-login gate: 5 addresses x 4 = PENDING_MAX 20. Only
    127.0.0.1 is available here, so the IOS adapter is started with the
    per-address limit raised to 20 to stand for 5 addresses.
 c) One server extension's share: a key of its own opening 260 connections.
 d) The core's handshake cap (32): idle TCP connections to the extension
    port, as anything that can reach 6590 could open (a compromised
    extension process in the shared network namespace, or a published 6590).

    ulimit -n 8192; python3.13 r1b_gate_retest.py
"""
import asyncio
import os
import subprocess
import time

from harness import PY, ROOT, Stack, frontends, free_port, http, post

sys_path = None


async def first_bytes(port, n=4096, wait=5):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    try:
        return await asyncio.wait_for(r.read(n), wait)
    except asyncio.TimeoutError:
        return b"(nothing)"
    finally:
        w.close()


async def web_ok(s):
    r = await http(s.ports["web"], post("/login.htm", "n=zed%d" % int(time.time() * 1000 % 100000)))
    return r.split("\r\n")[0]


async def main():
    s = Stack("password", adapters=[("ios", "adapters.ios"), ("fibs", "adapters.fibs"),
                                    ("web", "adapters.web"), ("telnet", "adapters.telnet")])
    frontends.write_key(s.keys, "probe", "frontend")
    s.start()
    try:
        print("== a) 410 idle connections from one address ==")
        held, refused = [], 0
        for i in range(410):
            port = (s.ports["ios"], s.ports["fibs"], s.ports["telnet"])[i % 3]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            held.append((r, w))
        await asyncio.sleep(2)
        for r, _ in held:
            try:
                data = await asyncio.wait_for(r.read(200), 0.05)
            except asyncio.TimeoutError:
                data = b""
            refused += b"full" in data
        print("refused 'Server is full': %d of 410" % refused)
        print("web sign-in for someone else:", await web_ok(s))
        for _, w in held:
            w.close()

        print("\n== b) IOS pre-login gate full (20 pending, standing for 5 addresses) ==")
        ios2 = free_port()
        code = ("import adapters.common as c; c.Gate.__init__.__defaults__ = (20, 20); import runpy; "
                "runpy.run_module('adapters.ios', run_name='__main__')")
        p = subprocess.Popen([PY, "-c", code, "--port", str(ios2), "--core", "127.0.0.1:%d" % s.fe,
                              "--key", os.path.join(s.keys, "ios.key")], cwd=ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        s._wait_port(ios2)
        conns = [await asyncio.open_connection("127.0.0.1", ios2) for _ in range(20)]
        await asyncio.sleep(0.5)
        print("21st client (a real player):", (await first_bytes(ios2, wait=2))[:60])
        for _, w in conns:
            w.close()
        p.kill()

        print("\n== c) one extension's share (key 'probe') ==")
        from adapters.common import CoreClient, CoreError
        mine, failed = [], 0
        for _ in range(260):
            try:
                mine.append(await CoreClient.connect("127.0.0.1", s.fe, os.path.join(s.keys, "probe.key")))
            except (CoreError, OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                failed += 1
        print("probe connections that got in: %d, refused: %d" % (len(mine), failed))
        print("web sign-in meanwhile:", await web_ok(s))
        for c in mine:
            c.close()
        await asyncio.sleep(1)

        print("\n== d) 32 idle TCP connections to the extension port (no TLS, no key) ==")
        idle = [await asyncio.open_connection("127.0.0.1", s.fe) for _ in range(32)]
        await asyncio.sleep(0.3)
        t = time.time()
        print("web sign-in while they are open:", await web_ok(s), "(%.1f s)" % (time.time() - t))
        try:
            c = await CoreClient.connect("127.0.0.1", s.fe, os.path.join(s.keys, "telnet.key"))
            print("telnet extension connects: yes")
            c.close()
        except Exception as e:
            print("telnet extension connects: no (%s)" % type(e).__name__)
        for _, w in idle:
            w.close()
    finally:
        s.stop()


asyncio.run(main())
