"""The plugin host: the Python game plugins in one process (docs/plugins.md).

    python3 pluginhost.py PLUGIN_FILE ...

Each plugin file is a game's plugin.py; its game name is its directory's name
(plugins/reversi/plugin.py is reversi). The host speaks the plugin protocol on
stdin and stdout, one JSON object per line, and each request names its plugin
with "game": NAME. The plugin answers it as it would in a process of its own;
"game" is taken out of the request first.

A plugin is imported on its first request, as a module of its own
(importlib, named parlor_plugin_NAME), so the plugins' names never meet. It
answers through its OPS table (op name -> function of the request), the one
its own main loop uses. A plugin that raises, or cannot be imported, answers
{"error": ...} for that call, and the host goes on serving the others. A
plugin's print goes to stderr, never into the replies, and its stdin is empty
(the requests are the host's). The core bounds
the time of every call: a call that hangs gets the host killed and started
again, as a plugin's own process would be.
"""
import importlib.util
import io
import json
import os
import sys


def plugin_name(path):
    return os.path.basename(os.path.dirname(os.path.abspath(path)))


class Host:
    def __init__(self, paths):
        self.paths = {plugin_name(p): p for p in paths}
        self.ops = {}

    def load(self, game):
        """The plugin's OPS table, importing it the first time."""
        ops = self.ops.get(game)
        if ops is None:
            modname = "parlor_plugin_" + game
            spec = importlib.util.spec_from_file_location(modname, self.paths[game])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                del sys.modules[modname]
                raise
            ops = getattr(mod, "OPS", None)
            if not isinstance(ops, dict):
                del sys.modules[modname]
                raise LookupError("%s has no OPS table to be hosted by" % game)
            self.ops[game] = ops
        return ops

    def answer(self, line):
        """One request line -> its reply object."""
        try:
            req = json.loads(line)
            game = req.pop("game", None)
            if game not in self.paths:
                return {"error": "host: no plugin %s here" % game}
            return self.load(game)[req["op"]](req)
        except (Exception, SystemExit) as e:          # one plugin's failure is that call's answer
            return {"error": "plugin: %s" % e}


def main(argv):
    if sys.path and sys.path[0] == os.path.dirname(os.path.abspath(__file__)):
        del sys.path[0]                     # plugins import the standard library, not parlord's modules
    host = Host(argv)
    # The replies get a descriptor of their own; plugins see an empty stdin (a main loop run on import
    # ends) and a stdout that is stderr, down to descriptor 1.
    inp = sys.stdin
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdin, sys.stdout = io.StringIO(), sys.stderr
    for line in inp:
        line = line.strip()
        if not line:
            continue
        reply = host.answer(line)
        try:
            text = json.dumps(reply, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            text = json.dumps({"error": "plugin: reply is not JSON: %s" % e})
        out.write(text + "\n")
        out.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
