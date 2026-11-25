"""Writes the docs site's rating marks, drawn by adapters/medals.py, into static/img/.

    python3 tools/medal_icons.py        # writes static/img/medal-gold.gif, medal-silver.gif,
                                        # medal-bronze.gif and unsupported.gif
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from adapters.medals import ICONS  # noqa: E402

OUT = os.path.join(ROOT, "static", "img")


if __name__ == "__main__":
    for name, draw in ICONS.items():
        path = os.path.join(OUT, name)
        with open(path, "wb") as f:
            f.write(draw())
        print(path)
