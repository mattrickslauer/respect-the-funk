-- =============================================================================
-- 016_recording_asset.sql — the spine gets somewhere to put a master
-- =============================================================================
--
-- `docs/2026-08-10-masters-and-classification.md` §4 is the argument; this is the
-- schema half of it. The short version: `recording` carries `title`, `isrc`,
-- `duration_ms`, `released_on` — and no audio. There is no storage port, no bucket and
-- no object key anywhere in `platform/`, which is why `spindle.audio` measures a
-- Deezer 30-second preview and why it deliberately refuses to report anything that a
-- thirty-second excerpt cannot support (sections, energy curve, hook window).
--
-- Every measured fact this platform wants about a recording needs the whole file.
--
--
-- ## Why a table and not a column on `recording`
--
-- A master is not one file, and it is not read by one consumer:
--
--     genre classification     any full-length render
--     tempo / key / sections   the master, full length  (SCOPE-RESET §2's list)
--     radio servicing          broadcast quality, often a specific edit
--     sync licensing           the master, and usually an instrumental
--     RemixKit                 master plus stems
--     distributor delivery     whatever format that distributor specifies
--
-- `recording.master_key STRING` serves the first consumer and is then in the way of
-- the other five. Stems alone are already one-to-many.
--
--
-- ## `(tenant_id, recording_id)` is a real foreign key, and this is the first table
-- ## in the schema where that is true
--
-- Every other child table in migration 005 carries `tenant_id UUID REFERENCES
-- tenant(id)` and `recording_id UUID REFERENCES recording(id)` as two independent
-- constraints, which means the database is perfectly happy to attach tenant A's row to
-- tenant B's recording. Nothing catches that but `tests/test_tenant_scoping.py`, and
-- that lint reads *our* source — it cannot see a row written by psql, a migration, or
-- a future caller that has not been written yet.
--
-- The last three commits on this branch are all that same bug in different clothes
-- (`5c89a2d` outreach views joining across tenants; `4b68315` the lint learning to
-- demand equality rather than a mention). An asset is a URL to a media object, so
-- getting this wrong here does not leak a row — it leaks a master. That is worth one
-- extra unique index:
--
--     CREATE UNIQUE INDEX recording_by_tenant_and_id ON recording (tenant_id, id)
--
-- `recording.id` is already the primary key, so this index adds no uniqueness that was
-- not already true; it exists solely to be a referenceable target for the composite
-- foreign key below. It is small and it never fires a constraint check on its own.
--
-- The other tables are **not** retrofitted here. Doing that is a separate migration
-- with its own verification against live rows, and quietly widening this one into a
-- schema-wide change is how a migration stops being reviewable. This table is new and
-- empty, so it can simply start correct.
--
--
-- ## `content_hash` is unique per tenant per kind, and the conflict is not an upsert
--
-- Re-uploading the same file is ordinary. Storing it twice, and analysing it twice, is
-- not — so the same bytes under the same `kind` are one row.
--
-- The non-obvious half: this constraint is scoped to the tenant and the kind, **not**
-- to the recording. So uploading recording A's master a second time under recording B
-- collides. That is deliberate and it is the more useful behaviour, because it is
-- almost always the operator having picked the wrong file — two recordings whose
-- masters are byte-identical are one recording. But it means a caller handling the
-- conflict must **not** blindly return the existing row: that row may belong to a
-- different recording, and handing it back would silently attach the wrong audio to
-- the right track. `storage.claim_asset` checks `recording_id` on the conflicting row
-- and raises when it differs, which is the whole reason that function exists rather
-- than a bare `ON CONFLICT DO UPDATE`.
--
--
-- ## Two states, and why there is no third
--
-- Uploads are presigned: the browser PUTs to S3 directly and no master ever transits a
-- Lambda (`BUILD-SPEC §2b` rule 2, and `app/remixkit/ports/storage.py` says the same
-- thing about RemixKit). Signing a URL requires knowing the key, which requires the
-- row — so the row is necessarily written **before** the bytes exist.
--
-- `state` is what stops that window from being invisible. A `pending` row is a promise
-- nobody has kept yet, and it must never read as an available master: `analyse_
-- recording` selects `WHERE state = 'stored'` and finds nothing rather than presigning
-- a GET for an object that was never PUT.
--
-- There is no `missing` state. It would need something to write it — a sweep that
-- HEADs every object and marks the vanished ones — and that sweep does not exist. A
-- state no code sets is a column that lies by omission: every row reads `stored`,
-- including the ones whose object was deleted out from under them. Instead the worker
-- discovers a vanished object at the point of use and fails the lead loudly, which is
-- `NO FALLBACKS` applied to storage. Add the state in the same migration that adds
-- the sweep.
--
--
-- ## Deleting a row does not delete the object
--
-- `ON DELETE CASCADE` reaches the row and stops at the bucket, so a deleted recording
-- leaves its bytes in S3 costing $0.023/GB/month forever. This is stated here rather
-- than left to be discovered from a bill. The answer is a bucket lifecycle rule plus a
-- reconciliation sweep over `object_key`, and neither is in this migration — the
-- `platform/README.md` orphan sweep that already owes `presence`, `party_credit` and
-- `lesson` a pass now owes one to the bucket too.
--
--
-- ## Derived facts name their asset through `fact_basis`, not a new column
--
-- The obvious move is `party_fact.asset_id`. It is the wrong one: `fact_basis`
-- (migration 005) is already the polymorphic provenance walk, it is already what
-- `research._basis` and `research._dependents` render with their indent rails, and it
-- already has the downward index the invalidator follows to mark a cascade stale. A
-- BPM measured from a master is:
--
--     INSERT INTO fact_basis (subject_kind, subject_id, basis_kind, basis_id)
--     VALUES ('fact', <fact id>, 'recording_asset', <asset id>)
--
-- and it renders in the console with no template change. That is the constraint
-- §4a rule 2 of the design note asks for — a BPM measured from a 30-second preview
-- stops being indistinguishable from one measured off the master — bought for zero new
-- schema. `fact_basis` has no rows in it on the live cluster today, so this is also the
-- first thing that will put any there.


-- ------------------------------------------------------- the referenceable target

-- Adds no uniqueness — `id` is already the primary key. It exists to be the target of
-- the composite foreign key below, because a foreign key must reference a unique index
-- and `(tenant_id, id)` did not have one.
CREATE UNIQUE INDEX IF NOT EXISTS recording_by_tenant_and_id
    ON recording (tenant_id, id);


-- --------------------------------------------------------------------- the table

CREATE TABLE IF NOT EXISTS recording_asset (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL,

    -- What this file *is*. Closed, because every consumer branches on it and an
    -- unrecognised kind is a delivery that silently ships the wrong render.
    kind         STRING NOT NULL,

    -- Free text, and only meaningful for kinds that come in more than one: which stem
    -- ('drums', 'vocal'), which edit ('clean', 'radio 3:30'). Empty for a master.
    label        STRING NOT NULL DEFAULT '',

    -- Stored rather than derived from configuration, so that changing the configured
    -- bucket leaves existing objects addressable instead of silently repointing every
    -- historical asset at a bucket that does not contain it.
    bucket       STRING NOT NULL,
    object_key   STRING NOT NULL,

    -- SHA-256 of the bytes, computed by the uploader before the PUT. See the header:
    -- this is identity, not a checksum nobody reads.
    content_hash STRING NOT NULL,

    mime         STRING NOT NULL DEFAULT '',
    bytes        INT,

    -- NULL until something has actually opened the file. The uploader knows the byte
    -- count and the MIME type from the browser; it does not know the sample rate, and
    -- guessing 44100 would be exactly the silent default this codebase refuses.
    sample_rate  INT,
    channels     INT,
    duration_ms  INT,

    state        STRING NOT NULL DEFAULT 'pending',

    -- Always 'asserted' today: a human uploaded this file and said what it is. The
    -- column exists because a generated instrumental or an auto-cut radio edit is a
    -- different claim, and `SCOPE-RESET §2a` rule 1 says that difference is recorded
    -- at write time or it is lost.
    provenance   STRING NOT NULL DEFAULT 'asserted',

    -- A remaster supersedes rather than overwrites, same as `party_fact` and `lesson`.
    -- The old master stays readable because "what did we service to radio in March"
    -- is a question with a right answer.
    supersedes_id UUID REFERENCES recording_asset(id) ON DELETE SET NULL,

    uploaded_by  STRING NOT NULL DEFAULT '',
    uploaded_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT asset_kind_known
        CHECK (kind IN ('master', 'instrumental', 'stem', 'radio_edit', 'preview')),

    CONSTRAINT asset_state_known
        CHECK (state IN ('pending', 'stored')),

    CONSTRAINT asset_provenance
        CHECK (provenance IN ('measured', 'inferred', 'asserted')),

    -- The same one-row cycle migration 014 closed on `party_fact` and `lesson`. Not a
    -- guard against a longer cycle; see that migration's comment for exactly what this
    -- does and does not buy.
    CONSTRAINT asset_no_self_supersede
        CHECK (supersedes_id IS NULL OR supersedes_id != id),

    -- An empty key or hash is a row that cannot address its own object. Both are
    -- written by us, never by a form, so this catches a bug rather than a user.
    CONSTRAINT asset_addressable
        CHECK (object_key != '' AND content_hash != '' AND bucket != ''),

    -- A zero-byte master is not a master. NULL is allowed — see `bytes` above.
    CONSTRAINT asset_bytes_positive
        CHECK (bytes IS NULL OR bytes > 0),

    -- `state = 'stored'` is the claim that the object is there, and the timestamp is
    -- when we confirmed it. One without the other is a row that cannot say whether it
    -- has been checked, which is the ambiguity `state` was added to remove.
    CONSTRAINT asset_stored_has_a_time
        CHECK ((state = 'stored') = (uploaded_at IS NOT NULL)),

    -- The composite key argued for in the header. CASCADE because an asset without its
    -- recording is unreachable by every query in the product.
    CONSTRAINT asset_recording_in_same_tenant
        FOREIGN KEY (tenant_id, recording_id)
        REFERENCES recording (tenant_id, id) ON DELETE CASCADE
);


-- One row per distinct file per kind. See the header for why the recording is
-- deliberately absent from this key, and for what a caller must check on conflict.
CREATE UNIQUE INDEX IF NOT EXISTS asset_one_row_per_file
    ON recording_asset (tenant_id, kind, content_hash);


-- The inspector's query: every asset on this recording, newest first. `STORING` the
-- display columns keeps the panel a single index read — it renders `kind`, `label`,
-- `state` and `bytes` and never the key.
CREATE INDEX IF NOT EXISTS asset_by_recording
    ON recording_asset (tenant_id, recording_id, kind, created_at DESC)
    STORING (label, state, bytes, mime, duration_ms, object_key, bucket);


-- The worker's query: what is stored and not yet analysed. `analyse_recording` claims
-- a lead per recording, so this serves the "is there anything to listen to" lookup
-- that decides whether the lead can run at all.
CREATE INDEX IF NOT EXISTS asset_stored_by_kind
    ON recording_asset (tenant_id, kind, state, created_at DESC)
    WHERE state = 'stored';
