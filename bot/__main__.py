"""python -m bot: the bot player (docs/bot.md).

    python -m bot --core 127.0.0.1:6590 --key /data/frontends/bot.key

The key is the bot's own server extension key, enrolled like the others
(python -m parlord.frontends --keys DIR add bot); the container's start script
enrolls it when the bot is turned on in the admin menus (Adapters)."""
import argparse
import asyncio
import os
import sys

from parlord import frontends

from . import service
from .games import gridwords


def pair(text):
    a, _, b = text.partition(",")
    return float(a), float(b or a)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bot", description="The ParlorNet bot: a player you can invite to a table")
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's extension channel, host:port")
    ap.add_argument("--key", default=os.environ.get("PARLOR_FRONTEND_KEY"), help="the bot's key file (named bot)")
    ap.add_argument("--names", default=",".join(service.NAMES), help="the bot names to play as")
    ap.add_argument("--cap", type=int, default=20, help="tables at once per bot name")
    ap.add_argument("--think", type=pair, default=(0.5, 2.0), help="seconds before a turn: least,most")
    ap.add_argument("--pace", type=float, default=0.12, help="least seconds between two requests on a connection")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between looks for invitations")
    ap.add_argument("--gridwords-secs", type=float, default=gridwords.TIME_CAP, help="Grid Words search time a turn")
    ap.add_argument("--host", help=argparse.SUPPRESS)     # start.py gives every extension these two
    ap.add_argument("--port", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if not a.key:
        ap.error("--key is needed: the bot's key file (python -m parlord.frontends --keys DIR add bot)")
    if not frontends.supported():
        sys.exit("bot: the extension channel needs Python 3.13 or later (TLS 1.3 pre-shared keys)")
    host, _, port = a.core.rpartition(":")
    gridwords.TIME_CAP = a.gridwords_secs
    names = [n.strip() for n in a.names.split(",") if n.strip()]
    bot = service.Bot(host or "127.0.0.1", int(port), a.key, names, a.cap, a.think, a.pace, a.poll,
                      log=lambda text: print(text, flush=True))
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
