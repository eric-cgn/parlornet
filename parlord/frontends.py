"""The frontend channel (docs/security-design.md): TLS 1.3 with one pre-shared
key per frontend, from the Python standard library (3.13+).

Each frontend has a key file <keys>/<name>.key (mode 600) holding one line

    parlornet-frontend-key 1 <name> <role> <base64url 32-byte key>

where role is "frontend" (web, telnet, IOS, ...) or "admin" (the admin
adapter). The core accepts a connection on a listener only if the frontend
proves its key in the TLS handshake and its role is the listener's role.

    python -m parlord.frontends --keys DIR add NAME [NAME...] [--role admin]
    python -m parlord.frontends --keys DIR rotate NAME
    python -m parlord.frontends --keys DIR remove NAME
    python -m parlord.frontends --keys DIR list

Keys are never printed; copy the file to a remote frontend's host over SSH.

In the container's per-user mode (an opt-in; deploy/start.py) each server
extension runs under its own uid: where a group named pn-<name> exists and the
key directory is the per-user layout (mode 711), a key file is made readable by
that group as well as its owner (mode 640), so each extension can read its own
key and no other, while the core reads them all. Otherwise keys are mode 600.
"""
import argparse
import asyncio
import base64
import hashlib
import os
import re
import secrets
import ssl
import sys

MAGIC = "parlornet-frontend-key"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
GROUP_PREFIX = "pn-"
ROLES = ("frontend", "admin")
HANDSHAKE_SECS = 3          # an unauthenticated socket gets this long to finish the TLS handshake


def supported():
    return hasattr(ssl.SSLContext, "set_psk_server_callback")


def parse_key(text):
    """(name, role, key bytes) from a key file's text; ValueError if malformed."""
    f = text.split()
    if len(f) != 5 or f[0] != MAGIC or f[1] != "1" or not NAME_RE.match(f[2]) or f[3] not in ROLES:
        raise ValueError("not a frontend key file")
    key = base64.urlsafe_b64decode(f[4] + "=" * (-len(f[4]) % 4))
    if len(key) != 32:
        raise ValueError("frontend keys are 32 bytes")
    return f[2], f[3], key


def read_key(path):
    with open(path, encoding="ascii") as fh:
        return parse_key(fh.read())


def key_group(name):
    """The gid of the group pn-<name> (the server extension's own, in the
    container), or None where there is no such group."""
    try:
        import grp
        return grp.getgrnam(GROUP_PREFIX + name).gr_gid
    except (ImportError, KeyError):
        return None


def share_key(path, name, keys):
    """Let the server extension's own group read its key file: owner (the
    core's user: the key directory's owner when run as root) read-write, the
    group pn-<name> read, nobody else. Nothing changes where there is no such
    group (outside the container)."""
    gid = key_group(name)
    if gid is None or not os.stat(keys).st_mode & 0o011:
        return          # no such group, or the one-user layout (key directory 700): owner only
    uid = os.stat(keys).st_uid if hasattr(os, "geteuid") and os.geteuid() == 0 else -1
    try:
        os.chown(path, uid, gid)
        os.chmod(path, 0o640)
    except OSError as e:
        print("%s: cannot give the group %s%s read access (%s); the server extension may not read its key"
              % (path, GROUP_PREFIX, name, e.strerror), file=sys.stderr)


def write_key(keys, name, role, replace=False):
    """Create (or with replace, rotate) <keys>/<name>.key; returns its path."""
    if not NAME_RE.match(name):
        raise ValueError("frontend names are 1 to 32 of a-z, 0-9 and -")
    if role not in ROLES:
        raise ValueError("role is frontend or admin")
    os.makedirs(keys, mode=0o700, exist_ok=True)
    path = os.path.join(keys, name + ".key")
    line = "%s 1 %s %s %s\n" % (MAGIC, name, role,
                                base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))
    tmp = path + ".new"
    if os.path.exists(tmp):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(line)
    share_key(tmp, name, keys)
    if replace:
        os.replace(tmp, path)
    else:
        try:
            os.link(tmp, path)          # fails if it exists: never overwrite a key by accident
        finally:
            os.unlink(tmp)
    return path


class Keyring:
    """The enrolled frontends, read from the key directory when it changes."""

    def __init__(self, keys):
        self.dir = keys
        self.stamp = None
        self.keys = {}          # name -> (role, key)

    def load(self):
        try:
            st = os.stat(self.dir)
            names = sorted(f for f in os.listdir(self.dir) if f.endswith(".key"))
            stamp = (st.st_mtime_ns, tuple((f, os.stat(os.path.join(self.dir, f)).st_mtime_ns) for f in names))
        except OSError:
            self.keys, self.stamp = {}, None
            return self.keys
        if stamp != self.stamp:
            keys = {}
            for f in names:
                try:
                    name, role, key = read_key(os.path.join(self.dir, f))
                except (OSError, ValueError):
                    continue
                if f == name + ".key":
                    keys[name] = (role, key)
            self.keys, self.stamp = keys, stamp
        return self.keys

    def fingerprint(self, name):
        k = self.keys.get(name)
        return hashlib.sha256(k[1]).hexdigest() if k else None


class Handshakes:
    """The TLS handshakes in progress on a listener, by client address (IPv6
    by its /48): at most per_source from one address and total in all. A new
    connection over either limit ends the oldest handshake of its own address,
    or of the address with the most, instead of being refused: a handshake
    with the key takes milliseconds, so the oldest ones are the idle sockets,
    and one address cannot hold the slots against the others."""

    def __init__(self, per_source=8, total=64):
        self.per_source, self.total = per_source, total
        self.by = {}            # address -> [writer, ...], oldest first

    def __len__(self):
        return sum(len(v) for v in self.by.values())

    @staticmethod
    def source(writer):
        from .accounts import source_key
        peer = writer.get_extra_info("peername")
        return (source_key(peer[0]) if peer else None) or "?"

    def _evict(self, src):
        w = self.by[src].pop(0)
        if not self.by[src]:
            del self.by[src]
        try:
            w.transport.abort()
        except Exception:
            pass

    def enter(self, writer):
        src = self.source(writer)
        if len(self.by.get(src, ())) >= self.per_source:
            self._evict(src)
        elif len(self) >= self.total:
            self._evict(max(self.by, key=lambda k: len(self.by[k])))
        self.by.setdefault(src, []).append(writer)
        return src

    def leave(self, src, writer):
        q = self.by.get(src)
        if q and writer in q:
            q.remove(writer)
            if not q:
                del self.by[src]


def server_context(keyring, role, rec):
    """A TLS 1.3 server context for one connection. No certificate: only a
    frontend that knows its key can complete the handshake. The identity the
    PSK callback accepted is written to rec["name"]."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3

    def psk(identity):
        entry = keyring.load().get(identity or "")
        if entry is None or entry[0] != role:
            return b""                  # unknown identity or wrong role: the handshake fails
        rec["name"] = identity
        rec["fingerprint"] = hashlib.sha256(entry[1]).hexdigest()
        return entry[1]
    ctx.set_psk_server_callback(psk, None)
    return ctx


async def accept(reader, writer, keyring, role):
    """Upgrade a fresh connection to TLS 1.3 with a frontend's PSK. Returns
    (name, fingerprint), or None (the connection is closed) if it fails."""
    rec = {}
    raw = writer.transport
    try:
        await asyncio.wait_for(writer.start_tls(server_context(keyring, role, rec)), HANDSHAKE_SECS)
        so = writer.get_extra_info("ssl_object")
        if so is None or so.version() != "TLSv1.3" or not so.session_reused or "name" not in rec:
            raise ssl.SSLError("not a PSK handshake")
        return rec["name"], rec["fingerprint"]
    except (OSError, ssl.SSLError, asyncio.TimeoutError, EOFError, AttributeError):
        # AttributeError: asyncio's start_tls on a transport closed meanwhile (dropped for a newcomer),
        # which leaves the writer without one
        if writer.transport is None:
            writer._transport = raw
        try:
            writer.transport.abort()
        except Exception:
            pass
        return None


def client_context(identity, key):
    """The frontend side: TLS 1.3 with the PSK; a certificate is never
    accepted (verification is on and no CA is loaded), so an impostor core
    without the key cannot complete the handshake."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_psk_client_callback(lambda hint: (identity, key))
    return ctx


def main(argv=None):
    ap = argparse.ArgumentParser(prog="parlord.frontends", description="Enroll, rotate and revoke frontend keys")
    ap.add_argument("--keys", required=True, help="the key directory the core uses (--keys of parlord)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="enroll frontends (an existing key is kept)")
    p.add_argument("names", nargs="+")
    p.add_argument("--role", default="frontend", choices=ROLES)
    p = sub.add_parser("rotate", help="give a frontend a new key (its connections are closed)")
    p.add_argument("name")
    p.add_argument("--role", default=None, choices=ROLES)
    p = sub.add_parser("remove", help="revoke a frontend (its connections are closed)")
    p.add_argument("name")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    for n in (a.names if a.cmd == "add" else [a.name] if a.cmd in ("rotate", "remove") else []):
        if not NAME_RE.match(n):            # never a path: ../x, a/b
            print("%s: not a server extension name (1 to 32 of a-z, 0-9 and -)" % n, file=sys.stderr)
            return 2
    path = lambda n: os.path.join(a.keys, n + ".key")  # noqa: E731
    if a.cmd == "add":
        for n in a.names:
            if os.path.exists(path(n)):
                print("%s: already enrolled (%s)" % (n, path(n)))
            else:
                print("%s: enrolled as %s, key in %s" % (n, a.role, write_key(a.keys, n, a.role)))
    elif a.cmd == "rotate":
        role = a.role or (read_key(path(a.name))[1] if os.path.exists(path(a.name)) else "frontend")
        print("%s: new key in %s" % (a.name, write_key(a.keys, a.name, role, replace=True)))
    elif a.cmd == "remove":
        if os.path.exists(path(a.name)):
            os.unlink(path(a.name))
            print("%s: removed" % a.name)
        else:
            print("%s: not enrolled" % a.name)
            return 1
    else:
        for n, (role, _) in sorted(Keyring(a.keys).load().items()):
            print("%s %s" % (n, role))
    return 0


if __name__ == "__main__":
    sys.exit(main())
