# Game plugin interface (draft 0)

A plugin is a program the server talks to over stdin/stdout, one JSON object per line, shared by all tables of its type. Calls are stateless, so a crashed or restarted plugin loses nothing. JSON suits this link because both ends are modern machines; the protocol for 8-bit clients is in protocol.md.

## Processes

Each game is a directory under `plugins/` holding one of:

- **`plugin.py`**, a Python plugin. All of them are served by one process, the plugin host (`parlord/pluginhost.py`). It imports each plugin on its first request as a module of its own (`importlib`, named `parlor_plugin_NAME`, so plugins never share names) and answers through the module's `OPS` table, a dict from op name to a function of the request (the same table the plugin's own main loop uses). Every request to the host carries `"game": NAME`, the plugin's directory name, which the host removes before handing the request on; otherwise requests and replies are those below. A plugin that raises, or cannot be imported, answers `{"error": ...}` for that call while the host goes on serving the others; a plugin's prints go to stderr, and it reads nothing from stdin (the requests are the host's). A hosted plugin imports only the standard library: it cannot import modules of its own beside it.
- **`plugin`**, an executable in any language. It runs in a process of its own, speaking the protocol below with no `"game"` field.

A `plugin.py` runs in a process of its own, `python3 plugin.py`, like an executable, when its `info` has `"process": "own"`, when it has no `OPS` table, or when the host cannot get its `info`. Every shipped plugin is hosted, and each still runs alone with `python3 plugin.py`. Every shipped plugin also answers `say` (below).

The core asks every plugin for its `info` once, at start-up (one start of the host for all the Python plugins, one start of each plugin of its own), and keeps it: listing the games (`GAM`) and creating tables need no running process. A process is started on the first call that needs it and stopped once it has had no call for `plugins.idle` seconds (default 600; the setting, from the admin store or the settings file, or `--plugin-idle` on parlord's command line) while no table of its games is being played. A call that fails, or gets no reply within 5 seconds, kills the process (the host, for a hosted plugin) and is tried once more in a new one.

## Requests

Server to plugin, each answered by one line. Every request for a table carries `"rules"` (the table's ruleset name from the plugin's `info`), so a plugin with several rulesets knows which one applies; `new` also carries `"players"` (the table's seat count, from the game's minimum to its maximum, chosen with `players=n` on `NEW`).

| Request | Reply |
|---|---|
| `{"op":"info"}` | `{"id","name","min","max","options":[...],"flags":[...],"views":{...}}`, `"process":"own"` for a `plugin.py` that needs a process of its own, and `"say":true` for a plugin that answers `say` |
| `{"op":"new","players":n,"options":{},"seed":int}` | `{"state":...}` |
| `{"op":"view","state","seat"}` | `{"wire":[view lines],"geometry":[static lines],"status":text,"data":{...}}`: the type's view lines in wire form, plus the same state as JSON for modern clients. The request may also have `"open":true`, which the core sends for a server extension at a table marked `integrity=false` (`VUE table O`, [protocol.md](protocol.md)): a game with racks then gives a seated player every rack and the bag as well (Grid Words' `RK` and `BO` lines); a plugin with nothing hidden ignores it |
| `{"op":"moves","state","seat"}` | `{"moves":[token lists]}` or `{"check":true}` for huge move sets |
| `{"op":"apply","state","seat","move":[tokens],"check":bool}` | `{"state","events":[wire event text],"end":result?,"tied":[seats]?}` or `{"error":"reason"}`; with `check` nothing is changed and `{"info"}` is returned. `tied` is optional, with an `end` that is a draw: the seats that share it, for the scores (protocol.md, Scores). Without it the core takes the seats still playing with the top score the result lists, or every seat still playing; Cannon Fire sends it, since its last tanks share a draw whatever their points |
| `{"op":"result","state"}` | `{"over":false}` or `{"over":true,"scores":[...]}` |
| `{"op":"say","rules","seat","events":[...]}` | Optional, for a plugin whose info has `"say":true`: a turn in a few words, `{"say":"played FRAULEIN for 65"}`, past tense and without the player's name, at most 60 printable ASCII characters (the core cleans and cuts it). The core asks it once a turn is over, with every event the turn made, from its first step to its end, and the seat that played it. The text goes into the turn's record (`TRN`, `FEED`, [protocol.md](protocol.md)), which anyone may read, so it is worked out from those public events only, never from the state; `""` says nothing. Events of other seats in the list (the next player's roll) are not the turn's own |
| `{"op":"typed","rules","state","seat","words":[...]}` | Optional, for a plugin whose info has `"typed":true`: what a move typed on a command line means ([protocol.md](protocol.md), Commands). `words` is the line after its slash, split at spaces, as typed (`/h8 across QUIZ` is `["h8","across","QUIZ"]`), and `seat` is the seated player who typed it, at a running game. The reply is `{}` when the words are no move of this game, `{"move":[tokens]}` to play those tokens as `MOV` does, `{"step":"undo"}` or `{"step":"end"}` for `UND` or `END` in a staged game, `{"say":text}` to answer in words and play nothing (a check), or `{"error":text}` for a refusal. Texts are sentences for the player. A move or a step may add `"ask":"pass"` or `"ask":"end"` when it gives up the turn or ends a turn that won't count, so the core asks first, and `"what"`, a verb phrase for the core's refusal when the move is refused ("You can't roll. ..."). No reply answers an offer: those words are an error that says the offer is answered in its dialog. It changes nothing, and the core plays the move with `apply` as for any other |
| `{"op":"drop","state","seat","why":"resign"\|"timeout"}` | Only for a plugin with the flag `drop`: one seat leaves a running game and the others play on. Answered like `apply`: `{"state","events","end"?}` (the events include `GONE seat why`; `end` when one seat is left), or `{"error"}` |

The flags in `info` are `staged` (turns of several steps, `UND`/`END`), `nomoves` (no move list; clients use `CHK`) and `drop` (the plugin answers `drop`). The core calls `drop` when a player resigns (`RES`) or runs out of time while more than two seats are still playing; it records the seat as gone (no more `MOV` or `RES` from it) and keeps the table running. With two seats left, or when `drop` answers an error, the game ends for everyone as for any other game (`END resign seat`, `END timeout seat`).

Rules:
- Calls are pure functions of their arguments. The state is opaque to the server, which stores it and hands it back.
- All randomness comes from the seed in state, so games replay and tests are reproducible. The core draws each table's seed from the system's secure generator, 63 bits wide; a plugin keeps it in the state and never puts it in a view or an event ([design/trust.md](design/trust.md)).
- A plugin needs no network or filesystem access, so the server can sandbox it.
- Every plugin ships a tests directory of recorded games (the conformance vectors for its type spec in docs/types/).
- A plugin's `view` returns the typed fields of its type spec, not a rendered board. The web and telnet renderings of each type are its frontend module in adapters/games/; games-howto.md says what a new game adds.
