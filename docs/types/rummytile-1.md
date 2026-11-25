# rummytile/1 (draft)

Rummy Tile Classic is tile rummy for 2-4 players (`NEW rummytile standard players=3`; two without it). Its flags are `staged`, `nomoves` and `drop`, its only ruleset is `standard`, and it has no options.

## Tiles

106 tiles: the numbers 1-13 in four colors, two of each, and two jokers. A tile is written as its color letter and number, or `J` for a joker:

| Letter | Color |
|---|---|
| `R` | red |
| `B` | blue |
| `K` | black |
| `Y` | yellow |
| `J` | joker |

`R7`, `B12`, `K1`, `Y13`, `J`. The two copies of a tile are interchangeable, and so are the two jokers. The classic pages and telnet show the letter as well as the color; ParlorNet Play draws the number in its color and names the tile in words for screen readers (docs/design/rummytile-colours.md).

## Rules (standard)

- Each player is dealt 14 tiles; the rest are the pool. Seat 0 plays first, then seat 1, and so on round the table.
- A **set** is a **run** (three or more tiles of one color with consecutive numbers, 1 to 13, no wrapping) or a **group** (three or four tiles of one number, each a different color). A joker stands for any tile; in a run its number is its place in the run.
- A turn is **staged**: any number of steps (move tokens below), each changing the table and the player's rack tentatively. The steps may take the table apart freely: split sets, join them, move tiles between them, make new sets. `END` ends the turn, and the turn counts only if, at that moment:
  1. every set on the table is valid,
  2. at least one tile came from the player's rack,
  3. before the player's **first meld**: the sets made this turn are worth at least 30 points (a joker counts as the tile it stands for), and
  4. the **joker rule** below holds.
- Otherwise `END` undoes the whole turn (the table and the rack go back to how they were when the turn began) and the player draws one tile from the pool. A player who plays nothing and sends `END` draws a tile. With the pool empty, the player passes instead.
- **First meld**: until a player's first meld is made, they may only build new sets from their own tiles; the sets already on the table cannot be touched (a step naming one of them is refused). Once the first meld is made, later turns may rearrange the whole table.
- **Jokers**: a joker on the table can be taken (after the first meld) by playing, in the same turn, the tile it stood for from the rack (for a joker in a group of three, either missing color; for a joker at the end of a run, the number at either end it could be). The joker must be played again in the same turn: tiles never go from the table to a rack, except tiles played from that rack this turn. Checked at `END`: every joker that was on the table when the turn began either still stands for a tile it could stand for then, or a tile it could stand for was played from the rack this turn (one played tile frees one joker).
- A set holds at most 13 tiles and the table at most 99 sets, even while a turn is staged.
- **The end**: the game ends when a player empties the rack (`OUT`), or when the pool is empty and every player in turn has passed. Each rack is worth the sum of its numbers, a joker 30. The lowest rack wins; every other player scores minus the difference between their rack and the lowest, and the winner scores the sum of those differences (several players tied for the lowest share it, rounded down, and the result is a draw).

## Static geometry (flag `G`)

`TIL RBKY 13 2 2`: the color letters, the highest number, the copies of each tile, the jokers.

## View lines

| Line | Meaning |
|---|---|
| `P n` | Tiles left in the pool. |
| `C n0 n1 [n2 n3]` | Tiles on each seat's rack; the number of fields is the number of seats. |
| `I f0 f1 [f2 f3]` | 1 when that seat has made its first meld, else 0. |
| `G g0 g1 [g2 g3]` | 1 for a seat that has left the game (see Leaving), else 0. |
| `W seat` | Whose turn it is (`W -` once the game is over). |
| `ST k kind tiles...` | Set k of the table (numbered from 1, in table order), its tiles in their fixed order. `kind`: `r` a valid run, `g` a valid group, `x` not a valid set; followed by `*` when the set is not one that lay on the table when the turn began (made or changed this turn). One line per set. |
| `H tiles...` | Your rack, sorted by color (R, B, K, Y) then number, jokers last; up to 16 tiles a line, as many lines as needed. Only in your own view: other players and watchers never see a rack. |
| `TP n points code` | While the game runs: the tiles the player on turn has played from the rack so far, the points of the sets made this turn (`-` once that player has made the first meld), and what `END` would do now: `ok` (the turn counts), `none` (nothing played: draw), `sets` (a set is not valid), `meld` (first meld under 30), `joker` (the joker rule fails); for every code but `ok`, `END` undoes the turn and draws. |
| `T seat points` | After the game: each seat's score (the common line). |

The order of the tiles in a set is fixed by the server, so a position means the same to every client: a run is in number order with each joker in the place it fills, spare jokers at the top end (at the bottom once the top reaches 13); a group, or a set that is not valid, is in color order (R, B, K, Y) by number, jokers last. Every change to a set puts it back in that order. Sets keep their numbers while a turn is staged, except that an emptied set is removed (the later ones move up) and a new set is added at the end.

## Move tokens

`M` is never sent (flag `nomoves`): there are too many possible steps. Each `MOV` is one step; several tokens on one `MOV` are applied in order as a single step (all or nothing; `UND` takes them back together). Tokens are case-insensitive. `k` and `d` are set numbers, `p` a position in a set (1 = first tile), `n` a new set (added at the end), `r` the rack.

| Token | Meaning |
|---|---|
| `R7-3` | Put rack tile R7 into set 3. |
| `R7B7K7-n` | Put rack tiles R7, B7 and K7 into a new set (up to 13 tiles in one token). |
| `3.2-5` | Move tile 2 of set 3 to set 5. `3.2-n`: to a new set. |
| `3.R7-5` | The same, naming the tile (the first R7 in set 3); `3.J-n` moves a joker. |
| `3.2-r` | Take tile 2 of set 3 back to the rack: only a tile played from the rack this turn. |
| `3-5` | Join set 3 onto set 5 (set 3 goes away). |
| `3/4` | Split set 3 before its tile 4: tiles 4 onwards become a new set at the end. |
| `reset` | Start the turn over: the table and the rack as they were when the turn began. `UND` takes the reset back. |

`MOV` answers `OK done` when `END` would count the turn now, `OK more` otherwise. `UND` takes back the last step. `END` ends the turn as described above (it never fails while it is your turn: an unready turn is undone and you draw). `CHK table !end` asks what `END` would do without doing it: `OK ok 3 tiles` (with ` meld 32` before the first meld) or `ERR ILLEGAL` and the reason (`set 4 is not valid`, `the first meld is 24 points; 30 needed`, ...). `CHK table token...` checks a step without playing it.

## Events

All events are seen by every player and watcher, so none of them names a tile from a rack that has not been played: a drawn tile is never named.

- `DEAL 14 pool`: the deal, tiles each and the pool left. Then `TURN 0`.
- `PUT seat tiles target`: rack tiles (written together, `R7B7K7`) put into set `target` (`n` a new set).
- `MOVE seat k tile target`: a table tile moved from set k to set `target` (or `n`).
- `BACK seat k tile`: a tile taken back from set k to the rack.
- `MERGE seat k d`: set k joined onto set d.
- `SPLIT seat k p`: set k split before its tile p.
- Each of these is followed by the exact change to the table: `DEL k` for each set removed (highest number first, numbered as before the step), then `SET k kind tiles...` for each set changed or added (numbered as after the step; k one past the last set adds a set). Set numbers in the step events above are as before the step.
- `RESET seat`: the turn started over; `UNDO seat`: the last step taken back. After these the table is as it was earlier in the turn; a client that keeps its own model fetches the view again.
- `MELD seat points`: the first meld made (the turn's new sets were worth that much).
- `DONE seat n`: the turn counts; n tiles came from the rack.
- `UNDONE seat reason`: `END` on an unready turn, which is undone (`reason`: `none`, `sets`, `meld` or `joker`; `none` when only the table was rearranged). The table and the rack are as they were when the turn began.
- `DRAW seat pool`: the player drew a tile (not named); the pool has `pool` left.
- `PASS seat`: nothing played with the pool empty.
- `OUT seat`: the player emptied the rack.
- `GONE seat resign|timeout`: the seat left the game and the others play on (see Leaving).
- `TURN seat`, and at the end `END win seat s0 s1 [s2 s3]` or `END draw s0 s1 [s2 s3]`, with every seat's score.

A typical step is a step event and one or two `SET`/`DEL` lines, 40-120 bytes; ending a turn is 20-40 bytes.

## Leaving

The state is built so that one seat can leave a game of three or four and the others play on. The plugin has an op for it (flag `drop`), `{"op":"drop","state","seat","why":"resign"|"timeout"}`, answering like `apply` (`state`, `events`, `end`). A staged turn of the leaving seat is undone (`UNDONE seat none`), then `GONE seat why`; if it was that seat's turn, `TURN` passes to the next seat still playing. From then on the turn skips the seat, the pool-out end needs only the seats still playing to pass, and the seat's rack stays out of play: at the end it scores minus its whole rack and cannot win. When only one seat is left, the game ends and that seat wins.

The core calls `drop` for `RES` and for a turn timeout while more than two seats are still playing, and keeps the table running. With two seats left, `RES` and a timeout end the whole table as in every game (`END resign seat`, `END timeout seat`).

## Size

A full view is `P`, `C`, `I`, `G`, `W`, `TP` (about 60 bytes), one `ST` line per set (12-60 bytes; at most about 35 valid sets, since all 106 tiles make at most that many) and the rack (`H`, about 3.5 bytes a tile). An opening view is about 120 bytes; a crowded late table about 1000; the worst case (every tile on the table, a long rack while a turn is staged) stays under 2000. Every line is under 80 bytes: a set has at most 13 tiles and an `H` line at most 16.

## Open

- Recorded games for the conformance directory.
