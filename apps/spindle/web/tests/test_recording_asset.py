"""Migration `016_recording_asset.sql`, exercised against the live cluster.

    python -m unittest discover apps/spindle/web/tests

Every assertion here is a claim the migration's header comment makes in prose. The
header is long and the constraints are the part that has to be true, so each paragraph
of reasoning gets a test that fails if the constraint was written differently than it
was argued for.

The two that matter most, because they are the ones that would leak or corrupt rather
than merely annoy:

  * `asset_recording_in_same_tenant` is a **composite** foreign key, the first in this
    schema. Every other child table in migration 005 constrains `tenant_id` and
    `recording_id` independently, which lets tenant A's row point at tenant B's
    recording. Here that would be a master served to the wrong label.
  * `asset_one_row_per_file` is keyed on `(tenant_id, kind, content_hash)` with the
    recording deliberately absent, so the same bytes cannot be two recordings' masters.
    The conflict this produces must NOT be handled as an upsert — see
    `test_the_same_file_under_a_second_recording_is_refused` and the note it carries.

Cluster-gated, in a tenant created and dropped per test — the same pattern
`test_repo.py`, `test_lessons.py` and `test_integrity_constraints.py` use.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

#: Every column the table requires, with the two variable ones left to the caller.
_INSERT = """
    INSERT INTO recording_asset
        (tenant_id, recording_id, kind, bucket, object_key, content_hash, bytes)
    VALUES (%s, %s, %s, 'rtf-masters', %s, %s, 1000)
    RETURNING id
"""


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class RecordingAsset(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        self.other_tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            for tid, slug in ((self.tenant, "asset"), (self.other_tenant, "asset-other")):
                cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                            (tid, f"test-{slug}-{tid[:8]}", f"{slug} test"))
            cur.execute(
                """INSERT INTO recording (tenant_id, slug, title)
                   VALUES (%s, 'one', 'Recording One') RETURNING id""", (self.tenant,))
            self.recording = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO recording (tenant_id, slug, title)
                   VALUES (%s, 'two', 'Recording Two') RETURNING id""", (self.tenant,))
            self.other_recording = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            for tid in (self.tenant, self.other_tenant):
                cur.execute("DELETE FROM tenant WHERE id = %s", (tid,))
        self.conn.close()

    def _insert(self, *, tenant: str | None = None, recording: str | None = None,
                kind: str = "master", key: str = "k/one.wav",
                content_hash: str = "hash-one") -> str:
        with self.conn.cursor() as cur:
            cur.execute(_INSERT, (tenant or self.tenant, recording or self.recording,
                                  kind, key, content_hash))
            return str(cur.fetchone()["id"])

    # ------------------------------------------------------------ the tenant seam

    def test_an_asset_cannot_attach_to_another_tenants_recording(self) -> None:
        """The composite foreign key, which is the reason this migration adds a unique
        index on `recording (tenant_id, id)` at all. Without it this insert succeeds:
        `tenant_id` references a real tenant and `recording_id` references a real
        recording, and nothing in the schema relates the two."""
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self._insert(tenant=self.other_tenant)

    # ------------------------------------------------------------- the closed sets

    def test_an_unrecognised_kind_is_refused(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._insert(kind="sausage")

    def test_an_unrecognised_state_is_refused(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO recording_asset (tenant_id, recording_id, kind,
                           bucket, object_key, content_hash, state)
                       VALUES (%s, %s, 'master', 'b', 'k', 'h', 'uploading')""",
                    (self.tenant, self.recording))

    # ---------------------------------------------------- the pending/stored seam

    def test_a_new_row_is_pending_and_has_no_upload_time(self) -> None:
        """A presigned upload writes the row before the bytes exist. `pending` is what
        keeps that window visible; if the default were `stored` every signed-but-never-
        PUT asset would read as available audio."""
        asset_id = self._insert()
        with self.conn.cursor() as cur:
            cur.execute("""SELECT state, uploaded_at FROM recording_asset
                            WHERE tenant_id = %s AND id = %s""", (self.tenant, asset_id))
            row = cur.fetchone()
        self.assertEqual(row["state"], "pending")
        self.assertIsNone(row["uploaded_at"])

    def test_stored_without_an_upload_time_is_refused(self) -> None:
        """`state = 'stored'` is the claim that the object is there and `uploaded_at` is
        when that was confirmed. A `stored` row with no timestamp cannot say whether it
        was ever checked, which is the exact ambiguity `state` was added to remove."""
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO recording_asset (tenant_id, recording_id, kind,
                           bucket, object_key, content_hash, state)
                       VALUES (%s, %s, 'master', 'b', 'k', 'h', 'stored')""",
                    (self.tenant, self.recording))

    def test_pending_with_an_upload_time_is_refused(self) -> None:
        """The biconditional runs both ways on purpose. A `pending` row carrying a
        timestamp is a confirm that half-happened, and it would read to the worker's
        `WHERE state = 'stored'` as absent while reading to a human as uploaded."""
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO recording_asset (tenant_id, recording_id, kind,
                           bucket, object_key, content_hash, uploaded_at)
                       VALUES (%s, %s, 'master', 'b', 'k', 'h', now())""",
                    (self.tenant, self.recording))

    def test_confirming_an_upload_is_accepted(self) -> None:
        asset_id = self._insert()
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE recording_asset SET state = 'stored', uploaded_at = now()
                            WHERE tenant_id = %s AND id = %s""", (self.tenant, asset_id))
            cur.execute("""SELECT state FROM recording_asset
                            WHERE tenant_id = %s AND id = %s""", (self.tenant, asset_id))
            self.assertEqual(cur.fetchone()["state"], "stored")

    # --------------------------------------------------------------- addressability

    def test_a_row_that_cannot_address_its_object_is_refused(self) -> None:
        for column, values in (
            ("object_key", ("b", "", "h")),
            ("content_hash", ("b", "k", "")),
            ("bucket", ("", "k", "h")),
        ):
            with self.subTest(column=column):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO recording_asset (tenant_id, recording_id,
                                   kind, bucket, object_key, content_hash)
                               VALUES (%s, %s, 'master', %s, %s, %s)""",
                            (self.tenant, self.recording, *values))

    def test_a_zero_byte_master_is_refused(self) -> None:
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO recording_asset (tenant_id, recording_id, kind,
                           bucket, object_key, content_hash, bytes)
                       VALUES (%s, %s, 'master', 'b', 'k', 'h', 0)""",
                    (self.tenant, self.recording))

    def test_an_unopened_file_may_leave_its_audio_shape_null(self) -> None:
        """The uploader knows the byte count and the MIME type from the browser and does
        not know the sample rate. Defaulting that to 44100 is the silent default this
        codebase refuses, so the columns are nullable and stay NULL until something
        opens the file."""
        asset_id = self._insert()
        with self.conn.cursor() as cur:
            cur.execute("""SELECT sample_rate, channels, duration_ms FROM recording_asset
                            WHERE tenant_id = %s AND id = %s""", (self.tenant, asset_id))
            row = cur.fetchone()
        self.assertEqual((row["sample_rate"], row["channels"], row["duration_ms"]),
                         (None, None, None))

    # ------------------------------------------------------------------ identity

    def test_the_same_file_under_a_second_recording_is_refused(self) -> None:
        """`asset_one_row_per_file` is keyed on `(tenant_id, kind, content_hash)` and
        deliberately not on the recording, so this collides.

        The reason to assert it: a caller that handles this `UniqueViolation` as an
        upsert and returns the existing row would hand back an asset belonging to a
        **different recording**, silently attaching the wrong audio to the right track.
        `storage.claim_asset` compares `recording_id` on the conflicting row for exactly
        this case.
        """
        self._insert()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._insert(recording=self.other_recording, key="k/two.wav")

    def test_the_same_bytes_under_a_different_kind_are_a_different_row(self) -> None:
        """A radio edit that happens to be byte-identical to the master is two claims
        about the same bytes, and `kind` is in the key so both can be made."""
        self._insert(kind="master")
        self._insert(kind="radio_edit", key="k/edit.wav")

    def test_an_asset_cannot_supersede_itself(self) -> None:
        """The same one-row cycle migration 014 closed on `party_fact` and `lesson`."""
        asset_id = self._insert()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute("""UPDATE recording_asset SET supersedes_id = id
                                WHERE tenant_id = %s AND id = %s""",
                            (self.tenant, asset_id))

    # ------------------------------------------------------------------- lifecycle

    def test_deleting_the_recording_removes_its_assets(self) -> None:
        """Rows only. The object stays in the bucket — see the migration header, which
        says so rather than leaving it to be found on a bill."""
        self._insert()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM recording WHERE tenant_id = %s AND id = %s",
                        (self.tenant, self.recording))
            cur.execute("SELECT count(*) AS n FROM recording_asset WHERE tenant_id = %s",
                        (self.tenant,))
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_a_measured_fact_can_name_the_asset_it_listened_to(self) -> None:
        """The claim the migration header makes in place of adding `party_fact.asset_id`:
        `fact_basis` already carries polymorphic provenance edges and already renders in
        the console, so a BPM measured off a master costs no new schema to distinguish
        from one measured off a 30-second preview."""
        asset_id = self._insert()
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_fact (tenant_id, recording_id, dimension,
                       value_text, provenance)
                   VALUES (%s, %s, 'bpm', '125', 'measured') RETURNING id""",
                (self.tenant, self.recording))
            fact_id = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO fact_basis (subject_kind, subject_id, basis_kind, basis_id)
                   VALUES ('fact', %s, 'recording_asset', %s)""", (fact_id, asset_id))
            cur.execute(
                """SELECT basis_kind, basis_id FROM fact_basis
                    WHERE subject_kind = 'fact' AND subject_id = %s""", (fact_id,))
            row = cur.fetchone()

        self.assertEqual(row["basis_kind"], "recording_asset")
        self.assertEqual(str(row["basis_id"]), asset_id)

        # `fact_basis` has no tenant column, so the cascade cannot reach it. Clean up
        # by hand — the same obligation `apps/spindle/README.md`'s orphan sweep already
        # carries for `presence`, `party_credit` and `lesson`.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM fact_basis WHERE subject_id = %s", (fact_id,))


if __name__ == "__main__":
    unittest.main()
