# Adding a game

Read [CONTRIBUTING.md](https://github.com/eric-cgn/parlornet/blob/main/CONTRIBUTING.md) first: a game added to ParlorNet gets a name and a look of its own, never a commercial product's.

A game is four new files and nothing else. None of them is shared with another game, so several games can be built at once without touching the same file:

| File | What it is |
|---|---|
| `plugins/<game>/plugin.py` | The rules: a game plugin as in [plugins.md](plugins.md). The core finds it and lists the game in `GAM`. |
| `docs/types/<game>-1.md` | The type spec: view lines, move tokens, events, sizes. |
| `adapters/games/<game>.py` | The frontend module: the view parser, the event texts, the web board and images, the telnet screens and the text for the Games section and the game's page. The registry finds it. |
| `tests/test_<game>.py` | The plugin's tests and the frontend's tests. |

`<game>` is the type word: `reversi`, `gridwords`, `cannonfire`. The plugin's `INFO` type, the spec's file name, the module's file name and the start of its `KEY` all use it.

With the plugin and the module in place, the game appears with no other edit in the web lobby (a Start button), the telnet lobby (the game picker that N opens; one entry per game, whatever its rulesets), the welcome page's Games list, the Games page (`/games.htm`), the game's own page (`/games/<type>.htm`), the docs site's Clients page, and ParlorNet Play's game chooser (marked unfinished until Play has a module for it).

Do not edit `adapters/web.py`, `adapters/telnet.py` or `adapters/common.py` for a game. In `web.py`, leave `GAMES` (the planned games in build order) and `GAME_ABOUT` (the planned games' text) as they are; once the module exists, its `NAME`, `PLAYERS` and `ABOUT` are used instead and the row keeps its place. The game's frontends need nothing from the module. Every browser and telnet program is listed for every game, and a period client (one that reaches a server extension) goes in `PERIOD` in `adapters/clients.py`, with the game's type in its `games`.

Reversi is the worked example throughout: `plugins/reversi/plugin.py`, `docs/types/reversi-1.md`, `adapters/games/reversi.py`, and its tests in `tests/test_adapters.py` and `tests/test_core.py`. Backgammon (`adapters/games/backgammon.py`) shows a staged game, dice, a picked-up piece and its own glyphs.

## 1. The plugin

Follow [plugins.md](plugins.md): one JSON request per line on stdin, one reply per line on stdout, pure functions of their arguments, all randomness from the seed in the state. `INFO` gives the type word, version, display name (the same as the module's `NAME`), rulesets, player counts, flags (`staged` for multi-step turns, `nomoves` for no move list, `drop` for a game of three or more where a player can leave and the others play on) and options. A game flagged `drop` answers the `drop` op: Rummy Tile Classic (`plugins/rummytile/plugin.py`) is the worked example, and Dots and Boxes and Cannon Fire do the same. With `say` in its `INFO`, the plugin answers the `say` op with a turn in a few words (`played d3, turning 2`) for the table's history, the activity and profiles; every game's plugin does. `view` returns the type's view lines, not a rendered board. `plugins/reversi/plugin.py` is complete in under 200 lines.

The core loads every `plugins/<game>/plugin.py` it finds at start; an operator can switch a game off in the admin menus (`games.off`).

## 2. The type spec

`docs/types/<game>-1.md`, in the form of [types/reversi-1.md](types/reversi-1.md): players, flags, rulesets and options; static geometry (`BRD` and the like, flag `G`); the view lines in a table; the move tokens; the events; the sizes in bytes. Every view starts `V <key> <seq>`, and the common lines (`S`, `T`, `M`, `CLK`) mean what [protocol.md](protocol.md) says; say whose turn it is with `W seat`, as Reversi and Backgammon do. A spec written before the game is built is a draft; update it as the game is built.

Move tokens go on a protocol line (`MOV 3 d3`), so they are ASCII letters, digits and `-` `_` `/` `.`: the protocol's name characters (`[A-Za-z0-9_-]`, [protocol.md](protocol.md)) plus `/` and `.` for coordinates and steps (Grid Words writes a blank as `_X`). A move of several tokens is sent space-separated.

## 3. The frontend module

`adapters/games/<game>.py` defines the names below. The registry (`adapters/games/__init__.py`) imports every module in the package whose name does not start with `_`, checks that these names are there, and skips (with a line on stderr) a module that lacks one, so one broken game never takes the others down. `tests/test_games.py` fails on a skipped module.

The module imports what it needs from `adapters.telnet` (the screen framework), `adapters.web` (`esc`, `a`, `make_gif`, `board_html`, `hidden_fields`) and `adapters.board` (`Canvas`, imported as `Picture` where the module also uses telnet's `Canvas`). Those modules import the registry, which loads the game modules only when first asked, so the imports do not go round in a circle.

### What the game is

| Name | Reversi's | Meaning |
|---|---|---|
| `KEY` | `"reversi/1"` | The game type and version, as in `GAM`. |
| `NAME` | `"Reversi"` | The display name. For a planned game, exactly the name in `web.GAMES` (`"Grid Words"`), so the Games table keeps it in its place. |
| `ORDER` | `1` | The place in the build order (README.md): Reversi 1, Backgammon 2, Grid Words 3, Cannon Fire 4, Dots and Boxes 5, Five in a Row 6, Mancala 7, Checkers 8, Naval Combat 9, Rummy Tile Classic 10. Menus, the lobby and the tables list games in this order. |
| `PLAYERS` | `"2"` | Players as text: `"2"` or `"2-4"` (the Games table uses the core's numbers when it hosts the game). |
| `ABOUT` | `(about, [rules])` | For the welcome page's Games section and the game's own page. `about`: two or three sentences in ParlorNet's own words on what the game is. `rules`: one plain sentence or two per ruleset and option this server supports, from the type spec. Plain ASCII text, generic names only: never a trademark (Othello, Scrabble, Battleship, Rummikub, Kalah, Scorched Earth...); the games page test checks some of these. |
| `RULE_NAMES` | (none: one ruleset) | Optional. `{ruleset: display name}` for a game with several rulesets (Mancala: `{"kalah": "store rules", "oware": "Oware rules"}`). When the plugin lists more than one ruleset, the web lobby's form has a rules select and the telnet lobby asks with a picker, both showing these names (the ruleset word with a capital when a name is missing), and the first ruleset in the plugin's list is the default. A game whose plugin takes more than two players gets a players select and picker the same way, sent as `players=n` on `NEW`. |

### The view and the events

`parse_view(lines)` turns the `VUE` reply (the lines before `OK`) into a dict. Both frontends read these keys, so every game's dict has them:

| Key | Meaning |
|---|---|
| `seq` | The view's sequence number, from `V key seq`. |
| `rows` | Truthy once the game has started. Before that both frontends show "Waiting for an opponent". Reversi keeps its board rows here (empty before the first view with a board); Backgammon sets True once there are checkers. A view of only `V <key> 1` must give a falsy `rows`. |
| `turn` | The seat to move (`W`), -1 when none. |
| `moves` | The legal move tokens (`M` lines); empty for a `nomoves` or `CHK` game. |
| `status` | The `S` line. The web page shows it, and treats "Game over" in it as the end. |
| `clock` | `(seat, seconds left, seconds per turn)` from `CLK`, or None. |

Add whatever else the game needs (Reversi: `n`, `counts`; Backgammon: `pts`, `bar`, `dice`, `phase` ...).

`describe_event(text, names)` turns one of the game's own events (the `EVT` payload after the sequence number) into a line for the log: `"ann plays d3 (flips 2)"`. `names` are the seated players' names by seat. Return None for any event that is not the game's; return `""` to show nothing for an event the log does not need. The shared events (`JOIN`, `LEAVE`, `START`, `PASS`, `CHT`, `END resign|timeout|win|draw|abandoned`, `INVITE`, `UNINVITE`) are described by the adapters; describe `END` yourself only when the game's result has another form (Backgammon's `END win seat points kind`).

### The web page (HTML 3.2)

`web_parts(v, tid, link, seat, names, sel)` returns four strings of HTML: `(board, side, hint, actions)`. The table page puts the board on the left; the side panel, the status line followed by the hint, the clock, the actions, the log and the page's own links on the right. It is called only once `rows` is truthy. `v` is the parsed view, `tid` the table, `seat` the viewer's seat (-1 for a watcher), `names` the seated names (padded with `-`), `sel` what the player picked last (see `SEL_RE`), or `""`.

`link(path, **query)` makes a link to a page of this site that keeps the player's session (`s=`, for a browser without cookies), the action check (`x=`, which every page that changes something needs) and the display choices; build every link with it, never by hand. A move is a link to `/move.htm` with `t=tid` and `mv=<move>`: `link("/move.htm", t=tid, mv="d3")`. Backgammon's staged turns also use `/und.htm` and `/end.htm` (`link("/und.htm", t=tid)`). A two-step move (pick a piece, then its target) is a link back to `/table.htm` with `sel=` (Backgammon's checkers). A move typed into a form submits to `/move.htm` with a field named `mv`, or with the game's own fields (`MOVE_FIELDS`, below); put the query of `link("/move.htm", t=tid)` into hidden fields with `hidden_fields(link("/move.htm", t=tid))` from `web.py`, as `invite_html` does, so the form carries `x=` and `s=` too.

| Name | Meaning |
|---|---|
| `MOVE_RE` | A compiled regex matching every `mv=` value the page can send (Reversi: `^[a-z][0-9]{1,2}\Z`; end with `\Z`, not `$`, which also matches before a final newline). `/move.htm` sends a value on only if some game's `MOVE_RE` matches it; the core then checks the move. Never match a CR, LF or anything outside the move token characters. |
| `SEL_RE` | A compiled regex for `sel=` values, or None when the game picks nothing first. |
| `MOVE_FIELDS`, `move_from_fields` | Optional. A move typed into several form fields: `MOVE_FIELDS` is the fields' names in order (1 to 6 names of 1 to 8 letters a-z, none of the site's own: `s`, `x`, `t`, `mv`, `sel` ... as `RESERVED_FIELDS` in `adapters/games/__init__.py` lists), and `move_from_fields(values)` takes their values in that order and returns the move, or None when they do not make one. A `/move.htm` (or `/check.htm`) request without `mv=` looks up the table's game, checks every field (printable ASCII, at most 32 characters, given at most once; spaces round it are trimmed; a field left out is `""`), builds the move and sends it only if the game's `MOVE_RE` matches it. Cannon Fire: `["angle", "power", "dir", "dist"]` makes `FIRE 45 60` from its shot form (angle and power) or `MOVE L 3` from its move form (dir and dist), and nothing from a mix of the two; Grid Words: `["sq", "dir", "letters"]` (a start square, a select of `H` or `V`, the letters) makes `PLAY 8 8 H HELLO`. The plugin still checks the move itself. |
| `WEB_CHECK` | Optional, `True` for a game whose page can preview a move with `CHK` (Grid Words). A form or link to `/check.htm` (the same `t=`, `mv=` or `MOVE_FIELDS`, the same checks as `/move.htm`) sends `CHK` for that table and shows the table page again, with `v["check"] = (move, reply)` in the parsed view: `reply` is the core's answer about the move (`OK ...` or `ERR ILLEGAL reason`), or None when the core said something else (not seated, no game running). `web_parts` shows it; with None it may fall back on what the page can work out itself. That page also gets the move as `sel` when `SEL_RE` takes it, so the forms keep it, and it does not refresh itself. A game without `WEB_CHECK` gets an error page from `/check.htm`. |
| `images()` | `{path: GIF bytes}`: the game's tiles (squares, pieces, dice), which `board_image` draws with. They are also served at those paths, made once on the first request, with a day's `Cache-Control`. New games put them under `/img/<game>/` (Reversi's are at `/img/*.gif` and Backgammon's at `/img/bg/`, where they were before the registry). `web.make_gif(w, h, palette, pixels)` writes a GIF of up to 8 colors. A game that draws everything itself returns `{}` (Cannon Fire), as does one with no images (Rummy Tile). |
| `board_image(v, seat, sel)` | Optional, and expected of every game with a board: the whole board as one image, an `adapters.board.Canvas` (below). |
| `board_click(v, seat, sel, x, y)` | Optional: for a board that is clickable anywhere (`canvas.anywhere = True`), the link a click at the pixel `x, y` stands for, as `(path, {query})`, or None. Cannon Fire drives the tank to a point on the ground within a move's reach, `("/move.htm", {"mv": "MOVE R 3"})`, and aims the barrel at any other point: `("/table.htm", {"sel": "60-50"})`. |
| `web_events(v, events)` | Optional: adds to the parsed view what the board shows from the table's events (every `EVT` payload so far, in order). Cannon Fire keeps the last shot's path (`TRJ`), burst (`HIT`) and dug columns (`ZCH`) in `v["trail"]`, `v["burst"]`, `v["dug"]`. The web calls it for the page and for the board image alike. |

#### One image per board

A board is one GIF, not a table of small ones: each image costs a browser a request and, with HTTP/1.0, a new connection, and a firewall that limits new connections per address cuts off a page of dozens of images. `board_image(v, seat, sel)` draws the board on a `Canvas` and returns it; `web_parts` shows it with

```python
board = board_html(board_image, v, tid, link, seat, sel, "Reversi board")
```

`adapters/board.py` has what a board needs, in pure Python:

- `Canvas(w, h, bg=(r, g, b), images=images)`: up to 256 colors, added as they are used. `tile(path, x, y)` draws one of the module's own `images()` (decoded once); `fill`, `frame`, `line` (with `width` and `dash`), `disc` (a ring with `r_in`), `polygon`, `put`; `text(x, y, s, rgb, font=FONT, align=...)` in a 5x7 font (letters, digits, a little punctuation; `SMALL` is 3x5 digits); `labels(x0, y0, cell, cols, rows, rgb)` writes column letters and row numbers round a grid.
- `area(shape, coords, alt, path, **query)`: a place to click: `"rect"` (x1, y1, x2, y2, inclusive), `"circle"` (x, y, r) or `"poly"` (x1, y1, x2, y2, ...), standing for `link(path, t=tid, **query)`; `alt` names it (`"d3"`, `"13 to 18"`). Paths are `/move.htm`, `/table.htm`, `/und.htm` and `/end.htm`. Areas may overlap; the first one listed wins, as in browsers.
- `canvas.anywhere = True` when every point is clickable (the module's `board_click` answers).

`board_html` renders the canvas into a GIF (LZW, `encode_gif`), keeps it in a small cache (the last few boards of each table, about 1.5 MB in all) and writes:

```html
<a href="/click.htm?t=..&q=..&st=..&d=..&x=.."><img src="/board.gif?t=..&q=..&st=..&d=.." width=W height=H border="0"
 alt="..." ismap usemap="#board"></a>
<map name="board"><area shape="rect" coords="112,80,143,111" href="/move.htm?...&mv=d3" alt="d3"> ...</map>
```

- `USEMAP` (the `<map>`) serves browsers with client-side image maps; every `href` comes from `link()`, so it carries `x=` and `s=`.
- `ISMAP` is for browsers without them (Mosaic, MacWeb, early Netscape): the browser adds `?x,y` to `/click.htm`'s link, and the web extension looks the point up in the same areas (or asks `board_click`) and answers as if that link had been followed. A point outside every area, or a click from a page whose board has changed since, shows the table again. `/click.htm` is an action: it needs `x=`.
- The image's URL names the table, the view's sequence number (`q`), the seat (`st`), `sel` and a digest of the view (`d`, without the clock), so a browser may keep it: `/board.gif` sends `Cache-Control: private, max-age=86400` and `Last-Modified`, and answers `If-Modified-Since` with 304. A page that refreshes itself on an unchanged board costs one request.
- `/board.gif` and `/click.htm` check the session and work the view out as `/table.htm` does (the seat from the session, a watcher by `SEE`), so an image is always drawn from the requester's own view: a URL copied from another seat gives the requester's own board, not kept by the browser. Hidden pieces (a fleet, a rack) are drawn only from the seat's own view, as the page's text is.
- With at most 40 areas, `board_html` also lists them as text links under the image, for browsers with images off; pass `text=False` when `web_parts` has its own move list or a form (`<form action="/move.htm">` with a field `mv`), as every game with many areas does.

Keep a board image small: a palette GIF of a few KB, under about 640 pixels wide, drawn in a few milliseconds. Draw clear outlines, so pieces stay apart from the board when a browser dithers to black and white, and never let color be the only mark (a letter, a shape, a count). The page itself then has one image; small decorations of their own count against the limit of four images a page that `web_page` checks.

HTML 3.2 rules, as for every page here (the test helpers check them):

- Tables, `img`, `a href`, `b`, `i`, `tt`, `br`, `p`, `font size` and `color`, `table bgcolor`, `td align` and `width`. No CSS, no `style=`, `class=` or `id=`, no `div` or `span`, no script, no frames.
- Every `img` has `width`, `height`, `alt` and, inside a link, `border="0"`. A page shows at most four images; the board is one.
- ASCII only. Entities are named (`&amp;` `&lt;` `&gt;` `&quot;` `&nbsp;`) or decimal (`&#39;`); 1997 browsers do not know hex ones.
- Pass every text that comes from a player, the view or the core (a `CHK` answer) through `esc()`; `a(href, html)` makes a link (its text is not escaped).
- Forms use `method="get"`, plain `input` fields and `select`, and a submit button; one form per action (HTML 3.2 has no per-button action).
- The page works with no JavaScript, and with images off (the `alt` texts, the move list or form under the board, and the side panel carry the game).
- Keep the board under about 640 pixels wide.

### Telnet: the screens

A game has two screen classes, built on the framework in `adapters/telnet.py`:

- `AnsiScreen`, a subclass of `telnet.AnsiGame`: the full screen for the `pc`, `utf8` and `ansi` modes, with arrows, Enter, shortcut letters, the command line and the mouse.
- `PlainScreen`, a subclass of `telnet.PlainGame`: the board printed line by line for the `plain` mode, with typed moves.

Reversi puts what both share in a base class (`ReversiBase(telnet.GameBase)`: `parse`, `describe`, `move_word`, the help text and the last move), then `class AnsiReversi(ReversiBase, AnsiGame)` and `class PlainReversi(ReversiBase, PlainGame)`, and ends with `AnsiScreen, PlainScreen = AnsiReversi, PlainReversi`. The screens follow docs/design.md: two modes, one meaning per letter, the shared layout, offers and confirmations as dialogs, and help in two pages.

`GameBase` keeps the table: `self.v` (the parsed view), `self.names`, `self.me` (the player's seat, -1 when watching), `self.log`, `self.over` (the end text, once the game ends), `self.last` (the last move as a sentence), `self.tid`. It does the core requests (`refresh`, `pull`, `move(token)`, `staged("UND" or "END", what)`, `chat`, `resign`), runs the commands every table takes (`run_command`), and answers `playing()`, `my_turn()` and `clock()`. Set or override in the shared base:

| Hook | Reversi's |
|---|---|
| `parse` | `staticmethod(parse_view)` |
| `GAME` | the game's name, for the title bar and help: `NAME` |
| `describe(text)` | its `describe_event`, then `telnet.describe_event` for the shared events |
| `move_word(cmd, words)` | Whether a typed line is a move here, as the move it stands for, or None: `cmd if SQUARE_RE.match(cmd) else None`. `cmd` is the first word in lower case, `words` the whole line's words as typed. Telnet uses it only for its hints: the sentence line while a move is typed, and plain mode's answer to a move typed without its slash. A typed move such as `/d3` runs on the server with every other command line (`CMD`), where the plugin's `typed` reads it (docs/plugins.md). |
| `COMMANDS`, `MOVE_EXAMPLE` | The game's own commands, `{name: what the sentence line says while it is typed}`, with the shared names (docs/design.md, The actions), and a typed move to show, as `/d3`. One name for each action, with no other words for it. |
| `game_command(cmd, args)` | A command that is more than a move, such as Grid Words' `/colors`: True when done. |
| `play_word(word)` | A move the board's keys make (the default: `self.move(word)`). Backgammon's sends `undo` and `end` as `UND` and `END`, and its drop asks first. |
| `offer()` | An offer waiting for this player's answer, such as a double or a draw offer, or None. The full screen opens it as a dialog with Accept and Decline, and plain mode asks at the prompt. |
| `last_move(text)`, `view_last()` | An event as the last-move sentence ("ann placed a disc on d3 and flipped 2."; `self.actor(seat)` says "You" for the player), and the same from the view alone where it carries the last move. |
| `HELP_MOVING`, `HELP_KEYS`, `HELP_COMMANDS`, `HELP_REFERENCE`, `reference()` | Help's two pages. Page 1 has Moving, Keys for the game, Commands and Every game, and page 2 is the reference, ending with the commands every table takes and the tutorial. At most 17 lines a page and 70 columns a line. |
| `playing()`, `my_turn()` | (the defaults.) Override when "my turn" needs more than `turn` and `moves`, as Backgammon's phases do. |
| `on_event(text)` | Called for every new event; Backgammon keeps the last roll. |

`AnsiScreen` draws the whole screen in `compose()`, which returns a `telnet.Canvas`; the framework sends only the cells that changed. The hooks:

| Hook | Reversi's |
|---|---|
| `compose()` | `title_bar(cv, g, self.title_text())`, the board, the panel by `draw_panel_parts` (the players with their score, then the turn, the clock, the last move and the game's facts, then the chat and log), row 20 for the game, the sentence line on row 21 from `self.sentence(self.hint)`, `input_line` on row 22, and `keys_line(cv, self.keys(...))` on row 23. While the game has not started, `draw_waiting(cv, x, y)` puts a box over the board. |
| `hint()` | The sentence line: `waiting_hint()`, `over_hint()`, `watch_hint()` and `their_hint(seat)` say the shared sentences, and the game says what Enter does on your turn and names the shortcuts that apply, with `self.kn(letter, command)` for a key that may be a movement letter. |
| `keys(arrows, enter, actions, cancel)` | The keys line's slots in the shared order. actions is `[(letter, label, command)]`, in the order of docs/design.md. |
| `cursor_at()` | Where the terminal's cursor sits (on the cursor square), or None. |
| `snap()` | After a new view: put the cursor somewhere useful (on a legal square when it becomes the player's move). Remember `self.was_my_turn`. |
| `on_arrow(k)` | `UP`, `DOWN`, `LEFT`, `RIGHT` move the cursor. |
| `on_enter()` | Enter or Space in game mode: place a disc on the cursor square. |
| `on_click(value)` | A mouse click on a hot area: `cv.hot_area(x, y, w, h, ("sq", col, row))` while drawing makes the cells clickable, and the click comes back as that tuple. Keys line items are clickable already (as their keys). A click does what Enter does on that place. |
| `on_escape()` | Esc in game mode. The default leaves for the lobby; Backgammon first puts a picked-up checker back. |
| `HOTKEYS`, `hotkey(k)` | The game's shortcut keys. The default handles `r` (the server's `/resign`, which asks first), `t` (the chat screen) and `?` (help); a game adds its own letters to `HOTKEYS`, each with its one meaning from `telnet.SHORTCUTS`, and handles them, calling `super().hotkey(k)` for the rest. Any other letter does nothing and says so. With letter movement keys (IJKL, ESDF, WASD or HJKL, the session's choice) the four letters always move the cursor, and a shortcut on one of them is given as its command. |
| `typed_reset()` | Before a command line runs on the server: put down a pick in progress, since the board may change. Backgammon drops a picked-up checker, and Grid Words leaves entry mode. A key whose action the server asks about first, such as P to pass in Grid Words, sends its command with `shared("pass")`, so the dialog has the server's words. |
| `LOG_TOP`, `LOG_ROWS` | Where `draw_log` writes the log (`draw_panel_parts` sets them). |

The `PlainScreen` hooks are `lines()` (the screen as a list of lines: `plain_header()`, the board with `side_text(players, facts)` beside it by `beside`, and `plain_log()`), `turn_prompt()` (Reversi's: `Your turn. Type a square such as /d3.`) and `BOARD_KINDS` (the events that reprint the board; others are printed as a sentence).

### Telnet: fitting 80x24 in every glyph mode

The screen is 80 columns by 24 rows and must fit in every mode:

- Row 0 is the title bar, rows 1 to 19 the board and the panel, row 20 the game's own line, row 21 the sentence line, row 22 the command line, row 23 the keys line (79 columns: the last cell of the screen is never written, so nothing scrolls). Writing outside the canvas sets `cv.clipped`, and the tests fail on it.
- Work out the budget first. Reversi: 4 columns per cell and 2 rows per board row with grid lines, falling back to 1 row per board row when `2n + 1` would pass 17 rows; the panel takes what is left on the right (`max(40, board right edge + 3)` to column 78). Backgammon: a 47-column board, the panel from column 49. A 15 by 15 board at 3 columns a cell is 47 columns with its frame and 17 rows; a 19 by 19 one has no room for grid lines between rows.
- Names are up to 12 characters, and the tests use 12-letter names: cut every text to its space (`text[:inner]`).
- The modes are `pc` (CP437 characters, sent as CP437), `utf8` (the same shapes in Unicode), `ansi` (ASCII with color), `plain` (ASCII, no escape codes). `self.g` is the mode's frame and disc set (`telnet.glyph_set`). For the game's own pieces keep a table per mode like `bg_glyphs(mode)`: in `pc` and `utf8` only characters that exist in CP437 (box drawing, blocks, `·`, `»`, `■`), in `ansi` and `plain` ASCII only. Pieces must differ in shape, not only in color (`(O)` and `(X)`), so monochrome terminals and `plain` work.
- Colors are SGR strings (`telnet.A_*`, each starting from `0`); a cell's background can show the cursor (`46`) and the board (`42`).
- The plain screen scrolls, so it may be longer than 24 lines, but every line is at most 80 columns and ASCII, and the board and its side text should fit on about 20 lines.

### Checked when the module loads

`adapters/games/__init__.py` lists the required names in `REQUIRED`; `games.problems(module)` says what a module lacks. `tests/test_games.py` checks, for every module: nothing missing, the plugin and the type spec exist, `ABOUT` and `NAME` are ASCII, image paths do not clash and sit under `/img/<game>/`, and every screen of the game, in every mode, fits 80x24 and says "Waiting for an opponent" before the game starts.

## 4. Tests

Write `tests/test_<game>.py` in the form of `tests/test_backgammon.py` for the plugin, and add the frontends. `tests/game_checks.py` has the frontend checks; a game's tests give them views:

```python
from game_checks import ansi_screens, plain_screen, web_page

from adapters.games import gridwords as G

class Screens(unittest.TestCase):
    def test_full_screen(self):
        for me in (0, 1, -1):                  # each seat, and a watcher
            for mode, (shown, reqs) in ansi_screens(self, G, MIDGAME, me, keys=["RIGHT", "ENTER"]).items():
                ...                            # what should be on the screen, and the MOV requests sent
```

- `ansi_screens(tc, module, view_lines, me, keys)` draws the full screen in `pc`, `utf8` and `ansi` with the keys pressed (as the telnet reader reports them: `"UP"`, `"ENTER"`, `"ESC"`, single characters, `("CLICK", x, y)`) and checks every screen: 24 rows of at most 80 columns, nothing clipped or scrolled, every character in the mode's character set. It returns each mode's screens as text and the requests sent to the core.
- `plain_screen(tc, module, view_lines, me)` checks the plain screen (80 columns, ASCII) and returns its text.
- `web_page(tc, module, view_lines, seat)` checks the web parts against the HTML 3.2 rules above (every image is the module's or its board image, at most four, every map area well formed) and returns the HTML.
- `board_image(tc, module, view_lines, seat, sel)` draws the board image and checks it: a GIF that decodes to the canvas's size and pixels, every area inside it and leading to a page `/click.htm` may answer, with a move `MOVE_RE` takes or a `sel` that `SEL_RE` takes. It returns the canvas: `used` lists the tiles drawn with their places, `words` the text, `areas` the map, so a test can check what the board shows without reading pixels.

Cover at least: the waiting view, the opening, a move for each seat and a watcher, a crowded or extreme position (the longest texts, the tallest stacks), the end of the game; the keys, a click and a typed move each sending the right `MOV`; every `MOVE_RE` token the web page links to; with `MOVE_FIELDS`, `move_from_fields` on good and bad values and a move sent through `/move.htm` from the fields by a browser signed in with a session (`tests/test_cannonfire.py` and `tests/test_navalcombat.py` use `Stack` and `Browser` from `tests/test_adapters.py`; `tests/test_gridwords.py` runs the core and the web adapter in its own process); `web_page` checks that every link and form carries `s=` and `x=`; with `WEB_CHECK`, `web_parts` given a `v["check"]` of each kind (`web_page(..., check=(move, reply))` in `tests/game_checks.py`). For the plugin: the rules, the events, a whole game over the protocol (`test_core.Client`). plugins.md also asks for a directory of recorded games per plugin; no plugin has one yet.

Run everything before committing:

```sh
python3 -W ignore -m unittest discover -s tests -p "test_*.py"
```

CI runs only the fast tests (`PARLOR_TESTS=fast`). Mark a game's whole games through a real server and its other long tests `@slow` (`tests/speed.py`): they run nightly and before each release.

## Reversi, file by file

- `plugins/reversi/plugin.py`: `INFO` (`reversi`, version 1, `standard`, 2 players, option `size`), `new` (the four center discs), `view` (`BRD`, `R` rows, `C` counts, `W`, `S`), `moves`, `apply` (`PUT seat sq FLP ...`, `PASS`, `END`), `result`.
- `docs/types/reversi-1.md`: the rows encoding, `C`, the square tokens, `PUT`/`PASS`/`END`, the sizes.
- `adapters/games/reversi.py`, top to bottom:
  - `KEY`, `NAME`, `ORDER`, `PLAYERS`, `ABOUT`.
  - `parse_view` (rows, counts, turn, moves, clock, status) and `describe_event` (`PUT` only; `PASS` and `END` are shared).
  - `MOVE_RE`, `SEL_RE = None`, `images()` (four 32x32 squares), `board_image` (the squares drawn on one canvas with the letters and numbers, an outline round each disc, an area per legal square linked to `/move.htm`) and `web_parts` (the board by `board_html`, the two players with their disc counts, the hint "click a yellow dot").
  - `ReversiBase` (the help text, `move_word`, `last_move`), `AnsiReversi` (geometry, `snap`, `compose`, `draw_board`, `draw_panel`, `hint`, `cursor_at`, then the key hooks), `PlainReversi` (`lines`, `turn_prompt`, `BOARD_KINDS`) and `AnsiScreen, PlainScreen`.
- Tests: the plugin and the protocol in `tests/test_core.py`; the screens in `tests/test_adapters.py` (`TelnetScreens`: every board size in every mode and seat, `test_plain_board`); a whole game by web and by telnet there too.
