# checkers/1 (draft)

Checkers is for two players, with the flag `staged`. The ruleset `american` is built and `international` is planned (below). The option `limit=n` sets how many moves each with no capture and no man moving draw the game (10-200, or 0 for no limit; default 40). The time per turn is the server's `turn=` (protocol.md).

Seat 0 moves first. In `american` seat 0 is Black and seat 1 White; a frontend names them so. Colors are not otherwise part of the contract.

## Static geometry (flag `G`)

`BRD 8`: the board is 8 by 8 (`BRD 10` for `international`).

The dark squares are numbered in the standard way, 1 to n*n/2, n/2 to a row: 1-4 on the top row, 29-32 on the bottom row, each row left to right, as seen from White's side. The top-left corner square is light, so 1 is the second square of the top row and 5 the first of the next; 4 and 29 are the single corners, 1, 5 and 28, 32 the double corners. Black's men start on 1-12 and move towards 32; White's start on 21-32 and move towards 1. This is the numbering of published checkers games, of xcheckers and ICDS, and of play-by-mail servers, so their move lists read the same here. A frontend may turn the board round for display (each player's own side at the bottom); the numbers never change.

## View lines

| Line | Meaning |
|---|---|
| `B cells` | The board: one character per dark square, in square-number order (32 for 8 by 8). `.` empty, `x` a man of seat 0, `X` a king of seat 0, `o` a man of seat 1, `O` a king of seat 1. |
| `C m0 k0 m1 k1` | Men and kings per seat. |
| `W seat` | Whose turn it is; `W -` when the game is over. |
| `Q phase` | `move` (play a step), `done` (the turn's steps are complete: send `END`, or `UND`), `draw` (answer a draw offer: `accept` or `reject`), `over`. |
| `L quiet limit` | Turns played in a row with no capture and no man moving, and the limit at which the game is a draw (twice the `limit` option; 0 for none). |
| `J sq sq ...` | While a turn is part played: the moving piece's path, its starting square first and the square it now stands on last. |
| `D seat` | While a draw offered by seat waits for an answer. |
| `T`, `S`, `M`, `CLK` | Common lines (protocol.md). `M` lists the legal steps, one per line, or `accept` and `reject` while a draw offer waits for this player. |

The board is a dense string, as Reversi's rows are: 34 bytes for 8 by 8, against up to about 120 for a list of 24 pieces. A full view is about 110 bytes.

## Move tokens

One step per token:

- `a-b`: a man or king moves from square a to the next square b, like `11-15`.
- `axb`: a jump from a over the opposing piece between them to b, like `15x22`. `a-b` is also accepted for a jump (xcheckers and ICDS send every move as `from-to`).
- A chain `axbxc...` (`15x22x31`), or several step tokens in one `MOV` (`MOV 3 15x22 22x31`), plays those steps in order, all or none.
- `draw` offers a draw (not listed in `M`): on a player's own turn before a step is played, or on the other player's turn while that turn has no step played. A player whose offer was declined may offer again after their next move. `accept` or `reject` answer an offer.
- `UND` takes back the last step of the turn (a capture puts the captured piece back; a crowning is undone with its step). `END` commits the turn once its steps are complete.

### Staged turns

A turn is a series of steps (flag `staged`), as in protocol.md:

1. If any piece of the side to move can jump, the turn must start with a jump (`M` then lists only jumps). Any capturing piece and any line of captures may be chosen; the number of pieces taken does not matter in `american`.
2. After a jump, if the same piece can jump again it must: `MOV` answers `OK more` and `M` lists only that piece's next jumps. `END` is refused until the line of jumps is finished.
3. A man that reaches the far row (Black's: 29-32, White's: 1-4) is crowned at once and the turn ends there, even if the new king could jump on.
4. A turn whose steps are complete answers `OK done`; the player sends `END` (or `UND` to rethink). A simple move is one step.
5. If the completed turn leaves the other player with no piece, or with no legal move, the game ends at once (`END win`), with no `END` needed.

## Events

- `STEP seat from to`: a move of one square.
- `JUMP seat from to sq`: a jump; the piece on sq is captured and removed.
- `CROWN seat sq`: the man on sq becomes a king.
- `UNDO seat from to piece [sq captured]`: the step from-to is taken back: the piece on `to` returns to `from` as `piece` (`x`, `X`, `o`, `O`: a man again if it was crowned on that step), and, after a jump, `captured` returns to sq.
- `DRAW seat`: a draw is offered. When the offer is made on the offerer's own turn, `TURN` passes to the other player for the answer and comes back after `REJECT`.
- `ACCEPT seat`, `REJECT seat`: the answer.
- `TURN seat` after `END`, and `END result` with `result` one of: `win seat captured` (the other side has no pieces left), `win seat blocked` (it has pieces but no legal move), `draw agreed`, `draw limit` (the move limit was reached). Resigning and the turn timer give the common `END resign seat` and `END timeout seat`.

Applying an event needs no rules knowledge: each says which squares change and to what.

## Size

Full view about 110 bytes (34 for the board). Typical event 10-25 bytes; a turn is one to a few step events.

## Planned: `international`

International draughts as a second ruleset of the same type (the view lines, events and tokens do not change): a 10 by 10 board (`BRD 10`, squares 1-50, five a row, 20 men each, White as seat 0 moving first), men capturing backwards as well as forwards, flying kings (moving and capturing along a whole diagonal; a jump token then spans several squares, like `28x46`), and the majority rule (the line of captures that takes the most pieces must be played; `M` lists only steps that begin or continue such a line). Captured pieces are taken off only when the line ends, and a man that passes the far row during a capture is not crowned unless the line ends there. The core passes the table's ruleset to the plugin in every request, so the plugin can tell the two apart.

## Not offered

- Draw claims by threefold repetition; the move limit and agreement cover drawn endings.
- The three-move ballot openings of match play.
