-- 007 — the vector indexes, and the column that keeps them honest.
--
-- Migrations 004 and 005 added `VECTOR(1024)` columns and never indexed them.
-- `apps/spindle/README.md` said they were "indexed and empty"; only the second half was
-- true. This migration makes the first half true too.
--
-- Nothing here is destructive. It adds one column and two indexes.
--
--
-- ## The column: `party_chunk.model`
--
-- `party_fact` already carries `model` and `model_version`. `party_chunk` carried an
-- `embedding` and no way to say what produced it, which is the setup for the worst
-- failure this system can have.
--
-- Embeddings from two different models are not comparable. Cosine distance between an
-- OpenAI vector and a Titan vector is a well-formed float and complete nonsense —
-- nothing raises, nothing logs, and retrieval quietly degrades to random. Because
-- Bedrock's Titan quota on this account is 0 RPM, the first embeddings will come from
-- OpenAI and Titan vectors will arrive later in the same column. Without `model` on the
-- row there is no way to tell them apart afterwards, and no way to filter to one.
--
-- So `model` is both the audit trail and a query predicate — and, conveniently, making
-- it a predicate is exactly what the index needs anyway (below).
--
--
-- ## The indexes: every prefix column is filtered by equality
--
-- `docs/reference/COCKROACHDB-AI.md` records the constraint that shapes these:
-- CockroachDB accelerates a vector-index filter *only* on prefix columns, and only with
-- equality or `IN`. A range comparison or a subquery degrades to a post-filter, which
-- means over-fetching `LIMIT k × n` and discarding — and it gets worse as the index
-- grows.
--
-- Both indexes below are therefore all-equality by construction:
--
--     tenant_id = $1  AND  model = $2  [AND status = 'live']  ORDER BY embedding <=> $3
--
-- Verified against this cluster (CockroachDB CCL v26.2.5) before this file was written.
-- The all-equality form plans as:
--
--     • vector search
--       table: party_fact@fact_semantic
--       prefix spans: [/'<tenant>'/'<model>'/'live' - …]
--
-- and swapping any one predicate for a range comparison collapses it to `• filter` over
-- a full scan, exactly as the documentation says. The verification is reproducible:
-- `EXPLAIN` either shows a `vector search` node or it does not.
--
-- Note the predicate must be a *constant*. An earlier probe used `gen_random_uuid()` in
-- the tenant filter and got a full scan — a volatile function cannot be folded into an
-- index span. That is a property of the query, not of the index, and it is written down
-- here because it looks identical to the index not working.
--
--
-- ## Why `party_id` is not a prefix column
--
-- Scoping retrieval to one party would mean putting `party_id` in the prefix, and that
-- forces every ANN search to be per-party. The more valuable query is the opposite one:
-- what did we learn about *anyone* that applies here. That is `SCOPE-RESET §1`'s
-- compounding claim — release n+1 is cheaper because something accumulated across the
-- roster, not just within one artist. Tenant-wide ANN with a post-filter by party costs
-- nothing at this row count and keeps the cross-party query available; the reverse
-- choice would not.
--
-- `party_id` is also nullable on both tables, and a nullable prefix column is a poor
-- index key regardless.
--
--
-- ## Backfill
--
-- None here. The columns stay NULL until an embedder writes them, and the writer must
-- use small `INSERT` batches: the vendor documents that large batch inserts degrade a
-- vector index, and `IMPORT INTO` is unsupported on a table carrying one.
-- `spindle/embed.py` fixes the batch at 16 for that reason.


-- ------------------------------------------------------- the provenance of a vector

ALTER TABLE party_chunk ADD COLUMN IF NOT EXISTS model STRING NOT NULL DEFAULT '';
ALTER TABLE party_chunk ADD COLUMN IF NOT EXISTS model_version STRING NOT NULL DEFAULT '';

-- An embedded chunk with no model is unattributable and therefore unsearchable: it can
-- never match the equality predicate a retrieval has to carry. Better to refuse the
-- write than to accumulate rows the index cannot serve.
ALTER TABLE party_chunk ADD CONSTRAINT chunk_embedding_has_a_model
    CHECK (embedding IS NULL OR model != '') NOT VALID;

ALTER TABLE party_fact ADD CONSTRAINT fact_embedding_has_a_model
    CHECK (embedding IS NULL OR model != '') NOT VALID;


-- --------------------------------------------------------------------- the indexes

-- R1 — semantic search over evidence. "Who else reads like this?"
CREATE VECTOR INDEX IF NOT EXISTS chunk_semantic
    ON party_chunk (tenant_id, model, embedding vector_cosine_ops);

-- R2 — retrieval over what we have concluded. "What do we already know that applies?"
-- `status` is in the prefix because a superseded or retracted fact must never surface
-- in a retrieval, and excluding it with a post-filter would mean over-fetching to
-- compensate. Equality on 'live' is both the correctness rule and the fast path.
CREATE VECTOR INDEX IF NOT EXISTS fact_semantic
    ON party_fact (tenant_id, model, status, embedding vector_cosine_ops);
