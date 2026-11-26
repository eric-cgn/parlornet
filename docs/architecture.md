# Architecture and layers (draft 0)

There are four layers, and each talks only to the one below it through a narrow interface.

1. **Games** (plugins, docs/plugins.md): rules and state only. They know nothing of sockets, users, servers or federation; they are pure functions that return state and events.
2. **Table host** (the core server): owns tables. Each table is one game instance, and the server hosting it is authoritative for it: it runs the plugin, orders moves, keeps the log, ends the game. Servers host whatever games they choose. The core knows seats and players as opaque ids; it knows no transport.
3. **Transport layer**: moves requests and events between players (or other servers) and the table host. Everything that is not a game or a table lives here, as separate swappable pieces:
   - the 8-bit line protocol on a TCP port (docs/protocol.md),
   - bridges (TLS, modern identity, rate limits) for the open internet,
   - server-to-server federation (see below),
   - server extensions (docs/adapters.md): web, telnet and game-network extensions, each speaking one protocol to players' frontends; they run beside the server but are not part of it.
4. **Notification layer**: human-readable reach-outs such as "it's your turn", via ActivityPub/Mastodon posts, email or anything else. It is optional, observes table events and is never part of game or federation traffic. The core offers server extensions a feed of notices for it (`NFY`, protocol.md, Notices); ParlorNet Play's push notifications are the first to use it (play-notifications.md).

A move takes the path below. A period program speaks its own protocol to the server extension for it, which passes the move to the core; a program that speaks ParlorNet's own protocol reaches the core directly. The core hands the move to the game's plugin and sends the result back the same way.

![A move's path: from the player's program through a server extension to the core and the game's plugin](../static/img/diagram-request-path.gif)

## Identity and the extension channel

Who a connection is belongs to one component, the accounts component (`parlord/accounts.py`), which runs the operator's sign-in method (`parlord/auth.py`) and owns sessions. Neither the table host's game code nor any server extension checks a credential or keeps one. docs/security-design.md has the threat model. In short:

- 8-bit and native clients sign in on the public port (6501, plain text) with the method's credential; the sign-in lasts as long as the connection.
- Server extensions (web, telnet, IOS, FIBS, Outland, DOoM, GameMaster, NetSprocket, batnav, Awale, NetLuff, Worthy Opponent, RSCARDS, Play, the bot, later bridges on other hosts) reach the core on the extension channel: the same protocol inside TLS 1.3 with a pre-shared key per server extension (`python -m parlord.frontends` enrolls, rotates and revokes keys). They relay the player's answers to the method's questions and hold the session handle the accounts component returns; only there can a session be resumed (`SES`).
- The admin adapter reaches the core's admin socket over the same kind of channel with an admin-role key; ordinary extension keys are refused there.
## Frontends and server extensions

- A **frontend** is the program a player uses: a web browser, a telnet program, a 1990s game client (Reversi The Eclipse, MacFIBS, FIBS/W, xfibs, the Outland Gateway), or a native ParlorNet client. It runs on the player's machine, outside the server.
- A **server extension** is a transport-layer program that speaks one protocol to frontends and the native protocol to the table host: web (HTTP, 6500), telnet (6502), IOS (6503), FIBS (6505), Outland (6506), DOoM (6507), GameMaster (6508), NetSprocket for NetBoxes (6512), ParlorNet Play (6513), batnav (6514), IRC for Awale (6515), Networdz (6516), NetLuff (6517, published as 1744 too, NetLuff's own port), ICDS for xcheckers (6518), Worthy Opponent (6519, dialed by modem) and RSCARDS (6520, by modem or serial line). The native protocol (6501) is served by the table host itself. In the code, server extensions are the `adapters/` package. Each declares its protocol's key, port, name and description to the table host when it connects, and the table host lists what is connected (`PRL`). docs/protocols.md describes each protocol for players.

In a running parlor these are separate processes in one container, as below. [Run a server](http://docs.parlornet.org/running.html) and [deploy/README.md](../deploy/README.md) show how to set one up.

![A parlor: the players' programs, the reverse proxy for ParlorNet Play, and the processes in the container](../static/img/diagram-parlor.gif)

## Federation is not notification

- **Federation** is machine-to-machine: server identity, remote players reaching a table on another host, a federated lobby. Identity is `user@domain`, verified by the player's home server with signed requests. Each table has exactly one host; there is no replicated game state.
- **Mastodon/ActivityPub posts** are for humans and belong to the notification layer. Federation may borrow ActivityPub's mechanics (actors, webfinger, HTTP signatures) but is its own protocol and does not depend on Mastodon compatibility.

## Rules of the seams

- A game never learns who a player is, only a seat number.
- The table host never learns how a player connected.
- A transport never interprets game moves; it carries move tokens.
- A transport never checks a credential; it relays the player's answers and holds a session handle.
- Only the table host decides: state, legality, randomness, hidden information, results, clocks and identity. Frontends and server extensions are believed about nothing but the player's own choices; [design/trust.md](design/trust.md) has the rules they follow and checks each service against them.
