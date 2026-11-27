"""The Docker Compose path for operators (deploy/README.md): deploy/compose.yaml, its settings in
deploy/env.example, the systemd unit deploy/parlornet.service and deploy/setup.sh, which installs them in
/opt/parlornet. The compose file must give the container what deploy/install.sh gives it: the same ports as the
catalog, the image's uid, the same limits and network, and the same settings. Where docker compose is installed,
the tests also compare the configuration it renders with install.sh's docker run, made with stand-ins.

    python3 -m unittest discover -s tests -p "test_compose.py"
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import catalog  # noqa: E402
from test_remote_extensions import install_args, published  # noqa: E402

PUB = "${PARLOR_PUBLISH_IP:+${PARLOR_PUBLISH_IP}:}"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def compose_ports():
    """compose.yaml's port lines, each as written between its quotes."""
    return re.findall(r'^      - "([^"]+)"$', read("deploy", "compose.yaml"), re.M)


def compose_env():
    """The names in compose.yaml's environment list, in order, and the values it fixes."""
    text = read("deploy", "compose.yaml")
    block = text[text.index("    environment:\n"):text.index("    volumes:\n")]
    names, fixed = [], {}
    for item in re.findall(r"^      - (\S+)$", block, re.M):
        name, eq, value = item.partition("=")
        names.append(name)
        if eq:
            fixed[name] = value
    return names, fixed


def install_env_names():
    """The settings install.sh passes to the container when they are set: the names in its loop."""
    text = read("deploy", "install.sh")
    start = text.index("$(for v in")
    return text[start + len("$(for v in"):text.index("; do", start)].replace("\\", " ").split()


def example_settings():
    """env.example's settings, each written as "# NAME=value": {name: value}."""
    return dict(re.findall(r"^# (PARLOR_[A-Z_]+)=(.*)$", read("deploy", "env.example"), re.M))


class ComposeFile(unittest.TestCase):
    def test_the_ports_are_the_catalogs(self):
        players = set()
        for name, _, port, _ in catalog.ADAPTERS:
            if port is not None and name not in ("admin", "play"):
                players.add("%d:%d" % (catalog.PUBLISHED.get(name, port), port))
        players |= {"6501:6501", "6517:6517"}               # the core, and NetLuff under its own number too
        ports = compose_ports()
        self.assertEqual({p[len(PUB):] for p in ports if p.startswith(PUB)}, players)
        self.assertEqual(len(ports), len(players) + 2)
        self.assertIn("${PARLOR_PLAY_PUBLISH-127.0.0.1}:6513:6513", ports)
        self.assertIn("127.0.0.1:6510:6510", ports)

    def test_the_ports_are_install_shs(self):
        args, _ = install_args()
        ours = {p[len(PUB):] if p.startswith(PUB) else p.replace("${PARLOR_PLAY_PUBLISH-127.0.0.1}", "127.0.0.1")
                for p in compose_ports()}
        self.assertEqual(ours, set(published(args)))

    def test_the_images_user_and_install_shs_limits(self):
        text = read("deploy", "compose.yaml")
        uid = re.search(r"useradd --system --uid (\d+) --gid (\d+) [^\n]* parlord\b", read("Dockerfile")).groups()
        self.assertIn('    user: "%s:%s"\n' % uid, text)
        install = read("deploy", "install.sh")
        self.assertIn("--user %s:%s" % uid, install)
        for line in ("    cap_drop: [ALL]\n", "    security_opt: [no-new-privileges]\n", "    read_only: true\n",
                     "    restart: unless-stopped\n", "    container_name: parlornet\n"):
            self.assertIn(line, text)
        self.assertNotIn("cap_add", text)
        self.assertNotIn("network_mode", text)
        self.assertNotIn("privileged", text)
        tmpfs = re.search(r"--tmpfs (\S+)", install).group(1)
        self.assertIn("      - %s\n" % tmpfs, text)
        self.assertIn("    pids_limit: %s\n" % re.search(r"--pids-limit (\d+)", install).group(1), text)
        memory = re.search(r'--memory "\$\{PARLOR_MEMORY:-(\w+)\}"', install).group(1)
        self.assertIn("    mem_limit: ${PARLOR_MEMORY:-%s}\n" % memory, text)
        self.assertIn("    memswap_limit: ${PARLOR_MEMORY_SWAP:-0}\n", text)       # 0: Docker's own default

    def test_the_network_is_install_shs(self):
        text = read("deploy", "compose.yaml")
        install = read("deploy", "install.sh")
        subnet = re.search(r"--subnet (\S+)", install).group(1)
        bridge = re.search(r"com\.docker\.network\.bridge\.name=(\S+)", install).group(1)
        self.assertIn("        - subnet: %s\n" % subnet, text)
        self.assertIn("      com.docker.network.bridge.name: %s\n" % bridge, text)
        self.assertIn("    name: parlor\n", text)
        self.assertIn("    networks: [parlor]\n", text)

    def test_the_settings_are_install_shs(self):
        names, fixed = compose_env()
        self.assertEqual(fixed, {"PARLOR_MOTD_FILE": "/app/deploy/motd.txt"})
        self.assertIn("-e PARLOR_MOTD_FILE=/app/deploy/motd.txt", read("deploy", "install.sh"))
        self.assertEqual([n for n in names if n not in fixed and n not in ("PARLOR_CONTRIBUTE_URL", "PARLOR_DOCS_URL")],
                         install_env_names())
        self.assertNotIn("env_file", read("deploy", "compose.yaml"))     # the host's own settings stay out

    def test_the_mounts_are_install_shs(self):
        text = read("deploy", "compose.yaml")
        for source, target, ro in (("${PARLOR_DATA_DIR:-/srv/parlornet}", "/data", False),
                                   ("${PARLOR_SETTINGS_FILE:-/etc/parlornet/settings.conf}",
                                    "/run/parlornet/settings.conf", True),
                                   ("${PARLOR_SITE_DIR:-/srv/parlornet-site}", "/run/parlornet/site", True)):
            mount = ("      - type: bind\n        source: %s\n        target: %s\n%s"
                     "        bind: {create_host_path: false}\n"
                     % (source, target, "        read_only: true\n" if ro else ""))
            self.assertIn(mount, text)
        install = read("deploy", "install.sh")
        self.assertIn("-v /srv/parlornet:/data", install)
        self.assertIn("/etc/parlornet/settings.conf:/run/parlornet/settings.conf:ro", install)
        self.assertIn(":/run/parlornet/site:ro", install)

    def test_the_image_is_this_release(self):
        image = re.search(r"^    image: \$\{PARLOR_IMAGE:-(\S+):(\S+)\}$", read("deploy", "compose.yaml"), re.M)
        self.assertIsNotNone(image)
        self.assertEqual(image.group(1), "ghcr.io/eric-cgn/parlornet")
        self.assertEqual(image.group(2), read("VERSION").strip(),
                         "deploy/compose.yaml names the image of the version in VERSION")
        self.assertIn("IMAGE=%s\n" % image.group(1), read("deploy", "setup.sh"))


class Example(unittest.TestCase):
    def test_every_setting_is_in_the_example(self):
        text = read("deploy", "compose.yaml")
        used = set(re.findall(r"\$\{(PARLOR_[A-Z_]+)", text)) | set(compose_env()[0]) - {"PARLOR_MOTD_FILE"}
        self.assertEqual(set(example_settings()), used)
        self.assertEqual(set(install_env_names()) - set(example_settings()), set())

    def test_it_sets_nothing_and_names_no_host_of_ours(self):
        text = read("deploy", "env.example")
        self.assertEqual([l for l in text.splitlines() if l and not l.startswith("#")], [])
        self.assertEqual(set(re.findall(r"[\w.-]*parlornet\.org", text)), {"docs.parlornet.org"})

    def test_its_defaults_are_the_compose_files(self):
        text = read("deploy", "compose.yaml")
        settings = example_settings()
        for name, default in re.findall(r"\$\{(PARLOR_[A-Z_]+):?-([^}$]+)\}", text):
            if name not in ("PARLOR_IMAGE", "PARLOR_MEMORY_SWAP"):
                self.assertEqual(settings[name], default, name)


class Unit(unittest.TestCase):
    def test_it_runs_the_project_in_setup_shs_directory(self):
        unit = read("deploy", "parlornet.service")
        setup = read("deploy", "setup.sh")
        directory = re.search(r"^DIR=(\S+)$", setup, re.M).group(1)
        self.assertIn("WorkingDirectory=%s\n" % directory, unit)
        self.assertIn("After=docker.service", unit)
        self.assertIn("Requires=docker.service", unit)
        self.assertIn("ExecStart=docker compose up -d\n", unit)
        self.assertIn("ExecStop=docker compose down\n", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("UNIT=/etc/systemd/system/parlornet.service\n", setup)


class Setup(unittest.TestCase):
    def test_it_installs_the_files_that_are_here(self):
        setup = read("deploy", "setup.sh")
        for name in ("compose.yaml", "env.example", "setup.sh", "parlornet.service"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "deploy", name)), name)
            self.assertIn('"$SRC/%s"' % name, setup)
        self.assertTrue(setup.startswith("#!/bin/sh\n"))
        for bash in ("[[", "local ", "function ", "$'", "<<<", "pipefail"):
            self.assertNotIn(bash, setup)

    def test_its_directories_are_the_compose_files(self):
        setup = read("deploy", "setup.sh")
        settings = example_settings()
        for name in ("PARLOR_DATA_DIR", "PARLOR_SITE_DIR", "PARLOR_SETTINGS_FILE"):
            self.assertIn("get %s %s)" % (name, settings[name]), setup)
        uid = re.search(r"useradd --system --uid (\d+)", read("Dockerfile")).group(1)
        self.assertIn('install -d -m 700 -o %s -g %s "$(get PARLOR_DATA_DIR' % (uid, uid), setup)
        self.assertIn('chown %s:%s "$CONF"' % (uid, uid), setup)
        self.assertIn('chmod 400 "$CONF"', setup)

    def functions(self):
        """setup.sh's functions, to run on their own."""
        setup = read("deploy", "setup.sh")
        return setup[setup.index("die() {"):setup.index('[ "$(id -u)" = 0 ]')]

    def sh(self, script, env=None, stdin=""):
        full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        full.update(env or {})
        return subprocess.run(["sh", "-eu", "-c", self.functions() + script], env=full, input=stdin,
                              capture_output=True, text=True)

    def test_it_writes_the_answers_into_the_example(self):
        d = tempfile.mkdtemp(prefix="pnsetup")
        self.addCleanup(shutil.rmtree, d, True)
        new = os.path.join(d, "env")
        shutil.copy(os.path.join(ROOT, "deploy", "env.example"), new)
        r = self.sh('NEW=%s; put PARLOR_NAME "Club Night"; put PARLOR_WEB_URL https://games.example.org/; '
                    'put PARLOR_NAME again' % new)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(new) as f:
            text = f.read()
        self.assertEqual([l for l in text.splitlines() if l and not l.startswith("#")],
                         ["PARLOR_NAME=Club Night", "PARLOR_WEB_URL=https://games.example.org/"])
        self.assertIn("# PARLOR_NAME=parlornet\n", read("deploy", "env.example"))
        self.assertNotIn("# PARLOR_NAME=parlornet\n", text)
        self.assertNotIn("again", text)                     # a second put of a name finds no line to replace

    def test_it_asks_or_takes_the_environment(self):
        r = self.sh('ask PARLOR_NAME "Name" parlornet; ask PARLOR_AUTH "Auth" password; echo "$PARLOR_NAME/$PARLOR_AUTH"',
                    stdin="festival\n\n")
        self.assertEqual(r.stdout, "festival/password\n", r.stderr)
        self.assertIn("Name [parlornet]: ", r.stderr)
        r = self.sh('ask PARLOR_NAME "Name" parlornet; echo "$PARLOR_NAME"', env={"PARLOR_NAME": "games.example.org"})
        self.assertEqual(r.stdout, "games.example.org\n")
        self.assertEqual(r.stderr, "")
        r = self.sh('ask PARLOR_WEB_URL "Web" ""; echo "[$PARLOR_WEB_URL]"', stdin="")
        self.assertEqual(r.stdout, "[]\n")
        for bad in ("a$b", "a#b", 'a"b', "a'b", "a\\b", "a`b", "a;b"):
            r = self.sh('ask PARLOR_NAME "Name" parlornet; echo "$PARLOR_NAME"', env={"PARLOR_NAME": bad})
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("PARLOR_NAME holds only", r.stderr)

    def test_it_reads_one_setting_from_env(self):
        d = tempfile.mkdtemp(prefix="pnsetup")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("# PARLOR_DATA_DIR=/srv/parlornet\nPARLOR_DATA_DIR=/srv/games\nPARLOR_NAME=Club Night\n")
        r = self.sh('DIR=%s; get PARLOR_DATA_DIR /srv/parlornet; get PARLOR_SITE_DIR /srv/parlornet-site; '
                    'get PARLOR_NAME x' % d)
        self.assertEqual(r.stdout.splitlines(), ["/srv/games", "/srv/parlornet-site", "Club Night"])
        r = self.sh('DIR=%s/none; get PARLOR_DATA_DIR /srv/parlornet' % d)
        self.assertEqual(r.stdout, "/srv/parlornet\n")


def compose(env):
    """docker compose's rendering of compose.yaml with this environment, as JSON."""
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    full.update(env)
    r = subprocess.run(["docker", "compose", "-f", os.path.join(ROOT, "deploy", "compose.yaml"), "--env-file",
                        os.devnull, "config", "--format", "json"], env=full, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)["services"]["parlornet"]


def as_published(port):
    """A port of docker compose's rendering as install.sh writes it for docker run -p."""
    host = port.get("host_ip")
    return "%s%s:%s" % (host + ":" if host else "", port["published"], port["target"])


def has_compose():
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0
    except OSError:
        return False


@unittest.skipUnless(has_compose(), "docker compose is not installed")
class Rendered(unittest.TestCase):
    """docker compose config needs no Docker daemon."""

    def test_the_same_ports_as_install_sh(self):
        for env, play in (({}, {}), ({"PARLOR_PUBLISH_IP": "192.0.2.10"}, {}),
                          ({"PARLOR_PUBLISH_IP": "192.0.2.10"}, {"PARLOR_PLAY_OPEN": "1"}), ({}, {"PARLOR_PLAY_OPEN": "1"})):
            ours = dict(env)
            if play:
                ours["PARLOR_PLAY_PUBLISH"] = env.get("PARLOR_PUBLISH_IP", "")
            rendered = sorted(as_published(p) for p in compose(ours)["ports"])
            self.assertEqual(rendered, sorted(published(install_args(**env, **play)[0])), (env, play))

    def test_the_same_settings_as_install_sh(self):
        env = {"PARLOR_NAME": "games.example.org", "PARLOR_AUTH": "password", "PARLOR_SIGNUP": "email",
               "PARLOR_PLAY_URL": "https://games.example.org/play/", "PARLOR_PLAY_PROXIES": "172.30.4.1",
               "PARLOR_DIALIN_CALL_MINS": "30"}
        args, _ = install_args(**env)
        theirs = {a.partition("=")[0]: a.partition("=")[2] for i, a in enumerate(args) if args[i - 1] == "-e"}
        ours = {k: v for k, v in compose(env)["environment"].items() if v is not None}
        self.assertEqual(ours, theirs)

    def test_the_same_limits_as_install_sh(self):
        args, _ = install_args(PARLOR_MEMORY="1792m", PARLOR_MEMORY_SWAP="1792m")
        svc = compose({"PARLOR_MEMORY": "1792m", "PARLOR_MEMORY_SWAP": "1792m"})
        self.assertEqual(svc["mem_limit"], str(1792 * 1024 * 1024))
        self.assertEqual(svc["memswap_limit"], str(1792 * 1024 * 1024))
        self.assertEqual(svc["user"], args[args.index("--user") + 1])
        self.assertEqual(svc["pids_limit"], int(args[args.index("--pids-limit") + 1]))
        self.assertEqual(svc["cap_drop"], [args[args.index("--cap-drop") + 1]])
        self.assertEqual(svc["security_opt"], [args[args.index("--security-opt") + 1]])
        self.assertEqual(svc["tmpfs"], [args[args.index("--tmpfs") + 1]])
        self.assertTrue(svc["read_only"])
        self.assertNotIn("cap_add", svc)
        self.assertNotIn("network_mode", svc)
        self.assertEqual(list(svc["networks"]), [args[args.index("--network") + 1]])
        self.assertNotIn("memswap_limit", compose({}))


if __name__ == "__main__":
    unittest.main()
