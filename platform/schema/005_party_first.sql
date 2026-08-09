-- =============================================================================
-- 005_party_first.sql — a Party is the root entity
-- =============================================================================
--
-- From `docs/superpowers/specs/2026-08-08-party-first-identity-design.md`.
--
-- An artist, a songwriter, a publisher, a label, a UGC creator, a radio
-- programmer and a journalist are all Parties. They differ by the role they play
-- relative to us, not by their type in the schema. That collapse is why
-- `artist_profile` and the counterparty index become one table, and why one
-- identifier system serves ISNI, IPI, MBID and a TikTok handle alike.
--
-- The catalogue is the industry's four layers, with the industry's identifiers:
--
--   party      who            ISNI, IPI/CAE, DPID
--   work       the song       ISWC        one work has many recordings
--   recording  the master     ISRC        a cover shares the ISWC, not the ISRC
--   release    the product    GTIN, GRid
--
-- 004 called a track "the work". That was wrong, and it is corrected here rather
-- than carried: a Work is the composition and a Recording is the master.
--
-- Every identifier is stored twice — `value` canonical and `value_raw` as
-- received. `QZ-ABC-25-00001` and `QZABC2500001` are one recording, and a naive
-- unique index files them as two. The raw form stays because the provenance model
-- has to be able to show what a source actually said.
--
-- ## What this migration destroys
--
-- `artist`, `artist_profile`, `artist_identifier` and `track` are replaced.
-- `artist_document`, `artist_chunk`, `artist_fact`, `artist_metric`,
-- `artist_budget`, `agent_run`, `fact_basis` and `lead` are dropped and recreated
-- with `party_id` and `recording_id` in place of `artist_id` and `track_id`.
--
-- That is safe at exactly one moment, and this is it: verified 2026-08-08, those
-- tables hold zero rows. `artist` holds 3, `artist_profile` holds 1, and both are
-- carried over below with their primary keys preserved. `apply_005.py` re-checks
-- the counts and refuses if anything has landed since.
--
-- Verified against CockroachDB CCL v26.2.5 on respect-the-funk-31317. The names
-- `party`, `work`, `recording`, `release`, `presence` and `suggestion` were each
-- tested as bare identifiers on that cluster before being used here.
-- =============================================================================


-- ----------------------------------------------------------------- the parties

CREATE TABLE IF NOT EXISTS party (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    slug        STRING NOT NULL,
    name        STRING NOT NULL,
    -- person | group | organisation. DDEX's party kinds. Distinct from the
    -- presentation type below: a duo is a `group` whose artist_type is `duo`.
    kind        STRING NOT NULL DEFAULT 'group',
    -- band|solo|dj|producer|… — meaningful only when the party performs, empty
    -- for a publisher. This is 004's `artist.type`, carried over unchanged.
    artist_type STRING NOT NULL DEFAULT '',
    status      STRING NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

-- One identifier identifies one party. The unique key IS the dedup rule: two
-- sources arriving with the same ISNI resolve to the same row, or the write fails
-- loudly, which is the correct direction for an identity system.
CREATE TABLE IF NOT EXISTS party_identifier (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    kind       STRING NOT NULL,
    value      STRING NOT NULL,
    value_raw  STRING NOT NULL DEFAULT '',
    provenance STRING NOT NULL DEFAULT 'measured',
    source_lead_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT identifier_provenance
        CHECK (provenance IN ('measured', 'inferred', 'asserted')),
    UNIQUE (tenant_id, kind, value)
);

-- Roles are many per party, and that is the point rather than an edge case: at an
-- independent label the artist is usually also the writer and often the label.
CREATE TABLE IF NOT EXISTS party_role (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    role       STRING NOT NULL,
    started_on DATE,
    ended_on   DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, party_id, role)
);

CREATE TABLE IF NOT EXISTS party_relation (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    from_party_id UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    to_party_id   UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    relation      STRING NOT NULL,
    started_on    DATE,
    ended_on      DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, from_party_id, to_party_id, relation)
);

-- What makes "the roster" a query rather than a table.
CREATE INDEX IF NOT EXISTS party_by_role ON party_role (tenant_id, role)
    STORING (party_id);


-- --------------------------------------------------------------- the catalogue

CREATE TABLE IF NOT EXISTS work (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    title      STRING NOT NULL,
    iswc       STRING NOT NULL DEFAULT '',
    iswc_raw   STRING NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS work_by_iswc ON work (tenant_id, iswc)
    WHERE iswc != '';

-- Not owned by an artist. That is the whole fix: a two-artist recording used to
-- be two rows because `track.artist_id` was NOT NULL, which forced presence to
-- key on ISRC instead of on a real foreign key. Credits live in `party_credit`.
CREATE TABLE IF NOT EXISTS recording (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    slug        STRING NOT NULL,
    title       STRING NOT NULL,
    isrc        STRING NOT NULL DEFAULT '',
    isrc_raw    STRING NOT NULL DEFAULT '',
    duration_ms INT,
    released_on DATE,
    status      STRING NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

CREATE UNIQUE INDEX IF NOT EXISTS recording_by_isrc ON recording (tenant_id, isrc)
    WHERE isrc != '';

CREATE TABLE IF NOT EXISTS release (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    title          STRING NOT NULL,
    gtin           STRING NOT NULL DEFAULT '',
    gtin_raw       STRING NOT NULL DEFAULT '',
    grid           STRING NOT NULL DEFAULT '',
    release_type   STRING NOT NULL DEFAULT 'single',
    released_on    DATE,
    label_party_id UUID REFERENCES party(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS release_by_gtin ON release (tenant_id, gtin)
    WHERE gtin != '';

CREATE TABLE IF NOT EXISTS recording_work (
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    work_id      UUID NOT NULL REFERENCES work(id) ON DELETE CASCADE,
    provenance   STRING NOT NULL DEFAULT 'inferred',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, recording_id, work_id)
);

CREATE TABLE IF NOT EXISTS release_recording (
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    release_id   UUID NOT NULL REFERENCES release(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    disc_no      INT NOT NULL DEFAULT 1,
    sequence     INT NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, release_id, recording_id)
);

-- Authoritative for "whose recording is this". `split_percent` stays NULL until
-- somebody actually knows: a default of 100, or an even split, would be a
-- fabricated number in a column people will eventually trust with money.
CREATE TABLE IF NOT EXISTS party_credit (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id      UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    subject_kind  STRING NOT NULL,
    subject_id    UUID NOT NULL,
    role          STRING NOT NULL,
    split_percent DECIMAL,
    provenance    STRING NOT NULL DEFAULT 'asserted',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT credit_subject
        CHECK (subject_kind IN ('work', 'recording', 'release')),
    UNIQUE (tenant_id, party_id, subject_kind, subject_id, role)
);

CREATE INDEX IF NOT EXISTS credit_by_subject
    ON party_credit (tenant_id, subject_kind, subject_id);


-- ------------------------------------------------------- carry the roster over

-- Primary keys are preserved, so nothing that already holds an artist id has to
-- be rewritten and the console's bookmarked `?sel=` links keep working.
INSERT INTO party (id, tenant_id, slug, name, kind, artist_type, status, created_at)
SELECT id, tenant_id, slug, name,
       CASE WHEN type IN ('solo','singer','songwriter','rapper','dj','producer','composer')
            THEN 'person' ELSE 'group' END,
       type, status, created_at
  FROM artist
    ON CONFLICT (id) DO NOTHING;

INSERT INTO party_role (tenant_id, party_id, role, created_at)
SELECT tenant_id, id, 'roster_artist', created_at FROM artist
    ON CONFLICT (tenant_id, party_id, role) DO NOTHING;


-- ------------------------------- drop the empty artist-shaped dependent tables

-- Order is FK-safe and deliberate. No CASCADE: a cascade here would hide the one
-- thing worth knowing, which is whether something unexpected still points at
-- these tables.
DROP TABLE IF EXISTS artist_chunk;
DROP TABLE IF EXISTS fact_basis;
DROP TABLE IF EXISTS artist_fact;
DROP TABLE IF EXISTS artist_metric;
DROP TABLE IF EXISTS artist_budget;
DROP TABLE IF EXISTS agent_run;
DROP TABLE IF EXISTS artist_document;
DROP TABLE IF EXISTS lead;
DROP TABLE IF EXISTS artist_identifier;


-- ------------------------------------------------------ the frontier, repointed

CREATE TABLE IF NOT EXISTS lead (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    scope_kind   STRING NOT NULL DEFAULT 'party',   -- tenant | party | recording
    party_id     UUID REFERENCES party(id) ON DELETE CASCADE,
    recording_id UUID REFERENCES recording(id) ON DELETE CASCADE,

    kind         STRING NOT NULL,
    mode         STRING NOT NULL DEFAULT 'auto',
    adapter      STRING NOT NULL,
    target       STRING NOT NULL,
    target_hash  STRING NOT NULL,
    platform     STRING NOT NULL DEFAULT '',

    parent_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    depth        INT NOT NULL DEFAULT 0,
    reason       STRING NOT NULL DEFAULT '',
    score        FLOAT NOT NULL DEFAULT 0.5,

    state        STRING NOT NULL DEFAULT 'pending',
    -- NULL = one-shot discovery. Set = recurring poll. One nullable column is the
    -- whole difference between a crawler and a frontier.
    cadence_seconds INT,
    next_action_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_agent  STRING,
    lease_expires_at TIMESTAMPTZ,
    attempts     INT NOT NULL DEFAULT 0,
    last_error   STRING NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT lead_scope_shape CHECK (
        (scope_kind = 'tenant'    AND party_id IS NULL     AND recording_id IS NULL)
     OR (scope_kind = 'party'     AND party_id IS NOT NULL AND recording_id IS NULL)
     OR (scope_kind = 'recording' AND party_id IS NOT NULL AND recording_id IS NOT NULL)
    ),
    UNIQUE (tenant_id, target_hash)
);

CREATE INDEX IF NOT EXISTS lead_claimable
    ON lead (tenant_id, party_id, next_action_at)
    STORING (score) WHERE state = 'pending';


-- ---------------------------------------------------------- evidence, repointed

CREATE TABLE IF NOT EXISTS party_document (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id     UUID REFERENCES party(id) ON DELETE CASCADE,
    lead_id      UUID REFERENCES lead(id) ON DELETE SET NULL,
    platform     STRING NOT NULL DEFAULT '',
    url          STRING NOT NULL DEFAULT '',
    title        STRING NOT NULL DEFAULT '',
    body         STRING NOT NULL DEFAULT '',
    content_hash STRING NOT NULL,
    mime         STRING NOT NULL DEFAULT '',
    lang         STRING NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    http_status  INT,
    gone_at      TIMESTAMPTZ,
    UNIQUE (tenant_id, content_hash)
);

CREATE TABLE IF NOT EXISTS party_chunk (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id    UUID REFERENCES party(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES party_document(id) ON DELETE CASCADE,
    ordinal     INT NOT NULL,
    text        STRING NOT NULL,
    embedding   VECTOR(1024),
    token_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);


-- ------------------------------------------------------------ claims, repointed

CREATE TABLE IF NOT EXISTS party_fact (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id      UUID REFERENCES party(id) ON DELETE CASCADE,
    recording_id  UUID REFERENCES recording(id) ON DELETE CASCADE,
    dimension     STRING NOT NULL,
    value_text    STRING NOT NULL DEFAULT '',
    value_json    JSONB,
    provenance    STRING NOT NULL,
    status        STRING NOT NULL DEFAULT 'live',
    confidence    FLOAT,
    embedding     VECTOR(1024),
    model         STRING NOT NULL DEFAULT '',
    model_version STRING NOT NULL DEFAULT '',
    source        STRING NOT NULL DEFAULT '',
    supersedes_id UUID REFERENCES party_fact(id) ON DELETE SET NULL,
    written_by    STRING NOT NULL DEFAULT '',
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fact_provenance CHECK (provenance IN ('measured','inferred','asserted')),
    CONSTRAINT fact_status CHECK (status IN ('live','superseded','stale','retracted'))
);

CREATE INDEX IF NOT EXISTS fact_by_dimension
    ON party_fact (tenant_id, party_id, dimension, status);

CREATE UNIQUE INDEX IF NOT EXISTS fact_one_live_per_dimension
    ON party_fact (tenant_id, party_id, dimension, provenance)
    WHERE status = 'live';

CREATE TABLE IF NOT EXISTS fact_basis (
    subject_kind STRING NOT NULL,
    subject_id   UUID NOT NULL,
    basis_kind   STRING NOT NULL,
    basis_id     UUID NOT NULL,
    weight       FLOAT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subject_kind, subject_id, basis_kind, basis_id)
);

CREATE INDEX IF NOT EXISTS fact_basis_down ON fact_basis (basis_kind, basis_id);


-- ------------------------------------------------------ measurements, with unit

-- `unit` is not cosmetic. Spotify popularity is a 0-100 index, Deezer fans are a
-- count and DSR streams are a count of something else again. The column exists so
-- the read API can refuse a cross-unit aggregate outright rather than trusting
-- every caller to remember that a total here is meaningless.
CREATE TABLE IF NOT EXISTS party_metric (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id     UUID REFERENCES party(id) ON DELETE CASCADE,
    recording_id UUID REFERENCES recording(id) ON DELETE CASCADE,
    platform     STRING NOT NULL DEFAULT '',
    entity_kind  STRING NOT NULL DEFAULT 'party',
    entity_id    STRING NOT NULL DEFAULT '',
    metric       STRING NOT NULL,
    value        DECIMAL NOT NULL,
    unit         STRING NOT NULL DEFAULT 'count',
    territory    STRING NOT NULL DEFAULT '',
    period_start DATE,
    period_end   DATE,
    provenance   STRING NOT NULL DEFAULT 'measured',
    source       STRING NOT NULL DEFAULT '',
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS metric_series
    ON party_metric (tenant_id, party_id, metric, observed_at DESC);


-- ------------------------------------------------------------ fleet, repointed

CREATE TABLE IF NOT EXISTS agent_run (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id    UUID REFERENCES party(id) ON DELETE CASCADE,
    agent_kind  STRING NOT NULL,
    lead_id     UUID REFERENCES lead(id) ON DELETE SET NULL,
    state       STRING NOT NULL DEFAULT 'ok',
    summary     STRING NOT NULL DEFAULT '',
    error       STRING NOT NULL DEFAULT '',
    -- Which source was called, so quota can be summed per source the same way
    -- money is summed per artist. See the spend gate's second currency.
    source      STRING NOT NULL DEFAULT '',
    calls       INT NOT NULL DEFAULT 0,
    documents   INT NOT NULL DEFAULT 0,
    facts       INT NOT NULL DEFAULT 0,
    metrics     INT NOT NULL DEFAULT 0,
    leads       INT NOT NULL DEFAULT 0,
    dropped     INT NOT NULL DEFAULT 0,
    tokens_in   INT NOT NULL DEFAULT 0,
    tokens_out  INT NOT NULL DEFAULT 0,
    cost_micro_usd BIGINT NOT NULL DEFAULT 0,
    refused_json JSONB,
    duration_ms INT NOT NULL DEFAULT 0,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS run_recent ON agent_run (tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS run_spend  ON agent_run (tenant_id, party_id, started_at DESC)
    STORING (tokens_in, tokens_out);
CREATE INDEX IF NOT EXISTS run_quota  ON agent_run (tenant_id, source, started_at DESC)
    STORING (calls);

CREATE TABLE IF NOT EXISTS party_budget (
    tenant_id       UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id        UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    max_leads_per_run     INT NOT NULL DEFAULT 25,
    max_documents_per_run INT NOT NULL DEFAULT 20,
    max_tokens_per_hour   INT NOT NULL DEFAULT 20000,
    max_depth       INT NOT NULL DEFAULT 3,
    paused          BOOL NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, party_id)
);


-- ------------------------------------------------- presence, for all three kinds

-- One table for parties, recordings and releases. Polymorphic like `fact_basis`:
-- the cost is no foreign key on `subject_id`, the benefit is that one probe, one
-- reconciler and one coverage grid serve all three. This replaces
-- `artist_profile`, and it is where the counterparty index will live.
--
-- `absent` is a stored answer, not a missing row. "We asked Deezer and it is not
-- there" is a distribution defect worth money. "We never asked" is nothing. A
-- schema that cannot tell them apart turns the coverage grid into a guess.
CREATE TABLE IF NOT EXISTS presence (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    subject_kind STRING NOT NULL,
    subject_id   UUID NOT NULL,
    platform     STRING NOT NULL,

    external_id  STRING NOT NULL DEFAULT '',
    url          STRING NOT NULL DEFAULT '',
    handle       STRING NOT NULL DEFAULT '',
    title_seen   STRING NOT NULL DEFAULT '',
    context_id   STRING NOT NULL DEFAULT '',
    markets      STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],

    -- owned | unowned | absent, and meaningful only for a party: whether they
    -- hold the account. An artist with no TikTok is still worth searching TikTok.
    mode         STRING NOT NULL DEFAULT '',
    state        STRING NOT NULL DEFAULT 'present',
    match_basis  STRING NOT NULL DEFAULT 'measured',
    confidence   FLOAT NOT NULL DEFAULT 1.0,
    enabled      BOOL NOT NULL DEFAULT true,

    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    document_id    UUID REFERENCES party_document(id) ON DELETE SET NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT presence_state CHECK (state IN ('present', 'absent', 'unknown')),
    CONSTRAINT presence_match CHECK (match_basis IN ('measured','inferred','asserted')),
    CONSTRAINT presence_subject
        CHECK (subject_kind IN ('party', 'recording', 'release')),
    UNIQUE (tenant_id, subject_kind, subject_id, platform)
);

CREATE INDEX IF NOT EXISTS presence_by_platform
    ON presence (tenant_id, platform, state);


-- --------------------------------------------- carry the configured surfaces over

-- `asserted`, because a person typed these in. The provenance class is about how
-- we came to believe it, and "an operator entered it" is exactly `asserted`.
INSERT INTO presence (tenant_id, subject_kind, subject_id, platform, external_id,
                      url, handle, mode, state, match_basis, enabled,
                      first_seen_at, checked_at)
SELECT tenant_id, 'party', artist_id, platform, external_id,
       profile_url, handle, mode, 'present', 'asserted', enabled,
       created_at, created_at
  FROM artist_profile
    ON CONFLICT (tenant_id, subject_kind, subject_id, platform) DO NOTHING;


-- --------------------------------------------------- drop the replaced originals

DROP TABLE IF EXISTS artist_profile;
DROP TABLE IF EXISTS track;
DROP TABLE IF EXISTS artist;


-- ---------------------------------------------------------- the source registry

-- A source is a row plus a function, exactly as an agent is. `accepts`/`emits`
-- make the sources a graph, so resolution is a traversal rather than a hardcoded
-- sequence, and nothing in the reconciler ever names a platform.
CREATE TABLE IF NOT EXISTS source_manifest (
    platform        STRING PRIMARY KEY,
    adapter         STRING NOT NULL,
    accepts         STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    emits           STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    yields          STRING[] NOT NULL DEFAULT ARRAY[]::STRING[],
    subject_kinds   STRING[] NOT NULL DEFAULT ARRAY['party','recording'],
    auth            STRING NOT NULL DEFAULT 'none',
    cost_class      STRING NOT NULL DEFAULT 'free',
    rate_per_sec    DECIMAL NOT NULL DEFAULT 1,
    quota_per_day   BIGINT,
    cadence_seconds INT,
    -- Fail closed, like the spend gate. A source nobody deliberately turned on
    -- does not get called.
    enabled         BOOL NOT NULL DEFAULT false,
    disabled_reason STRING NOT NULL DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT source_cost_class CHECK (cost_class IN ('free','quota','dollar'))
);

CREATE TABLE IF NOT EXISTS suggestion (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id     UUID REFERENCES party(id) ON DELETE CASCADE,
    kind         STRING NOT NULL,
    payload      JSONB NOT NULL,
    confidence   FLOAT NOT NULL DEFAULT 0.5,
    rationale    STRING NOT NULL DEFAULT '',
    source_lead_id UUID REFERENCES lead(id) ON DELETE SET NULL,
    -- Rejections are kept. A suggestion re-proposed every cycle after being
    -- refused is the fastest way to make an operator stop reading the queue.
    state        STRING NOT NULL DEFAULT 'pending',
    decided_by   STRING NOT NULL DEFAULT '',
    decided_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT suggestion_state
        CHECK (state IN ('pending', 'accepted', 'rejected'))
);

CREATE INDEX IF NOT EXISTS suggestion_pending
    ON suggestion (tenant_id, created_at DESC) WHERE state = 'pending';


-- Every row lands disabled. Turning one on is an UPDATE, which is the whole
-- point of the registry — no deploy, no code change, no new column.
INSERT INTO source_manifest
    (platform, adapter, accepts, emits, yields, subject_kinds,
     auth, cost_class, rate_per_sec, quota_per_day, cadence_seconds, disabled_reason)
VALUES
    ('musicbrainz', 'musicbrainz',
     ARRAY['mbid','isrc','iswc'], ARRAY['mbid','isni','isrc','iswc','url'],
     ARRAY['identity','catalogue'], ARRAY['party','recording'],
     'none', 'free', 1, NULL, 604800,
     'free and ready — enable when the adapter lands'),

    ('deezer', 'deezer',
     ARRAY['isrc','deezer_artist'], ARRAY['deezer_artist','deezer_track','isrc'],
     ARRAY['identity','catalogue','metrics'], ARRAY['party','recording'],
     'none', 'free', 5, NULL, 86400,
     'free and ready, no key required — enable when the adapter lands'),

    ('spotify', 'spotify',
     ARRAY['spotify_artist','isrc'], ARRAY['spotify_artist','spotify_track','isrc'],
     ARRAY['identity','catalogue','metrics'], ARRAY['party','recording'],
     'oauth', 'free', 5, NULL, 86400,
     'needs a client id and secret'),

    ('acoustid', 'acoustid',
     ARRAY['fingerprint'], ARRAY['mbid','isrc'],
     ARRAY['identity'], ARRAY['recording'],
     'key', 'free', 3, NULL, NULL,
     'needs a free API key'),

    ('youtube', 'youtube',
     ARRAY['youtube_channel','isrc'], ARRAY['youtube_channel','youtube_video'],
     ARRAY['identity','metrics'], ARRAY['party','recording'],
     'key', 'quota', 5, 10000, 86400,
     'needs a Google API key, and the daily unit cost is unmeasured'),

    ('apple_music', 'apple_music',
     ARRAY['isrc','gtin'], ARRAY['apple_artist','apple_album'],
     ARRAY['identity','catalogue'], ARRAY['party','recording','release'],
     'oauth', 'dollar', 5, NULL, 86400,
     'costs 99 USD a year — suggestable via MusicBrainz url-rels, not fetchable')
    ON CONFLICT (platform) DO NOTHING;


-- The forager now works on parties and recordings, and writes the repointed
-- tables. Adding an agent stays an INSERT and disabling one stays an UPDATE.
UPDATE agent_manifest
   SET scope_kinds = ARRAY['party','recording'],
       writes = ARRAY['party_document','party_fact','party_metric','presence'],
       updated_at = now()
 WHERE kind = 'forager';
