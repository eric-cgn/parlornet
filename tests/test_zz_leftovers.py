"""No test leaves a process behind.

unittest's discover runs the test files in name order, so this one runs last, after every other test class
has been torn down. It fails when a core, an extension, the bot or a plugin host that the run started is
still running (tests/procs.py keeps track of them), and then stops them, so the next run starts clean."""
import unittest

import procs


class Leftovers(unittest.TestCase):
    def test_no_process_is_left_running(self):
        left = procs.leftovers()
        if left:
            procs.stop_leftovers()
        self.assertEqual(left, [], "processes left running after the tests: %r" % (left,))
