"""Which documents become retrievable, and the one that deliberately does not.

`ingest.embed_evidence` queues the chunking stage over `party_document`, and the whole
design is in what it leaves out. `profile_v2` is the composed profile — exactly the text
`embed_party` writes into `party.profile_embedding`, which is what R1 searches. Chunking
it into `party_chunk` would build a second vector index over the same sentences and call
the duplication retrieval: R2 would "find evidence" that is a paraphrase of the thing
that ranked the party in the first place.

That failure is worse than finding nothing, because it does not look like a failure. The
retrieval returns rows, the rows are on-topic, and an operator reading them believes two
independent sources agree when there is one source counted twice. It is the same shape as
every other defect this codebase has spent its docstrings on — plausible, confident and
wrong in a direction nobody checks.

Which is why the exclusion is asserted here rather than left to `EVIDENCE_PLATFORMS`
being read correctly by whoever adds the next source. A platform added to that tuple by
someone who has not read the argument is one line; this file is the thing that stops it.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

from spindle import agents, ingest

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class TheExclusionIsDeliberate(unittest.TestCase):
    """No database. These are properties of the constant, and they are the argument."""

    def test_profile_v2_is_not_evidence(self) -> None:
        """The one that matters. If this ever passes because somebody added
        `profile_v2` to the tuple, R2 starts retrieving paraphrases of R1."""
        self.assertNotIn("profile_v2", ingest.EVIDENCE_PLATFORMS)

    def test_the_evidence_platforms_are_sources_we_did_not_compose(self) -> None:
        """Every entry must be something a third party wrote — an FCC licence row, a
        Radio Browser entry, a Wikipedia article, a Deezer editor. `internal` is the
        repo's own specs, which have their own queueing path in `ingest.load`, and
        anything we composed ourselves belongs to R1."""
        self.assertEqual(set(ingest.EVIDENCE_PLATFORMS),
                         {"fcc", "radio_browser", "wikipedia", "deezer"})
        for composed in ("profile_v2", "internal"):
            self.assertNotIn(composed, ingest.EVIDENCE_PLATFORMS)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class AgainstTheCluster(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-ev-{self.tenant[:8]}", "evidence test"))
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state)
                   VALUES (%s, 'p', 'A Station', 'counterparty', 'contactable')
                RETURNING id""", (self.tenant,))
            self.party = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _doc(self, platform: str, body: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_document (tenant_id, party_id, platform, url, title,
                                               body, content_hash, mime, lang, http_status)
                   VALUES (%s, %s, %s, '', %s, %s, %s, 'text/plain', 'en', 200)
                RETURNING id""",
                (self.tenant, self.party, platform, f"{platform} doc", body,
                 agents.content_hash(f"{platform}:{body}")))
            return str(cur.fetchone()["id"])

    def _queued_targets(self) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute("""SELECT target FROM lead
                            WHERE tenant_id = %s AND kind = 'embed_document'""",
                        (self.tenant,))
            return {r["target"] for r in cur.fetchall()}

    def test_evidence_is_queued_and_the_composed_profile_is_not(self) -> None:
        evidence = self._doc("fcc", "WXYZ is licensed to somebody in Ohio.")
        composed = self._doc("profile_v2", "Plays jazz. Genres: jazz. A radio station.")

        ingest.embed_evidence(self.conn, self.tenant)
        queued = self._queued_targets()

        self.assertIn(evidence, queued)
        self.assertNotIn(composed, queued,
                         "chunking profile_v2 duplicates the index R1 already searches")

    def test_running_it_twice_queues_nothing_the_second_time(self) -> None:
        """Every producer of leads here dedupes on `(tenant_id, target_hash)`, and a
        backfill an operator may re-run is exactly where that has to hold."""
        self._doc("wikipedia", "The station broadcasts jazz from a college campus.")
        first = ingest.embed_evidence(self.conn, self.tenant)
        second = ingest.embed_evidence(self.conn, self.tenant)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_an_empty_body_is_never_queued(self) -> None:
        """`_fetch_embed_document` raises `LeadFailed(permanent=True)` on an empty body.
        Queueing one anyway would put a lead in the frontier that is guaranteed to fail,
        and a frontier full of those is a frontier nobody reads."""
        empty = self._doc("radio_browser", "   ")
        ingest.embed_evidence(self.conn, self.tenant)
        self.assertNotIn(empty, self._queued_targets())


if __name__ == "__main__":
    unittest.main()
