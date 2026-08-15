-- =============================================================================
-- 035_autonomy_and_track_budget.sql — what the agent may do without being asked
-- =============================================================================
--
-- Two halves of this system have spent every migration so far at opposite extremes,
-- and neither extreme was ever chosen.
--
-- **Outreach is always human.** `/approvals` renders a draft, somebody clicks, and
-- `message.approved_by` is stamped; `sender.py` refuses an outbox row nobody signed.
-- There is no way to let an agent send, and `010_outreach.sql` never argued there
-- should not be — the approval queue was simply the only shape built.
--
-- **Spending is always automatic.** `spend.Gate` estimates a call before making it and
-- refuses over the ceiling. There is no way to ask a person instead. A refusal is the
-- only brake, and a refusal is not the same offer as "this one is big, look at it".
--
-- So a label that wants its agent to mail freely but to be consulted before anything
-- expensive cannot say so, and neither can the label that wants the reverse. This
-- migration is where that becomes a setting rather than a property of which module you
-- happen to be standing in.
--
--
-- ## Why a ceiling and not a switch
--
-- The obvious table is `(tenant_id, capability, enabled BOOL)`. It was rejected, and
-- the reason is that the interesting policy is not binary. "Send email on your own, but
-- ask me before spending more than five dollars" is the sentence real operators say,
-- and a boolean cannot hold it — it forces the answer to collapse to *never ask* or
-- *always ask*, and an operator who is asked about everything stops reading the queue,
-- which is worse than not having one.
--
-- A ceiling holds both. `mode = 'human'` asks every time regardless of cost, which is
-- the boolean's `false`. `mode = 'auto'` with a ceiling of zero is the same thing
-- reached from the other side, and is therefore *forbidden* below rather than allowed
-- as a second spelling — two ways to say one thing is two things to keep in agreement.
-- `mode = 'always'` never asks, and has to be typed out, because a ceiling high enough
-- to mean "never" is a number somebody picked and will later misread as a limit.
--
-- The cost being compared against is the one `spend.estimate` already computes before
-- every priced call. That function is the reason this design is small: the hard part of
-- "how expensive is this action" was solved in `spend.py` and is being reused, not
-- reinvented per capability.
--
--
-- ## Absence means human, and it is not a fallback
--
-- A tenant with no row for a capability gets `human`. This is the same default-deny
-- `spend.Policy` takes and for the same reason — the failure mode of a missing row must
-- point at *not acting*. `MEMORY.md`'s no-fallbacks rule is about silent defaults that
-- hide an error; this default is neither silent nor a guess. It is the conservative
-- answer to a question nobody has answered yet, it is stated here, and the console
-- shows a capability with no row as "asks every time" rather than as unconfigured.
--
-- What is deliberately *not* here is a NULL ceiling meaning unlimited. `spend.py` has
-- the same note: a number that means infinity when absent is a number that grants
-- permission by omission. Unlimited is `mode = 'always'`, spelled out.
--
--
-- ## `recording_budget`: money per track, next to quota per artist
--
-- `005_party_first.sql` already gave us `party_budget`, and it is worth being precise
-- about why this is not that table with more columns. `party_budget` bounds *effort* —
-- leads per run, documents per run, tokens per hour, crawl depth. It is keyed on a
-- party because effort is spent discovering things about a party.
--
-- Money is spent promoting a *record*. A label with one artist and four singles wants
-- the budget behind the single that is working, and `party_budget` cannot express that
-- because it has no recording column and would mean something different if it did.
-- Adding `allocated_usd` to it would make one row mean "how hard to crawl this artist"
-- and "how much to spend on this artist", which are set by different people for
-- different reasons on different days.
--
-- `ceiling_usd` is the part that makes automatic raising safe. `allocated_usd` is what
-- the track may spend now; `ceiling_usd` is the highest that number may ever reach
-- without a human. An agent raising its own budget with no upper stop is an agent with
-- no budget, and the whole point of the row is that the stop exists and somebody chose
-- it.
--
--
-- ## `budget_raise`: the workflow, and why the instant is not here
--
-- A raise is caused by facts that get overwritten. `party_metric` rows are re-observed,
-- `party_fact` rows are superseded, profiles are recomposed. "Why did you put another
-- two hundred dollars behind this track" cannot be answered on Friday from Tuesday's
-- numbers, because Tuesday's numbers are gone. `025_decision_provenance.sql` made this
-- argument for threads and every word of it applies here.
--
-- The first draft of this table therefore carried its own `decided_at_hlc`. It does not
-- any more, and the reason is `035_decision_ledger.sql`: that table already records one
-- row per consequential act, rooted to `cluster_logical_timestamp()`, and a spend cap
-- being raised is exactly such an act. Two tables each holding an instant for the same
-- event is two tables that can disagree about when it happened — which is the failure
-- `035` exists to prevent, arriving by way of a second implementation of it.
--
-- So the split is by *job*, not by subject:
--
--   * **`decision` is the provenance.** When, at what logical instant, by whom, and the
--     small scalars a replay must reproduce. It is the canonical answer to *when can
--     this be argued from*, and `budget_raise.decision_id` points at it.
--   * **`budget_raise` is the workflow.** The state machine a queued raise moves
--     through, the two amounts, and `basis_json` — the fuller record of what the
--     valuation saw, which is far too big to live in a ledger check.
--
-- `035`'s own rule governs how many decision rows a raise writes, and it is worth
-- restating because it is the part that is easy to get wrong: *the proposal is always a
-- decision; the resolution is a decision if and only if a person made one.* An
-- unattended raise is one act — the agent values, consults the policy, moves the money
-- and commits against a single timestamp — so it writes one `applied` row. A queued
-- raise writes `proposed` and later `applied` or `refused`. Row count alone therefore
-- says whether anybody was asked, with nothing to join.
--
-- `decision_id` is NOT NULL because every raise has a first decision, including a raise
-- a person typed in by hand: somebody deciding is still a decision, and `035` records it
-- with the human as `actor`. What distinguishes the hand-typed raise is not an absent
-- instant but an absent *valuation* — `basis_json IS NULL` — which is the same
-- distinction `025` draws with its NULL, moved to the column that can actually carry it.
--
-- `resolution_decision_id` is nullable and its NULL means something: nobody has answered
-- yet, or nobody was asked. Which of the two is readable from the decision it points
-- back to, per the rule above, and must not be inferred from this column alone.
--
-- The foreign keys are composite — `(tenant_id, decision_id)` against `decision`'s
-- `(tenant_id, id)` primary key — rather than pointing at `id` alone. A narrow key would
-- let a raise reference another tenant's decision row: unlikely, and unrepresentable is
-- better than unlikely on the edge between a spending record and its audit trail.
-- =============================================================================


-- ------------------------------------------------------------------ autonomy

CREATE TABLE IF NOT EXISTS autonomy (
    tenant_id  UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    -- What this row governs. Deliberately a small closed set rather than free text:
    -- a typo in a capability name would silently create a second policy that nothing
    -- reads, and the reader would then get `human` from the absent row it meant to
    -- configure — permission denied by misspelling, which is the safe direction but
    -- impossible to debug. The CHECK makes it a write error instead.
    --
    --   outreach     — sending a pitch to a counterparty
    --   spend        — any priced provider call through `spend.Gate`
    --   content      — generating copy, audio or artwork
    --   budget_raise — raising a track's own allocation
    capability STRING NOT NULL,

    -- human  — always queue for a person, whatever it costs
    -- auto   — act unattended when the estimate is at or under the ceiling, else queue
    -- always — never queue, whatever it costs
    mode       STRING NOT NULL DEFAULT 'human',

    -- The most one action may cost and still happen unattended, in dollars, compared
    -- against `spend.estimate`. Read only when `mode = 'auto'`; the CHECK below stops
    -- it being set to something meaningless under the other two modes rather than
    -- letting a stale number sit there looking like policy.
    unattended_ceiling_usd DECIMAL(12, 6) NOT NULL DEFAULT 0,

    -- Who set this and when. An autonomy policy is the row a dispute about an
    -- unattended action is settled from, so an unattributed change is not acceptable
    -- here for the same reason `tenant_budget.written_by` exists.
    written_by STRING NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, capability),

    CONSTRAINT autonomy_capability_known
        CHECK (capability IN ('outreach', 'spend', 'content', 'budget_raise')),
    CONSTRAINT autonomy_mode_known
        CHECK (mode IN ('human', 'auto', 'always')),
    -- `auto` with a zero ceiling is `human` spelled a second way, and two spellings of
    -- one policy is a disagreement waiting to happen. Say `human`.
    CONSTRAINT autonomy_auto_ceiling_positive
        CHECK (mode != 'auto' OR unattended_ceiling_usd > 0),
    -- A ceiling under `human` or `always` is a number nothing reads. Left set, it looks
    -- like a limit to the next person and is not one.
    CONSTRAINT autonomy_ceiling_only_when_auto
        CHECK (mode = 'auto' OR unattended_ceiling_usd = 0),
    CONSTRAINT autonomy_ceiling_not_negative
        CHECK (unattended_ceiling_usd >= 0)
);


-- ---------------------------------------------------------- recording_budget

CREATE TABLE IF NOT EXISTS recording_budget (
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,

    -- What this track may spend now. Default 0 is default-deny, matching
    -- `tenant_budget.daily_ceiling_usd`: a row that exists and says nothing allows
    -- nothing.
    allocated_usd DECIMAL(12, 6) NOT NULL DEFAULT 0,

    -- The highest `allocated_usd` may ever reach without a person. This is the stop on
    -- automatic raising and the reason `budget_raise` can be trusted to run unattended.
    -- It is not a spend limit — `tenant_budget` and `spend.Policy` still bound the day
    -- and the call. It bounds the *agent's own discretion*.
    ceiling_usd   DECIMAL(12, 6) NOT NULL DEFAULT 0,

    -- Per-track kill switch, distinct from a zero allocation for exactly the reason
    -- `tenant_budget.paused` is: "nothing left" and "somebody stopped this" have
    -- different remedies and read identically if collapsed into one number.
    paused        BOOL NOT NULL DEFAULT false,

    written_by    STRING NOT NULL DEFAULT '',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, recording_id),

    CONSTRAINT recording_budget_allocated_not_negative
        CHECK (allocated_usd >= 0),
    CONSTRAINT recording_budget_ceiling_not_negative
        CHECK (ceiling_usd >= 0),
    -- An allocation already above its own ceiling would make every future raise refuse
    -- with a message about a limit that has already been passed. Forbidden at write
    -- time so the confusing state cannot exist.
    CONSTRAINT recording_budget_allocated_within_ceiling
        CHECK (allocated_usd <= ceiling_usd)
);


-- --------------------------------------------------------------- budget_raise

CREATE TABLE IF NOT EXISTS budget_raise (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    recording_id UUID NOT NULL REFERENCES recording(id) ON DELETE CASCADE,

    -- Both sides of the change, not a delta. A delta plus a current value cannot
    -- reconstruct history once a second raise lands, and this table exists to be read
    -- long after the fact.
    from_usd     DECIMAL(12, 6) NOT NULL,
    to_usd       DECIMAL(12, 6) NOT NULL,

    -- applied        — `recording_budget.allocated_usd` was moved, in this transaction
    -- awaiting_human — over the autonomy ceiling; nothing moved, it is in the queue
    -- rejected       — a person said no
    -- withdrawn      — superseded before anybody answered
    state        STRING NOT NULL DEFAULT 'awaiting_human',

    -- The provenance row for the act that created this raise: `stage = 'applied'` when
    -- it ran unattended, `stage = 'proposed'` when it went to a person. `decision.at_hlc`
    -- is the instant `budgets.replay` time-travels to, and it lives there rather than
    -- here so that exactly one table can be wrong about when this happened.
    decision_id  UUID NOT NULL,

    -- The person's answer, when there was one. NULL means nobody has answered yet or
    -- nobody was asked — see the header on why those two are told apart by reading the
    -- decision this points back to rather than by this column's absence.
    resolution_decision_id UUID,

    -- What the valuation actually saw: the counterparty values summed, how many were
    -- unvalued and therefore excluded, the metric each value came from. Not prose — the
    -- numbers, so a person can read what the raise was built on without re-deriving it.
    --
    -- This is the *fuller record*, not the check. The check is two scalars in
    -- `decision.inputs.check`, and `035`'s header draws the line: a check as big as the
    -- answer has become the answer. `budgets.replay` therefore compares the replayed
    -- valuation against the ledger's two numbers and keeps this as the detail a human
    -- reads afterwards.
    --
    -- **NULL means a person set the budget by hand.** No valuation was computed, so there
    -- is nothing to replay — which is a different statement from "we have lost the
    -- reason". The act still has an instant, in `decision`, with the human as `actor`;
    -- what is absent is the computation, and `025`'s NULL argument is why that absence
    -- must stay visible rather than being filled in with something plausible.
    basis_json   JSONB,

    -- Set when a person resolves an `awaiting_human` row. Empty on an automatic raise,
    -- which is the distinction the console renders and the one an audit asks about.
    approved_by  STRING NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,

    CONSTRAINT budget_raise_state_known
        CHECK (state IN ('applied', 'awaiting_human', 'rejected', 'withdrawn')),
    CONSTRAINT budget_raise_amounts_not_negative
        CHECK (from_usd >= 0 AND to_usd >= 0),
    -- This table records *raises*. A reduction is a different act with a different
    -- audit question behind it, and letting it in here would make "sum of raises"
    -- silently wrong.
    CONSTRAINT budget_raise_is_a_raise
        CHECK (to_usd > from_usd),
    -- A human resolution has a name against it. Without this an operator's click and an
    -- unattended action become indistinguishable in the one table that exists to tell
    -- them apart.
    CONSTRAINT budget_raise_resolution_is_attributed
        CHECK (state NOT IN ('rejected') OR approved_by != ''),
    -- A resolved raise points at the decision that resolved it. `rejected` is the state
    -- worth naming here: the cap did not move, so this row and its decision are the only
    -- evidence anybody ever considered moving it.
    CONSTRAINT budget_raise_rejection_has_a_decision
        CHECK (state != 'rejected' OR resolution_decision_id IS NOT NULL),

    CONSTRAINT budget_raise_decision_fk
        FOREIGN KEY (tenant_id, decision_id)
        REFERENCES decision (tenant_id, id),
    CONSTRAINT budget_raise_resolution_fk
        FOREIGN KEY (tenant_id, resolution_decision_id)
        REFERENCES decision (tenant_id, id)
);


-- The queue read: what is waiting for a person, newest first. `state` leads the
-- non-tenant columns because every read of this index filters on it — the console asks
-- for `awaiting_human` and the valuation asks whether a pending raise already exists
-- before proposing a second one.
CREATE INDEX IF NOT EXISTS budget_raise_pending
    ON budget_raise (tenant_id, state, created_at DESC);

-- The history read: every raise against one track, for the console's per-track page and
-- for the replay that answers why this number is what it is.
CREATE INDEX IF NOT EXISTS budget_raise_by_recording
    ON budget_raise (tenant_id, recording_id, created_at DESC);
