"""Starts ParlorNet in its container: the adapters the admin panel turned on
(adapters/catalog.py, the bot among them), the admin adapter, and parlord with its frontend
channel on 127.0.0.1:6590 (or PARLOR_FRONTEND_HOST) and its admin socket on 127.0.0.1:6599 (both TLS
1.3 with a pre-shared key per frontend; docs/security-design.md). Each
adapter gets its own key in /data/frontends, made here at first start (the
admin adapter's with the admin role). Their output goes to the container
log. If any of them exits, the rest are stopped and the container exits
(Docker restarts it with --restart unless-stopped).

Users (docs/security-design.md, Containment). By default (the image's user,
parlord, uid 10042, and --cap-drop ALL with no capability added) every process
runs as that one unprivileged user: the database and the keys are 600 in
directories of mode 700, and the log says per-extension users are off.
Opt-in, needing extra capabilities: started as root (--user 0) with SETUID,
SETGID and KILL, this script keeps root only to start, watch and stop the
others: parlord runs as parlord and each server extension as its own user
pn-<name> (web, telnet, ios, fibs, outland, doom, gamemaster, netsprocket, play, batnav, awale,
networdz, netluff, icds, worthy, rscards, dialin, admin, bot), which can read its own
key (group pn-<name>, mode 640) and nothing else of /data. Started as root
without those capabilities, it cannot switch users and says so. Either way a
first step run as parlord (--prepare) sets the modes and enrolls missing keys.

The Play extension keeps its own files (push subscriptions and its VAPID key) in play/ beside the database:
mode 700, or with per-extension users 2770 with the group pn-play, so only that extension (and the core's
user, which makes it) can enter it.

Settings for the NetSprocket extension, from the admin store or the settings file: netsprocket.idle_note_secs
and netsprocket.idle_repeat_secs (45 and 75):
the seconds of silence before a chat line with the turn's time left, and between those lines (0 to 3600; an
idle note of 0 sends none). Each goes to its extension as --idle-note or --idle-repeat.

Environment (all optional):
    PARLOR_NAME        server name until the admin panel sets one (parlornet)
    PARLOR_AUTH        sign-in method until the admin panel sets one: none or password (password)
    PARLOR_SIGNUP      sign-up method until the admin panel sets one: none, password, or email (a
                       password mailed to the player; with sign-in password); by default the sign-in
                       method's own (none, password)
    PARLOR_ADAPTERS    frontends until the admin panel sets them, e.g. web,telnet,admin (all installed)
    PARLOR_FRONTEND_HOST  the address the core's extension channel (6590) listens on in the container:
                       127.0.0.1 (the default), or 0.0.0.0 so that Docker can publish it for server
                       extensions on another host (deploy/README.md). The extensions started here connect
                       to 127.0.0.1 either way
    PARLOR_ADMIN=1     start the admin adapter even if the admin panel turned it off
    PARLOR_SETTINGS    key=value file for secrets such as email.smtp_password
                       (/run/parlornet/settings.conf if it is there)
    PARLOR_MOTD_FILE   message of the day until the admin panel sets one
    PARLOR_CONTRIBUTE_URL  the address put in the message of the day for {contribute} until the admin
                       panel sets one (https://github.com/eric-cgn/parlornet; empty: that line is left out)
    PARLOR_DOCS_URL    the docs site the web pages link to until the admin panel sets one
                       (http://docs.parlornet.org/; empty: no link)
    PARLOR_GAMEMASTER_PORT  the port the ParlorNet rulebook connects to, declared to the core, when the
                       host publishes the container's 6508 under another number (6508)
    PARLOR_WEB_URL     web.public_url until the settings file sets it: the web site's own address, such as
                       https://games.example.org/, which its pages name as canonical for search engines
    PARLOR_WEB_PROXIES  web.proxies until the settings file sets it: the addresses of reverse proxies in front
                       of the web site (port 6500), whose X-Forwarded-For and X-Forwarded-Proto it believes
    PARLOR_PLAY_URL    play.public_url until the settings file sets it: the address of ParlorNet Play behind
                       a TLS proxy, such as https://parlornet.org/play/ (the web's /play answers 301 there)
    PARLOR_PLAY_PROXIES  play.proxies until the settings file sets it: the proxies' addresses, whose
                       X-Forwarded-For and X-Forwarded-Proto the Play extension believes
    PARLOR_PLAY_PUSH_CONTACT  play.push_contact until the settings file sets it: the operator's mailto: or
                       https: address that ParlorNet Play gives push services; push notifications are off
                       without it (docs/play-notifications.md)
    PARLOR_DIALIN_GATEWAYS  dialin.gateways until the admin menus or the settings file set it: the addresses
                       of dial-in gateways, which carry modem calls to the dial-in extension (6521) and
                       may start each with a PROXY header. The core reads it, with PARLOR_DIALIN_CALL_MINS
                       and the other limits of the time allowance (parlord/allowance.py), from the
                       environment it inherits, and the dial-in extension asks the core
    PARLOR_DB          the SQLite database (/data/parlord.db)
    PARLOR_KEYS        the frontend key directory (frontends/ beside the database)
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

try:
    import grp
    import pwd
except ImportError:         # not a Unix: no users to switch to
    grp = pwd = None

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from adapters import catalog  # noqa: E402
from parlord import frontends  # noqa: E402

CORE_PORT, FRONTEND_PORT, ADMIN_SOCKET = 6501, 6590, 6599
IDLE_NOTES = ("netsprocket",)      # the extensions that fill a silence with a chat line (their docstrings)
IDLE_FLAGS = (("idle_note_secs", "--idle-note"), ("idle_repeat_secs", "--idle-repeat"))
CORE_USER = "parlord"
NO_SETUID = ("start: cannot start %s as its own user (per-extension users need --user 0 --cap-add SETUID "
             "--cap-add SETGID --cap-add KILL; the default is --user 10042, one user for everything)")
CAP_KILL, CAP_SETGID, CAP_SETUID = 5, 6, 7
env = os.environ.get
DB = env("PARLOR_DB", "/data/parlord.db")
KEYS = env("PARLOR_KEYS") or os.path.join(os.path.dirname(os.path.abspath(DB)), "frontends")


def key_for(name):
    """This adapter's key file, enrolled now if it is not yet (never printed)."""
    path = os.path.join(KEYS, name + ".key")
    if not os.path.exists(path):
        frontends.write_key(KEYS, name, "admin" if name == "admin" else "frontend")
        print("start: enrolled server extension %s" % name, flush=True)
    return path
procs = []


def stored(key):
    """A setting the admin panel saved, read before parlord starts; None if unset."""
    if not os.path.exists(DB):
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key=?", ("set:" + key,)).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def pump(p, first):
    for line in iter(p.stdout.readline, b""):
        sys.stdout.buffer.write(line)
        sys.stdout.flush()
        first.set()
    first.set()


def user_of(name):
    """The user a process runs as: parlord for the core, pn-<name> for a server
    extension; None if there is no such user here."""
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(CORE_USER if name == "parlord" else "pn-" + name)
    except KeyError:
        return None


def capabilities(path="/proc/self/status"):
    """The effective capability bits of this process, or None where /proc
    does not tell (not Linux)."""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return None


def can_switch_users(caps=None):
    """True if this process can start others as other users: root with the
    SETUID and SETGID capabilities (Linux), or root where capabilities are
    not known."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    caps = capabilities() if caps is None else caps
    return caps is None or all(caps >> bit & 1 for bit in (CAP_SETUID, CAP_SETGID))


def separate():
    """True when this script starts each process under its own user (the
    opt-in: root in the container with SETUID and SETGID, and the users the
    image makes)."""
    return user_of("parlord") is not None and can_switch_users()


def as_user(name):
    """Popen arguments that run a process as its own user (and the core with
    every pn- group, so its key tool can give each key to its extension)."""
    if not separate():
        return {}
    pw = user_of(name) or user_of("parlord")
    groups = []
    if name == "parlord":
        groups = [g.gr_gid for g in grp.getgrall() if g.gr_name.startswith(frontends.GROUP_PREFIX)]
    return {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": groups, "umask": 0o077}


def prepare(per_user=False):
    """The files, as the core's user. One user for everything (the default):
    /data and the key directory 700, the database and the keys 600. Per-user
    (the opt-in): /data and the key directory 711 (each extension can reach
    its own key by name, list nothing), the database 600 and each key 640 to
    its extension's group pn-<name>. Then the adapters to start, with their
    keys enrolled. Returns {"names":
    [...], "settings": the settings file if the core's user can read it}."""
    data = os.path.dirname(os.path.abspath(DB))
    os.makedirs(KEYS, mode=0o700, exist_ok=True)
    for d in (data, KEYS):
        os.chmod(d, 0o711 if per_user else 0o700)
    base = os.path.basename(DB)
    for f in os.listdir(data):
        if f == base or f.startswith(base + "-"):
            os.chmod(os.path.join(data, f), 0o600)
    for f in os.listdir(KEYS):
        if f.endswith(".key") and frontends.NAME_RE.match(f[:-4]):
            path = os.path.join(KEYS, f)
            if per_user:
                frontends.share_key(path, f[:-4], KEYS)
            else:
                os.chmod(path, 0o600)
    names = catalog.chosen(stored("adapters.enabled"))
    if env("PARLOR_ADMIN") == "1" and "admin" not in names:
        names.append("admin")
    play_dir = os.path.join(data, "play")
    try:
        os.makedirs(play_dir, mode=0o700, exist_ok=True)
        if per_user and grp is not None:
            os.chown(play_dir, -1, grp.getgrnam(frontends.GROUP_PREFIX + "play").gr_gid)
            os.chmod(play_dir, 0o2770)
        else:
            os.chmod(play_dir, 0o700)
    except (OSError, KeyError) as e:
        print("start: %s cannot be prepared (%s); Play's push notifications are off" % (play_dir, type(e).__name__),
              flush=True)
    out = []
    for name, module, port, _ in catalog.ADAPTERS:
        if name not in names:
            continue
        if not catalog.installed(module):
            print("start: %s (%s) is not installed here; skipped" % (name, module), flush=True)
            continue
        key_for(name)
        out.append(name)
    settings = env("PARLOR_SETTINGS") or "/run/parlornet/settings.conf"
    if os.path.exists(settings) and not os.access(settings, os.R_OK):
        print("start: %s is not readable by uid %d; its settings are not used" % (settings, os.getuid()),
              flush=True)
    conf = {}
    if os.access(settings, os.R_OK):
        with open(settings, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep:
                    conf[k.strip()] = v.strip()

    def setting(key):           # the admin menus, then the settings file (then the environment, in main)
        v = stored(key)
        return (conf.get(key) if v is None else v) or ""
    return {"names": out, "settings": settings if os.access(settings, os.R_OK) else None,
            "web_hosts": setting("web.hosts"), "web_url": setting("web.public_url"),
            "web_proxies": setting("web.proxies"), "play_url": setting("play.public_url"),
            "play_proxies": setting("play.proxies"), "play_push_contact": setting("play.push_contact"),
            "play_dir": play_dir,
            "idle": {k: setting(k) for k in ("%s.%s" % (n, f) for n in IDLE_NOTES for f, _ in IDLE_FLAGS)
                     if setting(k)}}


def idle_flags(name, idle):
    """An extension's idle-note settings ({key: text}) -> its arguments; a value that is not 0 to 3600 seconds
    is left out (the extension's default holds) and the log says so."""
    out = []
    for key, flag in IDLE_FLAGS:
        v = idle.get("%s.%s" % (name, key), "").strip()
        if not v:
            continue
        try:
            ok = 0 <= float(v) <= 3600
        except ValueError:
            ok = False
        if ok:
            out += [flag, v]
        else:
            print("start: %s.%s is not a number of seconds from 0 to 3600; the default is used" % (name, key),
                  flush=True)
    return out


def prepared():
    """prepare(), run as the core's user when this script is root."""
    if not separate():
        return prepare()
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--prepare", "per-user"], cwd=APP, capture_output=True,
                           text=True, **as_user("parlord"))
    except PermissionError:
        sys.exit(NO_SETUID % "parlord")
    lines = r.stdout.strip().splitlines()
    for line in lines[:-1]:             # what it printed, then its answer on the last line
        print(line, flush=True)
    sys.stderr.write(r.stderr)
    if r.returncode or not lines:
        sys.exit("start: preparing /data as %s failed" % CORE_USER)
    return json.loads(lines[-1])


def start(name, args):
    try:
        p = subprocess.Popen([sys.executable, "-m"] + args, cwd=APP, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, **as_user(name))
    except PermissionError:
        print(NO_SETUID % name, flush=True)
        stop(1)
    p.label = name
    first = threading.Event()
    threading.Thread(target=pump, args=(p, first), daemon=True).start()
    procs.append(p)
    first.wait(15)          # its "listening on" line, so the log reads in order
    return p


def signal_all(how):
    """Signal the others; without the KILL capability, root cannot signal
    processes of other users: then this script's exit (PID 1 of the
    container) ends them."""
    for p in procs:
        if p.poll() is None:
            try:
                how(p)
            except PermissionError:
                return False
    return True


def stop(code):
    if signal_all(lambda p: p.terminate()):
        end = time.time() + 5
        for p in procs:
            try:
                p.wait(max(0.1, end - time.time()))
            except subprocess.TimeoutExpired:
                signal_all(lambda p: p.kill())
                break
    sys.exit(code)


def main():
    os.umask(0o077)         # the database, its journal and new keys: owner only (inherited by every process)
    if sys.argv[1:2] == ["--prepare"]:
        print(json.dumps(prepare(sys.argv[2:] == ["per-user"])))
        return
    signal.signal(signal.SIGTERM, lambda *a: stop(0))
    signal.signal(signal.SIGINT, lambda *a: stop(0))
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not separate():
        # root that cannot switch users could only run everything as root: refuse that
        sys.exit("start: started as root without the SETUID and SETGID capabilities, so it cannot switch to "
                 "an unprivileged user; start the container with --user 10042 (the image's default)")
    if not separate():
        print("start: per-extension users are off: every process runs as uid %d"
              % (os.getuid() if hasattr(os, "getuid") else -1), flush=True)
    else:
        print("start: per-extension users are on (opt-in: root with SETUID, SETGID and KILL)", flush=True)
    ready = prepared()
    names = ready["names"]
    for name, module, port, _ in catalog.ADAPTERS:
        if name not in names:
            continue
        key = os.path.join(KEYS, name + ".key")
        if port is None:            # the bot (docs/bot.md): a client of the core, listening on nothing
            start(name, [module, "--core", "127.0.0.1:%d" % FRONTEND_PORT, "--key", key])
        elif name == "admin":
            # 0.0.0.0 inside the container, so Docker can forward the host's 127.0.0.1:6510 to it
            # (install.sh publishes it there only; the image does not EXPOSE it)
            start(name, [module, "--host", env("PARLOR_ADMIN_HOST") or "0.0.0.0", "--port", str(port),
                         "--core-admin", "127.0.0.1:%d" % ADMIN_SOCKET, "--key", key])
        else:
            extra = []
            play_url = ready.get("play_url") or env("PARLOR_PLAY_URL") or ""
            if name == "web":       # web.hosts (admin menus), else PARLOR_WEB_HOSTS
                hosts = ready.get("web_hosts") or env("PARLOR_WEB_HOSTS") or ""
                extra = ["--hosts", hosts] if hosts else []
                extra += ["--play-url", play_url] if play_url else []
                web_url = ready.get("web_url") or env("PARLOR_WEB_URL") or ""
                web_proxies = ready.get("web_proxies") or env("PARLOR_WEB_PROXIES") or ""
                extra += (["--public-url", web_url] if web_url else []) + (["--proxies", web_proxies] if web_proxies else [])
            if name == "play":      # play.public_url and play.proxies (settings file), else the environment
                proxies = ready.get("play_proxies") or env("PARLOR_PLAY_PROXIES") or ""
                push_contact = ready.get("play_push_contact") or env("PARLOR_PLAY_PUSH_CONTACT") or ""
                extra = ["--data", ready.get("play_dir") or os.path.join(os.path.dirname(os.path.abspath(DB)), "play")]
                extra += ["--push-contact", push_contact] if push_contact else []
                extra += (["--public-url", play_url] if play_url else []) + (["--proxies", proxies] if proxies else [])
            if name in IDLE_NOTES:
                extra += idle_flags(name, ready.get("idle") or {})
            if name in catalog.PUBLISHED:   # the port players connect to, declared to the core
                extra += ["--public-port", str(catalog.PUBLISHED[name])]
            if name == "gamemaster" and env("PARLOR_GAMEMASTER_PORT"):
                extra += ["--public-port", env("PARLOR_GAMEMASTER_PORT")]
            start(name, [module, "--host", "0.0.0.0", "--port", str(port),
                         "--core", "127.0.0.1:%d" % FRONTEND_PORT, "--key", key] + extra)
    core = ["parlord", "--host", "0.0.0.0", "--port", str(CORE_PORT), "--db", DB,
            "--name", env("PARLOR_NAME") or "parlornet", "--auth", env("PARLOR_AUTH") or "password",
            "--frontend-host", env("PARLOR_FRONTEND_HOST") or "127.0.0.1", "--frontend-port", str(FRONTEND_PORT),
            "--keys", KEYS,
            "--admin-port", str(ADMIN_SOCKET), "--admin-telnet", "6510"]
    if env("PARLOR_SIGNUP"):
        core += ["--signup", env("PARLOR_SIGNUP")]
    if ready["settings"]:
        core += ["--settings-file", ready["settings"]]      # readable by the core's user (checked as that user)
    if env("PARLOR_MOTD_FILE"):
        core += ["--motd", env("PARLOR_MOTD_FILE")]
    start("parlord", core)
    while True:
        for p in procs:
            if p.poll() is not None:
                print("start: %s exited (%s); stopping" % (p.label, p.returncode), flush=True)
                stop(1)
        time.sleep(1)


if __name__ == "__main__":
    main()
