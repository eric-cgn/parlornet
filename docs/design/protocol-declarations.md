# Protocol declarations

Each server extension tells the core which protocol it serves, and the core lists what is connected. The web site's Protocols page, the welcome page's Other ways in, and any other frontend that wants to say "this server speaks these protocols" read that list instead of keeping a table of their own. The requests are in [protocol.md](../protocol.md#protocols-pro-prd-prl).

## What is declared

A protocol is declared by its key, the port players connect to, a name and a one-line description:

| Key | Declared by | Name | Description |
|---|---|---|---|
| `HTTPS` | Play extension | ParlorNet Play | The modern client for today's browsers and phones |
| `HTTP` | web extension | HTTP and HTML 3.2 | HTML 3.2 pages for any browser with tables and forms |
| `PARLORNET/1` | the core itself | ParlorNet protocol (PARLORNET/1) | ParlorNet's own protocol, for programs written for it |
| `TELNET` | telnet extension | Telnet with ANSI escape codes | Full-screen menus and boards for terminal programs |
| `IOS` | IOS extension | Internet Othello Server (IOS) | Reversi for clients of the Internet Othello Server |
| `FIBS` | FIBS extension | First Internet Backgammon Server (FIBS) | Backgammon for vintage FIBS clients |
| `OUTLAND` | Outland extension | Outland | Backgammon and Reversi for the Outland Gateway on Macs |
| `DOOM` | DOoM extension | DOoM crossword-game server | Grid Words for DOoM clients: BobGrid and telnet |
| `GAMEMASTER` | GameMaster extension | GameMaster | Nine games for GameMaster on 68k Macs |
| `NETSPROCKET` | NetSprocket extension | NetSprocket, for NetBoxes | Dots and Boxes (NetBoxes) |
| `BATNAV` | batnav extension | Batalla Naval (batnav) | Naval Combat for the batnav clients of 1995-98 |
| `ICDS` | ICDS extension | Internet Checkers and Draughts Server (ICDS) | Checkers for xcheckers and other ICDS clients |
| `NETLUFF` | NetLuff extension | NetLuff, for the Mac's NetLuff 1.4 | Five in a Row for NetLuff 1.4 on Macs |
| `WORTHY` | Worthy Opponent extension | Worthy Opponent, for DOS by modem | Reversi, Checkers, Five in a Row for Worthy Opponent DOS |
| `RSCARDS` | RSCARDS extension | RSCARDS, for GEnie's graphics front ends | Ten games for GEnie's RSCARDS front ends, by modem |

Each extension holds its own triple as `PROTOCOL` in its module; the port comes from its arguments (`--public-port`, else `--port`), so a container port published under another number is declared as the number players use.

The key is the protocol's name across ParlorNet: the docs site's protocols reference has an entry for each key at `protocols.html#KEY`, and the server's Protocols page, the docs site's client tables and setup pages all link there. In the anchor, any character of the key but letters, digits and `-` is written `-` (`PARLORNET/1` is `#PARLORNET-1`), so the fragment is plain for old browsers (`webstyle.protocol_anchor`).

## On the wire

- `PRO key port name` and `PRD key text`, on the extension channel only. Two requests rather than one because of the 80-byte line: a key of up to 12 characters, a port and a name of up to 48 fit one line, and a description of up to 56 fits another.
- `PRL` lists them, on either listener and before signing in, as the same `PRO` and `PRD` lines after a `DOC url` line: the docs site's address (`site.docs_url`), so that a frontend that links a key needs nothing else. The core's own `PARLORNET/1` is in the list with its public port.
- The extension channel's verbs are only for enrolled extensions (`ERR NOTFRONTEND` on the public port), so a player cannot add a protocol to the list.

## Lifetime

A declaration belongs to the connection that made it and ends when that connection closes, so an extension that stops, or loses the core, drops off the list at once, and nothing has to expire it. Extensions open core connections per player (the web per request), so a declaration cannot ride on those: each extension keeps one connection of its own for it (`adapters/declare.py`). It sends its declaration again every 5 minutes, which keeps the connection under the core's idle limit (15 minutes) without any change to that limit, and connects again after the core restarts. That connection counts against the extension's share of the extension channel like any other.

Two connections may declare the same key and port (two copies of an extension): it is listed once. The same key on two ports is listed twice.

## Compatibility

An extension that does not declare works as before and is not listed. A core older than these requests answers `PRO` with `ERR UNKNOWN`; the extension's declaring task then stops, and its own pages work without the list (the web's Protocols page says the server has not listed its protocols). Clients on the public port that never send `PRL` see no difference.

## Where it is shown

- The web extension asks `PRL` with its other start-up information (every 30 seconds) and again for the Protocols and welcome pages when its copy is more than 5 seconds old. The Protocols page is a table of key (linking to the docs site), port, name and description, in port order; the welcome page has one line each, with the `telnet host port` command for `TELNET`.
- The docs site's protocols reference (`docsite/protocols.py`) is the full list of keys, with the usual ports, the history and the programs for each. It is static; a test checks that every key an extension declares has an entry there, and that the table at the top of [protocols.md](../protocols.md) agrees with it.
