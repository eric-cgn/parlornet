"""Server extensions on another host (deploy/README.md): deploy/install.sh publishing the players' ports on one
address and the extension channel on another, and enrolling keys for extensions that run elsewhere;
deploy/install-extensions.sh running named extensions against a remote core; deploy/start.py's
PARLOR_FRONTEND_HOST; and the admin menus' list of extensions, which follows PARLOR_ADAPTERS as the start script
does. The scripts run here with stand-ins for docker, install, chown and chmod that write down what they were
asked to do."""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters import catalog  # noqa: E402

FAKE = r'''#!/bin/sh
# Writes down each call, one line of arguments separated by tabs, in $FAKE_LOG.
{ printf '%s' "$(basename "$0")"; for a in "$@"; do printf '\t%s' "$a"; done; printf '\n'; } >> "$FAKE_LOG"
if [ "$(basename "$0")" = docker ]; then
	case "$1 $2" in
	"network inspect") exit 1 ;;
	"ps -aq") printf '%s\n' $FAKE_PS ;;
	"inspect -f") eval "echo \${FAKE_LABEL_$4:-}" ;;
	esac
fi
exit 0
'''


def run_deploy(script, args, env):
    """Runs deploy/SCRIPT with stand-ins for docker, install, chown and chmod: (exit code, stderr, [each call
    as a list of its arguments, the command first])."""
    d = tempfile.mkdtemp(prefix="pndeploy")
    try:
        for tool in ("docker", "install", "chown", "chmod"):
            path = os.path.join(d, tool)
            with open(path, "w") as f:
                f.write(FAKE)
            os.chmod(path, stat.S_IRWXU)
        log = os.path.join(d, "log")
        full = {"PATH": d + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), "FAKE_LOG": log}
        full.update(env)
        r = subprocess.run(["sh", os.path.join(ROOT, "deploy", script)] + args, env=full, capture_output=True,
                           text=True)
        calls = []
        if os.path.exists(log):
            with open(log) as f:
                calls = [l.rstrip("\n").split("\t") for l in f]
        return r.returncode, r.stderr, calls
    finally:
        shutil.rmtree(d, True)


def install_args(**env):
    """install.sh's docker run of the parlornet container, as its arguments, with this environment."""
    code, err, calls = run_deploy("install.sh", ["parlornet:test"], env)
    if code:
        raise AssertionError(err)
    main = [c[2:] for c in calls if c[:2] == ["docker", "run"] and "parlornet" in c and "--rm" not in c]
    if len(main) != 1:
        raise AssertionError(calls)
    return main[0], calls


def published(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "-p"]


class Scripts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pndeploy")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.keys = os.path.join(self.dir, "keys")
        os.mkdir(self.keys)

    run_script = staticmethod(run_deploy)
    published = staticmethod(published)

    @staticmethod
    def runs(calls):
        """The docker run calls, each as its arguments after "run"."""
        return [c[2:] for c in calls if c[:2] == ["docker", "run"]]

    # ---- install.sh -------------------------------------------------------------------------------------

    def core(self, **env):
        return install_args(**env)

    def test_install_publishes_every_address_by_default(self):
        args, _ = self.core()
        pub = self.published(args)
        self.assertIn("6500:6500", pub)
        self.assertIn("6506:4000", pub)
        self.assertIn("1744:6517", pub)
        self.assertIn("127.0.0.1:6513:6513", pub)
        self.assertIn("127.0.0.1:6510:6510", pub)
        self.assertFalse([p for p in pub if "6590" in p])
        self.assertFalse([a for a in args if a.startswith("PARLOR_FRONTEND_HOST")])

    def test_install_publishes_the_players_ports_on_one_address(self):
        args, _ = self.core(PARLOR_PUBLISH_IP="192.0.2.10")
        pub = self.published(args)
        players = [p for p in pub if not p.startswith("127.0.0.1:")]
        self.assertEqual(len(players), 17)
        self.assertTrue(all(p.startswith("192.0.2.10:") for p in players), players)
        self.assertIn("192.0.2.10:6506:4000", pub)
        self.assertIn("127.0.0.1:6513:6513", pub)          # Play and the admin menus stay on loopback
        self.assertIn("127.0.0.1:6510:6510", pub)
        args, _ = self.core(PARLOR_PUBLISH_IP="192.0.2.10", PARLOR_PLAY_OPEN="1")
        self.assertIn("192.0.2.10:6513:6513", self.published(args))
        args, _ = self.core(PARLOR_PLAY_OPEN="1")
        self.assertIn("6513:6513", self.published(args))

    def test_install_publishes_the_extension_channel_on_the_link(self):
        args, _ = self.core(PARLOR_FRONTEND_PUBLISH="10.0.0.1", PARLOR_ADAPTERS="ios,fibs,admin,bot")
        self.assertIn("10.0.0.1:6590:6590", self.published(args))
        self.assertIn("PARLOR_FRONTEND_HOST=0.0.0.0", args)
        self.assertIn("PARLOR_ADAPTERS=ios,fibs,admin,bot", args)
        self.assertFalse([p for p in self.published(args) if p.endswith(":6599")])     # the admin socket stays in

    def test_install_enrolls_keys_for_extensions_elsewhere(self):
        args, calls = self.core(PARLOR_REMOTE_EXTENSIONS="web,telnet,play")
        enroll = [r for r in self.runs(calls) if "parlord.frontends" in r]
        self.assertEqual(len(enroll), 1)
        e = enroll[0]
        self.assertEqual(e[-5:], ["/data/frontends", "add", "web", "telnet", "play"])
        for flag in ("--rm", "--read-only", "--cap-drop", "no-new-privileges", "10042:10042"):
            self.assertIn(flag, e)
        self.assertEqual(e[e.index("--network") + 1], "none")
        self.assertNotIn("--role", e)
        copies = [c for c in calls if c[0] == "install" and c[1:3] == ["-m", "400"]]
        self.assertEqual([c[3:] for c in copies],
                         [["/srv/parlornet/frontends/%s.key" % n, "/srv/parlornet-keys/%s.key" % n]
                          for n in ("web", "telnet", "play")])
        self.assertIn(["install", "-d", "-m", "700", "/srv/parlornet-keys"], calls)
        order = [c[:2] for c in calls if c[0] == "docker"]
        self.assertLess(order.index(["docker", "run"]), order.index(["docker", "rm"]))     # before the restart
        _, calls = self.core()
        self.assertFalse([r for r in self.runs(calls) if "parlord.frontends" in r])

    def test_install_keeps_its_safety_flags(self):
        args, _ = self.core(PARLOR_FRONTEND_PUBLISH="10.0.0.1", PARLOR_PUBLISH_IP="192.0.2.10")
        for flag in ("--read-only", "no-new-privileges", "10042:10042"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertEqual(args[args.index("--network") + 1], "parlor")
        self.assertNotIn("--cap-add", args)

    def test_install_memory_limits(self):
        args, _ = self.core()
        self.assertEqual(args[args.index("--memory") + 1], "448m")
        self.assertNotIn("--memory-swap", args)
        args, _ = self.core(PARLOR_MEMORY="1792m", PARLOR_MEMORY_SWAP="1792m")
        self.assertEqual(args[args.index("--memory") + 1], "1792m")
        self.assertEqual(args[args.index("--memory-swap") + 1], "1792m")

    # ---- install-extensions.sh --------------------------------------------------------------------------

    def test_extensions_memory_limits(self):
        self.key("web", "telnet")
        got, _ = self.extensions(["web", "telnet"])
        for r in got.values():
            self.assertEqual(r[r.index("--memory") + 1], "96m")
            self.assertNotIn("--memory-swap", r)
        got, _ = self.extensions(["web", "telnet"], PARLOR_MEMORY="160m", PARLOR_MEMORY_SWAP="160m")
        for r in got.values():
            self.assertEqual(r[r.index("--memory") + 1], "160m")
            self.assertEqual(r[r.index("--memory-swap") + 1], "160m")

    def key(self, *names):
        for n in names:
            with open(os.path.join(self.keys, n + ".key"), "w") as f:
                f.write("parlornet-frontend-key 1 %s frontend x\n" % n)

    def extensions(self, names, **env):
        env = dict({"PARLOR_CORE": "10.0.0.1:6590", "PARLOR_KEYDIR": self.keys,
                    "PARLOR_DATA": os.path.join(self.dir, "data")}, **env)
        code, err, calls = self.run_script("install-extensions.sh", ["parlornet:test"] + names, env)
        self.assertEqual(code, 0, err)
        return {r[r.index("--name") + 1]: r for r in self.runs(calls)}, calls

    def test_each_extension_in_its_own_container_against_the_remote_core(self):
        self.key("web", "telnet", "play")
        got, calls = self.extensions(["web", "telnet", "play"])
        self.assertEqual(sorted(got), ["parlornet-play", "parlornet-telnet", "parlornet-web"])
        for name, uid, module, port in (("web", 10043, "adapters.web", 6500),
                                        ("telnet", 10044, "adapters.telnet", 6502),
                                        ("play", 10055, "adapters.playserver", 6513)):
            r = got["parlornet-" + name]
            self.assertEqual(r[r.index("--user") + 1], "%d:%d" % (uid, uid))
            self.assertEqual(r[r.index("--cap-drop") + 1], "ALL")
            for flag in ("--read-only", "no-new-privileges", "--no-healthcheck"):
                self.assertIn(flag, r)
            self.assertNotIn("--cap-add", r)
            self.assertEqual(r[r.index("--network") + 1], "parlor-ext")
            self.assertIn("%s/%s.key:/run/parlornet/key:ro" % (self.keys, name), r)
            self.assertEqual(r[r.index("-m") + 1], module)
            self.assertEqual(r[r.index("--port") + 1], str(port))
            self.assertEqual(r[r.index("--core") + 1], "10.0.0.1:6590")
            self.assertIn(["chown", "%d:%d" % (uid, uid), "%s/%s.key" % (self.keys, name)], calls)
            self.assertIn(["chmod", "400", "%s/%s.key" % (self.keys, name)], calls)
        self.assertEqual(self.published(got["parlornet-web"]), ["6500:6500"])
        self.assertEqual(self.published(got["parlornet-telnet"]), ["6502:6502"])
        self.assertEqual(self.published(got["parlornet-play"]), ["127.0.0.1:6513:6513"])
        net = [c for c in calls if c[:3] == ["docker", "network", "create"]][0]
        self.assertEqual(net[net.index("--subnet") + 1], "172.30.6.0/24")
        self.assertEqual(net[net.index("--gateway") + 1], "172.30.6.1")
        self.assertIn("com.docker.network.bridge.enable_icc=false", net)
        self.assertEqual(net[-1], "parlor-ext")

    def test_play_has_its_own_data_and_the_settings_go_to_their_extensions(self):
        self.key("web", "telnet", "play")
        data = os.path.join(self.dir, "data")
        env = {"PARLOR_WEB_HOSTS": "games.example.org", "PARLOR_WEB_URL": "https://games.example.org/",
               "PARLOR_WEB_PROXIES": "172.30.6.1", "PARLOR_PLAY_URL": "https://games.example.org/play/",
               "PARLOR_PLAY_PROXIES": "172.30.6.1", "PARLOR_PLAY_PUSH_CONTACT": "mailto:ops@example.org"}
        got, calls = self.extensions(["web", "telnet", "play"], **env)
        play, web, telnet = got["parlornet-play"], got["parlornet-web"], got["parlornet-telnet"]
        self.assertIn(data + "/play:/data", play)
        self.assertIn("PARLOR_PLAY_DATA=/data", play)
        self.assertIn(["install", "-d", "-m", "700", "-o", "10055", "-g", "10055", data + "/play"], calls)
        for v in ("PARLOR_PLAY_URL", "PARLOR_PLAY_PROXIES", "PARLOR_PLAY_PUSH_CONTACT"):
            self.assertIn("%s=%s" % (v, env[v]), play)
        for v in ("PARLOR_WEB_HOSTS", "PARLOR_WEB_URL", "PARLOR_WEB_PROXIES", "PARLOR_PLAY_URL"):
            self.assertIn("%s=%s" % (v, env[v]), web)
        self.assertNotIn("PARLOR_WEB_URL=https://games.example.org/", play)
        self.assertFalse([a for a in telnet if a.startswith("PARLOR_")])
        self.assertFalse([a for a in web if a.endswith(":/data")])

    def test_publish_address_and_declared_ports(self):
        self.key("web", "play", "outland", "netluff")
        got, _ = self.extensions(["web", "play", "outland", "netluff"], PARLOR_PUBLISH_IP="192.0.2.20")
        self.assertEqual(self.published(got["parlornet-web"]), ["192.0.2.20:6500:6500"])
        self.assertEqual(self.published(got["parlornet-play"]), ["127.0.0.1:6513:6513"])
        self.assertEqual(self.published(got["parlornet-outland"]), ["192.0.2.20:6506:4000"])
        self.assertEqual(self.published(got["parlornet-netluff"]), ["192.0.2.20:6517:6517", "192.0.2.20:1744:6517"])
        out, nl = got["parlornet-outland"], got["parlornet-netluff"]
        self.assertEqual(out[out.index("--public-port") + 1], "6506")
        self.assertEqual(nl[nl.index("--public-port") + 1], "1744")
        self.assertNotIn("--public-port", got["parlornet-web"])
        got, _ = self.extensions(["play"], PARLOR_PUBLISH_IP="192.0.2.20", PARLOR_PLAY_OPEN="1")
        self.assertEqual(self.published(got["parlornet-play"]), ["192.0.2.20:6513:6513"])

    def test_refuses_without_a_key_a_known_name_or_the_core(self):
        self.key("web")
        env = {"PARLOR_CORE": "10.0.0.1:6590", "PARLOR_KEYDIR": self.keys, "PARLOR_DATA": self.dir}
        for names in (["web", "telnet"], ["web", "admin"], ["bot"], ["../web"], ["web."], []):
            code, err, calls = self.run_script("install-extensions.sh", ["parlornet:test"] + names, env)
            self.assertEqual(code, 2, names)
            self.assertFalse(self.runs(calls), names)
        code, err, calls = self.run_script("install-extensions.sh", ["parlornet:test", "telnet"], env)
        self.assertIn("telnet.key is missing", err)
        for core in ("", "10.0.0.1"):
            code, err, calls = self.run_script("install-extensions.sh", ["parlornet:test", "web"],
                                               dict(env, PARLOR_CORE=core))
            self.assertNotEqual(code, 0)
            self.assertFalse(self.runs(calls))

    def test_a_rerun_removes_only_the_extensions_it_no_longer_names(self):
        self.key("web", "telnet")
        got, calls = self.extensions(["web", "telnet"], FAKE_PS="c1 c2 c3", FAKE_LABEL_c1="web",
                                     FAKE_LABEL_c2="ios", FAKE_LABEL_c3="telnet")
        removed = [c[3:] for c in calls if c[:3] == ["docker", "rm", "-fv"]]
        self.assertIn(["c2"], removed)
        self.assertNotIn(["c1"], removed)
        self.assertNotIn(["c3"], removed)
        self.assertIn(["parlornet-web"], removed)       # replaced
        self.assertEqual(sorted(got), ["parlornet-telnet", "parlornet-web"])
        for r in got.values():
            self.assertIn("org.parlornet.extension=" + r[r.index("--name") + 1][len("parlornet-"):], r)

    def test_the_table_matches_the_catalog_and_the_image(self):
        with open(os.path.join(ROOT, "deploy", "install-extensions.sh")) as f:
            text = f.read()
        rows = text[text.index('EXTENSIONS="') + len('EXTENSIONS="'):]
        rows = [l.split() for l in rows[:rows.index('"')].splitlines()]
        with open(os.path.join(ROOT, "Dockerfile")) as f:
            uids = dict(re.findall(r"\b([a-z]+):(100[0-9]{2})\b", f.read()))
        known = {name: (module, port) for name, module, port, _ in catalog.ADAPTERS}
        self.assertEqual(sorted(r[0] for r in rows), sorted(set(catalog.NAMES) - {"admin", "bot"}))
        for name, uid, module, port, publish, declare in rows:
            self.assertEqual(uids[name], uid, name)
            self.assertEqual(known[name], (module, int(port)), name)
            public = catalog.PUBLISHED.get(name)
            self.assertEqual(declare, str(public) if public else "-", name)
            self.assertEqual(publish.split(",")[0], str(public if name == "outland" else port), name)
        self.assertNotIn("--cap-add", text)
        self.assertNotIn("--network host", text)


# ---- start.py and the admin menus' list ------------------------------------------------------------------------

def load_start(env):
    import importlib.util
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("pnstart_remote_%d" % id(env),
                                                      os.path.join(ROOT, "deploy", "start.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class Start(unittest.TestCase):
    def started(self, env):
        d = tempfile.mkdtemp(prefix="pndata")
        self.addCleanup(shutil.rmtree, d, True)
        env = dict(env, PARLOR_DB=os.path.join(d, "parlord.db"), PARLOR_SETTINGS=os.path.join(d, "none.conf"))
        st = load_start(env)
        started = []
        with mock.patch.dict(os.environ, env), mock.patch.object(st, "capabilities", lambda *a: 0), \
                mock.patch.object(st, "start", lambda name, args: started.append((name, args))), \
                mock.patch.object(st.time, "sleep", mock.Mock(side_effect=KeyboardInterrupt)), \
                mock.patch("builtins.print"), mock.patch.object(st.signal, "signal"), \
                mock.patch.object(st.sys, "argv", ["start.py"]):
            with self.assertRaises(KeyboardInterrupt):
                st.main()
        return dict(started)

    def test_the_extension_channel_listens_where_it_is_told(self):
        for env, host in (({}, "127.0.0.1"), ({"PARLOR_FRONTEND_HOST": "0.0.0.0"}, "0.0.0.0")):
            got = self.started(dict(env, PARLOR_ADAPTERS="ios,bot,admin"))
            core = got["parlord"]
            self.assertEqual(core[core.index("--frontend-host") + 1], host)
            self.assertEqual(core[core.index("--admin-port") + 1], "6599")
            self.assertNotIn("--admin-host", core)          # the admin socket stays on loopback
            for name in ("ios", "bot"):                     # the extensions here connect over loopback
                self.assertEqual(got[name][got[name].index("--core") + 1], "127.0.0.1:6590")
            self.assertEqual(sorted(got), ["admin", "bot", "ios", "parlord"])


class AdaptersList(unittest.IsolatedAsyncioTestCase):
    def test_chosen(self):
        with mock.patch.dict(os.environ, {"PARLOR_ADAPTERS": "ios,fibs"}):
            self.assertEqual(catalog.chosen(None), ["ios", "fibs"])
            self.assertEqual(catalog.chosen("web,admin"), ["web", "admin"])
            self.assertEqual(catalog.chosen(""), [])
        with mock.patch.dict(os.environ, {"PARLOR_ADAPTERS": ""}):
            self.assertEqual(catalog.chosen(None), catalog.default_enabled())

    async def test_the_admin_menus_show_what_the_container_starts(self):
        """With no list saved, the Adapters screen checks the extensions PARLOR_ADAPTERS names, which are the
        ones the start script starts, and not every installed one."""
        from adapters import admin as A
        shown = {}

        class UI:
            async def checklist(self, title, text, items):
                shown.update((name, on) for name, _, on in items)
                return None

        adm = A.Admin(UI(), None)

        async def get():
            return {"settings": {}}
        adm.get = get
        with mock.patch.dict(os.environ, {"PARLOR_ADAPTERS": "ios,fibs,admin,bot"}):
            await adm.adapters()
        self.assertEqual(sorted(n for n, on in shown.items() if on), ["admin", "bot", "fibs", "ios"])
        self.assertFalse(shown["web"])


if __name__ == "__main__":
    unittest.main()
