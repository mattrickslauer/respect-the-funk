-- =============================================================================
-- 001_substrate.sql — the spine, the fleet's coordination, and its memory
-- =============================================================================
--
-- Implements docs/PLATFORM-SPEC.md §2, §3, §5, §6 and §7 against CockroachDB.
-- SCOPE-RESET.md is the binding scope; PLATFORM-SPEC.md is the binding
-- architecture. Where this file departs from either, the departure is marked
-- DEVIATION and argued in platform/README.md rather than made silently.
--
-- Applies as one transaction. Idempotent on re-run (IF NOT EXISTS throughout).
--
-- Verified against CockroachDB CCL v26.2.5 on
-- respect-the-funk-31317.j77.aws-us-east-1.cockroachlabs.cloud.
-- feature.vector_index.enabled is already `t` on this cluster; no
-- SET CLUSTER SETTING is required, closing PLATFORM-SPEC §10 risk 3.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Closed sets
-- -----------------------------------------------------------------------------
-- Enums are used for sets the *system* owns and whose membership changing is a
-- code change. They are deliberately NOT used for `channel` or counterparty
-- `kind` — PLATFORM-SPEC §7's claim is that "a channel is data, not code", and
-- an enum would make adding a channel an ALTER TYPE, i.e. code. Those two stay
-- STRING, keyed to channel_playbook. See README §"Channel is data".

CREATE TYPE IF NOT EXISTS provenance AS ENUM (
    'measured',   -- computed from the artefact itself
    'inferred',   -- a model's estimate
    'asserted'    -- a human stated it, and is accountable for it
);

CREATE TYPE IF NOT EXISTS contact_state AS ENUM (
    'contactable',
    'in_thread',
    'stale',
    'declined',
    'unusable'
);

CREATE TYPE IF NOT EXISTS thread_state AS ENUM (
    'discovered', 'shortlisted', 'approved', 'drafted', 'awaiting_human',
    'queued', 'sent', 'awaiting_reply', 'replied', 'negotiating', 'agreed',
    'delivered', 'verified',
    'closed_won', 'closed_lost', 'closed_no_reply'
);

CREATE TYPE IF NOT EXISTS campaign_state AS ENUM (
    'draft', 'active', 'paused', 'completed', 'abandoned'
);

CREATE TYPE IF NOT EXISTS msg_direction AS ENUM ('inbound', 'outbound');

CREATE TYPE IF NOT EXISTS outbox_state AS ENUM (
    'pending', 'claimed', 'sent', 'failed', 'dead'
);

CREATE TYPE IF NOT EXISTS agent_kind AS ENUM (
    'scout', 'researcher', 'drafter', 'sender',
    'inbox', 'negotiator', 'analyst', 'remixkit'
);

CREATE TYPE IF NOT EXISTS agent_run_state AS ENUM (
    'running', 'succeeded', 'failed'
);

CREATE TYPE IF NOT EXISTS lesson_scope AS ENUM (
    'artist', 'counterparty_kind', 'channel', 'global'
);


-- -----------------------------------------------------------------------------
-- 2. Roots — PLATFORM-SPEC §2a
-- -----------------------------------------------------------------------------
-- SCOPE-RESET §1 fixes the artist as the spine. tenant_id is carried on every
-- table regardless of the tenancy *policy* decision (SCOPE-RESET open decision
-- 6), because it is partition-key-class and near-impossible to retrofit.

CREATE TABLE IF NOT EXISTS tenant (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        STRING NOT NULL UNIQUE,
    name        STRING NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artist (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    slug        STRING NOT NULL,
    name        STRING NOT NULL,
    status      STRING NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS track (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    title       STRING NOT NULL,
    isrc        STRING,
    master_key  STRING,              -- B2 object key. Bytes never live in this DB.
    status      STRING NOT NULL DEFAULT 'draft',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX (tenant_id, artist_id),
    UNIQUE (tenant_id, isrc)         -- NULL isrc repeats freely; a real one cannot
);


-- -----------------------------------------------------------------------------
-- 3. Derived facts — analysed once. PLATFORM-SPEC §2b
-- -----------------------------------------------------------------------------
-- SCOPE-RESET §2a rule 1: every fact carries how it was obtained, and the three
-- provenance classes get three storage *shapes* so mixing them is a schema
-- error rather than a discipline failure.
--
-- DEVIATION (is_current): PLATFORM-SPEC §2b specifies supersedes_id chains with
-- "the current value is the head of the chain". Finding that head by
-- NOT EXISTS (SELECT 1 ... WHERE supersedes_id = t.id) is an anti-join on every
-- read of a fact — the hottest read path in the system. is_current is a
-- denormalisation written in the same serializable transaction as the
-- supersession, which is exactly the argument §6's contact_state amendment
-- already makes and accepts. The partial unique index makes "exactly one
-- current row" structural rather than conventional.

-- MEASURED — deterministic, permanent, one row per track.
CREATE TABLE IF NOT EXISTS track_measurement (
    track_id         UUID PRIMARY KEY REFERENCES track(id) ON DELETE CASCADE,
    tenant_id        UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    bpm              DECIMAL(6,2),
    musical_key      STRING,
    duration_ms      INT,
    drop_ms          INT,
    hook_start_ms    INT,
    hook_end_ms      INT,
    energy_curve     JSONB,
    method           STRING NOT NULL,          -- how it was computed
    tool_version     STRING NOT NULL,          -- reproducibility
    measured_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (hook_end_ms IS NULL OR hook_start_ms IS NULL OR hook_end_ms > hook_start_ms)
);

-- INFERRED — a model's estimate. Versioned, never overwritten in place.
CREATE TABLE IF NOT EXISTS track_character (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id          UUID NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    tenant_id         UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    mood              STRING,
    era               STRING,
    reference_artists STRING[],
    embedding         VECTOR(1024),            -- Bedrock Titan Text Embeddings V2
    model             STRING NOT NULL,
    model_version     STRING NOT NULL,
    confidence        DECIMAL(4,3),
    inferred_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    supersedes_id     UUID REFERENCES track_character(id),
    is_current        BOOL NOT NULL DEFAULT true,
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_character_per_track
    ON track_character (track_id) WHERE is_current;

-- ASSERTED — a human stated it, and is accountable for it.
CREATE TABLE IF NOT EXISTS track_rights (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    track_id         UUID NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    tenant_id        UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    master_owner     STRING,
    publishing       JSONB,
    splits           JSONB,
    clearance_state  STRING NOT NULL DEFAULT 'unknown',
    asserted_by      STRING NOT NULL,          -- a person, never an agent
    asserted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    doc_key          STRING,                   -- B2 key of the signed document
    supersedes_id    UUID REFERENCES track_rights(id),
    is_current       BOOL NOT NULL DEFAULT true
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_rights_per_track
    ON track_rights (track_id) WHERE is_current;

-- The artist-level audience model. Inferred, versioned, inherited by tracks.
CREATE TABLE IF NOT EXISTS artist_audience (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_id      UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    tenant_id      UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    model_version  STRING NOT NULL,
    position       JSONB NOT NULL,
    confidence     DECIMAL(4,3),
    inferred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    supersedes_id  UUID REFERENCES artist_audience(id),
    is_current     BOOL NOT NULL DEFAULT true
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_audience_per_artist
    ON artist_audience (artist_id) WHERE is_current;


-- -----------------------------------------------------------------------------
-- 4. Counterparty — one shape, many kinds. PLATFORM-SPEC §2c, §6
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS counterparty (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    kind               STRING NOT NULL,        -- creator|radio|curator|press|sync
    platform           STRING,
    platform_user_id   STRING,
    handle             STRING,
    display_name       STRING,
    profile_url        STRING,
    bio                STRING,
    profile_embedding  VECTOR(1024),

    -- §6 amendment. Denormalised so every shortlist predicate is an equality
    -- and therefore an index prefix span. Maintained in the same serializable
    -- transaction that opens or closes a thread.
    contact_state      contact_state NOT NULL DEFAULT 'contactable',

    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at  TIMESTAMPTZ,
    UNIQUE (tenant_id, platform, platform_user_id)
);

-- R1 — Shortlist. Verified: EXPLAIN resolves the three leading columns to
-- `prefix spans` on a `vector search` node, so the filter is accelerated
-- rather than post-applied. This is the query the retrieval design rests on.
CREATE VECTOR INDEX IF NOT EXISTS counterparty_shortlist
    ON counterparty (tenant_id, kind, contact_state,
                     profile_embedding vector_cosine_ops);

-- APPEND ONLY. An estimate may never overwrite a measurement.
CREATE TABLE IF NOT EXISTS counterparty_observation (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    counterparty_id   UUID NOT NULL REFERENCES counterparty(id) ON DELETE CASCADE,
    tenant_id         UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    dimension         STRING NOT NULL,        -- e.g. 'age', 'geo', 'median_views'
    bucket            STRING,                 -- e.g. '18-24', 'US'
    share             DECIMAL(6,4),
    provenance        provenance NOT NULL,
    source            STRING NOT NULL,
    vendor            STRING,
    confidence        DECIMAL(4,3),
    error_bar_pp      DECIMAL(5,2),
    sample_size       INT,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_by       STRING NOT NULL,
    notes             STRING,

    -- "Source rank decides what is read" (§2c) made explicit rather than left
    -- to each caller's ORDER BY. measured > asserted > inferred is SCOPE-RESET
    -- §2a rule 1's ordering: an inferred value may never win over a measured one.
    provenance_rank   INT NOT NULL AS (
        CASE provenance
            WHEN 'measured' THEN 3
            WHEN 'asserted' THEN 2
            ELSE 1
        END
    ) STORED,

    INDEX obs_read_path (counterparty_id, dimension, provenance_rank DESC, observed_at DESC)
);


-- -----------------------------------------------------------------------------
-- 5. Channel playbooks — PLATFORM-SPEC §7. A channel is data, not code.
-- -----------------------------------------------------------------------------
-- Declared before campaign/thread because they key to it.
-- Note the division of labour: thread_state (the enum above) is channel-
-- agnostic per §2d; state_machine describes which transitions and cadence a
-- given channel permits over those same states.

CREATE TABLE IF NOT EXISTS channel_playbook (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    channel              STRING NOT NULL,     -- ugc|radio|curator|press|sync|paid
    counterparty_kind    STRING NOT NULL,     -- which kind this channel works
    state_machine        JSONB NOT NULL,
    cadence              JSONB NOT NULL,
    draft_system_prompt  STRING NOT NULL,
    success_metric       STRING NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, channel)
);


-- -----------------------------------------------------------------------------
-- 6. Outreach — state machine and lock in one row. PLATFORM-SPEC §2d, §3
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaign (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    artist_id   UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    track_id    UUID NOT NULL REFERENCES track(id) ON DELETE CASCADE,
    channel     STRING NOT NULL,
    goal        STRING,
    state       campaign_state NOT NULL DEFAULT 'draft',
    started_at  TIMESTAMPTZ,
    ended_at    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, channel) REFERENCES channel_playbook (tenant_id, channel),
    INDEX (tenant_id, state)
);

CREATE TABLE IF NOT EXISTS thread (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    campaign_id        UUID NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    counterparty_id    UUID NOT NULL REFERENCES counterparty(id) ON DELETE CASCADE,
    state              thread_state NOT NULL DEFAULT 'discovered',
    next_action_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- §3a. The lock lives on the work item, not in a separate system.
    -- A lease, not a lock: an agent that dies releases its work by expiry,
    -- with no supervisor needed. This is what makes the fleet restartable.
    owner_agent        STRING,
    lease_expires_at   TIMESTAMPTZ,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now() ON UPDATE now(),
    UNIQUE (campaign_id, counterparty_id)
);

-- §3a claim query support: the exact predicate the fleet polls on.
CREATE INDEX IF NOT EXISTS thread_claimable
    ON thread (tenant_id, state, next_action_at)
    STORING (owner_agent, lease_expires_at);

-- §3c. Cross-channel collision, settled structurally. A creator who is also a
-- curator cannot be worked by two fleets at once; the second fleet's insert
-- fails and it does not need to know the first fleet exists.
-- Contact discipline becomes a constraint rather than a convention.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_thread_per_counterparty
    ON thread (tenant_id, counterparty_id)
    WHERE state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply');

CREATE TABLE IF NOT EXISTS message (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id            UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    tenant_id            UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    direction            msg_direction NOT NULL,
    channel              STRING NOT NULL,
    subject              STRING,
    body                 STRING,
    provider_message_id  STRING,
    idempotency_key      STRING NOT NULL UNIQUE,   -- §3b: survives a crash
    sent_at              TIMESTAMPTZ,
    received_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    INDEX (thread_id, created_at)
);

-- §3b. Sending is the only irreversible act here. The agent never sends; it
-- writes the message row and the outbox row in ONE transaction. Without that,
-- a crash between "email sent" and "recorded as sent" double-contacts a
-- counterparty — a relationship you burn exactly once.
CREATE TABLE IF NOT EXISTS outbox (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id         UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    tenant_id         UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    message_id        UUID REFERENCES message(id) ON DELETE CASCADE,
    kind              STRING NOT NULL,
    payload           JSONB NOT NULL,
    state             outbox_state NOT NULL DEFAULT 'pending',
    attempts          INT NOT NULL DEFAULT 0,
    last_error        STRING,
    claimed_by        STRING,
    claimed_at        TIMESTAMPTZ,
    lease_expires_at  TIMESTAMPTZ,        -- same lease discipline as thread
    not_before        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outbox_claimable
    ON outbox (tenant_id, state, not_before)
    STORING (claimed_by, lease_expires_at, attempts);


-- -----------------------------------------------------------------------------
-- 7. Memory and fleet — PLATFORM-SPEC §2e, §6 R2
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lesson (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    scope_kind     lesson_scope NOT NULL,
    scope_id       STRING NOT NULL DEFAULT '',   -- '' for scope_kind='global'
    text           STRING NOT NULL,
    evidence       JSONB,
    confidence     DECIMAL(4,3),
    embedding      VECTOR(1024),
    supersedes_id  UUID REFERENCES lesson(id),
    is_current     BOOL NOT NULL DEFAULT true,
    hit_count      INT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- R2 — Lessons for drafting. The query that makes campaign n+1 cheaper than
-- campaign n, which is SCOPE-RESET §1's whole justification for the artist
-- being the root.
--
-- DEVIATION (see README §"R2 has R1's problem"): PLATFORM-SPEC §6 amends R1 for
-- the vector-index prefix constraint but leaves R2 unamended, and R2 has the
-- same constraint. Scope is polymorphic (scope_kind, scope_id), so "this
-- artist AND this counterparty kind AND this channel" cannot be one equality
-- prefix. R2 is therefore N scoped ANN queries — one per scope level — merged
-- by the caller. Each is individually accelerated by this index.
CREATE VECTOR INDEX IF NOT EXISTS lesson_retrieval
    ON lesson (tenant_id, scope_kind, scope_id, embedding vector_cosine_ops);

-- Not telemetry. This is the record that makes a fleet restartable and a
-- decision explainable.
CREATE TABLE IF NOT EXISTS agent_run (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    agent       agent_kind NOT NULL,
    thread_id   UUID REFERENCES thread(id) ON DELETE SET NULL,
    input       JSONB,
    output      JSONB,
    state       agent_run_state NOT NULL DEFAULT 'running',
    model       STRING,
    tokens_in   INT,
    tokens_out  INT,
    cost_cents  DECIMAL(10,4),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    error       STRING,
    INDEX (tenant_id, agent, started_at DESC),
    INDEX (thread_id, started_at DESC)
);
