"""R11: two smaller session issues.

(a) The web's failed-resume limit is per client address, and once it trips
    every request from that address gets the cookie deleted (CLEAR_COOKIE),
    so 10 junk cookies sign out everyone behind the same NAT for 10 minutes.
(b) Rotating or removing a server extension's key closes its connections but
    does not end the sessions it was issued: after a web key compromise,
    session ids captured meanwhile keep working through the real web.

    python3.13 review/r11_web_sessions.py
"""
import asyncio
import re

from harness import Stack, frontends, http, post


def cookie_of(resp):
    return re.search(r"Set-Cookie: pn=([A-Za-z0-9_-]{22})", resp).group(1)


async def main():
    s = Stack("none", adapters=[("web", "adapters.web")]).start()
    w = s.ports["web"]
    try:
        sid = cookie_of(await http(w, post("/login.htm", "n=victim")))
        get = "GET /lobby.htm HTTP/1.0\r\nHost: 127.0.0.1\r\nCookie: pn=%s\r\n\r\n"
        r = await http(w, get % sid)
        print("(a) victim's lobby before:", "You are <b>victim</b>" in r)
        for i in range(10):
            await http(w, get % ("A" * 21 + chr(ord("A") + i)))
        r = await http(w, get % sid)
        print("    victim (same address) after 10 junk cookies from a neighbor:",
              "You are <b>victim</b>" in r, "| cookie deleted:", "Set-Cookie: pn=; Path=/; Max-Age=0" in r)

        print("(b) the operator rotates the web key after a compromise")
        sid2 = cookie_of(await http(w, post("/login.htm", "n=other")))   # the id the intruder captured
        frontends.main(["--keys", s.keys, "rotate", "web"])
    finally:
        s.stop()
    # the web restarts with its new key; the core keeps its sessions
    s.adapters = [("web", "adapters.web")]
    s.procs = []
    s.start_core_only = True
    import subprocess, os, sys
    from harness import PY, ROOT
    core = subprocess.Popen([PY, "-m", "parlord", "--port", str(s.core), "--frontend-port", str(s.fe), "--keys",
                             s.keys, "--db", s.db, "--auth", "none"], cwd=ROOT, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    s._wait_port(s.core)
    web = subprocess.Popen([PY, "-m", "adapters.web", "--port", str(w), "--core", "127.0.0.1:%d" % s.fe,
                            "--key", os.path.join(s.keys, "web.key")], cwd=ROOT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    s._wait_port(w)
    try:
        r = await http(w, "GET /lobby.htm HTTP/1.0\r\nHost: 127.0.0.1\r\nCookie: pn=%s\r\n\r\n" % sid2)
        print("    the captured id after the rotation and a restart still resumes:", "You are <b>other</b>" in r)
    finally:
        for p in (core, web):
            p.kill()
            p.wait()


asyncio.run(main())
