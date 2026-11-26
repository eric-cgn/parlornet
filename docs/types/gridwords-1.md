# gridwords/1

Grid Words is a crossword-building tile game for 2-4 players, each with a hidden rack. It has the flag `nomoves` (there is no legal-move list; a client builds a play and may preview it with `CHK`) and single-step moves (no `staged`). The rulesets are `english` and `classic`. It has no table options; the table's seat count is the core's `players=2|3|4` on `NEW` (default 2), and the time per turn is the core's `turn=`.

## Rules (`english`)

- 100 tiles: A 9, B 2, C 2, D 4, E 12, F 2, G 3, H 2, I 9, J 1, K 1, L 4, M 2, N 6, O 8, P 2, Q 1, R 6, S 4, T 6, U 4, V 2, W 2, X 1, Y 2, Z 1, and 2 blanks. Points: A E I L N O R S T U 1; D G 2; B C M P 3; F H V W Y 4; K 5; J X 8; Q Z 10; a blank 0.
- A 15 by 15 board with the usual premium squares: 8 triple word, 17 double word (the center among them), 12 triple letter, 24 double letter.
- Each player draws 7 tiles. Seat 0 moves first, then each seat in turn.
- A play puts one or more tiles from the rack in one row or one column, so that together with the tiles already there they make one unbroken word (the main word: it must take in any tiles touching its two ends). The first play covers the center square and has two letters or more; every later play touches a tile already on the board.
- Every word of two letters or more that the play makes is scored: the main word, and the word across the main word's line through each new tile. Letter premiums multiply the points of the new tile on them; word premiums multiply the whole word they are in. Premiums count only on the turn a tile is placed on them. A blank scores nothing. Placing all seven tiles of the rack in one play earns 50 more.
- Every word made must be in the server's word list, or the play is refused (`ERR ILLEGAL not in the word list: ...`). There is no challenge round.
- After a play the player draws back up to seven tiles while the bag has any.
- Instead of playing, a player may exchange 1 to 7 tiles (only while the bag holds at least 7) or pass.
- The game ends when a player plays their last tile with the bag empty, or after two full rounds (twice the number of players, turns in a row) of passes and exchanges. Each player then loses the points still on their rack; a player who went out gains the total of the others' racks. The highest score wins; a shared top score is a draw.

## Rules (`classic`)

The same board, tiles, scoring and word list as `english`, with the ending used in club and tournament play: six turns in a row without a word played (passes and exchanges, by any players) end the game, however many players there are. Everything else, including the rack penalties at the end, is as in `english`. The DOoM extension (docs/protocols.md) makes its tables with this ruleset, because the crossword-game servers of the 1990s ended games this way. The plugin keeps each ruleset's differences in one table (`RULESETS` in plugins/gridwords/plugin.py), so a later ruleset for another period program can be added there.

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

About 680 bytes, sent once and cached by the client.

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

- Web: an HTML 3.2 board of 24 by 24 GIF squares (premium squares named in their images and alt texts), the player's rack, and forms with three fields (the start square like `h8`, a select for across or down, and the letters): click the empty start square to fill them in (again: down), type the letters from the rack and press Play, or press Check score, which sends `CHK` (`/check.htm`) and shows the server's answer, the points and words or the reason the play is refused, with a link that plays it. Only when the server gives no answer about the play does the page show its own estimate, worked out from the view as the server would but without looking the words up. Exchange and Pass. The board is drawn in the player's colors (docs/design/gridwords-colours.md), chosen on the Grid Words colors page.
- ParlorNet Play (docs/modern-client.md): tiles dragged from the rack to the board, or typed on it; the score of the tiles laid is asked with `CHK` as they are laid.
- Telnet: the full board on the left in every glyph mode; arrows move a cursor to the next empty square, Enter sets the start square (again: down), typed letters show on the board and Enter plays them. Typed commands: `i4 a XY`, `i4 d XY` (the rack's letters from i4), `play 8 8 h WORD` (the whole word, as the protocol takes it), `check ...` (sends `CHK`), `pass`, `exchange ABC`, `colors` (or `colours`: the board's colors, kept for the next visit). The rack shows only the letters, and the help's second page lists every letter's points. On your turn, with no word started, a line under the board says how to pass or exchange. An exchange with fewer than seven tiles in the bag is refused before it is sent, with the reason. The plain mode prints the board and takes the same typed commands, and it says how to pass or exchange once a turn.

## Open

- Recorded games as conformance vectors.
