# fiveinarow/1 (draft)

Five in a Row is for two players, with single-step moves (no `staged`). It has the flag `nomoves`: every empty point is a legal move, so there is no move list (`M`), and a client may check a move with `CHK`.

The rulesets:

- `freestyle`: a line of five or more of a player's own stones, across, down or diagonally, wins.
- `standard`: exactly five wins; a line of six or more (an overline) does not win, though the move is legal and the game goes on.

In both, seat 0 plays first, the players alternate one stone a turn, there is no passing, no opening restriction and no swap, and the game is a draw when the board is full with no winning line.

Options: `size=` (the board is `size` by `size` points, 9 to 20, default 15; 20 by 20 is the board of the Mac program NetLuff, docs/clients/netluff.md). The time per turn is the table's (`turn=`, protocol.md).

The ruleset is the table's (`NEW fiveinarow standard`); the plugin reads it from the `rules` field of the `new` request and plays `freestyle` when the field is absent.

Colors are not part of the contract: seat 0 and seat 1, with a frontend free to show them as black and white (seat 0 black, as is usual for the first player), X and O, or anything else.

## Static geometry (flag `G`)

| Line | Meaning |
|---|---|
| `BRD w h` | The board: w columns by h rows of points (square: w = h = `size`). |
| `RUL rules` | The ruleset, `freestyle` or `standard`, so a frontend can say what wins. |

## Points

A point is a column letter and a row number, `h8`: columns `a` to `t` from the left, rows 1 to 20 from the top, as far as the board goes (a-o and 1-15 on the standard board, whose center is `h8`). Input is case-insensitive.

## View lines

| Line | Meaning |
|---|---|
| `P seat pt pt ...` | The stones of one seat, as a list of points in the order they were played. A seat with many stones has several `P` lines, each at most 78 characters; a client appends them. No `P` line before the seat's first stone. |
| `C n0 n1` | Stones per seat. |
| `L seat pt` | The last stone played (absent before the first move). |
| `F pt pt ...` | The winning line, once the game is won: its points from one end to the other (five, or up to nine in `freestyle`). |
| `W seat` | Whose turn it is, `-1` once the game is over. |
| `T seat points` | Scores (common line; not sent). |

The stones are a coordinate list, not a grid: a board is mostly empty for most of a game (protocol.md, sparse structures). Since seat 0 plays first and the players alternate, the order of the `P` lists also gives every stone's move number: seat 0's k-th stone is move 2k-1, seat 1's k-th is move 2k (counting from 1).

## Move tokens

One point per move: `MOV 3 h8`. The point must be on the board and empty. Errors: `no such point`, `that point is taken`, `not your turn`, `the game is over`. `CHK 3 h8` answers `OK wins` for a move that wins and `OK` for any other legal move.

## Events

- `PUT seat pt`: a stone placed, for example `PUT 0 h8`.
- `TURN seat` after every move that does not end the game.
- `END result`, with `result` either `win seat pt-pt` (the ends of the winning line, as in `F`) or `draw n0-n1` (the stone counts, when the board is full). Resignation and running out of time are the shared `END resign seat` and `END timeout seat`.

## Size

Full view (with `V`, `CLK`, the geometry and `S`): about 60 bytes at the start, about 200 bytes after 40 stones, at most about 900 bytes for a full 15 by 15 board (12 `P` lines) about 1430 bytes for a full 19 by 19 board (18 `P` lines) and about 1580 bytes for a full 20 by 20 board (20 `P` lines). A typical event is 10-15 bytes (`EVT 3 41 PUT 0 h8`); the winning move's events are about 40 bytes.

## Frontends

- Web (HTML 3.2): the board as one GIF with an image map, drawn from 25 by 25 pixel intersections (edge and corner pieces, star points, a black or white stone, a red dot on the last stone and on the winning line) with the column letters and row numbers round it; on the player's turn every empty point is an area of the map that plays there, and a small form takes a typed point.
- Telnet, full screen (`pc`, `utf8`, `ansi`): the board drawn as a grid of lines, 4 columns and 2 rows a point up to 10 by 10, 3 columns a point up to 15 by 15, 2 columns a point up to 20 by 20, so every size fits 80x24 with the panel beside it (on the 20 by 20 board the hint goes on the line below the board's last row). Stones differ in shape: `█` and `▒` (`X` and `O` in `ansi`), in brackets `(█)` where a point is 3 or more columns wide; the last stone is in square brackets, the winning line is highlighted and listed in the panel. Arrow keys, Enter or Space, a mouse click, or a typed point.
- Telnet, plain: the board printed with `X`, `O` and `.`, the last stone in square brackets, and the points typed at the prompt.
