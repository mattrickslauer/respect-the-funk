"""`repo.accept_suggestion` — the one place a guess becomes a fact.

Cluster-gated: skips when `DATABASE_URL` is unset, and works in a dedicated tenant
dropped in `tearDown`, the same pattern `test_fleet.py` uses.

The property under test: a suggestion payload that `harvested.Presence.parse`
cannot read (an old row with no `mode`, or a future non-`presence` kind) must
refuse with `repo.SuggestionUnacceptable` naming the reason — never silently
default the missing field, and never let an unhandled `HarvestInvalid` reach the
caller as an opaque crash.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from spindle import repo

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class AcceptSuggestion(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-repo-{self.tenant[:8]}", "repo test"))
        self.party = repo.create_party(self.conn, self.tenant, name="Test Act",
                                       type_="solo")

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _suggestion(self, payload: dict) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO suggestion (tenant_id, party_id, kind, payload)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (self.tenant, self.party["id"], payload.get("kind", "presence"),
                 Jsonb(payload)),
            )
            return str(cur.fetchone()["id"])

    def _state(self, suggestion_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM suggestion WHERE id = %s", (suggestion_id,))
            return cur.fetchone()["state"]

    # -------------------------------------------------------------- the landmine

    def test_a_payload_with_no_mode_refuses_rather_than_500s(self) -> None:
        # The exact shape measured on the live cluster: a `presence` suggestion
        # written before `mode` became required.
        sid = self._suggestion({
            "kind": "presence", "platform": "deezer", "value": "123",
            "url": "https://deezer.com/artist/123", "label": "Test Act",
        })
        with self.assertRaises(repo.SuggestionUnacceptable) as caught:
            repo.accept_suggestion(self.conn, self.tenant, sid)
        self.assertEqual(caught.exception.suggestion_id, sid)
        self.assertIn("mode", caught.exception.reason)
        # Refused, not silently accepted and not silently dropped: still pending.
        self.assertEqual(self._state(sid), "pending")

    def test_an_unknown_kind_refuses_explicitly_rather_than_parsing_as_presence(self) -> None:
        sid = self._suggestion({"kind": "merge", "candidate_party_id": "whatever"})
        with self.assertRaises(repo.SuggestionUnacceptable) as caught:
            repo.accept_suggestion(self.conn, self.tenant, sid)
        self.assertIn("merge", caught.exception.reason)
        self.assertEqual(self._state(sid), "pending")

    def test_a_complete_presence_suggestion_still_accepts(self) -> None:
        # The regression check: the fix must not have broken the working path.
        sid = self._suggestion({
            "kind": "presence", "platform": "deezer", "value": "123",
            "url": "https://deezer.com/artist/123", "label": "Test Act",
            "mode": "owned",
        })
        self.assertTrue(repo.accept_suggestion(self.conn, self.tenant, sid))
        self.assertEqual(self._state(sid), "accepted")
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT mode, url AS profile_url FROM presence
                    WHERE tenant_id = %s AND subject_kind = 'party'
                      AND subject_id = %s AND platform = 'deezer'""",
                (self.tenant, self.party["id"]),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["mode"], "owned")


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class DeletePartyClearsLessons(unittest.TestCase):
    """`011_lesson.sql`'s comment claims `repo.delete_party` clears `lesson` rows the
    same way it already clears `presence` — `lesson.scope_id` is polymorphic, a STRING
    with no foreign key, so `ON DELETE CASCADE` cannot fire for it any more than it can
    for `presence.subject_id`. The claim was false until this fix; this test is what
    makes it checkable rather than merely written down.
    """

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-repo-lesson-{self.tenant[:8]}",
                         "repo lesson test"))
        self.party = repo.create_party(self.conn, self.tenant, name="Test Act",
                                       type_="solo")
        self.other_party = repo.create_party(self.conn, self.tenant, name="Other Act",
                                             type_="solo")

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _insert_lesson(self, *, scope_kind: str, scope_id: str, text: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence)
                   VALUES (%s, %s, %s, %s, 0, 0.5) RETURNING id""",
                (self.tenant, scope_kind, scope_id, text),
            )
            return str(cur.fetchone()["id"])

    def _lesson_ids(self) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM lesson WHERE tenant_id = %s", (self.tenant,))
            return {str(r["id"]) for r in cur.fetchall()}

    def test_deleting_a_party_removes_its_party_scoped_lessons(self) -> None:
        mine = self._insert_lesson(scope_kind="party", scope_id=str(self.party["id"]),
                                   text="ghosted twice")
        self.assertTrue(repo.delete_party(self.conn, self.tenant, self.party["id"]))
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM lesson
                    WHERE tenant_id = %s AND scope_kind = 'party' AND scope_id = %s""",
                (self.tenant, str(self.party["id"])),
            )
            self.assertIsNone(cur.fetchone(),
                              "a deleted party must leave no lesson row naming it")
        self.assertNotIn(mine, self._lesson_ids())

    def test_deleting_a_party_leaves_other_parties_lessons_alone(self) -> None:
        untouched = self._insert_lesson(
            scope_kind="party", scope_id=str(self.other_party["id"]),
            text="replies fast")
        repo.delete_party(self.conn, self.tenant, self.party["id"])
        self.assertIn(untouched, self._lesson_ids(),
                      "deleting one party must not touch another party's lessons")

    def test_deleting_a_party_leaves_general_lessons_alone(self) -> None:
        general = self._insert_lesson(scope_kind="global", scope_id="",
                                      text="mood editors want the playlist named")
        repo.delete_party(self.conn, self.tenant, self.party["id"])
        self.assertIn(general, self._lesson_ids(),
                      "a channel/global lesson names no party and must survive")


if __name__ == "__main__":
    unittest.main()
