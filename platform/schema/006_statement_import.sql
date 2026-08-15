-- =============================================================================
-- 006_statement_import.sql — the file a distributor gave us
-- =============================================================================
--
-- Real stream counts do not come from a DSP's API. They come back down the supply
-- chain as DSR, monthly, per ISRC per territory, and what a label actually holds is
-- whatever its distributor passes on — a raw DSR file, an API, or a spreadsheet
-- exported from a dashboard. DistroKid is the third kind, which is why
-- `spindle/distributors/` reads files rather than fetching anything.
--
-- This table is evidence, in the same sense `party_document` is: it records the file
-- itself, not a claim derived from it. The measurements land in `party_metric` and
-- point back here.
--
-- `content_hash` is unique per tenant, and that is the whole idempotency story. An
-- operator uploading the same statement twice is not an error worth a message — it
-- is a Tuesday — but it must not double every stream count in the database. The
-- constraint makes the second upload a no-op instead of a silent corruption.
--
-- Verified against CockroachDB CCL v26.2.5 on respect-the-funk-31317.
-- =============================================================================

CREATE TABLE IF NOT EXISTS statement_import (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    distributor   STRING NOT NULL,
    -- Which reader was used, not just which company sent it. A distributor may have
    -- several report shapes over time, and knowing the file was read as
    -- `distrokid.bank-tsv` is what makes a later re-read reproducible.
    format_key    STRING NOT NULL,
    -- False when the reader's column map has never been checked against a real
    -- export. Recorded per import rather than looked up later, because the flag can
    -- change once somebody confirms a format and the row should keep saying what was
    -- true when it was written.
    format_verified BOOL NOT NULL DEFAULT false,

    filename      STRING NOT NULL DEFAULT '',
    content_hash  STRING NOT NULL,
    byte_size     INT NOT NULL DEFAULT 0,

    rows_read     INT NOT NULL DEFAULT 0,
    rows_loaded   INT NOT NULL DEFAULT 0,
    -- Rows read but not loaded, and why. A statement line with no usable ISRC
    -- cannot attach to a recording, and dropping it silently would make the totals
    -- disagree with the file for no visible reason.
    rows_no_isrc  INT NOT NULL DEFAULT 0,
    recordings_created INT NOT NULL DEFAULT 0,
    -- Artist names in the file that match no party on the roster. Kept so the
    -- operator can attribute them; never used to invent a party from a string.
    unmatched_artists STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],

    period_start  DATE,
    period_end    DATE,
    total_quantity BIGINT NOT NULL DEFAULT 0,
    total_earnings DECIMAL NOT NULL DEFAULT 0,
    currency      STRING NOT NULL DEFAULT 'USD',

    state         STRING NOT NULL DEFAULT 'loaded',   -- loaded | refused
    error         STRING NOT NULL DEFAULT '',
    imported_by   STRING NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT statement_state CHECK (state IN ('loaded', 'refused')),
    UNIQUE (tenant_id, content_hash)
);

CREATE INDEX IF NOT EXISTS statement_recent
    ON statement_import (tenant_id, created_at DESC);

-- Which import a measurement came from. `party_metric` already carries `source`
-- as free text; this is the edge that survives a rename of the file.
ALTER TABLE party_metric
    ADD COLUMN IF NOT EXISTS statement_import_id UUID
    REFERENCES statement_import(id) ON DELETE CASCADE;

-- One reading per recording, per store, per territory, per period, per metric.
-- Re-importing a corrected statement should replace a month rather than add a
-- second answer for it, and without this the two would sit side by side with
-- nothing to say which is current.
CREATE UNIQUE INDEX IF NOT EXISTS metric_one_per_period
    ON party_metric (tenant_id, recording_id, platform, territory, metric, period_start)
    WHERE recording_id IS NOT NULL AND period_start IS NOT NULL;
