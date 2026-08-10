"""Migration `014_integrity_constraints.sql`, exercised against the live cluster.

Two closed sets that used to be closed only in Python, or not at all:

  * `party_fact.supersedes_id` and `lesson.supersedes_id` may not equal their own
    `id` — `lessons.heads()` computes `replaced = {row.supersedes_id for row in rows
    if row.supersedes_id}` and drops every row whose `id` is in `replaced`, so a
    self-superseding row would vanish from its own retrieval, silently.
  * `presence.mode` may not be anything outside `domain.ProfileMode`'s three values —
    added `NOT VALID` because 18 of 21 live rows already violated it (see
    `agents._write_find_counterparties`'s history and the migration's own comment);
    `NOT VALID` still enforces the CHECK on every write from this migration forward,
    which is the property under test here.

Cluster-gated, in a tenant created and dropped per test — the same pattern
`test_repo.py`/`test_lessons.py` use.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class SelfSupersedeIsRefused(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-selfsuper-{self.tenant[:8]}",
                         "self-supersede test"))
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name)
                   VALUES (%s, 'act', 'Test Act') RETURNING id""",
                (self.tenant,),
            )
            self.party_id = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def test_a_party_fact_cannot_supersede_itself_on_insert(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT gen_random_uuid() AS id",
            )
            fact_id = cur.fetchone()["id"]
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO party_fact
                            (id, tenant_id, party_id, dimension, value_text,
                             provenance, supersedes_id)
                       VALUES (%s, %s, %s, 'genre', 'pop', 'measured', %s)""",
                    (fact_id, self.tenant, self.party_id, fact_id),
                )

    def test_a_party_fact_cannot_be_updated_to_supersede_itself(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_fact
                        (tenant_id, party_id, dimension, value_text, provenance)
                   VALUES (%s, %s, 'genre', 'pop', 'measured') RETURNING id""",
                (self.tenant, self.party_id),
            )
            fact_id = cur.fetchone()["id"]
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE party_fact SET supersedes_id = id WHERE id = %s",
                    (fact_id,),
                )

    def test_a_lesson_cannot_supersede_itself_on_insert(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT gen_random_uuid() AS id")
            lesson_id = cur.fetchone()["id"]
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lesson
                            (id, tenant_id, scope_kind, scope_id, text, valence,
                             confidence, supersedes_id)
                       VALUES (%s, %s, 'global', '', 'x', 0, 0.5, %s)""",
                    (lesson_id, self.tenant, lesson_id),
                )

    def test_a_lesson_cannot_be_updated_to_supersede_itself(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence)
                   VALUES (%s, 'global', '', 'x', 0, 0.5) RETURNING id""",
                (self.tenant,),
            )
            lesson_id = cur.fetchone()["id"]
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE lesson SET supersedes_id = id WHERE id = %s",
                    (lesson_id,),
                )

    def test_a_two_row_supersede_cycle_is_not_caught(self) -> None:
        """The CHECK this migration adds is a complete guard against the one-row
        self-reference and nothing more — the migration's own comment says so, and
        this test is what keeps that comment honest. A cycle across two rows never
        makes either row's `supersedes_id` equal its own `id`, so the CHECK, which
        only ever compares a row to itself, has nothing to object to."""
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence)
                   VALUES (%s, 'global', '', 'a', 0, 0.5) RETURNING id""",
                (self.tenant,),
            )
            a_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence, supersedes_id)
                   VALUES (%s, 'global', '', 'b', 0, 0.5, %s) RETURNING id""",
                (self.tenant, a_id),
            )
            b_id = cur.fetchone()["id"]
            # Close the cycle: a now supersedes b, and b supersedes a.
            cur.execute("UPDATE lesson SET supersedes_id = %s WHERE id = %s",
                        (b_id, a_id))
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, supersedes_id FROM lesson WHERE id IN (%s, %s)",
                        (a_id, b_id))
            rows = {str(r["id"]): str(r["supersedes_id"]) for r in cur.fetchall()}
        self.assertEqual(rows[str(a_id)], str(b_id))
        self.assertEqual(rows[str(b_id)], str(a_id))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class PresenceModeIsChecked(unittest.TestCase):
    """`presence_mode_known`, added `NOT VALID` — grandfathers the 18 rows already
    holding the illegal `'observed'` value live on the cluster, but must still refuse
    that value, or any other outside `domain.ProfileMode`, on every write from here on.
    """

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-mode-{self.tenant[:8]}", "mode test"))
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name)
                   VALUES (%s, 'act', 'Test Act') RETURNING id""",
                (self.tenant,),
            )
            self.party_id = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def test_an_illegal_mode_is_refused_on_insert(self) -> None:
        # The exact value `agents._write_find_counterparties` used to write, before
        # this migration and the code fix that landed with it.
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO presence (tenant_id, subject_kind, subject_id,
                                             platform, mode)
                       VALUES (%s, 'party', %s, 'deezer', 'observed')""",
                    (self.tenant, self.party_id),
                )

    def test_each_legal_mode_is_accepted(self) -> None:
        for platform, mode in (("spotify", "owned"), ("tiktok", "unowned"),
                               ("bandcamp", "absent")):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO presence (tenant_id, subject_kind, subject_id,
                                             platform, mode)
                       VALUES (%s, 'party', %s, %s, %s)""",
                    (self.tenant, self.party_id, platform, mode),
                )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM presence WHERE tenant_id = %s",
                (self.tenant,),
            )
            self.assertEqual(cur.fetchone()["n"], 3)


if __name__ == "__main__":
    unittest.main()
