-- =============================================================================
-- 002_artist_type.sql — a band is an artist with a type
-- =============================================================================
--
-- One column, not a second table. A band, a DJ, a singer, an orchestra and a
-- composer are the same row shape: a thing that has a music release. What
-- differs is a label on the row, and occasionally how the outreach copy reads.
--
-- STRING rather than an ENUM, deliberately, for the same reason `channel` is a
-- STRING: the set is open. "Anything that has a music release" cannot be
-- enumerated in advance, and an ENUM would make adding `orchestra` an
-- ALTER TYPE — a deploy — rather than a value someone types once.
--
-- No lookup table either, yet. The UI offers the common values as suggestions
-- and accepts anything. If the roster later needs types to be strict, or needs
-- per-type outreach playbooks, that is the moment for `artist_type` to become a
-- table — not before.
-- =============================================================================

ALTER TABLE artist
    ADD COLUMN IF NOT EXISTS type STRING NOT NULL DEFAULT 'artist';

CREATE INDEX IF NOT EXISTS artist_by_type ON artist (tenant_id, type);
