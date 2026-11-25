"""Re-test of R6: the Mailer against fake relays (local only).
 a) self-signed certificate for another name        -> must refuse
 b) a CA the operator trusts (email.smtp_ca_file), certificate for another name -> must refuse
 c) same CA, certificate for 127.0.0.1               -> must send (control)
 d) relay that offers no STARTTLS                    -> must refuse, no AUTH in clear
python3.13 r6b_smtp_retest.py"""
import asyncio
import os
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parlord import auth  # noqa: E402
from r6_smtp_mitm import FakeRelay  # noqa: E402


def sh(*a):
    subprocess.run(a, check=True, capture_output=True)


def certs(d):
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", d + "/self.key", "-out", d + "/self.pem",
       "-days", "1", "-subj", "/CN=attacker.invalid")
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", d + "/ca.key", "-out", d + "/ca.pem",
       "-days", "1", "-subj", "/CN=Test CA", "-addext", "basicConstraints=critical,CA:TRUE",
       "-addext", "keyUsage=critical,keyCertSign")
    for name, san in (("other", "DNS:other.invalid"), ("ip", "IP:127.0.0.1")):
        sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "%s/%s.key" % (d, name), "-out",
           "%s/%s.csr" % (d, name), "-subj", "/CN=%s" % name)
        with open("%s/%s.ext" % (d, name), "w") as f:
            f.write("subjectAltName=%s\nauthorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n" % san)
        sh("openssl", "x509", "-req", "-in", "%s/%s.csr" % (d, name), "-CA", d + "/ca.pem", "-CAkey", d + "/ca.key",
           "-CAcreateserial", "-out", "%s/%s.pem" % (d, name), "-days", "1", "-extfile", "%s/%s.ext" % (d, name))


class NoTLS(FakeRelay):
    async def handle(self, r, w):
        w.write(b"220 relay\r\n")
        while True:
            l = (await r.readline()).decode().rstrip("\r\n")
            if not l:
                return
            if l.upper().startswith("EHLO"):
                w.write(b"250-relay\r\n250 AUTH PLAIN LOGIN\r\n")
            elif l.upper().startswith("AUTH"):
                self.creds.append(l)
                w.write(b"235 ok\r\n")
            elif l.upper().startswith("QUIT"):
                w.write(b"221 bye\r\n")
                return
            else:
                w.write(b"250 ok\r\n")


def run(relay, extra=None):
    loop = asyncio.new_event_loop()
    srv = loop.run_until_complete(asyncio.start_server(relay.handle, "127.0.0.1", 0))
    port = srv.sockets[0].getsockname()[1]
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    s = {"email.transport": "smtp", "email.smtp_host": "127.0.0.1", "email.smtp_port": str(port),
         "email.smtp_user": "u", "email.smtp_password": "relay-password", "email.from": "noreply@x.example"}
    s.update(extra or {})
    m = auth.Mailer(s)
    ok = m.send("alice@example.org", "s", "secret body")
    loop.call_soon_threadsafe(loop.stop)
    return ok, m.error[:70], relay.creds, len(relay.mails)


d = tempfile.mkdtemp()
certs(d)
print("a) self-signed, other name:     ", run(FakeRelay(d + "/self.pem", d + "/self.key")))
print("b) trusted CA, other name:      ", run(FakeRelay(d + "/other.pem", d + "/other.key"), {"email.smtp_ca_file": d + "/ca.pem"}))
print("c) trusted CA, right name:      ", run(FakeRelay(d + "/ip.pem", d + "/ip.key"), {"email.smtp_ca_file": d + "/ca.pem"}))
print("d) no STARTTLS offered:         ", run(NoTLS(d + "/self.pem", d + "/self.key")))
