-- =============================================================================
-- 004_research.sql — the frontier, the evidence, and the claims
-- =============================================================================
--
-- The tables behind the console's Queue, Runs, Facts and Artists views, from
-- `docs/superpowers/specs/2026-08-07-forager-agent-design.md` §5 and the agent
-- contract design §2c, §5a and §7.
--
-- Three shapes, because there are three kinds of truth and mixing them should be
-- a schema error rather than a discipline failure:
--
--   artist_document   evidence. Has quotes, no truth value. Never goes stale —
--                     a 2026 article saying a thing remains an accurate quote of
--                     that article forever.
--   artist_fact       claims. Have a truth value, a provenance class and a
--                     status. Superseded by chain, never updated in place.
--   artist_metric     measurements over time. Append only. A stream count is not
--                     a claim, it is a reading.
--
-- `fact_basis` is what makes them one system: what every claim stands on. Its
-- primary key serves the walk up (provenance) and `fact_basis_down` serves the
-- walk down (invalidation, and credit for an outcome). Two indexes, two
-- directions, one table. No foreign keys on it, deliberately — subject and basis
-- span a dozen tables and a polymorphic FK does not exist, so the referential
-- discipline is the runtime's. Same trade `counterparty_observation` makes.
--
-- Scope tiers are enforced by CHECK rather than by convention: tenant-wide work
-- genuinely has no artist, so `scope_kind` is a real column and not a filter.
--
-- Vector columns are here and indexed. Nothing writes them yet — this account's
-- Bedrock on-demand quota is 0 RPM, verified 2026-08-07 — but the index costs
-- nothing over NULLs and `feature.vector_index.enabled` is already on, so the
-- schema is the one we want rather than the one today's quota allows.
--
-- Verified against CockroachDB CCL v26.2.5 on respect-the-funk-31317.
-- =============================================================================

-- ----------------------------------------------------------------- the tracks

CREATE TABLE IF NOT EXISTS track (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    slug        STRING NOT NULL,
    title       STRING NOT NULL,
    isrc        STRING NOT NULL DEFAULT '',
    released_on DATE,
    status      STRING NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, artist_id, slug)
);

-- ------------------------------------------------- where we look, per artist

-- `mode` is the whole optionality story. An artist with no TikTok is `absent`
-- there and we still search TikTok, because fan activity is content about them
-- whether they take part or not. One code path for a one-account act and a
-- five-account one.
CREATE TABLE IF NOT EXISTS artist_profile (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id    UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    platform     STRING NOT NULL,
    mode         STRING NOT NULL DEFAULT 'absent',   -- owned | unowned | absent
    handle       STRING NOT NULL DEFAULT '',
    profile_url  STRING NOT NULL DEFAULT '',
    external_id  STRING NOT NULL DEFAULT '',
    enabled      BOOL NOT NULL DEFAULT true,
    confirmed_by STRING NOT NULL DEFAULT '',
    supersedes_id UUID,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, artist_id, platform)
);

-- Canonical identifiers. The anchor that stops name drift: a lead reaching no
-- identifier and arriving through no owned edge scores low and, past depth 2, is
-- rejected outright.
CREATE TABLE IF NOT EXISTS artist_identifier (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id  UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    kind       STRING NOT NULL,                      -- mbid | spotify | isni | …
    value      STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, artist_id, kind, value)
);

-- ------------------------------------------------------------- the frontier

CREATE TABLE IF NOT EXISTS lead (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    scope_kind   STRING NOT NULL DEFAULT 'artist',   -- tenant | artist | track
    artist_id    UUID REFERENCES artist(id) ON DELETE CASCADE,
    track_id     UUID REFERENCES track(id) ON DELETE CASCADE,

    kind         STRING NOT NULL,      -- profile|catalogue|metric|engagement|…
    mode         STRING NOT NULL DEFAULT 'auto',     -- auto | manual
    adapter      STRING NOT NULL,
    target       STRING NOT NULL,
    target_hash  STRING NOT NULL,      -- dedup key; the constraint, not a habit
    platform     STRING NOT NULL DEFAULT '',

    parent_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    depth        INT NOT NULL DEFAULT 0,
    reason       STRING NOT NULL DEFAULT '',
    score        FLOAT NOT NULL DEFAULT 0.5,

    state        STRING NOT NULL DEFAULT 'pending',
    -- NULL = one-shot discovery. Set = recurring poll. One nullable column is the
    -- whole difference between a crawler and a frontier, and it means routine
    -- metric polling rides the same table, claim query and worker as discovery.
    cadence_seconds INT,
    next_action_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_agent  STRING,
    lease_expires_at TIMESTAMPTZ,
    attempts     INT NOT NULL DEFAULT 0,
    last_error   STRING NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT lead_scope_shape CHECK (
        (scope_kind = 'tenant' AND artist_id IS NULL     AND track_id IS NULL)
     OR (scope_kind = 'artist' AND artist_id IS NOT NULL AND track_id IS NULL)
     OR (scope_kind = 'track'  AND artist_id IS NOT NULL AND track_id IS NOT NULL)
    ),
    UNIQUE (tenant_id, target_hash)
);

-- The claim query's index. Partial, because a done lead is most of the table
-- within a day and none of it is claimable.
CREATE INDEX IF NOT EXISTS lead_claimable
    ON lead (tenant_id, artist_id, next_action_at)
    STORING (score) WHERE state = 'pending';

-- ------------------------------------------------------------------ evidence

CREATE TABLE IF NOT EXISTS artist_document (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id    UUID REFERENCES artist(id) ON DELETE CASCADE,
    lead_id      UUID REFERENCES lead(id) ON DELETE SET NULL,
    platform     STRING NOT NULL DEFAULT '',
    url          STRING NOT NULL DEFAULT '',
    title        STRING NOT NULL DEFAULT '',
    body         STRING NOT NULL DEFAULT '',
    content_hash STRING NOT NULL,      -- fetched once, however many leads reach it
    mime         STRING NOT NULL DEFAULT '',
    lang         STRING NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    http_status  INT,
    gone_at      TIMESTAMPTZ,          -- 404 later; the row stays, the claim goes stale
    UNIQUE (tenant_id, content_hash)
);

CREATE TABLE IF NOT EXISTS artist_chunk (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID REFERENCES artist(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES artist_document(id) ON DELETE CASCADE,
    ordinal     INT NOT NULL,
    text        STRING NOT NULL,
    embedding   VECTOR(1024),
    token_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

-- -------------------------------------------------------------------- claims

CREATE TABLE IF NOT EXISTS artist_fact (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id     UUID REFERENCES artist(id) ON DELETE CASCADE,
    track_id      UUID REFERENCES track(id) ON DELETE CASCADE,
    dimension     STRING NOT NULL,
    value_text    STRING NOT NULL DEFAULT '',
    value_json    JSONB,
    provenance    STRING NOT NULL,    -- measured | inferred | asserted
    status        STRING NOT NULL DEFAULT 'live',  -- live|superseded|stale|retracted
    confidence    FLOAT,
    embedding     VECTOR(1024),
    model         STRING NOT NULL DEFAULT '',
    model_version STRING NOT NULL DEFAULT '',
    source        STRING NOT NULL DEFAULT '',
    supersedes_id UUID REFERENCES artist_fact(id) ON DELETE SET NULL,
    written_by    STRING NOT NULL DEFAULT '',
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- An estimate may never overwrite a measurement, and neither may be smuggled
    -- in unlabelled. SCOPE-RESET §2a rule 1, enforced by the column rather than
    -- by everyone remembering.
    CONSTRAINT fact_provenance CHECK (provenance IN ('measured','inferred','asserted')),
    CONSTRAINT fact_status CHECK (status IN ('live','superseded','stale','retracted'))
);

CREATE INDEX IF NOT EXISTS fact_by_dimension
    ON artist_fact (tenant_id, artist_id, dimension, status);

-- One live fact per dimension per artist. A second live one is a contradiction,
-- and a contradiction should be a row somebody resolves rather than two answers
-- the reader picks between silently.
CREATE UNIQUE INDEX IF NOT EXISTS fact_one_live_per_dimension
    ON artist_fact (tenant_id, artist_id, dimension, provenance)
    WHERE status = 'live';

-- ------------------------------------------------------- what claims stand on

CREATE TABLE IF NOT EXISTS fact_basis (
    subject_kind STRING NOT NULL,   -- artist_fact | lesson | artist_audience | …
    subject_id   UUID NOT NULL,
    basis_kind   STRING NOT NULL,   -- document | chunk | metric | profile | fact | human
    basis_id     UUID NOT NULL,
    weight       FLOAT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subject_kind, subject_id, basis_kind, basis_id)
);

-- The reverse walk: what rests on this. Invalidation and credit assignment both
-- read this index; provenance reads the primary key.
CREATE INDEX IF NOT EXISTS fact_basis_down ON fact_basis (basis_kind, basis_id);

-- --------------------------------------------------------------- measurements

CREATE TABLE IF NOT EXISTS artist_metric (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID REFERENCES artist(id) ON DELETE CASCADE,
    track_id    UUID REFERENCES track(id) ON DELETE CASCADE,
    platform    STRING NOT NULL DEFAULT '',
    entity_kind STRING NOT NULL DEFAULT 'artist',
    entity_id   STRING NOT NULL DEFAULT '',
    metric      STRING NOT NULL,
    value       DECIMAL NOT NULL,
    provenance  STRING NOT NULL DEFAULT 'measured',
    source      STRING NOT NULL DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS metric_series
    ON artist_metric (tenant_id, artist_id, metric, observed_at DESC);

-- ---------------------------------------------------------------- the fleet

-- Not telemetry. This is what makes the fleet restartable, a decision
-- explainable, and — per the contract design §3c — it is where spend is summed
-- from, so no per-artist counter row goes hot under concurrent workers.
CREATE TABLE IF NOT EXISTS agent_run (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID REFERENCES artist(id) ON DELETE CASCADE,
    agent_kind  STRING NOT NULL,
    lead_id     UUID REFERENCES lead(id) ON DELETE SET NULL,
    state       STRING NOT NULL DEFAULT 'ok',       -- ok | error | refused
    summary     STRING NOT NULL DEFAULT '',
    error       STRING NOT NULL DEFAULT '',
    documents   INT NOT NULL DEFAULT 0,
    facts       INT NOT NULL DEFAULT 0,
    metrics     INT NOT NULL DEFAULT 0,
    leads       INT NOT NULL DEFAULT 0,
    dropped     INT NOT NULL DEFAULT 0,   -- what the budget refused, never silent
    tokens_in   INT NOT NULL DEFAULT 0,
    tokens_out  INT NOT NULL DEFAULT 0,
    -- Micro-dollars as an integer, not a float. Money summed as FLOAT drifts, and the
    -- one number nobody should have to argue about is the bill. `spend.spent_today`
    -- divides by 1e6 once, at the edge.
    cost_micro_usd BIGINT NOT NULL DEFAULT 0,
    -- What the gate refused, and what it would have cost. A refusal that is not written
    -- down reads as "there was nothing to do".
    refused_json JSONB,
    duration_ms INT NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS run_recent ON agent_run (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS run_spend  ON agent_run (tenant_id, artist_id, started_at DESC)
    STORING (tokens_in, tokens_out);

-- Limits only. Read on the hot path, never written to it.
CREATE TABLE IF NOT EXISTS artist_budget (
    tenant_id       UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id       UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    max_leads_per_run     INT NOT NULL DEFAULT 25,
    max_documents_per_run INT NOT NULL DEFAULT 20,
    max_tokens_per_hour   INT NOT NULL DEFAULT 20000,
    max_depth       INT NOT NULL DEFAULT 3,
    paused          BOOL NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, artist_id)
);

-- An agent is a row and a function. This is the row; the function is a handler in
-- `spindle/handlers/`. Adding one is an INSERT, and disabling one is an
-- UPDATE rather than a deploy.
CREATE TABLE IF NOT EXISTS agent_manifest (
    kind            STRING PRIMARY KEY,
    work_table      STRING NOT NULL DEFAULT 'lead',
    claim_state     STRING NOT NULL DEFAULT 'pending',
    scope_kinds     STRING[] NOT NULL DEFAULT ARRAY['artist'],
    adapters        STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    writes          STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    batch_size      INT NOT NULL DEFAULT 5,
    per_artist_cap  INT NOT NULL DEFAULT 3,
    lease_seconds   INT NOT NULL DEFAULT 300,
    max_attempts    INT NOT NULL DEFAULT 5,
    backoff_seconds INT[] NOT NULL DEFAULT ARRAY[60, 300, 1800, 7200],
    requires_human  BOOL NOT NULL DEFAULT false,
    enabled         BOOL NOT NULL DEFAULT true,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO agent_manifest (kind, adapters, writes, scope_kinds)
VALUES ('forager',
        ARRAY['musicbrainz','web_page'],
        ARRAY['artist_document','artist_fact','artist_metric'],
        ARRAY['artist','track'])
ON CONFLICT (kind) DO NOTHING;
