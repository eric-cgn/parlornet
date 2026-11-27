# ParlorNet Play notifications

ParlorNet Play ([modern-client.md](modern-client.md)) can tell a player when it's their turn, when a game they are playing ends, and when someone sits down at a table of theirs that is waiting for players, even when Play isn't open. They are push notifications, sent through the browser's own push service. A player turns them on separately on each phone, tablet or computer, and chooses in Settings which of the three they want; that choice applies to all their devices. Play has the same instructions on its own page, linked from its Settings.

Notifications are offered only when the parlor is reached over https and its operator has set a contact address (For operators, below). Without them, Play's Settings has no Notifications section.

## What a notification says

A notification names the game, the table's number and the other player, such as "Your turn in Reversi. Against ann. Table 12." When someone sits down, it also says what they play with, when their program said so: "bob sat down at your Reversi table. With GameMaster 1.0.0 on a Macintosh Plus. Table 12." It never includes a move, a position, a rack, a score or anything said at the table.

The message travels through the browser's push service: Google's for Chrome, Microsoft's for Edge, Mozilla's for Firefox, Apple's for Safari and for Play on an iPhone or iPad. It is encrypted for the browser (RFC 8291), so the push service cannot read it, though it can see that a message was sent and when.

While Play is open and in front of the player, nothing is sent: the page shows the news itself.

## On a computer

**Chrome and Edge.** Open Play, sign in, and choose Settings (the gear at the top right). Under Notifications, choose Turn on for this device, and then Allow when the browser asks. If the browser doesn't ask, notifications were blocked for the site earlier: click the icon at the left of the address bar, set Notifications to Allow, reload the page and try again. Chrome and Edge show notifications while the browser is running, even with no Play tab open. On a Mac, System Settings, Notifications must also allow Google Chrome or Microsoft Edge; on Windows, so must Settings, System, Notifications.

**Firefox.** In Play's Settings, choose Turn on for this device, and then Allow. If Firefox doesn't ask, click the icon at the left of the address bar, remove the block on notifications, reload and try again. Firefox shows notifications while it is running. On a Mac, System Settings, Notifications must also allow Firefox.

**Safari on a Mac.** With Safari 16 on macOS Ventura or later, choose Turn on for this device in Play's Settings, and then Allow. If Safari doesn't ask, open Safari, Settings, Websites, Notifications, set the site to Allow, and try again.

## On an Android phone or tablet

**Chrome.** In Play's Settings, choose Turn on for this device. Chrome may first ask whether Chrome itself may send notifications; allow it, and then allow Play. If nothing is asked, tap the icon at the left of the address bar, then Permissions, and turn Notifications on. Android's Settings, Apps, Chrome, Notifications must be on as well. Play can also be added to the home screen from Chrome's menu (Add to Home screen, or Install app); notifications work the same either way.

**Firefox.** In Play's Settings, choose Turn on for this device, and then Allow. If Firefox doesn't ask, tap the icon at the left of the address bar and allow notifications there. Android's Settings, Apps, Firefox, Notifications must be on as well.

## On an iPhone or iPad

Notifications need iOS or iPadOS 16.4 or later, and they come only to Play added to the Home Screen, not to a Safari tab.

1. Open Play in Safari, tap the Share button, then Add to Home Screen, then Add.
2. Open Play from its new Home Screen icon. It keeps its own sign-in, so sign in there.
3. In Play's Settings, choose Turn on for this device, and then Allow.

Later, how they appear, or whether they come at all, is set in the iPhone's Settings, Notifications, Play.

## Turning them off

- In Play's Settings, Turn off for this device. The player's other devices keep theirs.
- Untick the kinds not wanted; that applies to every device.
- Signing out of Play also turns notifications off for that device, so a shared computer gets none for the player who signed out.
- Blocking notifications for the site in the browser, or removing Play from the Home Screen, stops them too; the server forgets that device the next time the push service says it is gone.

## How it works

- **The page.** `sw.js` is a service worker scoped to `/play/` that only shows notifications and opens the table when one is clicked; it caches nothing. `manifest.webmanifest` makes Play a web app that iOS and iPadOS can put on the Home Screen. In Settings, Turn on asks the browser's permission, subscribes with the Play extension's public VAPID key (which `index.html` carries in its `pn-push` meta tag while push is on) and sends the subscription to `POST /play/push`, from this site's pages only, with the session cookie. A subscription the browser still holds is sent again after each sign-in, in case the server dropped it.
- **The extension.** `adapters/playpush.py` keeps each account's subscriptions in `push.db` in its data directory, at most 10 an account. It writes the kinds the player wants, with a token of its own, into the player's `PRF` key `notify` (Play's namespace, shared with the web pages); subscriptions carry that token, so those of a deleted account never reach a later account of the same name. It learns of turns, ends and joins from the core with `NFY` ([protocol.md](protocol.md), Notices), with the `NFX CLIENT` line that says what a player who sat down plays with, on one connection that waits for more as soon as it has an answer. It sends nothing for a player whose Play page asked something in the last 12 seconds.
- **Sending.** Each message is encrypted with a new key pair for the browser's keys (RFC 8291, the `aes128gcm` coding of RFC 8188), signed for the push service with VAPID (RFC 8292, ES256), with a TTL of a day and a topic per table, so a newer message for a table replaces one not yet delivered. The cryptography is the standard library and `adapters/pushcrypto.py`, with no package added; tests/test_push.py checks it against the RFCs' examples and NIST's vectors. Four worker threads send, each request with a 10-second timeout, so a slow push service never holds up play. A subscription answered with 404 or 410 is deleted. The extension posts only to the browsers' push services (Google's, Mozilla's, Apple's and Microsoft's), never to an address a page made up.
- **What it keeps.** The subscriptions (the push service's address and the browser's two public keys) and the VAPID private key, made on the first start in `vapid.key` with mode 600. The key is never printed or logged; deleting it makes every browser subscribe again.

## For operators

Push notifications are on when all three of these hold:

- `play.public_url` (`PARLOR_PLAY_URL`) is an https address;
- `play.push_contact` (`PARLOR_PLAY_PUSH_CONTACT`, `--push-contact`) is your own `mailto:` or `https:` address, which push services may use to reach you about the messages this server sends;
- the extension has a data directory (`--data`; in the container, `play/` beside the database, which deploy/start.py makes).

The container must be able to reach the push services over HTTPS; the networks of deploy/install.sh and deploy/install-extensions.sh allow that, while the split layout of deploy/install-split.sh gives each extension a network without a way out, so there notifications stay off. The log says at start whether push is on, and why not when it is off.
