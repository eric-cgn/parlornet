"""Plugin processes (docs/plugins.md): info read once at start-up, processes started on demand and
stopped when idle, the plugin host that serves every Python plugin in one process, and Grid Words'
compact word list.  python3 -m unittest discover -s tests"""
import asyncio
import contextlib
import gc
import importlib.util
import io
import os
import random
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parlord.core import Limits, Plugin, PluginError, Server, Table  # noqa: E402

_spec = importlib.util.spec_from_file_location("gw_plugin_words", os.path.join(ROOT, "plugins", "gridwords", "plugin.py"))
GW = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GW)

# Test plugins. NAME is the same global in each, with a different value: each hosted plugin has its
# own namespace.
PLUGIN = '''
import json, sys, time
NAME = %(name)r
INFO = {"type": NAME, "ver": 1, "name": NAME, "rules": ["standard"], "min": 2, "max": 2, "flags": [],
        "options": []%(extra)s}

def op_info(req):
    return INFO

def op_new(req):
    %(new)s
    return {"state": {"by": NAME, "game": req.get("game")}, "events": ["TURN 0"]}

OPS = {"info": op_info, "new": op_new}

def main():
    for line in sys.stdin:
        req = json.loads(line)
        try:
            reply = OPS[req["op"]](req)
        except Exception as e:
            reply = {"error": "plugin: %%s" %% e}
        sys.stdout.write(json.dumps(reply) + "\\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
'''
NO_OPS = '''
import json, sys
INFO = {"type": "noops", "ver": 1, "name": "noops", "rules": ["standard"], "min": 2, "max": 2, "flags": []}
for line in sys.stdin:
    req = json.loads(line)
    sys.stdout.write(json.dumps(INFO if req["op"] == "info" else {"state": {"by": "noops"}, "events": []}) + "\\n")
    sys.stdout.flush()
'''
EXECUTABLE = '''#!/bin/sh
while read -r line; do
  case "$line" in
    *'"info"'*) echo '{"type":"shell","ver":1,"name":"shell","rules":["standard"],"min":2,"max":2,"flags":[]}';;
    *) echo '{"state":{"by":"shell"},"events":[]}';;
  esac
done
'''


def plugin(name, new="pass", extra=""):
    return PLUGIN % {"name": name, "new": new, "extra": extra}


NEW = {"op": "new", "rules": "standard", "players": 2, "options": {}, "seed": 1}


class PluginDir(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.write("alpha", plugin("alpha"))
        self.write("beta", plugin("beta"))
        self.write("boom", plugin("boom", new="raise RuntimeError('boom')"))
        self.write("noisy", plugin("noisy", new="print('noise on stdout'); import os; os.write(1, b'raw noise\\n')"))
        self.write("quits", plugin("quits", new="sys.exit(3)"))
        self.write("hangs", plugin("hangs", new="time.sleep(30) if req.get('options') else None"))
        self.write("own", plugin("own", extra=', "process": "own"'))
        self.write("noops", NO_OPS)
        self.write("broken", "this is not python\n")
        self.write("shell", EXECUTABLE, "plugin")
        os.chmod(os.path.join(self.dir, "shell", "plugin"), 0o755)
        self.idle = Limits.plugin_idle_secs
        self.srv = Server(self.dir, "test")
        gc.collect()        # earlier tests' unclosed objects warn on stderr when collected; not inside the capture
        with contextlib.redirect_stderr(io.StringIO()) as err:
            await self.srv.load_plugins()
        # only parlord's own lines: asyncio's debug mode (on in IsolatedAsyncioTestCase) may add slow-task notes
        ours = [l for l in err.getvalue().splitlines(True) if l.startswith("parlord:")]
        self.assertEqual(ours, ["parlord: plugin broken gave no usable info; not offered\n"])
        self.host = self.srv.plugins["alpha/1"].process

    def write(self, name, text, fname="plugin.py"):
        os.makedirs(os.path.join(self.dir, name), exist_ok=True)
        with open(os.path.join(self.dir, name, fname), "w") as f:
            f.write(text)

    async def asyncTearDown(self):
        Limits.plugin_idle_secs = self.idle
        for p in self.srv.plugins.values():
            await p.process.close()
        shutil.rmtree(self.dir)


class InfoAndDemand(PluginDir):
    async def test_info_read_once_and_nothing_left_running(self):
        self.assertEqual(sorted(self.srv.plugins), ["alpha/1", "beta/1", "boom/1", "hangs/1", "noisy/1", "noops/1",
                                                    "own/1", "quits/1", "shell/1"])     # broken: not offered
        self.assertFalse(any(p.process.running for p in self.srv.plugins.values()))
        self.assertEqual(self.host.spawns, 1)                   # one host start for every hosted plugin's info

    async def test_who_is_hosted(self):
        p = self.srv.plugins
        hosted = sorted(k for k, x in p.items() if x.process is self.host)
        self.assertEqual(hosted, ["alpha/1", "beta/1", "boom/1", "hangs/1", "noisy/1", "quits/1"])
        for k in ("own/1", "noops/1", "shell/1"):               # asked for its own; no OPS; not Python
            self.assertIsNone(p[k].game)
            self.assertIsNot(p[k].process, self.host)
        self.assertEqual(p["alpha/1"].game, "alpha")

    async def test_gam_with_no_plugin_running(self):
        out = await self.srv.cmd_gam(None, [], "GAM")
        self.assertIn("GAM alpha/1 standard alpha 2 2", out)
        self.assertIn("GAM shell/1 standard shell 2 2", out)
        self.assertEqual(out[-1], "OK")
        self.assertFalse(any(p.process.running for p in self.srv.plugins.values()))
        self.assertEqual(self.host.spawns, 1)

    async def test_started_on_demand_stopped_when_idle_and_again(self):
        alpha = self.srv.plugins["alpha/1"]
        r = await alpha.call(NEW)
        self.assertEqual(r["state"], {"by": "alpha", "game": None})     # "game" is taken out for the plugin
        self.assertTrue(self.host.running)
        self.assertEqual(self.host.spawns, 2)
        Limits.plugin_idle_secs = 0.2
        await self.srv.stop_idle_plugins()
        self.assertTrue(self.host.running)                      # not idle long enough
        await asyncio.sleep(0.3)
        await self.srv.stop_idle_plugins()
        self.assertFalse(self.host.running)
        self.assertEqual((await alpha.call(NEW))["state"]["by"], "alpha")       # started again
        self.assertEqual(self.host.spawns, 3)

    async def test_setting_overrides_the_default(self):
        await self.srv.plugins["alpha/1"].call(NEW)
        Limits.plugin_idle_secs = 3600
        self.srv.settings["plugins.idle"] = "0"
        await asyncio.sleep(0.05)
        await self.srv.stop_idle_plugins()
        self.assertFalse(self.host.running)

    async def test_kept_while_a_game_of_its_type_is_played(self):
        alpha, own = self.srv.plugins["alpha/1"], self.srv.plugins["own/1"]
        await alpha.call(NEW)
        await own.call(NEW)
        t = Table("1", alpha, "standard", {}, 2, 1)
        t.status = "playing"
        self.srv.tables[t.id] = t
        Limits.plugin_idle_secs = 0
        await asyncio.sleep(0.05)
        await self.srv.stop_idle_plugins()
        self.assertTrue(self.host.running)                      # serves a game being played
        self.assertFalse(own.process.running)
        t.status = "done"
        await self.srv.stop_idle_plugins()
        self.assertFalse(self.host.running)

    async def test_own_process_and_executable_on_demand(self):
        for key, by in (("own/1", "own"), ("noops/1", "noops"), ("shell/1", "shell")):
            p = self.srv.plugins[key]
            self.assertFalse(p.process.running)
            self.assertEqual((await p.call(NEW))["state"]["by"], by)
            self.assertTrue(p.process.running)
        self.assertFalse(self.host.running)

    async def test_restart_after_the_process_dies(self):
        alpha = self.srv.plugins["alpha/1"]
        await alpha.call(NEW)
        self.host.proc.kill()
        await self.host.proc.wait()
        self.assertEqual((await alpha.call(NEW))["state"]["by"], "alpha")
        self.assertEqual(self.host.spawns, 3)


class HostIsolation(PluginDir):
    async def test_one_plugin_raising_does_not_stop_the_others(self):
        p = self.srv.plugins
        await p["alpha/1"].call(NEW)
        pid = self.host.proc.pid
        self.assertEqual(await p["boom/1"].call(NEW), {"error": "plugin: boom"})
        self.assertIn("error", await p["quits/1"].call(NEW))            # sys.exit in a plugin: an error
        self.assertEqual((await p["noisy/1"].call(NEW))["state"]["by"], "noisy")    # its print kept off stdout
        self.assertEqual((await p["beta/1"].call(NEW))["state"]["by"], "beta")
        self.assertEqual((await p["alpha/1"].call(NEW))["state"]["by"], "alpha")
        self.assertEqual(self.host.proc.pid, pid)               # the same host all along
        self.assertEqual(self.host.spawns, 2)

    async def test_namespaces_apart(self):
        p = self.srv.plugins
        for name in ("alpha", "beta", "alpha", "boom", "beta"):
            r = await p[name + "/1"].call({"op": "info"})
            self.assertEqual(r["name"], name)

    async def test_a_hung_call_restarts_the_host(self):
        p = self.srv.plugins
        await p["alpha/1"].call(NEW)
        pid = self.host.proc.pid
        with self.assertRaises(PluginError):
            await p["hangs/1"].call(dict(NEW, options={"x": "1"}), timeout=0.5)
        self.assertEqual((await p["beta/1"].call(NEW))["state"]["by"], "beta")
        self.assertNotEqual(self.host.proc.pid, pid)
        self.assertEqual((await p["hangs/1"].call(NEW))["state"]["by"], "hangs")

    async def test_unknown_game_and_bad_line(self):
        self.assertEqual(await self.host.call({"op": "info", "game": "nosuch"}),
                         {"error": "host: no plugin nosuch here"})
        self.assertIn("error", await self.host.call({"op": "nosuch", "game": "alpha"}))


class ShippedPlugins(unittest.IsolatedAsyncioTestCase):
    async def test_every_shipped_plugin_is_hosted(self):
        srv = Server(os.path.join(ROOT, "plugins"), "test")
        await srv.load_plugins()
        try:
            self.assertEqual(len(srv.plugins), 10)
            host = srv.plugins["reversi/1"].process
            self.assertTrue(all(p.process is host for p in srv.plugins.values()))
            self.assertFalse(host.running)
            for key, p in sorted(srv.plugins.items()):
                if key == "gridwords/1" and not os.path.isfile(GW.DEFAULT_WORDS):
                    continue
                r = await p.call(dict(NEW, rules=p.info["rules"][0], players=p.info["min"]))
                self.assertIn("state", r, key)
            self.assertEqual(host.spawns, 2)
        finally:
            await host.close()

    async def test_plugin_alone_still_runs_its_own_process(self):
        p = Plugin(os.path.join(ROOT, "plugins", "reversi", "plugin.py"))
        try:
            self.assertEqual((await p.call({"op": "info"}))["type"], "reversi")
        finally:
            await p.process.close()


def set_words(path):
    """The word list as the plugin read it before: a set of str."""
    words = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            w = line.strip().upper()
            if 2 <= len(w) <= GW.SIZE and w.isascii() and w.isalpha():
                words.add(w)
    return words


class WordList(unittest.TestCase):
    def lexicon(self, text):
        return GW.Lexicon(text.splitlines(True))

    def test_small_lists_as_sets(self):
        for text in ("cat\ndog\nCAT\n", "zoo\nant\nbee\nant\nqi\n", "", "a\nabcdefghijklmnop\nit's\n",
                     "straße\nnaïve\n  word  \n\tTAB\n"):
            lex = self.lexicon(text)
            want = set()
            for line in text.splitlines():
                w = line.strip().upper()
                if 2 <= len(w) <= 15 and w.isascii() and w.isalpha():
                    want.add(w)
            self.assertEqual(lex, want, text)
            self.assertEqual(len(lex), len(want))
            self.assertEqual(sorted(lex), sorted(want))
            for w in want:
                self.assertIn(w, lex)

    def test_not_words(self):
        lex = self.lexicon("cat\ndog\n")
        for x in ("cat", "CA", "CATS", "", "C", "DOGCATDOGCATDOGCAT", "CÄT", 3, None, b"CAT"):
            self.assertNotIn(x, lex)

    @unittest.skipUnless(os.path.isfile(GW.DEFAULT_WORDS), "no plugins/gridwords/words.txt")
    def test_enable_same_as_the_set(self):
        old = set_words(GW.DEFAULT_WORDS)
        lex = GW.load_words(GW.DEFAULT_WORDS)
        self.assertEqual(len(lex), len(old))
        self.assertTrue(all(w in lex for w in old))
        self.assertEqual(sorted(lex), sorted(old))
        rng = random.Random(7)
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        probes = set()
        for w in old:                           # every word changed by one letter, and longer by one
            i = rng.randrange(len(w))
            probes.add(w[:i] + rng.choice(letters) + w[i + 1:])
            probes.add(w + rng.choice(letters))
            probes.add(w.lower())
        probes |= {"".join(rng.choice(letters) for _ in range(rng.randint(1, 17))) for _ in range(50000)}
        for x in probes:
            self.assertEqual(x in lex, x in old, x)
        self.assertLess(lex.nbytes(), 3 * 1024 * 1024)

    def test_unsorted_list_with_repeats(self):
        words = ["TREE", "ANT", "BEE", "ANT", "ZEBRA", "APPLE", "TREE", "QI", "AA", "QI"]
        lex = GW.Lexicon(w + "\n" for w in words)
        self.assertEqual(lex, set(words))
        self.assertEqual(list(lex), ["AA", "QI", "ANT", "BEE", "TREE", "APPLE", "ZEBRA"])


if __name__ == "__main__":
    unittest.main()
