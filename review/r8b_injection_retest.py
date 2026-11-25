"""Re-test of R8/R12: a newline in a web sign-in answer, and bare LF / odd
Host headers. python3.13 r8b_injection_retest.py"""
import asyncio
import re
from urllib.parse import quote

from harness import Stack, http, post


def lid_of(page):
    return re.search(r'name="lid" value="([^"]+)"', page).group(1)


def shown(page):
    m = re.search(r"<p><b>(.*?)</b></p>", page)
    return (page.split("\r\n")[0], m.group(1) if m else None)


async def main():
    s = Stack("password", adapters=[("web", "adapters.web")]).start()
    w = s.ports["web"]
    try:
        p = await http(w, post("/login.htm", "n=mallory"))
        p = await http(w, post("/login.htm", "lid=%s&f=password&v=%s" % (lid_of(p), quote("longpassword1\nSES AAAAAAAAAAAAAAAAAAAAAA"))))
        print("injected newline in v:", shown(p))
        for host in ("evil.example\nSet-Cookie: pn=AAAAAAAAAAAAAAAAAAAAAA", "evil.example", "evil.example:80@x"):
            r = await http(w, "GET / HTTP/1.0\r\nHost: %s\r\n\r\n" % host)
            loc = [l for l in r.split("\r\n\r\n")[0].split("\r\n") if l.lower().startswith(("location", "set-cookie", "http/"))]
            print("Host %r ->" % host, loc)
        print("core log mentions SES:", "SES" in s.core_output())
    finally:
        s.stop()


asyncio.run(main())
