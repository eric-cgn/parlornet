# Bandwidth budget (draft 0)

Games should be playable at 2400 baud, comfortable from 14.4k and not wasteful on LocalTalk-class links (about 20 KB/s). Few real machines are that limited: an Apple II with a serial card can run 19200 (1920 B/s), which is the practical floor to design around, with 2400 as the stretch.

| Link | Bytes/s | 80-byte line | 2000 bytes (full 80x25) |
|---|---|---|---|
| 2400 baud (8N1) | 240 | 0.33 s | 8.3 s |
| 14.4 kbaud | 1440 | 0.06 s | 1.4 s |
| 19200 baud | 1920 | 0.04 s | 1.0 s |
| LocalTalk, effective | ~20 000 | ~0 | ~0.1 s |

On LocalTalk and TCP, round trips and bridge latency matter more than bytes; on serial, bytes are everything.

## Budgets

- A worst-case full view or repaint may take about 10 seconds at 2400 baud (roughly 2400 bytes); a full 80x25 screen fits within that.
- Routine turns should be small so play feels quick on slow links. The aim, not a rule, is a second or two at 2400, which typed events and piggybacked replies already give.
- Each game type declares its worst-case full-view and typical-delta sizes so clients and operators can see what to expect on a given link.
- Lines stay at 80 bytes at most.

## Rough sizes (typed views, from the type specs)

| Game | Full view | Typical event |
|---|---|---|
| Backgammon | ~150 B | 10-20 B per step; a two-dice turn under 50 B |
| Grid Words | ~680 B geometry once, then ~600 B mid-game | a play: four events of 6-40 B (~80 B) |
| Cannon Fire, 72 columns | ~150 B for two players, ~270 B for eight | 100-250 B per shot (`FIRE`, `TRJ`, `HIT`, `ZCH`, `DMG`, `TURN`), ~20 B per move (`MOVE`) |
| Checkers 8x8 | ~110 B | 10-25 B per step |
| Reversi 8x8 | ~100-150 B | 10-50 B |
| Dots and Boxes | ~270 B on 5x5, ~530 B on 9x9 (`M` lines add 9-10 B per open line) | 10-30 B |
| Five in a Row | ~60 B at the start, ~200 B after 40 stones, at most ~900 B on 15x15 (~1430 B on 19x19) | 10-15 B; the winning move ~40 B |
| Naval Combat | ~60 B while placing (+ up to ~140 `M` placements of 6-8 B), at most ~450 B | 15-30 B |
| Mancala | ~60 B | `SOW` up to ~55 B; others under 20 B |
| Rummy Tile Classic | ~120 B at the start, ~1000 B for a crowded table, under 2000 B worst case | 40-120 B per step; ending a turn 20-40 B |

(From the type specs, docs/types/; Five in a Row's are measured with its plugin. Backgammon's and Reversi's are estimates.)

## What serves this

- Typed events that say exactly what changed, so a client applies a few bytes instead of refetching; piggybacked events on `MOV` replies; static geometry sent once and cached; optional status and move-list lines (protocol.md).
- Compact encodings: sparse structures as coordinate lists, dense series (terrain) one character per entry.
- Server extensions that draw on terminals (telnet) update changed cells with cursor addressing instead of repainting; a first draw or a resync repaints in full.
- Serial bridges pace their output (XON/XOFF or a rate cap) so a burst cannot overrun a small machine's buffer.
- Run-length encoding could be added later as an option; draft 1 leaves it out to keep an 8-bit parser small.

## RSCARDS screens

The RSCARDS extension (docs/adapters.md) draws every game with the front end's own commands rather than sending views, so its costs are its own. Counted by the tests against the bot (tests/test_rscards.py, which write them out with `RSC_BYTES`): the whole screen when a table first opens (with the shapes the front end keeps for the rest of the session), the whole screen when the player comes back to it, and the median exchange (the player's turn and the bot's reply, with their message lines), in bytes sent; the front end's acknowledgments (2 bytes a packet) come back the other way. The time is the first screen at 2400 baud.

| Game | First screen (at 2400 baud) | Screen again | Exchange |
|---|---|---|---|
| Reversi | 1253 B (5 s) | 863 B | 180 B |
| Backgammon | 2662 B (11 s) | 1712 B | 934 B |
| Checkers | 1952 B (8 s) | 1249 B | 198 B |
| Mancala | 2041 B (9 s) | 940 B | 423 B |
| Five in a Row | 1440 B (6 s) | 969 B | 143 B |
| Dots and Boxes | 1187 B (5 s) | 793 B | 154 B |
| Naval Combat | 1842 B (8 s) | 1115 B | 162 B |
| Grid Words | 2117 B (9 s) | 1536 B | 506 B |
| Rummy Tile Classic | 1493 B (6 s) | 990 B | 381 B |
| Cannon Fire | 1373 B (6 s) | 984 B | 730 B |

These are the Mac color front end's; the tests measure the others with `RSC_MACHINE` (4 the IBM PC, 8 the mono Mac), whose screens cost the same within a few percent. An exchange is a whole turn of each side: in Backgammon the roll, two picks and steps, Done and the bot's turn, some 100 to 250 bytes a click; in Cannon Fire the aiming clicks and both shots.
