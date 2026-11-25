import argparse
import asyncio
import os
import sys

from . import frontends
from .core import Limits, serve

here = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(prog="parlord", description="ParlorNet table host")
ap.add_argument("--host", default="127.0.0.1", help="address to listen on")
ap.add_argument("--port", type=int, default=6501, help="native protocol port")
ap.add_argument("--plugins", default=os.path.join(os.path.dirname(here), "plugins"))
ap.add_argument("--name", default="parlornet", help="server name in the greeting")
ap.add_argument("--auth", default="password", choices=["none", "password"],
                help="sign-in method when the database has none set (the admin panel sets it)")
ap.add_argument("--signup", default=None, choices=["none", "password", "email"],
                help="sign-up method (default: the sign-in method's own, none or password; email mails a password)")
ap.add_argument("--auth-label", default=None, help="printable name of what the method asks for")
ap.add_argument("--motd", default=None, help="file with the message of the day (the admin panel can set it)")
ap.add_argument("--settings-file", default=None,
                help="key=value lines added to the settings at start (for secrets such as email.smtp_password)")
ap.add_argument("--db", default=":memory:", help="SQLite file for players, tables and games")
ap.add_argument("--frontend-host", default="127.0.0.1", help="address of the frontend channel (TLS 1.3, keys)")
ap.add_argument("--frontend-port", type=int, default=6590, help="port of the frontend channel; 0 for none")
ap.add_argument("--keys", default=None,
                help="frontend key directory (python -m parlord.frontends); default: frontends/ beside --db")
ap.add_argument("--admin-port", type=int, default=0,
                help="admin socket on 127.0.0.1 for the admin adapter, TLS 1.3 with admin-role keys (6599; 0: none)")
ap.add_argument("--admin-host", default="127.0.0.1",
                help="address of the admin socket (only for an admin adapter in another container)")
ap.add_argument("--admin-telnet", type=int, default=6510,
                help="the admin adapter's port, named in the first-launch banner")
ap.add_argument("--turn-min", type=int, default=Limits.turn_min, help=argparse.SUPPRESS)
ap.add_argument("--sweep", type=float, default=10, help=argparse.SUPPRESS)
ap.add_argument("--plugin-idle", type=float, default=Limits.plugin_idle_secs,
                help="seconds without a call before a plugin process stops (the setting plugins.idle overrides it)")
ap.add_argument("--rate", type=float, default=Limits.rate_per_sec, help="requests per second per connection")
ap.add_argument("--burst", type=int, default=Limits.rate_burst, help="request burst per connection")
a = ap.parse_args()
if a.keys is None and a.db != ":memory:":
    a.keys = os.path.join(os.path.dirname(os.path.abspath(a.db)), "frontends")
if a.frontend_port and a.keys is None:
    a.frontend_port = 0             # nothing to authenticate frontends with: no frontend channel
if (a.frontend_port or a.admin_port) and not frontends.supported():
    sys.exit("parlord: the frontend channel needs Python 3.13 or later (TLS 1.3 pre-shared keys)")
if a.frontend_port and a.frontend_port == a.port:
    sys.exit("parlord: the frontend channel needs its own port")
Limits.rate_per_sec, Limits.rate_burst = a.rate, a.burst
Limits.turn_min, Limits.sweep_secs = a.turn_min, a.sweep
Limits.plugin_idle_secs = a.plugin_idle
try:
    motd = open(a.motd, encoding="utf-8").read() if a.motd else ""
    extra = {}
    if a.settings_file:
        for line in open(a.settings_file, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                extra[k.strip()] = v.strip()
    asyncio.run(serve(a.host, a.port, a.plugins, a.name, a.db, a.auth, a.auth_label, admin_port=a.admin_port,
                      motd=motd, extra_settings=extra, admin_telnet=a.admin_telnet,
                      frontend_host=a.frontend_host, frontend_port=a.frontend_port, keys=a.keys,
                      signup=a.signup, admin_host=a.admin_host))
except KeyboardInterrupt:
    pass
