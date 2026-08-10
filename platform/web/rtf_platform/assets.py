"""`recording_asset` rows: claiming one before an upload, and confirming it after.

`storage.py` is the bucket; this is the table. They are separate because the ordering
between them is the whole design and it is easy to get backwards.

## The order of operations, and why the row comes first

A presigned URL names a key. The key is derived from the content hash, so it can be
computed before the row exists — but the *row* is what makes the upload accountable: it
records who asked, for which recording, and under what claim about the file. Writing it
after a successful PUT would mean every failed or abandoned upload leaves bytes in the
bucket that no row knows about, which is an orphan nobody can find and nobody is billed
for by name.

So: row first, in `pending`. Then the browser PUTs. Then `confirm` asks the bucket what
is actually there and promotes the row to `stored`. Migration 016's
`asset_stored_has_a_time` CHECK is what makes that promotion atomic in the sense that
matters — a `stored` row without a confirmation time cannot exist.

## What is verified, and what is merely asserted

`confirm` verifies **existence** and **size**, because both are free: `head_object`
returns them. A truncated upload — the browser died at 60 MB of a 90 MB file, S3 kept
what it got — produces a perfectly valid object that is not the master, and comparing
the byte count against what the uploader promised is the cheapest possible way to catch
it.

`content_hash` is **not** verified here, and this is worth being precise about because a
hash that looks verified and is not is worse than no hash. S3's ETag is an MD5 for a
single-part PUT and something else entirely for a multipart one; neither is the SHA-256
the browser computed, and checking it would mean downloading the object into a Lambda —
the exact byte-through-the-function move this whole design avoids. So the hash stays
`asserted` at this point.

It stops being asserted for free later: `analyse_recording` downloads the master to
listen to it, and hashing a stream it is already reading costs nothing. That is where an
asserted hash becomes a measured one, and where a corrupted object gets caught by
something other than a human noticing the track sounds wrong.
"""

from __future__ import annotations

from typing import Any

import psycopg

from rtf_platform import storage

#: What an operator may upload. `stem` is accepted by the schema and deliberately not
#: offered here yet: a stem set is many files that mean nothing individually, and the
#: upload flow for "twelve files that are one thing" is a different flow than this one.
#: Adding it to this tuple without building that flow would produce twelve unrelated
#: rows labelled 'stem' with no way to say which set they belong to.
UPLOADABLE_KINDS = ("master", "instrumental", "radio_edit")

#: 500 MB. A 24-bit/96 kHz stereo WAV runs about 33 MB a minute, so this is a fifteen
#: minute master at the largest format anyone delivers — comfortably past an extended
#: mix and well short of someone uploading a video by mistake. The cap is enforced when
#: the URL is signed rather than after the bytes arrive, which is the only point where
#: refusing still costs nothing.
MAX_ASSET_BYTES = 500 * 1024 * 1024


class AssetRefused(Exception):
    """An upload that will not be signed, with the reason an operator can act on."""


class AssetConflict(AssetRefused):
    """These exact bytes are already on a different recording.

    `asset_one_row_per_file` is keyed on `(tenant_id, kind, content_hash)` with the
    recording deliberately absent, so this is the case the migration header warns must
    never be handled as an upsert: returning the existing row would attach one
    recording's master to another recording, silently, and every fact measured from it
    afterwards would be about the wrong song.
    """

    def __init__(self, other_recording_id: str, other_title: str) -> None:
        self.other_recording_id = other_recording_id
        super().__init__(
            f"those exact bytes are already stored as the master of “{other_title}”. "
            f"Two recordings with byte-identical masters are one recording — check "
            f"which file you meant to pick, or delete the asset on that track first.")


def validate(kind: str, mime: str, size: int) -> None:
    """Everything checkable before a URL is signed. Raises `AssetRefused` or returns.

    Deliberately not a `Field` validator: the browser posts these and the browser is not
    where a closed set is enforced (`demo.Field`'s own docstring makes the same point).
    """
    if kind not in UPLOADABLE_KINDS:
        raise AssetRefused(
            f"{kind!r} is not something this form uploads. "
            f"Offered: {', '.join(UPLOADABLE_KINDS)}.")
    if mime not in storage.ACCEPTED_MIME:
        raise AssetRefused(
            f"{mime or 'that file'} is not an audio format this accepts. "
            f"Masters are WAV, AIFF, FLAC, MP3, M4A, AAC or OGG — if the browser "
            f"reported no type at all, the file has no extension it recognises.")
    if size <= 0:
        raise AssetRefused("that file is empty.")
    if size > MAX_ASSET_BYTES:
        raise AssetRefused(
            f"that file is {size / (1024 * 1024):.0f} MB, over the "
            f"{MAX_ASSET_BYTES // (1024 * 1024)} MB ceiling. If it is genuinely a "
            f"master that long, raise MAX_ASSET_BYTES deliberately rather than "
            f"splitting the file.")


def claim(conn: psycopg.Connection, tenant_id: str, recording_id: str, *,
          kind: str, content_hash: str, mime: str, size: int,
          uploaded_by: str, label: str = "") -> tuple[dict[str, Any], bool]:
    """Reserve the row an upload will fill. Returns `(row, is_new)`.

    Idempotent by construction: re-claiming the same file under the same kind returns
    the existing row rather than a second one, whether that row is still `pending` (the
    operator retried a failed upload) or already `stored` (they picked the same file
    twice). Both are ordinary, and neither should produce a duplicate object.

    The conflict that is **not** ordinary — the same bytes under a different recording —
    raises `AssetConflict`. See that class, and migration 016's header.
    """
    validate(kind, mime, size)
    key = storage.object_key(tenant_id, kind, content_hash, mime)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO recording_asset
                   (tenant_id, recording_id, kind, label, bucket, object_key,
                    content_hash, mime, bytes, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, kind, content_hash) DO NOTHING
               RETURNING id, recording_id, kind, label, bucket, object_key,
                         content_hash, mime, bytes, state, uploaded_at""",
            (tenant_id, recording_id, kind, label, _bucket_name(), key,
             content_hash, mime, size, uploaded_by),
        )
        row = cur.fetchone()
        if row is not None:
            return dict(row), True

        # DO NOTHING returns no row for a conflict, so the existing one is read back
        # rather than assumed. Scoped by tenant because the index is.
        cur.execute(
            """SELECT a.id, a.recording_id, a.kind, a.label, a.bucket, a.object_key,
                      a.content_hash, a.mime, a.bytes, a.state, a.uploaded_at,
                      r.title AS recording_title
                 FROM recording_asset a
                 JOIN recording r ON r.tenant_id = a.tenant_id
                                 AND r.id = a.recording_id
                WHERE a.tenant_id = %s AND a.kind = %s AND a.content_hash = %s""",
            (tenant_id, kind, content_hash),
        )
        existing = cur.fetchone()

    if existing is None:
        # The conflicting row vanished between the INSERT and the SELECT. Nothing here
        # can repair that, and retrying in a loop would hide a real problem — a
        # concurrent delete of the exact asset being uploaded is worth a human look.
        raise AssetRefused(
            "that file conflicted with an existing asset which was then deleted "
            "mid-upload. Try again.")

    if str(existing["recording_id"]) != str(recording_id):
        raise AssetConflict(str(existing["recording_id"]),
                            existing["recording_title"] or "another recording")
    return dict(existing), False


def confirm(conn: psycopg.Connection, store: storage.Storage, tenant_id: str,
            asset_id: str) -> dict[str, Any]:
    """Ask the bucket what actually arrived, and promote the row if it agrees.

    Called after the browser reports its PUT finished. The browser's report is not
    evidence — a PUT that failed on the network reports failure, but a tab closed
    mid-transfer reports nothing at all and a truncated one can report success. The
    bucket is the only witness, so this is a `head_object` and a comparison.
    """
    row = get(conn, tenant_id, asset_id)
    if row is None:
        raise AssetRefused("that asset row is gone.")
    if row["state"] == "stored":
        return row

    head = store.head(row["object_key"])
    if head is None:
        raise AssetRefused(
            "the upload did not finish — nothing is at that key in the bucket. "
            "The row is still pending, so re-uploading the same file resumes it.")

    claimed = row["bytes"]
    if claimed is not None and head.bytes != claimed:
        # Loud, and specific about which number came from where. A truncated upload is
        # the failure this catches, and reporting it as "upload failed" would send an
        # operator to check their connection instead of re-picking the file.
        raise AssetRefused(
            f"the bucket holds {head.bytes:,} bytes and the browser said the file was "
            f"{claimed:,}. That is a truncated upload, not a master — the row is left "
            f"pending. Upload it again.")

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE recording_asset
                  SET state = 'stored', uploaded_at = now(), bytes = %s
                WHERE tenant_id = %s AND id = %s AND state = 'pending'
            RETURNING id, recording_id, kind, label, bucket, object_key, content_hash,
                      mime, bytes, state, uploaded_at""",
            (head.bytes, tenant_id, asset_id),
        )
        promoted = cur.fetchone()

    if promoted is None:
        # Another request confirmed it between the read and the write. Not an error —
        # re-read and return what is there, since the desired state was reached.
        again = get(conn, tenant_id, asset_id)
        if again is None:
            raise AssetRefused("that asset row is gone.")
        return again
    return dict(promoted)


def get(conn: psycopg.Connection, tenant_id: str, asset_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        # `sample_rate`, `channels` and `duration_ms` are in the list because
        # `record_shape` writes them and a getter that cannot read back what a sibling
        # writes is a trap — the caller gets a `KeyError` that reads like a missing
        # column rather than a short SELECT.
        cur.execute(
            """SELECT id, recording_id, kind, label, bucket, object_key, content_hash,
                      mime, bytes, state, uploaded_at, uploaded_by, supersedes_id,
                      sample_rate, channels, duration_ms
                 FROM recording_asset
                WHERE tenant_id = %s AND id = %s""", (tenant_id, asset_id))
        row = cur.fetchone()
    return dict(row) if row else None


def for_recording(conn: psycopg.Connection, tenant_id: str,
                  recording_id: str) -> list[dict[str, Any]]:
    """Every asset on this recording, newest first. Serves the inspector panel."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, kind, label, bytes, mime, state, uploaded_at, uploaded_by,
                      object_key, duration_ms, sample_rate, supersedes_id
                 FROM recording_asset
                WHERE tenant_id = %s AND recording_id = %s
                ORDER BY kind, created_at DESC""", (tenant_id, recording_id))
        return [dict(r) for r in cur.fetchall()]


def current(conn: psycopg.Connection, tenant_id: str, recording_id: str,
            kind: str = "master") -> dict[str, Any] | None:
    """The asset a worker should listen to: stored, of this kind, and not superseded.

    "Not superseded" is computed the way `lessons.heads()` computes it — a row is
    replaced if some other row points at it — rather than with a status column. Migration
    016 chose `supersedes_id` over `UPDATE` for the same reason `party_fact` and `lesson`
    did, and this is the read that pattern implies: a remaster does not delete the master
    that was serviced to radio in March, it just stops being the current one.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, kind, label, bucket, object_key, content_hash, mime, bytes,
                      state, uploaded_at, duration_ms, sample_rate, channels
                 FROM recording_asset
                WHERE tenant_id = %s AND recording_id = %s AND kind = %s
                  AND state = 'stored'
                  AND id NOT IN (
                      SELECT supersedes_id FROM recording_asset
                       WHERE tenant_id = %s AND recording_id = %s
                         AND supersedes_id IS NOT NULL)
                ORDER BY uploaded_at DESC
                LIMIT 1""",
            (tenant_id, recording_id, kind, tenant_id, recording_id))
        row = cur.fetchone()
    return dict(row) if row else None


def record_shape(conn: psycopg.Connection, tenant_id: str, asset_id: str, *,
                 sample_rate: int, channels: int, duration_ms: int) -> None:
    """Fill in what only opening the file can tell us.

    Separate from `confirm` because it needs a decoder, which lives in the worker and
    not in the console. Until this runs the columns are NULL, which is the honest
    reading: nothing has opened the file.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE recording_asset
                  SET sample_rate = %s, channels = %s, duration_ms = %s
                WHERE tenant_id = %s AND id = %s""",
            (sample_rate, channels, duration_ms, tenant_id, asset_id))


def delete(conn: psycopg.Connection, tenant_id: str, asset_id: str) -> str | None:
    """Drop the row, and return the object key the caller is now responsible for.

    The row and the object are deleted in two different systems and this function only
    does the first. It returns the key rather than deleting it so the caller decides —
    and so the case that matters is expressible: if some *other* row shares this key,
    the object must not be deleted. A blind `store.delete` in here would make that a bug.

    The count is on `object_key` and not on `(kind, content_hash)`, which is the fix for
    what an earlier version of this function got wrong. Those two are *usually* the same
    question, because `storage.object_key` derives the key from exactly that pair — so
    `asset_one_row_per_file` already makes two rows with the same kind and hash
    impossible, and counting them could only ever return zero. Dead code that reads like
    a safety check. `object_key` is a plain column with no unique index on it, so a row
    written by anything other than `claim` — a backfill, a repair script, a future
    importer that adopts existing objects — genuinely can point a second row at these
    bytes. That is the case worth surviving, and it is the one being counted.
    """
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM recording_asset
                WHERE tenant_id = %s AND id = %s
            RETURNING object_key""", (tenant_id, asset_id))
        row = cur.fetchone()
        if row is None:
            return None

        cur.execute(
            """SELECT count(*) AS n FROM recording_asset
                WHERE tenant_id = %s AND object_key = %s""",
            (tenant_id, row["object_key"]))
        still_referenced = cur.fetchone()["n"] > 0

    return None if still_referenced else row["object_key"]


def queue_analysis(conn: psycopg.Connection, tenant_id: str, recording_id: str,
                   asset_id: str) -> bool:
    """Queue `analyse_recording` for a freshly stored asset. Returns whether it landed.

    This is what makes uploading a file the *only* action an operator takes. It is the
    same move `outreach.advance` makes when a thread closes — write a lead, and let
    whoever handles that kind claim it. No caller here names an agent; the lead names a
    kind and `agents.REGISTRY` decides the rest.

    Two constraints in `lead` shape this:

      * `lead_scope_shape` requires a `recording`-scoped lead to carry a party as well
        as a recording. The party is whoever is credited as the main artist, which is
        also the right answer for budgets — analysis is spent against the artist whose
        record it is. A recording with no credit yet cannot be scoped, so nothing is
        queued and this returns False rather than inventing an owner.
      * `UNIQUE (tenant_id, target_hash)` makes this idempotent. The hash is over the
        asset id, so re-confirming an upload does not queue a second analysis — and a
        *remastered* track is a different asset, so it does. That is exactly the
        boundary wanted: one analysis per distinct file, forever.
    """
    import hashlib

    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.party_id FROM party_credit c
                WHERE c.tenant_id = %s AND c.subject_kind = 'recording'
                  AND c.subject_id = %s AND c.role IN ('main_artist', 'featured')
                ORDER BY c.role LIMIT 1""",
            (tenant_id, recording_id))
        credit = cur.fetchone()
        if credit is None:
            return False

        target_hash = hashlib.sha256(f"analyse_recording:{asset_id}".encode()).hexdigest()
        cur.execute(
            """INSERT INTO lead (tenant_id, scope_kind, party_id, recording_id, kind,
                                 adapter, target, target_hash, reason, score)
               VALUES (%s, 'recording', %s, %s, 'analyse_recording', 's3', %s, %s,
                       'a master was uploaded and nothing has listened to it yet', 0.8)
               ON CONFLICT (tenant_id, target_hash) DO NOTHING
            RETURNING id""",
            (tenant_id, credit["party_id"], recording_id, asset_id, target_hash))
        return cur.fetchone() is not None


def _bucket_name() -> str:
    """The configured bucket, read at write time so the column records where the object
    actually went rather than where configuration currently points."""
    from rtf_platform import settings

    bucket = settings.load().masters_bucket
    if not bucket:
        raise storage.StorageUnconfigured(
            "PLATFORM_MASTERS_BUCKET is not set, so there is nowhere to put a master.")
    return bucket
