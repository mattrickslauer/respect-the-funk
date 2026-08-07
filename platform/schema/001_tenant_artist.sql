-- =============================================================================
-- 001_tenant_artist.sql — the roots
-- =============================================================================
--
-- PLATFORM-SPEC §2a, and nothing else. Two tables.
--
-- SCOPE-RESET §1 fixes the artist as the spine of the system. tenant_id is
-- carried here and will be carried on every table added later, because it is
-- partition-key-class: BUILD-SPEC rule 6 called that category
-- "near-impossible to retrofit" and was right. Everything else waits until
-- there is something for it to hang off.
--
-- Verified against CockroachDB CCL v26.2.5 on respect-the-funk-31317.
-- =============================================================================

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
