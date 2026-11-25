# Auth, deployment and federation (draft 0)

This file covers who may connect and how servers relate; architecture.md has the layering.

## Deployments

- **LAN / event (e.g. VCF):** one server, no accounts. The network is the trust boundary. `HELO name` claims a handle (sign-in `none`) for as long as the connection lasts; optional table PINs; admin kick/ban; tables expire. A read-only spectator frontend can drive a scoreboard display.
- **Open internet:** the server keeps its own accounts (accounts.md): sign-in by password, sign-up by email, with rate limits and lockouts in the core. The native protocol on 6501 is plain text and open to the internet, because period machines cannot do TLS. Server extensions speak the other protocols and ParlorNet Play adds HTTPS; each signs its players in through the core over its own trusted channel, and the core asks them the same questions it asks a native client.

## Federation (transport layer)

- Every table is hosted and arbitrated by exactly one server (its host); there is no replicated game state.
- Players are `user@domain`; the home server vouches for them with signed requests. A remote player reaches a table through their home server, acting as a server extension.
- Servers advertise their hosted games (type/ver and rulesets) and open tables to each other: a federated lobby. A remote client sees the same type/rules listing as a local one.

### ActivityPub as the server-to-server basis (decided direction)

- **Used for:** server and player identity (actors, WebFinger), discovery (actor collections for hosted games and open tables), signing (HTTP signatures), and challenges, invitations and session setup (activities delivered to inboxes). Our own extension types (`Table`, `Game`, `Move`) carry game data; servers that do not understand them ignore them.
- **Not used for live turns.** ActivityPub is asynchronous and retried, fine for the lobby, invitations and correspondence-speed play, but slow for live games. Once a remote player is seated, their home server holds a persistent signed connection to the host and carries the ordinary client protocol (protocol.md) over it.
- **Optional.** A LAN parlor, such as one at an event, needs none of it.
- **Humans see it too.** Because it is ActivityPub, the human-readable layer (notifications) can later use the same actors; that stays a separate layer (architecture.md).
- snac and snarc are the reference implementations for the actor, WebFinger and signature mechanics.

## Notifications

Notifications are layer 4 of architecture.md. ParlorNet Play's push notifications are the first (play-notifications.md); posts and email are left for later.
