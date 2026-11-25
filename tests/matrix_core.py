"""parlord for tests/test_matrix.py: the same server, with the per-address sign-up limit lifted (every test
player connects from 127.0.0.1, and the matrix signs up more names than the limit allows from one address).
Run as python -m tests.matrix_core with parlord's arguments."""
import runpy

from parlord import auth

auth.SIGNUPS_PER_SOURCE = (100000, 600)
runpy.run_module("parlord", run_name="__main__", alter_sys=True)
