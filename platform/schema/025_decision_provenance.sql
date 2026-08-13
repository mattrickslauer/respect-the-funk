-- =============================================================================
-- 025_decision_provenance.sql — a thread remembers the instant that justified it
-- =============================================================================
--
-- This system emails strangers. `010_outreach.sql` gave us the machinery to do it
-- safely — one open thread per counterparty, an idempotency key shared between the
-- message and its outbox row, a terminal `opted_out`. All of that answers *may we
-- send this*. None of it answers the question somebody will eventually ask:
--
--     why this person, and not one of the other fourteen thousand?
--
-- The honest answer is a vector ranking that existed at a particular moment and does
-- not exist any more. Lessons accumulate and rerank. Genres land and change
-- embeddings. `contact_state` moves as threads open, which changes the candidate set
-- itself. The shortlist that put this curator third on Tuesday cannot be recomputed
-- on Friday, because Friday's index is not Tuesday's index.
--
-- CockroachDB can read Tuesday's index directly. What it cannot do is guess *which*
-- Tuesday we mean. That is what these columns are for: not a copy of the ranking, but
-- the coordinate at which the ranking can be read back.
--
--
-- ## Why an HLC and not a timestamp
--
-- The obvious column here is `TIMESTAMPTZ`, and it is wrong.
--
-- `AS OF SYSTEM TIME '2026-08-13 14:22:31.447+00'` is legal and resolves to the most
-- recent MVCC version at or before that wall-clock instant. "At or before" is doing
-- a lot of work: several writes can share a millisecond, and a replay that lands on
-- the wrong side of one reconstructs a ranking nobody ever saw. For a display that is
-- a rounding error. For the record justifying an irreversible act it is a fabrication
-- — and a convincing one, because it looks exactly like the truth.
--
-- `cluster_logical_timestamp()` returns the hybrid logical clock reading of the
-- transaction that called it: a decimal like `1786644836824776765.0000000000`, whose
-- fractional part orders writes *within* a physical instant. Handed straight back to
-- `AS OF SYSTEM TIME`, it names the exact logical instant the shortlist read, not a
-- neighbourhood around it. Verified against this cluster on 2026-08-13: the quoted
-- decimal, the bare decimal and a quoted wall-clock string are all accepted, and only
-- the first two are exact.
--
-- DECIMAL, not STRING, because it is a number and comparing two of them should not
-- depend on how they were formatted. CockroachDB hands the value back as a Python
-- `Decimal` and takes it back without a cast.
--
--
-- ## Why nullable, and why the NULL means something
--
-- `decided_at_hlc IS NULL` is not missing data. It is the statement *no ranking
-- justified this thread* — an operator opened it by hand, from a name they already
-- knew. That is a legitimate way to start a conversation and it must stay legitimate,
-- so the column cannot be NOT NULL.
--
-- It must also stay *distinguishable*. A default of `cluster_logical_timestamp()`
-- would quietly stamp hand-opened threads with the instant they were created, and a
-- replay would then reconstruct "the ranking at the moment somebody typed a name",
-- present it as the justification, and be believed. There is no default here for the
-- same reason `SCOPE-RESET §2a` forbids an inferred fact wearing a measured one's
-- clothes: an absent reason must look absent.
--
-- `attempts`-style backfill is deliberately not done either. Every thread that exists
-- before this migration was opened without recording why, and no value we invent now
-- would be true. They read NULL, which is exactly correct.
--
--
-- ## Why rank and distance as well
--
-- The HLC alone is sufficient to replay, and replay is the strong claim. These two
-- are the cheap check on it: if a replay at `decided_at_hlc` does not put this
-- counterparty at `decided_rank` with `decided_distance`, then something is wrong —
-- the GC window has moved past it, the embedding model changed underneath, or the
-- replay is reading a different query than the one that ran. A claim that can be
-- checked against a stored copy of its own answer is worth more than one that cannot,
-- and two floats per thread is a cheap price for being able to fail loudly.
--
-- FLOAT for the distance because that is what `<=>` returns and rounding it to
-- something prettier would defeat the comparison above.
--
--
-- ## What bounds this
--
-- Replay is only possible while the MVCC history survives, which `gc.ttlseconds`
-- governs — raised on this cluster from CockroachDB's 75-minute default to seven days
-- during the 2026-08-11 sponsor audit. Raising it does not reach backwards: the GC
-- threshold only moves forward, so reachable depth grows from the moment it changed
-- rather than jumping. Measured 2026-08-13: -10h resolved, -18h did not.
--
-- The consequence belongs in the reader, not here, and it is the one behaviour this
-- migration exists to make possible: a replay whose HLC has been collected must say
-- so and return nothing. It must never fall back to the current ranking, because the
-- current ranking is a true answer to a question nobody asked, and presenting it as
-- the historical one is the single worst thing this table could be used to do.

ALTER TABLE thread ADD COLUMN IF NOT EXISTS decided_at_hlc DECIMAL;
ALTER TABLE thread ADD COLUMN IF NOT EXISTS decided_rank INT;
ALTER TABLE thread ADD COLUMN IF NOT EXISTS decided_distance FLOAT;

-- Rank is a position in a list, so zero and negatives are not merely unlikely, they
-- are meaningless. The constraint is NOT VALID against existing rows for the reason
-- every migration in this directory is additive: this is a shared, live cluster, and
-- pre-migration rows carry NULL, which passes anyway.
ALTER TABLE thread ADD CONSTRAINT thread_decided_rank_positive
    CHECK (decided_rank IS NULL OR decided_rank > 0);

-- The three columns are written together or not at all — a rank without the instant
-- it was a rank *at* cannot be replayed, and an instant without a rank cannot be
-- checked. Enforcing that here means the reader never has to handle a half-written
-- decision, and the writer finds out immediately rather than at replay time, which
-- may be days later and in front of somebody asking why we emailed their client.
ALTER TABLE thread ADD CONSTRAINT thread_decision_is_whole
    CHECK (
        (decided_at_hlc IS NULL AND decided_rank IS NULL AND decided_distance IS NULL)
        OR
        (decided_at_hlc IS NOT NULL AND decided_rank IS NOT NULL AND decided_distance IS NOT NULL)
    );
