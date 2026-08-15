-- 009 — the shortlist index: who should we take this record to?
--
-- `PLATFORM-SPEC §6` calls this R1, and names it the query that justifies the vector
-- index existing at all. Everything before this migration made the index possible; this
-- is the one that gives it a job worth doing.
--
--
-- ## No `counterparty` table
--
-- `PLATFORM-SPEC §2c` specified one. It is not built here, because migration 005 already
-- replaced the artist-shaped root with a Party, and a playlist curator is a party — a
-- person or a group, with a name, surfaces on platforms, and a role. `party_role` already
-- says what someone is to us, `presence` already says where they are, `party_document`
-- and `party_chunk` already hold what we have read about them.
--
-- A second table would duplicate all four and then need reconciling with them the first
-- time a curator turned out to also be an artist — which, in this industry, is most
-- Tuesdays. The unification claim in `SCOPE-RESET §2` was that these differ in playbook,
-- not in structure. Taking that seriously means adding columns, not tables.
--
--
-- ## Four columns, and why each one is on `party` rather than derived
--
-- `docs/reference/COCKROACHDB-AI.md` records the constraint that shapes this: a vector
-- index accelerates a filter only on prefix columns, and only with equality or `IN`. R1
-- wants to ask for contactable counterparties of a given kind, ordered by similarity —
-- and every one of those predicates has to be an equality column on the same table as
-- the vector, or the query degrades to over-fetching `LIMIT k × n` and discarding.
--
-- So:
--
--   * `party_class`      — roster or counterparty. The single most important predicate
--                          in the system: shortlisting our own artists to pitch to
--                          ourselves is the failure this prevents. It is denormalised
--                          from `party_role` deliberately, exactly as §6's amendment
--                          prescribes, and it is safe for the same reason: it is written
--                          in the same serializable transaction as the role it mirrors.
--   * `contact_state`    — contactable | in_thread | declined | unusable. A shortlist
--                          that returns somebody already mid-conversation is worse than
--                          useless, and `NOT EXISTS (…)` is a subquery, which is not
--                          accelerated. This is that subquery, precomputed.
--   * `profile_embedding`— what they sound like they care about, as a vector.
--   * `embedding_model`  — which model produced it. Same rule as `party_chunk`: two
--                          models in one index is a cosine distance that is a
--                          well-formed float and pure noise, and the only defence is to
--                          make the model an equality predicate that retrieval must
--                          carry.
--
-- Existing rows default to `roster`, which is correct — the three parties on this
-- cluster today are the label's own artists.


ALTER TABLE party ADD COLUMN IF NOT EXISTS party_class STRING NOT NULL DEFAULT 'roster';
ALTER TABLE party ADD COLUMN IF NOT EXISTS contact_state STRING NOT NULL DEFAULT 'contactable';
ALTER TABLE party ADD COLUMN IF NOT EXISTS profile_embedding VECTOR(1024);
ALTER TABLE party ADD COLUMN IF NOT EXISTS embedding_model STRING NOT NULL DEFAULT '';

ALTER TABLE party ADD CONSTRAINT party_class_known
    CHECK (party_class IN ('roster', 'counterparty'));

ALTER TABLE party ADD CONSTRAINT party_contact_state_known
    CHECK (contact_state IN ('contactable', 'in_thread', 'declined', 'unusable', 'stale'));

-- The same rule `007` applied to chunks and facts: an embedding that cannot name its
-- model can never satisfy the equality predicate a retrieval carries, so it is
-- unsearchable and should not be written.
ALTER TABLE party ADD CONSTRAINT party_embedding_has_a_model
    CHECK (profile_embedding IS NULL OR embedding_model != '') NOT VALID;


-- R1 — the shortlist. Every prefix column is filtered by equality:
--
--     WHERE tenant_id = $1 AND embedding_model = $2
--       AND party_class = 'counterparty' AND contact_state = 'contactable'
--     ORDER BY profile_embedding <=> $3 LIMIT 50
--
CREATE VECTOR INDEX IF NOT EXISTS party_shortlist
    ON party (tenant_id, embedding_model, party_class, contact_state,
              profile_embedding vector_cosine_ops);


-- Counterparties arrive from discovery in bulk and are read by name constantly; the
-- roster is read by slug. Neither is served by the vector index, which only answers
-- similarity questions.
CREATE INDEX IF NOT EXISTS party_by_class
    ON party (tenant_id, party_class, status) STORING (name, contact_state);
