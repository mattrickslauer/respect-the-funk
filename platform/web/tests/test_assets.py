"""Claiming a master before it is uploaded, and confirming it after.

    python -m unittest discover platform/web/tests

Two halves, because the two things being tested fail differently:

  * `StorageKeys` and `Validation` are pure and always run. They cover the decisions
    that are easy to get wrong quietly — a key layout that does not match the unique
    index, a MIME allowlist with a fallback in it.
  * `Claiming` and `Confirming` are cluster-gated and use a fake bucket. There is no
    network here on purpose: `confirm` is interesting because of what it does with the
    *answer* from `head_object`, and a real S3 call would test AWS rather than us.

The test that matters most is `test_the_same_file_on_a_different_recording_raises`.
Everything else checks that a correct upload works; that one checks that the one
conflict which must never be resolved as an upsert is not resolved as an upsert. See
migration 016's header — returning the existing row there would attach one recording's
master to a different recording and every fact measured from it afterwards would be
about the wrong song.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

from spindle import assets, storage

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

BUCKET = "rtf-masters-test"


class _FakeBucket:
    """A `storage.Storage` that remembers what was 'uploaded'.

    Implements the port rather than being patched into it, so if the Protocol grows a
    method this stops satisfying `isinstance` and the test says so.
    """

    name = "fake"

    def __init__(self, bucket: str = BUCKET) -> None:
        self.bucket = bucket
        self.objects: dict[str, storage.Head] = {}
        self.deleted: list[str] = []

    def put(self, key: str, *, size: int, mime: str = "audio/wav") -> None:
        self.objects[key] = storage.Head(bytes=size, mime=mime, etag="etag")

    def presign_put(self, key: str, *, content_type: str,
                    expires_in: int = storage.PUT_TTL) -> str:
        return f"https://{self.bucket}.example/{key}?put&ct={content_type}"

    def presign_get(self, key: str, *, expires_in: int = storage.GET_TTL) -> str:
        return f"https://{self.bucket}.example/{key}?get"

    def head(self, key: str) -> storage.Head | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class StorageKeys(unittest.TestCase):
    """The key layout, which has to agree with `asset_one_row_per_file` or the object
    store and the table disagree about what identity means."""

    def test_the_fake_bucket_satisfies_the_port(self) -> None:
        self.assertIsInstance(_FakeBucket(), storage.Storage)

    def test_the_key_is_content_addressed_under_tenant_and_kind(self) -> None:
        key = storage.object_key("ten", "master", "abc123", "audio/wav")
        self.assertEqual(key, "masters/ten/master/abc123.wav")

    def test_the_same_file_yields_the_same_key(self) -> None:
        """Idempotence at the object level. A retried upload overwrites its own bytes
        rather than leaving an orphan copy nothing references."""
        a = storage.object_key("ten", "master", "abc123", "audio/wav")
        b = storage.object_key("ten", "master", "abc123", "audio/wav")
        self.assertEqual(a, b)

    def test_the_key_omits_the_recording_exactly_as_the_unique_index_does(self) -> None:
        """`asset_one_row_per_file` is keyed on `(tenant_id, kind, content_hash)`. If
        the key carried a recording id and the index did not, moving an asset between
        recordings would need an object copy — and two rows could point at one object
        without `assets.delete`'s reference check being able to notice."""
        self.assertNotIn("recording", storage.object_key("t", "master", "h", "audio/wav"))

    def test_an_unknown_mime_gets_no_suffix_rather_than_a_guess(self) -> None:
        """The suffix is cosmetic and the hash is identity, so an unrecognised type
        loses its extension instead of gaining an invented one."""
        self.assertEqual(storage.object_key("t", "master", "h", "application/octet-stream"),
                         "masters/t/master/h")

    def test_every_accepted_mime_can_name_a_file(self) -> None:
        """A type accepted for upload with no suffix would land in the bucket as a
        bare hash, which is legal and unreadable. The two sets are the same set."""
        for mime in storage.ACCEPTED_MIME:
            with self.subTest(mime=mime):
                self.assertTrue(storage.object_key("t", "master", "h", mime).endswith(
                    storage._SUFFIX[mime]))


class Validation(unittest.TestCase):
    """What is refused before a URL is signed. Refusing after the bytes arrive is not
    refusing — the transfer has already been paid for."""

    def test_a_master_is_accepted(self) -> None:
        assets.validate("master", "audio/wav", 40 * 1024 * 1024)

    def test_a_kind_the_form_does_not_upload_is_refused(self) -> None:
        """`stem` is legal in the schema and deliberately not offered: a stem set is
        many files that mean nothing individually, and this flow uploads one file."""
        with self.assertRaises(assets.AssetRefused):
            assets.validate("stem", "audio/wav", 1000)

    def test_a_kind_outside_the_schema_is_refused(self) -> None:
        with self.assertRaises(assets.AssetRefused):
            assets.validate("sausage", "audio/wav", 1000)

    def test_a_non_audio_type_is_refused(self) -> None:
        with self.assertRaises(assets.AssetRefused):
            assets.validate("master", "video/mp4", 1000)

    def test_a_missing_type_is_refused_rather_than_assumed(self) -> None:
        """A browser reports no type for a file with an extension it does not know.
        Signing anyway and letting S3 store it as `binary/octet-stream` would put an
        unidentifiable object in the masters bucket."""
        with self.assertRaises(assets.AssetRefused):
            assets.validate("master", "", 1000)

    def test_an_empty_file_is_refused(self) -> None:
        with self.assertRaises(assets.AssetRefused):
            assets.validate("master", "audio/wav", 0)

    def test_a_file_over_the_ceiling_is_refused(self) -> None:
        with self.assertRaises(assets.AssetRefused):
            assets.validate("master", "audio/wav", assets.MAX_ASSET_BYTES + 1)

    def test_the_ceiling_itself_is_accepted(self) -> None:
        """Off-by-one on a size limit is a file an operator cannot upload and cannot
        find out why, because the message says "over" and theirs is exactly at."""
        assets.validate("master", "audio/wav", assets.MAX_ASSET_BYTES)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Claiming(unittest.TestCase):

    def setUp(self) -> None:
        os.environ["PLATFORM_MASTERS_BUCKET"] = BUCKET
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-claim-{self.tenant[:8]}", "claim test"))
            cur.execute("""INSERT INTO recording (tenant_id, slug, title)
                           VALUES (%s, 'losing-sleep', 'Losing Sleep') RETURNING id""",
                        (self.tenant,))
            self.recording = str(cur.fetchone()["id"])
            cur.execute("""INSERT INTO recording (tenant_id, slug, title)
                           VALUES (%s, 'denial', 'Denial') RETURNING id""",
                        (self.tenant,))
            self.other = str(cur.fetchone()["id"])
        self.store = _FakeBucket()

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()
        os.environ.pop("PLATFORM_MASTERS_BUCKET", None)

    def _claim(self, *, recording: str | None = None, content_hash: str = "h1",
               kind: str = "master", size: int = 1000):
        return assets.claim(self.conn, self.tenant, recording or self.recording,
                            kind=kind, content_hash=content_hash, mime="audio/wav",
                            size=size, uploaded_by="operator")

    def test_a_claim_writes_a_pending_row_with_the_content_addressed_key(self) -> None:
        row, is_new = self._claim()
        self.assertTrue(is_new)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["object_key"], f"masters/{self.tenant}/master/h1.wav")
        self.assertEqual(row["bucket"], BUCKET)

    def test_reclaiming_the_same_file_returns_the_same_row(self) -> None:
        """A retried upload is ordinary and must not produce a second row or a second
        object. `is_new` is False so the caller can tell the difference."""
        first, _ = self._claim()
        second, is_new = self._claim()
        self.assertFalse(is_new)
        self.assertEqual(str(first["id"]), str(second["id"]))

    def test_the_same_file_on_a_different_recording_raises(self) -> None:
        """The conflict that must never be an upsert. The message names the other
        recording, because the operator's actual mistake is almost always having picked
        the wrong file — and being told "already uploaded" would confirm the wrong
        thing."""
        self._claim()
        with self.assertRaises(assets.AssetConflict) as caught:
            self._claim(recording=self.other)
        self.assertIn("Losing Sleep", str(caught.exception))
        self.assertEqual(caught.exception.other_recording_id, self.recording)

    def test_the_same_bytes_under_a_different_kind_are_a_second_row(self) -> None:
        master, _ = self._claim(kind="master")
        edit, is_new = self._claim(kind="radio_edit")
        self.assertTrue(is_new)
        self.assertNotEqual(str(master["id"]), str(edit["id"]))

    def test_an_oversized_claim_never_reaches_the_table(self) -> None:
        with self.assertRaises(assets.AssetRefused):
            self._claim(size=assets.MAX_ASSET_BYTES + 1)
        self.assertEqual(assets.for_recording(self.conn, self.tenant, self.recording), [])


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Confirming(Claiming):
    """`confirm` asks the bucket what arrived. The browser's word is not evidence."""

    def test_confirming_a_real_upload_promotes_the_row(self) -> None:
        row, _ = self._claim(size=1000)
        self.store.put(row["object_key"], size=1000)
        done = assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        self.assertEqual(done["state"], "stored")
        self.assertIsNotNone(done["uploaded_at"])

    def test_confirming_an_upload_that_never_happened_is_refused(self) -> None:
        """Nothing at the key. The row stays pending, which is what makes the retry
        resume rather than duplicate."""
        row, _ = self._claim()
        with self.assertRaises(assets.AssetRefused):
            assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        self.assertEqual(assets.get(self.conn, self.tenant, str(row["id"]))["state"],
                         "pending")

    def test_a_truncated_upload_is_refused_and_says_which_number_came_from_where(self) -> None:
        """The failure this check exists for: the browser died mid-transfer and S3 kept
        a perfectly valid object that is not the master. Size is the only free way to
        catch it — the hash cannot be checked without downloading the file, which is
        the byte-through-a-function move this design avoids."""
        row, _ = self._claim(size=90_000_000)
        self.store.put(row["object_key"], size=60_000_000)
        with self.assertRaises(assets.AssetRefused) as caught:
            assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        message = str(caught.exception)
        self.assertIn("60,000,000", message)
        self.assertIn("90,000,000", message)
        self.assertEqual(assets.get(self.conn, self.tenant, str(row["id"]))["state"],
                         "pending")

    def test_confirming_twice_is_harmless(self) -> None:
        row, _ = self._claim(size=1000)
        self.store.put(row["object_key"], size=1000)
        first = assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        second = assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        self.assertEqual(first["uploaded_at"], second["uploaded_at"])

    # ------------------------------------------------------------------ reading

    def test_current_ignores_a_pending_asset(self) -> None:
        """The pending window is exactly the window in which a worker must find
        nothing. A signed-but-never-PUT row reading as available audio would send the
        classifier to presign a GET for an object that does not exist."""
        self._claim()
        self.assertIsNone(assets.current(self.conn, self.tenant, self.recording))

    def test_current_returns_the_stored_master(self) -> None:
        row, _ = self._claim(size=1000)
        self.store.put(row["object_key"], size=1000)
        assets.confirm(self.conn, self.store, self.tenant, str(row["id"]))
        found = assets.current(self.conn, self.tenant, self.recording)
        self.assertEqual(str(found["id"]), str(row["id"]))

    def test_current_skips_a_superseded_master(self) -> None:
        """A remaster does not delete the master serviced to radio in March; it stops
        being current. Computed the way `lessons.heads()` computes it — a row is
        replaced if another row points at it — rather than with a status column."""
        old, _ = self._claim(content_hash="old", size=1000)
        self.store.put(old["object_key"], size=1000)
        assets.confirm(self.conn, self.store, self.tenant, str(old["id"]))

        new, _ = self._claim(content_hash="new", size=1200)
        self.store.put(new["object_key"], size=1200)
        assets.confirm(self.conn, self.store, self.tenant, str(new["id"]))
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE recording_asset SET supersedes_id = %s
                            WHERE tenant_id = %s AND id = %s""",
                        (old["id"], self.tenant, new["id"]))

        found = assets.current(self.conn, self.tenant, self.recording)
        self.assertEqual(str(found["id"]), str(new["id"]))

    def test_deleting_the_only_row_hands_back_its_key(self) -> None:
        row, _ = self._claim()
        self.assertEqual(assets.delete(self.conn, self.tenant, str(row["id"])),
                         row["object_key"])

    def test_deleting_one_of_two_rows_sharing_a_key_hands_back_nothing(self) -> None:
        """Content-addressed keys mean two rows can point at one object. Deleting the
        object because one of them went away would break the other, so `delete` returns
        None and the caller leaves the bytes alone.

        Constructed by hand because `claim` cannot produce this state: it derives the
        key from `(kind, content_hash)`, which `asset_one_row_per_file` already makes
        unique. `object_key` carries no unique index of its own, though, so anything
        that writes a row without going through `claim` — a backfill, a repair script,
        an importer adopting objects already in the bucket — can point a second row at
        these bytes. That is what the reference count is for.

        An earlier version of `delete` counted `(kind, content_hash)` instead and could
        therefore only ever count zero: dead code that read like a safety check. This
        test is what caught it, by failing to construct the state it needed.
        """
        row, _ = self._claim()
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO recording_asset (tenant_id, recording_id, kind, bucket,
                       object_key, content_hash, mime, bytes)
                   VALUES (%s, %s, 'radio_edit', %s, %s, 'h1', 'audio/wav', 1000)""",
                (self.tenant, self.recording, BUCKET, row["object_key"]))
        self.assertIsNone(assets.delete(self.conn, self.tenant, str(row["id"])))


if __name__ == "__main__":
    unittest.main()
