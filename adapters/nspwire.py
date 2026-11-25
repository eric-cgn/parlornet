"""Apple's NetSprocket 1.7 on TCP/IP: the messages every NetSprocket game shares (docs/protocols.md#netsprocket).
No sockets here: bytes in, bytes out, so the extension
(adapters/netsprocket.py) and the tests share one reading of the format. Each game's own messages are in its
own module: adapters/netboxeswire.py for NetBoxes.

A message is a 28-byte big-endian header (version, what, from, to, id, when, messageLen) and a body; on TCP the
messages follow one another, each as long as its messageLen. NetSprocket's own messages have a negative what:
the join request, join approved or denied, player joined or left, game terminated. A game's messages use
positive values of its own choosing.

The join request carries no game ID on TCP. The extension knows a game by what it sends first after the
approval (adapters/netsprocket.py).
"""
import struct

NSP_VERSION = 0x10400000
NSP_HEADER = 28
MAX_MESSAGE = 16384

JOIN_REQUEST = 0x80000001
JOIN_APPROVED = 0x80000002
JOIN_DENIED = 0x80000003
PLAYER_JOINED = 0x80000004
PLAYER_LEFT = 0x80000005
GAME_TERMINATED = 0x80000007

SYSTEM_ID = -1          # NetSprocket's own messages come from the host's system (kNSpHostOnly)
HOST_ID = 1             # the host's player: the ParlorNet opponent
PLAYER_TYPE = 0         # NetBoxes joins and hosts with player type 0


class WireError(ValueError):
    pass


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def u32(v):
    return v & 0xFFFFFFFF


def pstr(text, size=32):
    """A Pascal string in a field of size bytes (Str31: 32): the length byte, the characters, zeros."""
    b = text.encode("mac_roman", "replace")[:size - 1]
    return bytes([len(b)]) + b + bytes(size - 1 - len(b))


def read_pstr(b):
    """The counted part of a Pascal string field; the bytes after it are often stale memory."""
    n = b[0] if b else 0
    return b[1:1 + n].decode("mac_roman", "replace")


def pack(what, frm, to, mid, when, body):
    return struct.pack(">IIiiIII", NSP_VERSION, u32(what), frm, to, mid, u32(when), NSP_HEADER + len(body)) + body


class Frame:
    __slots__ = ("what", "frm", "to", "mid", "when", "body")

    def __init__(self, what, frm, to, mid, when, body):
        self.what, self.frm, self.to, self.mid, self.when, self.body = what, frm, to, mid, when, body

    def __repr__(self):
        return "Frame(what=%08x from=%d to=%d id=%d len=%d)" % (self.what, self.frm, self.to, self.mid,
                                                                  NSP_HEADER + len(self.body))


class Reader:
    """A TCP stream -> NetSprocket messages: each says its own length (messageLen), and they follow one
    another on the stream."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= NSP_HEADER:
            ver, what, frm, to, mid, when, ln = struct.unpack_from(">IIiiIII", self.buf)
            if ver != NSP_VERSION:
                raise WireError("not a NetSprocket 1.7 message (version %08x)" % ver)
            if not NSP_HEADER <= ln <= MAX_MESSAGE:
                raise WireError("bad message length %d" % ln)
            if len(self.buf) < ln:
                break
            out.append(Frame(what, frm, to, mid, when, bytes(self.buf[NSP_HEADER:ln])))
            del self.buf[:ln]
        return out


def parse_join(body):
    """A join request's body -> (player name, password, player type). Name and password are Str31 fields."""
    if len(body) < 72:
        raise WireError("short join request")
    name = read_pstr(body[0:32])
    password = read_pstr(body[32:64])
    ptype = struct.unpack_from(">I", body, 64)[0]
    return name, password, ptype


def join_request(name, password, ptype=PLAYER_TYPE):
    """A joiner's request (for the tests' scripted clients): 103 bytes on the wire, as NetBoxes sends it."""
    return pstr(name) + pstr(password) + struct.pack(">II", ptype, 0) + bytes(3)


def _player(pid, name, ptype=PLAYER_TYPE):
    return struct.pack(">iI", pid, ptype) + pstr(name)


def join_approved(players, now, ptype=PLAYER_TYPE):
    """The host's approval: [(player id, name)], the host first. NetSprocket's private layout: the host's
    clock, a constant, the player and group counts, then 40 bytes a player (id, type, name), and the last
    field cut short by one byte, as NetSprocket sends it. A program's own host gives every player the type
    that program joins with."""
    body = struct.pack(">IIII", u32(now), 0xFFFFFC00, len(players), 0)
    for pid, name in players:
        body += _player(pid, name, ptype)
    return body + bytes(3)


def parse_join_approved(body):
    now, _, n, _ = struct.unpack_from(">IIII", body)
    out = []
    for i in range(n):
        pid, _ = struct.unpack_from(">iI", body, 16 + 40 * i)
        out.append((pid, read_pstr(body[24 + 40 * i:56 + 40 * i])))
    return out


def join_denied(reason):
    return pstr(reason, 256)


def player_joined(count, pid, name, ptype=PLAYER_TYPE):
    return struct.pack(">I", count) + _player(pid, name, ptype) + struct.pack(">II", 0, 0)


def parse_player_joined(body):
    """-> (player count, id, name)."""
    count, pid, _ = struct.unpack_from(">IiI", body)
    return count, pid, read_pstr(body[12:44])


def player_left(count, pid, name):
    return struct.pack(">Ii", count, pid) + pstr(name)


def parse_player_left(body):
    """-> (player count, id, name)."""
    count, pid = struct.unpack_from(">Ii", body)
    return count, pid, read_pstr(body[8:40])
