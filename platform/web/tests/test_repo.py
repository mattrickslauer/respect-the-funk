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

from rtf_platform import repo

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


if __name__ == "__main__":
    unittest.main()
