"""Shared helpers for the review reproductions: start parlord and adapters on
free local ports with a temporary data directory, seed players, speak the
native protocol. Local only (127.0.0.1)."""
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord import frontends  # noqa: E402
from parlord.accounts import Player  # noqa: E402
from parlord.store import Store  # noqa: E402
from parlord import auth  # noqa: E402

PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Stack:
    """parlord (+ optional adapters) in a fresh data directory."""

    def __init__(self, auth_m="password", signup=None, settings=None, adapters=(), admin=False):
        self.dir = tempfile.mkdtemp(prefix="pnreview")
        self.db = os.path.join(self.dir, "parlord.db")
        self.keys = os.path.join(self.dir, "frontends")
        self.core, self.fe = free_port(), free_port()
        self.auth, self.signup = auth_m, signup
        self.settings = settings or {}
        self.adapters = adapters
        self.admin = admin
        self.procs = []
        self.ports = {}
        self.out = {}

    def seed(self, name, password=None, email=None, fails=0, locked_until=0.0):
        st = Store(self.db)
        p = Player(name, cred=auth.hash_password(password) if password else "", email=email,
                   verified=1 if email else 0, fails=fails, locked_until=locked_until)
        st.save_player(p)
        st.db.close()

    def start(self):
        for n, _ in self.adapters:
            frontends.write_key(self.keys, n, "frontend")
        if self.admin:
            frontends.write_key(self.keys, "admin", "admin")
        args = [PY, "-m", "parlord", "--port", str(self.core), "--frontend-port", str(self.fe),
                "--keys", self.keys, "--db", self.db, "--auth", self.auth]
        if self.signup:
            args += ["--signup", self.signup]
        if self.settings:
            sf = os.path.join(self.dir, "settings.conf")
            with open(sf, "w") as f:
                for k, v in self.settings.items():
                    f.write("%s=%s\n" % (k, v))
            args += ["--settings-file", sf]
        if self.admin:
            self.admin_port = free_port()
            args += ["--admin-port", str(self.admin_port)]
        self.core_log = open(os.path.join(self.dir, "core.log"), "w+")
        p = subprocess.Popen(args, cwd=ROOT, stdout=self.core_log, stderr=subprocess.STDOUT)
        self.procs.append(p)
        self._wait_port(self.core)
        for name, module in self.adapters:
            port = free_port()
            self.ports[name] = port
            log = open(os.path.join(self.dir, name + ".log"), "w+")
            p = subprocess.Popen([PY, "-m", module, "--port", str(port), "--core", "127.0.0.1:%d" % self.fe,
                                  "--key", os.path.join(self.keys, name + ".key")], cwd=ROOT,
                                 stdout=log, stderr=subprocess.STDOUT)
            self.procs.append(p)
            self._wait_port(port)
        return self

    def core_output(self):
        self.core_log.flush()
        with open(self.core_log.name) as f:
            return f.read()

    @staticmethod
    def _wait_port(port, secs=15):
        end = time.time() + secs
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("port %d never opened" % port)

    def stop(self):
        for p in self.procs:
            p.kill()
            p.wait()

    def player_row(self, name):
        st = Store(self.db)
        r = st.db.execute("SELECT name, fails, locked_until, email, temp != '' FROM players WHERE key=?",
                          (name.lower(),)).fetchone()
        st.db.close()
        return r


def masked(text):
    """A request as shown: an AUT's value (a password) is never printed."""
    parts = text.split(" ", 2)
    return "%s %s ***" % (parts[0], parts[1]) if parts[0].upper() == "AUT" and len(parts) == 3 else text


class Client:
    """The native protocol on the public port (6501). Shown requests never show an AUT value."""

    def __init__(self, r, w):
        self.r, self.w = r, w

    @classmethod
    async def connect(cls, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        c = cls(r, w)
        c.greeting = await c.line()
        return c

    async def line(self):
        return (await asyncio.wait_for(self.r.readline(), 10)).decode().rstrip("\r\n")

    async def req(self, text, show=True):
        self.w.write((text + "\r\n").encode())
        out = []
        while True:
            l = await self.line()
            out.append(l)
            if l.startswith("OK") or l.startswith("ERR") or l.startswith("ASK"):
                if show:
                    print("> %s" % masked(text))
                    for x in out:
                        print("< %s" % x)
                return out

    def close(self):
        self.w.close()


async def http(port, raw, timeout=10):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(raw if isinstance(raw, bytes) else raw.encode("latin-1"))
    await w.drain()
    data = await asyncio.wait_for(r.read(), timeout)
    w.close()
    return data.decode("latin-1")


def post(path, body, cookie=None):
    h = "POST %s HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/x-www-form-urlencoded\r\n" % path
    if cookie:
        h += "Cookie: %s\r\n" % cookie
    return h + "Content-Length: %d\r\n\r\n%s" % (len(body), body)
