# navalcombat/1 (draft)

Naval Combat is a hidden-information game for two players, with single-step moves (no `staged`). Each player places a fleet on their own grid, and the two then take turns firing at the other's grid, one shot a turn or a salvo of six (`salvo`). The server holds both fleets; a view and the events never show a square of a ship to anyone but its owner until that ship is sunk, except that under `salvo` each player's view carries the other fleet once both are ready (below).

Rulesets (the grid is 10 by 10 in both):

- `classic`: five ships of 5, 4, 3, 3 and 2 squares. Ships may touch but not overlap.
- `batnav`: ten ships, four of 1 square, three of 2, two of 3 and one of 4 (the fleet of the batnav network game). Ships may not touch, not even at a corner.
- `salvo`: the classic fleet, and each turn is a salvo of six shots, however many ships are still afloat. This is GameMaster's BattleShips rulebook. Under `salvo`, each player's own program receives the other player's fleet when shooting starts, because GameMaster works out the hits on the Mac: each seated player's view carries the other fleet (`OF` lines) once both fleets are ready. The web and telnet do not show those lines, but a program reading the view can.

Every ship lies straight, across or down. Options: none; `NEW` with any game option is refused (`ERR BADOPT no options for this game`). `turn=`, `vis=` and `players=` are the server's (protocol.md). Planned: `salvo=1`, a salvo of one shot per ship still afloat each turn; it would add a staged turn (one `MOV` per shot, then `END`) and is not part of this version.

Seat 0 fires first. There is no draw: the game ends when one fleet is entirely sunk, or by the shared ends (`resign`, `timeout`, `abandoned`).

## Phases

1. **Placing** (`Q place`). Both players place at the same time, each ship in the order of `FL` (largest first). A player may take back the last ship (`undo`), remove them all (`clear`) or have the server place the ships not yet placed at random (`random`; with the whole fleet placed, `random` places the whole fleet again). Once the fleet is complete the player sends `ready`; after that the fleet cannot change. Random placements come from the table's seed, so a game replays.
2. **Shooting** (`Q shoot`) starts when both fleets are ready. One shot per turn, hit or miss; the turn then passes to the other player. Under `salvo` the same player fires six shots, each a `MOV` that is answered at once, and the turn passes after the sixth (or sooner, when no square of the other grid is left to fire at). Each shot of a salvo is followed by `TURN` for the same seat, which starts its clock afresh.
3. **Over** (`Q over`): every ship of one fleet is sunk.

The turn clock: the game starts with `TURN 0`, so seat 0 is on the clock while placing. When seat 0 is ready and seat 1 is not, `TURN 1` puts seat 1 on the clock. When both are ready, `TURN 0` starts the shooting with a fresh clock. A player who never places their fleet loses on time like any other.

## Static geometry (flag `G`)

| Line | Meaning |
|---|---|
| `BRD w h` | The grid: `BRD 10 10`. |
| `FL len len ...` | The fleet, in placement order: `FL 5 4 3 3 2` (classic and salvo), `FL 4 3 3 2 2 2 1 1 1 1` (batnav). |

## View lines

What a line shows depends on who asks: the server builds each seat's view (and the watchers' view) from what that viewer may know.

| Line | Meaning |
|---|---|
| `Q phase` | `place`, `shoot` or `over`. |
| `W seat` | Whose turn it is; `W -` when the game is over. While placing, a seat whose fleet is not ready sees its own seat (it has a move to make); others see the first seat still placing (the one on the clock). |
| `RD r0 r1` | 1 when that seat's fleet is placed and ready. |
| `B seat y cells` | Row `y` (1-10, from the top) of `seat`'s waters, one character per column a-j: `.` nothing known (for the owner: open water), `o` a miss, `x` a hit, `#` a square of a sunk ship, and **only in the owner's own view** `s` a ship square not hit. Rows that are all `.` are not sent. |
| `Y len from-to` | The viewer's own ships (seated players only, never another seat's), in placement order: `Y 5 a1-e1`. |
| `N len` | While placing: the length of the viewer's next ship (absent once the fleet is complete or ready). |
| `K seat len from-to` | A sunk ship of `seat`'s fleet, for everyone: every square of it has been hit. |
| `L n0 n1` | Ships afloat per seat (the whole fleet until that seat is ready). |
| `LS seat sq` | The last shot: who fired, and where. |
| `SL n` | Under `salvo`, while shooting: the shots left in the salvo of the seat to move (6 to 1). |
| `OF len from-to` | Under `salvo`, once both fleets are ready: the other seat's ships, in placement order, for seated players only (never for watchers). |

The common lines `S`, `M` and `CLK` mean what protocol.md says.

For a watcher, both grids show only shots and sunk ships; no `Y` or `N` lines. A seat's view of the other grid is the same as a watcher's. The view's `data` (JSON for modern clients) carries the same filtered rows, never the state.

The rows are a dense encoding because a grid fills with shots: 20 rows of 17 bytes is the worst case, and an empty grid sends nothing.

## Move tokens

- Placing: `from-to`, the two end squares of the next ship (`a1-e1` across, `a1-a5` down; either end first; a one-square ship is `c4-c4`). The length must be that of the next ship in `FL`. `random`, `undo`, `clear`, `ready` as above. `M` lists every legal place for the next ship (top or left end first), then `random`, `undo` and `clear` when a ship is placed, and `ready` once the fleet is complete.
- Shooting: a square, `e5` (columns a-j, rows 1-10 from the top). `M` lists every square of the other grid not fired at yet, only to the player whose turn it is.

`CHK` of a shot says only whether it may be fired (`OK`); it never tells hit or miss.

## Events

Events go to every player and watcher, so none names a square of a ship that has not been hit:

- `READY seat`: that seat's fleet is placed. Placing ships (one by one or with `random`), undoing and clearing send no event.
- `SHOT seat sq miss`, `SHOT seat sq hit`, `SHOT seat sq sunk len from-to`: seat fired at sq on the other grid; on `sunk`, the ship that went down (all its squares are hit by then). Under 30 bytes.
- `TURN seat`; `END win seat h0-h1`, where `h0-h1` are the hits each seat landed (the winner's equals the fleet's squares: 17 classic and salvo, 20 batnav).

## Size

A full view (with the `V`, `CLK` and `S` lines) is about 130 bytes while placing (plus the `M` list for the player placing: up to about 140 placements of 9 to 11 bytes with their line ends), and at most about 650 bytes late in a `classic` game, 720 under `salvo` and 850 under `batnav`. Typical event 15-30 bytes.

## Notes

- The ruleset reaches the plugin as `rules` in the `new` request (the core passes it in every call); the plugin keeps it in the state. Without it the plugin plays `classic`.
- The batnav ruleset is what the batnav extension (adapters/batnav.py) plays for the clients of Batalla Naval. batnav's own server took up to five players, each shot falling on every other board; tables here seat two, so those clients play its two-player game.
- The salvo ruleset is what the GameMaster extension (adapters/gamemaster.py) plays. In GameMaster the six squares of a salvo are marked first and revealed together after Fire; here each shot is answered as it is fired.
