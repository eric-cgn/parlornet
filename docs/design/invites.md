# Invitations across the frontends

This note covers the core's model of invitations and, for each protocol, what it did before this work and what it does now.

## The core's model

An invitation belongs to an open table. It records who is invited, who invited them, the seat held for them if any, and when it was made. The full rules are in docs/protocol.md under "Invitations and private tables".

A player seated at an open table invites someone with `INV table name [seat]`. Anyone with an account can be invited. If the invited player is not online, the core still keeps the invitation and answers `OK offline` instead of `OK`, so the frontend can tell the inviter.

The invited player sees the invitation in `IVL`. Everyone at the table sees an `INVITE` event. The invited player accepts by sitting down with `JON`, which puts them in the held seat. They decline with `UNV table theirname`.

An invitation can end in several other ways. A seated player can withdraw it with `UNV`. It expires an hour after it was made, unless the inviter sends `INV` again to renew it. It ends when the inviter stands up from the open table, and when the game starts. If the table goes away altogether, the invitation disappears from `IVL`.

Each of these endings, except sitting down, produces an `UNINVITE seat name why` event. The last word says why: `declined`, `withdrawn`, `expired`, `left` or `started`. The inviter is seated at the table, so these events are how the inviter learns what happened. The invited player learns about new and vanished invitations from `IVL`, which every frontend checks every few seconds.

`WHO` lists the players who are online now, including the bot while it runs. Every frontend builds its own player list from it. Online means signed in and active recently, as docs/accounts.md explains under "Who is online".

The bot (docs/bot.md) is invited like anyone else. It sees the invitation in its own `IVL` and sits down within a few seconds.

## IOS

IOS serves Reversi on port 6503, for Reversi The Eclipse and for terminals. IOS has no tables. One player challenges another by name with `match`, and the other answers with `accept name` or `decline name`. Eclipse takes the name from its player list, which is the reply to `who`. It shows an Accept dialog when it reads a line of the form `<ios> MATCH ... FROM name ...`.

Before this work, `who` listed only the IOS players and the players seated at tables. The bot is never seated until it is invited, so it never appeared, and Eclipse users could not pick it. When an IOS player matched someone who was not on IOS, the adapter opened a table but did not invite anyone. That is why matching the bot did nothing. Offers between two IOS players were kept inside the adapter, so the web and telnet never saw them. An invitation made on the web never reached an IOS player.

Now `who` lists the players who are online, and the bot is among them. `fi bot` explains that it is the server's bot.

When a player types `match X`, in Eclipse's form or the terminal form, the adapter tries three things in order. If X has already invited this player, it accepts that invitation. If X is waiting alone at an open public Reversi table, it joins that table. Otherwise it makes a new table, seats the player in the color they asked for, and invites X with the other seat held. The challenger's minutes become the time per turn. If no player has that name, the adapter says so and removes the table.

Every invitation to an IOS player now arrives as an `<ios> MATCH` line with `FROM`, followed by its second line and a sentence for people at a terminal. This works whether the invitation came from IOS, the web, telnet or the native protocol. Each session checks its invitations every three seconds. When one IOS player challenges another, the second session checks at once. An invitation to a game other than Reversi arrives as a line of text.

`accept X` sits down at X's table, and `decline X` declines the invitation. The challenger then sees "X declines the match." If the invitation runs out instead, the challenger sees "Your challenge to X has expired."

## FIBS

The FIBS adapter serves Backgammon on port 6505, for MacFIBS, FIBS/W, xfibs, xibc and CLIP clients. On FIBS, a player types `invite name n` to offer an n-point match. The other player sees "name wants to play a n point match with you." and then "Type 'join name' to accept." They accept with `join name`. FIBS has no command to decline. An invitation nobody answers stays until the inviter makes another.

Before this work, `invite` already made a private table and invited the other player to seat 1, and `join` already accepted an invitation. The `who`, `rawwho` and CLIP lists did not include the bot. An invitation made on the web or by telnet arrived as one long line. xfibs only recognizes "Type 'join name'" at the start of a line, so it never offered to join. The inviter was never told when an invitation was declined or had expired.

Now the who lists show the players who are online. The bot always shows as ready, and `whois bot` explains how to play it. `invite bot 1` starts a game against the bot.

An invitation from anywhere now arrives as FIBS's two lines. If the match was offered on FIBS, the line gives its length. Otherwise it says "a 1 point match", because a table made elsewhere plays a single game. An invitation to another game arrives as one line of text.

ParlorNet adds a `decline name` command, which `help` lists. When an invitation is declined, the inviter sees "** X declines your invitation." When it expires, they see "** Your invitation to X has expired."

## Outland

The Outland adapter serves Backgammon on port 6506, for the Outland Gateway. In Outland, a player sends a challenge record to someone in the lobby list. The other player's client shows a dialog with Accept as White, Accept as Red and Decline, and sends back a response record.

Before this work, the lobby listed the Outland players and the players at Backgammon tables, but not the bot. A challenge to anyone who was neither an Outland player nor waiting alone at an open table was declined at once. Invitations made elsewhere never reached an Outland player.

Now the lobby lists the players who are online, and the bot is always available.

A challenge can go to someone who is not an Outland player and is not waiting at an open table, such as a web or telnet player or the bot. In that case the adapter makes a private table with the cube turned off. It seats the challenger at seat 0, which is Red, and invites the other player to seat 1. The client waits for the answer. The adapter reports the challenge as accepted when the game starts. It reports it as declined if the invitation is declined, withdrawn or expires.

An invitation to an Outland player from anywhere arrives as a challenge from the inviter, as long as the player is free in the lobby. If the inviter is not in the client's list yet, the adapter adds them first. When the player accepts, the adapter sits them in the held seat and shows the board in the color they picked. When they decline, it declines the invitation. An invitation to another game arrives as a notice.

The same connection also serves Outland Reversi, which has its own waiting room and its own invitation. A Reversi player invites someone from the waiting room's list and chooses to move first or last. The invited player's client asks "Would you like to have a game of Reversi with ...?" and says whether they would move first or last. Its Yes or No goes back at once. The waiting room lists the players who are online, the bot among them, as the Backgammon lobby does. An invitation to another Outland Reversi player is relayed to them. An invitation to a player waiting alone at an open Reversi table sits the inviter down there. An invitation to anyone else makes a private table, seats the inviter in the seat they chose and holds the other for the invited player. The inviter's "Inviting your party!" window closes when the game starts. If the invitation is declined or expires, a line says which and the client's own "doesn't want to play" message closes the window. Cancel in that window withdraws the invitation. An invitation to an Outland Reversi player from anywhere arrives as that question, and Yes sits them in the held seat. A Backgammon invitation is left to the Backgammon window when both are open, and any other game's arrives as a line from the server.

## GameMaster

The GameMaster extension serves Dots and Boxes, Reversi, Naval Combat and ParlorNet's seven own games on port 6508, for GameMaster on the Mac (1991). GameMaster has no lobby and no player list. A Mac calls another Mac and uploads a new game, and the called Mac plays the other side. It sends no user name either, so a Mac plays on a parlor only through ParlorNet's own rulebook for GameMaster, which signs the player in and starts each game over its one connection, naming the opponent the player chose.

When the rulebook starts a game, the extension seats the player at seat 0, because GameMaster's caller always plays the side that moves first. For a named player or the bot it opens a private table and invites them to seat 1, then waits up to three minutes for them to sit down. For anyone it first takes up an invitation to the player, then an open public table of the game where someone waits. A named player's own invitation is taken up the same way. Seat 0 is taken when it is free. In Othello and ParlorNet's own games, when the other player already holds seat 0, the extension asks the Mac to swap players (`~Request to swap`). After `~Swap` the GameMaster player takes seat 1, and after `~Dont Swap` the game ends. Otherwise it opens a public table that web and telnet players can join, and invites the bot if nobody joins within a minute. The Mac is told the game is on (`~Play it Sam`) once the table starts, or turned down (`~sorry, not interested`) if nobody comes.

The rulebook's lobby window asks for the player's invitations (`IVL`) every five seconds and shows one at a time with Accept and Decline on its New game tab. Accept starts that game through the rulebook's connection with the inviter as the opponent, which takes the invitation up; Decline sends `UNV`. Its Opponent menu invites a player by name, as `/invite NAME` does: a player online from `WHO`, or any name typed after Other.... Its All games tab lists the open public tables where someone waits, and Join sits down there as `/join TABLE` does. Dots&Boxes and BattleShips cannot swap sides once connected, so for those two only an invitation that leaves seat 0 free can be taken. Othello and ParlorNet's own games swap.

## batnav

The batnav extension serves Naval Combat on port 6514, for the clients of Batalla Naval (1995-98). batnav's own server had no invitations: whoever had sent a board when someone pressed Start was in the game. On a parlor, the player sends the shared chat commands as messages from the client: `/invite NAME` (the bot too), `/open`, `/join TABLE` or `/join NAME`, `/accept NAME` and `/decline NAME`. The extension checks the player's invitations every three seconds and shows each new one as two lines in the message window, with the table and the commands that answer it. An invitation to a Naval Combat table with other rules than batnav's says that batnav cannot play it, and `/accept` takes the player's batnav invitations first. The inviter learns of a declined or expired invitation in the same window.

## ICDS

The ICDS extension serves Checkers on port 6518, for xcheckers and terminals. ICDS, like FICS before it, had no tables: players challenged each other with `match`. `match X` works as on IOS: it takes up X's invitation if there is one, or joins X's open public Checkers table, or makes a public table, seats the player on the side asked for (`black` or `white`; else either) and invites X with the other seat held, so other players can watch the game with `observe`. A number after the name sets the minutes a turn. `match bot` plays the bot. Each session checks its invitations every three seconds, and at once when another ICDS player challenges it; each new one arrives as a `Challenge:` line naming the challenger, their side and the table, then the words that answer it. `accept X` and `decline X` answer it; with one invitation waiting, `accept` and `decline` alone do, and while a draw offer waits for the player's answer they answer that instead. A declined or expired challenge is told to the challenger, and its table is closed. An invitation to another game is a line of text. The shared `/invite`, `/open`, `/join`, `/accept` and `/decline` work as well.

## Telnet

Before this work, telnet players could invite the bot with `bot` at a table or `new ... bot` in the menu. Nothing else worked. They never saw invitations sent to them, and they could neither decline one nor invite a person by name.

Now the menu lists the player's invitations. The plain menu shows them under "Invitations for you". The full-screen menu shows them at the top of Your tables and gives a notice when a new one arrives. Enter or `join n` accepts an invitation, and `X` or `decline n` declines it.

At a table, `invite name` invites a player and `uninvite name` withdraws the invitation. The table's log says how each invitation ended. `who` lists the players who are online.

## Web

Before this work, the lobby already listed "Invitations for you" with Join and Decline links. A table page could invite a player by name and offered "Invite the bot". The table log said "X is no longer invited" however the invitation ended.

Now the table log says how each invitation ended, and the lobby lists the players who are online.

## The native protocol

`INV`, `UNV` and `IVL` work as before. `UNINVITE` now ends with its reason, and `INV` answers `OK offline` for a player who is not online. `WHO` lists the players online, and `ACT` reports a player's activity. docs/protocol.md and docs/pnp.md describe them. A client written before these changes sees an extra word after `UNINVITE` and after `OK`, and can ignore it.

## GGS

GGS dates from about 1999 and is still running; no work on it is planned.

## Not verified

Some behavior could not be checked without the period clients themselves.

- IOS names are at most 8 characters, but a ParlorNet name can be longer. The adapter sends the inviter's full name in the `MATCH` line, and nobody has checked how Eclipse's Accept dialog handles a longer name.
- An Outland client now waits a few seconds for the bot to answer a challenge. Nobody has checked how the client behaves during that wait. Nobody has checked either how it handles a challenge from a player who was added to its list just before.
