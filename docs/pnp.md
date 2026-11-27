# ParlorNet Protocol (PARLORNET/1): a guide for frontend writers

This guide is for people writing their own frontend for ParlorNet: a program that connects to a parlor (a ParlorNet server) and lets a player sign in, find a table and play. It covers the native protocol, PARLORNET/1, in two parts:

- **Part 1, the server protocol**: what every frontend needs whatever the game: connecting, signing in, the lobby, tables, moves, events, the turn clock, chat, commands and errors.
- **Part 2, the games**: one chapter for each of the ten games this server hosts: what the view lines mean, how to write a move, the events, what is hidden from whom, and a short game.

[protocol.md](protocol.md) is the protocol's reference, [accounts.md](accounts.md) the sign-in dialog's, and each game's type spec in [types/](types/) is the full contract for that game. This guide teaches the protocol with examples; where it leaves something out, those documents have it.

## Part 1: the server protocol

### The sample sessions

Every sample session in this guide was recorded from a real server. `tools/pnp_examples.py` starts a local server with a fixed random seed, plays each session over the protocol and writes the transcript into this document; `tests/test_pnp_doc.py` plays every sample again against a fresh server and fails if any line from the server differs. The server in the samples is called `parlornet` and keeps nothing between sessions, so every sample starts on an empty server and its first table is table 1.

A transcript line starts with a connection letter and a direction:

| Start | Meaning |
|---|---|
| `A>` | A line the frontend on connection A sends to the server (the line ending is not shown). A connection to the public port starts with `A> PARLORNET/1`. |
| `A<` | A line the server sends to connection A. The first is the server's greeting. |
| `#` | A note on the session, not part of it. |

Several letters in one sample are several connections, usually two players at one table. Values that differ from run to run are written as placeholders in angle brackets: `<secs>` (the seconds left on a turn clock), `<sid>` (a session id), `<key>` (a sign-in key), `<mailed>` (the temporary password from the latest mail), `<n>` (a wait in seconds), `<when>` (the time a turn was played) and `<date>` (a day). The protocol itself never uses `<` or `>`.

The passwords in the samples (`swordfish 1997` and the like) and the addresses at `example.org` are made up for this guide.

### Connecting

A frontend opens a TCP connection to the server's public port, **6501**. There is no TLS and no binary framing: the protocol is lines of 7-bit ASCII text.

**The frontend speaks first.** The first thing it sends is the line `PARLORNET/1`, the protocol's name and version, and the ending of that line is the line ending it will use for the whole connection. The server sends nothing until it comes, then answers with the greeting, and from then on it ends every line it sends with that same ending. Any of the four common endings works:

| After `PARLORNET/1` the frontend sends | The connection's line ending |
|---|---|
| CR, then LF | CR LF |
| CR alone | CR |
| LF, then CR | LF CR |
| LF alone | LF |

The rules for that first line:

- It must come within **20 seconds** of connecting.
- The server reads it in any case: `parlornet/1` is the same.
- Up to **10 bytes** of anything before it are ignored, such as a stray Return or what a modem leaves on the line.
- After the CR or LF that follows the `1`, the server waits **250 milliseconds** for the other one. If it comes, the ending is the pair (CR LF or LF CR); if not, the ending is the single byte. Send a two-byte ending in one write, so both bytes arrive together.
- If the first line is not right, the server sends one error line, ending CR LF since no ending has been chosen, and closes the connection. Another version, such as `PARLORNET/2`, gets `ERR VERSION wrong version`. Anything else gets `ERR PROTOCOL wrong protocol`: more than 10 bytes before `PARLORNET/1`, something that cannot become it (an HTTP request, a telnet negotiation), a byte after the `1` that is not CR or LF, or nothing in time. When the operator has set the address of a docs site, the line goes on with where the protocol is described, if it fits the 80 bytes: `ERR PROTOCOL wrong protocol; see http://docs.parlornet.org/docs/protocol.html`.

After that, every line the frontend sends must end with exactly that ending. A line that ends any other way, with a CR or LF that is not the connection's ending (a bare LF on a CR LF connection, say), is refused with `ERR NEWLINE lines end with CR LF here` (naming the connection's ending) and not carried out; the connection stays open.

**A note for classic Mac C.** MPW and CodeWarrior swap the two escapes: `'\n'` is CR (13) and `'\r'` is LF (10). So `"\r\n"` in such a program sends LF CR, not CR LF. That is accepted, and the server answers in LF CR; read its lines with the ending you chose. `"\n"` alone gives CR, the classic Mac ending.

The other rules for lines:

- A line is at most **80 bytes** including its ending. The server never sends a longer one, so a frontend can read into an 80-byte buffer. A frontend that sends a longer line gets `ERR TOOLONG` and the connection is closed.
- Fields are separated by single spaces. Verbs are three or four letters; the server takes them in any case and sends them in upper case.
- Names (players) are 1 to 12 characters of `A`-`Z`, `a`-`z`, `0`-`9`, `_` and `-`. Table ids are numbers.

The **greeting** is one line:

    PARLORNET/1 name AUTH=signin SIGNUP=signup [LABEL=label]

- `PARLORNET/1` is the protocol and its version. A frontend checks it before anything else.
- `name` is the server's short name (in the samples, `parlornet`).
- `AUTH=` is the sign-in method: `none` or `password` (below).
- `SIGNUP=` is the sign-up method, how a new name gets an account: `none`, `password` (the player chooses one) or `email` (the server mails a temporary password).
- `LABEL=` appears only when the operator named the credential themselves, with `_` for spaces (for example `LABEL=Club_password`); a frontend shows it instead of its own word for the credential.

A server that is full sends `ERR BUSY server is full` (ending CR LF) as soon as the connection opens, without waiting for the first line, and closes it.

The samples in this guide use CR LF. Every public-port connection in them starts with `PARLORNET/1`, and the greeting is the reply to it.

### Requests and replies

After the greeting the frontend speaks again, and the server only ever answers: it sends nothing a frontend did not ask for, and there are no keepalives. Each request is one line; each reply is any number of data lines followed by exactly one final line:

- `OK [words]`: done. Some requests put a value after `OK` (a table id, a seat, a sequence number).
- `ERR CODE [text]`: refused. `CODE` is one upper-case word for programs; the text is for the player. The text may start with `wait=N`, the seconds to wait before trying again.
- `ASK field label [flags]`: only while signing in, signing up or changing a password; the server wants a value (below).

A frontend sends a line, then reads lines until one starts with `OK`, `ERR` or `ASK`. Data lines before it start with a tag that says what they are (`GAM`, `TAB`, `EVT`, `V`, `INF` and the view lines).

Requests allowed before signing in are `HELO`, `USER`, `REG`, `AUT`, `CAN`, `MOTD`, `GAM`, `PRL`, `TIM`, `CLI`, `TOP`, `PAG`, `CNT`, `BYE`, and on the extension channel `SES`, `SRC`, `PRO`, `PRD` and `NFY`. Anything else is refused with `ERR NOHELO`. This sample connects, reads the message of the day and the list of games, and signs in on a server with sign-in `none`:

```pnp greeting
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# Allowed before signing in: MOTD and GAM.
A> MOTD
A< MOTD Welcome to this parlor.
A< MOTD Finished tables are kept for a day.
A< OK
A> GAM
A< GAM backgammon/1 standard Backgammon 2 2 staged
A< GAM cannonfire/1 classic Cannon_Fire 2 8 nomoves,drop
A< GAM checkers/1 american Checkers 2 2 staged
A< GAM dotsandboxes/1 standard Dots_and_Boxes 2 4 drop
A< GAM dotsandboxes/1 majority Dots_and_Boxes 2 4 drop
A< GAM fiveinarow/1 freestyle Five_in_a_Row 2 2 nomoves
A< GAM fiveinarow/1 standard Five_in_a_Row 2 2 nomoves
A< GAM gridwords/1 standard Grid_Words 2 4 nomoves
A< GAM mancala/1 kalah Mancala 2 2
A< GAM mancala/1 oware Mancala 2 2
A< GAM navalcombat/1 classic Naval_Combat 2 2
A< GAM navalcombat/1 batnav Naval_Combat 2 2
A< GAM navalcombat/1 salvo Naval_Combat 2 2
A< GAM reversi/1 standard Reversi 2 2
A< GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves,drop
A< OK
# Anything else needs a sign-in first.
A> LST
A< ERR NOHELO say HELO first
# Sign-in none: the name alone.
A> HELO ann
A< OK -
A> HELO ann
A< ERR STATE already said HELO
A> LST
A< OK
```

`BYE` ends the connection (`OK`, then the server closes it). A connection that sends nothing for 15 minutes gets `ERR IDLE closing` and is closed. A connection has 5 minutes to sign in, counted from connecting or from signing out, whatever it sends in that time. After that it gets `ERR IDLE closing: sign in within 5 minutes of connecting` and is closed. Once signed in, it may stay in the lobby or at a table as long as it keeps talking to the server. Closing the connection, or losing it, does not leave a table or resign a game.

### Paged replies

Most replies are a few lines, but some are long. The list of tables with `LST clients` and a view with its legal moves can each run to a few kilobytes. A frontend with little memory can ask for long replies in pages, so it never holds more than one page at a time. On the public port, `PAG bytes` sets the most bytes in one page, from 512 to 65535, and each line counts with its line ending. Paging is off until a frontend asks for it, and `PAG 0` turns it off again.

A reply that fits in one page comes whole, as it always does. A longer one comes as a page of whole lines and then a line `CNT`, which counts toward the page too. The frontend sends `CNT` for the next page, and the last page ends with the reply's own final line. So with paging on, a frontend reads a page until a line starting with `OK`, `ERR`, `ASK` or `CNT`. Any other request drops the rest of the reply and is answered as usual. `CNT` with nothing waiting is `ERR STATE`.

The reply to a request that changes something, such as signing in, `NEW`, `JON`, `INV`, a move or a command, always fits in one page. A move's reply carries the table's events since the frontend last read them, and with paging on it carries only as many as fit. The rest wait for the next `SIN`, so a frontend that pages reads `SIN` after every move. [protocol.md](protocol.md) (Paged replies) has the full rules.

```pnp paging
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
# Pages of at most 512 bytes, each line counted with its CR LF.
A> PAG 512
A< OK
# The list of games is longer than a page: whole lines, then CNT.
A> GAM
A< GAM backgammon/1 standard Backgammon 2 2 staged
A< GAM cannonfire/1 classic Cannon_Fire 2 8 nomoves,drop
A< GAM checkers/1 american Checkers 2 2 staged
A< GAM dotsandboxes/1 standard Dots_and_Boxes 2 4 drop
A< GAM dotsandboxes/1 majority Dots_and_Boxes 2 4 drop
A< GAM fiveinarow/1 freestyle Five_in_a_Row 2 2 nomoves
A< GAM fiveinarow/1 standard Five_in_a_Row 2 2 nomoves
A< GAM gridwords/1 standard Grid_Words 2 4 nomoves
A< GAM mancala/1 kalah Mancala 2 2
A< GAM mancala/1 oware Mancala 2 2
A< CNT
# CNT brings the next page. The last page ends with the reply's own final line.
A> CNT
A< GAM navalcombat/1 classic Naval_Combat 2 2
A< GAM navalcombat/1 batnav Naval_Combat 2 2
A< GAM navalcombat/1 salvo Naval_Combat 2 2
A< GAM reversi/1 standard Reversi 2 2
A< GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves,drop
A< OK
A> GAM
A< GAM backgammon/1 standard Backgammon 2 2 staged
A< GAM cannonfire/1 classic Cannon_Fire 2 8 nomoves,drop
A< GAM checkers/1 american Checkers 2 2 staged
A< GAM dotsandboxes/1 standard Dots_and_Boxes 2 4 drop
A< GAM dotsandboxes/1 majority Dots_and_Boxes 2 4 drop
A< GAM fiveinarow/1 freestyle Five_in_a_Row 2 2 nomoves
A< GAM fiveinarow/1 standard Five_in_a_Row 2 2 nomoves
A< GAM gridwords/1 standard Grid_Words 2 4 nomoves
A< GAM mancala/1 kalah Mancala 2 2
A< GAM mancala/1 oware Mancala 2 2
A< CNT
# Any other request drops the rest of the list and is answered as usual.
A> LST
A< OK
# Nothing waits for CNT now.
A> CNT
A< ERR STATE no reply is waiting for CNT
# A page holds at least 512 bytes.
A> PAG 300
A< ERR SYNTAX PAG 0, or PAG bytes from 512 to 65535
# PAG 0 turns paging off.
A> PAG 0
A< OK
```

### Signing in

Three requests start it, all before signing in:

- `USER name` signs in only. If the name has an account, the server checks the credential of its sign-in method; if not, the reply is `ERR NOPLAYER`, and nothing is made.
- `REG name` signs up only: the sign-up method's steps for a name that has no account. A taken name is `ERR NAMEINUSE`; the words `new` and `guest`, which some frontends take at their login prompt, are `ERR RESERVED` for everyone, and so are the bot's names, `bot` and `bot1` to `bot3`, for `USER` and `HELO` too (see The bot, below).
- `HELO name` does either: it signs in a name that has an account and runs the sign-up for one that has not (under sign-up `email` it first asks whether to sign up, since the name may be a typing mistake). It is the original request and stays as it is; programs that keep sign-in and sign-up apart, as the web and telnet do, use `USER` and `REG`.

Each starts a dialog the server drives one step at a time, with these kinds of step:

| Line | Meaning | What a frontend does |
|---|---|---|
| `INF text` | A line to show the player (at most 74 characters of text; `INF` alone is a blank line). They come before an `ASK`. | Show it. |
| `ASK field label` | The server wants a value. `label` is what to show, with `_` for spaces (`Choose_a_password`). | Show the label, read a line, send `AUT field value`. |
| `ASK ... secret` | The same, but the value must not be echoed (a password). | Read without echo. |
| `ASK ... yn` | A yes or no question. | Send `AUT field y` or `AUT field n`. |
| `ASK ... cred` | It asks for the account's credential, or for a password being chosen at sign-up. | A frontend that took a password at its own login screen may answer this question with it, and no other. |
| `ASK ... new` | A new password is being chosen. | Never answer it with a password kept from a login screen unless it is also flagged `cred` (a sign-up); a password manager may offer to make one. |
| `OK -` | Signed in. | Go to the lobby. |
| `ERR CODE text` | Refused. | Show the text; a `wait=N` at its start is for programs. |

The flags come in that order after the label: `secret`, `yn`, `cred`, `new`. `AUT field value` answers the last `ASK`: the field word as the server sent it, then the value, which is the rest of the line as typed (a password may hold spaces; leading and trailing spaces are dropped, and at most 64 characters are used). A frontend that shows every `INF` line and every label and sends back what the player types can sign up and sign in under every method, including ones added to the server later, without knowing which method it is. The operator chooses one sign-in method and one sign-up method; the pairs that work are `none`+`none`, `password`+`password` and `password`+`email`.

**Sign-in `none`** asks nothing: `HELO name` answers `OK -` at once, and a free name is claimed the same way (sign-up `none`). Anyone may use any name; it is for a LAN or a club night. The sample above shows it.

**Sign-in `password`, sign-up `password`.** A new name chooses a password and types it again. A password is 8 to 64 printable ASCII characters (spaces are allowed inside) and uses at least three of four kinds of character: lowercase letters, capital letters, digits and symbols. A known name is asked for its password. `PWD` changes the password: the current one, a new one, the new one again; the player's other sessions end. A wrong password is refused with `ERR WRONG`, and the third in a row locks the name for a minute with `ERR LOCKED` (see Lockouts below).

```pnp signup-password
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# A free name under sign-up password: choose a password.
A> HELO bo
A< INF bo is new here. To sign up, choose a password.
A< INF A password needs at least 8 characters (at most 64) and at least three of
A< INF these four kinds: lowercase letters, capital letters, digits and symbols.
A< ASK password Choose_a_password secret cred new
# Too short: the question comes again, with the reason.
A> AUT password short
A< INF That password is too short. A password needs at least 8 characters (at
A< INF most 64) and at least three of these four kinds: lowercase letters,
A< INF capital letters, digits and symbols.
A< ASK password Choose_a_password secret cred new
A> AUT password swordfish 1997
A< ASK confirm Type_it_again secret cred new
# The two do not match: start the choice again.
A> AUT confirm swordfish 1996
A< INF The two did not match. A password needs at least 8 characters (at most 64)
A< INF and at least three of these four kinds: lowercase letters, capital
A< INF letters, digits and symbols.
A< ASK password Choose_a_password secret cred new
A> AUT password swordfish 1997
A< ASK confirm Type_it_again secret cred new
A> AUT confirm swordfish 1997
A< OK -
# Change the password: the current one, a new one, the new one again.
A> PWD
A< ASK current Current_password secret
A> AUT current swordfish 1997
A< INF A password needs at least 8 characters (at most 64) and at least three of
A< INF these four kinds: lowercase letters, capital letters, digits and symbols.
A< ASK password New_password secret new
A> AUT password two swordfish 1998
A< ASK confirm Type_it_again secret new
A> AUT confirm two swordfish 1998
A< OK
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# Signing in on another connection.
B> HELO bo
B< ASK password Password secret cred
B> AUT password two swordfish 1998
B< OK -
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# Two wrong passwords are only refused; the third locks the name for a minute.
C> HELO bo
C< ASK password Password secret cred
C> AUT password swordfish 1997
C< ERR WRONG Wrong password; try again
C> HELO bo
C< ASK password Password secret cred
C> AUT password swordfish 1996
C< ERR WRONG Wrong password; try again
C> HELO bo
C< ASK password Password secret cred
C> AUT password swordfish 1995
C< ERR LOCKED wait=60 Wrong password; try again in 60 seconds
# While it is locked (no address on file), nothing is checked.
C> HELO bo
C< ERR LOCKED wait=<n> Too many wrong answers for bo; try again in <n> seconds
# Sign out; the connection can sign in again.
A> OUT
A< OK 1
D> PARLORNET/1
D< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# A client that cannot hide what is typed says so first.
D> CAN text,info
D< OK info,text
D> HELO cy
D< ERR CANNOT this client cannot show the next step; use the web or telnet
```

A question asked again (after a password that was too short, or two that did not match) has no `cred` flag: a password the frontend took at its own login screen answers only the first credential question, never a repeated one.

**Sign in and sign up apart.** `USER` never makes an account and `REG` never signs in, so a mistyped name at a sign-in cannot become a new account. What they tell is no more than `HELO` always has (a free name starts a sign-up there); names are shown in the lobby anyway, and `USER` for names with no account counts against the client address as sign-ups do. Whether an email address is in use is never told (above).

```pnp signin-signup
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# Sign in only: a name with no account is refused; nothing is made.
A> USER bo
A< ERR NOPLAYER no account named bo here
# Sign up only: the sign-up method's steps for a new name.
A> REG bo
A< INF New account bo: choose a password.
A< INF A password needs at least 8 characters (at most 64) and at least three of
A< INF these four kinds: lowercase letters, capital letters, digits and symbols.
A< ASK password Choose_a_password secret cred new
A> AUT password swordfish 1997
A< ASK confirm Type_it_again secret cred new
A> AUT confirm swordfish 1997
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# A taken name is refused; REG never signs in.
B> REG bo
B< ERR NAMEINUSE the name bo is taken; choose another
# Signing in.
B> USER bo
B< ASK password Password secret cred
B> AUT password swordfish 1997
B< OK -
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# Words some frontends take at their login prompt are reserved.
C> REG guest
C< ERR RESERVED guest is a reserved word here; choose another name
```

**`CAN`.** A frontend that cannot show some kinds of step says which kinds it can show, before `HELO`: `CAN kind[,kind...]` with the kinds `text` (an ordinary question), `secret`, `yn` and `info` (`INF` lines). Without `CAN` all four are assumed. When the next step needs a kind the frontend did not declare, the server refuses it with `ERR CANNOT` instead of sending it, as at the end of the sample above. `INF` lines that only help (such as "Forgot it? Answer reset ...") are sent to a frontend without `info` anyway, and it may skip them; only lines the player needs before the question make `info` necessary.

**Sign-in `password`, sign-up `email`.** This is how parlornet.org signs players up. The player confirms the name and gives an email address. The server then makes a pending account and mails it a temporary password of three groups of four letters and digits, such as `abcd-efgh-jkmn`, which works for 24 hours (case, spaces and dashes do not matter when it is typed). The dialog goes on with a question for that password, so a player can type it at once or sign in with it later. The first sign-in with it confirms the account and asks straight away for a password of the player's own, typed twice; only then is the player signed in. A pending account that nobody signs into within 24 hours is deleted, and its name is free again.

```pnp signup-email-password
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=password SIGNUP=email
# A free name under sign-up email: confirm, give an address.
A> HELO una
A< INF una is not signed up on this server.
A< ASK confirm Sign_up_as_una?_(y/n) yn
A> AUT confirm y
A< INF We email you a password to sign in with; then you choose your own.
A< ASK email Email_address
A> AUT email una@example.org
A< INF We emailed a password for una to una@example.org.
A< INF It works for 24 hours. Type it here, or sign in with it later.
A< INF If you haven't received the email, it may take a while;
A< INF you can ask again after 7 days.
A< ASK password Password_from_the_email secret
# The temporary password from the mail. It confirms the account, and a password of the player's own comes next.
A> AUT password <mailed>
A< INF That was a temporary password. Choose a password of your own now.
A< INF A password needs at least 8 characters (at most 64) and at least three of
A< INF these four kinds: lowercase letters, capital letters, digits and symbols.
A< ASK password New_password secret new
# Not the temporary one again.
A> AUT password <mailed>
A< INF Choose a password of your own, not the one we emailed. A password needs at
A< INF least 8 characters (at most 64) and at least three of these four kinds:
A< INF lowercase letters, capital letters, digits and symbols.
A< ASK password New_password secret new
A> AUT password swordfish 1997
A< ASK confirm Type_it_again secret new
A> AUT confirm swordfish 1997
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=password SIGNUP=email
# Signing in another time.
B> USER una
B< INF Forgot your password? Answer reset and we email you a new one.
B< ASK password Password secret cred
# A forgotten password: a temporary one goes to the address on file.
B> AUT password reset
B< INF We emailed a new password to the address on file.
B< INF It works for 24 hours; your old password works until you use it.
B< INF Type the new one here, or sign in with it later.
B< ASK password Password secret
B> AUT password <mailed>
B< INF That was a temporary password. Choose a password of your own now.
B< INF A password needs at least 8 characters (at most 64) and at least three of
B< INF these four kinds: lowercase letters, capital letters, digits and symbols.
B< ASK password New_password secret new
B> AUT password two swordfish 1998
B< ASK confirm Type_it_again secret new
B> AUT confirm two swordfish 1998
B< OK -
```

An address that already has a name on the server gets a notice by mail instead of a password, and the dialog goes on exactly as for a free address, so the answer never tells whether an address is in use.

A frontend that cannot ask a further question after its login screen (it sent `CAN` without `text` and `info`) cannot choose the new password. Its sign-in with a temporary password is refused with `ERR NEWPASS Sign in on the web or by telnet first to choose your password.` The temporary password still confirms the account, and it keeps working there until its time is up.

**Sign-up limits.** An address gets one sign-up email (the password or that notice) and then none for a while, 7 days by default. A sign-up that gives it again in that time gets the same lines as one whose mail went, but no mail goes and nothing signs in to that account; hence the last two `INF` lines. At most a set number of pending accounts may wait at once (60 by default); while they are all taken, a new sign-up is refused before anything is asked, and one that waited at its address question is refused there:

    ERR BUSY wait=1740 Too many sign-ups in progress; try again in 29 minutes

A pending account holds its name, so another sign-up for that name is `ERR NAMEINUSE` until the account is confirmed or deleted.

**Forgotten passwords.** When the server asks for the password of an account that has an address on file, an `INF` line says so, and answering `reset` (`AUT password reset`) mails a new temporary password to that address, as in the sample above. It works like the sign-up's: it is good for 24 hours, and signing in with it asks for a new password. The old password keeps working until the temporary one is used. It is the same dialog, so a frontend needs nothing special for it. An account without an address is recovered by the operator, whose reset gives a temporary password to pass on and stops the old one.

### Lockouts

Every wrong password counts against the name. The first two are only refused (`ERR WRONG Wrong password; try again`). After the third the name is locked for 1 minute, after the fifth for 15 minutes, and the wait keeps growing to at most 4 hours. The refusal says how long, both for programs and for people:

    ERR LOCKED wait=60 Wrong password; try again in 60 seconds

While a name is locked, `HELO name` is refused at once (`ERR LOCKED wait=N ...`), unless the account has an address on file or a temporary password: then the question is still asked, with a note that sign-in waits. Until the wait is over only `reset` and a temporary password are checked. Besides each name's own count, wrong answers from an address the player has not signed in from lately count against that address and against the whole server, refused with `ERR LOCKED` or `ERR BUSY` and a `wait=`. Sign-ups started from one address are limited too (`ERR BUSY wait=600 ...`). A frontend never needs to count anything itself: it shows the text, and a program waits `wait=N` seconds before trying again.

### Sessions

A successful sign-in makes a **session**.

- **On the public port** the session lasts as long as the connection: the final line is `OK -`, there is no id, and a frontend that reconnects signs in again.
- **On the extension channel** (port 6590, reachable only by server extensions such as the web site and the telnet server, over TLS 1.3 with a key the operator enrolled for each) the final line is `OK sid`, where `sid` is a session id of 22 characters. Another connection of the same server extension can take the session up with `SES sid`, which answers `OK name`. This is how the web site keeps a player signed in across page loads. A session id is never shown to a player; it is a handle for the server extension, not a credential.

`SRC address` (extension channel only, before `HELO`) gives the address of the player the connection serves, so the per-address limits apply to the player and not to the server extension. `OUT` ends the current session (`OK 1`) and `OUT all` every session of the player, live connections included (`OK n`, the number ended); a connection whose session was ended elsewhere gets `ERR SIGNEDOUT` once, and is then signed out. A session also ends, by default, after 2 hours unused (when resuming) or 24 hours in all, and when the password changes or the operator suspends, resets or deletes the account.

```pnp sessions
# X, Y and Z are server extension connections (the extension channel, TLS 1.3 with a key);
# the server greets them at once. A is a connection to the public port, which starts with PARLORNET/1.
X< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# A server extension names the client it serves.
X> SRC 192.0.2.7
X< OK
# The session id comes back on the OK line.
X> HELO ann
X< OK <sid>
Y< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# Another connection of the same server extension resumes it.
Y> SES <sid>
Y< OK ann
Y> LST
Y< OK
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# The public port has no sessions to resume.
A> SES <sid>
A< ERR NOTFRONTEND SES is only for frontends
# Ending the session signs out every connection that holds it.
Y> OUT
Y< OK 1
X> LST
X< ERR SIGNEDOUT your session has ended; sign in again
Z< PARLORNET/1 parlornet AUTH=none SIGNUP=none
Z> SES <sid>
Z< ERR NOSESSION no such session; sign in again
```

A frontend on the public port can ignore `SES` and `SRC`, but it may meet `ERR SIGNEDOUT` (after `OUT all` from another place, or a password change).

### Staying signed in

A player can choose **Stay signed in** in a frontend that can keep a little data of its own, such as the 8-bit program on its disk or a browser in a cookie. It is a feature of the server, and every frontend uses the same requests for it. A frontend that cannot keep anything simply does not offer it. Telnet is one of those, because a telnet program has nowhere to keep a key.

- **Getting a key.** With the setting on, the frontend sends `KEY NEW [label]` right after the player signs in with the password, on the same connection. The reply is `OK key`, a sign-in key of 22 characters like a session id. The frontend keeps the key instead of the password and never shows it. The label is up to 32 characters, such as `Apple IIe`. Without one, the server uses the machine the frontend named with `CLI MACH`, then the program it named with `CLI PROG`.
- **Signing in with it.** Next time the frontend sends `USER name`, and when the server asks for the password it answers `AUT key key` instead. That signs in as the password would, so the player is asked nothing.
- **When the key is refused.** `ERR NOKEY` means the server does not take that key any more. It was forgotten, it expired or the password changed. The frontend then forgets the key and asks for the password with a new `USER name`. A refused key counts as one wrong answer toward the name's lockout (Lockouts, above), so it should not be tried twice.
- **Signing out** with `OUT` always forgets the key that session signed in with or was given, whatever the setting, and the frontend forgets its copy. `OUT all` forgets every key of the player. A frontend that stays signed in closes its connection without `OUT` (with `BYE`, or by hanging up) when the player leaves without signing out.
- **The player's list.** `KEY LST` lists the player's keys, one `KEY n made used here label` line each, where `made` and `used` are days and `here` marks the key this session signed in with (`-` for the others), then `OK count`. `KEY DEL n` forgets one, `KEY DEL all` every one, and `KEY DEL` alone the key of this session without signing out (when the player turns the setting off). A session that signed in with a key that is forgotten ends.
- **Expiry and the rest.** A key unused for 90 days expires (the setting `keys.idle_secs`). A password change, a recovery by mail and the operator's reset forget every key. A key works only where it was given, which is the public port or the server extension that asked for it. A player can hold at most 20 keys (`ERR LIMIT`). Under sign-in `none` there is nothing to keep (`ERR NOAUTH`).

```pnp stay-signed-in
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# bo makes an account on his Apple IIe, with Stay signed in on.
A> REG bo
A< INF New account bo: choose a password.
A< INF A password needs at least 8 characters (at most 64) and at least three of
A< INF these four kinds: lowercase letters, capital letters, digits and symbols.
A< ASK password Choose_a_password secret cred new
A> AUT password swordfish 1997
A< ASK confirm Type_it_again secret cred new
A> AUT confirm swordfish 1997
A< OK -
# The program names the machine; the key is labeled with it.
A> CLI MACH Apple IIe
A< OK
# Right after the password, the key for this computer. The program keeps it, never the password.
A> KEY NEW
A< OK <key>
# The computers where bo stays signed in.
A> KEY LST
A< KEY 1 <date> <date> here Apple IIe
A< OK 1
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# Another day: the key answers the password question, and nothing is asked.
B> USER bo
B< ASK password Password secret cred
B> AUT key <key>
B< OK -
# Signing out forgets the key, on the server and in the program.
B> OUT
B< OK 1
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=password SIGNUP=password
# A key the server no longer has is refused, and counts as a wrong answer.
C> USER bo
C< ASK password Password secret cred
C> AUT key <key>
C< ERR NOKEY This computer was signed out. Sign in again
# The program forgets it and asks for the password.
C> USER bo
C< ASK password Password secret cred
C> AUT password swordfish 1997
C< OK -
# Stay signed in is still on, so this sign-in gets a new key. A label may come with the request.
C> KEY NEW Apple IIe
C< OK <key>
C> KEY LST
C< KEY 2 <date> <date> here Apple IIe
C< OK 1
# Forget every key: no computer stays signed in as bo.
C> KEY DEL all
C< OK 1
C> KEY LST
C< OK 0
```

### The lobby

`MOTD` returns the operator's message of the day as `MOTD text` lines (at most 73 characters of text each; `MOTD` alone is a blank line), then `OK`.

`TIM` is for frontends on machines with no clock. It answers one line, then `OK`:

    TIM secs now left=rem call=rem month=rem

`secs` is how long the player has been signed in, in whole seconds, and `now` is the server's time in UTC, such as `TIM 754 2026-11-24T21:05:33Z left=unl call=unl month=unl`. On the public port `secs` counts from this connection's sign-in. On the extension channel it counts from the sign-in that started the session, whichever connection asks. Before signing in it is 0. Each `rem` is whole seconds, or `unl` for no limit. `left` is the time this connection has left, the smallest of its limits. `call` is the time the call has left, and `month` is the time the player has left this month. A call through a dial-in gateway has these limits (protocol.md, Time allowance). `TIM` is allowed before signing in and is not activity, so a frontend can send it with its regular lobby poll and count the seconds between polls itself.

A frontend that shows the time signed in shows the time left after it, as "12m onl 63m rem", or "12m onl unl rem" when there is no limit (design.md, Time). On a connection with a limit, a reply may start with an `INF` line that belongs to no request. That is the server's notice at 5 minutes and at 1 minute left, such as `INF This call ends in 5 minutes.`, and the frontend shows it. When the time runs out, `ERR LIMIT` comes without a request and the server closes the connection.

`GAM` lists the games this server hosts, one line per ruleset:

    GAM type/ver rules name minplayers maxplayers [flags]

`name` is the display name with `_` for spaces (`Five_in_a_Row`). The flags are a comma-separated list: `staged` (a turn is several steps, ended with `END`), `nomoves` (the server gives no list of legal moves; use `CHK`) and `drop` (in a game of three or more, one seat can leave and the others play on). The sample under Requests and replies shows the full list.

`LST` lists the tables that are open (waiting for players) or playing; finished tables are not listed:

    TAB id type/ver rules filled/seats status names turn=secs [vis=private] [bot=seat,...]

`status` is `open` or `playing`; `names` lists every seat in order, `-` for an empty one; `turn` is the time per turn in seconds; `vis=private` marks a private table; `bot=` lists the seats held by bots (The bot, below), when there are any. Public tables are listed for everyone; private ones only for their creator and for players seated or invited there.

The list comes in a fixed order. First come the player's own tables (where they sit, or that they created) and the tables they are invited to, all of them, by number. Then come the other tables: open ones, newest first, then games being played, newest first. Of those others the server lists at most 20 (the operator's setting `lists.max`), and when it leaves some out, a last line says how many:

    MORE n

Words after `LST` narrow the list. Each may be given once, in any order:

| Word | Lists only |
|---|---|
| `open` or `playing` | tables waiting for players, or games being played |
| `mine` | the player's own tables and invitations |
| `game=type` | one game: `game=reversi` (any version) or `game=reversi/1` |
| `player=name` | tables where that player sits. A private one only if the asker sits there too |
| `table=id` | that one table, if the asker may see it |

So `LST open game=backgammon` lists the Backgammon tables to join, and `LST player=amy` what Amy is playing and where she waits. A word the server does not know is `ERR SYNTAX`. A program that looks for one table by number asks `LST table=12` rather than reading the whole list, which may leave it out. Lines a client does not know, such as `MORE`, are skipped as always (Versions and what to ignore).

`IVL` lists the player's invitations to open tables:

    IVT table type/ver rules from status [seat=n]

where `from` is who invited them and `seat`, if present, the seat held for them.

`WHO` lists the players online now, by name:

    PLR name idle=secs [bot]

A player is online when they are signed in and have done something in the last half hour. The operator can change that time with the setting `presence.idle`. Signed in means a connection open on any frontend, or a web session that has not ended. `idle` is the number of seconds since the player last did something. `bot` marks the bot, which is online for as long as it runs.

Signing in counts as doing something, and so do the requests that act: `NEW`, `JON`, `LVE`, `INV`, `UNV`, `RES`, `MOV`, `CHK`, `UND`, `END`, `CHT`, `PWD`, `PRF SET`, `PRF DEL` and `PFL SET`. Requests that only look never count. These include `LST`, `IVL`, `WHO`, `VUE`, `SIN`, `WAT`, `SEE` and `TIM`. That way a frontend that polls does not keep its player online forever. Some frontends have commands of their own that need no request, such as a chat they carry themselves. When the player uses one, the frontend sends `ACT`, and the server answers `OK`.

### Tables

`NEW type rules [key=val ...]` makes a table and answers `OK id`. The type may be written without its version (`NEW reversi standard` is `reversi/1`). Making a table does not seat its creator: they sit down with `JON` like anyone else. Up to six options follow, each `key=val` with a key of letters and digits and a value of at most 12 characters. Three are the server's, for every game:

| Option | Meaning |
|---|---|
| `players=n` | The number of seats, from the game's minimum to its maximum (`GAM`). Default: the minimum. |
| `turn=t` | The time each player has per turn: seconds, or a number with `s`, `m`, `h` or `d` (`90`, `5m`, `1h`, `2d`); 1 minute to 5 days (the operator's setting `games.turn_max_days`). Default: the operator's, normally 1 day. |
| `vis=public` or `vis=private` | Who may sit down: anyone, or only the creator and players invited. Default `public`. |

The other options are the game's (Part 2), such as `size=6` for Reversi. The server checks them by asking the game before it makes the table, so a bad one is refused at once with `ERR BADOPT` and the game's reason. The server holds at most 400 tables, finished ones included (`ERR FULL`; the operator's setting `games.tables_max`). A table nobody has joined goes after 24 hours with nothing happening at it (`games.open_hours`).

A player has two limits on sitting down, checked by `JON` (taking up an invitation too) and by `NEW`, whose creator sits down next. Over either, the reply is `ERR LIMIT` with a reason the player can act on:

- **Games being played**: at most 20 at once (`games.active_max`). A player seated at that many tables whose games are running cannot sit down anywhere (`ERR LIMIT you are playing 20 games, the most allowed here; finish one first`) until one ends. Finished and resigned games, and a seat the player has left while the others play on, do not count.
- **Tables waited at**: at most 2 open tables of one game type, such as `reversi/1` (`games.waiting_per_type`). A third is refused (`ERR LIMIT you are already waiting at 2 Reversi tables; start or leave one`); other games are counted on their own. Sitting down in the seat that starts the game is not waiting, so it is not refused for this. With a long game name the text is shorter, to fit the line: `ERR LIMIT you are waiting at 2 Rummy Tile Classic tables; start or leave one`.

A game that starts is never refused or ended for these limits: when tables a player waits at fill up, their games start, so a player can be playing more than the limit, by at most the tables they were waiting at (`ERR LIMIT you are playing 22 games, over the 20 allowed; finish one first`). Bots are exempt from both. The operator sets the three numbers in the admin menus (Games) or the settings file.

`JON table [seat]` sits down, in the first free seat or the one asked for, and answers `OK seat`. Seats are numbered from 0; seat 0 moves first in every game. When the last seat fills, the game starts: the events `START` and the game's first events (usually `TURN 0`) follow the last `JOIN`.

`LVE table` stands up from an open table (`LEAVE seat`), or stops watching. At a running game it does nothing to the game: the player keeps the seat and can come back at any time. Only `RES` gives up a running game.

`SEE table` watches a table (`OK`; at most 20 watchers a table, `ERR FULL`). A watcher can `VUE`, `SIN`, `WAT` and `CHT` at that table, and sees what every watcher sees (Part 2 says what that is for each game).

**Invitations and private tables.** A player seated at an open table can invite another player by name, and hold a free seat for them: `INV table name [seat]`. Nobody else can sit in a held seat. The invited player sees the invitation with `IVL`, and the table in `LST` and with `SEE` even if it is private. `JON` puts them in the held seat and uses the invitation up. `UNV table name` withdraws an invitation (any player seated there) or declines one's own. An invitation also ends after an hour, unless `INV` renews it. It ends too when the player who made it stands up, and when the game starts. Each ending is an `UNINVITE seat name why` event, where `why` is `declined`, `withdrawn`, `expired`, `left` or `started`. A player who is not online can still be invited. The invitation waits for them, and `INV` answers `OK offline` instead of `OK`. A private table can be joined only by its creator and players invited there (`ERR PRIVATE` for anyone else).

```pnp tables
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO amy
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bo
B< OK -
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=none SIGNUP=none
C> HELO cy
C< OK -
D> PARLORNET/1
D< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# dan signs in and out again: he has an account but is not online.
D> HELO dan
D< OK -
D> OUT
D< OK 1
# Who is online: the seconds since each one's last activity.
A> WHO
A< PLR amy idle=<secs>
A< PLR bo idle=<secs>
A< PLR cy idle=<secs>
A< OK
# A private table: a game option (size), the time per turn, the visibility.
A> NEW reversi standard size=6 turn=1h vis=private
A< OK 1
# Making a table does not seat its creator.
A> JON 1
A< OK 0
# Invite bo and hold seat 1 for him.
A> INV 1 bo 1
A< OK
# Invite cy, with no seat held.
A> INV 1 cy
A< OK
B> IVL
B< IVT 1 reversi/1 standard amy open seat=1
B< OK
B> LST
B< TAB 1 reversi/1 standard 1/2 open amy,- turn=3600 vis=private
B< OK
# The only free seat is held for bo.
C> JON 1
C< ERR TAKEN the free seats are held for invited players
# An invited player may watch.
C> SEE 1
C< OK
# cy declines the invitation.
C> UNV 1 cy
C< OK
# The private table is gone from cy's list.
C> LST
C< OK
# With cy's invitation gone there is room for another. An invitation to a player who is not online waits for them.
A> INV 1 dan
A< OK offline
# bo sits in the seat held for him; the game starts.
B> JON 1
B< OK 1
# Every event of the table from the start.
A> SIN 1 0
A< EVT 1 1 JOIN 0 amy
A< EVT 1 2 INVITE 1 bo
A< EVT 1 3 INVITE - cy
A< EVT 1 4 UNINVITE - cy declined
A< EVT 1 5 INVITE - dan
A< EVT 1 6 JOIN 1 bo
A< EVT 1 7 UNINVITE - dan started
A< EVT 1 8 START
A< EVT 1 9 TURN 0
A< OK 9
B> CHT 1 good luck
B< OK
A> SIN 1
A< EVT 1 10 CHT 1 good luck
A< OK 10
# Leaving a running game does not resign it: amy keeps her seat.
A> LVE 1
A< OK
A> LST
A< TAB 1 reversi/1 standard 2/2 playing amy,bo turn=3600 vis=private
A< OK
```

**The bot.** A server may run the bot ([bot.md](bot.md)), a player for every game that anyone can invite, so a player alone on the server can still play. Invite it like anyone else, `INV table bot`, and it sits down within a few seconds; for more seats invite `bot1`, `bot2` and `bot3` as well, each name being one seat. It never sits down uninvited, plays to the end of the game, says one line when it sits down and "Good game!" at the end, and leaves an open table when the people there have stood up. Bots are flagged by the server: `LST` adds `bot=` with their seats to the table's `TAB` line (`TAB 7 reversi/1 standard 2/2 playing amy,bot turn=3600 bot=1`), and the view adds a line `BOT seat ...` once the game runs. The bot names are reserved: nobody can sign up or sign in as one (`ERR RESERVED`). On a server that does not run the bot, `INV table bot` is `ERR NOPLAYER`, or the invitation is never answered.

### Views

`VUE table [flags]` returns this player's view of the table: everything a frontend needs to draw it, with what the player may not see already taken out by the server. The flags are letters, written together or apart:

| Flag | Adds |
|---|---|
| `G` | The static geometry, first: lines fixed for the table's type, rules and options (a board's size, the special squares). Ask once and keep it. |
| `T` | An `S text` line, the status in words for the player (`Your move`, `Waiting for the other player`, `Game over: ...`). |
| `M` | The legal moves, one `M` line each, when it is this player's move and the game provides a list (not for `nomoves` games). |
| `C` | `CLIENT seat text` for each seated player who said what they play with (Saying what you are, below). |

The reply is, in this order: `V type/ver seq`, the geometry lines (with `G`), the game's view lines, `BOT seat ...` when bots hold seats, `CLK seat secs_left turn_secs` while the game runs, `S` (with `T`), the `M` lines (with `M`), the `CLIENT` lines (with `C`), `INT false text` at a table marked `integrity=false` (Part 2, Grid Words), then `OK`. `seq` is the table's latest event number: the view shows the table as it was after that event. Before the game starts the view is just `V type/ver seq` and `S Waiting for players`, with the geometry lines between them when you ask with `G` and the game has them, as Grid Words does. `GEO type rules` gives a ruleset's geometry without a table.

The view lines of each game are in Part 2. Some tags are shared by convention: `W seat` (whose turn it is; `-1` or `-` once the game is over), `T seat points` (a score line per seat), `S`, `M`, `CLK` and `BOT seat ...` (the seats held by bots, after the game's lines, only when there are any). A view is drawn from scratch: a frontend that is unsure of its own picture of the table asks for the view again, at any time.

### Moves

`MOV table token ...` plays a move (or, in a `staged` game, one step of a turn). The tokens are the game's (Part 2); several tokens on one line are one move. The reply carries the events this connection has not seen yet for that table (its own move and anything before it), then:

- `OK`: the move is played.
- `OK more`: a staged turn goes on; send the next step.
- `OK done`: a staged turn is complete; send `END` to commit it, or `UND` to take a step back.
- `ERR ILLEGAL reason`: refused, with the game's reason (`not your turn`, `that point is taken`); nothing changed.

`CHK table token ...` asks whether a move is legal without playing it: `OK [info]` or `ERR ILLEGAL reason`. Every game answers it; what `info` says is the game's (the points of a word, the boxes a line would close, whether a stone wins). For `nomoves` games it is how a frontend checks a move before sending it.

**Staged turns** (games flagged `staged`: Backgammon, Checkers, Rummy Tile Classic): a turn is several `MOV` steps, each applied at once but tentatively. `UND table` takes back the last step, `END table` commits the turn. The game checks the steps as they come and again at `END`. In a game without staged turns, `UND` and `END` are refused with `ERR NOSTAGE`. Tokens that start with `!` are the server's own: `!undo` and `!end`, each alone on a `MOV` or `CHK`, act as `UND` and `END`, and any other is refused (`ERR SYNTAX`).

### Events

Every change at a table is an event, numbered from 1 per table:

    EVT table seq kind ...

Events are the deltas: a frontend that keeps its own picture of the table applies them in order, and each says exactly what changed (a disc placed and the discs it flipped, a die rolled, a line drawn), so applying one needs no knowledge of the rules. A frontend that would rather not can fetch the view again after any event.

A frontend gets events three ways:

- **In replies.** `MOV`, `UND`, `END` and `RES` carry, before their final line, every event of that table the connection has not seen yet. A two-player frontend that only moves and asks for the view sees most events this way.
- **`SIN table [n]`**: the events after number `n`, then `OK seq` with the latest number. Without `n`, the events this connection has not seen. This is polling: a frontend waiting for the other player sends `SIN` every few seconds.
- **`WAT table [n] [secs]`**: as `SIN`, but if there is nothing new the server waits up to `secs` seconds (default 20, at most 30) for something to happen. A frontend that can hold a connection open uses it to see the other player's move as soon as it is made.

The server keeps track, per connection and table, of the last event the connection has seen: `JON`, `SEE`, `VUE`, `SIN`, `WAT` and the replies above move it to the latest. `JON` moves it past the player's own `JOIN`, so a player's first `MOV` reply starts with the events since they sat down (the samples in Part 2 show this). A table keeps its last 500 events; a frontend that falls further behind fetches the view.

The events every game shares:

| Event | Meaning |
|---|---|
| `JOIN seat name` | A player sat down. |
| `CLIENT seat [text]` | What the seat's player plays with, in words: right after their `JOIN`, and again when they play on from another program. No text where the reader may not see it (a private table). |
| `LEAVE seat` | A player stood up from an open table. |
| `START` | The last seat filled and the game began. |
| `TURN seat` | It is that seat's move; its clock starts. |
| `INVITE seat name`, `UNINVITE seat name why` | Someone was invited, or an invitation ended. `seat` is the seat held for the player, or `-` if none is held. `why` is `declined`, `withdrawn`, `expired`, `left` or `started`. |
| `CHT seat text` | Chat; `seat` is `-` for a watcher. |
| `GONE seat resign` or `GONE seat timeout` | In a `drop` game with more than two seats still playing, that seat left the game; the others play on. |
| `END result` | The game is over. `result` is the game's own (`win 0 6-10`, `draw agreed`), or `resign seat`, `timeout seat` or `abandoned` (the operator ended the table). |

Each game adds its own kinds (Part 2). A frontend shows an event kind it does not know as nothing, and fetches the view to stay right.

### The turn clock, resigning and leaving

Every table has a time per turn (`turn=` on `NEW`). The clock of the seat to move starts at each `TURN` event. The view's `CLK seat secs_left turn_secs` line says whose clock is running and what is left; the server sends `CLK` in every view of a running game, so a frontend can show a countdown without asking again. When a clock runs out, the player loses: `END timeout seat`. Disconnecting does nothing to the clock: a player who goes away keeps their seat and their clock keeps running.

`RES table` resigns: `END resign seat`. In a game flagged `drop` (Cannon Fire, Dots and Boxes, Rummy Tile Classic) with more than two seats still playing, resigning or running out of time takes only that seat out: `GONE seat resign` or `GONE seat timeout`, and the others play on. A seat that has gone can no longer move or resign there (`ERR STATE`). Once two seats are left, the next resignation or timeout ends the game for everyone as above. The Dots and Boxes sample in Part 2 shows both.

### Chat

`CHT table text` sends a line of chat to a table where the player is seated or watching. The server keeps printable ASCII only and at most 56 characters, and adds the event `CHT seat text` (`-` for a watcher's seat). The reply is `OK` alone: the sender sees their own line with the next events, like everyone else.

### Commands

A frontend's command line, where the player types `/who`, `/invite bot` or a move such as `/d3`, needs no code of its own for each command. `CMD [table] /line` sends the line as it was typed, with the table it was typed at, and the server runs it. The reply is `INF text` lines to show as they are, plain sentences of at most 74 characters, then `OK`. A command that has nothing to say, such as a typed move that was played or a line said with `/say`, answers `OK` alone. What it did at the table comes as events with the next `SIN` or `WAT`, because `CMD` leaves the connection's event cursors alone. A refusal is a sentence like any other, so a frontend shows every reply the same way. `ERR` comes only for a request the server can't take, such as a line without its slash or a table that isn't there.

A command that ends a turn, a game or the session asks first. The reply is then `DLG kind [table]` and the dialog's words as `INF` lines. Show them in your confirmation dialog, with Yes and No and No highlighted. Yes sends `CMD table YES /kind`, and No sends nothing. The kinds are `resign`, `pass`, `end` and `quit`. Nothing a player types can confirm, because the command line always sends its slash first. No command answers an offer either. Your dialogs for a double, a draw offer or an invitation send `MOV`, `JON` or `UNV` themselves.

The commands are those of [chat-commands.md](chat-commands.md), and [protocol.md](protocol.md) (Commands) lists what they do at a table. Keep your command line to 60 characters, so the request fits its 80 bytes with any table number.

```pnp commands
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
# In the lobby, the line goes as typed. Here /open needs a game.
A> CMD /open
A< INF Type /open and a game, such as /open backgammon. The games are backgammon,
A< INF cannonfire, checkers, dotsandboxes, fiveinarow, gridwords, mancala,
A< INF navalcombat, reversi and rummytile.
A< OK
A> CMD /open reversi
A< INF Your table 1 is open: anyone can join it. /close closes it.
A< OK
# At ann's open table, the command runs there.
A> CMD 1 /invite bob
A< INF You invited bob to table 1.
A< OK
# An invitation is answered in its dialog, which sends JON.
B> CMD /accept ann
B< INF An invitation or an offer is answered in its own dialog, not from the
B< INF command line.
B< OK
B> JON 1
B< OK 1
# A typed move that is played has nothing to say.
A> CMD 1 /d3
A< OK
# What it did comes as events, as after any request.
A> SIN 1
A< EVT 1 1 JOIN 0 ann
A< EVT 1 2 INVITE - bob
A< EVT 1 3 JOIN 1 bob
A< EVT 1 4 START
A< EVT 1 5 TURN 0
A< EVT 1 6 PUT 0 d3 FLP d4
A< EVT 1 7 TURN 1
A< OK 7
# A refusal is a sentence like any other.
A> CMD 1 /f5
A< INF It's not your turn.
A< OK
B> CMD 1 /a1
B< INF You can't play a1. That square flips nothing.
B< OK
B> CMD 1 /say well played
B< OK
# Resigning asks first, with DLG and the dialog's words.
B> CMD 1 /resign
B< DLG resign 1
B< INF Resign this game? ann wins. Leaving the table never resigns.
B< OK
# The dialog's Yes sends the confirmed form.
B> CMD 1 YES /resign
B< INF You resigned table 1.
B< OK
A> CMD /who
A< INF Online: ann, bob.
A< OK
# The line starts with its slash.
A> CMD who
A< ERR SYNTAX CMD [table] [YES] /command
A> CMD 9 /who
A< ERR NOTABLE no such table
```

### Keeping a little data

A frontend may keep a little data for the signed-in player on the server, such as a display choice, so it is there the next time they sign in from any machine. `PRF SET key value` keeps a value, `PRF GET key` reads it (`PRF key value`, then `OK`; `OK` alone if it is not set), `PRF DEL key` removes it, and `PRF LST` lists them all. A key is 1 to 12 of `A`-`Z`, `a`-`z`, `0`-`9`, `_` and `-` (in any case; the server keeps it in lower case), and a value 1 to 56 printable ASCII characters, spaces allowed inside. A player has at most 16 keys and 1024 bytes of keys and values. Every frontend on the public port shares the player's one set, so choose keys your program will not share by accident (a short prefix of its own). It is never for secrets: keep no password, code or anything private in it. Deleting the account deletes it.

```pnp prefs
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
# Keep a value for ann: a key, then the rest of the line.
A> PRF SET board dark
A< OK
A> PRF SET hello Good game, all
A< OK
A> PRF GET board
A< PRF board dark
A< OK
# A key that is not set: OK alone.
A> PRF GET sound
A< OK
A> PRF LST
A< PRF board dark
A< PRF hello Good game, all
A< OK
A> PRF DEL hello
A< OK
# Keys are letters, digits, - and _.
A> PRF SET a.b x
A< ERR BADKEY a key is 1 to 12 of A-Z a-z 0-9 _ -
# Values are at most 56 characters.
A> PRF SET note xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
A< ERR BADVALUE a value is 1 to 56 printable ASCII characters
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# Each player has their own.
B> HELO bob
B< OK -
B> PRF LST
B< OK
```

### Saying what you are, turns and profiles

Tell the server what your program is and what it runs on, so that the turns your player makes show it: `CLI PROG` with the program's name and version, and `CLI MACH` with the machine, each up to 40 printable ASCII characters. Send them right after the greeting, before signing in, or at any time after; each answers `OK`, and `CLI` alone shows what the server keeps for the connection. It is optional, and the server uses it only as a label. A frontend for the Apple IIe might send `CLI PROG ParlorNet for the Apple IIe 1.1` and `CLI MACH Apple IIe (enhanced)`; keep the version in the program's name, so players can see who runs which release.

The server records each turn with what its player's connection said. `TRN table` lists a table's turns, oldest first: a `TRN table seq time seat name type/ver` line for each, then `TRX MOVE` with the turn in the game's own words, `TRX PROG` and `TRX MACH` when the program named them. `TRN table n` gives only the turns after event `n`, which suits a frontend that has already shown the earlier ones. `FEED n` gives the latest `n` turns at all public tables, newest first, for an activity list or a stream overlay, and `FEED n name` one player's. Read every `TRX` line as part of the `TRN` line above it, and skip a `TRX` word you do not know. At a private table the programs and machines are sent only to its seated players, and its turns are never in `FEED`.

The table is told too. When a player whose connection said what it is sits down, the `JOIN` is followed by `CLIENT seat text`, where `text` says what they play with in words, ready to follow the name: `with ParlorNet for the Apple IIe 1.1 on an Apple IIe`. Show it with the join, as in "ann sat down with ParlorNet for the Apple IIe 1.1 on an Apple IIe", cut to your screen's width; a connection that said nothing gets no `CLIENT`, and nothing should be shown for it. When a seated player moves or chats from a connection that names another program, `CLIENT` comes again before that move or line. `VUE table C` gives the same lines for the players seated now, and `LST clients` puts them after each table's `TAB` line. At a private table only its creator and seated players get the text; anyone else watching gets `CLIENT seat` alone.

`PFL name` reads a player's profile: `PFL NAME`, the preferred machine (`PFL PREF`) when they gave one, their score (`PFL SCORE`, below), and what they played at public tables, as `PFL GAME type/ver n`, `PFL PROG n text` and `PFL MACH n text`. `PFL SET PREF text` sets your own preferred machine, and `PFL SET PREF` alone clears it.

```pnp clients
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# Before signing in (or after): the program and its version, then the machine.
A> CLI PROG ParlorNet for the Apple IIe 1.1
A< OK
A> CLI MACH Apple IIe (enhanced)
A< OK
A> CLI
A< CLI PROG ParlorNet for the Apple IIe 1.1
A< CLI MACH Apple IIe (enhanced)
A< OK
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# bob's program says nothing about itself.
B> HELO bob
B< OK -
A> NEW reversi standard
A< OK 1
A> JON 1
A< OK 0
# bob watches: the table was told what ann plays with, right after her JOIN.
B> SEE 1
B< OK
B> SIN 1 0
B< EVT 1 1 JOIN 0 ann
B< EVT 1 2 CLIENT 0 with ParlorNet for the Apple IIe 1.1 on an Apple IIe
B< OK 2
# The view with C: what each seated player plays with.
B> VUE 1 C
B< V reversi/1 2
B< S Waiting for players
B< CLIENT 0 with ParlorNet for the Apple IIe 1.1 on an Apple IIe
B< OK
# The same lines after each table's TAB line, for a lobby list.
B> LST clients
B< TAB 1 reversi/1 standard 1/2 open ann,- turn=86400
B< CLIENT 0 with ParlorNet for the Apple IIe 1.1 on an Apple IIe
B< OK
# bob's connection named nothing: no CLIENT follows his JOIN.
B> JON 1
B< OK 1
A> MOV 1 d3
A< EVT 1 3 JOIN 1 bob
A< EVT 1 4 START
A< EVT 1 5 TURN 0
A< EVT 1 6 PUT 0 d3 FLP d4
A< EVT 1 7 TURN 1
A< OK
B> MOV 1 c3
B< EVT 1 6 PUT 0 d3 FLP d4
B< EVT 1 7 TURN 1
B< EVT 1 8 PUT 1 c3 FLP d4
B< EVT 1 9 TURN 0
B< OK
# The table's turns: who, when, the turn in words, and with what.
B> TRN 1
B< TRN 1 7 <when> 0 ann reversi/1
B< TRX MOVE played d3, turning 1
B< TRX PROG ParlorNet for the Apple IIe 1.1
B< TRX MACH Apple IIe (enhanced)
B< TRN 1 9 <when> 1 bob reversi/1
B< TRX MOVE played c3, turning 1
B< OK 2
# Only the turns after event 7.
A> TRN 1 7
A< TRN 1 9 <when> 1 bob reversi/1
A< TRX MOVE played c3, turning 1
A< OK 1
# The latest turns at public tables, newest first.
B> FEED 5
B< TRN 1 9 <when> 1 bob reversi/1
B< TRX MOVE played c3, turning 1
B< TRN 1 7 <when> 0 ann reversi/1
B< TRX MOVE played d3, turning 1
B< TRX PROG ParlorNet for the Apple IIe 1.1
B< TRX MACH Apple IIe (enhanced)
B< OK 2
# ann names the machine she likes best.
A> PFL SET PREF Macintosh SE/30
A< OK
# Anyone signed in reads her profile.
B> PFL ann
B< PFL NAME ann
B< PFL PREF Macintosh SE/30
B< PFL SCORE 0 0 0 0 0 -
B< PFL PROG 1 ParlorNet for the Apple IIe 1.1
B< PFL MACH 1 Apple IIe (enhanced)
B< OK
B> PFL nobody
B< ERR NOPLAYER no player by that name
```

### Scores and the leaderboard

Every finished game gives each player seated at it points: by default 4 for a win, 2 each for a draw, 1 for a loss, and 0 for resigning or running out of time, which gives the other player the win. In a game of three or more the winner gets 4, every other player who played to the end 1, and anyone who resigned, ran out of time or left 0; a tie for first is a draw for those tied and a loss for the rest. Games against the bot count, and the bot itself is never on the leaderboard. Nothing stops a player from collecting points against themselves or the bot, so any score is easy to get on a public server: the leaderboard is meant for conferences, festivals and LAN parties, and a frontend that shows it should say so.

`TOP [n] [game=type]` lists the leaderboard, `TOP rank name points wins draws losses resigns` for each of the first `n` players (10 if left out, at most 50), the most points first, and ends with `OK count`, the number of players with a score. Players with as many points share a rank. `game=reversi` counts only that game's points. `TOP` works before signing in. `PFL name` carries the player's score as `PFL SCORE points wins draws losses resigns rank` (`-` for the rank before their first scored game). The operator can change the points, reset every score, or turn the leaderboard off; then `TOP` answers `ERR NOSCORES` and profiles carry no `SCORE` line.

```pnp scores
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW reversi standard
A< OK 1
A> JON 1
A< OK 0
B> JON 1
B< OK 1
# bob resigns: ann scores a win (4 points), bob a resignation (0).
B> RES 1
B< EVT 1 5 END resign 1
B< OK
A> NEW checkers american
A< OK 2
A> JON 2
A< OK 0
B> JON 2
B< OK 1
# A draw by agreement: 2 points each.
A> MOV 2 draw
A< EVT 2 2 JOIN 1 bob
A< EVT 2 3 START
A< EVT 2 4 TURN 0
A< EVT 2 5 DRAW 0
A< EVT 2 6 TURN 1
A< OK
B> MOV 2 accept
B< EVT 2 5 DRAW 0
B< EVT 2 6 TURN 1
B< EVT 2 7 ACCEPT 1
B< EVT 2 8 END draw agreed
B< OK
# The leaderboard: rank, name, points, wins, draws, losses, resigns.
B> TOP
B< TOP 1 ann 6 1 1 0 0
B< TOP 2 bob 2 0 1 0 1
B< OK 2
# One game's points only.
B> TOP 1 game=reversi
B< TOP 1 ann 4 1 0 0 0
B< OK 2
# A profile carries the score and the rank.
A> PFL bob
A< PFL NAME bob
A< PFL SCORE 2 0 1 0 1 2
A< PFL GAME checkers/1 1
A< PFL GAME reversi/1 1
A< OK
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# No sign-in is needed to read the leaderboard.
C> TOP 1
C< TOP 1 ann 6 1 1 0 0
C< OK 2
```

### A complete session

Two players, ann and bob, each on their own connection, play the start of a game of Reversi on a server with sign-in `none`:

```pnp sample
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# ann signs in (this server's sign-in is none).
A> HELO ann
A< OK -
# What this server hosts.
A> GAM
A< GAM backgammon/1 standard Backgammon 2 2 staged
A< GAM cannonfire/1 classic Cannon_Fire 2 8 nomoves,drop
A< GAM checkers/1 american Checkers 2 2 staged
A< GAM dotsandboxes/1 standard Dots_and_Boxes 2 4 drop
A< GAM dotsandboxes/1 majority Dots_and_Boxes 2 4 drop
A< GAM fiveinarow/1 freestyle Five_in_a_Row 2 2 nomoves
A< GAM fiveinarow/1 standard Five_in_a_Row 2 2 nomoves
A< GAM gridwords/1 standard Grid_Words 2 4 nomoves
A< GAM mancala/1 kalah Mancala 2 2
A< GAM mancala/1 oware Mancala 2 2
A< GAM navalcombat/1 classic Naval_Combat 2 2
A< GAM navalcombat/1 batnav Naval_Combat 2 2
A< GAM navalcombat/1 salvo Naval_Combat 2 2
A< GAM reversi/1 standard Reversi 2 2
A< GAM rummytile/1 standard Rummy_Tile_Classic 2 4 staged,nomoves,drop
A< OK
# A Reversi table with an hour per turn.
A> NEW reversi standard turn=1h
A< OK 1
A> JON 1
A< OK 0
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
# bob, on another connection, finds the table and sits down.
B> HELO bob
B< OK -
B> LST
B< TAB 1 reversi/1 standard 1/2 open ann,- turn=3600
B< OK
B> JON 1
B< OK 1
# ann's view: geometry, status text and her legal moves.
A> VUE 1 GTM
A< V reversi/1 4
A< BRD 8 8
A< R 1 ........
A< R 2 ........
A< R 3 ........
A< R 4 ...10...
A< R 5 ...01...
A< R 6 ........
A< R 7 ........
A< R 8 ........
A< C 2 2
A< W 0
A< CLK 0 <secs> 3600
A< S Your move
A< M d3
A< M c4
A< M f5
A< M e6
A< OK
# Check a move without playing it.
A> CHK 1 c4
A< OK flips 1
# Play it: the reply carries the new events.
A> MOV 1 c4
A< EVT 1 5 PUT 0 c4 FLP d4
A< EVT 1 6 TURN 1
A< OK
# Not ann's turn now.
A> MOV 1 f5
A< ERR ILLEGAL not your turn
# bob asks what happened since he last looked.
B> SIN 1
B< EVT 1 5 PUT 0 c4 FLP d4
B< EVT 1 6 TURN 1
B< OK 6
B> MOV 1 c3
B< EVT 1 7 PUT 1 c3 FLP d4
B< EVT 1 8 TURN 0
B< OK
A> SIN 1
A< EVT 1 7 PUT 1 c3 FLP d4
A< EVT 1 8 TURN 0
A< OK 8
A> CHT 1 well played
A< OK
# bob resigns: the game ends.
B> RES 1
B< EVT 1 9 CHT 0 well played
B< EVT 1 10 END resign 1
B< OK
A> SIN 1
A< EVT 1 9 CHT 0 well played
A< EVT 1 10 END resign 1
A< OK 10
A> BYE
A< OK
```

What happens, step by step:

1. ann connects, sends `PARLORNET/1` (ending CR LF) and reads the greeting: protocol `PARLORNET/1`, sign-in `none`. `HELO ann` signs her in at once. `GAM` lists the games.
2. `NEW reversi standard turn=1h` makes table 1 with an hour per turn; `JON 1` seats ann in seat 0.
3. bob signs in on his own connection, finds the table with `LST` (one of two seats filled, `ann,-`) and sits down. The table is full, so the game starts.
4. ann asks for her view with all three flags: the geometry `BRD 8 8`, the board as eight rows (`.` empty, `0` and `1` the seats' discs), the disc counts `C`, whose turn `W 0`, her clock, the status and her four legal moves.
5. `CHK 1 c4` asks about c4 without playing it: it would flip one disc. `MOV 1 c4` plays it; the reply carries the events: the disc placed and the one flipped, and `TURN 1`.
6. ann tries to move again and is refused: it is bob's turn.
7. bob polls with `SIN 1` and sees ann's move. He answers c3; ann polls and sees it.
8. ann chats. bob resigns: his `RES` reply carries the events he has not seen (ann's chat) and `END resign 1`. ann polls once more and sees the end; `BYE` closes her connection.

### Errors

An `ERR` line is `ERR CODE text`. A frontend acts on the code and shows the text. These are all the codes the server sends:

| Code | Meaning |
|---|---|
| `BADKEY` | `PRF` with a key that is not 1 to 12 of letters, digits, `_` and `-`, or `TDS` or `TDD` with a key that is not 1 to 8 of them. |
| `BADNOTE` | `NOTE` with a note that is not 1 to 48 printable ASCII characters. |
| `BADOPT` | An option on `NEW` that the server or the game refuses; the text says why. |
| `BADVALUE` | `PRF SET` or `TDS` with a value that is not 1 to 56 printable ASCII characters. |
| `BUSY` | A limit was reached: too many sign-ups, wrong answers, password checks or mails from an address or on the server (with `wait=`), as many players signed in as the server allows (`ERR BUSY wait=60 the server is full (200 players), try again later`, at `HELO`, `USER` or `REG`), or too many connections (`ERR BUSY server is full`, before the greeting, and the connection is closed). |
| `CANCELLED` | The player answered no to signing up. |
| `CANNOT` | The next sign-in step is of a kind the frontend did not declare with `CAN`. |
| `CLOSED` | `JON` or `INV` at a table that is no longer open. |
| `FULL` | The server has too many tables, or a table too many watchers. |
| `IDLE` | Nothing was sent for 15 minutes; the connection is closed. |
| `ILLEGAL` | A move or check the game refuses; the text is the game's reason. |
| `INTERNAL` | Something went wrong on the server; the request was not done. |
| `LIMIT` | `NEW` or `JON` by a player playing as many games as the server allows, or waiting at as many open tables of that game (Tables, above), a table has twice as many invitations out as free seats, or 20 notes, `PRF SET` would pass 16 keys or 1024 bytes, `TDS` 16 keys, a connection would declare more than 4 protocols with `PRO`, or `KEY NEW` for a player with 20 sign-in keys. Sent unasked, it means the connection's time ran out (protocol.md, Time allowance), and the connection is closed. |
| `LOCKED` | The name is locked after wrong answers, or the address has used up its wrong answers (with `wait=`). |
| `NAMEINUSE` | `REG` for a name that has an account, or the name was taken by someone else while this sign-up ran (a pending account holds its name). |
| `NAMERULE` | `REG` (or `HELO` for a free name) with a name outside the rule for new names: it has at least one letter, so it cannot read as a table number, and `-` and `_` go only between letters or digits. Names from before the rule still sign in. |
| `NOAUTH` | `PWD` or `KEY NEW` on a server whose sign-in has no password. |
| `NOGAME` | `NEW` or `TOP` for a game this server does not host, or `RES`, `MOV` or `CHK` at a table whose game is not running. |
| `NOHELO` | The request needs a sign-in first (or `OUT` when not signed in). |
| `NOKEY` | `AUT key` with a sign-in key the server does not take (forgotten, expired, or the password changed): forget it and ask for the password (with `wait=` when the name is locked); or `KEY DEL` for a key that is not there. |
| `NEWLINE` | A line that does not end with the connection's line ending; it was not carried out. |
| `NOINVITE` | `UNV` for an invitation that does not exist. |
| `NEWPASS` | A temporary password was right, but the frontend cannot ask for the new password; sign in on the web or by telnet first. |
| `NOMAIL` | The mail could not be sent, or the address cannot receive mail. |
| `NOPLAYER` | `USER`, `INV` or `PFL` for a name with no account; or a second word on `HELO` for a name that is not signed up. |
| `NOPROTO` | `PRD` for a protocol this connection did not declare. |
| `NORULES` | `NEW` with a ruleset the game does not have, or one the operator turned off. |
| `NOSCORES` | `TOP` on a server whose operator turned the leaderboard off. |
| `NOSESSION` | `SES` with an unknown, expired or ended session. |
| `NOSTAGE` | `UND` or `END` in a game without staged turns. |
| `NOTABLE` | No table with that id. |
| `NOTAT` | `VUE`, `SIN`, `WAT` or `CHT` at a table where the player is neither seated nor watching. |
| `NOTFRONTEND` | `SES`, `SRC`, `PRO`, `PRD`, `TDL`, `TDS`, `TDD` or `NFY` on the public port; they are for the extension channel. |
| `NOTSEATED` | `MOV`, `CHK`, `RES`, `INV`, `UNV` or adding a `NOTE` by a player not seated at the table (`UNV` of one's own invitation excepted). |
| `PAUSED` | The operator paused email sign-up, or new sign-ups by mail are held. |
| `PLUGIN` | The game failed to answer; try again. |
| `PRIVATE` | `JON` or `SEE` at a private table without an invitation, or `NOTE` or `TRN` there by someone who may not see it. |
| `PROTOCOL` | The connection did not start with `PARLORNET/1` within 20 seconds, or it started with a PROXY header from an address that is not a dial-in gateway; the connection is closed. |
| `RATE` | Too many requests too fast (the request was not done), or three failed `SES` on one connection. |
| `RESERVED` | `REG` (or `HELO` for a free name) with a reserved word: `new` or `guest`, or a name that would pass for the server or its staff (`parlornet`, the server's own name, `admin`, `administrator`, `sysop`, `system`, `server`, `operator`, `moderator`, `staff`, `root`); `USER`, `HELO` or `REG` with a bot's name (`bot`, `bot1` to `bot3`). |
| `SIGNEDOUT` | This connection's session was ended elsewhere; sign in again. |
| `STATE` | Not now: already signed in, already seated, an `AUT` that answers nothing asked, a seat that has left the game, `KEY NEW` anywhere but right after a sign-in with the password. |
| `SUSPENDED` | The operator suspended this name. |
| `SYNTAX` | The request is malformed; the text shows the right form. |
| `TAKEN` | The seat is taken or held for an invited player. |
| `TOOLONG` | A line over 80 bytes; the connection is closed. |
| `UNKNOWN` | No such request. |
| `VERSION` | The connection started with another version of the protocol (`PARLORNET/2`); the connection is closed. |
| `WRONG` | A wrong password or code, refused without a wait: one of a name's first two wrong answers in a row (the third is `LOCKED`). |

Some of them, from one session:

```pnp errors
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> FROB
A< ERR UNKNOWN no such request
A> VUE 1
A< ERR NOHELO say HELO first
A> HELO ann
A< OK -
A> VUE 9
A< ERR NOTABLE no such table
A> NEW chess standard
A< ERR NOGAME this server does not host that game
A> NEW reversi fancy
A< ERR NORULES unknown ruleset
A> NEW reversi standard size=7
A< ERR BADOPT size must be even, 4 to 10
A> NEW reversi standard turn=10s
A< ERR BADOPT turn is a time per turn, 1m to 5d (like 5m, 1h, 1d)
A> NEW reversi standard
A< OK 1
A> VUE 1
A< ERR NOTAT sit or watch first
A> MOV 1 d3
A< ERR NOTSEATED you are not playing at this table
A> JON 1
A< OK 0
A> RES 1
A< ERR NOGAME the game is not running
A> UND 1
A< ERR NOSTAGE this game has no staged turns
A> CHT 1
A< ERR SYNTAX CHT table text
A> JON 1
A< ERR STATE already seated
A> INV 1 nobody
A< ERR NOPLAYER no player by that name
A> PWD
A< ERR NOAUTH this server's sign-in has no password to change
```

### Limits

- **Requests**: 10 a second on one connection, with bursts of up to 30. Over that, `ERR RATE slow down` and the request is not done. 
- **Connections**: 120 on the public port (`ERR BUSY server is full` when full).
- **Players**: 200 signed in at once, bots not counted (`players.online_max`); past that, signing in is `ERR BUSY wait=60 the server is full (200 players), try again later`, though a player already signed in may sign in again elsewhere. `WHO` lists as many, and the bots.
- **Lines**: 80 bytes with CR LF, both ways.
- **Tables**: 400 on the server; per player, 20 games being played and 2 open tables of each game waited at; a time per turn of at most 5 days; `LST` lists at most 20 tables besides the player's own (the operator can change all of these); 20 watchers per table, and invitations out at a table up to twice its free seats. A table nobody joined is removed after 24 hours with nothing happening at it, a finished table after a day.
- **Chat**: 56 characters a line.
- **Clients and profiles**: 40 characters for `CLI PROG`, `CLI MACH` and `PFL SET PREF`; `TRN` lists at most 50 turns, `FEED` at most 30.

### Versions and what to ignore

The greeting names the protocol version, `PARLORNET/1`. A frontend checks it and stops if it does not know the version; a later version that changes the meaning of a line will have another number. Each game has its own version too (`reversi/1` in `GAM`, `NEW` and the `V` line): a change to a game's view lines, events or move tokens makes a new type version, while a new ruleset of the same type changes only what is legal, not the lines.

Within a version, the server may add things a frontend must step over:

- **Unknown view lines**: skip a view line whose tag you do not know. Backgammon's view gained `N`, `DR`, `CA` and others after its first draft this way.
- **Unknown events**: show nothing for an event kind you do not know, and fetch the view.
- **Unknown words on a known line**: ignore extra words at the end of a line (a new flag on `GAM`, a new field on `TAB`).
- **Unknown greeting fields**: ignore `KEY=value` fields you do not know.
- **Unknown `ASK` flags**: ignore them; show the label and send the answer.

## Part 2: the games

Each chapter gives the game's identity (as `GAM` lists it), its options, its view lines and events, its move tokens and what `CHK` says, what is hidden from whom, and the sizes on the wire, followed by a short game from a real server. The type spec linked at the top of each chapter is the full contract.

All ten games share these points:

- Seats are numbered from 0, and seat 0 moves first.
- Colors are not part of any game's contract: a frontend shows the seats as it likes (black and white, X and O), and never by color alone.
- Squares and points named by a column letter and a row number count rows from the top: `a1` is the top-left corner.
- `W` says whose move it is, and `-1` or `-` once the game is over.
- Unless a chapter says otherwise, a view shows everything to everyone, and the move list (`VUE ... M`) is sent only on the player's own turn.

### Reversi

Spec: [types/reversi-1.md](types/reversi-1.md).

| | |
|---|---|
| Type | `reversi/1`, ruleset `standard` |
| Players | 2 |
| Flags | none |
| Options | `size=n`: the board is n by n, even, 4 to 10; default 8 |

**Geometry** (flag `G`): `BRD w h`.

**View lines**:

| Line | Meaning |
|---|---|
| `R y cells` | Row y (1 at the top): one character per column, `.` empty, `0` or `1` the seat whose disc it is. |
| `C n0 n1` | Discs per seat. |
| `W seat` | Whose move; `-1` when the game is over. |

**Moves**: one square, a column letter and a row number: `MOV 1 d3`. The move list (`M`) holds every square that flips something. There is no pass token: when a player has no legal square, the server passes for them.

**Events**: `PUT seat sq FLP sq ...` (the disc placed and every disc it flipped), `PASS seat` (the seat could not move), `TURN seat`, and `END win seat n0-n1` or `END draw n0-n1` (the disc counts).

**CHK**: `OK flips n`, the discs the move would flip.

**Hidden**: nothing.

**Sizes**: a full 8 by 8 view is about 110 bytes; an event 10 to 40 bytes.

**Example**: a whole game on a 4 by 4 board. Each player's move reply also carries the other player's last move, which that connection had not seen yet.

```pnp reversi
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
# A 4 by 4 board, for a short game.
A> NEW reversi standard size=4
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> VUE 1 GTM
A< V reversi/1 4
A< BRD 4 4
A< R 1 ....
A< R 2 .10.
A< R 3 .01.
A< R 4 ....
A< C 2 2
A< W 0
A< CLK 0 <secs> 86400
A< S Your move
A< M b1
A< M a2
A< M d3
A< M c4
A< OK
A> MOV 1 b1
A< EVT 1 5 PUT 0 b1 FLP b2
A< EVT 1 6 TURN 1
A< OK
B> MOV 1 a1
B< EVT 1 5 PUT 0 b1 FLP b2
B< EVT 1 6 TURN 1
B< EVT 1 7 PUT 1 a1 FLP b2
B< EVT 1 8 TURN 0
B< OK
A> MOV 1 a2
A< EVT 1 7 PUT 1 a1 FLP b2
A< EVT 1 8 TURN 0
A< EVT 1 9 PUT 0 a2 FLP b2
A< EVT 1 10 TURN 1
A< OK
B> MOV 1 c1
B< EVT 1 9 PUT 0 a2 FLP b2
B< EVT 1 10 TURN 1
B< EVT 1 11 PUT 1 c1 FLP b1 c2
B< EVT 1 12 TURN 0
B< OK
A> MOV 1 d1
A< EVT 1 11 PUT 1 c1 FLP b1 c2
A< EVT 1 12 TURN 0
A< EVT 1 13 PUT 0 d1 FLP c2
A< EVT 1 14 TURN 1
A< OK
B> MOV 1 a3
B< EVT 1 13 PUT 0 d1 FLP c2
B< EVT 1 14 TURN 1
B< EVT 1 15 PUT 1 a3 FLP a2 b2 b3
B< EVT 1 16 TURN 0
B< OK
A> MOV 1 a4
A< EVT 1 15 PUT 1 a3 FLP a2 b2 b3
A< EVT 1 16 TURN 0
A< EVT 1 17 PUT 0 a4 FLP b3
A< EVT 1 18 TURN 1
A< OK
B> MOV 1 d2
B< EVT 1 17 PUT 0 a4 FLP b3
B< EVT 1 18 TURN 1
B< EVT 1 19 PUT 1 d2 FLP c2
B< EVT 1 20 TURN 0
B< OK
A> MOV 1 d3
A< EVT 1 19 PUT 1 d2 FLP c2
A< EVT 1 20 TURN 0
A< EVT 1 21 PUT 0 d3 FLP d2 c3
A< EVT 1 22 TURN 1
A< OK
B> MOV 1 b4
B< EVT 1 21 PUT 0 d3 FLP d2 c3
B< EVT 1 22 TURN 1
B< EVT 1 23 PUT 1 b4 FLP b3
B< EVT 1 24 TURN 0
B< OK
A> MOV 1 c4
A< EVT 1 23 PUT 1 b4 FLP b3
A< EVT 1 24 TURN 0
A< EVT 1 25 PUT 0 c4 FLP b4
A< EVT 1 26 TURN 1
A< OK
B> MOV 1 d4
B< EVT 1 25 PUT 0 c4 FLP b4
B< EVT 1 26 TURN 1
B< EVT 1 27 PUT 1 d4 FLP c3
B< EVT 1 28 END win 1 6-10
B< OK
B> SIN 1
B< OK 28
A> VUE 1 T
A< V reversi/1 28
A< R 1 1110
A< R 2 1110
A< R 3 1110
A< R 4 0001
A< C 6 10
A< W -1
A< S Game over: win 1 6-10
A< OK
```

### Backgammon

Spec: [types/backgammon-1.md](types/backgammon-1.md).

| | |
|---|---|
| Type | `backgammon/1`, ruleset `standard` |
| Players | 2 |
| Flags | `staged` |
| Options | `cube=0` or `1` (the doubling cube, default 1); `match=n` (a match to n points, 1 to 99; default 0, a single game) |

The server rolls every die from the table's seed. The points are numbered 1 to 24 for both players: seat 0 moves from 24 down to 1 and bears off below 1; seat 1 moves from 1 up to 24 and bears off above 24. A frontend may turn the board round for display; the numbers never change.

**Geometry**: `BRD 24`.

**View lines**:

| Line | Meaning |
|---|---|
| `P 1 c1 ... c12`, `P 13 c13 ... c24` | Checkers on each point: positive for seat 0, negative for seat 1, 0 empty. |
| `B n0 n1`, `O n0 n1` | Checkers on the bar, and borne off, per seat. |
| `D d ...` | The dice still to play this turn (`D` alone before the roll). |
| `C value owner` | The cube: its value and its owner, `-` while centered. |
| `W seat` | Whose move; `-` when the game is over. |
| `Q phase` | `roll` (send `roll` or `double`), `move`, `take` (answer a double), `resign` (answer a resignation), `over`. |
| `N n`, `DR d d` | The steps this roll requires, and the dice rolled this turn. |
| `CA a0 a1` | 1 if that seat may double at the start of its turn. |
| `L n`, `T seat points`, `G n [crawford]` | In a match: its length, the score and the game number. |
| `RS seat points kind` | A resignation offer waiting for an answer. |

**Moves**: a turn is staged, one checker step per `MOV`: `from-to`, where `from` is a point or `bar` and `to` a point or `off` (`13-9`, `bar-22`, `6-off`). After the last step the play needs, `MOV` answers `OK done`; send `END`, or `UND` to take a step back. With the cube on, a turn starts with `roll` or `double`, and a double is answered `take` or `drop`. On either player's turn, `resign-single`, `resign-gammon` or `resign-backgammon` offers to resign at that level; the other player answers `accept` or `reject`. The move list holds every legal single step, with the rules on using as many dice as possible and the larger die already applied; a player with no legal step is passed.

**Events**: `OPEN d0 d1` (the opening roll, one die per seat; the higher starts and plays both), `ROLL seat d d`, `STEPS seat n` (the steps this roll requires), `STEP seat from to [HIT]`, `UNDO seat from to`, `PASS seat`, `DBL seat value`, `TAKE seat value`, `DROP seat`, `OFFER seat points kind`, `ACCEPT seat`, `REJECT seat`, and `END win seat points single|gammon|backgammon`. In a match, a won game gives `WON seat points kind`, `SCORE s0 s1` and `GAME n [crawford]` before the next game's `OPEN`.

**CHK**: `OK` with a word for an accepted step or answer (`ok`, `roll`, `take` ...).

**Hidden**: nothing; the dice of the next roll are not known to anyone until rolled.

**Sizes**: a full view about 200 bytes; a turn of two steps is under 60 bytes of events.

**Example**: the opening roll, one turn each (with a step taken back and played again), and a resignation offer accepted. The cube is off, so each turn starts with the server's roll.

```pnp backgammon
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
# No doubling cube: each turn starts with a roll.
A> NEW backgammon standard cube=0
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
# The opening roll: one die each; the higher die starts and plays both.
A> SIN 1 0
A< EVT 1 1 JOIN 0 ann
A< EVT 1 2 JOIN 1 bob
A< EVT 1 3 START
A< EVT 1 4 OPEN 3 2
A< EVT 1 5 TURN 0
A< EVT 1 6 ROLL 0 3 2
A< EVT 1 7 STEPS 0 2
A< OK 7
A> VUE 1 GTM
A< V backgammon/1 7
A< BRD 24
A< P 1 -2 0 0 0 0 5 0 3 0 0 0 -5
A< P 13 5 0 0 0 -3 0 -5 0 0 0 0 2
A< B 0 0
A< O 0 0
A< D 3 2
A< C 1 -
A< W 0
A< Q move
A< N 2
A< DR 3 2
A< CA 0 0
A< CLK 0 <secs> 86400
A< S Your move
A< M 13-10
A< M 13-11
A< M 24-21
A< M 24-22
A< M 6-3
A< M 6-4
A< M 8-5
A< M 8-6
A< OK
A> MOV 1 8-6
A< EVT 1 8 STEP 0 8 6
A< OK more
# Take the step back, and play it again.
A> UND 1
A< EVT 1 9 UNDO 0 8 6
A< OK more
A> MOV 1 8-6
A< EVT 1 10 STEP 0 8 6
A< OK more
A> MOV 1 8-5
A< EVT 1 11 STEP 0 8 5
A< OK done
A> END 1
A< EVT 1 12 TURN 1
A< EVT 1 13 ROLL 1 2 4
A< EVT 1 14 STEPS 1 2
A< OK
B> MOV 1 19-23
B< EVT 1 8 STEP 0 8 6
B< EVT 1 9 UNDO 0 8 6
B< EVT 1 10 STEP 0 8 6
B< EVT 1 11 STEP 0 8 5
B< EVT 1 12 TURN 1
B< EVT 1 13 ROLL 1 2 4
B< EVT 1 14 STEPS 1 2
B< EVT 1 15 STEP 1 19 23
B< OK more
B> MOV 1 19-21
B< EVT 1 16 STEP 1 19 21
B< OK done
B> END 1
B< EVT 1 17 TURN 0
B< EVT 1 18 ROLL 0 2 5
B< EVT 1 19 STEPS 0 2
B< OK
A> VUE 1 T
A< V backgammon/1 19
A< P 1 -2 0 0 0 1 6 0 1 0 0 0 -5
A< P 13 5 0 0 0 -3 0 -3 0 -1 0 -1 2
A< B 0 0
A< O 0 0
A< D 2 5
A< C 1 -
A< W 0
A< Q move
A< N 2
A< DR 2 5
A< CA 0 0
A< CLK 0 <secs> 86400
A< S Your move
A< OK
# Offer to resign a single game.
A> MOV 1 resign-single
A< EVT 1 20 OFFER 0 1 single
A< EVT 1 21 TURN 1
A< OK
B> MOV 1 accept
B< EVT 1 20 OFFER 0 1 single
B< EVT 1 21 TURN 1
B< EVT 1 22 ACCEPT 1
B< EVT 1 23 END win 1 1 single
B< OK
```

### Grid Words

Spec: [types/gridwords-1.md](types/gridwords-1.md).

| | |
|---|---|
| Type | `gridwords/1`, ruleset `standard` and any the operator adds |
| Players | 2 to 4 (`players=n`) |
| Flags | `nomoves` |
| Options | none |

A crossword-building tile game on a 15 by 15 board. Every word a play makes must be in the server's word list (this server's is ENABLE 1.0, `plugins/gridwords/words.txt`). Squares are `x y`, 1 to 15, across from the left and down from the top; the center is `8 8` (`h8` on a frontend's screen).

**Geometry**: `BRD 15 15`, `CTR 8 8` (the square the first word covers), `PRM kind x y x y ...` (the premium squares by kind: `TW`, `DW`, `TL`, `DL`) and `TIL letter points count` (the tile set; `_` is the blank). About 680 bytes, once. Each ruleset has its own bonus squares and tiles, so draw the board and the points from these lines rather than from values of your own.

**View lines**:

| Line | Meaning |
|---|---|
| `X x y c ...` | Tiles on the board, as triples, up to 9 a line. Upper case is a tile, lower case a blank standing for that letter. |
| `R letters` | Your rack (`_` a blank; `R -` empty). |
| `K seat n` | Tiles on each seat's rack. |
| `BAG n` | Tiles left in the bag. |
| `T seat points` | Scores. |
| `W seat` | Whose move; `-1` when the game is over. |
| `LAST seat x y dir word points` | The last word played; `dir` is `H` (across) or `V` (down). |
| `RK seat letters` | Only for a server extension (`VUE table O` at a table marked integrity=false): every seat's rack (`RK seat -` when it is empty). |
| `BO letters` | Only for a server extension, as `RK`: the bag in draw order, the next tile first, up to 60 letters a line (`BO -` when it is empty). |

**Moves**: `PLAY x y H|V letters` writes the whole main word from its first square, across (`H`) or down (`V`), including letters already on the board; `_` before a letter plays a blank as that letter (`PLAY 8 8 H HEL_LO`). `EXCH letters` exchanges tiles (while the bag holds at least 7); `PASS` passes. Under `standard`, two full rounds of passes and exchanges end the game. A ruleset the operator adds may set another number of turns.

**Events**: `PLAY seat x y H|V word points`, `X x y c ...` (the new tiles), `DRAW seat n` (how many, never which), `EXCH seat n`, `PASS seat`, `TURN seat`; at the end `ADJ seat ±n letters` (each rack's tiles taken off its score) and `END win seat s0/s1/...` or `END draw s0/s1/...`.

**CHK**: `CHK table PLAY ...` tries a word and answers `OK points word word ...` with every word the play makes, or the reason it is refused (`ERR ILLEGAL not in the word list: ...`). It works when it is not your turn too, so a player can plan while waiting. `CHK` of `EXCH` or `PASS` answers `OK 0` when allowed.

**Hidden**: each rack. `R` is only in a seated player's own view; the others see the count (`K`), and `DRAW` never names a tile. Watchers see no rack. `RK` and `BO` go only to a server extension whose program receives the bag and every rack, at a table marked integrity=false (below); a player's own program never gets them.

**Tables marked integrity=false**: a table where the server gives out something it normally keeps, today a table with a Networdz player (protocols.md, Networdz), carries `integrity=false` in `LST`, `INT false text` in its view, an `INT false text` event and a note (protocol.md, Tables marked integrity=false). Nothing else changes: the moves, the turns and the end are as at any table, and each player's view still shows only their own rack. Show the event as a line in the table's log ("Integrity: text."), so the player knows.

**Sizes**: the geometry about 680 bytes once; a mid-game view about 600 bytes; a play is four events of 6 to 40 bytes.

**Example**: a word from each player, then an exchange and passes end the game. The racks' points come off the scores.

```pnp gridwords
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW gridwords standard
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
# The static geometry (flag G) comes first; a client keeps it.
A> VUE 1 GT
A< V gridwords/1 4
A< BRD 15 15
A< CTR 8 8
A< PRM TW 1 1 8 1 15 1 1 8 15 8 1 15 8 15 15 15
A< PRM DW 2 2 14 2 3 3 13 3 4 4 12 4 8 8 4 12 12 12 3 13
A< PRM DW 13 13 2 14 14 14
A< PRM TL 6 2 10 2 8 4 5 5 11 5 2 6 14 6 4 8 12 8 2 10
A< PRM TL 14 10 5 11 11 11 8 12 6 14 10 14
A< PRM DL 4 1 12 1 7 3 9 3 1 4 15 4 6 6 10 6 3 7 7 7
A< PRM DL 9 7 13 7 3 9 7 9 9 9 13 9 6 10 10 10 1 12 15 12
A< PRM DL 7 13 9 13 4 15 12 15
A< TIL A 1 9
A< TIL B 3 2
A< TIL C 2 3
A< TIL D 2 4
A< TIL E 1 11
A< TIL F 4 2
A< TIL G 3 3
A< TIL H 3 3
A< TIL I 1 8
A< TIL J 8 1
A< TIL K 5 1
A< TIL L 1 4
A< TIL M 3 2
A< TIL N 1 6
A< TIL O 1 8
A< TIL P 3 2
A< TIL Q 10 1
A< TIL R 1 6
A< TIL S 1 4
A< TIL T 1 7
A< TIL U 2 3
A< TIL V 5 2
A< TIL W 4 2
A< TIL X 7 1
A< TIL Y 3 2
A< TIL Z 10 1
A< TIL _ 0 2
A< R RTHIOVT
A< K 0 7
A< K 1 7
A< BAG 86
A< T 0 0
A< T 1 0
A< W 0
A< CLK 0 <secs> 86400
A< S Your move
A< OK
# Check a play: these letters are not a word.
A> CHK 1 PLAY 8 8 H HTORT
A< ERR ILLEGAL not in the word list: HTORT
# This one is: the points and the words.
A> CHK 1 PLAY 8 8 H TROTH
A< OK 26 TROTH
A> MOV 1 PLAY 8 8 H TROTH
A< EVT 1 5 PLAY 0 8 8 H TROTH 26
A< EVT 1 6 X 8 8 T 9 8 R 10 8 O 11 8 T 12 8 H
A< EVT 1 7 DRAW 0 5
A< EVT 1 8 TURN 1
A< OK
B> VUE 1
B< V gridwords/1 8
B< X 8 8 T 9 8 R 10 8 O 11 8 T 12 8 H
B< R AROOETN
B< K 0 7
B< K 1 7
B< BAG 81
B< T 0 26
B< T 1 0
B< W 1
B< LAST 0 8 8 H TROTH 26
B< CLK 1 <secs> 86400
B< OK
B> CHK 1 PLAY 8 6 V ANTE
B< OK 4 ANTE
B> MOV 1 PLAY 8 6 V ANTE
B< EVT 1 9 PLAY 1 8 6 V ANTE 4
B< EVT 1 10 X 8 6 A 8 7 N 8 9 E
B< EVT 1 11 DRAW 1 3
B< EVT 1 12 TURN 0
B< OK
A> VUE 1
A< V gridwords/1 12
A< X 8 6 A 8 7 N 8 8 T 9 8 R 10 8 O 11 8 T 12 8 H 8 9 E
A< R IVXAEEQ
A< K 0 7
A< K 1 7
A< BAG 78
A< T 0 26
A< T 1 4
A< W 0
A< LAST 1 8 6 V ANTE 4
A< CLK 0 <secs> 86400
A< OK
# Two passes or exchanges by each player end the game.
A> MOV 1 EXCH IV
A< EVT 1 13 EXCH 0 2
A< EVT 1 14 TURN 1
A< OK
B> MOV 1 PASS
B< EVT 1 13 EXCH 0 2
B< EVT 1 14 TURN 1
B< EVT 1 15 PASS 1
B< EVT 1 16 TURN 0
B< OK
A> MOV 1 PASS
A< EVT 1 15 PASS 1
A< EVT 1 16 TURN 0
A< EVT 1 17 PASS 0
A< EVT 1 18 TURN 1
A< OK
B> MOV 1 PASS
B< EVT 1 17 PASS 0
B< EVT 1 18 TURN 1
B< EVT 1 19 PASS 1
B< EVT 1 20 ADJ 0 -25 XAEEQNW
B< EVT 1 21 ADJ 1 -9 ROOTUDE
B< EVT 1 22 END win 0 1/-5
B< OK
A> VUE 1 T
A< V gridwords/1 22
A< X 8 6 A 8 7 N 8 8 T 9 8 R 10 8 O 11 8 T 12 8 H 8 9 E
A< R XAEEQNW
A< K 0 7
A< K 1 7
A< BAG 78
A< T 0 1
A< T 1 -5
A< W -1
A< LAST 1 8 6 V ANTE 4
A< S Game over: win 0 1/-5
A< OK
```

### Cannon Fire

Spec: [types/cannonfire-1.md](types/cannonfire-1.md).

| | |
|---|---|
| Type | `cannonfire/1`, ruleset `classic` |
| Players | 2 to 8 (`players=n`) |
| Flags | `nomoves`, `drop` |
| Options | `w=n` (the arena's width, 40 to 200, at least 8 columns a player; default 72); `seed=n` (the landscape and wind from this number instead of the table's seed); `wind=0` or `1` (default 1) |

Tanks on a landscape that shells dig into, taking turns to fire; before its shot a tank may drive a short way, four times a game. All the physics run on the server in whole numbers, so a frontend never simulates anything: the events say where the shell went and what it did. The spec gives every rule and constant, so another program can replay a game exactly.

**Geometry**: `ARN w h`, the arena's width and greatest height (63).

**View lines**:

| Line | Meaning |
|---|---|
| `Z x chars` | The terrain from column x on, one character per column: height h is the character `chr(48 + h)` (`0` to `o`); up to 60 a line. |
| `K seat x hp` | A tank: its column and hit points (0: destroyed). It stands on the terrain of its column. |
| `A seat angle power` | Each seat's last aim. |
| `L seat moves` | The moves each tank has left this game (4 at the start). |
| `N w` | The wind, -9 to 9; positive blows towards higher columns. |
| `H x y` | Where the last shell burst. |
| `W seat` | Whose shot; `-1` when the game is over. |
| `B budget moved` | The path one move may use, in tenths of a column (the arena's width: a tenth of it in columns), and `1` once the seat on turn has moved this turn. |
| `T seat points` | Scores. |
| `G g0 g1 ...` | Once a seat has left: 1 for each seat that left. |

**Moves**: a turn is an optional move, then a shot. `MOVE L n` or `MOVE R n` (or `MOVE -n`, `MOVE +n`): drive towards lower or higher columns using up to n columns of path, measured along the ground, so a slope takes more path than the flat. The tank goes as far as it can; the arena's edge, another tank or wreck, and a step of more than 4 units stop it. One move a turn, before the shot, four a game; the turn stays with the player (no `TURN`, and the turn clock runs on). `FIRE angle power`: the angle 0 to 180 (0 towards higher columns, 90 straight up), the power 0 to 100; it ends the turn.

**Events**: a move gives `MOVE seat from to used left` (the columns, the path used in tenths of a column, the moves left). For each shot in this order: `FIRE seat angle power`; `TRJ x y ...` (up to 18 points of the flight, 6 a line); `HIT x y r` (the burst and the crater's radius) or `OUT x` (the shell left the arena); `DMG seat hp` (the tank's hit points after the blast, which takes 20 for a direct hit and less with distance); `ZCH x chars` (the changed terrain, as in `Z`); `FALL seat x hp` (a tank whose ground dropped; falls do no damage); `DIE seat`; `N w` (a new wind); then `TURN seat` or `END win seat s0-s1-...` / `END draw s0-s1-...`. A frontend that skips `TRJ` just shows the burst.

**CHK**: for a shot, `OK angle a power p` when the numbers are in range and it is your turn; it does not say where the shell will land. For a move, `OK from x0 to x1 used u of b`: where the tank would end up and the path it would use.

**Hidden**: nothing.

**Sizes**: a full view about 150 bytes for two players and 270 for eight; a move about 20 bytes of events, a shot 100 to 250.

**Example**: the landscape of the spec's recorded game (`seed=2026`). ann moves up a slope, then fires; bob climbs out of the crater and fires back; ann fires again. The game goes on from there.

```pnp cannonfire
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
# seed=2026: the landscape and wind of the type spec's recorded game.
A> NEW cannonfire classic seed=2026
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> VUE 1 GT
A< V cannonfire/1 5
A< ARN 72 63
A< Z 0 QNOMONKMMKKKKLLKLLKJJHFHGFIIIKLMOLKKKJHFFFFEFCCDCCAA?<877878
A< Z 60 666556675566
A< K 0 11 100
A< K 1 61 100
A< A 0 45 50
A< A 1 135 50
A< L 0 4
A< L 1 4
A< N -4
A< W 0
A< B 72 0
A< T 0 0
A< T 1 0
A< CLK 0 <secs> 86400
A< S Your shot
A< OK
# CHK previews a move: where the tank would end up, and the path it would use of the budget, in tenths of a column (7.2 columns in a 72-column arena).
A> CHK 1 MOVE L 7
A< OK from 11 to 7 used 52 of 72
# Up the slope: 4 columns for 5.2 columns of path. No TURN: A still has the turn and fires next.
A> MOV 1 MOVE L 7
A< EVT 1 6 MOVE 0 11 7 52 3
A< OK
# One move a turn.
A> MOV 1 MOVE R 2
A< ERR ILLEGAL one move a turn, and you have moved: now fire
# CHK checks the shot's numbers only.
A> CHK 1 FIRE 73 98
A< OK angle 73 power 98
# A direct hit takes 20, the most a shot does (less with distance, none from 10 away). The crater drops bob's tank 6; it falls with the ground, unharmed.
A> MOV 1 FIRE 73 98
A< EVT 1 7 FIRE 0 73 98
A< EVT 1 8 TRJ 11 43 15 54 19 63 22 70 26 77 29 81
A< EVT 1 9 TRJ 32 84 36 85 39 85 42 82 44 79 47 74
A< EVT 1 10 TRJ 50 67 52 58 54 48 57 36 59 23 61 7
A< EVT 1 11 HIT 61 7 7
A< EVT 1 12 DMG 1 80
A< EVT 1 13 ZCH 54 74321110111234
A< EVT 1 14 FALL 1 61 80
A< EVT 1 15 N -2
A< EVT 1 16 TURN 1
A< OK
# The other tank sees the events it missed first. Its move climbs out of the crater: 5 columns for 6.2 columns of path.
B> MOV 1 MOVE R 7
B< EVT 1 6 MOVE 0 11 7 52 3
B< EVT 1 7 FIRE 0 73 98
B< EVT 1 8 TRJ 11 43 15 54 19 63 22 70 26 77 29 81
B< EVT 1 9 TRJ 32 84 36 85 39 85 42 82 44 79 47 74
B< EVT 1 10 TRJ 50 67 52 58 54 48 57 36 59 23 61 7
B< EVT 1 11 HIT 61 7 7
B< EVT 1 12 DMG 1 80
B< EVT 1 13 ZCH 54 74321110111234
B< EVT 1 14 FALL 1 61 80
B< EVT 1 15 N -2
B< EVT 1 16 TURN 1
B< EVT 1 17 MOVE 1 61 66 62 3
B< OK
B> MOV 1 FIRE 104 100
B< EVT 1 18 FIRE 1 104 100
B< EVT 1 19 TRJ 63 15 60 25 57 34 54 41 51 48 48 53
B< EVT 1 20 TRJ 45 57 42 60 38 62 35 63 32 63 29 61
B< EVT 1 21 TRJ 25 59 22 55 18 50 15 44 11 37 7 28
B< EVT 1 22 HIT 7 30 7
B< EVT 1 23 DMG 0 80
B< EVT 1 24 ZCH 0 PKJIHHHGHHHIJK
B< EVT 1 25 FALL 0 7 80
B< EVT 1 26 N -3
B< EVT 1 27 TURN 0
B< OK
# A fires without moving; it has 3 moves left.
A> MOV 1 FIRE 73 98
A< EVT 1 17 MOVE 1 61 66 62 3
A< EVT 1 18 FIRE 1 104 100
A< EVT 1 19 TRJ 63 15 60 25 57 34 54 41 51 48 48 53
A< EVT 1 20 TRJ 45 57 42 60 38 62 35 63 32 63 29 61
A< EVT 1 21 TRJ 25 59 22 55 18 50 15 44 11 37 7 28
A< EVT 1 22 HIT 7 30 7
A< EVT 1 23 DMG 0 80
A< EVT 1 24 ZCH 0 PKJIHHHGHHHIJK
A< EVT 1 25 FALL 0 7 80
A< EVT 1 26 N -3
A< EVT 1 27 TURN 0
A< EVT 1 28 FIRE 0 73 98
A< EVT 1 29 TRJ 11 37 15 48 19 57 22 64 26 71 30 75
A< EVT 1 30 TRJ 33 78 37 79 40 79 43 76 46 73 49 68
A< EVT 1 31 TRJ 52 61 55 52 58 42 60 30 63 17 66 1
A< EVT 1 32 HIT 66 4 7
A< EVT 1 33 DMG 1 60
A< EVT 1 34 ZCH 62 0000000000
A< EVT 1 35 FALL 1 66 60
A< EVT 1 36 N -5
A< EVT 1 37 TURN 1
A< OK
# Five direct hits destroy a tank, so a game runs to ten shots or more.
B> VUE 1 T
B< V cannonfire/1 37
B< Z 0 PKJIHHHGHHHIJKLKLLKJJHFHGFIIIKLMOLKKKJHFFFFEFCCDCCAA?<743211
B< Z 60 100000000000
B< K 0 7 80
B< K 1 66 60
B< A 0 73 98
B< A 1 104 100
B< L 0 3
B< L 1 3
B< N -5
B< H 66 4
B< W 1
B< B 72 0
B< T 0 40
B< T 1 20
B< CLK 1 <secs> 86400
B< S Your shot
B< OK
```

### Dots and Boxes

Spec: [types/dotsandboxes-1.md](types/dotsandboxes-1.md).

| | |
|---|---|
| Type | `dotsandboxes/1`, rulesets `standard` and `majority` (13 boxes across and 11 down, two players only; the game ends as soon as one player holds more than half the boxes) |
| Players | 2 to 4 (`players=n`) |
| Flags | `drop` |
| Options | `size=n` or `size=wxh`: boxes across (and down), 3 to 9 each way; default 5 (under `majority` the board is always 13 by 11) |

Dots are a column letter and a row number, `a1` at the top left. A line joins two neighboring dots; a box is named by its upper left dot.

**Geometry**: `BRD w h`, boxes across and down.

**View lines**:

| Line | Meaning |
|---|---|
| `HL y cells` | The lines across in dot row y: `.` open, `-` drawn. |
| `VL y cells` | The lines down from dot row y: `.` open, a vertical bar (ASCII 124) drawn. |
| `BX y cells` | The boxes in row y: `.` open, or the seat that closed it. |
| `C n0 n1 ...` | Boxes per seat. |
| `G g0 g1 ...` | Once a seat has left: 1 for each seat that left. |
| `LL line` | The last line drawn. |
| `W seat` | Whose move; `-` when the game is over. |

**Moves**: a line as its two dots, `a1-b1` (across) or `c2-c3` (down). The server also takes the dots in either order, without the dash, or as two tokens (`MOV 1 a1 b1`). A player who closes a box moves again. The move list holds every open line.

**Events**: `LINE seat line [BOX box ...]` (the boxes it closed), `TURN seat` after every move (also when the same player moves again: the clock starts afresh), `GONE seat resign|timeout`, `END win seat n0-n1-...` or `END draw n0-n1-...`.

**CHK**: `OK boxes n`, the boxes the line would close.

**Hidden**: nothing.

**Sizes**: a full view about 270 bytes on 5 by 5 and 530 on 9 by 9, plus 9 to 11 bytes for each `M` line; an event 10 to 30 bytes.

**Example**: three players. A box closed earns another move; one player resigns and the other two play on; then a resignation with two seats left ends the game.

```pnp dotsandboxes
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
C> PARLORNET/1
C< PARLORNET/1 parlornet AUTH=none SIGNUP=none
C> HELO cy
C< OK -
A> NEW dotsandboxes standard players=3 size=3
A< OK 1
A> JON 1
A< OK 0
B> JON 1
B< OK 1
C> JON 1
C< OK 2
A> MOV 1 a1-b1
A< EVT 1 2 JOIN 1 bob
A< EVT 1 3 JOIN 2 cy
A< EVT 1 4 START
A< EVT 1 5 TURN 0
A< EVT 1 6 LINE 0 a1-b1
A< EVT 1 7 TURN 1
A< OK
B> MOV 1 a1-a2
B< EVT 1 3 JOIN 2 cy
B< EVT 1 4 START
B< EVT 1 5 TURN 0
B< EVT 1 6 LINE 0 a1-b1
B< EVT 1 7 TURN 1
B< EVT 1 8 LINE 1 a1-a2
B< EVT 1 9 TURN 2
B< OK
# Either dot first, the dash left out: the event names the line.
C> MOV 1 b2b1
C< EVT 1 6 LINE 0 a1-b1
C< EVT 1 7 TURN 1
C< EVT 1 8 LINE 1 a1-a2
C< EVT 1 9 TURN 2
C< EVT 1 10 LINE 2 b1-b2
C< EVT 1 11 TURN 0
C< OK
# How many boxes would this line close?
A> CHK 1 a2-b2
A< OK boxes 1
# It closes box a1: ann moves again.
A> MOV 1 a2-b2
A< EVT 1 8 LINE 1 a1-a2
A< EVT 1 9 TURN 2
A< EVT 1 10 LINE 2 b1-b2
A< EVT 1 11 TURN 0
A< EVT 1 12 LINE 0 a2-b2 BOX a1
A< EVT 1 13 TURN 0
A< OK
A> MOV 1 c3-d3
A< EVT 1 14 LINE 0 c3-d3
A< EVT 1 15 TURN 1
A< OK
B> VUE 1 GT
B< V dotsandboxes/1 15
B< BRD 3 3
B< HL 1 -..
B< HL 2 -..
B< HL 3 ..-
B< HL 4 ...
B< VL 1 ||..
B< VL 2 ....
B< VL 3 ....
B< BX 1 0..
B< BX 2 ...
B< BX 3 ...
B< C 1 0 0
B< LL c3-d3
B< W 1
B< CLK 1 <secs> 86400
B< S Your move
B< OK
# With three playing, cy leaves and the others play on.
C> RES 1
C< EVT 1 12 LINE 0 a2-b2 BOX a1
C< EVT 1 13 TURN 0
C< EVT 1 14 LINE 0 c3-d3
C< EVT 1 15 TURN 1
C< EVT 1 16 GONE 2 resign
C< OK
B> MOV 1 a4-b4
B< EVT 1 16 GONE 2 resign
B< EVT 1 17 LINE 1 a4-b4
B< EVT 1 18 TURN 0
B< OK
A> MOV 1 c1-c2
A< EVT 1 16 GONE 2 resign
A< EVT 1 17 LINE 1 a4-b4
A< EVT 1 18 TURN 0
A< EVT 1 19 LINE 0 c1-c2
A< EVT 1 20 TURN 1
A< OK
# With two left, a resignation ends the game.
B> RES 1
B< EVT 1 19 LINE 0 c1-c2
B< EVT 1 20 TURN 1
B< EVT 1 21 END resign 1
B< OK
C> SIN 1
C< EVT 1 17 LINE 1 a4-b4
C< EVT 1 18 TURN 0
C< EVT 1 19 LINE 0 c1-c2
C< EVT 1 20 TURN 1
C< EVT 1 21 END resign 1
C< OK 21
```

### Five in a Row

Spec: [types/fiveinarow-1.md](types/fiveinarow-1.md).

| | |
|---|---|
| Type | `fiveinarow/1`, rulesets `freestyle` (five or more in a line wins) and `standard` (exactly five; six or more does not win) |
| Players | 2 |
| Flags | `nomoves` |
| Options | `size=n`: the board is n by n points, 9 to 20; default 15 |

**Geometry**: `BRD w h` and `RUL rules`.

**View lines**:

| Line | Meaning |
|---|---|
| `P seat pt ...` | One seat's stones in the order played (several `P` lines for many stones; append them). |
| `C n0 n1` | Stones per seat. |
| `L seat pt` | The last stone. |
| `F pt ...` | The winning line, once there is one. |
| `W seat` | Whose move; `-1` when the game is over. |

**Moves**: one point, `MOV 1 h8`. Every empty point is legal, so there is no move list.

**Events**: `PUT seat pt`, `TURN seat`, `END win seat pt-pt` (the two ends of the winning line) or `END draw n0-n1` (a full board).

**CHK**: `OK wins` for a winning move, `OK` for any other legal one.

**Hidden**: nothing.

**Sizes**: about 60 bytes at the start and 200 after 40 stones; an event 10 to 15 bytes.

**Example**: a short game on a 9 by 9 board: seat 0 makes five down the e column.

```pnp fiveinarow
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW fiveinarow freestyle size=9
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> MOV 1 e5
A< EVT 1 2 JOIN 1 bob
A< EVT 1 3 START
A< EVT 1 4 TURN 0
A< EVT 1 5 PUT 0 e5
A< EVT 1 6 TURN 1
A< OK
B> MOV 1 a9
B< EVT 1 5 PUT 0 e5
B< EVT 1 6 TURN 1
B< EVT 1 7 PUT 1 a9
B< EVT 1 8 TURN 0
B< OK
A> MOV 1 e4
A< EVT 1 7 PUT 1 a9
A< EVT 1 8 TURN 0
A< EVT 1 9 PUT 0 e4
A< EVT 1 10 TURN 1
A< OK
B> MOV 1 b9
B< EVT 1 9 PUT 0 e4
B< EVT 1 10 TURN 1
B< EVT 1 11 PUT 1 b9
B< EVT 1 12 TURN 0
B< OK
A> MOV 1 e3
A< EVT 1 11 PUT 1 b9
A< EVT 1 12 TURN 0
A< EVT 1 13 PUT 0 e3
A< EVT 1 14 TURN 1
A< OK
B> MOV 1 c9
B< EVT 1 13 PUT 0 e3
B< EVT 1 14 TURN 1
B< EVT 1 15 PUT 1 c9
B< EVT 1 16 TURN 0
B< OK
A> MOV 1 e2
A< EVT 1 15 PUT 1 c9
A< EVT 1 16 TURN 0
A< EVT 1 17 PUT 0 e2
A< EVT 1 18 TURN 1
A< OK
B> MOV 1 d9
B< EVT 1 17 PUT 0 e2
B< EVT 1 18 TURN 1
B< EVT 1 19 PUT 1 d9
B< EVT 1 20 TURN 0
B< OK
A> VUE 1 GT
A< V fiveinarow/1 20
A< BRD 9 9
A< RUL freestyle
A< P 0 e5 e4 e3 e2
A< P 1 a9 b9 c9 d9
A< C 4 4
A< L 1 d9
A< W 0
A< CLK 0 <secs> 86400
A< S Your move
A< OK
A> CHK 1 f5
A< OK
# CHK says whether a move wins.
A> CHK 1 e6
A< OK wins
A> MOV 1 e6
A< EVT 1 21 PUT 0 e6
A< EVT 1 22 END win 0 e2-e6
A< OK
B> VUE 1 T
B< V fiveinarow/1 22
B< P 0 e5 e4 e3 e2 e6
B< P 1 a9 b9 c9 d9
B< C 5 4
B< L 0 e6
B< F e2 e3 e4 e5 e6
B< W -1
B< S Game over: win 0 e2-e6
B< OK
```

### Mancala

Spec: [types/mancala-1.md](types/mancala-1.md).

| | |
|---|---|
| Type | `mancala/1`, rulesets `kalah` (store rules) and `oware` (Oware, abapa rules) |
| Players | 2 |
| Flags | none |
| Options | `seeds=n` (kalah only: seeds per pit at the start, 3 to 6; default 4) |

Two rows of six pits and a store for each seat. Seeds are sown counterclockwise from the mover's own pits.

**Geometry**: `BRD 6`, pits per row.

**View lines**:

| Line | Meaning |
|---|---|
| `RU rules seeds` | The ruleset and the seeds each pit started with. |
| `B c1 ... c14` | The board in sowing order: seat 0's pits 1 to 6, seat 0's store, seat 1's pits 1 to 6, seat 1's store. |
| `LM seat pit` | The last move. |
| `W seat` | Whose move; `-1` when the game is over. |

**Moves**: a pit number, 1 to 6, counted along the mover's own row from the mover's left. The move list holds the pits the mover may play.

**Events**: `SOW seat pit n c1 ... c14` (the board after the sowing and any capture: a frontend replaces its board), `CAP seat n`, `AGAIN seat` (kalah: the last seed fell in the mover's store, who moves again), `GRAND seat` (oware: a capture of the whole row is not made), `SWEEP seat n why` (at the end, seeds left on a row go to their owner's store), `TURN seat`, `END win seat s0-s1` or `END draw s0-s1`.

**CHK**: `OK captures n`, the seeds the move would capture.

**Hidden**: nothing.

**Sizes**: a full view about 60 bytes; `SOW` at most about 55 bytes, other events under 20.

**Example**: store rules. A last seed in the store earns another move; then a capture.

```pnp mancala
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW mancala kalah
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> VUE 1 GTM
A< V mancala/1 4
A< BRD 6
A< RU kalah 4
A< B 4 4 4 4 4 4 0 4 4 4 4 4 4 0
A< W 0
A< CLK 0 <secs> 86400
A< S Your move
A< M 1
A< M 2
A< M 3
A< M 4
A< M 5
A< M 6
A< OK
# The last seed lands in ann's store: she moves again.
A> MOV 1 3
A< EVT 1 5 SOW 0 3 4 4 4 0 5 5 5 1 4 4 4 4 4 4 0
A< EVT 1 6 AGAIN 0
A< EVT 1 7 TURN 0
A< OK
A> MOV 1 6
A< EVT 1 8 SOW 0 6 5 4 4 0 5 5 0 2 5 5 5 5 4 4 0
A< EVT 1 9 TURN 1
A< OK
B> MOV 1 6
B< EVT 1 5 SOW 0 3 4 4 4 0 5 5 5 1 4 4 4 4 4 4 0
B< EVT 1 6 AGAIN 0
B< EVT 1 7 TURN 0
B< EVT 1 8 SOW 0 6 5 4 4 0 5 5 0 2 5 5 5 5 4 4 0
B< EVT 1 9 TURN 1
B< EVT 1 10 SOW 1 6 4 5 5 1 5 5 0 2 5 5 5 5 4 0 1
B< EVT 1 11 TURN 0
B< OK
# CHK tells how many seeds a move would capture.
A> CHK 1 1
A< OK captures 6
A> MOV 1 1
A< EVT 1 10 SOW 1 6 4 5 5 1 5 5 0 2 5 5 5 5 4 0 1
A< EVT 1 11 TURN 0
A< EVT 1 12 SOW 0 1 5 0 6 2 6 6 0 8 0 5 5 5 4 0 1
A< EVT 1 13 CAP 0 6
A< EVT 1 14 TURN 1
A< OK
B> VUE 1 T
B< V mancala/1 14
B< RU kalah 4
B< B 0 6 2 6 6 0 8 0 5 5 5 4 0 1
B< LM 0 1
B< W 1
B< CLK 1 <secs> 86400
B< S Your move
B< OK
B> RES 1
B< EVT 1 15 END resign 1
B< OK
```

### Checkers

Spec: [types/checkers-1.md](types/checkers-1.md).

| | |
|---|---|
| Type | `checkers/1`, ruleset `american` |
| Players | 2 |
| Flags | `staged` |
| Options | `limit=n`: moves each with no capture and no man moving before the game is a draw, 10 to 200, or 0 for none; default 40 |

Seat 0 is Black, seat 1 White. The 32 dark squares are numbered as in published checkers games: 1 to 4 on the top row, 29 to 32 on the bottom. Black's men start on 1 to 12 and move towards 32; White's start on 21 to 32.

**Geometry**: `BRD 8`.

**View lines**:

| Line | Meaning |
|---|---|
| `B cells` | One character per dark square in number order: `.` empty, `x` and `X` a Black man and king, `o` and `O` a White man and king. |
| `C m0 k0 m1 k1` | Men and kings per seat. |
| `W seat` | Whose move; `-` when the game is over. |
| `Q phase` | `move`, `done` (the turn's steps are complete: `END` or `UND`), `draw` (answer a draw offer), `over`. |
| `L quiet limit` | Turns in a row with no capture and no man moving, and the limit for a draw. |
| `J sq sq ...` | While a turn is part played: the moving piece's path. |
| `D seat` | A draw offer waiting for an answer. |

**Moves**: a turn is staged, one step per token: `11-15` (a move) or `15x22` (a jump; `15-22` is taken too). A chain (`15x22x31`) or several tokens on one `MOV` plays those steps in order, all or none. A capture is compulsory, and a jump that can go on must; a man reaching the far row is crowned and the turn ends there. `draw` offers a draw (on your turn before a step, or on the other player's turn before they step), answered `accept` or `reject`. When the turn is complete, `MOV` answers `OK done`; send `END`.

**Events**: `STEP seat from to`, `JUMP seat from to sq` (the piece on sq is taken), `CROWN seat sq`, `UNDO seat from to piece [sq captured]`, `DRAW seat`, `ACCEPT seat`, `REJECT seat`, `TURN seat`, and `END win seat captured`, `END win seat blocked`, `END draw agreed` or `END draw limit`.

**CHK**: `OK` with a word (`ok`, `draw`, `end` ...) when the steps or answer would be accepted.

**Hidden**: nothing.

**Sizes**: a full view about 110 bytes; an event 10 to 25 bytes.

**Example**: a step taken back and played again, a compulsory capture and its answer, and a draw by agreement.

```pnp checkers
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW checkers american
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> VUE 1 GTM
A< V checkers/1 4
A< BRD 8
A< B xxxxxxxxxxxx........oooooooooooo
A< C 12 0 12 0
A< W 0
A< Q move
A< L 0 80
A< CLK 0 <secs> 86400
A< S Your move
A< M 9-13
A< M 9-14
A< M 10-14
A< M 10-15
A< M 11-15
A< M 11-16
A< M 12-16
A< OK
# One step; the turn is complete, so the reply is OK done.
A> MOV 1 11-15
A< EVT 1 5 STEP 0 11 15
A< OK done
A> UND 1
A< EVT 1 6 UNDO 0 11 15 x
A< OK more
A> MOV 1 11-15
A< EVT 1 7 STEP 0 11 15
A< OK done
A> END 1
A< EVT 1 8 TURN 1
A< OK
B> MOV 1 22-18
B< EVT 1 5 STEP 0 11 15
B< EVT 1 6 UNDO 0 11 15 x
B< EVT 1 7 STEP 0 11 15
B< EVT 1 8 TURN 1
B< EVT 1 9 STEP 1 22 18
B< OK done
B> END 1
B< EVT 1 10 TURN 0
B< OK
# A capture is compulsory.
A> VUE 1 TM
A< V checkers/1 10
A< B xxxxxxxxxx.x..x..o..o.oooooooooo
A< C 12 0 12 0
A< W 0
A< Q move
A< L 0 80
A< CLK 0 <secs> 86400
A< S Your move: you must capture
A< M 15x22
A< OK
A> MOV 1 15x22
A< EVT 1 11 JUMP 0 15 22 18
A< OK done
A> END 1
A< EVT 1 12 TURN 1
A< OK
B> MOV 1 26x17
B< EVT 1 11 JUMP 0 15 22 18
B< EVT 1 12 TURN 1
B< EVT 1 13 JUMP 1 26 17 22
B< OK done
B> END 1
B< EVT 1 14 TURN 0
B< OK
# Offer a draw.
A> MOV 1 draw
A< EVT 1 13 JUMP 1 26 17 22
A< EVT 1 14 TURN 0
A< EVT 1 15 DRAW 0
A< EVT 1 16 TURN 1
A< OK
B> MOV 1 accept
B< EVT 1 15 DRAW 0
B< EVT 1 16 TURN 1
B< EVT 1 17 ACCEPT 1
B< EVT 1 18 END draw agreed
B< OK
```

### Naval Combat

Spec: [types/navalcombat-1.md](types/navalcombat-1.md).

| | |
|---|---|
| Type | `navalcombat/1`, rulesets `classic` (ships of 5, 4, 3, 3 and 2 squares, which may touch) `batnav` (ten ships of 4, 3, 3, 2, 2, 2, 1, 1, 1, 1 squares, which may not touch) and `salvo` (the classic fleet, six shots a turn) |
| Players | 2 |
| Flags | none |
| Options | none |

Each player places a fleet on their own 10 by 10 grid, then the two take turns firing one shot at the other's grid (under `salvo`, six shots a turn). Squares are `a1` to `j10`.

**Geometry**: `BRD 10 10` and `FL len ...`, the fleet in placement order.

**View lines**:

| Line | Meaning |
|---|---|
| `Q phase` | `place`, `shoot` or `over`. |
| `W seat` | Whose move; `-` when over. While placing, a seat whose fleet is not ready sees its own seat. |
| `RD r0 r1` | 1 when that seat's fleet is ready. |
| `B seat y cells` | Row y of a seat's waters: `.` nothing known, `o` a miss, `x` a hit, `#` a square of a sunk ship, and, in your own waters only, `s` a ship square not hit. Empty rows are not sent. |
| `Y len from-to` | Your own ships. |
| `N len` | While placing: the length of your next ship. |
| `K seat len from-to` | A sunk ship. |
| `L n0 n1` | Ships afloat per seat. |
| `LS seat sq` | The last shot. |
| `SL n` | Under `salvo`, while shooting: the shots left in the salvo of the seat to move. |
| `OF len from-to` | Under `salvo`, once both fleets are ready: the other seat's ships, for seated players only. |

**Moves**: while placing, both players place at once, largest ship first: `from-to`, the ship's two end squares (`a1-e1`; a one-square ship is `c4-c4`). `random` places the ships not yet placed, `undo` takes back the last, `clear` all of them, and `ready` fixes the fleet. Shooting starts when both fleets are ready: one square per turn, `MOV 1 e5` (under `salvo`, six such moves a turn, each followed by `TURN` for the same seat). The move list holds every place for the next ship and then the words (while placing, for both players at once), or, on your turn to shoot, every square not yet fired at.

**Events**: `READY seat`; `SHOT seat sq miss`, `SHOT seat sq hit` and `SHOT seat sq sunk len from-to`; `TURN seat`; `END win seat h0-h1` (the hits each seat landed). Placing ships sends no event.

**CHK**: `OK` when the placement or the shot is allowed. It never tells hit or miss.

**Hidden**: the fleets. A player sees their own ships (`Y`, and `s` in their own rows); of the other fleet, only the shots fired at it and the ships once sunk. Watchers see only shots and sunk ships on both grids. No event names a square of a ship that has not been hit. Under `salvo` each seated player's view also carries the other fleet (`OF`) once both are ready, because GameMaster works out the hits on the Mac.

**Sizes**: about 130 bytes while placing (the placement list adds up to about 140 lines of 9 to 11 bytes), at most about 650 bytes late in a game (850 under `batnav`); an event 15 to 30 bytes.

**Example**: ann places one ship (after a placement of the wrong length) and lets the server place the rest; her view shows her own fleet. Both get ready; ann sinks bob's two-square ship. bob's view then shows his own fleet in full, and of ann's waters only his miss.

```pnp navalcombat
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW navalcombat classic
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
# Ships are placed largest first; this one has five squares.
A> MOV 1 a1-a4
A< ERR ILLEGAL the next ship is 5 squares long
A> MOV 1 a1-e1
A< EVT 1 2 JOIN 1 bob
A< EVT 1 3 START
A< EVT 1 4 TURN 0
A< OK
# The server places the rest.
A> MOV 1 random
A< OK
A> VUE 1 GT
A< V navalcombat/1 4
A< BRD 10 10
A< FL 5 4 3 3 2
A< Q place
A< W 0
A< RD 0 0
A< B 0 1 sssss.....
A< B 0 2 ......s...
A< B 0 3 ......s...
A< B 0 4 ....sss...
A< B 0 6 .s........
A< B 0 7 .s........
A< B 0 8 .s....sss.
A< B 0 9 .s........
A< Y 5 a1-e1
A< Y 4 b6-b9
A< Y 3 e4-g4
A< Y 3 g8-i8
A< Y 2 g2-g3
A< L 5 5
A< CLK 0 <secs> 86400
A< S Fleet placed: ready when you are
A< OK
A> MOV 1 ready
A< EVT 1 5 READY 0
A< EVT 1 6 TURN 1
A< OK
B> MOV 1 random
B< EVT 1 5 READY 0
B< EVT 1 6 TURN 1
B< OK
B> MOV 1 ready
B< EVT 1 7 READY 1
B< EVT 1 8 TURN 0
B< OK
# CHK never tells hit or miss.
A> CHK 1 c2
A< OK
A> MOV 1 c2
A< EVT 1 7 READY 1
A< EVT 1 8 TURN 0
A< EVT 1 9 SHOT 0 c2 hit
A< EVT 1 10 TURN 1
A< OK
B> MOV 1 j10
B< EVT 1 9 SHOT 0 c2 hit
B< EVT 1 10 TURN 1
B< EVT 1 11 SHOT 1 j10 miss
B< EVT 1 12 TURN 0
B< OK
A> MOV 1 c3
A< EVT 1 11 SHOT 1 j10 miss
A< EVT 1 12 TURN 0
A< EVT 1 13 SHOT 0 c3 sunk 2 c2-c3
A< EVT 1 14 TURN 1
A< OK
# bob's view: his own fleet, and of ann's only the shots.
B> VUE 1 T
B< V navalcombat/1 14
B< Q shoot
B< W 1
B< RD 1 1
B< B 0 10 .........o
B< B 1 1 s.........
B< B 1 2 s.#.......
B< B 1 3 s.#....s.s
B< B 1 4 s......s.s
B< B 1 5 .......s.s
B< B 1 6 ..sssss...
B< Y 5 c6-g6
B< Y 4 a1-a4
B< Y 3 j3-j5
B< Y 3 h3-h5
B< Y 2 c2-c3
B< K 1 2 c2-c3
B< L 5 4
B< LS 0 c3
B< CLK 1 <secs> 86400
B< S Your shot
B< OK
B> RES 1
B< EVT 1 15 END resign 1
B< OK
```

### Rummy Tile Classic

Spec: [types/rummytile-1.md](types/rummytile-1.md).

| | |
|---|---|
| Type | `rummytile/1`, ruleset `standard` |
| Players | 2 to 4 (`players=n`) |
| Flags | `staged`, `nomoves`, `drop` |
| Options | none |

Tile rummy with 106 tiles: 1 to 13 in four colors, two of each, and two jokers. A tile is its color letter and number (`R7`, `B12`, `K1`, `Y13`; `R` red, `B` blue, `K` black, `Y` yellow) or `J`. A set is a run (three or more of one color in sequence) or a group (three or four of one number in different colors). A player's first meld must be worth 30 points from their own tiles.

**Geometry**: `TIL RBKY 13 2 2`: the colors, the highest number, the copies of each tile, the jokers.

**View lines**:

| Line | Meaning |
|---|---|
| `P n` | Tiles in the pool. |
| `C n0 n1 ...` | Tiles on each rack. |
| `I f0 f1 ...` | 1 for a seat that has made its first meld. |
| `G g0 g1 ...` | 1 for a seat that has left the game. |
| `W seat` | Whose turn; `-` when the game is over. |
| `ST k kind tiles ...` | Set k on the table: `r` a valid run, `g` a valid group, `x` not valid; `*` after the kind for a set made or changed this turn. |
| `H tiles ...` | Your rack, up to 16 tiles a line. |
| `TP n points code` | The turn so far: tiles played, first-meld points (`-` after the first meld) and what `END` would do: `ok`, or `none`, `sets`, `meld`, `joker` (the turn would be undone and a tile drawn). |
| `T seat points` | After the game: the scores. |

**Moves**: a turn is staged; each `MOV` is one step that rearranges the table and your rack tentatively. `R7-3` puts a rack tile into set 3; `R7B7K7-n` makes a new set from rack tiles; `3.2-5` moves tile 2 of set 3 to set 5 (`3.R7-5` names the tile); `3.2-r` takes back a tile you played this turn; `3-5` joins set 3 onto set 5; `3/4` splits set 3 before its tile 4; `reset` starts the turn over. `MOV` answers `OK done` when `END` would count the turn now, `OK more` otherwise. `END` always ends the turn: if the turn does not count, it is undone and the player draws a tile (or passes when the pool is empty).

**Events**: each step gives a step event (`PUT seat tiles target`, `MOVE seat k tile target`, `BACK seat k tile`, `MERGE seat k d`, `SPLIT seat k p`) followed by the exact change: `DEL k` for each set removed, `SET k kind tiles ...` for each set changed or added. `RESET seat` and `UNDO seat` take the table back (fetch the view). Ending the turn gives `MELD seat points`, `DONE seat n`, or `UNDONE seat reason` and `DRAW seat pool` or `PASS seat`; then `TURN seat`. The end is `OUT seat` (a rack emptied) and `END win seat s0 s1 ...` or `END draw s0 s1 ...`. `GONE seat resign|timeout` when a seat leaves.

**CHK**: `CHK table !end` asks what `END` would do now: `OK ok n tiles [meld points]`, or `ERR ILLEGAL` and the reason. `CHK table token ...` checks a step without making it.

**Hidden**: the racks. `H` is only in a seated player's own view; others see the count (`C`), and no event names a tile from a rack until it is played (`DRAW` gives only the pool's count).

**Sizes**: about 120 bytes at the start, about 1000 for a crowded table, under 2000 at worst; a step 40 to 120 bytes of events.

**Example**: ann makes her first meld in two steps (the first set alone is under 30 points, so `OK more`; with the second, `OK done`). bob puts down a single tile, which is not a set, checks what `END` would do, takes it back, and ends his turn with nothing played, so he draws a tile.

```pnp rummytile
A> PARLORNET/1
A< PARLORNET/1 parlornet AUTH=none SIGNUP=none
A> HELO ann
A< OK -
B> PARLORNET/1
B< PARLORNET/1 parlornet AUTH=none SIGNUP=none
B> HELO bob
B< OK -
A> NEW rummytile standard
A< OK 1
A> JON 1
A< OK 0
# The second player sits down: the game starts.
B> JON 1
B< OK 1
A> VUE 1 GT
A< V rummytile/1 5
A< TIL RBKY 13 2 2
A< P 78
A< C 14 14
A< I 0 0
A< G 0 0
A< W 0
A< H R1 R4 R6 B4 B5 B12 K3 K11 Y4 Y6 Y8 Y8 Y9 Y10
A< TP 0 0 none
A< CLK 0 <secs> 86400
A< S Your turn: play tiles from your rack, or end the turn to draw a tile
A< OK
A> MOV 1 Y8Y9Y10-n
A< EVT 1 6 PUT 0 Y8Y9Y10 n
A< EVT 1 7 SET 1 r Y8 Y9 Y10
A< OK more
A> MOV 1 R4B4Y4-n
A< EVT 1 8 PUT 0 R4B4Y4 n
A< EVT 1 9 SET 2 g R4 B4 Y4
A< OK done
# What END would do now.
A> CHK 1 !end
A< OK ok 6 tiles meld 39
A> END 1
A< EVT 1 10 MELD 0 39
A< EVT 1 11 DONE 0 6
A< EVT 1 12 TURN 1
A< OK
B> VUE 1
B< V rummytile/1 12
B< P 78
B< C 8 14
B< I 1 0
B< G 0 0
B< W 1
B< ST 1 r Y8 Y9 Y10
B< ST 2 g R4 B4 Y4
B< H R4 R9 R11 R12 R13 B1 B12 K1 K4 K7 K12 Y5 Y9 Y12
B< TP 0 0 none
B< CLK 1 <secs> 86400
B< OK
# One tile is not a set; before a first meld the table's sets cannot be touched.
B> MOV 1 R4-n
B< EVT 1 13 PUT 1 R4 n
B< EVT 1 14 SET 3 x R4
B< OK more
B> CHK 1 !end
B< ERR ILLEGAL set 3 is not valid
B> UND 1
B< EVT 1 15 UNDO 1
B< OK more
# Nothing played: END draws a tile.
B> END 1
B< EVT 1 16 DRAW 1 77
B< EVT 1 17 TURN 0
B< OK
A> RES 1
A< EVT 1 13 PUT 1 R4 n
A< EVT 1 14 SET 3 x R4
A< EVT 1 15 UNDO 1
A< EVT 1 16 DRAW 1 77
A< EVT 1 17 TURN 0
A< EVT 1 18 END resign 0
A< OK
```
