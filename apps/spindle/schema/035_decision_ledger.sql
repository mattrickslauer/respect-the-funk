-- =============================================================================
-- 035_decision_ledger.sql — every consequential act is rooted to an instant
-- =============================================================================
--
-- `025_decision_provenance.sql` gave one kind of decision a coordinate: a thread
-- remembers the hybrid logical clock at which the shortlist that opened it was read,
-- so *why this person and not one of the other fourteen thousand* has an answer that
-- can be replayed rather than reconstructed from memory.
--
-- That argument was never specific to shortlists. It applies to every act this system
-- takes that a person might later have to be answered about, and there are more of
-- those than one:
--
--     why did you contact me?          the shortlist that selected them
--     why did this message go out?     the approval, and the ranking behind it
--     why were we billed for that?     the cap, and the spend as it stood when raised
--     why did you contact me AGAIN?    the opt-out, and everything after it
--     why did you NOT contact them?    the suppression, which leaves no other trace
--
-- The last two are the reason this table exists rather than four more nullable columns
-- spread across four tables. An opt-out and a suppression are decisions whose entire
-- content is that *nothing happened afterwards*, and a decision that produced no row
-- anywhere else has nowhere to hang a coordinate. A negative decision is exactly the
-- one an audit asks about and exactly the one an event-shaped schema loses.
--
--
-- ## What a row is, and what it is not
--
-- A row here is a **coordinate and a claim**, not a copy. `at_hlc` is the instant the
-- decision was taken; `summary` is what a human reads; `inputs` records the values the
-- decision turned on. What it deliberately does not contain is the decision's *output*
-- — the ranking, the balance, the recipient list. Those are read back by pointing the
-- database at `at_hlc`, which is the whole point:
--
--     a copy of the answer can drift from the data it was computed on.
--     a coordinate into the data cannot.
--
-- So `inputs` is for what a replay cannot recover — a human's stated reason, the
-- operator who acted, an amount agreed on a call. Anything the database already knows
-- at `at_hlc` must NOT be duplicated here. A `budget_increase` records who raised it
-- and to what; it does not record the spend at the time, because the spend at the time
-- is readable and a stored copy of it is a second source of truth that can disagree.
--
--
-- ## The one exception: `inputs.check`, and why it is not a copy
--
-- The rule above, taken literally, forbids the thing that makes a replay worth having.
-- A replay nobody can falsify is a claim, not evidence: re-running the query and
-- printing whatever comes back proves only that the query still runs. `025` already
-- solved this and the solution is worth stating as a general rule rather than
-- rediscovering per kind — it stores `decided_rank` and `decided_distance` next to
-- `decided_at_hlc` for exactly one purpose, which its own header gives as *"if a replay
-- at `decided_at_hlc` does not put this counterparty at that rank, something is wrong
-- and you would know"*. `outreach.justification` returns that comparison as `matched`.
--
-- So `inputs` may carry a reserved `check` object, and only that object is exempt:
--
--     the ANSWER is forbidden      — the ranking, the balance, the recipient list.
--                                    Large, derived, and the thing a coordinate exists
--                                    to fetch. Storing it creates the second source of
--                                    truth this table is designed to avoid.
--     a CHECK VALUE is required    — a small scalar the replay must reproduce. It is
--     wherever a replay is claimed   not consulted in place of the replay; it is
--                                    compared against it, and a disagreement is the
--                                    finding.
--
-- The distinction is direction of use. An answer is read *instead of* replaying. A
-- check is read *only after* replaying, to see whether the replay agrees. A stored
-- value that nothing ever contradicts is a copy; a stored value whose whole job is to
-- be contradicted is a test.
--
-- Keep it small and scalar for the same reason `025` stored a rank and a distance
-- rather than the ranking: a check that is as big as the answer has become the answer.
--
-- **But a scalar that cannot fail is not a check.** `025` stored two values, not one,
-- and the pairing is the point: a rank alone would match by coincidence far more often
-- than a rank and a distance together. The same trap appears wherever a figure is a
-- *floor* rather than a total — a valuation that excludes what it could not measure,
-- say. Seven items valued and none dropped can produce the identical figure to three
-- valued and four dropped, so a check holding the figure alone agrees in a case where
-- the composition changed completely. Store whatever second scalar makes the first
-- interpretable (the count that was excluded, the population it was drawn from) so the
-- comparison has something to fail on. Two small numbers is still nothing like the
-- answer; one number that cannot disagree is worse than none, because it reads as
-- verification while verifying nothing.
--
--
-- ## Why an HLC, and why a wall clock as well
--
-- `at_hlc` is `cluster_logical_timestamp()` — the reading `025` argues for at length,
-- and that argument is not repeated here. Short version: several writes can share a
-- millisecond, and a replay that lands on the wrong side of one reconstructs a state
-- nobody ever saw. The HLC's fractional part orders writes inside a physical instant,
-- so it names the exact logical moment and not a neighbourhood around it.
--
-- `at_wall` exists because a human reading an audit needs a date, and a decimal like
-- `1786644836824776765.0000000000` is not one. It is for **display only**. Handing it
-- to a time-travel read would reintroduce precisely the ambiguity `at_hlc` exists to
-- remove, so the rule is: `at_wall` is what you show, `at_hlc` is what you query with,
-- and code that queries with `at_wall` is a bug even though it will appear to work.
--
--
-- ## `stage`, and why an unattended act is one row and not two
--
-- An autonomy policy decides, per capability, whether an action runs unattended or
-- queues for a person. That splits some acts in two and leaves others whole, and the
-- ledger has to represent both without lying about either:
--
--     the proposal is always a decision.
--     the resolution is a decision if and only if a person made one.
--
-- Unattended, there is one act. The agent computes, consults the policy, applies the
-- change and commits, all against one `cluster_logical_timestamp()`. Writing a proposal
-- row and a resolution row for that would record two decisions at one instant where one
-- happened, and an auditor counting rows would double every automatic action. Worse, a
-- synthesised resolution stamped with the proposal's own instant reads as *a human
-- approved this at 14:22* for something no human ever saw — which is the fabrication
-- this whole file exists to prevent, arriving through the back door.
--
-- So an `applied` row with **no `proposed` row before it** is the statement that nobody
-- was asked, and that absence stays absent. That is `025`'s NULL argument one level up:
-- the missing row carries the meaning, and synthesising it to make the two paths look
-- alike destroys the only evidence that they differed. A useful consequence is that the
-- paths are distinguishable by row count alone — one row is unattended, two is attended
-- — without joining anything.
--
-- A `proposed` row with no resolution yet is a third, honest state: somebody was asked
-- and has not answered. It is not the same as unattended and must not be read as one.
--
-- `stage` carries this rather than a `_proposed` suffix on every kind, because the
-- autonomy policy is per *capability*, not per budget. Suffixing would need a
-- `_proposed` and a `_refused` twin for every kind the policy ever governs, and a closed
-- set that must grow combinatorially is a closed set that will be opened. `kind` stays
-- the act; `stage` says where in the attended flow it sat.
--
--     proposed   an agent asked a person. Only ever written when one was actually asked.
--     applied    the act happened. The only stage an unattended action writes.
--     refused    a person declined. A decision somebody is more likely to be asked
--                about than an approval, and it has no other trace — the cap did not
--                move, so nothing downstream records that anyone considered moving it.
--
--
-- ## Why `kind` is a closed set
--
-- A CHECK rather than a lookup table, for the reason this repository prefers loud
-- failures: an unknown kind is a programming error, and the useful moment to find out
-- is at the INSERT that invented it, not at the report that silently omits it. Adding
-- a kind is a migration, which is the correct amount of friction for adding a new
-- category of thing this system is answerable for.
--
--
-- ## The tenant is first in the primary key
--
-- As everywhere: `SCOPE-RESET.md` §1 fixes this as partition-key-class and the SQL
-- scoping lint parses every statement in `spindle/` to keep it true. An audit ledger
-- that could be read across a tenant boundary would be worse than no audit ledger,
-- because it would be an audit ledger that leaks.

CREATE TABLE IF NOT EXISTS decision (
    tenant_id    UUID        NOT NULL,
    id           UUID        NOT NULL DEFAULT gen_random_uuid(),

    kind         STRING      NOT NULL,

    -- Where in the attended flow this sat. `applied` is the default because the
    -- unattended path is one row and that row is the act happening; a writer that
    -- says nothing is saying "this happened", which is the common case and the safe
    -- reading. See the header for why this is a column and not a suffix on `kind`.
    stage        STRING      NOT NULL DEFAULT 'applied',

    at_hlc       DECIMAL     NOT NULL,
    at_wall      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- What the decision was about. `subject_id` is nullable because a tenant-level
    -- decision — a plan change, a cap raise — has no narrower subject than the tenant
    -- already named by `tenant_id`.
    subject_kind STRING      NOT NULL,
    subject_id   UUID,

    -- Who. An agent name (`scout`, `sender`) or a human, recorded as the address that
    -- authenticated rather than a display name, because the display name can change
    -- and an audit trail that renames its own actors is not one.
    actor        STRING      NOT NULL,

    summary      STRING      NOT NULL,
    inputs       JSONB       NOT NULL DEFAULT '{}'::JSONB,

    CONSTRAINT decision_pk PRIMARY KEY (tenant_id, id)
);

-- The closed set. Each of these is a question somebody can ask after the fact.
ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_kind_known;
ALTER TABLE decision ADD CONSTRAINT decision_kind_known CHECK (kind IN (
    'shortlist',        -- a counterparty was selected out of the index
    'send',             -- a message left the building
    'budget_increase',  -- a spend cap was raised
    'plan_change',      -- the tier moved
    'opt_out',          -- somebody asked us to stop, and that is terminal
    'suppress',         -- we decided NOT to contact somebody
    'lesson'            -- something learned that reranks future shortlists
));

ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_stage_known;
ALTER TABLE decision ADD CONSTRAINT decision_stage_known
    CHECK (stage IN ('proposed', 'applied', 'refused'));

-- `recording` is here because money is spent promoting a *record*, not an artist in
-- general: a track budget is keyed `(tenant_id, recording_id)`, and the question a
-- `budget_increase` row exists to answer is *why did you put another two hundred
-- dollars behind this one*. Neither alternative can express that. `tenant` with a NULL
-- subject drops the track, which is the entire content of the question. `campaign` is
-- worse than useless here: `campaign.recording_id` is nullable on purpose — an
-- artist-level push is a real campaign with no single recording behind it — so a
-- single's push and an artist's push would land at the same subject and be
-- indistinguishable afterwards.
--
-- `release` is deliberately NOT here. It is the obvious next candidate and nothing
-- needs it yet, and this set is one a migration has to widen precisely so that adding a
-- category of thing we are answerable for stays a decision somebody makes rather than a
-- default somebody inherits.
ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_subject_kind_known;
ALTER TABLE decision ADD CONSTRAINT decision_subject_kind_known
    CHECK (subject_kind IN ('party', 'recording', 'thread', 'message',
                            'tenant', 'campaign'));

-- An HLC is a positive decimal. Zero is not an instant, and a negative one is a
-- corrupted write that should fail at the INSERT rather than at a replay months later.
ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_at_hlc_positive;
ALTER TABLE decision ADD CONSTRAINT decision_at_hlc_positive CHECK (at_hlc > 0);

-- A summary is what a person reads when they ask the question. An empty one makes the
-- row unreadable to the only audience it has.
ALTER TABLE decision DROP CONSTRAINT IF EXISTS decision_summary_present;
ALTER TABLE decision ADD CONSTRAINT decision_summary_present
    CHECK (length(summary) > 0);

-- The read this table exists for: the most recent decisions for a tenant, newest
-- first, and the same filtered to one kind. Both are prefixed by `tenant_id` so the
-- scan cannot begin outside the boundary.
CREATE INDEX IF NOT EXISTS decision_recent
    ON decision (tenant_id, at_hlc DESC);
CREATE INDEX IF NOT EXISTS decision_by_kind
    ON decision (tenant_id, kind, at_hlc DESC);

-- Answering "what happened to this person" needs the decisions about one subject, and
-- that read is by subject rather than by time.
CREATE INDEX IF NOT EXISTS decision_by_subject
    ON decision (tenant_id, subject_kind, subject_id, at_hlc DESC);
