# cannonfire/1

In Cannon Fire, 2-8 tanks on destructible terrain take turns to set an angle and a power and fire one shell. Before its shot a tank may drive left or right once, four times a game. The flags are `nomoves` and `drop`. The only ruleset is `classic`, with one shell type; weapon variants would be a later type version. The options are `w=` (arena width, 40-200, default 72; at least 8 columns per player), `seed=` (a number: the terrain and wind come from it instead of the table's random seed, so a table can replay a known landscape), and `wind=0|1` (default 1). The time per turn is the server's `turn=` option.

All physics run on the server in integer arithmetic, so games replay exactly and every client sees the same result. A client never simulates; the events say what happened. The rules below are complete, so another implementation that follows them gives the same terrain, the same flights and the same damage from the same seed and shots.

## Static geometry (flag `G`)

- `ARN w h`: the arena width in columns and the maximum height (63).
- Terrain heights are 0-63, one printable character each: `chr(0x30 + h)`, so `0` (bedrock) to `o`. Column 0 is the left edge.

## View lines

| Line | Meaning |
|---|---|
| `Z x chars` | Terrain: the heights of columns x, x+1, ... one character each, at most 60 per line, as many lines as the arena needs (a 72-column arena is two lines, about 80 bytes). |
| `K seat x hp` | A tank: its column and hit points 0-100 (0 = destroyed; the wreck stays). It stands on the terrain of its column. |
| `A seat angle power` | The seat's last aim (angle 0-180, power 0-100), a starting point for the next shot. |
| `L seat moves` | The moves the seat's tank has left this game, 4 down to 0. |
| `N w` | Wind, -9 to +9; positive blows towards higher columns. |
| `H x y` | Where the last shell burst. Absent before the first shot and after a shell that left the arena. |
| `W seat` | Whose turn it is; `W -1` when the game is over. |
| `B budget moved` | The path one move may use, in tenths of a column (the arena width w: 10% of it in whole columns), and `1` if the seat on turn has moved this turn (it must fire now), else `0`. |
| `T seat points` | Scores (common line), one per seat. |
| `G g0 g1 ...` | Seats that have left the game (see Leaving): one per seat, `1` for a seat that left, `0` for one still playing. Sent only once a seat has left. |

Seat letters: frontends show seat 0-7 as tanks `A` to `H`.

## Move tokens

A turn is an optional move, then a shot.

- `MOVE L n` or `MOVE R n` (also `MOVE -n` and `MOVE +n`): drive the tank towards lower (`L`, `-`) or higher (`R`, `+`) columns, using up to n columns of path (n is 1-999; more than the budget is the whole budget). The tank goes as far as the path and the obstacles allow (see Moving). At most one move a turn, before the shot, and 4 a game. The turn stays with the seat: no `TURN` follows, and the turn clock goes on running (it is not restarted). A second move in the same turn is `ERR ILLEGAL one move a turn, and you have moved: now fire`; with none left, `ERR ILLEGAL no moves left this game: fire`; a move that cannot go one column is `ERR ILLEGAL the tank cannot move left: ...` (the arena edge, another tank, too steep, not enough path for one column) and uses no move.
- `FIRE angle power`: angle 0-180 degrees (0 points towards higher columns, 90 straight up, 180 towards lower columns), power 0-100. It ends the turn. `fire` and `move` (and `l`, `r`) are accepted in any case.

No staged turn: each token is played by itself. `M` is not provided (flag `nomoves`): the moves op answers `{"check": true}`. `CHK table FIRE a p` answers `OK angle a power p` for a valid shot on your turn; `CHK table MOVE L n` answers `OK from x0 to x1 used u of b` (where the tank would end up, the path it would use and the budget, both in tenths of a column); `ERR ILLEGAL` otherwise (out of range, not your turn, a move that is refused).

## Events

A shot gives, in this order:

- `FIRE seat angle power`
- `TRJ x y x y ...`: the shell's path, at most 18 points spread evenly over the flight (the last one is where it stopped), 6 points per line, so up to 3 lines. Heights may exceed 63 (the shell left the top of the screen). Clients that skip it just show the impact.
- `HIT x y r`: the burst point and the crater radius (7); or `OUT x`: the shell left the arena past column x (`0` or `w-1`) and nothing happens. A shell still flying after 4000 steps is lost the same way, with `OUT` and the column it was over.
- `DMG seat hp`: a tank in the blast, with its hit points after it.
- `ZCH x chars`: the changed terrain columns starting at x (the crater and the dirt that fell into it), in the `Z` encoding, at most 40 characters per line.
- `FALL seat x hp`: the ground under a living tank dropped; it stands on the new ground at the same column. A fall does no damage: hp is what the tank had after the blast.
- `DIE seat`: a tank's hit points reached 0.
- `N w`: the wind changed (only when the wind is on).
- `TURN seat`: the next living tank, in seat order. Or the game ends: `END win seat s0-s1-...` (the last tank standing, with every seat's points) or `END draw s0-s1-...` (the last tanks destroyed each other).

A move gives one event, and the same seat still has the turn:

- `MOVE seat from to used left`: the tank drove from column `from` to column `to` using `used` tenths of a column of path, and has `left` moves left this game.

At the start: `N w`, then `TURN 0` (seat 0 fires first).

When a seat leaves (see Leaving): `GONE seat resign|timeout`, then `DIE seat` if its tank was still standing, then `TURN seat` for the next living tank if it was that seat's turn, or `END win seat s0-s1-...` when one tank is left.

## Leaving

With three or more seats still playing, a player who resigns (`RES`) or runs out of time leaves the game and the others fire on (flag `drop`, protocol.md). Its tank becomes a wreck: 0 hit points, still standing on its column, skipped in turn order like any destroyed tank. Shells pass through it, as through every wreck; moving tanks cannot. If it left on its turn after moving, the next seat's turn starts fresh (it may move). The points it scored stay in `T`. When this leaves one tank standing, that seat wins. Nothing here uses the random generator, so a game replays the same with the same shots and departures.

With two seats left, a resignation or a timeout ends the whole table for everyone: `END resign seat`, `END timeout seat`, as in every game on this server. A seat whose tank was already destroyed still counts as playing until it resigns; resigning then only adds `GONE`.

## Rules and constants

Randomness. One generator in the state: `rng = (rng * 1103515245 + 12345) mod 2^31`, and a draw `r(n) = (rng >> 16) mod n`, taken after the step. The first `rng` is the seed (`seed=` if given, else the table's seed) mod 2^31. Draws happen in exactly the order below.

Terrain (width w). Knots every 8 columns: `k0 = 16 + r(24)`, then for i = 1 .. w div 8 + 1, `ki = clamp(k(i-1) + r(25) - 12, 6, 48)`. Column x: `i = x div 8`, `t = x mod 8`, `s = t*t*(24 - 2t)`, `h = ki + floor((k(i+1) - ki) * s / 512)`, then `h = clamp(h + r(3) - 1, 1, 50)` (one draw per column, left to right).

Tanks (n players). `j = w div 4n`. For slot i = 0 .. n-1: `x = (2i + 1) * w div 2n + r(2j + 1) - j`, clamped to 2 .. w-3. Then the seats are shuffled over the slots (`order = [0 .. n-1]`; for i = n-1 down to 1: swap `order[i]` and `order[r(i + 1)]`), and seat `order[i]` takes slot i. For each slot in turn, columns x-1 and x+1 are set to the height of column x (a flat pad). Every tank starts with 100 hit points and 4 moves, and aims at angle 45 if its column is left of w div 2, else 135, power 50.

Wind. When on, the first wind is `r(11) - 5`. After every shot that does not end the game, `wind = clamp(wind + r(5) - 2, -9, 9)`; an `N` event is sent only if it changed. When off, the wind is always 0.

Flight. Fixed point: 1 column or 1 height unit = 65536. Sines are the table `S[d] = round(65536 * sin(d degrees))` for d = 0..90 (in the plugin). Speed `v = power * 512`. For angle a <= 90: `vx = (v * S[90 - a]) >> 16`, `vy = (v * S[a]) >> 16`; for a > 90: `vx = -((v * S[a - 90]) >> 16)`, `vy = (v * S[180 - a]) >> 16`. The shell starts at `x = tx * 65536 + 32768`, `y = (h[tx] + 2) * 65536`, where tx is the firing tank's column. Each step, at most 4000:

1. `vx += wind * 6`; `vy -= 320`; `x += vx`; `y += vy`.
2. If `x < 0` or `x >= w * 65536`, the shell is out (`OUT 0` or `OUT w-1`).
3. `c = x >> 16`. The firing tank cannot be hit until the shell has once been in another column.
4. A living tank in column c is hit when `y < (h[c] + 2) * 65536`: the burst is at `(c, h[c] + 1)`.
5. Otherwise the ground is hit when `y < h[c] * 65536`: the burst is at `(c, h[c])`.

The trajectory samples are `(c, y >> 16)` after each step (negative heights sent as 0); with N steps and N > 18, sample i (0..17) is the step `(i + 1) * N div 18` (counting from 1).

Damage, from the distance to the burst only. For each living tank, with its ground height g before the crater: `d = isqrt((x - cx)^2 + (g + 1 - cy)^2)` (floor square root, from the tank's center one unit above its ground to the burst point); if `d < 10` it loses `20 * (10 - d) div 10` = `2 * (10 - d)` hit points, down to 0. A direct hit (d = 0) is the most, 20 of a tank's 100: it takes five to destroy a tank. Constants: `DMG_MAX = 20`, `REACH = 10` (the crater radius 7 and a margin of 3), 100 hit points.

Crater. Radius r = 7. For each column i within r of cx: `dy = isqrt(r*r - (i - cx)^2)`, `lo = max(0, cy - dy)`, `hi = min(h[i], cy + dy + 1)`; if `hi > lo`, `h[i] -= hi - lo`. A burst at the surface digs a bowl; under an overhang of dirt the dirt above falls into the hole, so the column simply shortens (terrain collapse in a heightmap).

Falling. A living tank whose ground dropped falls with it to the new ground, whatever the height, and takes no damage (`FALL`).

Moving. The budget of one move is w tenths of a column of path (10% of the arena width). A tank at column x drives one column at a time towards the side asked for, with a limit of `min(10 * n, w)` tenths. A step from column x to the next column x' costs `STEP[|h[x'] - h[x]|]` tenths, with `STEP = [10, 14, 22, 32, 41]` (`round(10 * sqrt(1 + d * d))` for a height change d of 0 to 4): the arc length over the height profile, so a climb or a descent covers less horizontal ground than the flat. The tank stops before a step that would:

1. leave the arena (x' < 0 or x' > w - 1),
2. enter a column where another tank stands, living or a wreck,
3. change height by more than 4 (too steep, `CLIMB = 4`), or
4. take the path used past the limit.

It stands on the ground of the column it reaches; a crater's side or floor is ground like any other. Moving uses no random draw and does not change the wind. A move that stops before its first step is refused and costs nothing.

Scores. The firing seat scores every hit point the shot took from other tanks, and 25 for each other tank it destroyed. Damage to itself scores nothing. The kill bonus stays 25: with 20 at most a shot, a kill is worth a little more than one direct hit, so finishing a tank pays without outweighing the damage that wore it down.

The game ends when one tank or none is left. A tank takes at least five shots to destroy, so a game of two runs to ten shots or more, and longer with misses; the time per turn is the table's.

## Recorded game

The conformance vector in `tests/test_cannonfire.py` (`VECTOR_VIEW`, `FIRST_SHOT`, `VECTOR_TURNS`, `VECTOR_END`), recorded with the rules above: `seed=2026`, 72 columns, two players. The opening view, then:

| Seat | Move | Events |
|---|---|---|
| 0 | `MOVE L 7` | `MOVE 0 11 7 52 3` (with a step up of 2 on the way: 4 columns for 5.2 of path; a fifth would cost 2.2 more) |
| 0 | `FIRE 45 60` | ... `HIT 64 5 7`, `DMG 1 86`, `ZCH 57 721000000000125`, `FALL 1 61 86`, `N -2`, `TURN 1` |
| 1 | `MOVE R 7` | `MOVE 1 61 68 70 3` (level ground: 7 columns for 7.0) |
| 1 | `FIRE 120 55` | ... `HIT 51 17 7`, `ZCH 44 E>=<;;;:;;`, `N -3`, `TURN 0` |
| 0 | `FIRE 50 66` | ... `HIT 71 5 7`, `DMG 1 76`, `ZCH 69 000`, `N -5`, `TURN 1` |
| 1 | `MOVE -3` | `MOVE 1 68 65 30 2` |
| 1 | `FIRE 135 45` | ... `HIT 54 8 7`, `ZCH 47 ;5432221222`, `N -6`, `TURN 0` |

## Size

Full view: terrain about 80 bytes (72 columns), tanks, aims, moves, wind and scores about 20 bytes per seat, so about 150 bytes for two players and 270 for eight. A move: one `MOVE` event of about 20 bytes. A typical shot: `FIRE` (12 bytes), two or three `TRJ` lines (up to 150 bytes), `HIT`, one `ZCH` of about 15 columns, a few `DMG`/`FALL` lines and `TURN`: 100-250 bytes, about a second at 2400 baud. Every line stays well under 80 bytes with the `EVT table seq` prefix.

## Open

- Whether clients may ask to leave out `TRJ` (per session, at `HELO`, or per `SIN`).
- Weapons and a shop (a later type version if the view changes).
- Simultaneous fire (not in v1).
