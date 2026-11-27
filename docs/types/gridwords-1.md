# gridwords/1

Grid Words is a crossword-building tile game for 2-4 players, each with a hidden rack. It has the flag `nomoves` (there is no legal-move list; a client builds a play and may preview it with `CHK`) and single-step moves (no `staged`). It has no table options; the table's seat count is the core's `players=2|3|4` on `NEW` (default 2), and the time per turn is the core's `turn=`.

## Rulesets

Each ruleset is a rules id of `gridwords/1`, the word every table has from `NEW type rules`. A ruleset sets what each letter is worth, how many of each letter the bag holds, where the bonus squares are, and how many turns in a row without a word played end the game. The board is always 15 by 15, the first word always covers the center square, racks always hold seven tiles, and the scoring is always as below.

The plugin's own ruleset is `standard`, and it is the default. An operator may add rulesets as files in the site directory (Ruleset files, below). `GAM` lists `standard` first and then the operator's rulesets by id. A table's static geometry (`BRD`, `CTR`, `PRM`, `TIL`) is its ruleset's, so a client that draws the board and the tiles from it shows every ruleset as it is.

## Rules (`standard`)

- The bag holds 100 tiles, two of them blanks. Each letter is worth the points below, and a blank is worth nothing.

| Letter | A | B | C | D | E | F | G | H | I | J | K | L | M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Points | 1 | 3 | 2 | 2 | 1 | 4 | 3 | 3 | 1 | 8 | 5 | 1 | 3 |
| In the bag | 9 | 2 | 3 | 4 | 11 | 2 | 3 | 3 | 8 | 1 | 1 | 4 | 2 |

| Letter | N | O | P | Q | R | S | T | U | V | W | X | Y | Z | blank |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Points | 1 | 1 | 3 | 10 | 1 | 1 | 1 | 2 | 5 | 4 | 7 | 3 | 10 | 0 |
| In the bag | 6 | 8 | 2 | 1 | 6 | 4 | 7 | 3 | 2 | 2 | 1 | 2 | 1 | 2 |

- The board is 15 by 15, with 8 triple word squares, 13 double word squares (the center among them), 16 triple letter squares and 24 double letter squares. The layout looks the same when it is mirrored across either middle line or either diagonal. In the grid, `T` is a triple word square, `D` a double word square, `t` a triple letter square, `d` a double letter square and `.` a plain square. The center, h8, is a double word square.

```text
      a b c d e f g h i j k l m n o
   1  T . . d . . . T . . . d . . T
   2  . D . . . t . . . t . . . D .
   3  . . D . . . d . d . . . D . .
   4  d . . D . . . t . . . D . . d
   5  . . . . t . . . . . t . . . .
   6  . t . . . d . . . d . . . t .
   7  . . d . . . d . d . . . d . .
   8  T . . t . . . D . . . t . . T
   9  . . d . . . d . d . . . d . .
  10  . t . . . d . . . d . . . t .
  11  . . . . t . . . . . t . . . .
  12  d . . D . . . t . . . D . . d
  13  . . D . . . d . d . . . D . .
  14  . D . . . t . . . t . . . D .
  15  T . . d . . . T . . . d . . T
```

- Each player draws 7 tiles. Seat 0 moves first, then each seat in turn.
- A play puts one or more tiles from the rack in one row or one column, so that together with the tiles already there they make one unbroken word (the main word: it must take in any tiles touching its two ends). The first play covers the center square and has two letters or more; every later play touches a tile already on the board.
- Every word of two letters or more that the play makes is scored: the main word, and the word across the main word's line through each new tile. Letter premiums multiply the points of the new tile on them; word premiums multiply the whole word they are in. Premiums count only on the turn a tile is placed on them. A blank scores nothing. Placing all seven tiles of the rack in one play earns 50 more.
- Every word made must be in the server's word list, or the play is refused (`ERR ILLEGAL not in the word list: ...`). There is no challenge round.
- After a play the player draws back up to seven tiles while the bag has any.
- Instead of playing, a player may exchange 1 to 7 tiles (only while the bag holds at least 7) or pass.
- The game ends when a player plays their last tile with the bag empty, or after two full rounds (twice the number of players, turns in a row) of passes and exchanges. Each player then loses the points still on their rack; a player who went out gains the total of the others' racks. The highest score wins; a shared top score is a draw.

The rules id and its data are kept in `plugins/gridwords/rules/standard.json`, in the format of a ruleset file.

## Ruleset files

An operator adds a ruleset with a file `rules/gridwords/<id>.json` in the site directory, the directory that `install.sh` takes as `PARLOR_SITE_DIR` and mounts at `/run/parlornet/site` (deploy/README.md, A site of your own). The plugin reads the directory named by `PARLOR_SITE`, else `/run/parlornet/site`, once, when it starts. The file name gives the rules id, which is a lower-case letter and then up to 11 lower-case letters or digits, such as `club.json` for the rules id `club`. The id `standard` is the plugin's own and can't be taken. Players see the id with a capital letter, such as Club, wherever they pick rules.

A ruleset file is one JSON object with these keys:

| Key | Meaning |
|---|---|
| `tiles` | Required. An object with a key for every letter `A` to `Z` and `_` for the blank. Each value is `[points, count]`, two whole numbers. A letter is worth 0 to 10 points, and the blank is worth 0. The bag holds 60 to 200 tiles in all. |
| `board` | Required. 15 strings of 15 characters, row 1 first and column a first in each. A character is `T` (triple word), `D` (double word), `t` (triple letter), `d` (double letter) or `.` (a plain square). The center, h8, is the start square whatever its character says. |
| `symmetric` | Optional, `true` or `false` (the default). When it is `true`, the plugin checks that every bonus square has the same kind at its mirror images across both middle lines and both diagonals. |
| `idle` | Optional. The number of turns in a row without a word played (passes and exchanges, by any players) that ends the game, 2 to 20. Without it, two full rounds end the game. |
| `about` | Optional. A sentence about the ruleset for the operator, in plain ASCII, up to 300 characters. |

A file with any other key, or with a value outside these limits, is left out, and so is a file that isn't JSON or is larger than 16 KB. The core's log then has a line such as `parlord: plugin gridwords: ruleset /run/parlornet/site/rules/gridwords/mine.json left out: The bag holds 239 tiles, and it must hold 60 to 200.` A ruleset that is read has the line `ruleset mine added from ...`. Restart the server after changing a file. A table keeps its rules id, so a game of a ruleset whose file has been removed can't go on until the file is back. Give a changed ruleset a new id when games of the old one are still being played.

A client that draws the board or scores plays with values of its own, rather than from the table's geometry, can play only the rulesets that match it. Its server extension lists the rules ids it plays and shows and makes only tables of those (docs/protocol.md, Game identity). On a server without such a ruleset, that client has no Grid Words. The Networdz extension plays only `standard`, because the `PARLOR.CFG` on its client page is standard's board and tiles. ParlorNet's own clients, the bot, the DOoM extension and GameMaster's Grid Words rulebook take the board and the values from the table, so they play every ruleset.

## A table marked integrity=false

A server extension whose program receives every rack and the bag, as the Networdz extension's does (docs/protocols.md, Networdz), marks the table `integrity=false` and reads them with `VUE table O`: a seated player's view then also has `RK seat letters` for every seat (`RK seat -` for an empty rack) and the bag in draw order, the next tile first, in `BO letters` lines of up to 60 letters (`BO -` for an empty bag), for that extension only. Watchers never get them, and neither does a player's own program. The game is played exactly as at any other table: the table deals, checks every word against its list and ends the game, and the other players' views are as usual.

## The word list

The plugin reads a text file, one word per line (2-15 letters A-Z, any case; other lines are skipped), named by the operator's environment setting `GRIDWORDS_WORDS`, else `plugins/gridwords/words.txt`. It is read once and again when the file changes. Without one, `NEW` fails with `ERR BADOPT this server has no word list for Grid Words yet`. The list and its license are the operator's choice; the type contract does not depend on which. (This is the one file the plugin reads.)

The server ships ENABLE 1.0 (1997, public domain) as `plugins/gridwords/words.txt`: 172,823 words, of which the plugin takes the 168,551 of 2-15 letters. `plugins/gridwords/SOURCES.md` records where it comes from and its checksums.

## Squares

Squares are 1-based: `x` across from the left, `y` down from the top, so the center is `8 8`. Frontends show columns as letters `a` to `o` and rows as numbers, so `8 8` is `h8` on the screen.

## Static geometry (flag `G`)

| Line | Meaning |
|---|---|
| `BRD w h` | Board size: `BRD 15 15`. |
| `CTR x y` | The square the first word covers: `CTR 8 8`. |
| `PRM kind x y x y ...` | Premium squares by kind: `TW` `DW` (triple and double word), `TL` `DL` (triple and double letter). Up to 10 squares a line; several lines per kind. The center is also listed as `DW`. |
| `TIL letter points count` | One line per letter, `_` for the blank: `TIL Q 10 1`, `TIL _ 0 2`. |

About 680 bytes, sent once and cached by the client for the table's ruleset. A table still waiting for players gives its geometry too when the view asks for it with `G`, and `GEO gridwords/1 rules` gives a ruleset's geometry without a table (docs/protocol.md).

## View lines

| Line | Meaning |
|---|---|
| `X x y c x y c ...` | Tiles on the board as triples, up to 9 a line, as many lines as needed (none on an empty board). `c` upper case is a tile; lower case is a blank standing for that letter. |
| `R letters` | Your rack, `_` for a blank; `R -` when it is empty. Only to a seated player, and only their own. |
| `K seat n` | Tiles on each seat's rack, one line per seat. |
| `BAG n` | Tiles left in the bag. |
| `T seat points` | Scores, one line per seat (common line). |
| `W seat` | Whose turn it is; `W -1` once the game is over. |
| `LAST seat x y H\|V word points` | The last word played (as on the board, blanks in lower case), when there is one. |

`S` (with flag `T`): `Your move`, `Waiting for another player`, `Seat n to move` (watchers) or `Game over: result` (as in `END`). With 60 tiles on the board a full view is about 600 bytes; with all 225 squares filled, about 1.9 KB. Every line is at most 78 bytes.

## Move tokens

Letters, the verb and the direction are case-insensitive.

- `PLAY x y H|V letters`: the whole main word, starting at x y, across (`H`) or down (`V`). Letters already on the board are written out and must match; the others come from the rack. `_` before a letter plays a blank as that letter: `PLAY 8 8 H HEL_LO` (the second L is a blank).
- `EXCH letters`: put those tiles back for as many new ones, `_` for a blank: `EXCH QV_`.
- `PASS`.

`M` is not provided (flag `nomoves`). `CHK table PLAY ...` answers `OK points word word ...` (the words as they would stand, blanks in lower case; cut to fit the line) or `ERR ILLEGAL reason`, the same reason `MOV` would give, including words not in the list. `CHK` works when it is not your turn too, so a player can plan while waiting. `CHK table EXCH ...` and `CHK table PASS` answer `OK 0` when they would be accepted.

## Events

| Event | Meaning |
|---|---|
| `PLAY seat x y H\|V word points` | A word played: the main word as it stands on the board, and the points scored for the whole play. |
| `X x y c ...` | The new tiles of that play, as triples (the event after `PLAY`). |
| `DRAW seat n` | The player drew n tiles; which ones only that player sees, in the next `VUE`. |
| `EXCH seat n` | The player exchanged n tiles. |
| `PASS seat` | The player passed. |
| `TURN seat` | Whose turn it is now. |
| `ADJ seat ±n letters` | At the end: the change to each seat's score and the tiles left on its rack (`-` for none: the player who went out, who gains the others' total). |
| `END win seat s0/s1/...` or `END draw s0/s1/...` | The final scores, after the `ADJ` changes. |

## Size

A play is four events of about 25, 40, 10 and 6 bytes (`PLAY`, `X`, `DRAW`, `TURN`), at most 80 bytes each with the `EVT` prefix. Static geometry about 680 bytes (once). Full view about 600 bytes mid-game.

## Frontends

On the web and telnet a player types only the letters from their own rack, starting on an empty square. The letters fill the empty squares in order, and the tiles already on the board between them, just before them and just after them join the word, so `x` typed at i4 beside HE on g4 and h4 plays HEX (`PLAY 7 4 H HEX`). A word cannot start on a played square, and the arrow keys and clicks on the board skip or refuse played squares. The DOoM service keeps DOoM's own rule, where the whole word is typed.

Every frontend draws the bonus squares and the tiles' points from the table's static geometry, so each table shows its own ruleset.

- Web: an HTML 3.2 board of 24 by 24 GIF squares (premium squares named in their images and alt texts, and each tile with its points at the table), the player's rack, and forms with three fields (the start square like `h8`, a select for across or down, and the letters): click the empty start square to fill them in (again: down), type the letters from the rack and press Play, or press Check score, which sends `CHK` (`/check.htm`) and shows the server's answer, the points and words or the reason the play is refused, with a link that plays it. Only when the server gives no answer about the play does the page show its own estimate, worked out from the view as the server would but without looking the words up. Exchange and Pass. The board is drawn in the player's colors (docs/design/gridwords-colours.md), chosen on the Grid Words colors page. The game's own page, `/games/gridwords.htm`, shows each ruleset the server has, with its letter values, its tiles and a picture of its board.
- ParlorNet Play (docs/modern-client.md): tiles dragged from the rack to the board, or typed on it; the score of the tiles laid is asked with `CHK` as they are laid.
- Telnet: the full board on the left in every glyph mode; arrows move a cursor to the next empty square, Enter sets the start square (again: down), typed letters show on the board and Enter plays them. Typed commands: `i4 a XY`, `i4 d XY` (the rack's letters from i4), `play 8 8 h WORD` (the whole word, as the protocol takes it), `check ...` (sends `CHK`), `pass`, `exchange ABC`, `colors` (or `colours`: the board's colors, kept for the next visit). The rack shows only the letters, and the help's second page lists every letter's points and the tiles the bag starts with. On your turn, with no word started, a line under the board says how to pass or exchange. An exchange with fewer than seven tiles in the bag is refused before it is sent, with the reason. The plain mode prints the board and takes the same typed commands, and it says how to pass or exchange once a turn.

## Open

- Recorded games as conformance vectors.
