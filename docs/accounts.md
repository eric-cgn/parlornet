# Accounts, sign-in, sign-up and administration

Accounts are three separate pieces, each replaceable:

1. **Sign-in and sign-up methods** (how a player proves who they are, and how an account is made): two pluggable registries, run as one dialog the server drives. The operator picks one of each.
2. **Account management** (`parlord/accounts.py`): players, sessions, lockouts, suspending, resetting, deleting.
3. **The admin panel**: configures the server from what is installed.

The table host only ever sees a player id and a display name; games see seat numbers. The threat model, the trust boundaries and the reasons behind this design are in docs/security-design.md.

## Methods

**Sign-in methods**, checked at every sign-in:

| Method | Credential | Recovery |
|---|---|---|
| `none` | the name alone; anyone may be anyone (a LAN, a festival) | - |
| `password` | a password the player chose, stored as a salted scrypt hash, or a temporary password the server mailed or the operator gave (below) | with an address on file: answer `reset` at the password question, and a temporary password goes by mail to that address; the old password keeps working until the temporary one is used. Without an address, the operator's reset gives a temporary password and the old one stops working |

**Sign-up methods**, which make the account and set up what the sign-in method needs:

| Method | Steps |
|---|---|
| `none` | nothing: the name is claimed |
| `password` | choose a password, type it again |
| `email` | confirm the name (y/n; asked only after `HELO`, not after `REG`) and give an address. The server makes a pending account and mails it a temporary password; the dialog then asks for that password, which can also be typed at a later sign-in. The first sign-in with it confirms the account and asks at once for a password of the player's own, typed twice. An address that already has a name gets a notice instead ("someone asked to sign up with this address; your name is ..."), and the dialog goes on exactly as for a free address, so it never tells whether, or to whom, an address belongs. An address gets one sign-up email in 7 days, and at most a set number of pending accounts wait at once (Sign-up limits, below) |

Verification is always a value typed back (the mailed password), never a link to click.

The pairs that work are `none` + `none` (a festival), `password` + `password` (the container's default) and `password` + `email` (parlornet.org). Anything else is refused: sign-in `none` needs nothing set up, and sign-in `password` needs a sign-up that sets one. The admin menus refuse such a pair with the reason (changing the sign-in method brings the sign-up it needs); a pair from the command line that cannot work falls back to the sign-in method's usual sign-up with a warning in the log.

The greeting names both: `PARLORNET/1 parlornet.org AUTH=password SIGNUP=email`, with `LABEL=...` only if the operator set a label of their own. More methods can be added to either registry (an external directory, federated identity) without client changes, because of the dialog below.

## Passwords and temporary passwords

**The rule.** A password a player chooses is 8 to 64 printable ASCII characters (spaces are kept inside, but not at either end) and uses at least three of four kinds of character: lowercase letters, capital letters, digits and symbols, where a space counts as a symbol. A password that breaks the rule is refused with the reason in plain words, such as "That password uses only two of the four kinds", followed by the rule, and the question is asked again. The same rule holds at a sign-up, at a change (`PWD`) and after a temporary password.

**Temporary passwords.** The server makes one for a sign-up by email, for a forgotten password (`reset`) and for the operator's reset. It is three groups of four lowercase letters and digits, such as `abcd-efgh-jkmn`, from an alphabet without look-alike characters (no 0, o, 1, l or i), about 59 random bits. Case, spaces and dashes do not matter when it is typed, so it is easy to read off a mail and type on an old keyboard. It works for 24 hours. Only its scrypt hash is stored, with the time it stops working. Signing in with it asks at once for a new password, typed twice, before the player is signed in, and the new one may not be the temporary one again. While a name is locked after wrong answers, a temporary password is still checked, so a player whose name is under attack can recover by mail.

**Pending accounts.** Sign-up `email` makes the account as soon as its mail step is done, as a pending account: it holds the name, keeps the address and the hash of its temporary password, and has no password of its own. Signing in with the temporary password confirms it. A pending account that nobody signs into within 24 hours is deleted by the core's sweep (every 10 seconds), and its name is free again. Until it is confirmed, `reset` does nothing for it (it is checked as a wrong password), so the only way in is the mailed password.

**Forgotten passwords.** Answering `reset` at the password question of a confirmed account with an address on file mails a new temporary password there. The old password keeps working until the temporary one is used, so a stranger who asks for a reset changes nothing for the owner. The operator's reset is different: it gives the operator a temporary password to pass on, and the old password stops working at once.

## Wrong answers

Every wrong answer counts toward the name's lockout: a wrong password, or a wrong temporary password. The name is locked 1 minute after one, 15 minutes after three, growing by the same factor each time, at most 4 hours; it survives restarts. Each 8 hours without a wrong answer halve the count. The refusal carries the reason and the wait for programs: `ERR LOCKED wait=60 Wrong password; try again in 60 seconds`. A right answer clears the count, and so does a right temporary password (the mailbox is proven).

**Budgets per address and for the server.** A wrong answer for a name its client address has not signed in from in the last 30 days also counts against that address (12 a day) and the whole server (48 a day); once either is spent, untrusted addresses are refused before anything is checked (`ERR LOCKED wait=N Too many wrong answers from your address`, `ERR BUSY wait=N Too many wrong answers on this server; try later, or answer reset`). An address a player signed in from is never held up by them. docs/security-design.md has the arithmetic.

**A locked name can still recover by mail.** While a name with an address on file, or with a temporary password that still works, is locked, `HELO name` still asks the question, with a note that sign-in waits and, where there is an address, that `reset` gets a new password by email now. Until the wait is over only `reset` and the temporary password are checked; any other answer is refused without being checked. Recovery has its own limits instead: 2 recovery mails a day for one name asked from one client address, the mail caps below, and temporary passwords of about 59 bits. From an address the player signed in from before, the shared caps for the name and its address do not apply, so strangers asking for it cannot use up its owner's recovery. A name without an address is recovered by the operator.

## The dialog: sign-in, sign-up, recovery

**Sign in and New account are separate.** `USER name` signs in a name that has an account and refuses one that has not (`ERR NOPLAYER`); `REG name` makes a new account and refuses a taken name (`ERR NAMEINUSE`). So a mistyped name at a sign-in never becomes an account. `HELO name`, the original request, still does either (docs/protocol.md). The words `new` and `guest` are reserved: some frontends take them at their login prompt to start a sign-up (telnet's `new`, FIBS's `guest`), so the core refuses them as names under every method (`ERR RESERVED`), whichever request asks.

| Frontend | Sign in | New account | A name with no account at the login |
|---|---|---|---|
| web (6500) | Sign in (`/login.htm`): the name, and the credential box unless the server signs in by name alone (`USER`) | New account (`/signup.htm`): the name wanted, then the sign-up steps as pages (`REG`) | "There is no account named x here", with a link to New account |
| telnet (6502) | `Login (your name, or "new" for a new account):` with a name (`USER`) | `new` (any case), then the name wanted, then the steps (`REG`) | "There is no account named x here. To make one, type new at the Login prompt." |
| IOS (6503) | the name and password at the login (`HELO`) | the same: a free name runs the sign-up in the client's window, with its own questions (y/n and the address under `email`, the password typed again under `password`) | signs up |
| FIBS (6505) | the name and password at `login:` (`USER`) | `guest`, then `name x` and the steps (`REG`) | "There is no account named x here. New here? Log in as guest." |
| Outland (6506) | the Gateway's account name and password (`USER`) | on the web or by telnet | V 1 (no such account), with a pointer to the web and telnet |
| DOoM (6507) | `connect name password` (`USER`) | `request name`, then the steps as prompts (`REG`) | "There is no player named x here. To make an account, enter `request x'." |
| GameMaster (6508) | the name and the server's steps typed in the ParlorNet rulebook's window (`USER`) | on the web or by telnet | "There is no account named x here", with a pointer to the web and telnet |
| NetBoxes (6512) | the player name and password of the game's Join dialog (`USER`) | on the web or by telnet | the reason in the program's chat window, then the connection closes (NetBoxes shows no join denial) |
| batnav (6514) | `/signin name password` sent as a message from the client (`USER`); under sign-in `none`, the name the client sent when it connected (`HELO`) | on the web or by telnet | "There is no account x here. Make one on the web site or by telnet first." |
| Awale (6515) | `!signin name password` in Write to group (`USER`); under sign-in `none`, the name of the program's Network setup | on the web or by telnet | "There is no account x here. Make one on the web site first." |
| ICDS (6518) | the name and password at `login:` in xcheckers' terminal (`USER`) | `guest`, then the name wanted and the steps (`REG`) | "There is no account named x here. New here? Log in as guest." |
| NetLuff (6517, 1744) | `/signin password` in the chat box for the name NetLuff sent, or `/signin name password` (`USER`) | on the web or by telnet | "There is no account x here. Make one on the web site first." |
| Networdz (6516) | `/signin name password` in the program's Chat window (`USER`) | on the web or by telnet | "There is no account x here. Make one on the web site first." |
| Worthy Opponent (6519) | `/signin password` in the Talk window for the name the program sent, or `/signin name password` (`USER`) | on the web or by telnet | "There is no account x here. Make one on the web site first." |
| RSCARDS (6520) | the name in the first field, then each step the server asks in the same field, a password typed unseen (`HELO`) | the same: a free name runs the sign-up in the fields | signs up |

Under sign-in `none` a name is the whole sign-in and nothing can be lost to a typing mistake, so FIBS and Outland logins take a free name (`HELO`); the web and telnet keep Sign in and New account apart there too.

**The first sign-in with a temporary password** asks for a new password before anything else, so it needs a frontend that can ask a further question. The web, telnet, ParlorNet Play (`/play/`), IOS and FIBS clients (in the client's window), xcheckers (in its terminal), DOoM, the ParlorNet rulebook's window in GameMaster and native clients on 6501 all ask for it. The Outland Gateway, NetBoxes' Join dialog, batnav's message window, NetLuff's chat box and Networdz's Chat window cannot: their sign-in with a temporary password is refused with "Sign in on the web or by telnet first to choose your password." The temporary password still confirms a pending account there, and it keeps working on the web or by telnet until its 24 hours are up.

The server drives every flow a step at a time, and every step is one of a few generic kinds. Any server extension that can show a line of text and read one back can run them, so a player can sign up from a 1997 Mac client as well as from the web:

| Kind | On the wire | What a server extension does |
|---|---|---|
| info | `INF text` lines before an `ASK` (74 characters each) | show the lines |
| ask | `ASK field label` | show the label, read a line, send `AUT field value` |
| secret | `ASK field label secret` | the same without echo |
| confirm | `ASK field label yn` | a yes/no question; answer `y` or `n` |
| done | `OK ...` | signed in (see Credentials and sessions) |
| refused | `ERR code [wait=N] text` | show the text; `wait` is the seconds before trying again |

An `ASK` may also carry `cred`: it asks for the account's credential (the password) or for a password being chosen at sign-up. It may also carry `new`: a new password is being chosen. A question flagged `new` but not `cred` (the new password after a temporary one, and its confirmation) is never answered with a password a client took at its login screen. Info lines that only help (such as "Forgot it? Answer reset ...") are sent as `INF` too but are optional: a server extension that cannot show them still gets the question.

The password travels by mail, never through the server extension, and nothing but lines of text passes through it. So an email sign-up works on a text-only client:

    > REG tia
    < INF New account: tia.
    < INF We email you a password to sign in with; then you choose your own.
    < ASK email Email_address
    > AUT email tia@example.org
    < INF We emailed a password for tia to tia@example.org.
    < INF It works for 24 hours. Type it here, or sign in with it later.
    < INF If you haven't received the email, it may take a while;
    < INF you can ask again after 7 days.
    < ASK password Password_from_the_email secret
    > AUT password abcd-efgh-jkmn
    < INF That was a temporary password. Choose a password of your own now.
    < INF A password needs at least 8 characters (at most 64) and at least three of
    < INF these four kinds: lowercase letters, capital letters, digits and symbols.
    < ASK password New_password secret new
    > AUT password Tia plays 1997
    < ASK confirm Type_it_again secret new
    > AUT confirm Tia plays 1997
    < OK -

A password sign-up and sign-in:

    > REG bo
    < INF New account bo: choose a password.
    < INF A password needs at least 8 characters (at most 64) and at least three of
    < INF these four kinds: lowercase letters, capital letters, digits and symbols.
    < ASK password Choose_a_password secret cred new
    > AUT password Bo secret 1
    < ASK confirm Type_it_again secret cred new
    > AUT confirm Bo secret 1
    < OK -
    > USER bo                                  (another time)
    < ASK password Password secret cred
    > AUT password Bo secret 1
    < OK -

`PWD` (signed in, sign-in `password`) runs the change flow: current password, new password, the new one again; the player's other sessions end.

A second word on `HELO name x` is refused under sign-in `password` (`ERR SYNTAX`: the password is asked for) and ignored under `none`. Server extensions never send one.

**Server extensions never volunteer a credential.** A server extension signs in with the name alone. If its client supplied a password with the login (the IOS or FIBS password, the Outland Gateway's password, the web form's field), that value answers only a question flagged `cred`, and only the first one; every other question is asked with the server's own label. If the server asks nothing (sign-in `none`) or nothing flagged `cred` (the confirm, the address and the mailed password of an email sign-up), the supplied value is dropped: never sent to the core, logged or kept.

**What a server extension can show.** A server extension declares the kinds it can show with `CAN kind[,kind...]` (`text`, `secret`, `yn`, `info`) before `HELO` (or `USER`, `REG`); without `CAN` all four are assumed. When the next step needs a kind the server extension did not declare, the server refuses it with `ERR CANNOT this client cannot show the next step; use the web or telnet`. When that step is the new password after a temporary one, the refusal is `ERR NEWPASS Sign in on the web or by telnet first to choose your password.` instead.

| Server extension | Declares | Sign-in | Sign-up and recovery |
|---|---|---|---|
| web (6500) | all | POST forms: the welcome form's password field answers the credential question; other steps are pages (info paragraphs, a text or password box, Yes/No) | yes |
| telnet (6502) | all | lines and prompts, secrets not echoed | yes; `P` (or `password`) in the menu changes the password |
| IOS (6503), e.g. Reversi The Eclipse | all | the IOS password answers the credential question; with none, it is a prompt with the server's label (`Password: `) in the client's window, which Eclipse forwards while it waits for the first `<ios>` line; the new password after a temporary one is asked there too | yes, in the same window before the lobby: a free name runs the sign-up flow (`HELO`) |
| FIBS (6505): MacFIBS, FIBS/W, xfibs, CLIP | all | the password answers the credential question; other steps are prompts | yes, after `guest` and `name x` (the login itself only signs in) |
| DOoM (6507): BobGrid, telnet and MUD clients | all | the password answers the credential question; other steps are prompts | yes, with `request name` (the `connect` line itself only signs in) |
| Outland Gateway (6506) | `secret` | the password in the Gateway's login packet answers the credential question; a temporary password is refused with the pointer to the web or telnet (`ERR NEWPASS`) | no: a name with no account is refused (V 1) with a pointer to the web or telnet, where accounts are made |
| GameMaster (6508), in the ParlorNet rulebook's window | all | every step is a question in the window, answered in its field | no: a name with no account is refused with a pointer to the web or telnet |
| NetBoxes (6512), in the game's Join dialog | `secret` | the Join dialog's password answers the credential question; a temporary password is refused with the reason in its chat window, and the parlor's reason is logged | no: a name with no account is refused the same way |
| batnav (6514), in the client's message window | `secret`, `info` | the password typed after the name answers the credential question; a temporary password is refused with the pointer to the web or telnet | no: a name with no account is refused with a pointer to the web and telnet |
| Awale (6515), in Write to group | `secret`, `info` | the password typed after the name answers the credential question; a temporary password is refused with the pointer to the web or telnet | no: a name with no account is refused with a pointer to the web |
| ICDS (6518): xcheckers, terminals | all | the password answers the credential question; other steps are prompts in the terminal | yes, after `guest` (the login itself only signs in) |
| NetLuff (6517, 1744), in the program's chat box | `secret`, `info` | the password typed after `/signin` answers the credential question, for the name NetLuff sent or the one typed; a temporary password is refused with the pointer to the web or telnet | no: a name with no account is refused with a pointer to the web |
| Networdz (6516), in the program's Chat window | `secret`, `info` | the password typed after `/signin` and the name answers the credential question; a temporary password is refused with the pointer to the web or telnet | no: a name with no account is refused with a pointer to the web |
| Worthy Opponent (6519), in the program's Talk window | `secret`, `info` | the password typed after `/signin` answers the credential question, for the name the program sent or the one typed; a temporary password is refused with the pointer to the web or telnet | no: a name with no account is refused with a pointer to the web |
| RSCARDS (6520), in the front end's input fields | all | every step is a question above the field, answered in it; a secret one is typed into a field drawn in one color | yes: a free name runs the sign-up flow (`HELO`) |
| native clients (6501) | all unless they send `CAN` | as the protocol | as the protocol |

The shared helper for line-based adapters is `sign_in_lines` in `adapters/common.py`: an adapter gives it the name, the client's password, a `prompt(label, secret)` and a `show(lines)`, or neither if its client cannot be asked anything after its login.

## Credentials and sessions

After a successful check the accounts component creates a **session**, the same for every method including `none`:

- **On the extension channel** (docs/security-design.md: TLS 1.3, a pre-shared key per server extension) the reply is `OK sid`, where `sid` is 22 characters of `[A-Za-z0-9_-]` (128 random bits). Only its SHA-256 is stored. A server extension that serves many short requests (the web) resumes it on a new connection with `SES sid`; the session belongs to that server extension only and to the sign-in and sign-up pair in force (changing either ends every session).
- **On the public port** (6501, plain text for 8-bit clients) the session lasts as long as the connection and has no id; the reply is `OK -`. `SES` is refused there (`ERR NOTFRONTEND`).
- **Expiry** (settings): idle `session.idle_secs` (default 7200 seconds, checked when resuming) and absolute `session.max_secs` (default 86400, also cuts off a live connection).
- **Ending**: `OUT` ends the current session; `OUT all` ends every session of the player, live connections included (they get `ERR SIGNEDOUT` on their next request). A password change, a recovery, the operator's suspend, reset and delete end sessions too.
- **Wrong `SES`**: nothing is locked. A connection gets three tries, then `ERR RATE`; the core counts failures per server extension in its log (counts only, never ids). The web also stops resuming for a client address after 10 failures in 10 minutes.

What server extensions do with sessions:

| Server extension | Session |
|---|---|
| web | cookie `pn=<sid>; Path=/; HttpOnly; SameSite=Lax`, no expiry (ends with the browser); for a browser that returns no cookie, `s=<sid>` in links, decided on the first page after sign-in (the redirect carries a one-time 60-second handle, not the session). Every page resumes with `SES`. Actions carry `x=` (an HMAC of the session id) against cross-site requests. "Sign out" (`OUT`) on every page, "Sign out everywhere" (`OUT all`) and "Change your password" in the lobby |
| telnet, IOS, FIBS, Outland, DOoM, NetBoxes, batnav, Networdz, NetLuff, ICDS, Worthy Opponent, RSCARDS | the connection's own sign-in; `OUT` when the player leaves |
| Play | cookie `pnplay=<sid>; Path=/play/; HttpOnly; SameSite=Strict`, with `Secure` when `play.public_url` is https; the page gets a one-time 60-second handle after signing in over its WebSocket and trades it for the cookie (`POST /play/session`), so the session id never reaches the page. A reload resumes with `SES` |
| GameMaster | the sign-in on the ParlorNet rulebook's connection, which carries that Mac's games; each game's connections to the core resume it (`SES`). Sign out in the window (`OUT`), or closing it, ends it |

No server extension ever shows a credential or a session id.

## Who is online

A player with an account is signed up. A player is online only while they are signed in and doing something. Every who list shows only the players who are online. That covers IOS `who`, FIBS `who` and `rawwho`, CLIP's who rows, DOoM's `who`, ICDS's `who`, the Outland lobby, the chat consoles' `/who` (chat-commands.md), telnet's `who` and plain menu, the web lobby's "Online now" line, and the protocol's `WHO`.

A player counts as signed in while they have a live connection on any frontend, or a web session that has not ended. Live connections include the public port, telnet, IOS, FIBS and Outland. A web session ends when the player signs out, when it expires, or when the operator or a password change ends it.

A player counts as active if their last activity was no more than `presence.idle` seconds ago. The default is 1800 seconds, which is 30 minutes. The operator can change it in the admin menus under Server, Idle time, to anything from 1 minute to 7 days, or in the settings file. A connection that stays open but idle stops counting once that time is up.

Activity means things the player does. Signing in counts. So do the requests `NEW`, `JON`, `LVE`, `INV`, `UNV`, `RES`, `MOV`, `CHK`, `UND`, `END`, `CHT`, `PWD`, `PRF SET`, `PRF DEL`, `PFL SET` and `ACT`. Looking does not count. Frontends send `LST`, `IVL`, `WHO`, `VUE`, `SIN`, `WAT`, `SEE`, `GAM`, `MOTD`, `TIM`, `PRF GET`, `PRF LST`, `TRN`, `FEED` and `PFL` on their own every few seconds, so these requests never keep anyone online. Resuming a session with `SES` does not count either.

Some of a frontend's commands need no request to the core. Examples are a chat between two IOS players, `who`, or a FIBS toggle. For these, the server extension sends `ACT`. Telnet, IOS, FIBS, DOoM and ICDS report every key or line the player sends, and NetSprocket every chat line. Outland reports every record the player's Backgammon or Reversi program sends, and batnav every board, shot and message. Each of them sends `ACT` at most once every 30 seconds (`CoreClient.user_input` in `adapters/common.py`).

The web sends `ACT` for every page the player asks for. It does not send it for what a page fetches by itself. That means `/seq.htm`, which the page's script checks for news, and `/board.gif`. It also means the table page's own reloads, from the meta refresh or from the script, which carry `a=1` in the address.

The Outland adapter rolls the dice when its player's turn starts. That roll is a `MOV`, so it counts as activity, but it only happens during a game the player is playing.

The bot is online whenever its process is connected, whatever it last did. Its lobby connection keeps each bot name signed in.

The server extensions that announce arrivals and departures work from the same list, which they check every few seconds. When a player passes `presence.idle` without activity, the others get the protocol's own departure notice. On FIBS that is "X logs out.", on CLIP it is line 8, on IOS it is `<ios> [X disconnected`, and on Outland it is a player-left record. When the player does something again, the others get the arrival notice: "X logs in.", CLIP line 7, or `<ios> [X(-) connected`. Only activity brings a player back and only the idle time takes them away, so the notices do not repeat back and forth.

An invitation to a player who is not online is kept like any other. It waits in the player's `IVL` until they accept or decline it, until it is withdrawn, or until it expires after an hour. `INV` answers `OK offline` instead of `OK`, so the inviter's frontend can say that the player is away.

## Account management interface

`parlord/accounts.py`, class `Accounts`: the only code that decides who a connection is. The core calls `greeting`, `begin` (HELO, USER, REG), `answer` (AUT), `resume` (SES), `current` (every request), `sign_out` (OUT), `change` (PWD); the admin calls `suspend`, `reset`, `delete`; `sweep` drops expired sessions, deletes pending accounts past their 24 hours and forgets temporary passwords past theirs. It keeps players and sessions in parlord's SQLite database.

## Admin panel

Blue-screen menus over telnet, in the manner of dialog and whiptail (a blue screen, gray dialog boxes with shadows, highlighted buttons; arrow keys, Tab, Enter and Esc), served by their own operator extension (`adapters/admin.py`) on the admin port, **6510**. They are 80x24, in the pc (CP437), utf8 and ASCII character sets with ANSI color. The menus are:

- **Server**: name (in the greeting and in the mail the server sends), message of the day (a small text editor; the marker `{contribute}` in it is replaced by the contribute address), the contribute address (`site.contribute_url`, below), the docs address (`site.docs_url`: the docs site the web pages link to, for Docs and each protocol's key; the setting, else `PARLOR_DOCS_URL`, else http://docs.parlornet.org/; empty: the web links no docs, and the old documentation addresses answer Not found), default time per turn, and the names the web answers to (`web.hosts`; a redirect for any other Host goes to the first; empty: any; read when the container starts). It also sets the idle time after which a player no longer counts as online (`presence.idle`, described under "Who is online" above), and the most players signed in at once (`players.online_max`, below).
- **Sign-in**: the sign-in method (`none`, `password`) and the sign-up method (`none`, `password`, `email`), each explained, and the label; for sign-up `email`, the SMTP relay (host, port, user, a password typed hidden and never shown, From), the caps below with their counters, the sign-up limits (below), pause and resume sending, and a test email to an address the operator types.
- **Games**: which installed games and rulesets are offered (Offered: `GAM` lists only those and `NEW` refuses the others; running tables play on), and the limits on tables and games (below).
- **Adapters**: which server extensions the container starts (web 6500, telnet 6502, IOS 6503, FIBS 6505, Outland 6506, DOoM 6507, GameMaster 6508, admin 6510, NetSprocket 6512, Play 6513, batnav 6514, Awale 6515, Networdz 6516, NetLuff 6517, ICDS 6518, Worthy Opponent 6519, RSCARDS 6520, and the bot, which has no port); a change takes effect when the container restarts.
- **Accounts**: search by name or address; suspend (ends every session) and unsuspend; reset (`password`: a temporary password shown to the operator to give the player, who signs in with it within 24 hours and chooses a new password, while the old password stops working at once; any method: the lockout is cleared and every session ends); delete (every session ends, and the player's profile, its counts, their recorded turns, their score, the programs and machines kept with their moves, and what the tables were told they played with are deleted too). All of these go through the accounts component.
- **Tables**: list; end a table (a running game ends with `END abandoned`, an open table is removed).
- **Scores**: whether the leaderboard is shown (`scores.show`), the points for each result (below), and Reset scores, which after a confirmation sets every player's score back to nothing, at the start of a festival for example. The games stay recorded: their tables, events and turns, and the points each gave. The admin socket's request is `reset_scores`.
- **Status**: uptime, connections, players, tables, email counters, the budgets (Clear budgets), and the sign-ups: the pending accounts as "n of max", how long each may wait (24 hours), and how many addresses are in their cooldown and for how long. Its Sign-ups button clears the cooldown of one address, typed by the operator (hashed for the lookup, never stored or logged), or of every address after a confirmation ("Clear address cooldowns"). The admin socket's requests are `signups`, `clear_cooldown` (with `address`) and `clear_cooldowns`.

Settings live in the SQLite database and take effect at once; the command line (`--auth`, `--signup`) and the container's environment (`PARLOR_AUTH`, `PARLOR_SIGNUP`) only give defaults. A settings file (`--settings-file`) can supply secrets such as `email.smtp_password` without writing them to the database; a value set in the menus takes precedence. The limits above can go there too, as `games.active_max=30` and the like.

**Limits on players, tables and games.** Each is a whole number, set in the admin menus (Players online under Server, the rest under Games) or in the settings file; 0 or empty means the default.

| Setting | Default | Most | What it limits |
|---|---|---|---|
| `players.online_max` | 200 | 5000 | Players signed in at once, on every frontend together; a web session counts until it ends. Past it a sign-in is refused with `ERR BUSY wait=60 the server is full (200 players), try again later`. A player signed in already may sign in again elsewhere, and bots neither count nor are refused. `WHO` lists as many players, and the bots. |
| `games.active_max` | 20 | 999 | Games one player is playing at once. At that many they cannot sit down at another table until one ends: `ERR LIMIT you are playing 20 games, the most allowed here; finish one first`. Finished and resigned games do not count. |
| `games.waiting_per_type` | 2 | 99 | Open tables of one game type a player sits at, waiting for opponents: `ERR LIMIT you are already waiting at 2 Reversi tables; start or leave one`. Other games count on their own, and taking the seat that starts a game is not waiting. |
| `games.tables_max` | 400 | 5000 | Tables on the server, finished ones (kept a day) included (`ERR FULL`). Each takes up to about 100 KB of memory, so 400 stay near 40 MB, well within the container's share. |
| `games.open_hours` | 24 | 720 | Hours an open table stays while nobody joins it and nothing happens there. |
| `games.turn_max_days` | 5 | 30 | Days per turn a new table may set at most; `turn=` past it is `ERR BADOPT` with the maximum. Running tables keep their own, and the default time per turn (Server) is held within it. |
| `lists.max` | 20 | 200 | Tables `LST` lists besides the player's own tables and invitations, which are always all listed; a last line `MORE n` says how many more there are. |

**Scores.** Every finished game gives each player seated at it points (docs/protocol.md, Scores and the leaderboard), counted when the game ends with the values set then. Each value is a whole number from 0 to 99, set in the admin menus (Scores) or in the settings file; empty means the default.

| Setting | Default | What it is |
|---|---|---|
| `scores.win` | 4 | Points for a win; also for every other player still playing when one resigns or runs out of time. |
| `scores.draw` | 2 | Points for a draw, for each player sharing it (in a game of three or more, a tie for first). |
| `scores.loss` | 1 | Points for a game played to its end that someone else won. |
| `scores.resign` | 0 | Points for resigning, running out of time, or leaving a game of three or more. |
| `scores.show` | `on` | `off` hides the leaderboard and the scores on profiles on every frontend (`TOP` answers `ERR NOSCORES`); games are still scored. |

There is no protection against cheating, by design: on a public server any score is easy to get, so the leaderboard is meant for conferences, festivals and LAN parties, where the operator can reset it when the event starts. A public server may prefer to turn it off.

The two player limits on games are checked when a player sits down (`JON`, taking up an invitation, and `NEW`) and exempt bots. A game that starts is never refused or ended for them, so a player whose waiting tables fill up can be playing more than `games.active_max`, by at most those tables.

**The message of the day** is plain ASCII, at most 20 lines; a line of up to 73 characters is shown as written on every frontend (the web shows it in `<pre>`, telnet before the login prompt, the protocol's `MOTD`), so it can hold an ASCII layout. parlornet.org's is `deploy/motd.txt`, an ASCII box 39 columns wide (a 40-column screen shows it whole, without wrapping onto blank lines) with the contribute address below it, so an address of any length leaves the box intact, and last two lines that say it is a server setting, the file's path and when it last changed (plain text: an operator who writes their own message drops them). The web shows it in an ivory box under a light green "Message of the Day" header. It is given with `--motd` (`PARLOR_MOTD_FILE`) until the menus set one. Where it says `{contribute}`, the contribute address goes: the setting `site.contribute_url` from the menus (Server, Contribute) or the settings file, else the environment's `PARLOR_CONTRIBUTE_URL`, else the project's home, https://github.com/eric-cgn/parlornet, where anyone can get the source to run a server or contribute. Set to nothing in any of those places, it is cleared, and every line holding `{contribute}` is left out, so no marker ever shows.

**Two sockets.** The menus have no power of their own. They talk to parlord's admin socket (127.0.0.1:6599, JSON lines, `parlord/admin.py`), which speaks TLS 1.3 with a pre-shared key like the extension channel but accepts only keys enrolled with the admin role (`python -m parlord.frontends --keys DIR add admin --role admin`; the container's start script does this), so no player-facing server extension can make admin requests. The admin port, 6510, is what the operator reaches: publish it on the host's 127.0.0.1 only (`ssh -L 6510:127.0.0.1:6510`, then `telnet 127.0.0.1 6510`). Since operators publish ports as they like, the menus also ask for the admin password.

**First launch.** While no admin password is set, parlord makes a one-time setup password at each start and prints it in its log (`docker logs`) with how to reach the admin port. Signing in with it allows only setting the permanent admin password (at least 8 characters), which is stored as a salted scrypt hash. Wrong admin passwords lock the admin sign-in on the players' schedule for the client address they come from (the admin adapter passes it), so a guesser at one address cannot keep the operator out from another; the counts are kept in memory. A forgotten password is cleared with `python -m parlord.admin --db /data/parlord.db --reset-password`; the next start prints a new setup password.

## Email

**Transport.** Mail goes by plain SMTP only, with a host, a port, a user and password that can only send, and a From address (settings `email.smtp_host`, `email.smtp_port`, `email.smtp_user`, `email.smtp_password`, `email.from`). The connection is always encrypted and the relay's certificate is always verified against the system's CA certificates, for the host name in `email.smtp_host` (so set the relay's name, not its address): STARTTLS on any port but 465, implicit TLS on 465 (or with `email.smtp_tls=tls`). A relay that offers no STARTTLS, or whose certificate does not verify, gets nothing, neither the credentials nor the mail; the step is refused with `ERR NOMAIL`. For a relay with a certificate from a CA of your own, name that CA's certificate file in `email.smtp_ca_file` (settings file). Any provider that offers SMTP works; the release includes no provider's API, SDK or credentials. Until a relay is set the transport is `log`: nothing is sent, and the log gets the address and subject only, never the body.

**Temporary passwords.** A mailed password is a temporary password (Passwords and temporary passwords, above): about 59 random bits, good for 24 hours, and stored only as an scrypt hash. Each wrong one counts toward the name's lockout like any wrong password.

**Sign-up limits.** Two settings (admin menus, Sign-in, Sign-up limits; or the settings file):

| Setting | Default | What |
|---|---|---|
| `signup.address_cooldown` | 7 days (604800 seconds) | After a sign-up email to an address (the temporary password, or the notice to an address that already has a name), no other sign-up email goes to it for this long. |
| `signup.max_pending` | 60 | Pending accounts (made by sign-up email and not yet signed into) at once. |

- **One email per address.** Addresses are compared in lower case without surrounding spaces, as everywhere. A sign-up that gives an address in its cooldown is answered exactly like one whose mail went: the same lines, the same caps and counts (it is counted as a mail), about the same time, and it makes a pending account in the same way; but nothing is sent, and nothing signs in to that account. The lines after the address say so for everyone: "If you haven't received the email, it may take a while; you can ask again after 7 days." The cooldown starts when a mail goes and is not lengthened by later requests; a pending account's end does not lift it. Only the operator can (Status, Sign-ups). The database keeps no address for this, only a keyed hash (below).
- **At most so many waiting.** A pending account takes a place when its address step is done and holds it, with its name, until someone signs into it or its 24 hours end. While every place is taken, a new sign-up is refused before anything is asked: `ERR BUSY wait=N Too many sign-ups in progress; try again in ...` (the wait is until the oldest place frees). The web, telnet, FIBS and IOS show that text; the core's sweep deletes pending accounts as their time ends, which frees their places and their names. Another sign-up for a pending account's name is `ERR NAMEINUSE`.
- **Recovery** (`reset` at the sign-in question) mails the address on file under its own caps below, and neither the cooldown nor the places apply to it. A pending account has no recovery: its mailed password is the only way in.

**Caps.** parlord counts a send before it is made, in sliding windows kept in SQLite (so restarts do not reset them). Each default below is a setting (`email.limit.<key>`) the admin menus can change up to a ceiling:

| Per | Default |
|---|---|
| address | 3 an hour, 6 a day, at least 60 seconds apart; recovery mail to an address on file counts only recovery mail to it, so sign-ups naming that address cannot use up its owner's recovery |
| name | 2 an hour (not for recovery), 4 a day; recovery from an address the player signed in from is not counted here (it has its own 2 a day) |
| name, asked from one client address | 2 recovery mails a day |
| connection | 2; after that the player must reconnect |
| client address (the public port's peer; the address a server extension passes with `SRC`) | 4 an hour, 10 a day, sign-up and recovery together; and no more sign-up mail for the day once 2 addresses mailed for it bounced |
| sign-up, whole server | 3 a minute, 20 an hour, 60 a day; 30 a day to addresses never verified before |
| recovery asked by strangers, whole server | 3 a minute, 20 an hour, 60 a day: a budget of its own, so a flood of sign-ups cannot use it up |
| owners' own recovery, whole server | 20 an hour, 60 a day: asked from an address the player signed in from, or finished by signing in with the mailed password; strangers' requests never count here |

The cap for new addresses bounds how many bounces a flood of made-up addresses can cause, while returning players keep signing in. Set the server-wide caps (sign-up and recovery together) under your relay's own limits. Separately, one client address may start at most 20 sign-ups in 10 minutes (any sign-up method).

**When a cap is hit**, the step is refused with `ERR BUSY` or `ERR PAUSED` and a message that says which limit and when to try again (`wait=N`). Nothing is queued or sent later.

**Addresses in the mail tables.** The mail log (`email_log`, kept 3 days), the blocked addresses (`email_blocked`) and the cooldown (`signup_cooldown`) hold an address only as HMAC-SHA256 under a random key of the database's own, so the database keeps no list of addresses that never became accounts; an account's own address is on its player row. A pending account keeps its address there only if its temporary password was mailed to it, and only until it is deleted.

**Never send again** to an address in parlord's `email_blocked` table. It is filled by permanent failures at submission (SMTP 5xx for that recipient) and from a plain file named by the setting `email.blocked_file` (one address a line, `#` for comments), read when it changes. The public release only reads the file; filling it from a provider's bounce and complaint list is the operator's own, separate service. Bounces are counted by who asked for the mail: the client address that asked for the latest mail to a blocked address, or the address itself when that is not known. One client address whose sign-ups bounce is refused sign-up mail for the day (above). If bounces come from more than 3 such requesters in 24 hours, parlord holds sign-up mail until the operator resumes sending in the admin menus; recovery mail to addresses on file is never held.

**Message content** is fixed. A sign-up's mail gives the server name, the account's name and its temporary password, where to sign in (the web and telnet addresses the server speaks at the time, under its name), that the password works for 24 hours and that the first sign-in asks for a password of the player's own, and ends: "Nothing is kept until you sign in with this password. If you did not sign up for an account, you can ignore this message and it will expire shortly." A forgotten password's mail gives the same, says the old password keeps working until then, and that nothing changes if the reader did not ask. Of what the requester typed, a message holds only the name (at most 12 letters, digits, `-` and `_`) and the address it goes to, so the sign-up form cannot be used to send someone a message. One recipient per message, in plain text.

## The database

Players (with their password hashes, temporary password hashes and pending accounts), sessions, tables, games and settings live in one SQLite file, `/data/parlord.db` in the container. A database from before sign-up mailed passwords keeps accounts in another format and is not carried over. When the server starts and finds one, it renames it to `parlord.db.old-<date and time>` (with its `-wal` and `-shm` files) in the same directory, says so in its log, and starts a new, empty database: players, tables and games in progress start over, and the admin menus ask to be set up again with a new setup password. Settings from the environment and the settings file still apply. The old file is left for the operator to keep or delete.

**Operator controls.** The admin menus show the counters, can pause email sending and can change the caps. Every shipped server extension passes its client's address with `SRC` (docs/protocol.md); the public port uses the connection's own. IPv6 addresses are counted by their /48. The Status screen shows the server's wrong-answer and mail budgets, with an alarm when one is used up, and Clear budgets frees them without a restart (each name's lockout and caps stay); it also shows the sign-up limits and clears address cooldowns (Sign-up limits, above).
