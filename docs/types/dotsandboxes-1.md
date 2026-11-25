# dotsandboxes/1 (draft)

Dots and Boxes is for two to four players (see Players), with the flag `drop` and single-step moves (no `staged`). The time per turn is the table's `turn=` (protocol.md), handled by the server.

The rulesets:

- `standard`: a grid of boxes with every line open at the start, and seat 0 moves first. A move draws one line between two neighboring dots. A player who closes the fourth side of one box or two boxes takes them and moves again. The game ends when every box is taken, and the most boxes wins. The option `size=` gives the boxes across, or across `x` down (`size=5`, `size=6x4`), from 3 to 9 each way, with 5 by 5 as the default.
- `majority`: the same moves on a board of 13 boxes across and 11 down, for two players only. The game ends as soon as one player holds more than half the boxes (72 of 143), and that player wins. This is the board and the end of GameMaster's Dots&Boxes rulebook. `size=` is refused unless it is `13x11`.

Colors are not part of the contract: seats 0 to 3, which a frontend shows as A, B, C, D (and colors if it likes).

## Players

Two, three or four seats, chosen with `players=n` on `NEW` (default 2); turns go round in seat order.

With three or more seats still playing, a player who resigns (`RES`) or runs out of time leaves the game and the others play on (flag `drop`, protocol.md): the event `GONE seat resign|timeout`, then `TURN` for the next seat still playing if it was that seat's turn. From then on its turn is skipped; the boxes it took stay its own and count in `C` and in the final scores, but it cannot win. If only one seat is left, that seat wins (`END win seat ...`). With two seats left, a resignation or a timeout ends the game for everyone as in any game (`END resign seat`, `END timeout seat`).

## Static geometry (flag `G`)

`BRD w h`: boxes across and down. The dots are `w+1` by `h+1`.

## Coordinates

Dots are a column letter and a row number, `a1` at the top left, rows counted from the top: on the default 5 by 5 board, columns `a`-`f` and rows `1`-`6`; on 9 by 9, `a`-`j` and `1`-`10`; on the majority board, `a`-`n` and `1`-`12`. A box is named by its upper left dot: box `a1` lies between `a1`, `b1`, `a2` and `b2`.

## View lines

| Line | Meaning |
|---|---|
| `HL y cells` | The lines across in dot row y (1 to h+1): w characters, `.` open, `-` drawn. Cell x is the line from dot x to dot x+1. |
| `VL y cells` | The lines down from dot row y to y+1 (y = 1 to h): w+1 characters, `.` open, `\|` drawn. Cell x is the line in dot column x. |
| `BX y cells` | The boxes in row y (1 to h): w characters, `.` open, `0` to `3` the seat that closed it. |
| `C n0 n1 ...` | Boxes taken, one number per seat. |
| `G g0 g1 ...` | Seats that have left the game: one per seat, `1` for a seat that left, `0` for one still playing. Sent only once a seat has left. |
| `LL line` | The last line drawn (`a1-b1`); absent before the first move. |
| `W seat` | Whose turn it is; `W -` when the game is over. |

The rows are a dense encoding: every line exists and fills in, so rows (about 200 bytes for 5 by 5, 450 for 9 by 9, with line ends) beat a list of the drawn lines (up to 180 of them on 9 by 9).

## Move tokens

A line is its two dots joined by `-`, the left or upper dot first: `a1-b1` (across), `c2-c3` (down). The server also takes the dots the other way round, in capitals, without the dash (`a1b1`), or as two tokens (`MOV 3 a1 b1`), and replies with the line in its own form in the event. `M` lists every open line on the mover's turn; every open line is legal, so a client short of bandwidth can leave out the `M` flag and read the open lines from `HL` and `VL` (60 lines on 5 by 5, 180 on 9 by 9). There is no pass.

`CHK table line` answers `OK boxes n`: the boxes that line would close.

## Events

- `LINE seat line [BOX box ...]`: the line drawn and the boxes it closed (none, one or two), for example `LINE 0 b2-c2 BOX b1 b2`. Under 30 bytes.
- `TURN seat` after every move that does not end the game, also when the same player moves again after closing a box: each `TURN` starts that player's turn clock afresh.
- `GONE seat resign|timeout`: with three or more seats playing, that seat left the game (see Players).
- `END result`, with `result` of the form `win seat n0-n1` or `draw n0-n1` (more seats: `win 2 5-3-6-2`, `draw 6-6-2-2`; a draw is a tie for the most boxes). Only seats still playing can win or draw: a seat that left is not counted for the top score, though its boxes are in the list.

## Size

Full view (with the `V`, `BRD`, `CLK` and `S` lines) about 270 bytes on 5 by 5, 530 bytes on 9 by 9 and 800 bytes on the 13 by 11 majority board. The `M` lines add 9 to 11 bytes per open line (540 bytes for a fresh 5 by 5 board, 1650 for 9 by 9 and about 2900 for 13 by 11). Typical event 10-30 bytes.

## Open

- Variants seen in period clients (NetBoxes: points for lines and boxes, timer penalties, mines) are not part of this type. NetBoxes 1.1 plays `standard` tables through the NetSprocket extension with mines off; its points are its own display.
- The GameMaster extension (adapters/gamemaster.py) plays GameMaster's Dots&Boxes rulebook on `majority` tables.
