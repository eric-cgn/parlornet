"""The protocols list (docs/protocol.md, Protocols): server extensions declare their protocol (PRO, PRD) on the
extension channel, and the core lists what is connected (PRL) with its own PARLORNET/1 and the docs site's
address. The web pages show no list of protocols or ports: /protocols.htm has moved to the docs site's
protocols reference.

    python3 -m unittest discover -s tests -p "test_protocols_list.py"
"""
import asyncio
import os
import re
import sys
import time
import unittest

from test_core import ROOT, adapter_args, free_port, make_keys
import procs
from test_adapters import Stack, spawn

sys.path.insert(0, ROOT)
from adapters import declare  # noqa: E402
from adapters.common import CoreClient  # noqa: E402
from parlord import DOCS_URL, frontends  # noqa: E402
from parlord.core import Server  # noqa: E402
from speed import slow  # noqa: E402


class Declarations(unittest.IsolatedAsyncioTestCase):
    """The core's side, in process: an extension channel and the public port."""

    async def asyncSetUp(self):
        self.keys = make_keys("ios", "web")
        self.srv = Server(os.path.join(ROOT, "plugins"), "test", ":memory:", "none")
        self.srv.keyring = frontends.Keyring(self.keys)
        self.fe = await asyncio.start_server(self.srv.handle_frontend, "127.0.0.1", 0)
        self.pub = await asyncio.start_server(self.srv.handle, "127.0.0.1", 0)
        self.srv.public_port = self.pub.sockets[0].getsockname()[1]
        self.held = []

    async def asyncTearDown(self):
        for c in self.held:
            c.close()
        self.fe.close()
        self.pub.close()

    async def ext(self, name="ios"):
        c = await CoreClient.connect("127.0.0.1", self.fe.sockets[0].getsockname()[1],
                                     os.path.join(self.keys, name + ".key"))
        self.held.append(c)
        return c

    async def public(self, *lines):
        """Requests on the public port -> every reply line."""
        r, w = await asyncio.open_connection("127.0.0.1", self.srv.public_port)
        w.write(b"PARLORNET/1\r\n")
        await r.readline()          # the greeting
        out = []
        for l in lines:
            w.write(l.encode() + b"\r\n")
            while True:
                got = (await r.readline()).decode().rstrip("\r\n")
                out.append(got)
                if got.startswith(("OK", "ERR")):
                    break
        w.close()
        return out

    async def prl(self):
        data, final = await (await self.ext("web")).req("PRL")
        self.assertEqual(final, "OK")
        return data

    async def test_declare_list_and_leave(self):
        port = self.srv.public_port
        self.assertEqual(await self.prl(), ["DOC " + DOCS_URL, "PRO PARLORNET/1 %d ParlorNet protocol (PARLORNET/1)" % port,
                                            "PRD PARLORNET/1 ParlorNet's own protocol, for programs written for it"])
        ios = await self.ext("ios")
        self.assertEqual((await ios.req("PRO IOS 6503 Internet Othello Server (IOS)"))[1], "OK")
        self.assertEqual((await ios.req("PRD IOS Reversi for clients of the Internet Othello Server"))[1], "OK")
        web = await self.ext("web")
        self.assertEqual((await web.req("PRO HTTP 6500 HTTP and HTML 3.2"))[1], "OK")
        got = await self.prl()
        keys = [l.split()[1] for l in got if l.startswith("PRO ")]
        ports = [int(l.split()[2]) for l in got if l.startswith("PRO ")]
        self.assertEqual(ports, sorted(ports))                   # by port
        self.assertEqual(set(keys), {"HTTP", "PARLORNET/1", "IOS"})
        self.assertIn("PRD IOS Reversi for clients of the Internet Othello Server", got)
        self.assertFalse([l for l in got if l.startswith("PRD HTTP")])     # no description given: none listed
        # the same list on the public port, before signing in
        self.assertEqual((await self.public("PRL"))[:-1], got)
        # declared again: the port and name change, the description stays
        await ios.req("PRO IOS 6603 IOS again")
        got = await self.prl()
        self.assertIn("PRO IOS 6603 IOS again", got)
        self.assertIn("PRD IOS Reversi for clients of the Internet Othello Server", got)
        # the connection that declared it closes: it is gone
        ios.close()
        for _ in range(50):
            if not any(" IOS " in l for l in await self.prl()):
                break
            await asyncio.sleep(0.05)
        self.assertFalse([l for l in await self.prl() if " IOS " in l])
        # two connections of the same key and port: listed once
        a, b = await self.ext("web"), await self.ext("web")
        await a.req("PRO TELNET 6502 Telnet")
        await b.req("PRO TELNET 6502 Telnet")
        await b.req("PRD TELNET Full-screen menus")
        got = await self.prl()
        self.assertEqual([l for l in got if l.startswith("PRO TELNET")], ["PRO TELNET 6502 Telnet"])
        self.assertIn("PRD TELNET Full-screen menus", got)

    async def test_refusals(self):
        c = await self.ext()
        for line in ("PRO", "PRO IOS", "PRO IOS 6503", "PRO ios 6503 lower case", "PRO IOS 0 zero",
                     "PRO IOS 70000 too big", "PRO IOS x name", "PRO 1OS 6503 digit first",
                     "PRO ABCDEFGHIJKLM 1 thirteen", "PRO IOS 6503 " + "n" * 49, "PRD IOS " + "d" * 57):
            self.assertTrue((await c.req(line))[1].startswith("ERR SYNTAX"), line)
        self.assertTrue((await c.req("PRD NOPE text"))[1].startswith("ERR NOPROTO"))
        for k in ("A", "B", "C", "D"):
            self.assertEqual((await c.req("PRO %s 1 x" % k))[1], "OK")
        self.assertTrue((await c.req("PRO E 1 x"))[1].startswith("ERR LIMIT"))
        self.assertEqual((await c.req("PRO A 2 again"))[1], "OK")         # replacing one is not a new one
        out = await self.public("PRO IOS 6503 x", "PRD IOS x")
        self.assertTrue(out[0].startswith("ERR NOTFRONTEND"), out)
        self.assertTrue(out[1].startswith("ERR NOTFRONTEND"), out)

    async def test_docs_url_setting(self):
        """site.docs_url: the setting, cleared with "" (no DOC line), always ending with /, and never an address
        that is not one."""
        self.srv.set_setting("site.docs_url", "https://docs.example.org/parlor")
        self.assertEqual((await self.prl())[0], "DOC https://docs.example.org/parlor/")
        self.srv.set_setting("site.docs_url", "")
        self.assertFalse([l for l in await self.prl() if l.startswith("DOC")])
        for bad in ("not an address", "ftp:/x", "https://" + "x" * 80):
            self.srv.set_setting("site.docs_url", bad)
            self.assertFalse([l for l in await self.prl() if l.startswith("DOC")], bad)
        self.srv.set_setting("site.docs_url", None)
        self.assertEqual((await self.prl())[0], "DOC " + DOCS_URL)

    async def test_keep_declared(self):
        """The extension's side (adapters/declare.py): it declares, and the core lists it while it runs."""
        task = asyncio.ensure_future(declare.keep_declared(
            "127.0.0.1", self.fe.sockets[0].getsockname()[1], [("FIBS", 6505, "FIBS", "Backgammon")],
            os.path.join(self.keys, "ios.key"), every=0.2, retry=0.1))
        try:
            for _ in range(100):
                if "PRD FIBS Backgammon" in await self.prl():
                    break
                await asyncio.sleep(0.05)
            self.assertIn("PRO FIBS 6505 FIBS", await self.prl())
            self.assertIn("PRD FIBS Backgammon", await self.prl())
            await asyncio.sleep(0.5)                                   # declared again: still one
            self.assertEqual(len([l for l in await self.prl() if l.startswith("PRO FIBS")]), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(50):
            if not any("FIBS" in l for l in await self.prl()):
                break
            await asyncio.sleep(0.05)
        self.assertFalse([l for l in await self.prl() if "FIBS" in l])
        self.assertEqual(declare.requests([("IOS", 6503, "N", "")]), ["PRO IOS 6503 N"])

    async def test_an_older_core_ends_the_declaring(self):
        """A core that answers PRO with ERR UNKNOWN (older than the declarations) is left alone."""
        async def old_core(reader, writer):
            writer.write(b"PARLORNET/1 old AUTH=none SIGNUP=none\r\n")
            while True:
                line = await reader.readline()
                if not line:
                    break
                writer.write(b"ERR UNKNOWN no such request\r\n")

        class Plain(CoreClient):
            @classmethod
            async def connect(cls, host, port, key_file=None):
                r, w = await asyncio.open_connection(host, port)
                c = cls(r, w)
                c.greeting = await c.readline()
                return c
        srv = await asyncio.start_server(old_core, "127.0.0.1", 0)
        real = declare.CoreClient
        declare.CoreClient = Plain
        try:
            await asyncio.wait_for(declare.keep_declared("127.0.0.1", srv.sockets[0].getsockname()[1],
                                                         [("X", 1, "x", "")], retry=0.05), 5)
        finally:
            declare.CoreClient = real
            srv.close()


@slow
class LiveList(Stack):
    """The running stack: the web and telnet extensions declare themselves and the core lists them (PRL) on its
    public port; a stopped extension leaves the list. The web pages show none of it."""

    def prl(self):
        """PRL on the core's public port, before signing in: (docs url, {key: (port, name, what)})."""
        import socket
        from adapters import web
        s = socket.create_connection(("127.0.0.1", self.core), timeout=5)
        try:
            s.sendall(b"PARLORNET/1\r\nPRL\r\n")
            f = s.makefile("rb")
            f.readline()                    # the greeting
            lines = []
            while True:
                l = f.readline().decode("ascii").rstrip("\r\n")
                if l.startswith(("OK", "ERR")) or not l:
                    break
                lines.append(l)
        finally:
            s.close()
        url, rows = web.parse_protocols(lines)
        return url, {k: (port, name, what) for k, port, name, what in rows}

    def protocols_page(self, want, absent=()):
        """The core's list once every key in want shows (and none in absent): (docs url, rows)."""
        deadline = time.time() + 20
        while True:
            url, rows = self.prl()
            if set(want) <= set(rows) and not set(absent) & set(rows) or time.time() > deadline:
                return url, rows
            time.sleep(0.3)

    def test_the_live_list(self):
        from adapters import telnet, web
        url, rows = self.protocols_page(("HTTP", "TELNET", "PARLORNET/1"))
        self.assertEqual(url, DOCS_URL)
        self.assertEqual(rows["HTTP"], (self.web_port, web.PROTOCOL[1], web.PROTOCOL[2]))
        self.assertEqual(rows["TELNET"], (self.tel_port, telnet.PROTOCOL[1], telnet.PROTOCOL[2]))
        self.assertEqual(rows["PARLORNET/1"][0], self.core)
        for key in ("IOS", "FIBS", "OUTLAND"):            # not connected: not listed
            self.assertNotIn(key, rows)
        ports = [r[0] for r in rows.values()]
        self.assertEqual(ports, sorted(ports))
        # the web pages list none of it: the old Protocols page is the docs site's reference now
        st, loc, _, _ = self.get("/protocols.htm")
        self.assertEqual((st, loc), (301, DOCS_URL + "protocols.html"))
        welcome = re.sub(r"<[^>]+>", "", self.get("/welcome.htm")[3])
        for key, (port, _, _) in rows.items():
            self.assertNotIn(str(port), welcome, key)

    def test_an_extension_that_stops_leaves_the_list(self):
        port = free_port()
        keys = make_keys("ios")
        for f in os.listdir(keys):              # enroll it with the stack's core
            os.replace(os.path.join(keys, f), os.path.join(self.keys, f))
        ios = spawn(*adapter_args("adapters.ios", port, self.fe, self.keys, "ios"), "--public-port", "7503")
        try:
            _, rows = self.protocols_page(("IOS",))
            self.assertEqual(rows["IOS"][0], 7503)            # the port players connect to
        finally:
            procs.stop(ios)
        _, rows = self.protocols_page((), ("IOS",))
        self.assertNotIn("IOS", rows)


if __name__ == "__main__":
    unittest.main()


class DeclaredLengths(unittest.TestCase):
    def test_every_extension_declaration_fits_the_core(self):
        """Each server extension's PROTOCOL (key, name, description) fits the core's PRO/PRD limits, so the
        core never refuses a declaration."""
        import importlib
        from adapters import catalog
        from parlord import core
        for name, module, _, _ in catalog.ADAPTERS:
            proto = getattr(importlib.import_module(module), "PROTOCOL", None)
            if proto is None:
                continue
            self.assertLessEqual(len(proto[2]), core.PROTO_DESC_LEN, name)
