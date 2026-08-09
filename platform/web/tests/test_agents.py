"""The chunker and the document hash, offline.

The agents themselves are exercised end to end by `rtf_platform.ingest` against the
cluster; what is worth unit-testing here is the pure text handling, because a chunker
that silently drops a paragraph produces an index that is quietly incomplete — the
failure that looks exactly like a working system with nothing to say.
"""

from __future__ import annotations

import unittest

from rtf_platform import agents, fleet


class Hashing(unittest.TestCase):

    def test_same_text_same_hash(self):
        self.assertEqual(agents.content_hash("abc"), agents.content_hash("abc"))

    def test_edited_text_is_a_different_document(self):
        self.assertNotEqual(agents.content_hash("abc"), agents.content_hash("abd"))


class Splitting(unittest.TestCase):

    def test_short_text_is_one_chunk(self):
        self.assertEqual(agents.split("one paragraph"), ["one paragraph"])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(agents.split(""), [])
        self.assertEqual(agents.split("   \n\n  "), [])

    def test_no_paragraph_is_lost(self):
        # The property that matters: everything in, everything findable.
        paragraphs = [f"paragraph number {i} " + "filler " * 40 for i in range(25)]
        text = "\n\n".join(paragraphs)
        joined = " ".join(" ".join(c.split()) for c in agents.split(text))
        for i in range(25):
            self.assertIn(f"paragraph number {i}", joined,
                          f"paragraph {i} vanished in chunking")

    def test_chunks_respect_the_budget(self):
        text = "\n\n".join("word " * 60 for _ in range(30))
        for chunk in agents.split(text):
            self.assertLessEqual(len(chunk), agents.CHUNK_CHARS + agents.OVERLAP_CHARS + 8)

    def test_a_single_oversized_paragraph_is_force_split(self):
        chunks = agents.split("x" * (agents.CHUNK_CHARS * 3))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= agents.CHUNK_CHARS for c in chunks))

    def test_registry_maps_kinds_to_something_the_fleet_can_run(self):
        """Either a plain `(conn, lead, gate) -> Outcome` callable — for an agent whose
        body is free of network I/O — or a `fleet.NetworkAgent`, whose `fetch` and
        `write` must themselves each be callable. `work_once` dispatches on which shape
        it got; this is the contract that dispatch depends on.
        """
        for kind, agent in agents.REGISTRY.items():
            if isinstance(agent, fleet.NetworkAgent):
                self.assertTrue(callable(agent.fetch), f"{kind}.fetch is not callable")
                self.assertTrue(callable(agent.write), f"{kind}.write is not callable")
            else:
                self.assertTrue(callable(agent), f"{kind} is not callable")


if __name__ == "__main__":
    unittest.main()
