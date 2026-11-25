# Transports and bridges (draft 0)

The client protocol (protocol.md) needs only one of:

- **Stream profile:** a reliable, ordered byte stream (TCP, serial, modem, AppleTalk ADSP, IPX/SPX, DECnet). Lines as in protocol.md.
- **Transaction profile:** one request per packet, one reply back (AppleTalk ATP, UDP, Econet, AX.25 UI). This works because every request is self-contained and polling needs no server push. Bridges retry on loss; requests that change state carry a client sequence number so a retry is not applied twice.

A **bridge** is a separate transport-layer process: it speaks a network on one side and the core's trusted port for server extensions (loopback or LAN) on the other. The core never knows which network a player came from. Each bridge maps its network's identity to a handle for `HELO`: an AppleTalk NBP name or node, an Econet station number, a modem caller's typed name, and so on. Bridges also announce the service on networks that have service discovery.

Several bridges can run on one server at once; a Mac on LocalTalk, a CoCo on a serial line and a modern browser can sit at the same table.

## Candidate bridges

Pre-TCP networks and the machines that used them. None is designed yet.

| Network | Era / machines | Profile | Notes |
|---|---|---|---|
| Serial / RS-232 | everything | stream | A null-modem cable or a Pi serial port. |
| Dial-up modem | 70s-90s | stream | A modem pool or a Hayes emulator in front of the bridge; BBS-style login. |
| AppleTalk (LocalTalk, EtherTalk) | Mac, Apple II with a card, IIgs | stream via ADSP; transaction via ATP | NBP registers the server by name and zone so it appears in the network browser. |
| GlobalTalk | AppleTalk over the internet by AURP | as AppleTalk | See below. |
| IPX/SPX | DOS and Novell, late 80s | stream via SPX | The common DOS LAN game transport. |
| DECnet | VAX, PDP-11 | stream | Still found at museum events. |
| Econet | BBC Micro, school nets | transaction | Station numbers as identity. |
| NetBIOS / NetBEUI | IBM PC LANs | stream (sessions) | |
| AX.25 packet radio | amateur radio, 80s TNCs | transaction or stream | Callsign as identity. |
| FidoNet and BBS mail | pre-internet store and forward | async | Works for slow correspondence games using the `SIN`/event model. |
| Minitel and videotex | Minitel, Prestel | stream | Terminal-class, 40x24: a narrow rendering per type in the telnet extension. |
| Device-side adapters (FujiNet, DriveWire, Uthernet, WiFi modems) | Atari, CoCo, Apple II, C64 | stream | These present TCP or serial to the machine. They need no bridge of ours; they reach the plain port. |

Players whose machine has only a serial port can reach a parlor today through a WiFi modem, a Unix login or a bridge: [serial.md](serial.md).

A vintage computer festival may have a room of LocalTalk Macs, a CoCo or Atari corner with its own adapters and a DOS IPX room. One server on the event's LAN, with a bridge per network, puts all of them in the same lobby, even if no machine there speaks TCP.

## GlobalTalk

A server reachable on GlobalTalk is open to everyone on that network, so it is the "open internet" scenario in auth-federation.md with the AppleTalk bridge as the exposed edge: the bridge takes the abuse limits, the core stays on loopback. For development, bridges are tested on isolated local AppleTalk only, never on a live GlobalTalk connection.

## Open questions

- How much of the view and move format a 1-2 KB 8-bit client can actually hold; a compact encoding may be needed (the line format already limits width; Cannon Fire's terrain is the stress case).
- Display widths below 80 columns (40-column machines, Minitel): a narrow rendering per type in the telnet extension, or only graphical frontends there.
- Whether the transaction-profile retry rule (client sequence number on state-changing requests) belongs in protocol.md proper.
