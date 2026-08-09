-- 010 — the lesson: the only table in this schema whose job is to make the next run
-- better than this one.
--
-- `SCOPE-RESET §1` justifies the party being the root of the spine on one claim: that
-- release n+1 is cheaper and lands harder than release n, because something accumulates
-- between them. Up to this migration, nothing did. Facts accumulate about a party, and
-- documents accumulate about a party, but nothing recorded what *worked* — so every
-- shortlist was computed as though the label had never pitched anybody.
--
-- This is that table, and `PLATFORM-SPEC §6` calls the query over it R2.
--
--
-- ## Scope, and why `scope_id` is a STRING with no foreign key
--
-- A lesson is about one curator ("replies within a day, never to a template"), or about
-- a kind of counterparty ("mood editors want the playlist named"), or about a channel,
-- or about everything. Those are four different things for `scope_id` to hold, and only
-- the first is a UUID.
--
-- This is the same polymorphism `presence` and `party_credit` already carry, and it has
-- the same price: `ON DELETE CASCADE` cannot fire, so `repo.delete_party` deletes
-- lessons itself. That is now three tables a party deleter must clear by hand, and the
-- periodic orphan sweep `platform/README.md` says is worth writing covers all three.
--
--
-- ## `supersedes_id` rather than UPDATE
--
-- `SCOPE-RESET §2a` rule 1. A lesson that turns out to be wrong is superseded and stays
-- readable, because why we used to believe something is itself evidence — and because an
-- inferred value may never overwrite anything in place.
--
--
-- ## `hit_count` is not telemetry
--
-- It is how an operator sees which lessons earn their place. A lesson retrieved in fifty
-- drafts and a lesson retrieved never should not look the same in the console, and the
-- second is a candidate for supersession rather than a permanent thumb on the scale.


CREATE TABLE IF NOT EXISTS lesson (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    scope_kind    STRING NOT NULL,
    scope_id      STRING NOT NULL DEFAULT '',

    text          STRING NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}',
    confidence    FLOAT NOT NULL DEFAULT 0.5,
    valence       FLOAT NOT NULL DEFAULT 0,

    embedding     VECTOR(1024),
    model         STRING NOT NULL DEFAULT '',
    model_version STRING NOT NULL DEFAULT '',

    supersedes_id UUID REFERENCES lesson(id) ON DELETE SET NULL,
    hit_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT lesson_scope_known
        CHECK (scope_kind IN ('party', 'party_kind', 'channel', 'global')),

    -- The rule migration 007 set for chunks and 009 set for parties: an embedding that
    -- cannot name its model can never satisfy the equality predicate a retrieval
    -- carries, so it is unsearchable and must not be written.
    CONSTRAINT lesson_embedding_has_a_model
        CHECK (embedding IS NULL OR model != ''),

    CONSTRAINT lesson_confidence_is_a_probability
        CHECK (confidence >= 0 AND confidence <= 1),

    -- Signed, and bounded, because the rerank multiplies by it. An unbounded valence is
    -- a single lesson that can dominate every shortlist forever.
    CONSTRAINT lesson_valence_is_bounded
        CHECK (valence >= -1 AND valence <= 1)
);


-- R2 — what have we learned that applies here? Every prefix column is equality or IN:
--
--     WHERE tenant_id = $1 AND model = $2 AND scope_kind IN ('channel','party_kind','global')
--     ORDER BY embedding <=> $3 LIMIT 10
--
CREATE VECTOR INDEX IF NOT EXISTS lesson_semantic
    ON lesson (tenant_id, model, scope_kind, embedding vector_cosine_ops);


-- Lessons about one named party are looked up by id, not by similarity — we already
-- know who we mean. The vector index cannot serve that and should not be asked to.
CREATE INDEX IF NOT EXISTS lesson_by_scope
    ON lesson (tenant_id, scope_kind, scope_id, created_at DESC)
    STORING (text, confidence, valence, supersedes_id);
