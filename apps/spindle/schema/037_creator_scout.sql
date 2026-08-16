-- =============================================================================
-- 037_creator_scout.sql — UGC creators, indexed the only way this repo allows
-- =============================================================================
--
-- `SCOPE-RESET.md §6` open decision 4 has been open since 2026-08-06:
--
--     "Counterparty acquisition method. Pillar 10 §4's verdict is 'no scraper, ever,'
--      and its §5 finding is that manual sound-page browsing is both compliant and the
--      highest-signal path. A human-in-the-loop scout that surfaces candidates for bulk
--      human acceptance is the middle option. Still open — and it is now the most urgent
--      of these, because it gates the Scout agent."
--
-- This migration takes the middle option, and takes it deliberately rather than by
-- default. What follows is why the obvious alternatives were refused.
--
--
-- ## Why there is no fetch here, and why that is the finding rather than the gap
--
-- Every other source in `source_manifest` is an HTTP call: `fcc`, `radio_browser`,
-- `podcast_index`. This one is a person. That asymmetry is not an unfinished adapter and
-- a later migration will not "complete" it, so it is worth stating exactly what was
-- checked, because a reader's first instinct is that we ran out of time.
--
-- Pillar 10 §5a, measured July 2026:
--
--   * **TikTok Research API** exposes a `music_id` query field — precisely the "who used
--     this sound" question we want — and is restricted to verified academic and
--     non-profit institutions, takes about four weeks to approve, and *explicitly
--     prohibits commercial use*. It is the right endpoint and we may not have it.
--   * **TikTok Commercial Music Library** is a licensing catalogue, not an analytics one.
--   * **TikTok Creative Center** publishes aggregate trends, not per-sound creator lists.
--   * **Instagram Graph API** returns audience data only with the creator's own OAuth
--     grant. Pillar 10 §1: *"There is no endpoint on any platform that returns an
--     arbitrary creator's follower demographics. No exception, no partner tier, and no
--     price."*
--   * **Unofficial scrapers** (Apify's sound-library actors and the rest) do return the
--     handles. Pillar 10 §4's verdict, carried forward by `fcc.py`'s header as the
--     standing rule of this codebase, is *no scraper, ever* — reached on enforcement
--     grounds rather than legal ones. hiQ won the CFAA question twice and went out of
--     business anyway.
--
-- So the compliant, highest-signal path is the one Pillar 10 §5c actually recommends:
-- open the sound pages of adjacent artists, read who used them, and type that list in.
-- At one artist and dozens of creators that is *a person-afternoon, not an engineering
-- project* — and the rows it produces are `measured` behavioural evidence rather than a
-- vendor's 60%-confidence guess at an age bracket.
--
-- **The operator is the adapter.** `source_manifest.adapter = 'manual_scout'` says so in
-- the one place the fleet reads. This source is `enabled` because nothing is missing from
-- it: no key, no entitlement, no unwritten worker. Contrast `podcast_index` in `023`,
-- which is disabled for a key that exists and has not been fetched. Disabling this one
-- would file a deliberate design decision under the same heading as an errand.
--
--
-- ## What a creator's "vibe" is here, and what it deliberately is not
--
-- The ask that produced this migration was to *look at their videos and embed their
-- vibe*. We cannot look at their videos — see above — and the interesting part is that
-- we do not need to.
--
-- A creator's vibe, for the only purpose we have (will this person's audience sit still
-- for this record?), is **the music they already choose to post over**. That is not a
-- proxy for the signal; on Pillar 10 §5's argument it *is* the signal, and it beats the
-- demographic estimate we are structurally unable to buy:
--
--     "If a creator already posted with an adjacent artist's sound and it performed,
--      that is measured behavioural evidence their audience responds to this music —
--      categorically better than an inferred demographic guess. It skips the entire
--      estimation problem."
--
-- So `sound_usage` below is the vibe. A creator who posts over 124bpm four-on-the-floor
-- house is a creator whose audience has demonstrated a tolerance for 124bpm
-- four-on-the-floor house, and `analyse_recording` already measures exactly those
-- properties off the audio with Discogs-EffNet.
--
--
-- ## The embedding-space constraint that shaped this table
--
-- The temptation was to embed creators from audio directly and get a "real" vibe vector.
-- That would have been wrong here for a mechanical reason worth recording, because it is
-- not obvious and it is expensive to discover late.
--
-- `party_shortlist` is `(tenant_id, embedding_model, party_class, contact_state)` before
-- the vector. `embedding_model` is *inside the index prefix*. Two embedding spaces in one
-- table are therefore two disjoint index prefixes: an artist embedded by
-- `openai:text-embedding-3-small` and a creator embedded by an audio model can never
-- appear in one another's nearest-neighbour results, no matter how similar they are. The
-- shortlist would return radio stations and silently omit every creator, and it would
-- look like it worked.
--
-- The fix is the pattern `profile_party` already established for our own roster: compose
-- what is known into text from structured rows, embed *that* with the one model, and let
-- `embed_party` do it. `profile_creator` in `spindle/creators.py` composes from the
-- measured audio facts of the sounds a creator posts over. One space, one index, one
-- query — pitching a dance record to creators is the identical statement that shortlists
-- radio programmers, with no new machinery and no second code path to keep correct.
--
-- `profile_party`'s comment applies here unchanged and is the reason the composed text
-- reads flat: *"every clause here is a row somebody can go and check, and a fluent
-- biography generated by a model would be an `inferred` fact wearing the costume of an
-- `asserted` one."*
--
--
-- ## Provenance, which is the whole discipline
--
-- `SCOPE-RESET.md §2a` rule 1 says every fact carries how it was obtained, and that an
-- inferred value may never overwrite a measured one. Creator rows are the easiest place
-- in this system to break that, because the vendor market sells estimates that look like
-- measurements. Two classes appear here and the third is refused:
--
--   * **measured** — the sound's musical properties, when the sound is a recording this
--     system has analysed. BPM and key come off the audio.
--   * **asserted** — a genre the operator typed while reading the sound page, and the
--     follower count they read off the profile. A human stated it; it is timestamped and
--     it decays.
--   * **inferred** — audience age, gender and country splits. **Not stored, at any
--     confidence.** There is no honest source for them (Pillar 10 §1) and a column would
--     eventually be filled by somebody with a vendor export. Pillar 10 §7's note is the
--     standing warning: *"never render an estimated share next to a measured one."* The
--     cheapest way to honour that is to have nowhere to put one.
--
-- When a creator is onboarded and shares their own analytics screenshot — Pillar 10 §5c
-- step 5, which is how rate negotiation works anyway — that arrives as an `asserted` fact
-- with a human behind it, through the same `party_fact` path as everything else. It needs
-- no column here.
--
--
-- ## Why nothing in this migration can send an email
--
-- Creators arrive as `suggestion` rows, not as contactable counterparties. `009` and the
-- suggestion queue already require a human to accept an inferred party before it can be
-- contacted, and this migration adds no route of any kind. A creator becomes reachable
-- only when somebody records a `contact_route` for them, and `018`'s rules then apply
-- unchanged: an `inferred` route is refused outright by `sender._prepare` rather than
-- ranked lower, and `opted_out` is terminal. Doc 14 §9 states the resulting shape
-- exactly: *"the UGC channel is not blocked on a source, it is blocked on a route class.
-- A creator who publishes a business email in their bio has declared a route; a creator
-- who has not, has not."*
--
-- =============================================================================


-- ---------------------------------------------------------------- sound_usage ---
--
-- One row per (creator, sound) they have posted over. Pillar 10 §7 calls this the
-- shortlisting key — *"rank by 'used an adjacent artist's sound and beat their own
-- median', not by demographics"* — which is why `view_count` and `creator_median_views`
-- sit next to each other rather than in separate tables. The ratio is the judgement and
-- it needs both numbers in one row to be computable without a join.
--
-- `recording_id` is nullable and is the hinge of the whole design. When it is set, the
-- sound is a recording this system has analysed and the creator inherits *measured*
-- musical facts. When it is NULL the sound belongs to somebody whose audio we do not
-- have, and `asserted_genre` carries what the operator read off the page instead. Both
-- are legitimate; they are not the same standing, and the schema refuses to let a reader
-- confuse them by making the distinction structural rather than a convention.
CREATE TABLE IF NOT EXISTS sound_usage (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             UUID NOT NULL REFERENCES tenant (id),

    --: The creator. A `party` like any other, so every guarantee in `010` and `018`
    --: applies without a second compliance regime — the point `SUBMISSION.md` item 7
    --: makes about channels being data rather than code.
    creator_party_id      UUID NOT NULL REFERENCES party (id),

    platform              STRING NOT NULL,

    --: What they posted over. `sound_ref` is always present and human-readable
    --: ("Artist — Title"); `recording_id` is set only when we hold and have analysed
    --: that audio.
    sound_ref             STRING NOT NULL,
    recording_id          UUID NULL REFERENCES recording (id),

    --: What the operator read off the sound page. `asserted` provenance by construction:
    --: a person typed it while looking at it.
    asserted_genre        STRING NOT NULL DEFAULT '',

    --: Performance, in the only form that means anything. An absolute view count says
    --: nothing without the creator's own baseline beside it.
    view_count            INT8 NULL,
    creator_median_views  INT8 NULL,

    --: Timestamped because it decays fast — Pillar 10 §7: *"follower counts and
    --: demographics both decay; vendor guidance treats >3 months as unreliable."*
    observed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    --: Who recorded it. Always a person for now; a column rather than an assumption so
    --: that the day a compliant API does arrive, the rows it writes are distinguishable
    --: from the rows a human typed without archaeology.
    written_by            STRING NOT NULL DEFAULT 'manual_scout'
);

--: A creator posting the same sound twice is one relationship, not two. Enforced rather
--: than deduplicated on read, so a scout pasting an overlapping list twice is a no-op
--: instead of a doubled view count.
CREATE UNIQUE INDEX IF NOT EXISTS sound_usage_once
    ON sound_usage (tenant_id, creator_party_id, platform, sound_ref);

--: `tenant_id` leads, as it does on every index in this schema — the lint in
--: `test_tenant_scoping.py` is a real predicate check and will fail if it does not.
CREATE INDEX IF NOT EXISTS sound_usage_by_creator
    ON sound_usage (tenant_id, creator_party_id, observed_at DESC);

--: The reverse question: who has posted over this recording of ours? This is the one
--: that gets asked when a release goes out.
CREATE INDEX IF NOT EXISTS sound_usage_by_recording
    ON sound_usage (tenant_id, recording_id, observed_at DESC)
    WHERE recording_id IS NOT NULL;

ALTER TABLE sound_usage DROP CONSTRAINT IF EXISTS sound_usage_platform_known;
ALTER TABLE sound_usage ADD CONSTRAINT sound_usage_platform_known
    CHECK (platform IN ('tiktok', 'instagram', 'youtube'));

--: A sound has to be identifiable. An empty `sound_ref` would make the unique index
--: above collapse every unattributed post by one creator into a single row.
ALTER TABLE sound_usage DROP CONSTRAINT IF EXISTS sound_usage_ref_present;
ALTER TABLE sound_usage ADD CONSTRAINT sound_usage_ref_present
    CHECK (length(sound_ref) > 0);

--: Counts are counts. A negative view count means a parse went wrong upstream, and
--: `NULL` is the honest way to say "not read", so there is no reason to admit one.
ALTER TABLE sound_usage DROP CONSTRAINT IF EXISTS sound_usage_counts_sane;
ALTER TABLE sound_usage ADD CONSTRAINT sound_usage_counts_sane
    CHECK ((view_count IS NULL OR view_count >= 0)
       AND (creator_median_views IS NULL OR creator_median_views >= 0));


-- ---------------------------------------------------------------- the source ---
--
-- `enabled` is true and `disabled_reason` is empty because nothing is missing. See the
-- header: the adapter is a person, by a decision recorded in Pillar 10 §4, and filing
-- that under the same flag as "needs an API key" would misreport a conclusion as an
-- errand.
--
-- `cadence_seconds` is NULL rather than a number, and it is the column that carries the
-- meaning here. There is no polling interval for a human reading sound pages, and a
-- cadence would eventually be read as a promise that something wakes up.
--
-- `rate_per_sec` is `1` only because the column is NOT NULL and has to hold something.
-- It is meaningless for this source — there is no request to rate-limit — and it is
-- recorded here rather than left for a reader to infer significance from. The column
-- that actually says "nothing polls this" is `cadence_seconds`, immediately above it.
INSERT INTO source_manifest
    (platform, adapter, accepts, emits, yields, subject_kinds,
     auth, cost_class, rate_per_sec, quota_per_day, cadence_seconds,
     enabled, disabled_reason)
VALUES
    ('tiktok_sound_page', 'manual_scout',
     ARRAY['sound_ref', 'handle'],
     ARRAY['handle', 'url', 'sound_ref'],
     ARRAY['identity', 'catalogue'], ARRAY['party'],
     'none', 'free', 1, NULL, NULL,
     true, '')
ON CONFLICT (platform) DO NOTHING;


-- ---------------------------------------------------------------- the agent ---
--
-- `profile_creator` composes a creator's profile text from their `sound_usage` rows and
-- queues `embed_party`, exactly as `profile_party` does for our own roster. It makes no
-- network call — everything it reads is already in this database — which is why it has
-- no `adapters` entry and why its `lease_seconds` is short.
--
-- `enabled` is deliberately absent from the DO UPDATE list, following `023`: re-applying
-- this migration must never silently switch an agent back on that somebody turned off.
INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES ('profile_creator', 'lead', 'pending', ARRAY['party'], ARRAY[]::STRING[],
        ARRAY['party_document', 'party_fact', 'lead'],
        20, 0, 120, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
