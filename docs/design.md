# Frontend design

This document describes how a ParlorNet frontend should look and work, so that a player who learns one frontend knows them all. It is for anyone writing a frontend, whether it runs in a terminal, on an 8-bit computer, in a browser, on a 1997 web page or inside an old game program. ParlorNet's own frontends follow it. They are telnet, the program for the Apple IIe, IIgs and Commodore 64, ParlorNet Play, the web pages and the ParlorNet rulebooks for GameMaster.

The design has three layers. The principles say what every frontend does and why. The shared words are the exact names, labels and sentences a player reads, and they are the same everywhere. The mappings say how each kind of frontend carries the principles with its own keys, pointer, buttons or forms. Native clients, the old programs that speak their own protocols, keep their own interfaces and aren't covered here.

## The principles

1. **The `/` key opens the command line, and the command line is only for commands and typed moves.** A player always knows where typing goes. A command starts with a slash, and a typed move starts with one too, as in `/d3`. The command line never answers a question or an offer, so a word typed out of habit can't accept a double.
2. **A game has a game mode and a command mode.** In game mode, keys play the game and never type. Keys are shortcuts there, and the arrows move the cursor. The `/` key switches to command mode, where every key types until Enter runs the line or Esc clears it. Two modes mean a key has one job at a time.
3. **Each letter has one meaning in every game.** R always resigns, U always undoes and D always doubles or offers a draw. A letter that a game has no use for does nothing. A player who learns a letter in one game never has to unlearn it in another.
4. **Offers and confirmations are dialogs.** A double, a draw offer, an offer to resign, an invitation and every "are you sure" question appear in a dialog over the board. The dialog is answered inside itself, with its own buttons or keys. A question that interrupts play should look like one, and it should never be answered by accident from the command line.
5. **The `?` key opens help that explains the keys for where you are.** It works the same at a table, in the lobby, in the tutorial and on the chat screen. A player who is lost needs one key to remember.
6. **The Esc key cancels the current operation, and with nothing to cancel it goes back to the lobby.** It drops a pick, clears a typed command line, closes a dialog or leaves an entry mode. When nothing is going on, it takes you to the lobby, and the game goes on without you. Esc never ends a game, a turn or a session, so pressing it is always safe.
7. **One sentence always says what you can do now.** The sentence line says whose turn it is and what the next key or click will do. A player who reads it never has to guess.
8. **Every frontend uses the same names, labels and order.** An action has one name, its button has one label, and the actions appear in one order. A player who moves from a terminal to a browser finds everything where they expect it.
9. **The server writes the shared sentences.** The turn sentence, the last move, the refusals and the help pages are written once, on the server, and every frontend shows them. That is the only way the words stay the same on every frontend, and it saves memory on small machines.
10. **Every action can be reached by keys alone and by pointer alone.** A keyboard player never needs a mouse, and a joystick, mouse or touch player never needs the keyboard, except to type chat or commands. Old machines have one or the other, and some players can use only one.
11. **The words are plain.** Every line on screen is a sentence a person would say aloud. The screen never shows internal words such as seat numbers, protocol verbs or port numbers, and it never uses labels like "PASS=p". A player reads a game, not a program.
12. **Nothing that ends a turn or a game happens without asking.** Resigning, passing, dropping a double, ending a turn that won't count and signing out all ask first, in a dialog. A slip of a finger should never cost a game.
13. **A few commands belong to the client, and the client never sends them to the server.** `/tutorial`, `/help` and redrawing the screen are the client's own, and every other `/` line goes to the server. A command that only changes what is on the player's screen has no business on the parlor. [Commands the client handles itself](#commands-the-client-handles-itself) says which ones and how.

## The shared words

### The actions

Every frontend names each action with the word in the first column. Buttons, links and menu items carry the label in the second column. The command is what a player types on the command line.

| Action | Label | Command | What it does |
|---|---|---|---|
| roll | Roll | `/roll` | It rolls the dice in Backgammon. |
| end the turn | End turn | `/end` | It finishes a turn made of steps, in Backgammon, Checkers and Rummy Tile. |
| play | Play | a typed move, such as `/h8 across QUIZ` | It plays the word laid in Grid Words. |
| fire | Fire | `/fire 45 60` | It fires in Cannon Fire. |
| ready | Ready | `/ready` | It says your fleet is placed, in Naval Combat. |
| undo | Undo | `/undo` | It takes back the last step of a turn that isn't finished. |
| reset | Reset | `/reset` | It takes back the whole turn in Rummy Tile, or every ship placed in Naval Combat. |
| pass | Pass | `/pass` | It gives up your turn without playing, after asking. |
| exchange | Exchange | `/exchange TILES` | It puts tiles back in the bag and draws new ones, in Grid Words. |
| check | Check | `/check` | It tells you whether a turn would count, or what a word would score, without playing it. |
| double | Double | `/double` | It offers to double the stakes in Backgammon. |
| draw | Draw | `/draw` | It offers a draw in Checkers. |
| new set | New set | a typed step | It makes a new set from the tiles you picked, in Rummy Tile. |
| split | Split | `/split` | It breaks a set in two before the tile under the cursor, in Rummy Tile. |
| sort | Sort | `/sort` | It sorts your rack by color or by number, in Rummy Tile. |
| random | Random | `/random` | It places the rest of your fleet at random, in Naval Combat. |
| drive | Drive left, Drive right | `/drive left 5` | It drives your tank before the shot, in Cannon Fire. |
| home | Home left, Home right | `/home left` | It moves your home board to that side in Backgammon. |
| cancel | Cancel | none | It drops what you picked. |
| say | Chat | `/say TEXT` | It sends a line to the table. |
| help | Help | `/help` | It opens help. |
| resign | Resign | `/resign` | It gives up the game, after asking. |
| leave | Lobby | `/leave` | It goes back to the lobby, and the game goes on. |
| sign out | Sign out | `/quit` | It signs you out, after asking, and your games go on. |

Each action has one command and one label. A frontend doesn't add other words for the same action.

### The order of actions

Wherever actions are listed, as buttons, links, a keys line or help, they come in this order.

1. The step that moves the game on now. That is Roll, Play, Fire, Ready or End turn, whichever applies.
2. Undo, then Reset.
3. The game's other actions. They are Pass, Exchange and Check in Grid Words, Double and the home board in Backgammon, Draw in Checkers, Random in Naval Combat, New set, Split, Check and Sort in Rummy Tile, and Drive left and Drive right in Cannon Fire.
4. Cancel, while something is picked.
5. The table's own actions, which are Chat, Help, Resign and Lobby, always last and always in that order.

An action appears only while it applies. On another player's turn, a frontend shows only what you can do then, such as Chat, Help, Resign and Lobby.

### One meaning per letter

These are the shortcut letters. Telnet and ParlorNet Play use the letter alone in game mode. The 8-bit program uses the letter with Open-Apple on the Apple II or the Commodore key on the C64, written OA here.

| Letter | 8-bit form | What it does | Games | Command |
|---|---|---|---|---|
| B | OA-B | It splits a set before the tile under the cursor. | Rummy Tile | `/split` |
| C | OA-C | It checks whether your turn would count. | Rummy Tile | `/check` |
| D | OA-D | It doubles, or offers a draw. | Backgammon, Checkers | `/double`, `/draw` |
| E | OA-E | It ends your turn. | Backgammon, Checkers, Rummy Tile | `/end` |
| G | OA-G | It places the rest of your fleet at random. | Naval Combat | `/random` |
| H | OA-H | It moves your home board to the other side. | Backgammon | `/home left`, `/home right` |
| M | OA-M | It makes a new set from the tiles you picked. | Rummy Tile | a typed step |
| O | OA-O | It sorts your rack. | Rummy Tile | `/sort` |
| P | OA-P | It passes your turn, after asking. | Grid Words, and Rummy Tile when you have played nothing | `/pass` |
| R | OA-R | It resigns, after asking. | every game | `/resign` |
| T | OA-T | It opens the chat. | every game | `/say TEXT` |
| U | OA-U | It takes back your last step. | Backgammon, Checkers, Rummy Tile, and Naval Combat while you place ships | `/undo` |
| V | OA-V | It shows the points of every letter. | Grid Words | `/help 2` |
| X | OA-X | It starts an exchange. | Grid Words | `/exchange TILES` |
| Z | OA-Z | It starts your turn over, or clears your fleet. | Rummy Tile, and Naval Combat while you place ships | `/reset` |

The letters A, F, I, J, K, L, N, Q, S, W and Y have no meaning at a table. Y and N answer dialogs, and only dialogs. The 8-bit program also uses OA-L to redraw the screen and OA-Q to sign out, because no game uses those letters.

Signs and digits have one meaning too. In Mancala, 1 to 6 move the cursor to that pit. In Cannon Fire, `<` and `>` turn the barrel by 10, `+` and `-` change the power by 10, and `[` and `]` drive the tank left or right.

The keys that work in every game are the arrows, Enter or Space, Esc, `/`, `?`, R and T.

### The sentence line

The sentence line holds two sentences. The first says whose turn it is. The server writes it, and every frontend shows it word for word. The second says what you can do now, in the frontend's own keys or buttons.

| Moment | The first sentence |
|---|---|
| your turn | "Your turn." Cannon Fire and Naval Combat say "Your shot." |
| another player's turn | "Waiting for ann." |
| your own table, before anyone sits down with you | "Waiting for a second player." |
| you are watching | "You are watching ann and bob." |
| the game is over | "Game over. ann won by 12 points." |
| an offer waits for your answer | "ann doubles to 4.", "ann offers a draw." or "ann offers to resign a gammon." |
| you made an offer | "You offered to double to 4. Waiting for ann." |
| a jump must go on | "You must jump." |
| placing ships | "Place your fleet." |
| a box was closed | "You closed a box, so it's your turn again." |
| a move sent and not yet answered | "Your move is on its way." |

The second sentence takes one of two forms.

- **The key form** is for frontends where the keyboard leads, which are telnet and the 8-bit program. It reads "Press KEY to VERB, or KEY to VERB." It names what Enter does on the place under the cursor, then the shortcuts that apply now. For example, "Your turn. Press Enter to roll the dice, or D to double to 2."
- **The button form** is for frontends where every action is a labeled button or link, which are Play, the web pages and GameMaster. It gives the same instruction without key names, because the buttons carry them. For example, "Your turn. Roll the dice, or double to 2."

The verbs are fixed. You "place a disc on d3", "place a stone on h8", "draw the line from a1 to b1", "sow the 4 seeds in pit 3", "roll the dice", "double to 2", "pick up the checker on 13", "move it to 9", "end your turn", "undo a step", "play QUIZ", "pass", "exchange tiles", "fire" and "place your 4-square ship".

| Game | Moment | The whole line, in the key form |
|---|---|---|
| Reversi | your turn | "Your turn. Press Enter to place a disc on d3." |
| Reversi | no legal square under the cursor | "Your turn. You can't place a disc on d3. The dots show where you can." |
| Mancala | your turn | "Your turn. Press Enter to sow the 4 seeds in pit 3." |
| Cannon Fire | your turn | "Your shot. Aim with the arrows and press Enter to fire. [ and ] drive first." |
| Backgammon | before rolling | "Your turn. Press Enter to roll the dice, or D to double to 2." |
| Backgammon | a checker picked | "Press Enter on a marked point to move there, or Esc to put it back." |
| Backgammon | all played | "Your dice are played. Press Enter to end your turn, or U to undo a step." |
| Backgammon | an offer put away | "ann doubles to 4. Press Enter to answer." |
| Checkers | your turn | "Your turn. Press Enter to pick up the man on 11, or D to offer a draw." |
| Grid Words | game mode | "Your turn. Press Enter where your word starts, P to pass or X to exchange." |
| Grid Words | tiles laid | "QUIZ scores 32. Press Enter to play it, or keep typing letters." |
| Naval Combat | placing | "Place your 4-square ship. Press Enter where it starts, or G for random." |
| Rummy Tile | your turn | "Your turn. Pick tiles from your rack with Enter, or press E to end your turn and take a tile." |
| any game | their turn | "Waiting for ann. Press T to chat, or ? for help." |
| any game | command mode | "/exchange puts the tiles you name back in the bag and draws new ones." |
| any game | an unused letter | "H does nothing in Reversi. Press ? to see the keys." |

A refusal replaces the second sentence until the next key or click. It says why in the player's own terms, as in "You can't play d3. It flips no discs." The line never uses semicolons, colons between ideas or labels, and it never names an action that doesn't apply.

### Dialogs

A dialog appears over the board for every offer and every confirmation. It holds a question, a sentence for what each answer does, and two buttons. A confirmation has Yes and No. An offer has Accept and Decline.

| Dialog | What it says | Buttons | Starts on |
|---|---|---|---|
| resign | "Resign this game? ann wins. Leaving the table never resigns." | Yes, No | No |
| pass | "Pass your turn? You play nothing this turn." | Yes, No | No |
| end a turn that won't count | "End your turn? The table doesn't count yet, so your tiles go back and you take a tile." | Yes, No | No |
| sign out | "Sign out? Your games go on without you." | Yes, No | No |
| a double | "ann doubles to 4. If you accept, the game goes on for 4 points. If you decline, you lose 2 points and the game." | Accept, Decline | Accept |
| a draw offer | "ann offers a draw. If you accept, the game ends as a draw. If you decline, the game goes on." | Accept, Decline | Decline |
| an offer to resign | "ann offers to resign a gammon. If you accept, you win 2 points and the game. If you decline, the game goes on." | Accept, Decline | Decline |
| an invitation | "ann invites you to play Grid Words at table 12. If you accept, you sit down there." | Accept, Decline | Accept |
| the tutorial, at the first sign-in | "Would you like a short tutorial on how the games work? You can run it again any time with /tutorial." | Yes, No | Yes |

At a table with more than two players, "ann wins." becomes "The others play on."

Every way of starting a confirmed action opens the same dialog. That covers the key, the button, the link and the typed command, such as `/resign` or `/pass`.

These are the keys inside a dialog.

- Y presses Yes or Accept, and N presses No or Decline.
- Left, Right and Tab move between the buttons. Enter or Space presses the highlighted one.
- A dialog starts on the answer that keeps the game going, as the table above shows. So a stray Enter never ends a game.
- Esc cancels the dialog. In a confirmation, that means No, which is always the safe answer. In an offer, both answers send something to the other player, so Esc answers nothing. It puts the dialog away, the offer still waits, and the sentence line says "ann doubles to 4. Press Enter to answer." Enter brings the dialog back. An offer's turn clock keeps running while it waits.
- A pointer clicks a button. A frontend without a keyboard gives every dialog a Cancel or Back control that does what Esc does.

Doubling, offering a draw, offering to resign and exchanging don't ask first. The other player can decline each offer, and an exchange costs nothing you can't see.

### The last move

The last move is said in one sentence and marked on the board. The server writes the sentence. A player's name appears as the player wrote it, even at the start of a sentence, and your own moves start with "You".

| Game | The sentence | The mark on the board |
|---|---|---|
| Reversi | "ann placed a disc on d3 and flipped 2." | a frame around the disc |
| Five in a Row | "ann placed a stone on h8." | a mark on the stone |
| Dots and Boxes | "ann drew a1-b1." or "ann drew a1-b1 and closed a box." | the line in its own color |
| Mancala | "ann sowed pit 3." | a mark on the pit |
| Cannon Fire | "ann fired at angle 45 with power 60 and hit bob." or "... and missed." | the shell's trail and burst |
| Backgammon | "ann rolled 6 and 3 and moved 12-18 and 12-15." "ann doubled to 4." "bob accepted the double." | the points moved to |
| Checkers | "ann moved 24-19." or "ann jumped 15x22x31." | the square moved to, and the squares jumped |
| Grid Words | "ann played DOZE down from h7 for 19 points." "ann passed." "ann exchanged 3 tiles." | the word's tiles in their own color |
| Naval Combat | "ann fired at e10 and hit your 2-square ship." "ann fired at e10 and missed." "ann sank your 2-square ship." | the square fired at |
| Rummy Tile | "ann played 3 tiles." "ann ended the turn and took a tile." | the sets changed this turn |

### Stacks of pieces

When a place holds more pieces than the screen draws, as a Backgammon point or the bar can, the last piece drawn shows a number. The number is how many pieces that one drawn piece stands for: itself and the ones hidden under it. So a point of five checkers drawn as four shows 2 on the top checker, and a stack that fits on the screen shows no number at all. The number is plain, such as 2, never +2, and it never counts the whole point and is never 1. The number sits on the piece itself, cut out of it or drawn in a contrasting color, not beside it. A frontend that can only fit one digit shows + for 10 or more.

### Time

| What | How it reads |
|---|---|
| the time signed in | "0m" in the first minute, then "42m", then "1h 3m" from an hour on. The time left follows it, so the clock reads "12m onl 63m rem" on a connection with a time limit and "12m onl unl rem" on one without. The time left is in whole minutes, from `TIM`'s `left=` (protocol.md, Time allowance). The clock starts when sign-in completes, and no screen shows it before then. |
| your clock | "You have 4:12 left." It shows in red, or in bold on a black and white screen, under a minute. |
| another player's clock | "ann has 2:03 left." |
| a clock from an hour up | "You have 1h 3m left." |
| a clock from a day up | "You have 1d 2h left." |
| a table without a running clock | "Each turn has 5 minutes." |

A frontend that can't redraw once a second may show whole minutes above the last minute, as in "You have 4m left." Under a minute it always shows seconds, as in "You have 0:42 left."

### Help

Every game's help has two pages, with the same headings on every frontend. The title of a page reads "Grid Words help, page 1 of 2".

- **Page 1 explains how to play here.** It has four parts. "Moving" comes first, with at most four lines in the order the move happens. "Keys for Grid Words" comes next, with one sentence for each shortcut. A frontend of buttons calls it "Buttons for Grid Words". "Commands" follows, with the typed move and the game's commands. "Every game" comes last, and it says the same thing in every game. On telnet it reads "R resigns, after asking first. T or Tab opens the chat. Esc goes back to the lobby, and the game goes on without you."
- **Page 2 is the reference.** It holds the facts you look up, such as the letter values, the notation, the rules in use, ship sizes and what the marks on the board mean. Its last line names the commands that work at every table, and the tutorial where the frontend has one.

Space or the Right arrow turns to the next page, the Left arrow turns back, and Esc or Enter closes help. The foot of each page says how, as in "Press Space for page 2, or Esc to close." The lobby's help has one page, which explains the lobby's keys and commands.

The server keeps the text of both pages with each game. Page 2 is the same on every frontend. Page 1's lines are the same sentences with each frontend's keys or buttons put in.

### Words with fixed meanings

| Word | What it means on screen |
|---|---|
| turn | the time when it's your go |
| move | what you do in a turn, whether you place, sow, play, fire or step |
| step | one part of a turn that has several, such as one checker moved in Backgammon |
| pick | taking a piece or tile up, before you put it down |
| place | putting down what you picked, or putting a new piece on the board |
| shortcut | a key that does something in game mode |
| command | anything typed after a slash |
| command line | the line where you type a command |
| dialog | the box that asks a question or shows an offer |
| draw | a drawn game, and never taking a tile |
| leave | going back to the lobby while the game goes on |
| sign out | ending your session, while your games go on; it also ends Stay signed in on this computer |
| Stay signed in | the setting that lets this computer sign in again without the password |
| lobby | the list of your games, your invitations and the open tables |
| log | the list of what happened at the table, under the chat |
| clock | the time you have left for this turn |
| the bot | ParlorNet's computer player |
| tutorial | the Tic Tac Toe lesson, which is never played on the parlor |

## The program flow

Every frontend takes a player through the same ten steps. Each step below says what the screen says and what the player can do. The examples use telnet's keys, and the mappings at the end say how each frontend carries them.

### 1. Connecting

The frontend reaches the parlor. It says what it is doing in a sentence, as in "Connecting to the parlor." When it can't connect, it says why, as in "The parlor didn't answer." and offers to try again.

| What you can do | How |
|---|---|
| connect | Enter, or the frontend's Connect step |
| choose the terminal type or the line's settings | where the frontend has them |
| run the tutorial | on the 8-bit program, before connecting |

### 2. Signing in or signing up

The parlor asks its questions one at a time, and the frontend shows each one as the parlor words it. A new name starts a sign-up. A password is never shown on screen. Esc goes back to the step before.

**Stay signed in.** A frontend that can keep a little data of its own offers the setting Stay signed in, with that label. On the 8-bit program it is in the settings, and on the web pages and in Play it is a checkbox on the sign-in form. With it on, the first successful sign-in with the password gets a sign-in key for this computer (`KEY NEW`), and nothing more is asked. The frontend keeps the key and never the password. Next time it signs in with the name and the key (`AUT key`), and the player goes straight to the lobby. When the parlor refuses the key (`ERR NOKEY`), the frontend forgets it and asks for the password as usual, in the parlor's words. Turning the setting off forgets the key (`KEY DEL`). A frontend that cannot keep anything does not show the setting. Telnet is one, because a telnet program has nowhere to keep a key. The player's profile lists the computers where they stay signed in, with each one's label and when it was last used, and lets them forget any of them.

At the first sign-in on telnet's full screen or the 8-bit program, a player who has never seen the tutorial gets the tutorial dialog. It asks whether the player wants the lesson, and it says that `/tutorial` runs it again any time. The answer is kept with the player either way, so the dialog never comes back.

### 3. The lobby

The lobby lists, in this order, your games with the ones where it's your turn first, your invitations, the open tables waiting for a player, and the tutorial. Its sentence says what matters most, as in "It's your turn at table 12." or "You have no games yet. Press N to open a table."

| What you can do | How |
|---|---|
| go back to a game | Enter on it |
| answer an invitation | Enter on it, which opens the invitation dialog |
| join an open table | Enter on it |
| watch a game | Enter on a game you don't play in |
| open a table | N, or the New table button |
| run the tutorial | Enter on it, or `/tutorial` |
| run a command | `/`, as in `/who` or `/top` |
| see the keys | `?` |
| sign out | `/quit`, or the Sign out button, which opens the sign-out dialog |

In the lobby, a letter means what it means at a table, or nothing. N opens a table, because no game uses N. Esc does nothing in the lobby, since there is nothing to cancel and nowhere further back. The lobby doesn't mention the bot. The bot's hint belongs at your own waiting table.

### 4. Opening or joining a table

Opening a table asks which game, then the rules and the number of players where the game has a choice, then the time per turn, and then whom to play. The choices are "the bot", "anyone who joins" and "a player you name". The last question shows what will happen, as in "Grid Words against the bot." Enter opens the table, and Esc goes back one question.

Joining is Enter on an open table in the lobby. You sit down at once, and the game starts when every seat is taken.

### 5. Waiting for an opponent

At your own table, before anyone sits down with you, the board area shows the game's empty board. The sentence line says "Waiting for a second player." On telnet and the 8-bit program the second sentence reads "Type /invite bot to play the bot, or /invite and a name to invite a player." On Play, the web and GameMaster the panel shows the buttons "Invite the bot" and "Invite a player". This is the only place the bot's hint appears.

| What you can do | How |
|---|---|
| invite the bot | `/invite bot`, or Invite the bot |
| invite a player | `/invite NAME`, or Invite a player |
| withdraw an invitation | `/uninvite NAME` |
| chat | T |
| close the table | `/close` |
| go back to the lobby | Esc, and the table stays open |

### 6. Playing a turn

When your turn comes, the sentence line says "Your turn." and what Enter does. The panel shows the other player's last move in words, and the board marks it.

You point at a place and choose it. A move with one step, such as a disc in Reversi, takes one Enter. A move that picks something up, such as a checker in Backgammon, takes an Enter to pick it and an Enter to put it down, and Esc puts it back. A turn made of steps ends with Enter once every step is played, or with E. U takes back a step.

Shortcuts do the game's other actions, and the sentence line names the ones that apply. A key that ends your turn or the game opens its dialog first. A refused move shows its reason on the sentence line until the next key.

While another player moves, the sentence line says "Waiting for ann. Press T to chat, or ? for help." You can look around the board, chat, read help, resign or leave.

### 7. Offers

An offer always arrives as a dialog. When ann doubles, your screen shows the double dialog, starting on Accept. You answer with Accept or Decline inside it. Esc puts it away unanswered, the sentence line says "ann doubles to 4. Press Enter to answer.", and Enter brings it back.

Making an offer is a shortcut, a button or a command, such as D or `/double`. The sentence line then says "You offered to double to 4. Waiting for ann." When the answer comes, the panel's last move says it, as in "ann accepted the double."

### 8. The end of the game

The sentence line says "Game over. ann won by 12 points." The last move stays on the board and in the panel. The chat stays open, so players can say "good game". Esc or Lobby goes back to the lobby, where the finished game no longer appears among your games.

### 9. Leaving

Esc with nothing picked, the Lobby button or `/leave` takes you back to the lobby. The game goes on, your turn clock keeps running, and leaving never resigns. You come back with Enter on the game in the lobby. A frontend never asks before leaving, because leaving loses nothing.

### 10. Signing out

`/quit`, the Sign out button or the 8-bit program's OA-Q opens the sign-out dialog, "Sign out? Your games go on without you." Yes signs you out. Your games wait for you at their tables. Signing out always forgets this computer's sign-in key, on the computer and at the parlor, whatever the Stay signed in setting, so the next sign-in asks for the password. With the setting still on, that sign-in gets a new key. Leaving the program without signing out, by switching the computer off or closing the window, keeps the key.

## Each game's actions

| Game | The cursor sits on | Enter | The game's shortcuts | Offers | A typed move |
|---|---|---|---|---|---|
| Reversi | squares | places a disc | none | none | `/d3` |
| Five in a Row | points | places a stone | none | none | `/h8` |
| Dots and Boxes | lines | draws the line | none | none | `/a1-b1` |
| Mancala | your pits, which 1 to 6 also choose | sows the pit | none | none | `/3` |
| Cannon Fire | the aim | fires | `<` `>` `+` `-` aim by 10, `[` `]` drive | none | `/fire 45 60` |
| Backgammon | points, the bar and the tray | rolls, then picks up a checker, then moves it, then ends the turn | D double, H home board, U undo, E end the turn | a double, an offer to resign | `/13-9` |
| Checkers | dark squares | picks up a piece, then moves it, then ends the turn | D draw, U undo, E end the turn | a draw | `/11-15`, `/15x22` |
| Grid Words | empty squares | starts entry mode where your word starts, then plays the word | P pass, X exchange, V letter values | none | `/h8 across QUIZ` |
| Naval Combat | your grid, then the other grid | picks where a ship starts and places it, then fires | G random, U undo, Z reset | none | `/a1 across`, `/e5` |
| Rummy Tile | the sets, the tiles and your rack | picks tiles, then puts them on a set | M new set, B split, U undo, Z reset, C check, E end the turn, P pass, O sort | none | `/R7-3` |

**Grid Words has an entry mode.** Enter on an empty square starts it, and an arrow on the board shows where the next tile goes. Right or Down set the direction before the first tile. Letters then lay tiles from your rack, Backspace takes the last one back, and Enter plays the word. A blank is typed as `_` and then the letter it stands for. Esc leaves entry mode and puts the tiles back on your rack.

**An exchange is an entry mode too.** X starts it. On telnet it opens the command line with `/exchange ` typed. On a frontend with a pointer, you mark the tiles to put back and press Exchange. Esc cancels it.

**Naval Combat places a ship with a pick and a direction.** Enter picks the square where the ship starts. Right or Down set its direction, with a preview, and Enter places it. A frontend with a pointer may take a click on the other end instead.

**Page 2 of each game's help** holds the board's notation, the marks on the board and the rules in use. It also covers Grid Words' letter values and premium squares, Backgammon's numbering, cube and bearing off, Checkers' compulsory captures and kings, Naval Combat's ship sizes and salvos, Cannon Fire's angles, power and wind, and Rummy Tile's step forms, set marks and first meld.

## The tutorial

The tutorial is a lesson in Tic Tac Toe that teaches the keys, the command line, the chat, help and dialogs. It runs only on the player's own screen. It never sends a move to the parlor and never makes a table. Telnet and the 8-bit program have it, because their keys are the one thing a new player can't guess. Play, the web pages and GameMaster don't, because every action there is a labeled button or link.

It uses the same screen as a real game. The title reads "Tic Tac Toe tutorial", and a line says "This tutorial is not played on the parlor. Nothing you do here reaches it." The opponent is the "tutorial partner", whose moves are scripted so that every player sees the same game. A Lesson box says "Step 2 of 18." and what the step teaches. Each step puts one instruction on the sentence line, and the next step comes only when the player does that thing. Any other key gets the same instruction again, starting with "Not yet." Every key still works as in a real game. Commands that need the parlor answer "This is the tutorial, so that command does nothing here."

It starts from the lobby's list, from `/tutorial`, from the first-sign-in dialog and from the last line of every game's help page 2. `/tutorial` is a command the client handles itself, and it never contacts the parlor. The tutorial's last step says that `/tutorial` runs it again. On the 8-bit program it also starts from the start screen, before connecting.

The steps are below in telnet's keys. The 8-bit program names its own keys in each step, such as Return, OA-U and RUN/STOP, and each of its steps also takes the button or the click that does the same thing.

| Step | The instruction | What the player does | What it teaches |
|---|---|---|---|
| 1 | "This tutorial shows how every game works. Press Enter to begin." | presses Enter | Enter |
| 2 | "Press the arrows to move to the middle square." | moves to b2 | moving |
| 3 | "Press Enter to place your X." | places X on b2, and the partner places O on a1 | Enter plays, and the panel shows the last move |
| 4 | "Your partner placed an O on a1. Move to c1 and press Enter." | places X on c1 | the turn and the last move in words |
| 5 | "Press U to take back that X. Lit keys on the bottom line are shortcuts." | presses U | a shortcut |
| 6 | "Your X is gone. Press Enter on c1 to place it again." | places X on c1, and the partner places O on a3 | undo |
| 7 | "Press / to open the command line." | presses `/` | command mode |
| 8 | "Type time and press Enter. The lit keys went dark because you are typing." | runs `/time` | running a command |
| 9 | "Press / again, type anything, then press Esc to clear it." | clears the line with Esc | Esc cancels |
| 10 | "You can type a move too. Type /c3 and press Enter." | places X on c3, and the partner places O on b1 | a typed move |
| 11 | "Press T to open the chat." | presses T | the chat screen |
| 12 | "Type hello and press Enter to say it." | says hello, and the partner answers | chat without `/say` |
| 13 | "Press Esc to go back to the board." | presses Esc | leaving the chat screen |
| 14 | "You can chat from the board too. Type /say good game and press Enter." | runs `/say good game` | `/say` |
| 15 | "Press ? to open help. Space turns the page, and Esc closes it." | opens help and closes it | help |
| 16 | "Press R to see how a game asks before it ends something. Then press Esc to answer No." | opens the resign dialog and cancels it | dialogs, and Esc in a dialog |
| 17 | "Move to c2 and press Enter to finish your line." | places X on c2 and wins | the end of a game |
| 18 | "You won. Press Esc to go back to the lobby. /tutorial runs it again any time." | presses Esc | Esc with nothing to cancel |

## Commands the client handles itself

A few `/` commands belong to the client. The client runs them on its own and never sends them to the server. Every other `/` line, a typed move included, goes to the server with the `CMD` request ([What the server provides](#what-the-server-provides)), and the server answers it in the shared words.

**A client-side command must never be sent to the server.** It is not a command line for `CMD`, it is not chat and it is not a request of any other kind. These commands belong to the client's own screens, so they mean nothing to the server. Some of them open a screen of the client's that then asks the server for what it shows, as the lobby's `/password` does with its own requests. `/tutorial` goes further, because it never contacts the parlor at all.

Every full-screen client handles at least these two commands itself. Today those clients are telnet's full screen and the 8-bit program.

| Command | What it does |
|---|---|
| `/tutorial` | It starts the tutorial, which runs entirely on the client and never reaches the parlor. |
| `/help` | It opens help for where you are, as `?` does. |

A client may add commands of its own, such as one that redraws the screen or one that goes back to the lobby. Each kind of frontend lists its client-side commands in its own section below. A client keeps the list short, because a command that the server runs reads the same on every frontend, and a client's own command does not.

## How each kind of frontend carries it

### Full-screen text (telnet)

- The screen has 24 rows. Row 0 is the title bar, with the game, the table and who you play with, as "Grid Words, table 12, with ann", and the time signed in at the right. Rows 1 to 19 hold the board and the panel. Row 20 belongs to the game. Row 21 is the sentence line, row 22 is the command line and row 23 is the keys line.
- The keys line lists the slots in the shared order, as "Arrows move  Enter pick  P pass  X exchange  ? help  / type  Esc lobby", with the key names lit. In command mode nothing is lit.
- A player may choose WASD, ESDF, IJKL or HJKL for moving. A movement letter always moves, and a shortcut on the same letter shows as its command instead, as `/double`.
- T or Tab opens the chat screen, where every line is said without `/say`, and a line that starts with `/` is a command.
- A dialog is a box over the board with its two buttons.
- Plain mode, for terminals without cursor control, works line by line. A line that starts with `/` is a command or a move, and any other line is chat.
- Telnet handles these commands itself. At a table they are `/tutorial`, `/help`, `/redraw`, `/leave` and a game's few commands for its screen, such as `/sort` in Rummy Tile and `/home` in Backgammon. In the lobby they are `/tutorial`, `/help`, `/list`, `/resume`, `/watch`, `/password`, `/machine`, `/activity` and `/keys`, and `/open` with nothing after it, which asks the questions N asks. Plain mode has no tutorial, so there `/tutorial` says that the tutorial needs the full screen.

### 8-bit Hi-Res (the Apple IIe, IIgs and C64 program)

- The screen is 40 columns. The title row holds the game, the table, the players and the time signed in. Under the board come the message line, the status line and a row of buttons. The status line holds the first sentence and the clock, as "Your turn. You have 4m left." The message line holds the second sentence, refusals, the other player's last move as it arrives, and chat as it comes.
- The arrows hop the pointer from target to target. Return or Space clicks the target under the pointer, so the message line says what Return does there.
- Shortcuts take OA, which is Open-Apple on the Apple II and the Commodore key on the C64. Plain letters type only in Grid Words' entry mode and on the command line.
- `?` opens help, and on both machines `?` is Shift and the `/` key. Help is a full page drawn over the board. The program fetches the pages from the parlor.
- Esc is the Esc key on the Apple II. On the C64 it is RUN/STOP or the left-arrow key, and the screen names RUN/STOP.
- `/` opens the command line on the message line, with the slash typed. OA-T opens the chat screen, and on the Apple II Tab does too.
- A dialog is a box drawn over the board. Y and N answer it without OA, and Esc cancels it.
- The buttons along the bottom are Chat, Help, Resign and Lobby. The display choice lives on the start screen and the lobby's Display button. The line console opens with OA-C from the lobby, since the console belongs to the connection and not to a game.
- **The joystick and the mouse.** A joystick hops the pointer as the arrows do, and a mouse moves it freely. The first button clicks, exactly as Return does. That is the fire button, button 0 of an Apple joystick and the left mouse button. The second button cancels, as Esc does, but it never leaves the table. That is button 1 of an Apple joystick and the 1351 mouse's right button. On the Apple II, the joystick's buttons are the same switches as Open-Apple and Closed-Apple, so the joystick is turned on in the settings. While it is on, a press of button 0 with no key during it is a click, and a key pressed while it is held is an OA shortcut.
- Every action has a button on screen, so a joystick or a mouse alone can play every game. Grid Words can be played by clicking the rack's tiles.
- The tutorial is an overlay loaded from the disk, so none of it stays in memory during a real game.
- The program handles `/tutorial`, `/help` and `/leave` itself, because they open its own screens. Every other `/` line goes to the server.
- **Stay signed in** is one of the settings, beside the line's. With it on, the program keeps the name and the sign-in key with its settings, where they last across a power-off (from ProDOS on the Apple II), and signs in with them when it connects. Signing out with OA-Q forgets the key there. A disk image made to share is made signed out, so it carries no key.

### The browser (ParlorNet Play)

- The board fills the left, and the cards on the right hold the players with their clocks, the game's panel with its buttons, the notes and the chat. The sentence line sits over the board. The header shows the time signed in after "Signed in as eric".
- Touch, a mouse and the keyboard all work. A tap or click does what Enter does on the place you touch, and dragging moves pieces where a game allows it.
- The letters work while the board has the keyboard. Tab moves between controls as it does in every browser. T moves the keyboard to the chat box, and `/` does the same with the slash typed. The chat box takes chat lines and `/` commands.
- `?` opens a help dialog with the two pages. Esc cancels, and with nothing to cancel it goes back to the lobby. In full screen, the browser takes the first Esc to leave full screen. Full screen is the header's button, and no letter switches it.
- Every button names its key, as "Roll (Enter)", "Undo (U)" and "New set (M)".
- A dialog is a modal box. Its safe button has the focus, and Esc cancels it.
- Refusals show in a short notice at the foot of the board, in the server's words.
- Play handles `/help` itself, and it opens the help dialog. Play has no tutorial, because every action there is a labeled button.
- The sign-in form has a Stay signed in checkbox. The key goes into Play's own cookie by a one-time handle, so the page never holds it.

### HTML 3.2 forms (the web pages)

- Every action is a link or a form button, in the shared order, with the shared labels. An action shows only while it applies.
- The heading names the game, the table and the players, as "Grid Words, table 12, with ann". The panel lists the players, the turn, the clock, the last move, the facts and the log.
- The board is an image map. A click does what Enter does there.
- A dialog is a page of its own, with the question and two links such as "Yes | No". A Cancel link wherever something is picked does what Esc does, and a Back link answers nothing.
- A "Say" form takes chat lines and `/` commands. Each game also has one form for a typed move, with the prompt "Or type a move (like d3):" and a "Play" button.
- A Help link opens the game's help page, which shows both pages one after the other.
- The bar shows how long you have been signed in, as "Signed in 42m".
- A refusal shows at the top of the table page, above the board.
- The page reloads on a timer, because HTML 3.2 can't be told that something changed.
- The web pages handle no command themselves. Every line of the Say form that starts with `/` goes to the server, and help is a link.
- The sign-in form has a Stay signed in checkbox, and the key goes into a cookie of its own. Your profile lists the computers where you stay signed in, each with a Forget link.

### GameMaster's rulebooks

- GameMaster owns the window, the menu bar and its commands, its own words while a game connects, and the limit of two players. A rulebook owns everything inside its play area.
- The panel holds the sentence line, in the button form, with the players' names on a parlor. The last move is said in words in the panel and marked on the board.
- The game is played with the mouse, which is how a Mac of 1991 plays. Where a rulebook reads keys, it uses the shared letters, and Return does the main step.
- A pick is put back by clicking it again, because the Mac Plus keyboard has no Esc key.
- A dialog is a modal box drawn by the rulebook, with its two buttons. The safe button is the default, which Return presses. Command-period and the Cancel button do what Esc does.
- Help is a box with the two pages, opened by a Help button in the panel.
- GameMaster has no console, so there is no command line and no client-side command. The ParlorNet window's controls stand for the lobby's commands.
- Closing a game window leaves the game at its table, as leaving does on every frontend. GameMaster's own Othello, Dots&Boxes and BattleShips rulebooks can't take a game up again, so the window says before closing that the game can be finished on the web or by telnet.

## What the server provides

Every frontend reaches the server through a protocol. ParlorNet's own frontends use PARLORNET/1 ([protocol.md](protocol.md)), directly or through a server extension. These requests carry the design.

| Request | What it carries |
|---|---|
| `HELO`, `USER`, `REG`, `AUT`, `CAN` | signing in and signing up, one question at a time |
| `LST`, `IVL` | the lobby's games, open tables and invitations |
| `NEW`, `JON`, `INV`, `UNV`, `SEE`, `LVE` | opening, joining, inviting, watching and leaving |
| `VUE` | the board, whose turn it is (`W`), the clock (`CLK`), the legal moves (`M`) and the status text (`S`) |
| `MOV`, `CHK`, `UND`, `END`, `RES` | playing, checking, undoing, ending a turn and resigning |
| `SIN`, `WAT` | what happened since you last looked, including chat |
| `CHT` | a line said at the table |
| `CMD` | a command line typed after the slash, at a table or in the lobby, run on the server with the lines to show, and the confirmation dialog for the commands that ask first |
| `NOTE` | the table's notes |
| `TIM` | the time signed in and the time left, for frontends without a clock |
| `PRF` | small settings kept with the player, such as the display choice and the answer to the tutorial dialog |
| `KEY` | Stay signed in, with a sign-in key for this computer (`KEY NEW`), the player's list of keys (`KEY LST`) and forgetting them (`KEY DEL`). `AUT key` signs in with one. |
| `PAG`, `CNT` | long replies in pages of a size the frontend chooses, so a frontend with little memory reads one page at a time |
| `OUT`, `BYE` | signing out, which forgets this computer's sign-in key, and closing |

The server runs every command in one place, with `CMD` ([protocol.md](protocol.md), Commands). It has the names of [chat-commands.md](chat-commands.md), and a typed move goes to the game's own reading of it. Telnet's command line, Play's chat box, the web's Say form and the 8-bit command line all send their slash lines this way. A command that ends a turn or a game, such as `/resign` or `/pass`, answers with the dialog to open, and the dialog's Yes sends the confirmed form. No command answers an offer. The commands the client handles itself never take this path ([Commands the client handles itself](#commands-the-client-handles-itself)).

The design also asks the server for these, which are decided and not yet in the protocol.

- **The shared first sentence.** The view's status text becomes the sentence line's first sentence, in the shared words and with the players' names. Today each game writes its own status, with seat numbers and protocol verbs.
- **The last-move sentence.** The server writes the sentence for each move from the move's events, and a frontend reads it with the events or the view.
- **The actions open now.** A view line lists the actions that apply now, with the verb phrase for each, so that a frontend can build the second sentence and its buttons without knowing each game's rules.
- **Offers.** A view line says that an offer waits for your answer, with its dialog's words. Today each game shows its offers in its own view lines ([types/](types/)).
- **Refusals in the player's terms.** Each game's `ERR ILLEGAL` reason is a sentence for the player, as in "You can't play d3. It flips no discs."
- **Help pages.** A request returns a game's two help pages, with the keys left as slots for each frontend to fill.

The tutorial needs nothing from the server, because it never reaches it.
