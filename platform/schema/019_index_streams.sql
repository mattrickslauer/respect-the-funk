-- =============================================================================
-- 019_index_streams.sql — the manifest row for the genre stage
-- =============================================================================
--
-- `agent_manifest` is what `/fleet` reads; `agents.REGISTRY` is what the fleet runs.
-- Migration 017 was written because those two had drifted for a second time, and the
-- only way that stops happening is if adding an agent means adding its row in the same
-- change. `index_streams` is added here for that reason and no other.
--
-- `adapters` is `radio_browser` — a community database, not a government register, and
-- the distinction is carried all the way down into the `asserted` provenance on every
-- genre fact this stage writes.
--
-- `batch_size` is 2 rather than 5. A page is 500 stations and each one can touch six
-- tables, so a batch of five would hold one transaction across 2,500 stations. The lease
-- is 600s for the same reason: a page that enriches heavily is a long write, and a lease
-- that expires mid-page hands the work to a second worker that will redo all of it.

INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('index_streams', 'lead', 'pending', ARRAY['tenant'], ARRAY['radio_browser'],
        ARRAY['party', 'party_role', 'party_identifier', 'party_fact',
              'party_document', 'presence', 'lead'],
        2, 0, 600, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    enabled = excluded.enabled,
    updated_at = now();
