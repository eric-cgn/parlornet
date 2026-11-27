# ParlorNet client protocol (draft 1)

Any machine that can open a byte stream and parse a line can play. The protocol carries typed game state and moves; it has no generic board and no lowest-common-denominator display. Clients are written against game types (below). Terminals reach games through the telnet server extension, which knows each type and renders it for a character screen; it is a server extension (adapters.md), not part of the protocol.

[pnp.md](pnp.md) is a guide for people writing their own frontend, with sample sessions recorded from a real server for the protocol and for every game.

The server is the only authority: it rolls every die, deals every tile, keeps every hidden thing and accepts or refuses every move, and a client may check a move locally for convenience but never decides. What clients and server extensions must and must not do is in [design/trust.md](design/trust.md).

## Framing

- Messages are 7-bit ASCII, one per line. On the public port every line, both ways, ends with the line ending the client chose when it connected: CR LF, CR, LF CR or LF (Connecting, below). On the extension channel lines end with CR LF.
- A line is at most 80 bytes including its ending, and servers never send a longer one. A longer line from a client is `ERR TOOLONG`, and the connection is closed.
- Fields are separated by single spaces. Verbs are 3 or 4 upper-case letters. Names and ids are `[A-Za-z0-9_-]`, max 12 chars; session ids and sign-in keys are 22 of `[A-Za-z0-9_-]`. The value of `AUT` is the rest of the line as typed (a password may hold spaces), without leading and trailing spaces, and at most 64 characters of it are used.
- Input is case-insensitive; verbs are sent in upper case.
- There is no TLS, compression or binary data; TLS and tunnels are a transport matter.

## Connecting (public port)

On the public port (6501) the client speaks first. Its first line is the protocol's name and version, `PARLORNET/1`, and the byte or two after the `1` set the connection's line ending:

| After `PARLORNET/1` the client sends | Line ending |
|---|---|
| CR, then LF within 250 ms | CR LF |
| CR, and no LF within 250 ms | CR |
| LF, then CR within 250 ms | LF CR |
| LF, and no CR within 250 ms | LF |

- The opening is matched in any case (`parlornet/1` works too).
- Up to 10 bytes of anything may come before `PARLORNET/1` and are ignored: a stray Return, line noise, or what a modem or a shell leaves behind.
- The server sends nothing until the opening comes. Then it sends the greeting, and from then on it ends every line it sends with the connection's ending.
- The client has 20 seconds from connecting to send the opening.
- If the opening does not come, the server sends one error line, ending CR LF since no ending is set, and closes the connection. A different version (`PARLORNET/2`) is `ERR VERSION wrong version`. Anything else is `ERR PROTOCOL wrong protocol`: more than 10 bytes before `PARLORNET/1`, bytes that cannot become it (an HTTP request or telnet negotiation, say), a byte after the `1` that is not CR or LF, or no opening within the 20 seconds. Each line goes on with `; see ` and the address of this page on the operator's docs site (`site.docs_url`) when it is set and the line still fits 80 bytes, for example `ERR PROTOCOL wrong protocol; see http://docs.parlornet.org/docs/protocol.html`.
- A client should send a two-byte ending in one write, so the second byte is not late.
- After that, each line the client sends must end with exactly that ending. A CR or LF that is not part of the connection's ending ends a line that is refused with `ERR NEWLINE lines end with E here` (`E` is `CR LF`, `CR`, `LF CR` or `LF`) and not carried out; the connection stays open. An empty line with the right ending is ignored.
- No other bytes are treated specially: telnet negotiation (IAC) is not understood on this port (the telnet extension has its own, 6502).
- A server that is full sends `ERR BUSY server is full` (ending CR LF) at once on connecting and closes the connection.
- The extension channel does not use these rules: there the server sends the greeting as soon as the TLS handshake is done, and lines end with CR LF.
- A dial-in gateway may put a PROXY header before the opening (Dial-in gateways, below).

## Dial-in gateways (PROXY)

A dial-in gateway answers calls from modems and carries each call to the public port as a TCP connection. The operator lists the gateways' addresses or networks in the setting `dialin.gateways` (`PARLOR_DIALIN_GATEWAYS`), separated by commas, the same way the web's proxies are listed in `web.proxies`. Without the setting, no connection comes from a gateway.

A gateway may send a header of the PROXY protocol, version 2, before anything else on the connection. This is the binary header that the HAProxy project describes in its `proxy-protocol.txt`. It has a 12-byte signature, a byte with the version and the command, a byte with the address family, a 16-bit length, the addresses and then TLVs. The server reads these TLVs and passes over any others:

| Type | What it holds |
|---|---|
| `0xE0` | the caller ID, as text |
| `0xE1` | the number the caller dialed |
| `0xE2` | the modulation, such as `V.34` |
| `0xE3` | the rate |
| `0xE4` | the seconds the call had already been up when the gateway connected, as a 32-bit big-endian number |

The header counts toward the 20 seconds the client has for its opening, and the client's opening comes after it as usual. With the PROXY command, the connection carries a call and is in the dial-in class (Time allowance, below). When the header names a source address, the per-address limits count that address. Otherwise they count the gateway's. With the LOCAL command the connection is the gateway's own, a health check for example, and it is in the default class. The server keeps the text TLVs with the connection in memory only. It never stores or logs them.

A PROXY header from an address that is not listed is a protocol error. So is a header from a listed gateway that is cut short, has more than 512 bytes after its first 16, or is not well formed. Either way the server sends `ERR PROTOCOL wrong protocol`, as for any other bad opening, and closes the connection. A listed gateway may also connect without a header, and then the connection is in the default class.

## Greeting

After the client's opening (public port), or straight after the TLS handshake (extension channel), the server sends one line: `PARLORNET/1 name AUTH=signin SIGNUP=signup [LABEL=label]`, where `name` is the server's short name, `signin` the sign-in method (`none` or `password`; parlornet.org sends `AUTH=password SIGNUP=email`), `signup` the sign-up method (`none`, `password` or `email`) and `label`, if the operator set one, the printable name of the credential, with `_` for spaces (shortened, or left out, so the line fits 80 bytes). A client checks the first word and the version before anything else, and signs in as the server asks (docs/accounts.md): `USER name` to sign in, `REG name` to sign up (or `HELO name` for either), then answers each `ASK` with `AUT`.

## Two listeners

- **The public port** (6501): plain text, for any client. The client sends `PARLORNET/1` first, whose line ending sets the connection's (Connecting, above). Sign-in is with the method's credential only; the sign-in lasts as long as the connection. `SES` is refused (`ERR NOTFRONTEND`).
- **The extension channel** (6590, loopback in the container): the same protocol inside TLS 1.3 with a pre-shared key per server extension (docs/security-design.md). Only enrolled server extensions (web, telnet, IOS, ...) connect. After sign-in the reply carries a session id, which `SES` resumes on another connection of the same server extension.

## WebSocket (the modern client)

A browser cannot open a TCP connection, so the Play extension (adapters/playserver.py) carries PARLORNET/1 over a WebSocket (RFC 6455) at `/play/ws` on its own port (6513), for ParlorNet Play (docs/modern-client.md) and any other program that can only speak WebSocket. It is usually reached through a reverse proxy that terminates TLS, as `wss://` the parlor's name `/play/ws`. Behind each WebSocket is one connection of the Play extension to the core's extension channel; the extension relays lines and keeps no rules of its own (adapters/wsbridge.py).

- **Handshake.** `GET /play/ws HTTP/1.1` with `Upgrade: websocket`, `Connection: Upgrade`, `Sec-WebSocket-Version: 13` and a 16-byte `Sec-WebSocket-Key`. The `Origin` header must be this site, or the answer is 403: with the setting `play.public_url`, that URL's origin (such as `https://parlornet.org`); without it, `http://` or `https://` and the same host and port as `Host`. So a page of another site cannot open it, even with the visitor's cookie. Another version is 426, a malformed handshake 400. A client may offer the subprotocol `parlornet1`, and gets it back.
- **Framing.** Each text message from the client is one request line, without a line ending: printable ASCII, at most 78 characters (80 bytes with the CR LF the extension channel adds). Each line the server sends is one text message. A longer line is answered `ERR TOOLONG lines are 80 bytes at most` and the WebSocket is closed (1009). The client's frames must be masked (else 1002); binary frames close it (1003), text that is not printable ASCII too (1007), a frame of more than 125 bytes too (1009). Fragmented text messages are taken, up to 125 bytes in all; pings are answered.
- **Greeting.** The server speaks first, with the core's greeting line; there is no opening to send first (that is the public port's rule).
- **What differs from the extension channel.** The Play extension sends `SRC` with the browser's address before anything else, for the per-address limits: the peer's, or, from a proxy listed in the setting `play.proxies`, the last address in `X-Forwarded-For` that is not a listed proxy. The extension channel's own requests are refused: `SRC`, `PRO`, `PRD`, the table data requests `TDL`, `TDS` and `TDD`, the notices `NFY`, and `SES` with a session id answer `ERR NOTFRONTEND`. A sign-in's reply is `OK -`, as on the public port, never the session id; before it comes a data line `HND handle`, a one-time handle (60 seconds) that the page trades for the Play extension's own session cookie, `pnplay` (`Path=/play/`, `HttpOnly`, `SameSite=Strict`, and `Secure` when the public URL is https), by `POST /play/session` with `h=handle`; a POST whose `Origin` is another site is refused. `SES` alone resumes the session in that cookie, if the handshake carried one: `OK name`, or `ERR NOSESSION`. Staying signed in (below) works the same way: the reply to `KEY NEW` is `OK -`, with an `HND handle` line before it that `POST /play/session` trades for a second cookie, `pnplaykey`, with the same attributes and a long life, so the key never reaches the page either. When `SES` alone finds no session to resume and the handshake carried that cookie, the extension signs in with its key itself and answers `OK name`, with an `HND` line for the new session first, or `ERR NOKEY` when the key was refused. `POST /play/session` with `out=1` clears both cookies. The web extension's cookie (`pn`) is neither read nor set. `PRF` uses the namespace Play shares with the 1997 pages (`web`; Data kept per extension and player, below).
- **Limits and timeouts.** At most 20 WebSockets still signing in (4 from one address, IPv6 by its /48; a fifth closes that address's least active), and 50 signed in; past those, `ERR BUSY server is full` and a close. A WebSocket that has not signed in after 10 minutes is closed (`ERR IDLE sign in first`). The server pings every 30 seconds and closes a client silent for 75. The client may send at most 20 frames a second (a burst of 60); the core's own rate limit and idle time (15 minutes without a request) apply as on any connection.
- **Over HTTPS.** A page loaded over HTTPS must use `wss://`. The web extension speaks plain HTTP; a TLS terminator in front of it must pass the upgrade through, and send the `Origin` and `Host` it received.

```
C> (handshake)
S< PARLORNET/1 parlornet AUTH=password SIGNUP=password
C> USER ann
S< ASK password Password secret cred
C> AUT password correct horse
S< HND Wq1aT0x7f3VbQ0x5hY2LmA
S< OK -
C> LST
S< TAB 4 reversi/1 standard 1/2 open ann,- turn=86400
S< OK
```

## Client requests

A reply is any number of data lines (`GAM`, `TAB`, `V`, `EVT` ...) ending with exactly one line starting `OK`, `ERR code text` or, during sign-in, `ASK field label [secret]`. A client reads until it sees one of them. An `ERR` text may start with `wait=N`, the seconds to wait before trying again (for example after a wrong password). On a public-port connection that asked for pages with `PAG`, a long reply comes in pages, and every page but the last ends with the line `CNT` (Paged replies, below).

| Request | Meaning |
|---|---|
| `HELO name` | Sign in, or (a name not yet taken) sign up. The reply is `INF` lines and an `ASK` (answer with `AUT`), `ERR`, or `OK` (below). A second word is refused under sign-in `password` (`ERR SYNTAX`: the password is asked for) and ignored under `none`. Wrong answers lock the name (`ERR LOCKED wait=N ...`). With as many players signed in as the server allows (`players.online_max`, 200; bots not counted), a name not signed in already is refused at once: `ERR BUSY wait=60 the server is full (200 players), try again later` (also for `USER` and `REG`). |
| `USER name` | Sign in only: as `HELO` for a name that has an account (the same steps and lockouts); a name with no account is `ERR NOPLAYER` and nothing is made. Unknown names count against the client address as sign-ups do (20 in 10 minutes, then `ERR BUSY wait=N`). |
| `REG name` | Sign up only: the sign-up method's steps for a new name (under sign-up `email` it goes straight to the address, without `HELO`'s yes/no question). A name that has an account is `ERR NAMEINUSE`; a reserved word (`new`, `guest`: some frontends take them at their login prompt; `parlornet`, the server's name and staff titles such as `admin`, docs/accounts.md) is `ERR RESERVED`, also on `HELO`, and a new name must have at least one letter and have `-` and `_` only between letters or digits (`ERR NAMERULE`). Under sign-up `email` the address step makes a pending account and mails it a temporary password, and the dialog goes on with a question for it (docs/accounts.md). While as many pending accounts as the server allows are waiting, a new sign-up (by `REG`, or `HELO` for a free name) is refused at once with `ERR BUSY wait=N Too many sign-ups in progress; try again in ...`; a pending account holds its name, so a sign-up for it is `ERR NAMEINUSE` (docs/accounts.md, Sign-up limits). The bot's names (`bot`, `bot1` to `bot3`, docs/bot.md) are `ERR RESERVED` on `USER`, `HELO` and `REG` for every connection but the bot's own. `HELO` keeps doing both, for clients written before these two. |

| `AUT field value` | The answer to an `ASK` during sign-in, sign-up, recovery or a password change (docs/accounts.md). At the password question of a sign-in, `AUT key key` answers with a sign-in key instead of the password (Staying signed in, below): `OK` as for the password, or `ERR NOKEY` when the key is refused. Before an `ASK` the reply may carry `INF text` lines to show the player (info). `ASK field label [secret] [yn] [cred] [new]`: `secret` means do not echo; `yn` means a yes/no question, answered `y` or `n`; `cred` means it asks for the account's credential (or a password being chosen at sign-up), the only question a password the client already supplied may answer; `new` means a new password is being chosen. After a right temporary password the server asks for a new password (`new`, not `cred`) before signing in; a client that declared with `CAN` that it cannot show that step gets `ERR NEWPASS Sign in on the web or by telnet first to choose your password.` |
| `CAN kind[,kind...]` | Before `HELO`: the sign-in step kinds this client can show, of `text`, `secret`, `yn`, `info` (all four if never sent). A step needing another kind is refused with `ERR CANNOT`. `OK kinds`. |
| `OK` after sign-in | Public port: `OK -`. Extension channel: `OK sid`, the session id: a handle for the server extension, never a credential, never shown to the player. |
| `SRC address` | Extension channel only, before `HELO`: the IPv4 or IPv6 address of the client this connection serves, for the per-address limits on sign-up and mail (docs/accounts.md). `OK`, `ERR NOTFRONTEND` (public port, where the connection's own address is used), `ERR SYNTAX`. |
| `SES sid` | Extension channel only: resume a session this server extension was issued. `OK name`, or `ERR NOSESSION` (unknown, expired, ended, another server extension's, or issued under a key the server extension no longer holds), `ERR NOTFRONTEND` (public port), `ERR RATE` (three failures on this connection). |
| `OUT [all]` | End this session (the connection is signed out and may sign in again), or with `all` every session of the player, live connections included. `OK n`, the number ended. A connection whose session was ended elsewhere gets `ERR SIGNEDOUT` once. |
| `PWD` | Sign-in `password` only (`ERR NOAUTH` otherwise): change the password. The server asks for the current one, a new one and the new one again (`ASK`/`AUT`), then `OK`; the player's other sessions end. |
| `MOTD` | The operator's message of the day: `MOTD text` lines (at most 73 characters of text each, so the line fits 80 bytes with its CR LF; a line that fits is sent as written, spaces and all, so an ASCII layout survives; a longer one is wrapped at spaces, and a longer word is cut; `MOTD` alone for a blank line), at most 20 of them, then `OK`. The marker `{contribute}` in the operator's text is replaced by the setting `site.contribute_url` (by default the project's home, https://github.com/eric-cgn/parlornet); where the operator cleared it, every line holding the marker is left out. Allowed before signing in. |
| `GAM` | List what this server hosts: `GAM type/ver rules name minplayers maxplayers [flags]`, ending `OK`. Flags: `staged` (turns made of steps, needs `UND`/`END`), `nomoves` (no legal-move list; use `CHK`), `drop` (in a game of three or more, one player can leave and the others play on; see Turn timer). Allowed before signing in. |
| `PRL` | The protocols this server speaks now: `DOC url` (the documentation site, the setting `site.docs_url`; left out when the operator cleared it), then for each protocol `PRO key port name` and, when it has one, `PRD key text`, by port; then `OK`. The core's own `PARLORNET/1` on the public port is always there; every other protocol is there while the server extension that declared it is connected (Protocols, below). Allowed before signing in, on both listeners. |
| `TIM` | How long the player has been signed in, the server's time, and the time this connection has left, for clients with no clock of their own: one line `TIM secs now left=rem call=rem month=rem`, then `OK`, such as `TIM 754 2026-11-24T21:05:33Z left=2400 call=3780 month=2400`. `secs` is whole seconds, and it is 0 until the connection signs in. On the public port it counts from this connection's sign-in. On the extension channel it counts from the start of the session, the sign-in on that server extension, so every connection that resumes the session with `SES` (the web opens one for each page) gives the same count. `secs` may pass 65535 (about 18 hours). `now` is the server's time in UTC, as `YYYY-MM-DDTHH:MM:SSZ`. Each `rem` is whole seconds, or `unl` for no limit (Time allowance, below). `left` is the connection's time allowance, the smallest of its limits, and the one a clock shows. `call` is the time the call has left, and `month` is the time the signed-in player has left this month. The line fits the 80-byte limit. `TIM` writes nothing to the database and is not activity: it leaves `WHO`'s `idle=` alone. A client without a clock can poll it, for example with its regular lobby poll, and count the seconds between polls itself. Allowed before signing in, on both listeners. RSCARDS front ends and GameMaster have clocks of their own, and nothing changes for them. |
| `PRO key port name` | Extension channel only: this connection serves the protocol `key` on the public port `port`, under `name` (below). `OK`, `ERR SYNTAX`, `ERR LIMIT` (more than 4 keys on one connection), `ERR NOTFRONTEND` (public port). Allowed before signing in. |
| `PRD key text` | Extension channel only: the one-line description of a protocol this connection declared. `OK`, `ERR NOPROTO` (not declared on this connection), `ERR SYNTAX`, `ERR NOTFRONTEND`. Allowed before signing in. |
| `LST [words]` | List the tables that are open or playing (finished ones are not listed): `TAB id type/ver rules filled/seats status names turn=secs [vis=private] [bot=seat,...] [integrity=false]`, where `names` lists every seat in order, `-` for an empty one, `turn` is the table's time per turn, `vis=private` marks a private table, `bot=` lists the seats held by bots (docs/bot.md), and `integrity=false` marks a table where the server has given out something it normally keeps (`INT`); ending `OK`. Public tables are listed for everyone, private ones only for their creator and players seated or invited there. The asker's own tables (seated, or the creator) and invitations come first, all of them, by number; then the others, open ones newest first, then games being played newest first, at most `lists.max` of them (20), and a line `MORE n` when n more were left out. The words narrow the list: `open` or `playing`; `mine` (own tables and invitations); `game=reversi` or `game=reversi/1`; `player=name`, the tables where that player sits (a private one only if the asker sits there too); `table=id`, that one table. The word `clients` adds, after each `TAB` line, a `CLIENT seat text` line for each seated player who said what they play with (Clients and machines, below), where the asker may see it. Each at most once, in any order; anything else is `ERR SYNTAX`. On the extension channel, `all` lists every table, with no `MORE` (`ERR NOTFRONTEND` on the public port): for an extension's own bookkeeping, not for a list it shows. |
| `NEW type rules [key=val...]` | Create a table (one game instance, hosted and arbitrated by this server). `OK tableid`. The creator is not seated by it: they sit down with `JON` like anyone else. `type` may leave out its version (`reversi` is `reversi/1`). At most six options; a key is letters and digits, a key or a value at most 12 characters. The option `turn=` (like `5m`, `1h`, `1d`; 1 minute to `games.turn_max_days`, 5 days; default 1 day) sets the time each player has per turn, and `vis=public` or `vis=private` (default public) who may sit down; both are handled by the server, not the game. The option `players=n` sets the number of seats, from the game's minimum to its maximum (default: the minimum). `ERR FULL` when the server holds `games.tables_max` tables (400); `ERR LIMIT` when the creator could not sit down at it (the limits under `JON`). |
| `JON table [seat]` | Sit at a table, in the first free seat or the one asked for (`ERR TAKEN` if it is not free or held for an invited player). An invited player gets the seat held for them, if any, and the invitation is used up. A private table without an invitation is `ERR PRIVATE`. `ERR LIMIT` with the reason when the player is playing `games.active_max` games (20; finished, resigned and left games do not count), or when the game would not start with them and they already wait at `games.waiting_per_type` open tables of this game type (2); bots are exempt, and a game that starts is never refused, so a player can play more than the limit by the tables they were waiting at (docs/pnp.md, Tables). `OK seat` |
| `INV table name [seat]` | Invite a player to an open table where you are seated, and optionally hold a free seat for them. Inviting the same player again replaces the invitation and starts its hour again. The reply is `OK`. If the player is not online now, the reply is `OK offline`, and the invitation is still kept for them. Errors are `ERR NOTSEATED`, `CLOSED`, `NOPLAYER`, `STATE` (already seated), `TAKEN`, `LIMIT`. |
| `UNV table name` | Withdraw an invitation (any player seated at the table), or decline your own. `OK`, or `ERR NOINVITE`. |
| `IVL` | Your invitations: `IVT table type/ver rules from status [seat=n]`, where `from` is who invited you and `seat` the seat held for you. The reply ends with `OK`. |
| `WHO` | List the players who are online now. Each is a line `PLR name idle=secs [bot]`, sorted by name, at most `players.online_max` (200) and the bots, and the reply ends with `OK`. A player is online when they are signed in and have been active in the last `presence.idle` seconds (default 1800). Signed in means a live connection on any frontend, or a web session that has not ended. `idle` is the number of seconds since the player's last activity. `bot` marks the bot, which is online while it is connected and always shows `idle=0`. Activity means requests that do something, such as `NEW`, `JON`, `MOV`, `CHT` and `ACT`. Requests that only look, such as `LST`, `SIN`, `WAT`, `VUE` and `WHO`, never count. docs/accounts.md gives the full list under "Who is online". |
| `ACT` | Tell the server that the player did something on this frontend that needs no other request, such as a command the frontend handles itself. It counts as activity. The reply is `OK`. |
| `LVE table` | Stand up from an open table, or stop watching. A running game is not affected: the player stays seated and can come back at any time. |
| `RES table` | Resign a running game: `END resign seat`. In a game flagged `drop` with more than two seats still playing, only this seat leaves (`GONE seat resign`) and the others play on; the seat can no longer move or resign there (`ERR STATE`). |
| `SEE table` | Watch a table (spectator view). A private table can be watched only by its creator and players seated or invited there (`ERR PRIVATE`). At most 20 watchers a table (`ERR FULL`). |
| `VUE table [flags]` | This player's view (see Views). Flag `M` adds the legal-move list, `T` the status text, `G` the table's static geometry, `C` what each seated player plays with (`CLIENT seat text` lines; Clients and machines, below). Flag `O`, on the extension channel only, adds every rack and the bag for a seated player at a table marked `integrity=false` (Tables marked integrity=false, below); anywhere else it adds nothing. |
| `MOV table token...` | Play a move, or one step of a staged turn. `OK`, `OK more` (turn continues), `OK done` (turn complete, send `END`), or `ERR ILLEGAL reason`. |
| `CHK table token...` | Validate a move without playing it. `OK [info]` (for example a score) or `ERR ILLEGAL reason`. Every type answers it; for `nomoves` types it takes the place of the move list. |
| `UND table` | Undo the last step of a staged turn (`ERR NOSTAGE` for a type without staged turns). |
| `END table` | Commit a staged turn (`ERR NOSTAGE` likewise). |
| `SIN table [n]` | Events since sequence number n (polling); without n, the events this connection has not seen. |
| `WAT table [n] [secs]` | As SIN, but wait up to secs (default 20, at most 30) for something (optional; polling clients may ignore). |
| `CHT table text` | Table chat, by a seated player or a watcher: printable ASCII, at most 56 characters kept. `OK`; the line comes back as the event `CHT`. |
| `CMD [table] [YES] /line` | Run a command line as the player typed it, such as `/who`, `/invite bot` or the move `/d3`, at the table named or in the lobby (Commands, below). The reply is `INF text` lines to show as they are, then `OK`. A command that asks first answers `DLG kind [table]` with the dialog's words, and the dialog's Yes sends `CMD [table] YES /kind`. Errors are `ERR SYNTAX` (no slash), `ERR NOTABLE`, `ERR NOTAT` (the player neither sits nor watches there) and `ERR RATE`. |
| `NOTE table` | The table's notes, oldest first: `NOTE date name text` lines, then `OK n`, the number of notes (Notes, below). Anyone who may see or watch the table may read them; a private table's are refused to others (`ERR PRIVATE`). |
| `NOTE table text` | A seated player adds a note: 1 to 48 printable ASCII characters. `OK n`, or `ERR NOTSEATED`, `ERR BADNOTE`, `ERR LIMIT` (the table has 20 notes). |
| `TDL table` | Extension channel only: the data kept with the table, `TDL key value` lines by key, then `OK` (Data kept with a table, below). `ERR NOTFRONTEND` on the public port and over the WebSocket. |
| `TDS table key value` | Extension channel only: keep a value with the table. `OK`, `ERR BADKEY`, `ERR BADVALUE`, `ERR LIMIT`, `ERR NOTFRONTEND`. |
| `TDD table key` | Extension channel only: forget a key. `OK` (also when it was not set), `ERR BADKEY`, `ERR NOTFRONTEND`. |
| `INT table false text` | Extension channel only: mark the table `integrity=false`, because the extension's program receives what the server normally keeps there, and say what in `text` (1 to 49 printable ASCII characters). Only its creator or a player seated there may, while its game is open or running. One way: `OK` too when the table is marked already, which keeps its first text. `OK`, `ERR SYNTAX`, `ERR BADVALUE`, `ERR NOTSEATED`, `ERR STATE` (the game is over), `ERR NOTFRONTEND` (the public port and the WebSocket) (Tables marked integrity=false, below). |
| `NFY`, `NFY n [secs]` | Extension channel only, allowed before signing in: the notices for players away from their tables (Notices, below). `NFY` alone answers `OK seq`, the latest notice's number; `NFY n` lists the notices after `n` as `NFY seq kind table name token who type/ver` lines (a `join` notice may be followed by `NFX CLIENT text`), then `OK seq`, waiting up to `secs` (at most 30) while there are none. `ERR NOTFRONTEND` on the public port and over the WebSocket. |
| `PRF GET key`, `PRF SET key value`, `PRF DEL key`, `PRF LST` | A little data kept for the signed-in player, such as a display setting (below). `GET`: `PRF key value` if the key is set, then `OK`. `SET` keeps a value, `DEL` removes a key (`OK` either way), `LST` lists every key as `PRF key value` lines, then `OK`. A key is 1 to 12 of `[A-Za-z0-9_-]`, in any case (kept in lower case); a value is the rest of the line, 1 to 56 printable ASCII characters (spaces allowed inside). At most 16 keys and 1024 bytes of keys and values per namespace and player: over that, `ERR LIMIT`. `ERR BADKEY`, `ERR BADVALUE`, `ERR SYNTAX`; before signing in, `ERR NOHELO`. |
| `CLI`, `CLI PROG [text]`, `CLI MACH [text]` | What this client is: `PROG` the program and its version, `MACH` the machine it runs on (Clients and machines, below). Each is up to 40 printable ASCII characters, cleaned by the server; nothing after the word forgets it. `OK`. `CLI` alone lists what this connection said, as `CLI PROG text` and `CLI MACH text` lines, then `OK`. Optional, allowed before signing in, and never believed about anything else. |
| `TRN table [n]` | The turns played at a table after its event number `n` (0, or none, for all), oldest first, at most the latest 50: who played each one, when, the turn in a few words, and the program and machine the player said they used (Turns and activity, below). `OK count`. A private table's turns are refused to anyone who may not see it (`ERR PRIVATE`), and their programs and machines go only to the players at that table. |
| `FEED [n [name]]` | The latest `n` turns at public tables (10 if left out, at most 30), newest first, as `TRN` gives them, with a name only that player's; `OK count`. A private table's turns are never in it. |
| `PFL [name]`, `PFL SET PREF [text]` | A player's profile (your own without a name): their preferred machine, their score, the games they played to the end at public tables, and the programs and machines they played with there (Profiles, below); then `OK`, or `ERR NOPLAYER`. `PFL SET PREF text` sets your own preferred machine, up to 40 printable ASCII characters (nothing after `PREF` clears it); `OK`. |
| `TOP [n] [game=type]` | The leaderboard: `TOP rank name points wins draws losses resigns` for the first `n` players (10 if left out, at most 50), by every game or by one game type (`game=reversi` or `game=reversi/1`), the most points first; then `OK count`, the number of players with a score. Bots are never listed. `ERR NOGAME` for a type this server does not host, `ERR NOSCORES` when the operator turned the leaderboard off (Scores, below). Allowed before signing in. |
| `KEY NEW [label]` | Staying signed in: a sign-in key for this computer, asked for right after signing in with the password, once, on the same connection (below). `OK key`. The label names the computer in the player's list. It is up to 32 printable ASCII characters, and the server cleans it. Without one, the server uses what the connection said with `CLI MACH`, then `CLI PROG`. `ERR STATE` (not right after a sign-in with the password: after `SES`, after a sign-in with a key, or a second time), `ERR LIMIT` (the player has 20 keys), `ERR NOAUTH` (sign-in `none`, or a bot). |
| `KEY LST` | The player's sign-in keys: `KEY n made used here label` lines, by number, where `made` and `used` are days (`YYYY-MM-DD`, UTC) and `here` marks the key this session signed in with or was given (`-` for the others); then `OK count`. |
| `KEY DEL`, `KEY DEL n`, `KEY DEL all` | Forget this session's key (the connection stays signed in), the key numbered `n`, or every key of the player. Every other session that signed in with a forgotten key, or was given it, ends. `OK count`, or `ERR NOKEY` (no such key). |
| `PAG bytes` | Public port only: send every reply on this connection in pages of at most `bytes` bytes, each line counted with its line ending (Paged replies, below). `bytes` is from 512 to 65535, and `PAG 0` turns paging off, as it is when the connection opens. `OK`, `ERR SYNTAX` for any other number, `ERR NOTPUBLIC` on the extension channel. Allowed before signing in. |
| `CNT` | The next page of a reply that waits for it (Paged replies, below). `ERR STATE` when no reply is waiting. Allowed before signing in. |
| `BYE` | Close. |

## Commands (`CMD`)

Every frontend has a command line, where a player types a command after a slash, such as `/who` or `/invite bot`, or a move, such as `/d3`. `CMD` runs that line on the server. Every frontend then has the same commands with the same words, and a small machine such as the Apple II or the Commodore 64 needs no code of its own for each command ([design.md](design.md), What the server provides). The commands are the shared ones of [chat-commands.md](chat-commands.md), with the same names, arguments and words, and the core and the chat consoles of the server extensions run them from one implementation (parlord/commands.py).

**The request.** `CMD [table] /line` sends `line` as the player typed it, from its slash on. `table` is the table it was typed at, where the player sits or watches. A table where the player does neither is `ERR NOTAT`. Without a table the command runs as in the lobby. The request is one line like any other, at most 80 bytes with its ending, so a frontend's command line should take at most 60 characters, which fits with any table number and the confirmed form. A line without a slash is `ERR SYNTAX`.

**The reply.** The reply is `INF text` lines, then `OK`. Each `INF` line is a line to show as it is, in plain sentences written for the player. A line is at most 74 characters, and longer text is wrapped at spaces. A frontend with a narrower screen wraps the lines again at spaces. An empty line is `INF` alone. A command with nothing to say answers `OK` alone, such as a typed move that was played or a line said with `/say`. What a command changes at a table reaches the client as events, through `SIN` or `WAT`, exactly as when the client sends the requests itself. `CMD` leaves the connection's event cursors where they were, so the next `SIN` brings those events. A command that is refused says why in a sentence like any other, such as "You can't play a1. That square flips nothing." `ERR` is only for a request the server can't take: `ERR SYNTAX`, `ERR NOTABLE`, `ERR NOTAT`, `ERR RATE` and `ERR NOHELO`.

**Confirmations.** Resigning, passing, ending a Rummy Tile turn that won't count and signing out ask first, in a dialog ([design.md](design.md), Dialogs). The reply to such a command is `DLG kind [table]`, then the dialog's words as `INF` lines, then `OK`, and nothing has been done yet. The client opens its confirmation dialog with those words and the buttons Yes and No, starting on No. Yes sends the confirmed form, `CMD [table] YES /kind`, with the table and the kind from the `DLG` line. No and Esc send nothing. The kinds are `resign`, `pass`, `end` and `quit`. The command line can't send the confirmed form, because what it sends starts with the slash, and a `yes` typed after a command confirms nothing. So a word typed out of habit never ends a game.

| `DLG` line | The dialog's words | Yes sends |
|---|---|---|
| `DLG resign 12` | "Resign this game? ann wins. Leaving the table never resigns." At a table of more than two players, "ann wins." is "The others play on." | `CMD 12 YES /resign` |
| `DLG pass 12` | "Pass your turn? You play nothing this turn." | `CMD 12 YES /pass` |
| `DLG end 12` | "End your turn? The table doesn't count yet, so your tiles go back and you take a tile." | `CMD 12 YES /end` |
| `DLG quit` | "Sign out? Your games go on without you." | `CMD YES /quit` |

A confirmed `/quit` ends the player's session on this connection, as `OUT` does, and the client goes back to signing in.

**Offers stay in their dialogs.** No command answers an offer. A double, a draw offer, an offer to resign and an invitation are each answered in their own dialog, which sends the request itself: the game's answer with `MOV` (Backgammon's `take` and `drop`, for example), and `JON` or `UNV` for an invitation. `/accept` and `/decline` answer "An invitation or an offer is answered in its own dialog, not from the command line.", and the game refuses a typed `/take`, `/drop` or `/reject` at a table the same way. Making an offer is a command like any other, such as `/double` or `/draw`.

**The commands.** These are `/list`, `/resign [table]`, `/invite NAME [game]`, `/uninvite NAME`, `/open [game]`, `/join TABLE` or `/join NAME`, `/close`, `/who [NAME]`, `/tables NAME`, `/top`, `/score [NAME]`, `/note [text]`, `/say TEXT`, `/motd`, `/time`, `/quit` (also `/bye`) and `/help`. [chat-commands.md](chat-commands.md) says what each does. The table the line was typed at changes a few of them.

- At the open table where the player waits, `/invite NAME` invites NAME there, and `/invite bot` invites the bot, which sits down in a few seconds. `/uninvite NAME` withdraws an invitation, and `/close` closes the table.
- `/open` and `/invite` make a table of the game named, as in `/open reversi` or `/invite ann checkers`, or else of the game at the table the line was typed at. In the lobby a game must be named, by its type or by its name without spaces.
- `/say TEXT` says a line at the table, as `CHT` does, and keeps 56 characters. `/note` reads or adds the table's notes, and `/resign` resigns its game.
- `/resume` and `/signin` answer "Not available here.", because a frontend shows its games and signs in on its own screens. `/leave`, `/tutorial` and the help pages also belong to the frontend, which never sends them ([design.md](design.md), Commands the client handles itself). `/help` lists the commands.

**Typed moves.** At a table where the player sits, a line that is no command belongs to the game. The game's plugin reads the words ([plugins.md](plugins.md), `typed`), and the server plays them as the player's own `MOV`, `UND` or `END`. The forms are the ones in [design.md](design.md), Each game's actions: `/d3` in Reversi, `/h8` in Five in a Row, `/a1-b1` in Dots and Boxes, `/3` in Mancala, `/fire 45 60` and `/drive left 5` in Cannon Fire, `/13-9`, `/roll`, `/double`, `/undo` and `/end` in Backgammon, `/11-15`, `/15x22` and `/draw` in Checkers, `/h8 across QUIZ`, `/pass`, `/exchange TILES` and `/check h8 across QUIZ` in Grid Words, `/a1 across`, `/random`, `/ready` and `/e5` in Naval Combat, and `/R7-3`, `/end` and `/check` in Rummy Tile. A refused move says why, as in "You can't play a1. That square flips nothing." Before the game starts or once it is over, a typed line says so.

**Limits.** Each request a command sends counts against the connection's rate limit (Rate limits, below), as if the client had sent it, so a command that does a lot, such as `/list` with many games, costs more than one request. Past the limit the reply is `ERR RATE slow down`. `CMD` counts as the player's activity (`WHO`).

```
CMD /open reversi
INF Your table 1 is open: anyone can join it. /close closes it.
OK
CMD 1 /invite bot
INF You invited the bot to table 1. It will sit down in a few seconds.
OK
CMD 1 /resign
DLG resign 1
INF Resign this game? bot wins. Leaving the table never resigns.
OK
CMD 1 YES /resign
INF You resigned table 1.
OK
```

## Paged replies (`PAG`, `CNT`)

A client with little memory may ask for long replies in pages, so that it never has to hold more than one page at a time. Lines are already at most 80 bytes, and a page limits a whole reply the same way. Paging is a matter of the public port. The extension channel refuses `PAG`, because the server extensions read whole replies. Play, telnet, the web and GameMaster never send it, so their replies come whole.

- **Asking for pages.** `PAG bytes` sets the most bytes the server sends in one page of any reply on this connection. Each line counts with its line ending, so a CR LF line of 78 characters is 80 bytes. The number is from 512 to 65535. `PAG 0` turns paging off, and paging is off until the client sends `PAG`. The setting lasts until the connection closes or the client sends `PAG` again, and signing in or out leaves it alone.
- **Pages.** A reply that fits in one page, its final line included, comes whole, exactly as without `PAG`. When a reply would be longer, the server sends as many whole lines as fit, then a line `CNT`, and waits. The `CNT` line counts toward the page too. A line is never split, and no data line starts with `CNT`, so a client reads a page until a line that starts with `OK`, `ERR`, `ASK` or `CNT`.
- **The next page.** The client sends the request `CNT` for the next page. Each page follows the same rule, and the last page ends with the reply's own final line. That is `OK` or `ERR`, or `ASK` during sign-in. `CNT` with no reply waiting is `ERR STATE no reply is waiting for CNT`.
- **Dropping the rest.** Any other request while a reply waits drops the rest of that reply, and the new request is answered as usual. `PAG` is a request like any other here. A line the server refuses without running it, with `ERR RATE` or `ERR NEWLINE`, leaves the rest waiting. `CNT` counts against the rate limit like any other request.
- **Events.** The events of a reply count as read when the reply is made, whether every page of it is sent or not. Each `EVT` line carries its number, so a client that drops the rest of a `SIN` or `WAT` reply asks again with `SIN table n` from the last event it read.
- **The server's own lines.** The time allowance's notices go in front of the next reply, or in front of the next page when the client sends `CNT`, and they count toward that page (Time allowance, below). `ERR IDLE` and `ERR LIMIT` close the connection when they are due, also while a reply waits for `CNT`, and the rest of the reply goes with the connection. Waiting for `CNT` is idle time like any other.
- **Requests that change something.** The reply to a request that changes something always fits in one page, so a client never has to choose between the rest of such a reply and its next request. At 512 bytes, the least a client may ask for, this covers signing in and signing up with all their questions, `OUT`, `PWD`, `KEY NEW` and `KEY DEL`, `NEW`, `JON`, `LVE`, `INV`, `UNV`, `SEE`, `CHT`, `NOTE` with a text, `PRF SET` and `PRF DEL`, `PFL SET`, `CLI`, `CAN`, `ACT`, `PAG` and every command of `CMD` that changes something, with the two longest notices of the time allowance in front. The longest of them, the question for the password from a sign-up mail, is about 420 bytes with the notices. The reply to a move fits as well, as the next point says.
- **Events after a move.** The reply to `MOV`, `UND`, `END` and `RES` carries the table's events since the connection last read them (Events, below), and those have no bound of their own. A Cannon Fire shot at a table of eight can come near 512 bytes by itself, and chat from others may have piled up before it. So on a connection that pages, these replies carry only the oldest of those events that fit in one page, together with the notices in front and the reply's own final line. The connection's event cursor stays after the last event sent, and the next `SIN` without a number brings the rest, in order. A client that pages reads `SIN` after a move, to get any events that did not fit. `SIN table n` from the last event it read works as well. Without `PAG` these replies carry every event.
- **Replies that only show.** Every other reply may come in pages. The long ones are `LST`, above all with `clients`, `VUE` with `M`, `GAM`, `WHO`, `MOTD`, `TRN`, `FEED`, `TOP`, `PFL`, `KEY LST`, `NOTE`, `SIN` and `WAT`, and `CMD` with a command that only shows something, such as `/help` or `/list`.

```
C> PAG 512
S< OK
C> GAM
S< GAM backgammon/1 standard Backgammon 2 2 staged
   (eight more GAM lines)
S< GAM mancala/1 kalah Mancala 2 2
S< CNT
C> CNT
S< GAM mancala/1 oware Mancala 2 2
   (four more GAM lines)
S< GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves,drop
S< OK
```

## Data kept per extension and player (`PRF`)

Each server extension may keep a little data with a player, such as the telnet extension's movement keys, so a setting follows the player to their next sign-in. The data is kept in namespaces:

- On the extension channel, the namespace is the server extension's key name: the name its TLS 1.3 handshake proved (parlord/frontends.py), never a word of the request. Telnet's data is telnet's; the web cannot read or change it, nor can any other extension. The one exception is the two web frontends: Play (`play`) keeps its data in the 1997 pages' namespace (`web`), so a player's choices follow them between the two.
- On the public port, the namespace is `native` (stored as `:native`, which no key name can be), one set for the player shared by every client program on that port. Such programs should give their keys a short prefix of their own.
- A request always acts on the player signed in on that connection, in the connection's namespace. There is no way to name another player or another namespace. A server extension reaches a player's data only on a connection signed in as that player: one it signed in, or one that resumed (`SES`) a session it was issued.
- The limits (16 keys of up to 12 characters, values of up to 56 printable ASCII characters, 1024 bytes per namespace and player) are `Limits.pref_*` in parlord/core.py. They fit `PRF SET key value` in the 80-byte line.
- Never for secrets: no password, code, session id or anything else private goes in it. It is kept in the clear, the operator can see how much each extension keeps (counts and bytes, not values), and under sign-in `none` anyone can sign in as anyone.
- Deleting an account deletes its data in every namespace. A server extension that is removed leaves its data until the account is deleted; enrolled again under the same name, it sees it again.

## Staying signed in (`KEY`)

Any client that can keep a little data of its own, such as the 8-bit program on its disk or a browser in a cookie, may offer the player **Stay signed in**. It is one feature of the server, and every client uses it the same way on both listeners. A client that cannot keep anything does not offer it. The telnet extension doesn't offer it, because a telnet program has nowhere to keep a key.

- **The key.** With the setting on, the client sends `KEY NEW [label]` right after the player signs in with the password, on the same connection, and keeps the key from `OK key` instead of the password. A key is 128 random bits, written as 22 characters. The server keeps only its SHA-256, with its label, the day it was made and the day it was last used.
- **Signing in with it.** The client sends `USER name` and answers the password question with `AUT key key`. Nothing else is asked. A key works only where it was given. A key given on the public port works there, and one given to the web works only through the web.
- **A refused key.** `ERR NOKEY` means the key is not taken: it was forgotten, it expired, or the password changed. The client forgets its copy and asks for the password, starting again with `USER name`. A refused key counts as a wrong answer toward the name's lockout and the per-address and server budgets, exactly as a wrong password does. The third wrong answer in a row carries `wait=60`. A right key signs in while the name is locked, as a temporary password does, and clears the count.
- **Signing out.** `OUT` forgets the key the session signed in with or was given, whatever the client's setting, and the client forgets its copy. `OUT all` forgets every key of the player. A client that stays signed in leaves without `OUT` (with `BYE`, or by closing the connection) when the player leaves without signing out.
- **The player's list.** `KEY LST` lists the keys, and `KEY DEL n` or `KEY DEL all` forgets them. A session that signed in with a forgotten key ends. `KEY DEL` alone forgets this session's key and stays signed in, for a player who turns the setting off.
- **Expiry and what forgets them.** A key unused for 90 days expires (the setting `keys.idle_secs`, in seconds). A password change, a new password after a temporary one and the operator's reset forget every key. Deleting the account deletes them.
- **Limits.** At most 20 keys a player. Under sign-in `none` there is no password to stand in for, so `KEY NEW` is `ERR NOAUTH`.

```
C> USER bo
S< ASK password Password secret cred
C> AUT password swordfish 1997
S< OK -
C> KEY NEW Apple IIe
S< OK Kd8sV0q2LmZx7yTq4wR1nA
C> BYE                                  (the player leaves; another day the computer connects again)
S< OK
C> USER bo
S< ASK password Password secret cred
C> AUT key Kd8sV0q2LmZx7yTq4wR1nA
S< OK -
C> OUT                                  (signing out forgets the key)
S< OK 1
C> USER bo
S< ASK password Password secret cred
C> AUT key Kd8sV0q2LmZx7yTq4wR1nA
S< ERR NOKEY This computer was signed out. Sign in again
C> USER bo                              (the password, as before)
```

docs/pnp.md (Staying signed in) has a sample session recorded from a real server; docs/security-design.md has the reasons and the threats.

## Notices (`NFY`)

A server extension that tells players about their games while they are away, such as ParlorNet Play with its push notifications ([play-notifications.md](play-notifications.md)), learns of them from the core with `NFY`. It is the notification layer's way in (architecture.md): the core only says what happened and to whom; the extension decides how to tell them.

- **Kinds.** `turn`: it is now the player's turn at a table (from a `TURN` event); `who` is the player who had the turn before, or `-`. `end`: a game the player is seated at ended (from `END`); `who` is the player the result names (the winner, or the one who resigned or ran out of time) when that is not the player, else another player at the table. `join`: someone sat down at an open table where the player is seated (from `JOIN`); `who` is the one who sat down. Bots get no notices, and a bot sitting down gives none.
- **Who gets them.** A player gets a kind from an extension only if they listed it under the `PRF` key `notify` in that extension's namespace (the extension sets it for them when they turn notices on): the value is the kinds, comma-separated (`turn,end,join`), then a space and a token of 1 to 8 of `[A-Za-z0-9_-]` that the extension chose. Each notice line carries the token as it is when the notice is read, or `-` if there is none, so the extension can tell a later account of the same name from the one that asked. Deleting an account deletes the key, and its notices stop.
- **Reading them.** `NFY` gives the current number; then `NFY n secs` waits for notices after `n` and returns them with the number to ask from next (`OK seq`). The numbers count from 1 since the core started; an `n` past the latest (the core restarted) is read from 0. The core keeps the latest 1000 notices in memory only, so notices made while no extension was asking, or before a restart, are not kept for later.
- **Lines.** `NFY seq kind table name token who type/ver`, such as `NFY 41 turn 12 ann Xq3_k9Lm bob reversi/1`. A `join` notice is followed by `NFX CLIENT text` when the player who sat down said what they play with, in the words of the `CLIENT` event (Clients and machines, below); the notice goes to a player seated at the table, who may see it. An extension that does not know `NFX` ignores it. Every line fits the 80-byte line; one that would not is left out.

## Notes (`NOTE`)

Each table keeps a short list of notes, a record of things worth knowing about the game that the events do not say in words, such as "resumed on the Mac at move 12". The players seated at the table add them, and the core writes one itself when it marks the table integrity=false (`Integrity: text`, below).

- `NOTE table` lists them, oldest first, as `NOTE date name text`: `date` is the day it was written (UTC, `2026-11-22`), and `name` is the player who wrote it, or `-` for the core's own. Every line fits the 80-byte line: a player's text is at most 48 characters, the core's at most 60.
- A table keeps at most 20 notes. A player's note past that is refused with `ERR LIMIT`; a note the core writes then takes the place of the oldest one.
- Notes cannot be changed or deleted. They go when the table goes.
- The web table page, the telnet table screens (the typed commands `notes` and `note TEXT`; the latest three show in the log when the screen opens), ParlorNet Play's table panel and the chat consoles (`/note`) show them.

## Clients and machines (`CLI`)

Part of the fun of playing on old machines is letting others know what you are using. A client says which program it is and which machine it runs on, and the server records both with every turn the player makes on that connection, so a table's history, the activity feed and the player's profile can say "eric played FRAULEIN for 65 with BobGrid 2.3 on a Sun SPARCstation".

- `CLI PROG text` gives the client program and its version, such as `CLI PROG ParlorNet for the Apple IIe 1.1` or `CLI PROG GameMaster 1.0.0 + ParlorNet 0.4`. `CLI MACH text` gives the machine, such as `CLI MACH Apple IIe (enhanced)` or `CLI MACH Macintosh SE/30`. The answer is `OK`.
- The server cleans each text before keeping it: anything that is not printable ASCII becomes a space, runs of spaces become one, the ends are trimmed, and only the first 40 characters are kept. `CLI PROG` or `CLI MACH` with nothing after it forgets that one. `CLI` alone lists what the connection has said, as `CLI PROG text` and `CLI MACH text` lines (only those that are set), then `OK`.
- It is optional. A client that never sends `CLI` plays as before, and its turns are recorded without a program or a machine.
- It may be sent before or after signing in, and as often as the client likes; each turn records what was said last. It lasts as long as the connection and does not follow the player to another one.
- On the extension channel, the server extension sends it for the player it serves, because it knows the real client: the NetSprocket extension says `NetBoxes 1.1` on a `Macintosh`, the telnet extension the terminal type the player's program named, the web pages the browser, and so on (docs/adapters.md). Over the WebSocket, ParlorNet Play's page says which browser and system it runs on.
- It is the client's own claim and nothing more. The server shows it, but never believes it about anything else: it does not decide who the player is, what they may do or how a game is played (docs/design/trust.md). Anyone can say they are anything.
- The table hears it when the player sits down, and again when they play on with another program (below).

```
CLI PROG ParlorNet for the Apple IIe 1.1
OK
CLI MACH Apple IIe (enhanced)
OK
CLI
CLI PROG ParlorNet for the Apple IIe 1.1
CLI MACH Apple IIe (enhanced)
OK
```

### Who plays with what at a table (`CLIENT`)

When a player sits down, the table is told what they play with: right after `JOIN seat name` comes the event `CLIENT seat text`, where `text` is the program and the machine their connection named, in words without the player's name, as `TRX MOVE` gives a turn. A frontend shows it after the name: "ann sat down with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus".

- The words are `with PROG on a MACH`, `with PROG` or `on a MACH`, with `an` before a vowel, no article before a system's name (`on macOS`) and `the` before a few programs' names (`with the Outland Gateway`). Each event is at most 60 characters of text and fits the 80-byte `EVT` line: the program is kept whole, and the machine is cut at a word, or left out when not even its first word fits.
- A connection that named neither sends no `CLIENT`: where nothing is known, nothing is said. The bot says nothing either.
- When a seated player moves or chats from a connection that names another program or machine than the seat's, such as after coming back to a running game from another client, the table hears `CLIENT seat text` again, just before that move or line. A connection that named nothing changes nothing.
- `VUE table C` lists `CLIENT seat text` for each seated player whose program or machine is known, so a player who opens the table later sees what each opponent uses; `LST clients` gives the same lines after each table's `TAB` line, for lobby lists.
- A frontend reads `CLIENT` as belonging to the seat it names, and one that does not know the event ignores it, as any event kind it does not know.

Who sees it follows the rule for the turns' programs and machines: at a public table, everyone. At a private table, only its creator and the players seated there. Anyone else who may watch the table, such as an invited player, gets each `CLIENT` event as `CLIENT seat`, with no text, and no `CLIENT` lines from `VUE C` or `LST clients`. A frontend shows nothing for a `CLIENT` with no text.

```
EVT 12 5 JOIN 1 ann
EVT 12 6 CLIENT 1 with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus
EVT 12 7 START
VUE 12 C
V reversi/1 9
...
CLIENT 0 with Netscape Navigator 3.0 on a Macintosh
CLIENT 1 with GameMaster 1.0.0 + ParlorNet 0.5 on a Macintosh Plus
OK
```

## Turns and activity (`TRN`, `FEED`)

The server keeps a record of each turn: the table, the player, when it was played, the turn in a few words and the program and machine the player's connection named with `CLI`. A turn is recorded when it is over: a move of a game without staged turns, or the `END` of a staged one (the steps in between belong to it). A move that makes no event, such as placing a ship before a Naval Combat game begins, is not recorded, since there is nothing public to say about it.

`TRN table [n]` lists a table's turns after its event number `n`, oldest first (at most the latest 50), and `FEED [n]` the latest `n` turns at every public table, newest first (10 if `n` is left out, at most 30); `FEED n name` lists only that player's, for a profile. Both answer with the same lines for each turn, and end with `OK count`, the number of turns listed:

- `TRN table seq time seat name type/ver`: the table, the event number of the turn's last event, the time in UTC (`2026-11-23T14:05:09Z`), the seat, the player and the game type.
- `TRX MOVE text`: the turn in a few words, without the player's name, such as `played FRAULEIN for 65`, `rolled 6-3, moved 13/7 8/5*` or `fired at b4: a hit`. The game's plugin says it, from the turn's public events only (docs/plugins.md), so it never gives away what is hidden; a game that says nothing has no `MOVE` line. At most 60 characters.
- `TRX PROG text`: the client program the player's connection named, when it named one.
- `TRX MACH text`: the machine, when it named one.

A client reads `TRX` lines as belonging to the `TRN` line before them, and ignores a `TRX` word it does not know. Every line fits the 80-byte line.

Who sees what:

- At a public table, everyone who is signed in sees every turn, with its program and machine.
- At a private table, only those who may see the table (its creator and the players seated or invited there) get its turns from `TRN`; anyone else gets `ERR PRIVATE`. The `PROG` and `MACH` lines go only to the players seated at the table: an invited player or a watcher sees who played and what, but not with what.
- A private table's turns are never in `FEED`, and never counted on a profile.
- Turns are kept after their table is gone, so a finished table's history can still be read with `TRN`, by everyone for a public table and by the players who played there for a private one. The server keeps the latest 5000 turns. Deleting an account deletes its turns.

```
FEED 2
TRN 12 41 2026-11-23T14:05:09Z 0 eric gridwords/1
TRX MOVE played FRAULEIN for 65
TRX PROG BobGrid 2.3
TRX MACH Sun SPARCstation
TRN 9 17 2026-11-23T14:02:40Z 1 ann reversi/1
TRX MOVE played d3, turning 1
TRX PROG ParlorNet for the Apple IIe 1.1
TRX MACH Apple IIe (enhanced)
OK 2
```

## Profiles (`PFL`)

Each player has a profile: what they chose to say about themselves, which for now is the machine they like best, their score, and what the server has counted from their play at public tables. It holds nothing else: no address, no email and nothing from a private table but the points its games gave to the score.

- `PFL name` reads a player's profile, and `PFL` alone your own. The lines are `PFL NAME name` (the name as the account has it), `PFL PREF text` (the preferred machine, when the player gave one), `PFL SCORE points wins draws losses resigns rank` (Scores, below; `rank` is `-` before the player's first scored game; there is no `SCORE` line for a bot or when the operator turned the leaderboard off), then `PFL GAME type/ver n` for each game they played to its end at a public table, `PFL PROG n text` for each client program they played turns with there, and `PFL MACH n text` for each machine, each the most first and at most 8 of a kind, then `OK`. A name with no account is `ERR NOPLAYER`.
- `PFL SET PREF text` sets your own preferred machine, up to 40 printable ASCII characters, cleaned as `CLI` cleans its text; `PFL SET PREF` with nothing after it clears it. `OK`.
- Every signed-in player may read every profile. Deleting the account deletes the profile and its counts.

```
PFL SET PREF Macintosh SE/30
OK
PFL eric
PFL NAME eric
PFL PREF Macintosh SE/30
PFL SCORE 43 9 2 3 1 4
PFL GAME gridwords/1 12
PFL GAME reversi/1 3
PFL PROG 140 BobGrid 2.3
PFL PROG 22 Netscape Navigator 3.0
PFL MACH 162 Macintosh
OK
```

## Scores and the leaderboard (`TOP`, `PFL SCORE`)

Every player account has a score. When a game ends, each player seated at it gets points for how it went, at public and private tables alike, and games against the bot count like any other. The bot's own accounts are not scored and never appear on the leaderboard.

The points, by default:

| Result | Points | Counted as |
|---|---|---|
| A win | 4 | wins |
| A draw, for each player sharing it | 2 | draws |
| A loss: the game was played to its end and someone else won | 1 | losses |
| Resigning (`RES`), running out of time, or leaving a game of three or more (`GONE`) | 0 | resigns |

How each ending scores:

- `END win seat ...`: that seat wins, and every other player still playing loses.
- `END draw ...`: the players who share the draw get a draw, and every other player still playing loses. In a game of two that is both players. In a game of three or more it is a tie for first: the players still playing with the top score in the result (`draw 4-4-1` shares it between the first two), or the seats the game's plugin names (in Cannon Fire, the last tanks, which destroyed each other).
- `END resign seat` and `END timeout seat`: that seat gets a resignation, and every other player still playing scores a win, as when the opponent of a two-player game resigns.
- A seat that left a game flagged `drop` earlier (`GONE seat resign`, `GONE seat timeout`) counts as resigned when the game ends.
- `END abandoned` (the operator ended the table) scores nothing.

A game is scored once, when it ends, and the points it gave stay as they were when the operator changes the point values. There is no protection against cheating: anyone can play against themselves under a second name or against the bot, so any score is easy to get on a public server. The leaderboard is meant for conferences, festivals and LAN parties.

- `TOP [n] [game=type]` lists the leaderboard: `TOP rank name points wins draws losses resigns` for the first `n` players (10 if left out, at most 50), then `OK count`, the number of players with a score. The most points come first; players with as many points share a rank and are listed by the most wins, then the fewest games. `resigns` counts resignations, timeouts and seats left. With `game=type`, the board counts the points earned in that game type alone. Bots are never listed, and a player appears once they have a scored game. Every line fits the 80-byte line. `TOP` is allowed before signing in, so a page or a screen can show the board without an account.
- `PFL name` carries the player's score as `PFL SCORE points wins draws losses resigns rank`, with `-` for the rank of a player with no scored game.
- The operator can reset every score at once, at the start of a festival for example (docs/accounts.md, Admin panel). The totals start again from nothing, and so does each game type's board; the games themselves stay recorded (their tables, events and turns, and the points each gave).
- The operator can change the points (`scores.win`, `scores.draw`, `scores.loss`, `scores.resign`) and turn the leaderboard off (`scores.show`). With it off, games are still scored, `TOP` answers `ERR NOSCORES`, and profiles have no `SCORE` line, so frontends show no leaderboard and no scores.
- Deleting an account deletes its score.

```
TOP 3
TOP 1 ann 22 5 1 0 0
TOP 2 bob 13 3 0 1 1
TOP 2 eric 13 2 2 1 0
OK 7
TOP 2 game=reversi
TOP 1 eric 9 2 0 1 0
TOP 2 ann 8 2 0 0 0
OK 4
```

## Data kept with a table (`TDL`, `TDS`, `TDD`)

A table may carry a little data that server extensions need, such as which client family a table was made for: an extension may keep `client NAME` with every table it makes, so it knows them again after a restart.

- Only the core and server extensions set it. `TDS` and `TDD` are for the extension channel only, and so is reading it with `TDL`, which is also refused over the WebSocket, so a player's own program never sets or reads it.
- A key is 1 to 8 of `[a-z0-9_-]`, in any case (kept in lower case); a value is the rest of the line, 1 to 56 printable ASCII characters, so `TDS table key value` fits the line. At most 16 keys a table, besides the core's own.
- The bot keeps `bot open` with each of its open tables, the tables it keeps for players to join, and takes it off when a player sits down (docs/bot.md, Open tables).
- The core keeps one key itself, `integrity`, which holds the mark of a table marked integrity=false (below). It is longer than any key `TDS` and `TDD` take, so no extension sets or forgets it there. The table's options and its game state stay the authority on how a game is played; this data only helps extensions along.
- A request acts on a table the signed-in player may see. Never for secrets: it is kept in the clear.

## Tables marked integrity=false (`INT`)

The server is the authority at every table: it deals, keeps every secret and decides every move. A table is marked `integrity=false` when the server gives out something it normally keeps, such as the other players' racks or the bag. The mark is a plain statement of that, for the players: the server still deals, judges every move and ends the game, and the game goes on as at any other table.

- The mark is one way: once set it stays for the rest of the game. The first reason given is the one kept.
- Only a server extension sets it, with `INT table false text`, when its program receives what the server normally keeps. Nothing else marks a table today.
- The Networdz extension sets it when a Networdz player sits down, because Networdz's protocol sends the bag and every rack to each player's program: `INT 12 false a Networdz program receives the bag and all racks`. The extension then reads every rack and the bag with `VUE table O`, which the core answers only on the extension channel, only for a seated player and only at a marked table. The Networdz program shows its player only their own rack.
- `LST` shows `integrity=false` on the table's line, `VUE` adds `INT false text`, the table's events get `INT false text` when the mark is set, and the core writes the note `Integrity: text` (Notes, above). The web table page, the telnet screens and ParlorNet Play show the event as "Integrity: text." in the table's log.
- Nothing else changes: nobody is refused because of the mark, and no client shows another player's rack because of it.

## Protocols (`PRO`, `PRD`, `PRL`)

Each server extension tells the core which protocol it serves, so the core can say what the server speaks now and a server's web pages list it without a table of their own (docs/design/protocol-declarations.md).

- A protocol has a key: an upper-case letter, then up to 11 more of upper-case letters, digits, `/` and `-`. The keys in use: `HTTP` (the web extension), `HTTPS` (ParlorNet Play), `PARLORNET/1` (the core itself), `TELNET`, `IOS`, `FIBS`, `OUTLAND`, `DOOM`, `NETSPROCKET` (NetBoxes), `NETLUFF` (NetLuff), `GAMEMASTER`, `BATNAV` (the batnav clients), `ICDS` (xcheckers), `WORTHY` (Worthy Opponent) and `RSCARDS` (GEnie's RSCARDS front ends). The docs site's protocols reference has an entry for each, at `protocols.html#KEY`, where any character of the key but letters, digits and `-` is written `-` (`PARLORNET/1`: `#PARLORNET-1`).
- `PRO key port name` declares one: `port` is the port players connect to (1 to 65535; where a container's port is published under another number, the published one), `name` the rest of the line, 1 to 48 printable ASCII characters. `PRD key text` gives it a one-line description, 1 to 56 printable ASCII characters. Declaring a key again replaces its port and name and keeps its description. One connection may declare up to 4 keys.
- A declaration lasts as long as the connection that made it: when the connection closes, the protocol leaves the list. A server extension keeps one connection open for it, and sends its `PRO` and `PRD` again every 5 minutes, which also keeps the connection from going idle (adapters/declare.py); if the core goes away it connects again and declares again.
- `PRL` lists the core's own `PARLORNET/1` and the declared ones, by port; the same key and port declared on two connections is listed once.
- A server extension that does not declare is not listed, and nothing else changes for it. A core older than these requests answers `PRO` with `ERR UNKNOWN`, and the extension then stops declaring.

```
PRO IOS 6503 Internet Othello Server (IOS)
OK
PRD IOS Reversi for clients of the Internet Othello Server
OK
PRL
DOC http://docs.parlornet.org/
PRO HTTP 6500 HTTP and HTML 3.2
PRD HTTP HTML 3.2 pages for any browser with tables and forms
PRO PARLORNET/1 6501 ParlorNet protocol (PARLORNET/1)
PRD PARLORNET/1 ParlorNet's own protocol, for programs written for it
PRO IOS 6503 Internet Othello Server (IOS)
PRD IOS Reversi for clients of the Internet Othello Server
OK
```

## Events

Events are numbered per table: `EVT table seq kind ...`.

- Common kinds: `JOIN seat name`, `CLIENT seat [text]` (what the seat's player plays with, right after `JOIN` and when it changes; Clients and machines, above), `LEAVE seat` (a player stood up from an open table), `START`, `TURN seat`, `END result`, `CHT seat text` (`seat` is `-` for a watcher), `INVITE seat name`, `UNINVITE seat name why`. In the invitation events, `seat` is the seat held for the player, or `-` when no seat is held. `why` says how the invitation ended. It is `declined` when the player declined it, and `withdrawn` when a seated player withdrew it or the operator deleted the account. It is `expired` when the hour ran out, `started` when the game began without the player, and `left` when the player who made the invitation stood up. Servers older than this send no reason, so a client should treat the word as optional. `END abandoned` means the operator ended the table. `GONE seat resign|timeout` means one seat left a game flagged `drop` and the others play on. `INT false text` comes once, when the table is marked `integrity=false` (Tables marked integrity=false, above).
- Type-defined kinds say exactly what changed (a checker moved, tiles placed, terrain changed, a die rolled). They are the deltas: a client can keep its model by applying them without knowing the rules, or refetch with `VUE` at any time.
- A `MOV` reply carries, before its `OK`, every event the client has not yet seen for that table (its own move, and the opponent's reply if it has already come). On a connection that pages, it carries as many as fit in one page (Paged replies, above).
- `SIN` and `WAT` end with `OK seq`, the sequence number to ask from next time.

## Views

`VUE` returns `V type/ver seq`, then type-defined lines, then `OK`; before the game starts, `V type/ver seq` and `S Waiting for players`. Hidden information is removed per player by the server.

- **Common lines** (every type may use these): `S text` status; `T seat points` scores; `M token...` one legal move per line when the type provides a list (flag `M`, only on your turn); `CLK seat secs_left turn_secs` while a game runs: whose turn it is and how long they have left; `BOT seat...` the seats held by bots (docs/bot.md), only when there are any; `CLIENT seat text` what a seated player plays with (flag `C`, before and during the game, where the player may see it; Clients and machines, above). `INT false text` at a table that keeps no integrity, with why (`INT`, below), before and during the game.
- **Type-defined lines** carry the state itself, with a short tag per line (`P` points, `R` rack, and so on). Each type/ver spec defines them. Large or sparse structures are lists (coordinates and values), not character grids; a dense one-dimensional series, such as terrain, is a string with one character per entry.
- **Static geometry** (flag `G`) is sent once and cached by the client: things fixed for a type/rules/options, such as a board's size and the positions of special squares.
- There are no keepalives, and nothing is pushed to a client that did not ask (`WAT`). The one exception is the `ERR` line the server sends just before it closes a connection, `ERR IDLE` or `ERR LIMIT`.

## Turn timer

Every table has a time per turn, set by its creator (`turn=` on `NEW`). When it is a player's turn the clock starts; if it runs out, the game ends with `END timeout seat` and that player loses. In a game flagged `drop` (Rummy Tile Classic, Dots and Boxes, Cannon Fire) with more than two seats still playing, a resignation or a timeout takes only that seat out of the game (`GONE seat resign`, `GONE seat timeout`) and the others play on; once two seats are left, the next one ends the game as above. Disconnecting does nothing: a player who goes away keeps their seat and their clock keeps running. Only resigning (`RES`) or running out of time loses a game without finishing it.

## Invitations and private tables

A table is public or private, chosen by its creator (`vis=` on `NEW`). Anyone may sit at a public table; a private table's seats are only for its creator, players already seated and players invited there, and only they see it in `LST` or may watch it.

Any player seated at an open table, public or private, can invite another player by name (`INV`), optionally holding a seat for them; nobody else can sit in a held seat. The invitation shows in the invited player's `IVL` and as an `INVITE` event at the table.

An invitation stays pending until something ends it. If the player sits down with `JON`, they get the held seat and the table shows a `JOIN` event. Every other ending shows an `UNINVITE` event with a reason:

- The player declines with `UNV table theirname`. The reason is `declined`.
- A player seated at the table withdraws it with `UNV`. The reason is `withdrawn`.
- An hour passes without the player accepting it. The reason is `expired`. Sending `INV` again starts the hour over.
- The player who made the invitation stands up from the open table. The reason is `left`.
- The game starts without the player. The reason is `started`.

If the whole table goes away, its invitations disappear from `IVL` without an event. That happens when the last player stands up, when the operator ends the table, or when it stays open for six hours.

The inviter is seated at the table, so they see these events through `SIN`, `WAT` or a `MOV` reply. The invited player sees invitations come and go in `IVL`. An invitation to a player who is not online is kept the same way, and `INV` answers `OK offline` to say so. Each frontend shows invitations in its own protocol's form and maps its own accept and decline commands to `JON` and `UNV`, as docs/design/invites.md describes. The server keeps invitations, visibility and held seats in its database. Telling a player who is not connected that they have been invited is left to the notification layer (docs/architecture.md).

## Storage

The server keeps players, sign-in keys (as hashes), tables, game state, events, invitations, each table's notes and data, the data kept with `PRF`, the turns played, the players' profiles and their scores (each player's totals, and the points each game gave each player) in SQLite (`--db`), written as they change, so a restart loses nothing. Each event a player's move made is stored with the player and the program and machine their connection named (`CLI`). A database from an earlier version gains the tables and columns it lacks when the server starts, and keeps everything it had.

## Staged turns (compound moves)

For types flagged `staged`, a turn is a series of steps. `MOV` applies one step tentatively (`OK more` while the turn is incomplete, `OK done` when it is complete), `UND` takes back the last step, `END` commits the turn. Validation happens as steps are made and again at `END`. A one-step game commits on its first `MOV`.

## Game identity: type and rules

- **type/ver** is the mechanics contract: the typed view lines, the event kinds and the move-token grammar. A frontend, or a server extension that draws games (such as telnet), is written against a type/ver. Example: `backgammon/1`.
- **rules** is a ruleset within that type, for example `checkers/1` with `american` or `brazilian`. Rules change what is legal, not the shape of a view or a move, so a frontend or server extension for a type works on any server's ruleset for it. The server enforces rules; the client does not need to know them.
- **options** (`NEW ... key=val`) are table tweaks the ruleset allows (a clock, a variant flag).
- A change to the view lines, event kinds or move grammar is a new type/ver, not a new ruleset.
- Servers host whatever games they like and list them in `GAM`.

## Type specs

Every type/ver has a spec (docs/types/) that defines: its view lines, event kinds, move tokens, staged-turn pattern, worst-case view and typical event size, and a set of recorded games as conformance vectors. It must also have a reference 80x25 ASCII rendering in the telnet extension, so every game works on a character screen.

## Time allowance (`TIM`, `ERR LIMIT`)

Every connection on the public port is in a class. A connection that a dial-in gateway carries for a call is in the dial-in class, `dialin` (Dial-in gateways, above). Every other connection is in the default class, `default`. Each class can have three limits, and each limit is a setting in whole minutes (accounts.md, Admin panel), where 0 means no limit:

- `CLASS.call_mins` limits one connection. For a call it counts from when the call came up, so the seconds the gateway gave in `0xE4` count too.
- `CLASS.user_month_mins` limits one player in a calendar month, in UTC. It counts only the time the player is signed in on connections of the class.
- `CLASS.pool_month_mins` limits the whole parlor in a calendar month. It counts every connection of the class from when it connects, signed in or not.

The dial-in class has 75, 60 and 150 minutes by default. The default class has no limits unless the operator sets some. Connections of server extensions on the extension channel have no time allowance.

A connection's time allowance is the smallest of the time its call has left, the time its player has left this month and the time the pool has left. `TIM` gives it as `left=` and the seconds, or as `left=unl` when none of the limits applies. It also gives the call's time left as `call=` and the player's month as `month=`, so a client can say each limit on its own. The pool has no field of its own. When the pool is the shortest, `left` is below both. Before signing in, only the call and the pool apply, and `month` is `unl`. Signing out ends the charge to the player, and the call goes on.

At 5 minutes left, and again at 1 minute, the server tells the player. It puts an `INF` line in front of its next reply on that connection, whatever the request was, such as `INF This call ends in 5 minutes.` A client shows it as it shows any other `INF` line. When the time runs out, the server sends `ERR LIMIT` with the limit that ran out and closes the connection, without waiting for a request:

```
ERR LIMIT closing: this call has had its 75 minutes
ERR LIMIT closing: you have used your 60 dial-in minutes this month
ERR LIMIT closing: the parlor has used its 150 dial-in minutes this month
```

A player whose month is used up can still sign in. The reply to the sign-in carries `INF You have used your 60 dial-in minutes this month.` and `INF This call closes in 20 seconds.` Twenty seconds later the server sends `ERR LIMIT` and closes the connection. When the pool is used up, a new connection of the class gets the greeting, then `ERR LIMIT` at once, and is closed.

## Rate limits

The server limits requests, sign-ups and sign-in attempts itself, per connection and per client address, and locks a name after repeated wrong passwords; security-design.md (Lockouts and rate limits) gives the numbers.
