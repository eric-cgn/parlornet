"""Fast and slow tests.

Every test is fast unless it is marked @slow. The fast tests are the unit and protocol tests: the core's
requests and replies, each plugin's rules, the wire formats, the adapters' parsers, formatters and screens,
the docs site, and one end-to-end smoke test (test_smoke.py: the server starts, a player signs up and signs
in over the native protocol and plays a short game against the bot). CI runs only the fast tests, on every
pull request and every push (.github/workflows/ci.yml and dev.yml).

The slow tests play whole games through the real extensions and the bot, start many processes, wait for real
timers (turn clocks, idle limits, flood limits), or run long matrices (test_matrix.py). They run nightly and
by hand on dev (.github/workflows/slow.yml), and in release.yml before a release is tagged.

    python3 -m unittest discover -s tests                     # everything, fast and slow
    PARLOR_TESTS=fast python3 -m unittest discover -s tests   # the fast tests only, as CI runs them

A slow test is skipped when PARLOR_TESTS is fast, so the plain command still runs everything. Mark a test
class, or a single test, with @slow from this module:

    from speed import slow

    @slow
    class Service(Stack):
        ...

A class marked slow is skipped whole, setUpClass included. The mark is inherited, so put it on the classes
that hold the slow tests, never on a base class that fast tests share.
"""
import os
import unittest

FAST_ONLY = os.environ.get("PARLOR_TESTS", "").strip().lower() == "fast"


def slow(thing):
    """Mark a test class or a test method slow: skipped when PARLOR_TESTS=fast."""
    return unittest.skipIf(FAST_ONLY, "slow test (PARLOR_TESTS=fast)")(thing)
