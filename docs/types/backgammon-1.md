# backgammon/1 (draft)

Backgammon is for two players, and the server rolls all the dice. Its flag is `staged`. The only ruleset is `standard`; `nackgammon` and `hypergammon` are not offered. The options are `cube=0|1` (default 1) and `match=n` (a match to n points, 1-99; default 0, a single money game). The time per turn is the server's `turn=` (protocol.md).

## Geometry

Static geometry (flag `G`): `BRD 24`, the number of points.

Absolute points 1-24. Seat 0 moves from 24 down to 1 and bears off from 1-6; seat 1 moves 1 up to 24 and bears off from 19-24. A client may flip or mirror for display; the protocol never does.

## View lines

| Line | Meaning |
|---|---|
| `P 1 c1 ... c12` | Checkers on points 1-12. Signed: positive = seat 0's, negative = seat 1's, 0 empty. |
| `P 13 c13 ... c24` | Points 13-24, same form. |
| `B n0 n1` | Checkers on the bar, per seat. |
| `O n0 n1` | Checkers borne off, per seat. |
| `D d d [d d]` | Dice still to play this turn (two, or up to four on doubles). Empty line `D` before rolling. |
| `C value owner` | Cube: value 1-64, owner `-` (centered), `0` or `1`. |
| `W seat` | Whose turn it is (`W -` when the game is over). |
| `Q phase` | `roll` (cube available: send `roll` or `double`), `move`, `take` (answer a double), `resign` (answer a resignation), `over`. |
| `N n` | Steps the roll requires this turn (the maximum-dice rule); 0 before rolling or when it is not a moving turn. |
| `DR d d` | The dice rolled this turn (only after the roll; `D` shrinks as steps are played, `DR` does not). |
| `CA a0 a1` | 1 if that seat may double when its turn starts: cube on, not the Crawford game, cube below 64, centered or owned by that seat. |
| `L n` | Match play only: the match length in points. |
| `T seat points` | Match play only: the score, one line per seat (the common `T` line). |
| `G n [crawford]` | Match play only: the game number in the match; `crawford` in the Crawford game. |
| `RS seat points kind` | While a resignation offered by seat is waiting for an answer: points and `single`, `gammon` or `backgammon`. |

`N`, `DR`, `CA`, `L`, `T`, `G` and `RS` were added after the first draft; a client that does not know them can skip them. A full view is about 200 bytes.

## Move tokens (one step each)

- `from-to`: `13-9`, `bar-22`, `6-off`. `from` is a point or `bar`; `to` is a point or `off`.
- Turn start with the cube available: `roll` or `double`. Answer to a double: `take` or `drop`. (Only when `cube=1`, the default.)
- `resign-single`, `resign-gammon`, `resign-backgammon`: offer to resign at that level, on either player's turn (not while a double waits for an answer). The other player answers `accept` (the game ends with that result) or `reject` (the game goes on where it was). These are not listed in `M`.
- `UND` takes back the last step of the turn; `END` commits the turn once no step is left to play.

`M` lists legal single steps given the dice left, including the rule that the maximum possible dice must be played (bearing off the last checker counts as playing every remaining die) and the larger-die rule. When a turn begins, the server rolls and sends the event; a player with no legal step is passed automatically (`PASS`). After the last playable step `MOV` returns `OK done`; the client sends `END` (or `UND` to rethink). `roll` answers `OK more` (the turn goes on with the steps). `CHK` of an accepted step or answer answers `OK` with a word (`ok`, `roll`, `double`, `take`, `drop`, `resign`, `accept`, `reject`, `end`).

## Events

- `OPEN d0 d1`: a game's opening roll, one die per seat (ties are rolled again unseen). The higher die starts and plays both: `TURN`, `ROLL` and `STEPS` follow.
- `ROLL seat d d` (server roll, doubles are the same event with equal dice)
- `STEPS seat n`: after a roll, the number of steps the roll requires (`PASS` instead when none can be played)
- `STEP seat from to [HIT]` (a hit also puts the opposing checker on the bar)
- `UNDO seat from to` (a step taken back)
- `DBL seat value`, `TAKE seat value`, `DROP seat`
- `OFFER seat points kind`, `ACCEPT seat`, `REJECT seat`: a resignation offered and answered. When the offer is made on the offerer's own turn, `TURN` passes to the other player for the answer and comes back after `REJECT`.
- `PASS seat`
- `TURN seat` (after `END`), `END result` with `result` of the form `win seat points gammon|backgammon|single`

Match play (`match=n`): a won game (by bearing off, a dropped double or an accepted resignation) gives `WON seat points kind` and `SCORE s0 s1`. Unless the match is decided, the next game follows in the same reply: `GAME n [crawford]`, then `OPEN`, `TURN`, `ROLL`, `STEPS`. The table ends (`END`) only when a player reaches n; `END` then repeats the last game's result. The first match starts with `GAME 1`. The Crawford rule applies: no doubling in the game after a player first reaches n-1. Gammons and backgammons count in every game.

## Size

Typical event 10-20 bytes; a turn with two dice is a roll event, a steps event and two steps, under 60 bytes. Worst-case view about 200 bytes.

## Open

- Clock details for slow links (start counting from view delivery).
- Beavers, Jacoby and automatic doubles are not offered.
