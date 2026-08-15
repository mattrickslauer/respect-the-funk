-- =============================================================================
-- 022_enrich_genre.sql — the manifest row for the Wikipedia format stage
-- =============================================================================
--
-- Added in the same change as the agent, for the reason migration 017 exists: the
-- manifest and the registry have drifted twice, and the only thing that stops it is
-- never shipping one without the other.
--
-- `adapters` is `wikipedia`. `requires_human` is false — the stage writes a fact only
-- when an infobox names a format, and writes nothing at all when it does not, so there
-- is no guess for a human to adjudicate. That is the whole difference between this and
-- a classifier keying off a licensee's name.
--
-- `batch_size` 1 and `lease_seconds` 900: one lead makes up to six HTTP round trips to
-- a service answering for free, and a batch of five would make thirty inside one lease.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('enrich_genre', 'lead', 'pending', ARRAY['tenant'], ARRAY['wikipedia'],
        ARRAY['party_fact', 'party_document', 'lead'],
        1, 0, 900, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    enabled = excluded.enabled,
    updated_at = now();
