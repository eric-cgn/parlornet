# reversi/1 (draft)

Reversi is for two players, with single-step moves (no `staged`). The ruleset `standard` plays on 8x8 with four discs on the center diagonals; seat 0 moves first, a player with no legal move passes, the game ends when neither can move or the board is full, and more discs wins. The option `size=` sets the board (even, 4-10, default 8). The time per turn is the server's `turn=` (protocol.md).

Colors are not part of the contract. The seats are 0 and 1, and a frontend may show them as black and white, dark and light, or anything else.

## Static geometry (flag `G`)

`BRD w h`. Boards are square for now.

## View lines

| Line | Meaning |
|---|---|
| `R y cells` | One line per row, y = 1..h from the top. `cells` is w characters: `.` empty, `0` or `1` the seat owning the disc. |
| `C n0 n1` | Disc counts per seat. |
| `W seat` | Whose turn it is; `W -1` once the game is over. |

The rows are a type-defined dense encoding, not a generic grid: Reversi boards fill up, so rows (about 100 bytes for 8x8) beat a coordinate list (up to about 260).

## Move tokens

Squares are a column letter and a row number, `d3` (columns a-h, rows 1-8 on the standard board, rows counted from the top). `M` lists the legal squares. There is no `PASS` token: when a player has no legal square the server passes for them and sends `PASS`. `CHK table sq` answers `OK flips n`, the discs the move would flip.

## Events

- `PUT seat sq FLP sq sq ...`: the placed disc and every flipped square, for example `PUT 0 d3 FLP d4 d5`. Under 40 bytes in practice.
- `PASS seat`, `TURN seat`, `END result` with `result` of the form `win seat n0-n1` or `draw n0-n1`.

## Size

A full view is about 110 bytes, and a typical event 10-40 bytes.

## First-case test plan

Reversi was implemented first, as the simplest game that exercises the server, a plugin, the typed view, events, `CHK`-free move lists, the telnet adapter, a custom client and the IOS adapter.
