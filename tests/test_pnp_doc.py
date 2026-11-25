"""docs/pnp.md, the protocol guide: every sample session in it is replayed against a fresh server and
must match line for line (tools/pnp_examples.py wrote them; a placeholder such as <secs> matches any
one word). Run tools/pnp_examples.py to record them again after a change to the server or a game."""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import pnp_examples as P  # noqa: E402


def doc_blocks():
    with open(P.DOC, encoding="utf-8") as f:
        return P.blocks(f.read())


class Samples(unittest.TestCase):
    def test_every_block_has_a_scenario_and_every_scenario_a_block(self):
        blocks = doc_blocks()
        self.assertEqual(sorted(blocks), sorted(P.SCENARIOS))
        for name, lines in blocks.items():
            self.assertTrue(lines, "%s: empty; run tools/pnp_examples.py" % name)
            for l in lines:
                if not l.startswith("#"):
                    self.assertRegex(l, r"^[A-Z][<>] ", name)
                    self.assertLessEqual(len(l) - 3, 78, l)       # a protocol line fits 80 bytes with CR LF

    def test_samples_replay(self):
        from parlord import frontends
        for name, lines in doc_blocks().items():
            if P.SCENARIOS[name].get("ext") and not frontends.supported():
                continue                                        # the extension channel needs Python 3.13
            with self.subTest(sample=name):
                problems = asyncio.run(P.replay(name, lines))
                self.assertEqual(problems, [])

    def test_replay_notices_a_changed_line(self):
        lines = doc_blocks()["errors"]
        changed = [l.replace("no such table", "no table") for l in lines]
        problems = asyncio.run(P.replay("errors", changed))
        self.assertEqual(len(problems), 1)
        self.assertIn("no such table", problems[0])

    def test_no_real_credentials(self):
        """Only the made-up passwords and example.org addresses appear in the samples."""
        text = "\n".join(l for lines in doc_blocks().values() for l in lines)
        for l in text.split("\n"):
            if " AUT password " in l or " AUT confirm " in l or " AUT current " in l:
                value = l.split(" ", 3)[3]
                self.assertTrue(value in ("y", "reset", "<mailed>", P.PASSWORD, P.NEW_PASSWORD) or "swordfish" in value
                                or value == "short", l)
            if "@" in l:
                self.assertRegex(l, r"@example\.org")


class Served(unittest.TestCase):
    def test_the_docs_site_has_the_guide(self):
        from docsite import build
        self.assertIn("/docs/pnp.html", build.doc_files())
        self.assertIn('href="docs/pnp.html"', build.relativize("docs.html", build.docs_index(build.doc_files())))
        from adapters.mdhtml import to_html
        with open(P.DOC, encoding="utf-8") as f:
            html = to_html(f.read())
        self.assertIn("<pre>", html)
        self.assertNotIn("&#x", html)
        self.assertIn('<a name="part-2-the-games">', html)

    def test_linked_from_readme_and_protocol(self):
        for doc in ("README.md", os.path.join("docs", "protocol.md")):
            with open(os.path.join(ROOT, doc), encoding="utf-8") as f:
                self.assertTrue("pnp.md" in f.read(), doc)


if __name__ == "__main__":
    unittest.main()
