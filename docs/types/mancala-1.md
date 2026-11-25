# mancala/1 (draft)

Mancala is for two players, with single-step moves (no `staged`), and seat 0 moves first. The board has two rows of six pits, one row per seat, and a store for each seat. Seeds are sown counterclockwise: along the mover's own row from its pit 1 to its pit 6, then (where the rules allow it) into the mover's store, then along the opponent's row from its pit 1 to its pit 6, then the opponent's store, and round again.

The rulesets:

- `kalah`, store rules (the default): 4 seeds in each pit to start (option `seeds=3` to `6`). Sowing goes into the mover's own store and skips the opponent's. When the last seed lands in the mover's store, the mover moves again. When it lands in an empty pit of the mover's own row and the pit opposite holds seeds, the mover captures both the last seed and the seeds opposite into their store (when the pit opposite is empty, nothing is captured). When either row is empty after a move, the game ends: each player adds the seeds left on their own row to their store. The bigger store wins; equal stores are a draw.
- `oware`, Oware with the abapa rules: 4 seeds in each pit (48 in all; `seeds` may only be 4). The stores hold captured seeds and are never sown into. A sowing of 12 or more seeds skips the pit it started from on each lap. When the last seed lands in a pit on the opponent's row and makes it 2 or 3, those seeds are captured, and so are the seeds of the pit before it, and the one before that, as long as each holds 2 or 3 and is on the opponent's row. **Grand slam:** a move that would capture every seed on the opponent's row is allowed but captures nothing (the seeds stay where they were sown). **Must feed:** when the opponent's row is empty, the mover must play a pit whose sowing reaches it; the legal-move list holds only those. **End:** a player with more than 24 seeds in their store wins at once. When the player to move has no legal move (the opponent's row is empty and no sowing reaches it), that player takes the seeds on their own row into their store and the game ends. When a position (the pits and the player to move) comes back, or after 200 moves in a row without a capture, the game is in an endless cycle: each player takes the seeds on their own row and the game ends. 24 each is a draw.

Options: `seeds=` (kalah only, 3 to 6, default 4); `rules=kalah|oware` (see Choosing the ruleset); `turn=` and `vis=` are the server's.

### Choosing the ruleset

The ruleset is the word after the type on `NEW` (`NEW mancala oware`). The plugin reads it from the `rules` field of the plugin's `new` request. The option `rules=` is only for a core that does not pass that field (draft 0 of docs/plugins.md had none): such a core starts every table with `kalah` unless the table has `rules=oware`. When the core passes the ruleset, as this one does, the ruleset wins and the option is ignored. The view's `RU` line always says which ruleset the table plays.

## Static geometry (flag `G`)

`BRD 6`: pits per row.

## View lines

| Line | Meaning |
|---|---|
| `RU rules seeds` | The ruleset (`kalah` or `oware`) and the seeds each pit started with. |
| `B c1 ... c14` | The board, 14 counts in sowing order: seat 0's pits 1-6, seat 0's store, seat 1's pits 1-6, seat 1's store. In `oware` a store holds that seat's captures. |
| `LM seat pit` | The last move: the seat and the pit it played. Absent before the first move. |
| `W seat` | Whose turn it is; `W -1` once the game is over. |
| `T seat points` | Scores (common line; not sent, the stores are the score). |

Pit `p` of seat `s` is the count at position `s * 7 + p` of `B` (counting from 1); the pit opposite seat 0's pit `p` is seat 1's pit `7 - p`. A frontend draws the viewer's row along the bottom, pit 1 on the left, their store on the right, and the opponent's row along the top read right to left.

## Move tokens

A pit number, `1` to `6`, counted along the mover's own row from the mover's left (the pit after the opponent's store). `M` lists the legal pits on the mover's turn: every non-empty pit, except under the oware must-feed rule. There are no passes: in `kalah` the game ends as soon as a row is empty, and in `oware` a player with no legal move ends the game. `CHK table pit` answers `OK captures n`, the seeds the move would capture.

## Events

- `SOW seat pit n c1 ... c14`: seat played its pit, sowing n seeds; the board after the sowing and any capture, in the order of `B`. A client applies it by replacing the board.
- `CAP seat n`: that sowing captured n seeds into seat's store (already in the `SOW` board).
- `GRAND seat`: oware: the sowing would have captured every seed on the opponent's row, so it captured nothing.
- `AGAIN seat`: kalah: the last seed landed in seat's store; seat moves again (`TURN` follows with the same seat).
- `SWEEP seat n why`: at the end of the game, the n seeds left on seat's row go into seat's store. `why` is `empty` (kalah: a row is empty), `nofeed` (oware: the player to move cannot move or feed the opponent) or `cycle` (oware: an endless cycle). Sent after `SOW` and before `END`, one per seat with seeds left.
- `TURN seat`, `END result` with `result` of the form `win seat s0-s1` or `draw s0-s1` (the final stores).

A typical move: `SOW 0 3 4 4 4 0 5 5 5 1 4 4 4 4 4 4 0`, `AGAIN 0`, `TURN 0`.

## Size

Full view about 60 bytes (`RU`, a `B` line of at most 45 bytes, `LM`, `W`, `CLK`). `SOW` is at most about 55 bytes (14 counts: 48 seeds by default, 72 with `seeds=6`, so few counts have two digits), so an `EVT` line stays under 80 bytes. Other events under 20 bytes.

## Period clients

Awale (Myriad, Mac and Windows, 1997-2001) plays only the Oware family of rules, over IRC. The Awale extension (`adapters/awale.py`, port 6515) plays it on `oware` tables: Awale's hole h, counted from 0 along the mover's row, is pit h + 1 here. Its own "Awale" rule makes the same moves and ends differently: it plays on past 24 seeds and after a repeated position, and a player who cannot feed still moves once more.
