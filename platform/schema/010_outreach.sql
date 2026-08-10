-- 010 — outreach: the campaign, the thread, the message and the outbox.
--
-- `PLATFORM-SPEC §2d` specified these four tables and `§3` specified how they
-- coordinate. This migration builds them, which turns the last four wireframe views
-- (`/campaigns`, `/threads`, `/approvals`, `/inbox`) into views over real rows.
--
--
-- ## Party-first, the same as 009
--
-- The spec was written before migration 005 and names `artist_id`, `track_id` and
-- `counterparty_id` as if each pointed at its own table. Two of those tables no longer
-- exist and the third never did:
--
--   * `artist_id`       -> `party_id`, a party whose class is `roster`
--   * `track_id`        -> `recording_id`, because a campaign promotes a specific
--                          master, not the composition behind it — the split 005 drew
--                          between `work` and `recording` exists for exactly this
--   * `counterparty_id` -> `party_id` again, a party whose class is `counterparty`
--
-- So `thread` carries two party references with different classes, and the column names
-- say which is which rather than relying on the reader to remember.
--
--
-- ## The state machine is a CHECK, not a convention
--
-- `§2d` lists fourteen states. They are written into a constraint here so a typo in an
-- UPDATE is a failed transaction rather than a thread that quietly stops matching every
-- query that filters on state. A stuck thread is invisible; a rejected write is not.
--
--
-- ## §3c is the reason this migration is interesting
--
-- `one_open_thread_per_counterparty` is a partial unique index. It is what stops two
-- campaigns working the same person at the same time, and it is structural — the second
-- campaign's INSERT fails and it never needs to know the first one exists. The partial
-- predicate is what releases the counterparty again: closing a thread takes its row out
-- of the index, and the next campaign can open one.
--
-- `contact_state` on `party` is the denormalised mirror of that, maintained in the same
-- transaction, because `§6`'s amendment established that the shortlist's filter has to
-- be an equality predicate on a prefix column and `NOT EXISTS (…)` is neither.
-- `outreach.py` writes both or neither.


-- ------------------------------------------------------------------- campaign

-- One track, one channel, one goal. Channels run in parallel because a channel is a
-- column rather than a code path.
CREATE TABLE IF NOT EXISTS campaign (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    -- Whose campaign it is. The party is where lessons accumulate, which is
    -- `SCOPE-RESET §1`'s whole argument for the artist being the root.
    party_id     UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    -- What it is promoting. Nullable: an artist-level push — a profile feature, a
    -- residency announcement — is a real campaign with no single recording behind it.
    recording_id UUID REFERENCES recording(id) ON DELETE CASCADE,
    name         STRING NOT NULL,
    channel      STRING NOT NULL DEFAULT 'curator',
    goal         STRING NOT NULL DEFAULT '',
    state        STRING NOT NULL DEFAULT 'draft',
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT campaign_channel_known CHECK (
        channel IN ('curator', 'ugc', 'press', 'radio', 'sync')),
    CONSTRAINT campaign_state_known CHECK (
        state IN ('draft', 'running', 'paused', 'done'))
);

CREATE INDEX IF NOT EXISTS campaign_by_party
    ON campaign (tenant_id, party_id, created_at DESC);


-- --------------------------------------------------------------------- thread

-- One conversation with one counterparty inside one campaign. The lock lives on the
-- work item — `owner_agent` plus `lease_expires_at` is the same claim primitive `lead`
-- already uses, so `fleet.claim` works here unchanged.
CREATE TABLE IF NOT EXISTS thread (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    campaign_id    UUID NOT NULL REFERENCES campaign(id) ON DELETE CASCADE,
    -- A party whose class is `counterparty`. Not enforced by a foreign key to a subset,
    -- because there is no such thing — it is enforced by `outreach.open_thread`, which
    -- is the only writer.
    counterparty_id UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,

    state          STRING NOT NULL DEFAULT 'discovered',
    -- Why it is in that state, in the words of whoever moved it. An operator reading a
    -- stalled thread wants the reason, and reconstructing it from message bodies is
    -- work the writer could have done once.
    reason         STRING NOT NULL DEFAULT '',

    next_action_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_agent    STRING,
    lease_expires_at TIMESTAMPTZ,
    attempts       INT NOT NULL DEFAULT 0,
    last_error     STRING NOT NULL DEFAULT '',

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at      TIMESTAMPTZ,

    -- `§2d`'s fourteen states, in the order they happen.
    CONSTRAINT thread_state_known CHECK (state IN (
        'discovered', 'shortlisted', 'approved', 'drafted', 'awaiting_human',
        'queued', 'sent', 'awaiting_reply', 'replied', 'negotiating', 'agreed',
        'delivered', 'verified',
        'closed_won', 'closed_lost', 'closed_no_reply')),

    UNIQUE (campaign_id, counterparty_id)
);

-- §3c. The cross-channel collision, settled structurally rather than by convention:
-- a creator who is also a curator cannot be worked by two fleets at once. The partial
-- predicate is load-bearing in both directions — it enforces the lock while the thread
-- is open and releases it the moment the thread closes.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_thread_per_counterparty
    ON thread (tenant_id, counterparty_id)
 WHERE state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply');

-- The claim query's access path: state equality, then due-ness, ordered by due-ness.
CREATE INDEX IF NOT EXISTS thread_claimable
    ON thread (tenant_id, state, next_action_at)
 STORING (owner_agent, lease_expires_at, campaign_id, counterparty_id);


-- -------------------------------------------------------------------- message

-- Everything actually said, in both directions. Append-only: an outbound row is written
-- before the send and never rewritten, so "what did we tell them" survives a redraft.
CREATE TABLE IF NOT EXISTS message (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    thread_id   UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,

    direction   STRING NOT NULL,
    channel     STRING NOT NULL DEFAULT 'email',
    subject     STRING NOT NULL DEFAULT '',
    body        STRING NOT NULL DEFAULT '',

    -- Inbound only, written by the classifier. Empty on outbound.
    intent      STRING NOT NULL DEFAULT '',
    -- Inbound only. How sure the classifier was, so a low-confidence read can be shown
    -- as one rather than acted on as fact.
    confidence  FLOAT NOT NULL DEFAULT 0,

    -- What the provider called it, once there is a provider. Empty until sent.
    provider_message_id STRING NOT NULL DEFAULT '',
    -- §3b's deduplication key. The message row and the outbox row are written in one
    -- transaction carrying this; a crash between claim and send retries against the same
    -- key and the provider deduplicates. Unique across the tenant, not the thread,
    -- because the provider's idea of "already sent this" is global.
    idempotency_key STRING NOT NULL,

    -- Which draft an operator approved, and who approved it. A send with no approver is
    -- the thing the gate exists to make impossible, and the record is how you show it.
    approved_by STRING NOT NULL DEFAULT '',
    approved_at TIMESTAMPTZ,

    sent_at     TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT message_direction_known CHECK (direction IN ('outbound', 'inbound')),
    CONSTRAINT message_intent_known CHECK (intent IN (
        '', 'interested', 'declined', 'question', 'out_of_office',
        'unsubscribe', 'bounce', 'unclear')),
    -- A reply nobody sent us is a bug in the reader, and an outbound message that
    -- arrived is a bug in the writer. Cheap to check, expensive to notice later.
    CONSTRAINT message_time_matches_direction CHECK (
        (direction = 'outbound' AND received_at IS NULL)
     OR (direction = 'inbound'  AND sent_at IS NULL)),

    UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS message_in_thread
    ON message (tenant_id, thread_id, created_at);

-- The inbox view: unhandled inbound, newest first.
CREATE INDEX IF NOT EXISTS message_unread
    ON message (tenant_id, direction, received_at DESC)
 STORING (thread_id, subject, intent);


-- --------------------------------------------------------------------- outbox

-- §3b: the agent never sends. It writes a `message` row and an `outbox` row in one
-- transaction; a sender claims from here, calls the provider, and records the provider's
-- id against the key it already holds.
--
-- Nothing claims this table yet. That is deliberate and it is the honest state: the
-- send is the one irreversible act in the product, no provider is wired, and a row
-- sitting here in `pending` is a send that is fully prepared and has not happened.
CREATE TABLE IF NOT EXISTS outbox (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    thread_id   UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    -- The message this will deliver. One row each, which is what makes the
    -- idempotency key shared between them meaningful.
    message_id  UUID NOT NULL REFERENCES message(id) ON DELETE CASCADE,

    kind        STRING NOT NULL DEFAULT 'email',
    payload_json JSONB,

    state       STRING NOT NULL DEFAULT 'pending',
    attempts    INT NOT NULL DEFAULT 0,
    last_error  STRING NOT NULL DEFAULT '',

    claimed_by  STRING,
    claimed_at  TIMESTAMPTZ,
    not_before  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT outbox_state_known CHECK (
        state IN ('pending', 'claimed', 'sent', 'failed', 'cancelled')),
    -- One outbox row per message. A second one is a double send, which is the failure
    -- this whole table is shaped to prevent, so it is a constraint rather than a
    -- discipline.
    UNIQUE (message_id)
);

CREATE INDEX IF NOT EXISTS outbox_claimable
    ON outbox (tenant_id, state, not_before)
 STORING (thread_id, message_id, claimed_by, claimed_at);


-- ------------------------------------------------------- agent_manifest seeding
--
-- The fleet view reads this table. Until now it held one row — `forager` — while
-- `agents.REGISTRY` held five implementations, so the view could only ever have been
-- either a fixture or wrong.
--
-- These are UPSERTs so a rerun does not clobber an operator's `enabled` change: turning
-- an agent off is an UPDATE rather than a deploy, and a migration that undid that would
-- make the off switch untrustworthy. `updated_at` is left alone for the same reason.

UPSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters, writes,
                            batch_size, per_artist_cap, requires_human, enabled)
VALUES
    ('map_source', 'lead', 'pending', ARRAY['party'],
     ARRAY['musicbrainz', 'web_page'], ARRAY['source_manifest', 'suggestion'],
     5, 3, false, true),

    ('embed_document', 'lead', 'pending', ARRAY['party'],
     ARRAY['openai'], ARRAY['party_chunk'],
     5, 3, false, true),

    ('find_counterparties', 'lead', 'pending', ARRAY['party', 'recording'],
     ARRAY['web_page'], ARRAY['party', 'party_role', 'presence'],
     5, 3, false, true),

    ('profile_party', 'lead', 'pending', ARRAY['party'],
     ARRAY['web_page'], ARRAY['party_document', 'party_fact'],
     5, 3, false, true),

    ('embed_party', 'lead', 'pending', ARRAY['party'],
     ARRAY['openai'], ARRAY['party'],
     5, 3, false, true);

-- Declared and not implemented. They appear in the fleet view as `off`, and the view
-- cross-references `agents.REGISTRY` so an agent with a manifest and no code reads as
-- "declared" rather than as an agent that merely happens to be disabled. The difference
-- matters: one is a switch, the other is unwritten work.
UPSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters, writes,
                            batch_size, per_artist_cap, requires_human, enabled)
VALUES
    ('drafter', 'thread', 'approved', ARRAY['recording'],
     ARRAY['openai'], ARRAY['message', 'thread'],
     5, 3, true, false),

    ('sender', 'outbox', 'pending', ARRAY['recording'],
     ARRAY['email'], ARRAY['message', 'outbox'],
     5, 3, true, false),

    ('inbox', 'message', 'inbound', ARRAY['recording'],
     ARRAY['openai'], ARRAY['thread', 'message'],
     5, 3, false, false);
