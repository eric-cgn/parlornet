# Backlog

These ideas are recorded for later. None is designed or approved, and each needs its own design pass with the user before work starts.

## Notifications

Telling a player who is not connected that something needs them: an invitation, their turn, a move by the opponent, a game ending, a turn clock running low.

ParlorNet Play sends push notifications for a player's turn, a game's end and someone sitting down at a waiting table (docs/play-notifications.md), from the core's notices (`NFY`). Other channels and events are still open.

Questions to settle before building:

- Which events deserve a notification at all, and how players choose (per event, per game, quiet hours, a digest).
- Channels: email (we already send sign-up mail through an SMTP relay, with strict caps and bounce rules), ActivityPub/Mastodon direct posts (readable by server admins, so never hidden information), and anything a period client can show when it next connects (an inbox at sign-in).
- Abuse and cost: invitations from strangers, floods, sending limits and reputation (docs/accounts.md, email sending limits), opting out, and never revealing private information.
- Privacy: what a notification may contain (no hidden game state), where addresses are stored, and deletion.
- Layering: notifications stay a separate layer that observes table events (docs/architecture.md), never part of the core or the games.

## Answering the 1992 call for rulebooks

When ParlorNet is released, answer Rhys Hollow's February 1992 call for GameMaster rulebooks where the GameMaster community can see it: ParlorNet now has six new rulebooks and a way to play all nine games over the Internet. The kit's co-authors are the people most likely to pass it on.

## Other candidates

- KOTH, the Unix artillery game, for Cannon Fire (tabled 2026-11-23). Every KOTH client flies each shot and digs each crater itself; the server only passes the shot on, though it can reset the terrain, the tanks' positions and armor and the wind at any time. ParlorNet's Cannon Fire physics therefore cannot drive what a KOTH player sees. The options are a correction after every shot, a `koth` ruleset of Cannon Fire with KOTH's own physics written from its behavior (KOTH is GPL), or leaving it out. The 1999 version is never told whose turn it is, so the work would target 0.8.0 (2003).
- Dome Wars (PowerPC Mac) for Cannon Fire: each Mac simulates every shot, so it would need Dome Wars' own physics on the server. Not planned.
