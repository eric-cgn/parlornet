# Rummy Tile colors

Rummy Tile Classic is tile rummy: numbered tiles in four colors, laid in runs and groups on a shared table. Games of this kind are old and free for anyone to make. The best-known commercial tile-rummy game also has a well-known look, and ParlorNet Play's board keeps clear of it, as the Grid Words board keeps clear of the best-known crossword game's (gridwords-colours.md has the background on why a product's look matters apart from its rules). The code is `adapters/rtpalette.py` and `adapters/play/game-rummytile.js`; the tests are `tests/test_rummytile_colours.py` and the Rummy Tile tests in `tests/test_play.py`.

## What to avoid

The commercial game is recognized by its cream or ivory plastic tiles standing on a slanted rack in front of each player, and by a smiling face on its jokers. Its name and box are its own as well. Numbers printed in four colors are not its own: Okey and other tile games have long been played that way. ParlorNet uses none of the commercial game's marks:

- The game is called Rummy Tile Classic everywhere, and the rules are written in ParlorNet's own words.
- The joker is a five-pointed star. It never shows a face.
- The tiles are flat, in a cool light gray by default, never cream or ivory. Each number is drawn in its color's shade, and the shades are ParlorNet's own: a deep red, a mid blue, near black and a dark gold.
- The rack is a flat tray under the table, like the other Play boards, not a slanted stand.

The four color families stay in the protocol as they are: `R` red, `B` blue, `K` black and `Y` yellow (types/rummytile-1.md). They are the traditional four colors of Okey, a much older tile game played with the same 106 tiles ([Okey](https://en.wikipedia.org/wiki/Okey)). The shades on the screen are ParlorNet's own and a player can change them.

## The tile

A tile is drawn flat, slightly raised from the table, in the tile color. Its number fills the middle of the tile in bold, drawn in the family's color, and nothing else is on it. A joker carries a five-pointed star in the ink color and no number, which is enough to tell it from every other tile.

Color carries the tile's family: a red 7 and a blue 7 differ only in the color of the number. So every preset keeps the four colors readable on the tile and clearly apart from each other, with normal color vision and with each of the three common kinds of color blindness, and the color-blind-safe and high-contrast presets are there for players who need more than the default gives. A screen reader names each tile in words ("red 7", "joker"), and the panel's tile buttons and typed steps use the protocol's letters (`R7`). The sets on the table carry no words or marks, and their cues do not depend on hue alone: a set that is not valid yet lies on a red mat that is filled much more strongly than the others, with a red edge; a set made or changed this turn has only a thin light edge; and a set that may not be touched before the first meld has dimmed tiles. The line over the buttons says in words how the turn stands, the first press of Done says why a turn would not count, and the panel and screen readers name each set's kind.

The numbers have to be dark enough to read on a light tile, so the yellow family is a dark gold rather than a bright yellow. With red-green color blindness, red and yellow look much alike in hue, so in every preset the red is kept clearly darker than the gold and they are told apart by lightness.

## The default palette: Slate

| Element | Color | |
|---|---|---|
| Table | `#24313C` | dark slate blue |
| Tiles | `#E0E8F5` | cool light gray |
| Joker star | `#15181C` | near black |
| Red (R) | `#A8241C` | deep red |
| Blue (B) | `#2563C9` | mid blue |
| Black (K) | `#1B1E23` | near black |
| Yellow (Y) | `#A07C00` | dark gold |

The rack tray is the table color a little lighter (or darker on a light table), and the words and mats on the table are white or near black, whichever reads better on it. Both are worked out from the seven (`derived()`).

The contrast of each number on the tiles, as WCAG measures it, and the closest two of the four colors (CIEDE2000) with normal vision and with each simulated color blindness:

| Preset | Red | Blue | Black | Gold | Normal | Protanopia | Deuteranopia | Tritanopia |
|---|---|---|---|---|---|---|---|---|
| Slate | 5.8 | 4.6 | 13.6 | 3.2 | 31.7 | 22.3 | 14.5 | 19.9 |
| Color-blind safe | 7.3 | 4.6 | 18.8 | 3.3 | 32.4 | 24.5 | 20.9 | 21.4 |
| High contrast | 9.6 | 6.3 | 21.0 | 4.6 | 33.1 | 22.0 | 15.9 | 21.3 |

## Presets

| Preset | For | Table | Tiles | Red, blue, black, yellow |
|---|---|---|---|---|
| Slate (default) | everyday play | dark slate blue | cool light gray | deep red, mid blue, near black, dark gold |
| Color-blind safe | color-vision deficiencies | dark gray | pale blue-white | deep red `#952818`, blue `#0072B2`, black, gold `#B47A00` |
| High contrast | low vision | black | white | dark red `#900000`, strong blue `#0047FF`, black, dark gold `#8F7200` |

The color-blind-safe preset takes its blue and black from the palette Masataka Okabe and Kei Ito proposed for figures that people with any common color-vision deficiency can read ([Color Universal Design](https://jfly.uni-koeln.de/color/)). Their vermilion and yellow are too light to read as numbers on a light tile, so the preset uses a deep red and a gold in their place, set further apart in lightness than in the default. The high-contrast preset draws every number at a contrast of at least 4.5 on white tiles.

## How it is checked

`tests/test_rummytile_colours.py` checks the default and every preset:

- **Kept clear of cream tiles.** The tile color is at least 7 apart (CIEDE2000) from four cream and ivory references and has no warm tint (its CIELAB b\* is not above zero); for the default it is at least 14 apart. About 2 is a just-noticeable difference; over 10 is plainly another color.
- **The numbers read on the tiles.** Each of the four number colors has a WCAG contrast of at least 3 on the tile color, the level WCAG sets for large text; the numbers are bold and about half the tile's height. In the high-contrast preset each has at least 4.5. The joker's star has at least 7, and the tiles have at least 3 on the table. Black stays near black (CIELAB L\* at most 15).
- **The four colors stay apart.** The tiles and the four family colors differ from each other by at least 12 with normal vision and at least 10 as simulated for protanopia, deuteranopia and tritanopia (the Machado, Oliveira and Fernandes 2009 model at full severity, as for Grid Words). In the color-blind-safe preset the four colors differ by at least 18 with each. For Slate the closest pair is red and gold under deuteranopia, about 14.5 apart, which differ plainly in lightness.

## A player's own colors

A player can choose a preset, or a color of their own for each of the seven elements, in Play's Settings under Rummy Tile colors, with a preview of four tiles and a joker on the table. A player's own colors are not checked; only the default and the presets keep to the rules above.

The choice is kept with the player on the server with `PRF` (docs/protocol.md), key `rtcolors`, in the namespace Play shares with the web pages. The value is a preset's name (`slate`, `cvd`, `contrast`) or `custom:` and the seven colors in hex, in the order table, tiles, joker star, red, blue, black, yellow:

```
custom:24313c,e0e8f5,15181c,a8241c,2563c9,1b1e23,a07c00
```

The Play extension serves the presets to the page as `/play/rummytile-colours.json`. The classic web pages and telnet draw Rummy Tile Classic in their own way and do not read this key.
