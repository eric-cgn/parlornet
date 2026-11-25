"""A server extension tells the core which protocol it serves (docs/protocol.md, Protocols;
docs/design/protocol-declarations.md).

Each extension that players connect to has a PROTOCOL: (key, name, one-line description), such as
("IOS", "Internet Othello Server (IOS)", "Reversi for clients of the Internet Othello Server"). At start it
calls start(args, PROTOCOL): one connection to the core is kept open for the declaration, which sends

    PRO key port name
    PRD key description

and sends them again every EVERY seconds, which also keeps the connection from going idle. The core lists
the protocol (PRL) while that connection is open, so a stopped extension drops off the list. When the core
goes away the connection is made again after RETRY seconds. A core that does not know PRO (ERR UNKNOWN:
older than the declarations) is left alone.

port is the port players connect to: --public-port when the extension listens on another one (a container
port published under another number, as Outland's 4000 is published as 6506), else --port.
"""
import asyncio
import ssl

from .common import CoreClient, CoreError

EVERY = 300         # seconds between declarations: well under the core's idle limit (Limits.idle_secs, 900)
RETRY = 10          # seconds before connecting again after the core went away
_tasks = set()      # the running declarations (a task nobody holds may be collected)


def add_args(ap):
    ap.add_argument("--public-port", type=int, default=None,
                    help="the port players connect to, when it is not --port (a published container port); "
                         "declared to the core")


def requests(protocols):
    """The request lines that declare protocols: [(key, port, name, description)] -> PRO and PRD lines."""
    out = []
    for key, port, name, desc in protocols:
        out.append("PRO %s %d %s" % (key, port, name))
        if desc:
            out.append("PRD %s %s" % (key, desc))
    return out


async def keep_declared(host, port, protocols, key_file=None, every=EVERY, retry=RETRY):
    """Declare protocols to the core at host:port and keep them declared, until canceled."""
    while True:
        core = None
        try:
            core = await CoreClient.connect(host, port, key_file)
            while True:
                for line in requests(protocols):
                    _, final = await core.req(line)
                    if final.startswith("ERR UNKNOWN"):
                        return
                try:
                    got = await asyncio.wait_for(core.r.readline(), every)
                except asyncio.TimeoutError:
                    continue
                if not got:
                    break           # the core closed the connection
        except (OSError, CoreError, ssl.SSLError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            if core is not None:
                core.close()
        await asyncio.sleep(retry)


def start(args, protocol, key_file=None):
    """Start declaring protocol (key, name, description) for an extension with these arguments (--port,
    --public-port, --core). Returns the task."""
    key, name, desc = protocol
    port = getattr(args, "public_port", None) or args.port
    t = asyncio.ensure_future(keep_declared(args.core_host, args.core_port, [(key, port, name, desc)], key_file))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t
