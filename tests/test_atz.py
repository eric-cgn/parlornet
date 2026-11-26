"""tools/ATZ (docs/serial.md): the small Hayes modem for a serial line logged in to a Unix shell. It runs
on a pseudo-terminal, as a period program would find it, and dials a parlor on this machine; and as ATZ,
run by a shell when a program resets its modem. Skipped where there is no pty or sh."""
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procs  # noqa: E402
from test_core import free_port  # noqa: E402
from speed import slow  # noqa: E402

ATZ = os.path.join(ROOT, "tools", "ATZ")


@unittest.skipUnless(os.name == "posix" and hasattr(os, "forkpty") and shutil.which("python3")
                     and os.path.exists("/bin/sh"), "needs a pty, /bin/sh and python3")
@slow
class Atz(unittest.TestCase):
    def setUp(self):
        self.port, closed = free_port(), free_port()
        self.parlor = procs.spawn("parlord", "--host", "127.0.0.1", "--port", self.port, "--name", "test",
                                  "--auth", "none", "--db", ":memory:", "--frontend-port", "0", "--admin-port", "0",
                                  stderr=subprocess.DEVNULL)
        self.addCleanup(procs.stop, self.parlor)
        self.closed = closed
        self.tmp = tempfile.TemporaryDirectory()
        self.pid = None

    def start(self, argv, env=None):
        """argv run on a pty, the shell side of the line, at 9600 bit/s."""
        import termios
        pid, fd = os.forkpty()
        if pid == 0:
            a = termios.tcgetattr(0)
            a[4] = a[5] = termios.B9600
            termios.tcsetattr(0, termios.TCSANOW, a)
            os.execve(argv[0], argv, env or os.environ)
        self.pid, self.fd, self.status = pid, fd, None

    def shell(self):
        """An interactive shell on the line, as a serial login gives, with the tmp directory on its PATH."""
        env = {"PATH": self.tmp.name + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), "PS1": "$ ",
               "HOME": self.tmp.name, "TERM": "dumb"}
        self.start(["/bin/sh", "-i"], env)
        self.say(b"", b"$ ")

    def tearDown(self):
        self.tmp.cleanup()
        if self.pid is not None:
            if self.status is None:
                os.kill(self.pid, 9)
                os.waitpid(self.pid, 0)
            os.close(self.fd)               # an ATZ the shell ran gets SIGHUP

    def say(self, data, want=None, secs=5.0):
        """Send data as the period program; what comes back until want has come and the line is quiet."""
        if data:
            os.write(self.fd, data)
        out, end, last = b"", time.monotonic() + secs, time.monotonic()
        while time.monotonic() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    d = os.read(self.fd, 4096)
                except OSError:
                    break
                out, last = out + d, time.monotonic()
            elif (want is None or want in out) and time.monotonic() - last > 0.3:
                break
        return out

    def test_commands_dials_and_hangups(self):
        self.start([ATZ, "127.0.0.1", str(self.closed)],
                   dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")))
        self.assertEqual(self.say(b"", b"OK\r\n"), b"\r\nOK\r\n")                # the answer to ATZ
        self.assertEqual(self.say(b"AT\r", b"OK\r\n"), b"AT\r\n\r\nOK\r\n")         # echoed, as a modem
        self.assertEqual(self.say(b"ATE0\r", b"OK\r\n"), b"ATE0\r\n\r\nOK\r\n")
        self.assertEqual(self.say(b"AT&F&C1&D2\r\n", b"OK\r\n"), b"\r\nOK\r\n")    # &D is no dial
        self.assertEqual(self.say(b"HELLO\r"), b"")
        # a number dials the host and port given to ATZ: nothing listens there
        self.assertEqual(self.say(b"ATDT5551212\r", b"NO CARRIER\r\n"), b"\r\nNO CARRIER\r\n")
        # host:port dials the parlor; every byte passes as it is
        self.assertEqual(self.say(b"ATDT 127.0.0.1:%d\r" % self.port, b"CONNECT"), b"\r\nCONNECT 9600\r\n")
        self.assertEqual(self.say(b"PARLORNET/1\r\n", b"\r\n"), b"PARLORNET/1 test AUTH=none SIGNUP=none\r\n")
        self.assertTrue(self.say(b"\x03\x11\x13\r\n", b"\r\n").startswith(b"ERR "))
        self.assertIn(b"GAM reversi/1", self.say(b"GAM\r\n", b"OK\r\n"))
        # the escape: a second's quiet, +++ and a second's quiet; the line stays up for ATO
        time.sleep(1.1)
        self.assertEqual(self.say(b"+++", b"OK\r\n", 4.0), b"\r\nOK\r\n")
        self.assertEqual(self.say(b"ATO\r", b"CONNECT"), b"\r\nCONNECT 9600\r\n")
        self.assertTrue(self.say(b"+++\r\n", b"\r\n").startswith(b"ERR "))   # +++ with no quiet is data
        self.assertIn(b"GAM reversi/1", self.say(b"GAM\r\n", b"OK\r\n"))    # the same session
        got = self.say(b"BYE\r\n", b"NO CARRIER\r\n", 2.0)
        self.assertTrue(got.endswith(b"OK\r\n\r\nNO CARRIER\r\n"), got)
        # ATH hangs up after the escape
        self.say(b"ATDT 127.0.0.1:%d\r" % self.port, b"CONNECT")
        self.say(b"PARLORNET/1\r\n", b"\r\n")
        time.sleep(1.1)
        self.assertEqual(self.say(b"+++", b"OK\r\n", 4.0), b"\r\nOK\r\n")
        self.assertEqual(self.say(b"ATH\r", b"OK\r\n"), b"\r\nOK\r\n")
        self.assertEqual(self.say(b"ATO\r", b"NO CARRIER\r\n"), b"\r\nNO CARRIER\r\n")  # no line to go back to
        self.assertEqual(self.say(b"ATZ\r", b"OK\r\n"), b"\r\nOK\r\n")             # commands again
        os.write(self.fd, b"\x03")              # Ctrl-C while it waits ends it
        end = time.monotonic() + 5
        while time.monotonic() < end and self.status is None:
            p, st = os.waitpid(self.pid, os.WNOHANG)
            if p:
                self.status = st
            self.say(b"", None, 0.05)
        self.assertIsNotNone(self.status)
        self.assertEqual(os.waitstatus_to_exitcode(self.status), 130)

    def dial_parlor(self):
        """After OK: the Apple II client's dial, CONNECT, and the parlor's greeting to its opening."""
        self.assertEqual(self.say(b"ATDT127.0.0.1:%d\r" % self.port, b"CONNECT"),
                         b"ATDT127.0.0.1:%d\r\n\r\nCONNECT 9600\r\n" % self.port)
        self.assertEqual(self.say(b"PARLORNET/1\r\n", b"\r\n"), b"PARLORNET/1 test AUTH=none SIGNUP=none\r\n")

    def test_from_the_shell(self):
        # installed on the PATH as ATZ: the shell echoes the program's ATZ and runs it, and it answers OK
        shutil.copy(ATZ, os.path.join(self.tmp.name, "ATZ"))
        self.shell()
        got = self.say(b"ATZ\r", b"OK\r\n")
        self.assertTrue(got.endswith(b"ATZ\r\n\r\nOK\r\n"), got)
        self.assertEqual(self.say(b"AT&F\r", b"OK\r\n"), b"AT&F\r\n\r\nOK\r\n")    # an init string
        self.dial_parlor()


if __name__ == "__main__":
    unittest.main()
