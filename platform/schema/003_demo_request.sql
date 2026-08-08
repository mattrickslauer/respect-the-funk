-- =============================================================================
-- 003_demo_request.sql — the only thing an unauthenticated visitor can write
-- =============================================================================
--
-- The console is now closed. `/` serves a landing page to anyone without a
-- session, and its second call to action books a demo — which means one public
-- POST, and this is the table behind it.
--
-- No tenant_id. Every other table in this schema carries one because it is
-- partition-key-class and near-impossible to retrofit; this one genuinely
-- predates the tenant relationship, because the whole point of the row is that
-- the label filling it in is not a customer yet. It is the one table in the
-- system that is about someone who has no tenant.
--
-- Deliberately thin. Name, label, email, a note, and where they came from. A
-- lead-capture form that asks for more than it needs converts worse and gives
-- the operator nothing extra to act on.
--
-- `state` rather than a boolean: new -> contacted -> booked -> declined. A
-- STRING for the same reason `artist.type` is one — the set will change, and an
-- ENUM would make that a migration coordinated with a deploy.
-- =============================================================================

CREATE TABLE IF NOT EXISTS demo_request (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        STRING NOT NULL,
    label       STRING NOT NULL,
    email       STRING NOT NULL,
    roster_size STRING NOT NULL DEFAULT '',
    note        STRING NOT NULL DEFAULT '',
    source      STRING NOT NULL DEFAULT 'landing',
    state       STRING NOT NULL DEFAULT 'new',
    user_agent  STRING NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The operator reads this newest-first and only ever wants the unhandled ones.
CREATE INDEX IF NOT EXISTS demo_request_triage
    ON demo_request (state, created_at DESC);
