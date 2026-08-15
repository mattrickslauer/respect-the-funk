"""The rerank, offline.

This is the arithmetic that turns "who resembles this artist" into "who resembles this
artist and has not ignored us twice", and it is a pure function precisely so it can be
tested without a cluster, a model, or a network. `PLATFORM-SPEC §6`'s R1 is the search;
this is the part that makes the search remember.

The property under test throughout: **an adjustment must be traceable to the lesson that
caused it.** A rerank that produces a better order and cannot say why is not usable by
the console's inspector, and the inspector is the reason the product claims every object
has a why.
"""

from __future__ import annotations

import unittest

from spindle import lessons


def candidate(cid: str, distance: float) -> dict:
    return {"id": cid, "name": f"party {cid}", "distance": distance}


def lesson(scope_kind: str, scope_id: str, valence: float,
           confidence: float = 1.0, lid: str = "l1", text: str = "a lesson") -> dict:
    return {"id": lid, "scope_kind": scope_kind, "scope_id": scope_id,
            "valence": valence, "confidence": confidence, "text": text}


class Applies(unittest.TestCase):

    def test_a_party_lesson_applies_only_to_that_party(self):
        les = lesson("party", "a", -1.0)
        self.assertTrue(lessons.applies(les, candidate("a", 0.5)))
        self.assertFalse(lessons.applies(les, candidate("b", 0.5)))

    def test_a_general_lesson_applies_to_everyone_in_the_run(self):
        # Retrieval already scoped these by channel and kind; if it came back, it applies.
        for kind in ("party_kind", "channel", "global"):
            les = lesson(kind, "curator", 0.5)
            self.assertTrue(lessons.applies(les, candidate("anyone", 0.5)),
                            f"{kind} lesson should apply to every candidate")


class Rerank(unittest.TestCase):

    def test_no_lessons_preserves_distance_order(self):
        cands = [candidate("far", 0.9), candidate("near", 0.1)]
        out = lessons.rerank(cands, [])
        self.assertEqual([c["id"] for c in out], ["near", "far"])
        self.assertEqual(out[0]["adjusted"], 0.1)

    def test_a_negative_lesson_sinks_the_candidate_it_names(self):
        cands = [candidate("ghoster", 0.10), candidate("quiet", 0.12)]
        out = lessons.rerank(cands, [lesson("party", "ghoster", -1.0)],
                             weight=0.05)
        self.assertEqual([c["id"] for c in out], ["quiet", "ghoster"],
                         "a curator who ignored us twice must not outrank a fresh one")

    def test_a_positive_lesson_lifts_the_candidate_it_names(self):
        cands = [candidate("replied", 0.12), candidate("unknown", 0.10)]
        out = lessons.rerank(cands, [lesson("party", "replied", 1.0)], weight=0.05)
        self.assertEqual([c["id"] for c in out], ["replied", "unknown"])

    def test_confidence_scales_the_shift(self):
        cands = [candidate("a", 0.5)]
        sure = lessons.rerank(cands, [lesson("party", "a", -1.0, confidence=1.0)],
                              weight=0.05)[0]["adjusted"]
        unsure = lessons.rerank(cands, [lesson("party", "a", -1.0, confidence=0.25)],
                                weight=0.05)[0]["adjusted"]
        self.assertGreater(sure, unsure,
                           "a lesson we are sure of must move the needle further")

    def test_a_general_lesson_shifts_everyone_and_changes_no_order(self):
        cands = [candidate("a", 0.10), candidate("b", 0.20)]
        out = lessons.rerank(cands, [lesson("channel", "email", 1.0)], weight=0.05)
        self.assertEqual([c["id"] for c in out], ["a", "b"])

    def test_every_adjustment_names_the_lesson_that_caused_it(self):
        les = lesson("party", "a", -1.0, lid="L-42", text="ghosted twice")
        out = lessons.rerank([candidate("a", 0.5)], [les], weight=0.05)
        applied = out[0]["applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["lesson_id"], "L-42")
        self.assertEqual(applied[0]["text"], "ghosted twice")
        self.assertAlmostEqual(applied[0]["shift"], -0.05,
                               msg="a discouraging lesson reports a negative shift")

    def test_an_unaffected_candidate_reports_no_lessons(self):
        out = lessons.rerank([candidate("a", 0.5), candidate("b", 0.5)],
                             [lesson("party", "a", -1.0)])
        by_id = {c["id"]: c for c in out}
        self.assertEqual(by_id["b"]["applied"], [])

    def test_inputs_are_not_mutated(self):
        cands = [candidate("a", 0.5)]
        lessons.rerank(cands, [lesson("party", "a", -1.0)])
        self.assertNotIn("adjusted", cands[0],
                         "rerank must not write into the caller's rows")

    def test_many_lessons_cannot_drive_a_score_below_zero(self):
        # Cosine distance is non-negative. A score that goes negative is not a distance
        # any more, and anything reading it as one is now wrong.
        piles = [lesson("party", "a", 1.0, lid=f"l{i}") for i in range(50)]
        out = lessons.rerank([candidate("a", 0.1)], piles, weight=0.05)
        self.assertGreaterEqual(out[0]["adjusted"], 0.0)

    def test_shift_sign_says_which_way_the_lesson_pushed(self):
        # The inspector renders this. Magnitude alone cannot distinguish "we like them
        # because they replied" from "we avoid them because they did not".
        good = lessons.rerank([candidate("a", 0.5)],
                              [lesson("party", "a", 1.0)], weight=0.05)
        bad = lessons.rerank([candidate("a", 0.5)],
                             [lesson("party", "a", -1.0)], weight=0.05)
        self.assertGreater(good[0]["applied"][0]["shift"], 0)
        self.assertLess(bad[0]["applied"][0]["shift"], 0)


class Heads(unittest.TestCase):
    """A superseded lesson must never reach the rerank.

    `SCOPE-RESET §2a` rule 1 says revisions are appended and the current value is the
    head of the chain. If retrieval returns both a lesson and the correction that
    replaced it, the rerank applies both and the correction is worth nothing.
    """

    def test_an_unsuperseded_row_is_a_head(self):
        rows = [{"id": "a", "supersedes_id": None}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["a"])

    def test_a_superseded_row_is_dropped(self):
        rows = [{"id": "old", "supersedes_id": None},
                {"id": "new", "supersedes_id": "old"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["new"])

    def test_a_chain_of_three_resolves_to_one_head(self):
        rows = [{"id": "v1", "supersedes_id": None},
                {"id": "v2", "supersedes_id": "v1"},
                {"id": "v3", "supersedes_id": "v2"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["v3"])

    def test_a_row_superseding_something_absent_is_still_a_head(self):
        # Retrieval is a top-k: the row it replaced may simply not have scored. The
        # replacement is still current, and dropping it would lose the lesson entirely.
        rows = [{"id": "v2", "supersedes_id": "v1-not-in-this-result"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["v2"])

    def test_order_is_preserved(self):
        rows = [{"id": "a", "supersedes_id": None}, {"id": "b", "supersedes_id": None}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["a", "b"])


class Bounds(unittest.TestCase):
    """Rounding error is absorbed; a misunderstanding is refused."""

    def test_float_noise_is_clamped(self):
        self.assertEqual(lessons._bounded(1.0000001, 0.0, 1.0, "confidence"), 1.0)
        self.assertEqual(lessons._bounded(-1.0000001, -1.0, 1.0, "valence"), -1.0)

    def test_a_value_in_range_is_unchanged(self):
        self.assertEqual(lessons._bounded(0.5, 0.0, 1.0, "confidence"), 0.5)

    def test_a_scale_error_is_refused_rather_than_flattened(self):
        # A caller working in percent must not silently become "maximally confident".
        with self.assertRaises(ValueError) as caught:
            lessons._bounded(50, 0.0, 1.0, "confidence")
        self.assertIn("confidence=50", str(caught.exception))

    def test_the_bound_names_itself_in_the_error(self):
        with self.assertRaises(ValueError) as caught:
            lessons._bounded(-3, -1.0, 1.0, "valence")
        self.assertIn("valence", str(caught.exception))


import os
import uuid

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class RetrievalIsScoped(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test.

    What cannot be faked: that a lesson written under one embedding model is invisible to
    a retrieval carrying another. A fake returning rows from a dict proves the fake works.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-lesson-{self.tenant[:8]}", "lesson test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _vector(self, fill: float) -> str:
        return "[" + ",".join([str(fill)] * 1024) + "]"

    def _insert(self, *, model: str, scope_kind: str = "global",
                scope_id: str = "", text: str = "a lesson") -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence, embedding, model)
                   VALUES (%s, %s, %s, %s, 0.5, 0.9, %s::VECTOR(1024), %s)
                RETURNING id""",
                (self.tenant, scope_kind, scope_id, text, self._vector(0.1), model),
            )
            return str(cur.fetchone()["id"])

    def test_a_lesson_from_another_model_is_invisible(self):
        from spindle import lessons as mod
        self._insert(model="other-model")
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[])
        self.assertEqual(found, [],
                         "a vector from a different model is noise, not a near neighbour")

    def test_a_lesson_from_the_same_model_is_found(self):
        from spindle import lessons as mod
        self._insert(model="the-model", text="found me")
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[])
        self.assertIn("found me", [row["text"] for row in found])

    def test_a_party_lesson_is_fetched_by_id_not_by_similarity(self):
        # A party-scoped lesson with a deliberately distant embedding must still come
        # back when that party is a candidate — we know who we mean.
        from spindle import lessons as mod
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence, embedding, model)
                   VALUES (%s, 'party', %s, 'ghosted twice', -1, 0.9,
                           %s::VECTOR(1024), 'the-model')""",
                (self.tenant, party_id, self._vector(0.9)),
            )
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[party_id])
        self.assertIn("ghosted twice", [row["text"] for row in found])


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class ShortlistDoesNotDuplicateOnPresence(unittest.TestCase):
    """`presence` is polymorphic: a party can own several rows in it — Spotify, Deezer,
    YouTube. R1 must not join against it, because a join multiplies: a party with three
    surfaces would become three shortlist rows, `LIMIT` would return fewer distinct
    parties than asked for, and the same party would earn `hit_count` more than once
    per campaign for lessons that only actually applied to it once.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-dedup-{self.tenant[:8]}", "dedup test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _vector(self, fill: float) -> str:
        return "[" + ",".join([str(fill)] * 1024) + "]"

    def test_a_party_with_three_presence_rows_appears_once(self):
        from spindle import agents, spend

        model = "the-model"
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state,
                                      profile_embedding, embedding_model)
                   VALUES (%s, 'artist', 'the artist', 'roster', 'contactable',
                           %s::VECTOR(1024), %s)
                RETURNING id""",
                (self.tenant, self._vector(0.1), model),
            )
            artist_id = str(cur.fetchone()["id"])

            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state,
                                      profile_embedding, embedding_model)
                   VALUES (%s, 'curator', 'the curator', 'counterparty', 'contactable',
                           %s::VECTOR(1024), %s)
                RETURNING id""",
                (self.tenant, self._vector(0.1), model),
            )
            curator_id = str(cur.fetchone()["id"])

            for platform in ("spotify", "deezer", "youtube"):
                # `mode` is explicit rather than left to the column default: as of
                # migration `014`'s `presence_mode_known` CHECK, the default (`''`)
                # is itself illegal, so a bare INSERT that used to slide through on
                # it now fails loudly instead — correctly, but this test is about
                # dedup, not about mode, hence the arbitrary legal value.
                cur.execute(
                    """INSERT INTO presence (tenant_id, subject_kind, subject_id,
                                             platform, mode, url)
                       VALUES (%s, 'party', %s, %s, 'unowned', %s)""",
                    (self.tenant, curator_id, platform,
                     f"https://{platform}.example/curator"),
                )

        gate = spend.Gate.open(self.conn, self.tenant)
        rows = agents.shortlist(self.conn, self.tenant, artist_id, gate=gate)

        matches = [row for row in rows if str(row["id"]) == curator_id]
        self.assertEqual(len(matches), 1,
                         "a party with three presence rows must shortlist once, "
                         "not three times")
