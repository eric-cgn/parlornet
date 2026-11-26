"""The child processes and ports of the tests.

Every process a test starts goes through start() or spawn(), which put it in a process group of its own.
stop() ends the whole group, so a core's plugin hosts end with the core, and waits for the process. owned()
gives a test class or a test a list of processes that is stopped when it is done, even when setUpClass or
setUp fails halfway. Whatever is still running when the test run ends is stopped at exit, and
test_zz_leftovers.py, which sorts last, fails the run when a test left anything behind.

free_port() hands out ports below the kernel's range of ephemeral ports, so no outgoing connection and no
bind to port 0 can take one between the choice and the server's start, and it never hands out the same port
twice in a run. spawn() reads a server's opening line and checks that it names the port the server was
given: a server that could not listen fails the test at once, saying so, instead of letting a client reach
whatever else holds the port.
"""
import atexit
import collections
import os
import random
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START_WAIT = 60         # seconds a server may take to print its opening line

_started = []           # every process start() started, in order
_given = set()          # every port free_port() handed out
_rand = random.SystemRandom()   # tests seed the random module; the ports must not follow those seeds


# ---- ports ------------------------------------------------------------------------------------------------

def _port_range():
    """Ports to hand out: below the ephemeral range (Linux 32768-60999 and macOS 49152-65535 by default)."""
    low, high = 20000, 32767
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as f:
            eph_low, eph_high = (int(x) for x in f.read().split())
    except (OSError, ValueError):
        return range(low, high + 1)
    if eph_low > low + 2000:
        return range(low, min(high, eph_low - 1) + 1)
    return range(max(eph_high + 1, 1025), 65536)       # a machine with a wider ephemeral range


PORTS = _port_range()


def bindable(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port():
    """A port nothing is bound to now, outside the ephemeral range, and not handed out before in this run."""
    for _ in range(2000):
        p = _rand.choice(PORTS)
        if p not in _given and bindable(p):
            _given.add(p)
            return p
    raise RuntimeError("no free port in %r" % (PORTS,))


# ---- processes --------------------------------------------------------------------------------------------

def start(argv, **kw):
    """subprocess.Popen in a process group of its own (cwd: the repository), remembered for the end of the
    run."""
    kw.setdefault("cwd", ROOT)
    kw["start_new_session"] = True
    p = subprocess.Popen(argv, **kw)
    p.tail = collections.deque(maxlen=200)      # the last lines drain() read, for a failure's message
    p.drainers = []                             # drain()'s threads
    _started.append(p)
    return p


def _members(pgid):
    """The processes in process group pgid, from pgrep."""
    try:
        out = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split()]


def _exited(p, timeout):
    """Wait for p to exit without reaping it, so its pid (its group's id) cannot be given to another process
    yet. -> True when it has exited."""
    end = time.monotonic() + timeout
    while True:
        try:
            if os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
        except ChildProcessError:
            return True                     # reaped already
        if time.monotonic() > end:
            return False
        time.sleep(0.01)


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stop(p, sig=signal.SIGKILL, timeout=10):
    """End p, then whatever is left of its process group (a core's plugin hosts), wait for p and close its
    pipes. sig goes to p alone first: SIGTERM stops a server as an operator would."""
    if p.returncode is None:
        try:
            os.kill(p.pid, sig)
        except ProcessLookupError:
            pass
        if not _exited(p, timeout):
            os.kill(p.pid, signal.SIGKILL)
            _exited(p, timeout)
        _kill_group(p.pid)                  # p is not reaped: the group id is still its own
    elif _members(p.pid):
        _kill_group(p.pid)                  # the group lives on in its members: the id is still this group's
    try:
        p.wait(timeout)
    except subprocess.TimeoutExpired:
        pass
    for t in p.drainers:                    # the group is gone: its pipes end, and the drains read the rest
        t.join(5)
    for f in (p.stdin, p.stdout, p.stderr):
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
    end = time.monotonic() + timeout
    while _members(p.pid) and time.monotonic() < end:     # SIGKILL is sure, but takes a moment
        time.sleep(0.02)


def stop_all(procs, sig=signal.SIGKILL):
    for p in reversed(list(procs)):
        stop(p, sig)
    del procs[:]


def owned(case):
    """A list for the processes of a test class (in setUpClass: owned(cls)) or of one test (owned(self)):
    they are stopped when it is done, even when its setup fails halfway."""
    procs = []
    if isinstance(case, type):
        case.addClassCleanup(stop_all, procs)
    else:
        case.addCleanup(stop_all, procs)
    return procs


def drain(p, out=None, err=None):
    """Read p's stdout and stderr in threads, so no pipe fills up and blocks it. Each line read goes to the
    list out or err when one is given (decoded, without its line end) and to p.tail."""
    for f, keep in ((p.stdout, out), (p.stderr, err)):
        if f is None:
            continue

        def run(f=f, keep=keep):
            try:
                for raw in iter(f.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    p.tail.append(line)
                    if keep is not None:
                        keep.append(line)
            except (OSError, ValueError):       # closed under it by stop()
                pass
        t = threading.Thread(target=run, daemon=True)
        t.start()
        p.drainers.append(t)
    return p


def _arg(args, name):
    args = list(args)
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return None


def banner(p, args, timeout=START_WAIT):
    """Read p's opening line. A server given --port N must name N in it ("... on 127.0.0.1:N"), which it
    prints only once it listens. -> the line; raises when p exited, said nothing in time, or names another
    port."""
    ready, _, _ = select.select([p.stdout], [], [], timeout)
    line = p.stdout.readline().decode("utf-8", "replace") if ready else ""
    port = _arg(args, "--port")
    if line and (port is None or re.search(r" on \S+:%s\b" % re.escape(str(port)), line)):
        return line
    if line:
        why = "printed %r" % line.rstrip()
    elif ready:
        why = "exited with %s" % p.wait(10)
    else:
        why = "said nothing in %ds" % timeout
    if p.returncode is None:
        _kill_group(p.pid)                  # it is not reaped: the group id is still its own
    err = b""
    if p.stderr is not None:
        end = time.monotonic() + 5
        while time.monotonic() < end and select.select([p.stderr], [], [], max(0, end - time.monotonic()))[0]:
            chunk = os.read(p.stderr.fileno(), 65536)
            if not chunk:
                break
            err += chunk
    stop(p)
    err = err.decode("utf-8", "replace").strip()[-3000:]
    raise RuntimeError("python -m %s did not start listening on port %s: it %s%s"
                       % (args[0], port, why, "\nits stderr:\n" + err if err else ""))


def spawn(*args, env=None, stderr=subprocess.PIPE, drain_output=True, out=None, err=None):
    """python -m args..., started with start() and its opening line checked by banner(); then its output is
    drained (into out and err, when given) unless drain_output is false."""
    p = start([sys.executable, "-m"] + [str(a) for a in args], env=env, stdout=subprocess.PIPE, stderr=stderr)
    p.first = banner(p, args)
    if drain_output:
        drain(p, out, err)
    return p


# ---- what is left -----------------------------------------------------------------------------------------

def leftovers():
    """Processes this run started that still run: a member of a group start() made, or a child of this
    process (not a zombie). -> [(pid, ppid, command)]."""
    try:
        ps = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,command="], capture_output=True,
                            text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    groups = {p.pid for p in _started}
    me = os.getpid()
    out = []
    for line in ps.stdout.splitlines():
        f = line.split(None, 4)
        if len(f) < 5 or not all(x.isdigit() for x in f[:3]):
            continue
        pid, ppid, pgid, stat, cmd = int(f[0]), int(f[1]), int(f[2]), f[3], f[4]
        if pid == me or stat.startswith("Z") or cmd.startswith("ps -A"):
            continue
        if pgid in groups or ppid == me:
            out.append((pid, ppid, cmd))
    return out


def stop_leftovers():
    """Stop everything leftovers() finds: the groups start() made, then this process's own children (an
    unreaped child keeps its pid, so the pid is still that child's)."""
    for p in list(_started):
        if p.returncode is None or _members(p.pid):
            stop(p)
    me = os.getpid()
    for pid, ppid, _ in leftovers():
        if ppid == me:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


atexit.register(stop_leftovers)
