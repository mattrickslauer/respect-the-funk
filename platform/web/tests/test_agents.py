"""The chunker and the document hash, offline.

The agents themselves are exercised end to end by `spindle.ingest` against the
cluster; what is worth unit-testing here is the pure text handling, because a chunker
that silently drops a paragraph produces an index that is quietly incomplete — the
failure that looks exactly like a working system with nothing to say.
"""

from __future__ import annotations

import os
import unittest
import uuid

from spindle import agents, fleet


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


HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class MapSourceSupersession(unittest.TestCase):
    """`_write_map_source` marks a replaced `party_fact` row `superseded` and must set
    `supersedes_id` on the row that replaces it in the same statement, so the two can
    never disagree. Measured on the live cluster before this fix: 3 of 4 `party_fact`
    rows carrying `status = 'superseded'` had nothing pointing at them — orphans,
    because the column was never written.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        from spindle import repo

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-supersede-{self.tenant[:8]}", "supersede test"))
        self.party = repo.create_party(self.conn, self.tenant, name="Test Act",
                                       type_="solo")

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _write(self, value_text: str) -> None:
        from spindle import sources, spend

        lead = {"id": str(uuid.uuid4()), "tenant_id": self.tenant,
                "party_id": self.party["id"]}
        gate = spend.Gate.open(self.conn, self.tenant)
        harvest = sources.Harvest(facts=[
            {"dimension": "genre", "value_text": value_text, "provenance": "measured"},
        ])
        agents._write_map_source(self.conn, lead, gate,
                                 {"platform": "spotify", "harvest": harvest})

    def _facts(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, value_text, status, supersedes_id FROM party_fact
                    WHERE tenant_id = %s AND party_id = %s AND dimension = 'genre'
                    ORDER BY created_at""",
                (self.tenant, self.party["id"]),
            )
            return cur.fetchall()

    def test_a_second_reading_sets_supersedes_id_on_the_new_row(self) -> None:
        self._write("pop")
        first = self._facts()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "live")
        self.assertIsNone(first[0]["supersedes_id"])

        self._write("rock")
        rows = self._facts()
        self.assertEqual(len(rows), 2, "the old reading must stay, not be overwritten")
        by_status = {r["status"]: r for r in rows}
        self.assertEqual(by_status["superseded"]["id"], first[0]["id"])
        self.assertEqual(by_status["live"]["value_text"], "rock")
        # The claim migration 010's comment made and this fix makes true: status and
        # relationship cannot disagree because the new row names exactly what it replaced.
        self.assertEqual(by_status["live"]["supersedes_id"], first[0]["id"],
                         "the live row must point at the row it superseded")
        self.assertIsNone(by_status["superseded"]["supersedes_id"])

    def test_an_unchanged_reading_supersedes_nothing(self) -> None:
        self._write("pop")
        first = self._facts()
        self._write("pop")
        rows = self._facts()
        self.assertEqual(len(rows), 1,
                         "re-mapping the same value must not fork a second row")
        self.assertEqual(rows[0]["id"], first[0]["id"])
        self.assertEqual(rows[0]["status"], "live")

    def test_a_chain_of_three_supersessions_links_each_to_the_one_before(self) -> None:
        self._write("pop")
        first = self._facts()[0]
        self._write("rock")
        second = next(r for r in self._facts() if r["status"] == "live")
        self._write("jazz")
        rows = self._facts()
        third = next(r for r in rows if r["status"] == "live")
        self.assertEqual(third["supersedes_id"], second["id"])
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[second["id"]]["supersedes_id"], first["id"])


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class WriteFindCounterpartiesWritesALegalMode(unittest.TestCase):
    """A discovered curator's own presence must land as `mode='owned'` — the value
    `domain.ProfileMode` actually has three of, not the `'observed'` literal this
    function used to write. Run against the live cluster so migration `014`'s
    `presence_mode_known` CHECK is the thing that would catch a regression: if this
    function ever goes back to writing an illegal mode, the INSERT below fails, not
    just an assertion in this file.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-cp-mode-{self.tenant[:8]}", "cp mode test"))
            # `party_document.lead_id` carries a real foreign key (`ON DELETE SET
            # NULL`, but still enforced on insert) — a synthetic UUID is not enough,
            # a real `lead` row is needed. `scope_kind='tenant'` needs no `party_id`.
            cur.execute(
                """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                     target_hash)
                   VALUES (%s, 'tenant', 'find_counterparties', 'deezer', 'x',
                           %s) RETURNING id""",
                (self.tenant, f"test-cp-mode-{self.tenant}"),
            )
            self.lead_id = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def test_the_curators_own_presence_is_owned_not_observed(self) -> None:
        from spindle import sources, spend

        lead = {"id": self.lead_id, "tenant_id": self.tenant}
        gate = spend.Gate.open(self.conn, self.tenant)
        harvest = sources.Harvest(
            counterparties=[{
                "name": "DJ Mixtape", "platform_id": "cp-1",
                "url": "https://deezer.example/dj-mixtape",
                "profile_text": "Curates a weekly playlist of new funk.",
            }],
            summary="1 curator",
        )
        agents._write_find_counterparties(
            self.conn, lead, gate,
            {"platform": "deezer", "harvest": harvest, "cap": 10})

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT mode FROM presence
                    WHERE tenant_id = %s AND platform = 'deezer'""",
                (self.tenant,),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row, "the curator's presence row must have been written")
        self.assertEqual(row["mode"], "owned")


if __name__ == "__main__":
    unittest.main()


class DiscoveryRefusesToSearchByName(unittest.TestCase):
    """`DeezerSource.discover_counterparties` must not fall back to the artist's name.

    Offline: the refusal happens before any network call, which is itself the property
    worth having — an adapter that reaches a provider only to throw the answer away has
    already spent the call.

    Deezer fuzzy-matches, so a playlist search for "Amanda Kurt" returns every curator
    called Amanda, and those get embedded and ranked like any other candidate. Of the
    eighteen counterparties this harvest put on the live cluster, thirteen were Amandas.

    The fallback fired precisely when the system knew least about the record, which is
    the worst possible moment to become confident. Knowing nothing is a state to report.
    """

    def _source(self):
        from spindle import sources
        return sources.DeezerSource()

    def test_no_style_terms_refuses_rather_than_searching_the_name(self):
        from spindle import sources

        with self.assertRaises(sources.SourceUnavailable) as caught:
            self._source().discover_counterparties("Amanda Kurt", "123", terms=[])
        self.assertIn("style terms", str(caught.exception))

    def test_blank_terms_count_as_no_terms(self):
        """`party_fact` can hold an empty `value_text`; a list of empty strings is not a
        vocabulary, and filtering it away must reach the same refusal rather than
        searching for the empty string."""
        from spindle import sources

        with self.assertRaises(sources.SourceUnavailable):
            self._source().discover_counterparties("Amanda Kurt", "123", terms=["", " "])

    def test_the_refusal_is_permanent_so_the_fleet_stops_retrying(self):
        """Retrying cannot conjure a genre fact. The lead should park for a human, not
        burn four attempts and a provider call each time."""
        from spindle import sources

        with self.assertRaises(sources.SourceUnavailable) as caught:
            self._source().discover_counterparties("Amanda Kurt", "123", terms=[])
        self.assertTrue(caught.exception.permanent)

    def test_the_message_says_what_would_fix_it(self):
        from spindle import sources

        with self.assertRaises(sources.SourceUnavailable) as caught:
            self._source().discover_counterparties("Amanda Kurt", "123", terms=[])
        message = str(caught.exception).lower()
        self.assertTrue("measure" in message or "assert" in message,
                        "the refusal does not tell an operator how to clear it")
