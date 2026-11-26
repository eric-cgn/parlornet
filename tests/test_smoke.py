"""The fast set's end-to-end smoke test (tests/speed.py): the core with password sign-in and the bot, as
processes. A player signs up and signs in over the native protocol, opens a Reversi table on the 4 by 4
board, invites the bot and plays to the end. The slow tests play every game through every extension."""
import os
import socket
import subprocess
import time
import unittest

import procs
from test_core import ROOT, free_port, make_keys, stack_args

WAIT = 30
PASSWORD = "Smoke-test-9"


class Conn:
    """A native-protocol connection."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=WAIT)
        self.s.sendall(b"PARLORNET/1\r\n")       # the opening first, then the greeting
        self.f = self.s.makefile("rb")
        self.greeting = self.f.readline().decode("latin-1").rstrip("\r\n")

    def req(self, text):
        self.s.sendall(text.encode() + b"\r\n")
        out = []
        while True:
            l = self.f.readline().decode("latin-1").rstrip("\r\n")
            if not l:
                raise EOFError("connection closed after %r" % text)
            out.append(l)
            if l.startswith(("OK", "ERR", "ASK")):
                return out

    def close(self):
        for x in (self.f, self.s):
            try:
                x.close()
            except OSError:
                pass


def until(pred, what, timeout=WAIT, step=0.05):
    end = time.time() + timeout
    while True:
        r = pred()
        if r:
            return r
        if time.time() > end:
            raise AssertionError("timed out waiting for %s" % what)
        time.sleep(step)


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.fe = free_port(), free_port()
        cls.keys = make_keys("bot")
        cls.procs = procs.owned(cls)
        cls.spawn(*stack_args(cls.core, cls.fe, cls.keys), "--auth", "password")
        cls.spawn("bot", "--core", "127.0.0.1:%d" % cls.fe, "--key", os.path.join(cls.keys, "bot.key"),
                  "--think", "0,0", "--pace", "0", "--poll", "0.2")

    @classmethod
    def spawn(cls, *args):
        p = procs.spawn(*args, stderr=subprocess.STDOUT)    # after its first line: listening, or signed in
        cls.procs.append(p)
        return p

    @classmethod
    def tearDownClass(cls):
        procs.stop_all(cls.procs)

    def test_sign_up_sign_in_and_a_game_against_the_bot(self):
        a = Conn(self.core)
        self.assertTrue(a.greeting.startswith("PARLORNET/1 "), a.greeting)
        self.assertIn("AUTH=password", a.greeting)
        self.assertEqual(a.req("HELO smoky")[-1], "ASK password Choose_a_password secret cred new")
        self.assertEqual(a.req("AUT password " + PASSWORD)[-1], "ASK confirm Type_it_again secret cred new")
        self.assertEqual(a.req("AUT confirm " + PASSWORD)[-1], "OK -")
        a.close()
        c = Conn(self.core)                             # signs in again with the password chosen
        self.assertEqual(c.req("HELO smoky")[-1], "ASK password Password secret cred")
        self.assertEqual(c.req("AUT password " + PASSWORD)[-1], "OK -")
        try:
            final = c.req("NEW reversi standard size=4 turn=1h")[-1]
            self.assertTrue(final.startswith("OK "), final)
            tid = final.split()[1]
            self.assertEqual(c.req("JON %s 0" % tid)[-1], "OK 0")
            self.assertEqual(c.req("INV %s bot" % tid)[-1], "OK")
            until(lambda: any(l.startswith("TAB %s " % tid) and " playing smoky,bot" in l.lower()
                              for l in c.req("LST")), "the bot to sit down")

            def ended():
                return [l for l in c.req("SIN %s 0" % tid) if l.startswith("EVT ") and " END " in l]

            def my_turn_or_over():
                v = c.req("VUE %s M" % tid)
                moves = [l[2:] for l in v if l.startswith("M ")]
                return moves or ended()

            for _ in range(40):                         # a 4 by 4 board has 12 moves at most
                got = until(my_turn_or_over, "a move or the end")
                if isinstance(got[0], str) and got[0].startswith("EVT "):
                    break
                r = c.req("MOV %s %s" % (tid, got[0]))
                self.assertTrue(r[-1].startswith("OK"), r)
            end = until(ended, "the end")
            self.assertRegex(end[0], r" END (win [01] \d+-\d+|draw \d+-\d+)")
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
