"""NetSprocket extension: ParlorNet as the host of NetSprocket games on the Mac.

Apple's NetSprocket 1.7 carried network games for PowerPC Macs over TCP/IP: one copy of a game hosts, and the
others join it by its address and port. This extension is the host, on one port, for the game it knows:

    NetBoxes 1.1                Dots and Boxes (adapters/nsp_netboxes.py)

A player chooses the game's Join, TCP/IP, and types the parlor's name, this port, their ParlorNet name as the
player name and their password in the password field. The join, the sign-in and the approval are shared by
every NetSprocket game (adapters/nsphost.py, Join), so another game can be added beside NetBoxes as a module of
its own in GAMES. NetSprocket's join request carries no game ID on TCP, so the game is known by the first
message its program sends after the approval: NetBoxes' first is its version request (0x101). NetSprocket's
own messages are in adapters/nspwire.py.

    python3 -m adapters.netsprocket --core 127.0.0.1:6590 --key netsprocket.key --port 6512
"""
import argparse
import asyncio
import sys

from . import declare, nsp_netboxes
from . import nspwire as N
from .common import CoreClient, CoreError, Gate, keepalive, peer_addr
from .nsphost import IDLE_NOTE_SECS, IDLE_REPEAT_SECS, POLL_SECS, Join, log

PROTOCOL = ("NETSPROCKET", "NetSprocket, for NetBoxes", "Dots and Boxes (NetBoxes)")
GAMES = (nsp_netboxes,)     # each: recognises(frame), CLIENT_NAME, PLAYER_TYPE, Session
MAX_SESSIONS = 40


class Service:
    def __init__(self, args):
        self.args = args
        self.core_addr = (args.core_host, args.core_port)
        self.poll = args.poll
        self.turn = args.turn
        self.idle_note = getattr(args, "idle_note", IDLE_NOTE_SECS)
        self.idle_repeat = max(getattr(args, "idle_repeat", IDLE_REPEAT_SECS), 1.0)


async def serve(args):
    svc = Service(args)
    gate = Gate(MAX_SESSIONS)

    async def handle(reader, writer):
        ticket = gate.admit(peer_addr(writer), writer)
        if ticket is None:
            writer.close()
            return
        keepalive(writer)
        s = Join(svc, reader, writer, ticket, GAMES)
        log(s.sid, "connect")
        try:
            await s.run()
        except (EOFError, ConnectionError, asyncio.TimeoutError, CoreError, OSError, N.WireError) as e:
            log(s.sid, "ended: %s" % (e or type(e).__name__))
        except Exception as e:
            print("netsprocket session error: %r" % e, file=sys.stderr, flush=True)
        finally:
            ticket.release()
            log(s.sid, "disconnect")
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, args.host, args.port)
    declare.start(args, PROTOCOL)
    print("netsprocket adapter on %s:%d -> core %s:%d" % (args.host, args.port, args.core_host, args.core_port),
          flush=True)
    async with server:
        await server.serve_forever()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="adapters.netsprocket")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6512)
    ap.add_argument("--core", default="127.0.0.1:6590", help="the core's frontend channel")
    ap.add_argument("--key", default=None, help="this frontend's key file (python -m parlord.frontends)")
    ap.add_argument("--turn", default="30m", help="time per turn at the tables this extension makes")
    ap.add_argument("--idle-note", type=float, default=IDLE_NOTE_SECS,
                    help="seconds without a message before a chat line with the time left (0: never)")
    ap.add_argument("--idle-repeat", type=float, default=IDLE_REPEAT_SECS,
                    help="seconds between those lines while the silence lasts")
    declare.add_args(ap)
    ap.add_argument("--poll", type=float, default=POLL_SECS, help=argparse.SUPPRESS)    # tests
    a = ap.parse_args(argv)
    if a.key:
        CoreClient.key_file = a.key
    a.core_host, _, p = a.core.rpartition(":")
    a.core_port = int(p)
    return a


if __name__ == "__main__":
    try:
        asyncio.run(serve(parse_args()))
    except KeyboardInterrupt:
        pass
