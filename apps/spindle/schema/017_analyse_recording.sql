-- =============================================================================
-- 017_analyse_recording.sql — two agents that exist in code and not in the manifest
-- =============================================================================
--
-- `agent_manifest` is what the fleet view reads. `agents.REGISTRY` is what the fleet
-- actually runs. Migration 010 seeded the manifest with five implemented agents and
-- three declared-but-unwritten ones, and made the point in its own comment that the
-- table holding one row while the registry held five meant the view "could only ever
-- have been either a fixture or wrong".
--
-- It drifted again. Checked against the live cluster before writing this:
--
--     SELECT kind, enabled FROM agent_manifest ORDER BY kind;
--       drafter False | embed_document True | embed_party True |
--       find_counterparties True | forager True | inbox False |
--       map_source True | profile_party True | sender False
--
-- `distil_lesson` is absent, and it has been absent since it was written (`fde8688`).
-- The important part of that: **it still runs**. Nothing in `fleet.py` reads
-- `agent_manifest` — only `research.fleet` and the console's enable toggle do — so the
-- memory loop has been live and merely invisible, and `research.fleet` has been
-- reporting it in the branch that says "this name has agent_run rows and no row in
-- agent_manifest", which reads as an anomaly rather than as an agent. The off switch
-- also does not work for an agent with no row to toggle.
--
-- This migration adds it, and adds `analyse_recording` alongside.
--
--
-- ## Why the two are added together
--
-- They are the two halves of the same claim. `SCOPE-RESET §1` says release n+1 lands
-- harder than release n because something accumulates between them; `distil_lesson`
-- accumulates what worked with a counterparty, and `analyse_recording` accumulates what
-- is true about the record. Neither was reachable from the console's fleet view.
--
--
-- ## `analyse_recording` is scoped to `recording`, and its adapter is `s3`
--
-- `scope_kinds` is `ARRAY['recording']` alone — not `['party','recording']` like
-- `find_counterparties`. A party has no master; only a recording does. `lead_scope_shape`
-- still requires a party id on a recording-scoped lead, and `assets.queue_analysis`
-- supplies the credited main artist, which is also the right party to bill the work to.
--
-- `adapters` is `ARRAY['s3']`, which is a new adapter name in this column and an honest
-- one: this agent's network dependency is a bucket, reached with a URL it signs itself.
-- Naming it `web_page` because that is the existing generic value would make the fleet
-- view claim this agent scrapes, and `apps/spindle/README.md`'s pillar 10 is "no scraper,
-- ever".
--
-- `writes` names three tables rather than one. It writes `party_fact` (the measurements),
-- `fact_basis` (the edge saying which asset each measurement listened to — the first
-- rows that table will hold on this cluster) and `recording_asset` (the sample rate,
-- channel count and duration that only opening the file can supply).
--
--
-- ## `requires_human` is false and `enabled` is true, and neither is a default
--
-- There is nothing to approve: the agent writes measurements, not messages. Every agent
-- with `requires_human = true` in migration 010 is one that would say something to a
-- person on the label's behalf, and that distinction is the one this column exists for.
--
-- `enabled = true` because the work is queued by an operator uploading a file. An agent
-- that only ever acts on something a human just did does not need a second switch to
-- stop it; not uploading is the switch.
--
--
-- ## UPSERT, for the reason 010 gives
--
-- So a rerun does not clobber an operator's `enabled` change. Turning an agent off is an
-- UPDATE rather than a deploy, and a migration that undid that would make the off switch
-- untrustworthy. `updated_at` is left alone for the same reason.

UPSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters, writes,
                            batch_size, per_artist_cap, requires_human, enabled)
VALUES
    -- The write half of the memory loop. Reads a closed thread, composes one sentence
    -- from its own columns — there is no language model in this repository — embeds it,
    -- and commits the lesson in the same transaction as the run record and the lead.
    ('distil_lesson', 'lead', 'pending', ARRAY['party'],
     ARRAY['openai'], ARRAY['lesson'],
     5, 3, false, true),

    -- Listen to the master. Batch size 1, unlike every other agent's 5: this one
    -- downloads tens of megabytes and shells out to ffmpeg per lead, so a batch of five
    -- would hold one lease window across five decodes and hand the last lead a deadline
    -- that was mostly spent before its turn. `fleet.renew` mitigates that and does not
    -- eliminate it, and there is no throughput argument on a roster this size.
    ('analyse_recording', 'lead', 'pending', ARRAY['recording'],
     ARRAY['s3'], ARRAY['party_fact', 'fact_basis', 'recording_asset'],
     1, 3, false, true);
