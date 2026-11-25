# Grid Words colors

Grid Words is a crossword-building tile game, a kind of game that is old and free for anyone to make. The best-known commercial game of the kind also has a well-known look, and ParlorNet's boards keep clear of that look. The code is `adapters/palette.py`; the tests are `tests/test_palette.py`.

## What to avoid, and why

The commercial game's board is instantly recognized by its colors: red triple-word squares, pink double-word squares, dark blue triple-letter squares, light blue double-letter squares, beige or cream letter tiles, and a green or tan board. Taken together, a product's colors and visual arrangement can be protected as trade dress (in the United States under the Lanham Act; elsewhere as get-up or as a registered shape or color mark), separately from its name and from copyright. A game's rules and the idea of building words from letter tiles are not protected this way, but a look that makes people think of a particular product can be.

Public sources on this game in particular:

- In 2008 the makers of the commercial game sued the authors of Scrabulous, a Facebook word game. Commentary at the time noted that the board's colors and design could be claimed as trade dress, alongside the name. The authors relaunched the game as Wordscraper with a new name, a different board layout and different colors, so as not to copy the original's look. ([Sitepoint](https://www.sitepoint.com/scrabulous-is-toast-did-hasbro-really-have-a-case/), [IPOsgoode](https://yorku.ca/osgoode/iposgoode/?p=1287), [Digital Trends](https://www.digitaltrends.com/web/hasbro-sues-scrabulous-makers/))
- In the United Kingdom, a trade mark registration for the game's three-dimensional tile was held invalid in 2012 because it did not define a sign precisely enough. That judgment is about how a mark is registered, not a license to copy the look. ([EIP](https://eip.com/uk/case-reports/zynga-hit-triple-word-score-as-mattel-s-scrabble-tile-claim-fails/), [Mondaq](https://mondaq.com/uk/trademark/216112/3-d-scrabble-tile-not-a-sign-capable-of-graphical-representation))
- No public source sets out a palette that is safe to use. The working rule is the one Wordscraper and later word games followed: choose a look of your own that nobody would take for the original.

So ParlorNet's defaults avoid every color of that set in its role and in any other role: no red or pink word squares, no blue letter squares, no beige tiles, and no green or tan board. The board's layout of premium squares stays the usual one, because it is part of the rules a player expects (docs/types/gridwords-1.md); this note is about color only.

Before this change, the web board and the telnet screen used exactly the colors above. Both now use the new default.

## The default palette: Slate

| Element | Color | |
|---|---|---|
| Board (empty squares) | `#2F343B` | dark slate gray |
| Triple word | `#E07B00` | amber |
| Double word | `#F5D22E` | yellow |
| Triple letter | `#7B3AA8` | violet |
| Double letter | `#C9B6F5` | lavender |
| Tiles | `#FAFAFA` | near white |
| Letters | `#15181C` | near black |

The word squares are warm colors and the letter squares violets, the stronger color for the triple, all on a dark board with white tiles. Every other color is worked out from these seven (`derived()`): the grid lines, the ink of each premium's label (black or white, whichever reads better), a blank's letter, the start arrow.

Color is never the only sign:

- Every premium square carries its name: `3W`, `2W`, `3L`, `2L` (on the web board image, in the modern client and on telnet).
- Word squares have a frame inside their edge; letter squares do not.
- A blank shows its letter in an accent color and has no value in the corner.
- The modern client and the web pages also give a legend in words.

## How it is checked

`tests/test_palette.py` checks the default and every preset:

- **Kept clear of the look above.** Each premium is at least 14 apart (CIEDE2000) from all four of red, pink, dark blue and light blue; the tiles at least 14 from beige; the board at least 12 from green and tan. (About 2 is a just-noticeable difference; over 10 is plainly another color.) The board images no longer contain any of the old colors.
- **Color-vision deficiencies.** The board, the four premiums and the tiles must differ from each other by at least 12 as seen with normal vision and at least 10 as simulated for protanopia, deuteranopia and tritanopia (the Machado, Oliveira and Fernandes 2009 model at full severity). For Slate the closest pair under deuteranopia, the hardest case, is the two word squares, which still differ in lightness.
- **Contrast.** Each premium's label has a WCAG contrast of at least 4.5 on its square, and the letters at least 7 on the tiles.

## Presets

| Preset | For | Board | Word squares | Letter squares | Tiles |
|---|---|---|---|---|---|
| Slate (default) | everyday play | dark slate | amber, yellow | violet, lavender | near white |
| Daylight | bright rooms, light themes | pale gray | dark amber, gold | deep violet, lavender | dark slate, white letters |
| High contrast | low vision | black | orange, yellow | violet, pale violet | white |
| Grayscale | monochrome screens and print | white | dark and mid gray | gray and light gray | black, white letters |

All four pass the same checks.

## A player's own colors

A player can choose a preset, or a color of their own for each of the seven elements. A player's own colors are not checked against anything; only the defaults and presets keep to the rules above.

The choice is kept with the player on the server with `PRF` (docs/protocol.md), key `gwcolors`, in each server extension's namespace (ParlorNet Play and the web pages share one). The value is a preset's name (`slate`) or `custom:` and the seven colors in hex, in the order board, triple word, double word, triple letter, double letter, tiles, letters:

```
custom:2f343b,e07b00,f5d22e,7b3aa8,c9b6f5,fafafa,15181c
```

- **The modern client** (`/play/`, docs/modern-client.md): Settings, with the presets, a color picker per element and a live preview. It shares the web pages' namespace, so a choice made in either applies to both.
- **The web pages**: Grid Words colors (`/colours.htm`), linked from the lobby's Your account and from every Grid Words table. HTML 3.2: a radio button and a preview image per preset, and seven `#RRGGBB` fields for the player's own. The board image is drawn in the player's colors.
- **Telnet**: type `colors` (or `colours`) on a Grid Words table. Pick a preset, or pick each element from the eight ANSI colors (the letters from sixteen). A preset is shown in the nearest ANSI colors, taking the next nearest where two kinds of square would otherwise share one, so every kind still differs; a player's own picks are shown as they are. Telnet keeps its own choice, apart from the web's.
