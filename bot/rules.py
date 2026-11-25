"""The games' own rules code, for the bot's look-ahead: each game's plugin
(plugins/NAME/plugin.py) is a set of pure functions of the state, so the bot
imports it and runs those functions on a state it rebuilds from the view. The
server stays the judge: every move still goes through MOV (and CHK where the
game has no move list). A plugin is loaded the first time a game needs it, once
per process."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.environ.get("PARLOR_PLUGINS") or os.path.join(ROOT, "plugins")
_loaded = {}


def plugin(name):
    """The plugin module of a game (its directory name under plugins/), loaded once."""
    m = _loaded.get(name)
    if m is None:
        path = os.path.join(PLUGINS, name, "plugin.py")
        spec = importlib.util.spec_from_file_location("pnbot_plugin_" + name, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        _loaded[name] = m
    return m
