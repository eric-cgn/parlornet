"""R1: unauthenticated connections to the IOS (and FIBS) adapter each open a
core extension-channel connection before login and are not counted against
the adapter's MAX_SESSIONS (only signed-in sessions are). 400 of them fill
the core's max_frontend_conns, after which every server extension (web,
telnet, IOS, FIBS, Outland) is refused by the core.

    python3.13 review/r1_extension_exhaustion.py
"""
import asyncio

from harness import Stack, http, post

N = 410


async def main():
    s = Stack("password", adapters=[("ios", "adapters.ios"), ("fibs", "adapters.fibs"),
                                    ("web", "adapters.web"), ("telnet", "adapters.telnet")]).start()
    try:
        before = await http(s.ports["web"], post("/login.htm", "n=alice"))
        print("web sign-in before the flood:", before.split("\r\n")[0], "| ASK page:", "Choose a password" in before)
        socks = []
        for i in range(N):
            port = s.ports["ios"] if i % 2 == 0 else s.ports["fibs"]
            socks.append(await asyncio.open_connection("127.0.0.1", port))     # say nothing: never log in
        await asyncio.sleep(4)
        print("opened %d idle connections to the IOS and FIBS ports (nothing sent)" % N)
        after = await http(s.ports["web"], post("/login.htm", "n=bob"))
        print("web sign-in after:", after.split("\r\n")[0], "|", "isn't answering" in after and
              "The game server isn't answering." or after[-200:])
        r, w = await asyncio.open_connection("127.0.0.1", s.ports["telnet"])
        w.write(b"\r\n")                          # Enter at the title page, typed ahead
        data = b""
        try:
            while b"answering" not in data and b"name" not in data:
                chunk = await asyncio.wait_for(r.read(4096), 15)
                if not chunk:
                    break
                data += chunk
        except asyncio.TimeoutError:
            pass
        print("telnet after:", "The game server isn't answering." if b"isn't answering" in data else data[-120:])
        w.close()
        for _, w in socks:
            w.close()
    finally:
        s.stop()


asyncio.run(main())
