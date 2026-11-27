"""The ParlorNet bot (docs/bot.md): a player for every game, run as a client of
the core like any other. It keeps one open table of every two-player game for
players to join, sits down at other tables only when invited, plays one simple
strategy per game (bot/games/), and leaves when the game is over.

    python -m bot --core 127.0.0.1:6590 --key /data/frontends/bot.key
"""
