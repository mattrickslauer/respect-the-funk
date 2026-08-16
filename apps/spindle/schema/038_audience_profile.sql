-- =============================================================================
-- 038_audience_profile.sql — the one creator whose audience we may lawfully read
-- =============================================================================
--
-- `037_creator_scout.sql` closed open decision 4 by finding a wall, and stated the wall
-- precisely enough that this migration knows exactly which door it is walking through:
--
--     "**Instagram Graph API** returns audience data only with the creator's own OAuth
--      grant. Pillar 10 §1: *There is no endpoint on any platform that returns an
--      arbitrary creator's follower demographics. No exception, no partner tier, and no
--      price.*"
--
-- Every word of that still holds. What changes here is not the rule but the subject: the
-- roster artist is not an arbitrary creator. She can grant, and has. `037` is about
-- reading strangers and refuses to; this is about reading ourselves and does not have to.
--
-- So the two migrations are not in tension and the asymmetry is worth stating plainly,
-- because a reader who meets `038` first will otherwise conclude that `037`'s "no
-- endpoint, no exception" was defeatism:
--
--   * `037` — **counterparties**, whose data we may not have. The operator is the adapter.
--   * `038` — **our own roster**, whose data they hand us. OAuth is the adapter.
--
-- A row here is only ever about a party the tenant represents. That rule is enforced in
-- `audience.refresh_audience`, which reads `party.party_class` and refuses anything but a
-- roster party — and it is worth saying why it is *not* also a constraint here, because
-- every other invariant in this schema is: a `CHECK` may not reference another table, so
-- "the subject is one of ours" is not expressible as one. `test_audience.py` asserts the
-- refusal instead. A rule proved by a test rather than by the database is a weaker rule,
-- and naming which kind it is beats implying the stronger one.
--
--
-- ## Why this is a stored snapshot and not `AS OF SYSTEM TIME`
--
-- The first design of this table did not exist: the plan was to write the current
-- audience over the top each refresh and let CockroachDB's time travel serve history,
-- since `budgets.replay` already reads `AS OF SYSTEM TIME` and the machinery was right
-- there. That plan is wrong, and it is wrong by a factor of about three hundred.
--
-- `AS OF SYSTEM TIME` reaches back exactly as far as the garbage collection window and
-- not one second further. Measured on this cluster rather than read off the documentation
-- (`SHOW ZONE CONFIGURATION FROM RANGE default`, 2026-08-15):
--
--     gc.ttlseconds = 4500
--
-- **Seventy-five minutes.** The published defaults are 14400 and 90000 depending on which
-- page you read; CockroachDB Cloud Basic ships neither. A question worth asking about an
-- audience — did the Berlin share grow after the March release — is a question about
-- months, and every one of those instants was collected while nobody was looking. The
-- feature would have worked perfectly for the whole of its development and returned
-- `HistoryExpired` the first time it was asked something interesting.
--
-- Hence: history is rows, and rows are immutable. A refresh **appends** a new
-- `captured_at` and supersedes nothing, which is `011_lesson.sql`'s rule about inferred
-- values arriving by insert rather than by overwrite, applied to a table that would
-- otherwise have been the exception.
--
-- Time travel keeps the job it is actually good at, and `budgets.replay` already does it:
-- reconstructing *one consistent instant across every table at once* for a decision made
-- inside the window. Two mechanisms, two jobs, and the difference written down here so
-- that the next person to reach for `AS OF SYSTEM TIME` reaches for it on purpose:
--
--   * **This table** — how the audience changed over months. Durable, arbitrary reach.
--   * **`AS OF SYSTEM TIME`** — what the whole database looked like at the instant a
--     shortlist was decided. Total consistency, 75 minutes of reach.
--
--
-- ## Why an absent demographic is a recorded state and not an absent row
--
-- `follower_demographics` is not returned at all below 100 followers, and the grant can
-- be revoked between one refresh and the next. Both produce the same thing at the API
-- boundary — no breakdown — and writing that as "no `audience_segment` rows" would make
-- three different situations identical in the database:
--
--   1. we have never fetched this artist,
--   2. we fetched and the account is below the threshold,
--   3. we fetched and she revoked the grant.
--
-- A shortlist that silently skips its geographic rerank because of (2) while the operator
-- believes (1) is the exact failure this repository refuses everywhere else — the console
-- would show a ranking that looks audience-aware and is not. So `demographics_state`
-- carries the answer explicitly and `audience_profile_absence_is_explained` makes an
-- unexplained absence unwritable. The console reads that column and says which it is.


-- ---------------------------------------------------------------- the snapshot ---

CREATE TABLE IF NOT EXISTS audience_profile (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenant (id) ON DELETE CASCADE,

    --: Whose audience. A roster party — ours — never a counterparty. Enforced by
    --: `audience.refresh_audience` reading `party.party_class`, not by a constraint; the
    --: header says why that is the weaker guarantee and why it is the available one.
    party_id        UUID NOT NULL REFERENCES party (id) ON DELETE CASCADE,

    --: `instagram` today. YouTube is the same shape — an aggregate breakdown of people
    --: we may not enumerate — so it arrives as another value here and another adapter,
    --: not as another table. That is the whole reason this column exists ahead of its
    --: second value, and `038` is a cheaper place to put it than a later `ALTER`.
    platform        STRING NOT NULL,

    --: The account as the platform names it, kept so a re-grant against a *different*
    --: account is visible as a different subject rather than as a discontinuity in one
    --: artist's history that nobody can explain.
    account_ref     STRING NOT NULL DEFAULT '',

    --: The headline number, and the only absolute count on this table. Everything else
    --: is a share, because a share is what survives the account growing.
    follower_count  INT8 NULL,

    --: Whether the breakdown below exists, and if not, why not. See the header.
    demographics_state STRING NOT NULL DEFAULT 'present',
    --: The platform's own words when it declined, verbatim and untranslated. An error
    --: this module paraphrased is an error nobody can search the vendor's docs for.
    unavailable_detail STRING NOT NULL DEFAULT '',

    --: When the platform computed it, and when we asked. They are not the same instant
    --: and the gap matters: Instagram's demographics lag, so a profile captured at noon
    --: describes an audience as it was somewhat earlier. Recording both means a later
    --: reader can tell a stale fetch from a stale audience.
    observed_at     TIMESTAMPTZ NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    --: The lead whose run produced this, for the same reason `party_document` carries
    --: one: a row that cannot name the run that wrote it cannot be audited backwards.
    lead_id         UUID NULL REFERENCES lead (id) ON DELETE SET NULL,

    CONSTRAINT audience_profile_platform_known
        CHECK (platform IN ('instagram', 'youtube')),

    --: The three states above, plus the one that matters most: `not_granted` is written
    --: when a token that used to work stops working, and it is distinct from
    --: `below_threshold` because the operator's next action is completely different —
    --: one is a conversation with the artist, the other is arithmetic.
    CONSTRAINT audience_profile_demographics_state_known
        CHECK (demographics_state IN ('present', 'below_threshold', 'not_granted')),

    --: An absence has to say why. `present` needs no detail and is allowed none, so the
    --: column cannot become a second, contradictory account of a profile that is fine.
    CONSTRAINT audience_profile_absence_is_explained
        CHECK ((demographics_state = 'present' AND unavailable_detail = '')
               OR (demographics_state != 'present' AND unavailable_detail != '')),

    CONSTRAINT audience_profile_follower_count_is_not_negative
        CHECK (follower_count IS NULL OR follower_count >= 0)
);


-- One artist's history, newest first — the only way this table is ever read. `STORING`
-- the state and the count means the console's "what do we know about her audience" panel
-- is served from the index without touching the table.
CREATE INDEX IF NOT EXISTS audience_profile_by_party
    ON audience_profile (tenant_id, party_id, platform, captured_at DESC)
    STORING (follower_count, demographics_state, observed_at);


-- ---------------------------------------------------------------- the breakdown ---
--
-- One row per (profile, dimension, value). Instagram returns `age`, `city`, `country`
-- and `gender`; YouTube returns the same shape under different names and the adapter is
-- responsible for the translation, so that a consumer of this table never has to know
-- which platform a segment came from to interpret it.

CREATE TABLE IF NOT EXISTS audience_segment (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant (id) ON DELETE CASCADE,

    --: `ON DELETE CASCADE`, and this one is a real cascade rather than the hand-rolled
    --: sweep `011` needed: a segment has exactly one parent and no polymorphism, so the
    --: foreign key can do its job.
    profile_id   UUID NOT NULL REFERENCES audience_profile (id) ON DELETE CASCADE,

    dimension    STRING NOT NULL,
    --: The bucket, as the platform spells it: `US`, `Chicago, Illinois`, `25-34`, `F`.
    --: Not normalised on the way in — `sources.py`'s rule that a harvest stores what it
    --: was given and interprets later, so a mapping bug is fixable without a re-fetch.
    value        STRING NOT NULL,

    --: The fraction of the audience in this bucket, 0..1. A share and not a count,
    --: because Instagram gives percentages for some breakdowns and counts for others and
    --: a table that stored whichever arrived would be uncomparable with itself.
    share        FLOAT NOT NULL,

    CONSTRAINT audience_segment_dimension_known
        CHECK (dimension IN ('age', 'city', 'country', 'gender')),

    CONSTRAINT audience_segment_share_is_a_fraction
        CHECK (share >= 0 AND share <= 1)
);


-- The geographic rerank's query: one profile's countries or cities, largest share first.
-- `STORING (share)` because the rank reads nothing else.
CREATE INDEX IF NOT EXISTS audience_segment_by_dimension
    ON audience_segment (tenant_id, profile_id, dimension, share DESC)
    STORING (value);


-- A profile may not carry the same bucket twice. Without this, a partial retry that
-- re-wrote the breakdown would double every share and the rerank would read an audience
-- twice its real size — silently, because the shares are individually still legal.
CREATE UNIQUE INDEX IF NOT EXISTS audience_segment_one_row_per_bucket
    ON audience_segment (tenant_id, profile_id, dimension, value);


-- ---------------------------------------------------------------- the source ---
--
-- `oauth`, and it is the first source in this manifest with that auth mode. Every other
-- entry is a key we hold (`podcast_index`) or nothing at all (`radio_browser`); this one
-- is a grant a person makes and can withdraw, which is why `demographics_state` exists
-- and why `enabled` here means "we have somewhere to put the grant", not "we have one".
INSERT INTO source_manifest
    (platform, adapter, accepts, emits, yields, subject_kinds,
     auth, cost_class, rate_per_sec, quota_per_day, cadence_seconds,
     enabled, disabled_reason)
VALUES
    ('instagram_graph', 'instagram_oauth',
     ARRAY['party_id'],
     ARRAY['follower_count', 'audience_segment', 'media_caption'],
     ARRAY['audience', 'catalogue'], ARRAY['party'],
     'oauth', 'free', 1, NULL, 86400,
     true, '')
ON CONFLICT (platform) DO NOTHING;


-- ---------------------------------------------------------------- the agents ---
--
-- Two, and the split is the `NetworkAgent` boundary made structural rather than internal:
--
--   * `refresh_audience` talks to Instagram and writes `audience_profile` plus its
--     segments. It is the only thing here that leaves the building.
--   * `profile_audience` reads those rows back, composes the prose an embedding can be
--     taken of, and queues `embed_party` — exactly what `profile_creator` does with
--     `sound_usage`, and deliberately the same shape so that the artist's audience lands
--     in the *same* vector space as every counterparty rather than a private one.
--
-- Splitting them means a revoked grant stops the fetch without stranding the composition,
-- and a composition bug is re-runnable without spending another call on a platform whose
-- rate limits we do not control.
--
-- `enabled` is absent from both DO UPDATE lists, per `023`.
INSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters,
                            writes, batch_size, per_artist_cap, lease_seconds,
                            max_attempts, backoff_seconds, requires_human, enabled,
                            updated_at)
VALUES
    ('refresh_audience', 'lead', 'pending', ARRAY['party'], ARRAY['instagram_graph'],
     ARRAY['audience_profile', 'audience_segment', 'lead'],
     5, 0, 300, 4, ARRAY[60, 300, 1800, 7200], false, true, now()),
    ('profile_audience', 'lead', 'pending', ARRAY['party'], ARRAY[]::STRING[],
     ARRAY['party_document', 'lead'],
     20, 0, 120, 4, ARRAY[60, 300, 1800, 7200], false, true, now())
ON CONFLICT (kind) DO UPDATE SET
    adapters = excluded.adapters,
    writes = excluded.writes,
    batch_size = excluded.batch_size,
    lease_seconds = excluded.lease_seconds,
    updated_at = now();
