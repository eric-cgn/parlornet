"""The Play extension: ParlorNet Play, the modern client for today's browsers and phones
(docs/modern-client.md), on its own port, 6513, apart from the web extension's HTML 3.2 pages on 6500.
Either runs without the other.

It answers under /play/ only:

- the client's files from adapters/play/, as they are, /play/palette.json (the Grid Words presets,
  adapters/palette.py) and /play/rummytile-colours.json (the Rummy Tile presets, adapters/rtpalette.py),
  each with a Content-Security-Policy that allows only this site's own files and its WebSocket;
- /play/ws, PARLORNET/1 over a WebSocket (adapters/wsbridge.py; docs/protocol.md, WebSocket);
- index.html names the classic pages (the web extension's) by their full address, http://<this host>:<the
  port the web extension declared>/, since they are another site now; while the web extension does not run
  (it is not in the core's PRL), the page has none and hides its links to them;
- POST /play/push, the page's requests about push notifications (adapters/playpush.py,
  docs/play-notifications.md): this browser's subscription on or off, and which notices the player wants;
  index.html carries the extension's VAPID public key in its meta tag pn-push while push is on;
- POST /play/session, which trades the one-time handle a sign-in over the WebSocket gave (HND) for this
  extension's own session cookie, pnplay (Path=/play/, HttpOnly, SameSite=Strict), so a reload resumes with
  SES alone; out=1 clears it. The web extension's cookie (pn) is never read or set here.

It speaks plain HTTP and is meant to sit behind a reverse proxy that terminates TLS (deploy/README.md):

- --public-url (the setting play.public_url, PARLOR_PLAY_URL) is the address players open, such as
  https://parlornet.org/play/. The WebSocket's Origin must then be that URL's origin, the cookie is Secure
  when it is https, and the port declared to the core is that URL's port. Without it, the Origin must name
  the host and port of the Host header, as a page loaded straight from this port does.
- --proxies (play.proxies, PARLOR_PLAY_PROXIES) lists the proxies' addresses or networks. For a request from
  one of them, the client is the last address in X-Forwarded-For that is not a proxy (SRC to the core, and
  the per-address limits), and X-Forwarded-Proto says whether the browser came over https. From any other
  address those headers are ignored and the peer is the client.

It never sends Strict-Transport-Security: that would hold for every port of the name, and the 1997 pages
on the same name are plain HTTP for browsers that know nothing else.

Push notifications need the public https URL, a contact (--push-contact, the setting play.push_contact,
PARLOR_PLAY_PUSH_CONTACT: the operator's mailto: or https: address, which push services may use) and a data
directory (--data, PARLOR_PLAY_DATA) for the subscriptions and the VAPID key; without any of them they are off.

    python3 -m adapters.playserver --core 127.0.0.1:6590 --key /data/frontends/play.key --port 6513 \\
        --host 0.0.0.0 --public-url https://parlornet.org/play/ --proxies 172.30.4.1 --data /data/play \\
        --push-contact mailto:operator@example.org
"""
import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .common import CoreClient, CoreError, addr_key, keepalive, peer_addr
from . import declare
from . import palette as PAL
from . import playpush
from . import rtpalette as RTPAL
from . import wsbridge

# The protocol this extension serves, declared to the core (adapters/declare.py): key, name, description.
PROTOCOL = ("HTTPS", "ParlorNet Play", "The modern client for today's browsers and phones")

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "play")
FILE_RE = re.compile(r"^/play/([a-z0-9][a-z0-9-]{0,40}\.(?:html|js|css|svg|png|ico|webmanifest))\Z")
TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
         "css": "text/css; charset=utf-8", "svg": "image/svg+xml", "png": "image/png", "ico": "image/x-icon",
         "webmanifest": "application/manifest+json"}
# The pages of /play/ load nothing from anywhere else: their own scripts, styles and images, and the WebSocket.
HEADERS = ("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; "
           "img-src 'self' data: blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'; "
           "base-uri 'none'; form-action 'none'\r\nX-Content-Type-Options: nosniff\r\n"
           "Referrer-Policy: no-referrer\r\n")
COOKIE = "pnplay"
COOKIE_PATH = "/play/"
SID_RE = re.compile(r"^[A-Za-z0-9_-]{22}\Z")
HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,62})(?:\.[a-z0-9-]{1,63})*\.?|\[[0-9a-f:.]{2,45}\])(?::\d{1,5})?\Z")
MAX_REQUESTS = 80           # requests in progress (an open WebSocket is not one: the bridge counts those)
REQUESTS_PER_ADDR = 20      # ... from one client address
HEAD_SECS = 10              # to send a request's head
MAX_HEAD = 8192
MAX_BODY = 512
MAX_PUSH_BODY = 2048        # POST /play/push: a push service's address and the browser's keys
GOOD_SECS = 86400           # how long a session id that resumed is remembered (session.max_secs)
SES_FAIL_LIMIT = (10, 600)  # failed resumes per client address in that many seconds, then no more tries
PRL_SECS = 30               # the core's list of protocols (PRL), for the web extension's port, is asked again
WEB_KEY = "HTTP"            # the web extension's protocol key (web.PROTOCOL)
CLASSIC_META = '<meta name="pn-classic" content="">'      # in index.html: the classic pages' address goes here
PUSH_META = '<meta name="pn-push" content="">'            # ... and the VAPID public key, while push is on
CLASSIC_LINKS = ("welcome.htm", "protocols.htm")         # index.html's links to them (href="/welcome.htm")
REASONS = {101: "Switching Protocols", 200: "OK", 302: "Found", 400: "Bad Request", 403: "Forbidden",
           404: "Not Found", 405: "Method Not Allowed", 426: "Upgrade Required", 503: "Service Unavailable"}
_cache = {}


def play_file(path):
    """A file of the modern client for a path under /play/ -> (bytes, content type), or None."""
    if path in ("/play/", "/play/index.html"):
        path = "/play/index.html"
    m = FILE_RE.match(path)
    if not m:
        return None
    full = os.path.join(DIR, m.group(1))
    try:
        st = os.stat(full)
    except OSError:
        return None
    hit = _cache.get(full)
    if not hit or hit[0] != st.st_mtime_ns:
        with open(full, "rb") as f:
            hit = _cache[full] = (st.st_mtime_ns, f.read())
    return hit[1], TYPES[m.group(1).rsplit(".", 1)[1]]


def respond(status, body=b"", ctype="text/plain; charset=utf-8", extra=""):
    if isinstance(body, str):
        body = body.encode("ascii", "replace")
    head = ("HTTP/1.1 %d %s\r\nServer: parlornet-play\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Connection: close\r\n%s\r\n" % (status, REASONS.get(status, "OK"), ctype, len(body), extra))
    return head.encode("ascii") + body


def public_url(text):
    """play.public_url -> (the URL, its origin), or ("", "") when it is empty. It must be http or https on
    a host name or address, at /play/ (the client's own paths start there); a bare origin gets /play/.
    ValueError otherwise."""
    text = (text or "").strip()
    if not text:
        return "", ""
    u = urlsplit(text)
    host = (u.netloc or "").lower()
    if (u.scheme not in ("http", "https") or "@" in host or not HOST_RE.match(host) or u.query or u.fragment
            or u.path not in ("", "/", "/play", "/play/")):
        raise ValueError("play.public_url is an address like https://parlornet.org/play/")
    default = {"http": 80, "https": 443}[u.scheme]
    if host.endswith(":%d" % default):
        host = host[:-len(":%d" % default)]
    origin = "%s://%s" % (u.scheme, host)
    return origin + "/play/", origin


def url_port(url):
    """The port of a public URL (its scheme's own when it names none)."""
    u = urlsplit(url)
    return u.port or (443 if u.scheme == "https" else 80)


def proxy_list(text):
    """play.proxies (comma-separated addresses or networks) -> [ip_network]. ValueError if one is not."""
    out = []
    for x in (text or "").split(","):
        if x.strip():
            try:
                out.append(ipaddress.ip_network(x.strip(), strict=False))
            except ValueError:
                raise ValueError("play.proxies is a comma-separated list of addresses or networks: %r" % x.strip())
    return out


def as_ip(text):
    """An address as an ip_address (an IPv4-mapped IPv6 one as IPv4), or None."""
    try:
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def parse_head(raw):
    """A request's head -> (method, target, version, headers), or None if it is not well formed. A header
    sent twice is joined with commas (X-Forwarded-For)."""
    lines = raw.decode("latin-1")[:-4].split("\r\n")
    parts = lines[0].split()
    if len(parts) != 3 or any(c in l for l in lines for c in "\r\n\0"):
        return None
    headers = {}
    for l in lines[1:]:
        k, sep, v = l.partition(":")
        if not sep:
            return None
        k, v = k.strip().lower(), v.strip()
        headers[k] = headers[k] + ", " + v if k in headers else v
    return parts[0], parts[1], parts[2], headers


class Play:
    def __init__(self, core_host, core_port, key_file=None, url="", proxies=(), data_dir="", push_contact="",
                 push_hosts=()):
        self.core_host, self.core_port = core_host, core_port
        self.key_file = key_file or CoreClient.key_file
        self.url, self.origin = public_url(url)
        self.proxies = list(proxies)
        self.active = 0
        self.active_by = {}         # client address -> requests in progress
        self.ses_fails = {}         # client address -> [times of failed resumes]
        self.good = {}              # SHA-256 of a session id that resumed here -> when it last did
        self.bridge = wsbridge.Bridge(self)
        self.web_port, self.prl_when = None, 0.0     # the web extension's port, from PRL; when it was asked
        self.push = playpush.Push(self, data_dir, push_contact, push_hosts)

    # ---- what the bridge asks of its server -------------------------------------------------------
    async def connect(self, req=None):
        """A connection to the core; with a request, the core is told the client's address (SRC)."""
        core = await CoreClient.connect(self.core_host, self.core_port, self.key_file)
        if req is not None:
            await core.source(req.get("peer"))
        return core

    def resume_allowed(self, addr, sid=None):
        """Failed resumes are counted per client address; past the limit no more are tried for a while,
        except for a session id that has resumed here before (so junk from one address behind a shared NAT
        does not sign out the others there)."""
        n, secs = SES_FAIL_LIMIT
        now = time.time()
        q = [t for t in self.ses_fails.get(addr, []) if now - t < secs]
        if q:
            self.ses_fails[addr] = q
        else:
            self.ses_fails.pop(addr, None)
        if len(q) < n:
            return True
        seen = self.good.get(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()) if sid else None
        return seen is not None and now - seen < GOOD_SECS

    def note_resumed(self, sid):
        """A session id that resumed (or was just issued) here: it may resume even past its address's limit."""
        if sid:
            now = time.time()
            if len(self.good) > 20000:
                self.good = {k: t for k, t in self.good.items() if now - t < GOOD_SECS}
            self.good[hashlib.sha256(sid.encode("ascii", "replace")).hexdigest()] = now

    def note_failed_resume(self, addr, sid):
        self.ses_fails.setdefault(addr, []).append(time.time())
        if sid:
            self.good.pop(hashlib.sha256(sid.encode("ascii", "replace")).hexdigest(), None)

    # ---- the client behind a proxy, the origin, the cookie ------------------------------------------
    def trusted(self, ip):
        return ip is not None and any(ip.version == n.version and ip in n for n in self.proxies)

    def client(self, peer, headers):
        """(the client's address, the scheme it came over or None). From a trusted proxy: the last address
        of X-Forwarded-For that is not a proxy (a malformed entry stops the walk at the one after it), and
        X-Forwarded-Proto; from anywhere else, the peer and None."""
        ip = as_ip(peer)
        if not self.trusted(ip):
            return peer, None
        addr = str(ip)
        for x in reversed(headers.get("x-forwarded-for", "").split(",")):
            hop = as_ip(x)
            if hop is None:
                break
            addr = str(hop)
            if not self.trusted(hop):
                break
        proto = headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
        return addr, proto if proto in ("http", "https") else None

    def origin_ok(self, origin, host, proto=None):
        """The public URL's origin when there is one; else the host and port of the Host header (and the
        scheme a trusted proxy names, if it names one)."""
        if self.origin:
            return origin == self.origin
        m = re.match(r"^(https?)://([^/]+)\Z", origin)
        return bool(m and len(host) <= 260 and HOST_RE.match(host) and m.group(2) == host
                    and (proto is None or m.group(1) == proto))

    def secure(self, req):
        """Whether the cookie is Secure: the public URL is https, or (without one) a trusted proxy says the
        browser came over https."""
        return self.url.startswith("https:") if self.url else req["proto"] == "https"

    def cookie(self, req, sid):
        attrs = "; Path=%s; HttpOnly; SameSite=Strict%s" % (COOKIE_PATH, "; Secure" if self.secure(req) else "")
        if sid:
            return "Set-Cookie: %s=%s%s\r\n" % (COOKIE, sid, attrs)
        return "Set-Cookie: %s=%s; Max-Age=0\r\n" % (COOKIE, attrs)

    @staticmethod
    def cookie_sid(headers):
        for part in headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and SID_RE.match(v):
                return v
        return None

    # ---- the classic pages ----------------------------------------------------------------------------
    async def classic(self, req):
        """The classic pages' address, http://host:port/ (the public URL's host or the Host header's name, the
        port the web extension declared), or "" while the web extension does not run."""
        if time.time() - self.prl_when > PRL_SECS:
            self.prl_when = time.time()
            try:
                core = await self.connect()
                try:
                    data, final = await core.req("PRL")
                finally:
                    core.close()
                if final.startswith("OK"):
                    rows = [l.split(" ", 3) for l in data]
                    self.web_port = next((int(f[2]) for f in rows if len(f) > 2 and f[0] == "PRO" and f[1] == WEB_KEY
                                          and f[2].isdigit()), None)
            except (OSError, asyncio.TimeoutError, CoreError):
                pass
        host = urlsplit(self.url).netloc if self.url else req["headers"].get("host", "").strip().lower()
        if not self.web_port or len(host) > 260 or not HOST_RE.match(host):
            return ""
        name = host if host.endswith("]") else host.rsplit(":", 1)[0] if ":" in host else host
        return "http://%s:%d/" % (name, self.web_port)

    async def index(self, req, data):
        """index.html with the classic pages' address in its meta tag and its links."""
        base = await self.classic(req)
        text = data.decode("utf-8").replace(CLASSIC_META, '<meta name="pn-classic" content="%s">' % base)
        text = text.replace(PUSH_META, '<meta name="pn-push" content="%s">' % self.push.public)
        if base:
            for page in CLASSIC_LINKS:
                text = text.replace('href="/%s"' % page, 'href="%s%s"' % (base, page))
        return text.encode("utf-8")

    # ---- requests ----------------------------------------------------------------------------------
    async def route(self, req):
        path = req["path"]
        if path in ("/", "/play"):
            return respond(302, "/play/", extra="Location: %s\r\n" % (self.url or "/play/"))
        if path == "/play/palette.json":
            return respond(200, PAL.client_json(), "application/json", extra="Cache-Control: no-cache\r\n" + HEADERS)
        if path == "/play/rummytile-colours.json":
            return respond(200, RTPAL.client_json(), "application/json", extra="Cache-Control: no-cache\r\n" + HEADERS)
        if path == "/play/session":
            return self.session(req)
        if path == "/play/push":
            return await self.push_request(req)
        if path == wsbridge.WS_PATH:
            return respond(426, "This is a WebSocket.", extra="Upgrade: websocket\r\n")
        if req["method"] not in ("GET", "HEAD"):
            return respond(405, "GET only", extra="Allow: GET, HEAD\r\n")
        got = play_file(path)
        if got:
            body = await self.index(req, got[0]) if path in ("/play/", "/play/index.html") else got[0]
            return respond(200, body, got[1], extra="Cache-Control: no-cache\r\n" + HEADERS)
        return respond(404, "Not found.", extra=HEADERS)

    def session(self, req):
        """POST /play/session: h= (the one-time handle the WebSocket gave after a sign-in) sets this
        extension's session cookie, so the client resumes after a reload (SES alone, wsbridge.py); out=1
        clears it. A request whose Origin is not this site's is refused. Text answers: ok, or no."""
        if req["method"] != "POST":
            return respond(405, "POST only", extra="Allow: POST\r\n")
        origin = req["headers"].get("origin", "").strip().lower()
        if origin and not self.origin_ok(origin, req["headers"].get("host", "").strip().lower(), req["proto"]):
            return respond(403, "no", extra="Cache-Control: no-store\r\n")
        form = req["form"]
        if (form.get("out") or [""])[0] == "1":
            return respond(200, "ok", extra=self.cookie(req, None) + "Cache-Control: no-store\r\n")
        sid = self.bridge.take((form.get("h") or [""])[0])
        if not sid:
            return respond(403, "no", extra="Cache-Control: no-store\r\n")
        self.note_resumed(sid)
        return respond(200, "ok", extra=self.cookie(req, sid) + "Cache-Control: no-store\r\n")

    async def push_request(self, req):
        """POST /play/push (playpush.Push.request), from this site's pages only: a JSON answer."""
        if req["method"] != "POST":
            return respond(405, "POST only", extra="Allow: POST\r\n")
        origin = req["headers"].get("origin", "").strip().lower()
        if not origin or not self.origin_ok(origin, req["headers"].get("host", "").strip().lower(), req["proto"]):
            return respond(403, '{"error": "no"}', "application/json", extra="Cache-Control: no-store\r\n")
        try:
            status, answer = await self.push.request(req, req["form"])
        except (OSError, asyncio.TimeoutError, CoreError):
            status, answer = 503, {"error": "The game server isn't answering."}
        return respond(status, json.dumps(answer), "application/json", extra="Cache-Control: no-store\r\n")

    async def handle(self, reader, writer):
        if self.active >= MAX_REQUESTS:
            writer.write(respond(503, "Busy, try again."))
            writer.close()
            return
        self.active += 1
        mine = []           # the client address this request counts against, once it is known
        held = [True]

        def claim(addr):
            if self.active_by.get(addr, 0) >= REQUESTS_PER_ADDR:
                return False
            self.active_by[addr] = self.active_by.get(addr, 0) + 1
            mine.append(addr)
            return True

        def release():
            if held[0]:
                held[0] = False
                self.active -= 1
                for addr in mine:
                    self.active_by[addr] -= 1
                    if not self.active_by[addr]:
                        del self.active_by[addr]
        try:
            await self.serve_request(reader, writer, claim, release)
        finally:
            release()

    async def serve_request(self, reader, writer, claim, release):
        """One request, or a WebSocket handshake and the WebSocket after it."""
        try:
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_SECS)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    ConnectionError):
                return
            head = parse_head(raw)
            if head is None:
                writer.write(respond(400, "That request is not well formed."))
                return
            method, target, version, headers = head
            client, proto = self.client(peer_addr(writer) or "?", headers)
            src = addr_key(client)
            if not claim(src):
                writer.write(respond(503, "Busy, try again."))
                return
            req = {"method": method, "path": urlsplit(target).path or "/", "headers": headers, "form": {},
                   "peer": client, "src": src, "proto": proto, "sid": self.cookie_sid(headers)}
            if wsbridge.is_upgrade(headers):
                try:
                    out = wsbridge.handshake(method, version, req["path"], headers,
                                             lambda origin, host: self.origin_ok(origin, host, proto))
                except wsbridge.Refuse as e:
                    writer.write(respond(e.status, e.text, extra=e.extra))
                    return
                writer.write(out)
                await writer.drain()
                release()           # an open WebSocket is not a request in progress: the bridge counts it
                keepalive(writer)
                await self.bridge.serve(reader, writer, req)
                return
            if method == "POST":
                try:
                    n = int(headers.get("content-length", "0"))
                except ValueError:
                    n = -1
                if not 0 <= n <= (MAX_PUSH_BODY if req["path"] == "/play/push" else MAX_BODY):
                    writer.write(respond(400, "That request is too long."))
                    return
                body = await asyncio.wait_for(reader.readexactly(n), HEAD_SECS) if n else b""
                req["form"] = parse_qs(body.decode("latin-1"), max_num_fields=6)
            elif method not in ("GET", "HEAD"):
                writer.write(respond(405, "GET or POST only.", extra="Allow: GET, HEAD, POST\r\n"))
                return
            out = await self.route(req)
            if method == "HEAD":
                out = out[:out.index(b"\r\n\r\n") + 4]
            writer.write(out)
            await writer.drain()
        except Exception as e:      # the type and place only: a request may hold a session id or a credential
            tb = e.__traceback__
            while tb.tb_next:
                tb = tb.tb_next
            print("play error: %s at line %d" % (type(e).__name__, tb.tb_lineno), file=sys.stderr, flush=True)
        finally:
            try:
                writer.close()
            except Exception:
                pass


async def serve(args):
    p = Play(args.core_host, args.core_port, args.key, args.url, args.proxy_nets, args.data, args.push_contact,
             playpush.host_list(args.push_hosts))
    server = await asyncio.start_server(p.handle, args.host, args.port, limit=MAX_HEAD)
    declare.start(args, PROTOCOL, p.key_file)
    print("play adapter on %s:%d -> core %s:%d%s" % (args.host, args.port, args.core_host, args.core_port,
                                                       ", public at %s" % p.url if p.url else ""), flush=True)
    p.push.start()
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.playserver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6513)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this extension's key file (python -m parlord.frontends)")
    declare.add_args(ap)
    ap.add_argument("--public-url", default=os.environ.get("PARLOR_PLAY_URL", ""),
                    help="play.public_url: the address players open, behind a TLS proxy "
                         "(https://parlornet.org/play/); its port is the one declared to the core")
    ap.add_argument("--proxies", default=os.environ.get("PARLOR_PLAY_PROXIES", ""),
                    help="play.proxies: comma-separated addresses or networks of the reverse proxies whose "
                         "X-Forwarded-For and X-Forwarded-Proto are believed")
    ap.add_argument("--data", default=os.environ.get("PARLOR_PLAY_DATA", ""),
                    help="this extension's own directory: push subscriptions and the VAPID key")
    ap.add_argument("--push-contact", default=os.environ.get("PARLOR_PLAY_PUSH_CONTACT", ""),
                    help="play.push_contact: the operator's mailto: or https: address, given to push services "
                         "(VAPID sub); push notifications are off without it")
    ap.add_argument("--push-hosts", default="",
                    help="push services to send to besides the browsers' own: comma-separated host[:port] "
                         "(a loopback address may be plain http, for a local push service)")
    a_ = ap.parse_args(argv)
    if a_.key:
        CoreClient.key_file = a_.key
    # A setting that does not parse is left out, said in the log: the extension still runs (one that stopped
    # would stop the container, deploy/start.py) as if it were unset: no public URL, or no trusted proxy.
    a_.url, a_.proxy_nets = "", []
    try:
        a_.url = public_url(a_.public_url)[0]
    except ValueError as e:
        print("play: %s; not used" % e, file=sys.stderr, flush=True)
    try:
        a_.proxy_nets = proxy_list(a_.proxies)
    except ValueError as e:
        print("play: %s; no proxy is trusted" % e, file=sys.stderr, flush=True)
    if a_.url and not a_.public_port:
        a_.public_port = url_port(a_.url)
    a_.core_host, _, p = a_.core.rpartition(":")
    a_.core_port = int(p)
    return a_


if __name__ == "__main__":
    from adapters import playserver as _play
    try:
        asyncio.run(_play.serve(_play.parse_args()))
    except KeyboardInterrupt:
        pass
