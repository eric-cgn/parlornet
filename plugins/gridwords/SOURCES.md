# Sources: the Grid Words word list

Where `words.txt` in this directory comes from, and how it was checked. Checksums are SHA-256.
Fetched 2026-11-21 from the Internet Archive's Wayback Machine.

| What | Version | Source | Checksum | Where |
|---|---|---|---|---|
| ENABLE word list (Enhanced North American Benchmark LExicon), `WORD.LST` | 1.0, 1997-09-07 (the first public release; M. Cooper with Alan Beale). License: public domain ("formally released into the Public Domain", the archive's README) | `enable10.zip`, the original distribution archive from the authors' page, as captured by the Wayback Machine on 2000-08-15: https://web.archive.org/web/20000815073537/http://personal.riverusers.com:80/~thegrendel/enable10.zip | archive `43c20e320ff36869bc08c5a0bee79c83419cd67f8e136242db883e4c0e76844c` (480947 bytes, every member's CRC good); `WORD.LST` in it `bc03faa228574cb9a68c5b8500b24657617741c8f661aedbc035b99750dea5ad` (1916186 bytes, CRLF line endings) | `plugins/gridwords/words.txt` |
| Cross-check: `enable1.txt` | the same list, as published by the National Puzzlers' League | https://web.archive.org/web/20071218051822/http://www.puzzlers.org/pub/wordlists/enable1.txt (the Wayback Machine, 2007-12-18) | `3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89` (1743363 bytes, LF line endings) | not installed |

## The installed file

`words.txt` is `WORD.LST` from `enable10.zip` with its line endings changed from CRLF to LF, and
nothing else changed:

- SHA-256 `3f16130220645692ed49c7134e24a18504c2ca55b3c012f7290e3e77c63b1a89`, 1743363 bytes
- 172,823 words, one per line, `aa` to `zyzzyvas`; sorted, no duplicates
- plain ASCII, lower-case letters a-z only, LF after every line
- the plugin takes the words of 2 to 15 letters: 168,551 of them (the longest in the list has 28)

## Cross-check

`WORD.LST` from `enable10.zip`, with LF line endings, is byte for byte the same as the National
Puzzlers' League's `enable1.txt` (the same SHA-256, above). The Wayback Machine holds that file
unchanged (one content digest) in every capture from 2007 to 2018.

## Not used

- `enable2k.zip` (ENABLE2K, 2000-04-14; https://web.archive.org/web/20051109231624/http://personal.riverusers.com:80/~thegrendel/enable2k.zip,
  archive `44e2dd5c16c14336e8d4075a2f625af55a47cbb8b9f9ebcb7fcc3b5b1e1f0e5e`): a later revision.
  Its `WORD.LST` (`f1e9b50e535a31d0d8451f7b373bb7966637d225e256a7c485983a0c7545fcd3`, CRLF)
  has 173,528 words: 798 added to 1.0 and 93 removed.
- `CHECKSUM.EXE` in `enable10.zip` (the authors' DOS checksum program) was not extracted or run.
