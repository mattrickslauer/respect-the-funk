-- 040 — mailbox: whose address a pitch goes out from, and how the answer finds its way back.
--
-- Until this migration the system could talk and could not listen. `010_outreach.sql`
-- built `message` with a `direction` column and an `inbound` half of its CHECK, `sender.py`
-- came along and drained the outbox, and the receiving half was left as a declared agent
-- in `agent_manifest` (`kind = 'inbox'`) with no code and no table behind it. Measured on
-- the production cluster on 2026-08-16: five outbound rows, **zero inbound rows, ever**.
-- A thread reached `awaiting_reply` and stopped there permanently, because nothing in the
-- product could observe a curator answering.
--
-- Two things were missing and only one of them was code.
--
--
-- ## 1. There was no mailbox that could receive
--
-- `PLATFORM_MAIL_REPLY_TO` was `spindle@mattrickslauer.com`, and that domain publishes no
-- MX record — verified against 8.8.8.8 on 2026-08-16, with `bounce.mattrickslauer.com`
-- correctly serving `v=spf1 include:amazonses.com ~all`, so the absence is specific rather
-- than DNS being broken. Under RFC 5321 §5.1 a sender with no MX falls back to the A
-- record, which here is a Lambda function URL that does not speak SMTP. So every reply
-- ever sent to that address failed to deliver. The inbound code did not exist, and it
-- would have had nothing to read if it had.
--
--
-- ## 2. The address was the platform's, and it should have been the tenant's
--
-- This is the part that makes the fix a table rather than an MX record. A pitch signed by
-- a label should come *from* that label, and a curator's reply should land in the mailbox
-- that label already reads. That makes deliverability rest on a domain the tenant owns and
-- has been sending from for years, rather than on a shared identity whose reputation every
-- tenant can damage for every other tenant.
--
-- So `mail_account` is the tenant's own mail identity: a Gmail account connected by OAuth,
-- or any provider at all over IMAP and SMTP.
--
--
-- ## The row means "connected", and its absence means the platform sends
--
-- There is no `provider = 'platform'` row and that is deliberate. A row in this table is a
-- statement that a tenant has connected a mailbox of their own, and the CHECKs below make
-- a row that says anything less impossible to write — `secret_arn` cannot be empty, and an
-- `imap` row cannot be missing its hosts. A tenant with no row sends through the platform's
-- SES identity, which is what the free tier does.
--
-- The resolution is still explicit rather than a fallback: `mail.identity_for` reads the
-- plan and the row together and refuses when they disagree — a tenant whose plan no longer
-- entitles them to a connected mailbox, or whose row is in `failed`, gets a refusal naming
-- the reason and not a silent downgrade onto the platform identity. Silently sending a
-- label's pitch from somebody else's address is precisely the failure this table exists to
-- prevent, so it must not be this table's error path.


-- --------------------------------------------------------------- mail_account

-- One row per tenant, keyed by tenant. `033_account_billing.sql` makes the argument for
-- `account` and it holds unchanged here: a tenant has one outreach identity, and two rows
-- disagreeing about which address is theirs is a bug with no correct answer. A label that
-- one day needs a second sending identity gets a second table, not a second row here —
-- because at that point the question "which one sends this pitch" has to be answered by
-- something, and today nothing would be asking it.
CREATE TABLE IF NOT EXISTS mail_account (
    tenant_id   UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,

    -- How we talk to it. `gmail` is the Gmail API under an OAuth refresh token; `imap`
    -- is IMAP for reading and SMTP for sending, which between them cover every provider
    -- that is not Google. The CHECK is the same device `010` used for thread states: a
    -- typo in an UPDATE becomes a failed transaction rather than an account that silently
    -- matches no poller and is never read again.
    provider    STRING NOT NULL,

    -- The address mail is sent from and replies are expected at. Not derived from the
    -- credential: a Google account's login address and the address it sends as are
    -- routinely different (delegated send-as, Workspace aliases), and guessing wrong
    -- means the From header disagrees with the DKIM signature.
    address     STRING NOT NULL,

    -- The pointer, never the credential. `spindle/vault.py`'s header has the full
    -- argument for why this column is an ARN and not a token: a refresh token has to be
    -- replayed at a provider, so it cannot be hashed the way `account.token_hash` is, so
    -- it does not live in this database at all.
    secret_arn  STRING NOT NULL,

    -- Non-secret transport configuration. Hosts and ports are not credentials and belong
    -- here rather than in the vault, where they would cost an API call to read on every
    -- poll for information that is public in the provider's own documentation.
    imap_host   STRING NOT NULL DEFAULT '',
    imap_port   INT8   NOT NULL DEFAULT 993,
    smtp_host   STRING NOT NULL DEFAULT '',
    smtp_port   INT8   NOT NULL DEFAULT 587,

    -- Where the last poll got to, in whatever form the provider counts in: a Gmail
    -- `historyId`, or `<uidvalidity>:<uid>` for IMAP. Opaque to everything but the adapter
    -- that wrote it, which is why it is one STRING and not a typed pair — the two
    -- providers do not agree on what a cursor is, and a schema that pretended they did
    -- would need a third form the day a third provider arrives.
    --
    -- Empty means "never polled", and the adapters treat that as *start from now* rather
    -- than as the beginning of time. Connecting a mailbox must not import a decade of
    -- history and file it against threads that did not exist.
    sync_cursor STRING NOT NULL DEFAULT '',

    -- `connected` is the working state. `failed` is set by a poll that could not
    -- authenticate and carries `last_error`; it stops the poller retrying a credential the
    -- provider has already rejected, which is how an account gets locked at the far end.
    -- `revoked` is terminal and set when the provider says the grant is gone.
    state       STRING NOT NULL DEFAULT 'connected',

    -- Why it is in `failed`, in words for the tenant rather than a provider error code.
    -- The person who has to fix this is the one who owns the mailbox, and "invalid_grant"
    -- tells them nothing.
    last_error  STRING NOT NULL DEFAULT '',

    last_polled_at TIMESTAMPTZ,
    connected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT mail_account_provider_known CHECK (provider IN ('gmail', 'imap')),
    CONSTRAINT mail_account_state_known CHECK (state IN ('connected', 'failed', 'revoked')),

    -- A row is a connected mailbox. Both of these enforce that at the database rather
    -- than in the connect handler, because the handler is one code path and this table is
    -- read by three (console, sender, poller) — and a half-written row would present to
    -- all three as a tenant who has connected a mailbox we cannot actually open.
    CONSTRAINT mail_account_has_credential CHECK (secret_arn != ''),
    CONSTRAINT mail_account_address_present CHECK (address != ''),
    CONSTRAINT mail_account_imap_has_hosts CHECK (
        provider != 'imap' OR (imap_host != '' AND smtp_host != '')),

    -- Ports are the one place a fat-fingered connect form can produce a row that passes
    -- every other check and then hangs a worker on a connection to nothing.
    CONSTRAINT mail_account_ports_sane CHECK (
        imap_port BETWEEN 1 AND 65535 AND smtp_port BETWEEN 1 AND 65535)
);

-- The poller's own query: everything connected, oldest poll first, so a mailbox that has
-- been waiting longest goes next and one noisy tenant cannot starve the rest.
CREATE INDEX IF NOT EXISTS mail_account_due
    ON mail_account (state, last_polled_at ASC NULLS FIRST)
 STORING (tenant_id, provider, address, secret_arn, sync_cursor);


-- ------------------------------------------------------- thread.reply_token

-- The address a reply comes back to, for the platform path.
--
-- When the platform's SES identity sends on a tenant's behalf, every tenant's mail
-- arrives in one mailbox, so the message has to carry its own routing. It does, as a
-- plus-addressed Reply-To: `spindle+<reply_token>@<domain>`, and the token is this column.
-- A reply's `To` header hands us the thread directly, which is the only correlation
-- strategy that survives a curator replying from a phone, forwarding to a colleague, or
-- using a client that strips `References`.
--
-- ## Why a random token and not the thread's own UUID
--
-- Three reasons, in order of weight. It is **revocable** — one UPDATE retires an address
-- that has leaked onto a mailing list, without touching the thread it belongs to. It
-- **leaks nothing**: a UUID in a Reply-To is a database key on public display, and the
-- next question is always whether anything else accepts it. And it is **cheap to verify**
-- — no signing key to hold, rotate or get wrong, because the token is looked up rather
-- than validated.
--
-- ## The one lint exemption in this schema, made deliberately
--
-- The lookup is `WHERE reply_token = %s` with no `tenant_id` predicate, because the token
-- is *how the tenant is discovered* — the same position `account.token_hash` occupies, and
-- `test_tenant_scoping.py` classifies `account` as unscoped-by-design for exactly this
-- reason. `thread` cannot follow it there: it is tenant-scoped in every other statement
-- and must stay linted. So this one statement is registered in that file's `ALLOWLIST`
-- with its justification, which is the mechanism built for this and which fails the suite
-- if the entry ever stops matching a live statement.
ALTER TABLE thread ADD COLUMN IF NOT EXISTS reply_token STRING NOT NULL DEFAULT '';

-- Partial, so the existing rows — all of which carry the '' default — do not collide with
-- each other. A token is minted when a thread first sends, not when it is created, so ''
-- is the normal state of a thread that has never been mailed and not a backfill to finish.
CREATE UNIQUE INDEX IF NOT EXISTS thread_by_reply_token
    ON thread (reply_token)
 WHERE reply_token != '';


-- ---------------------------------------------------- inbound_unmatched

-- Mail that arrived for a tenant we know, on a thread we could not identify.
--
-- This table exists because of `no-fallbacks` applied to the most expensive silent failure
-- in the product. A curator's reply is the thing the entire pipeline is built to produce;
-- discarding one because the correlation ladder came up empty would be losing the single
-- most valuable message this system ever receives, and losing it *quietly* — no row, no
-- error, no count. The ladder is allowed to fail. It is not allowed to fail invisibly.
--
-- ## Why `tenant_id` is NOT NULL, and what happens when the tenant is unknown too
--
-- The tempting shape is a nullable `tenant_id` covering both failures at once. It was
-- rejected: a nullable tenant forces this table into `UNSCOPED_BY_DESIGN`, which retires
-- the tenant lint for every statement against it — and these rows hold the full body of a
-- curator's email. A console query that lost its predicate would show one label another
-- label's private correspondence, which is a worse outcome than the one this table is
-- preventing.
--
-- So the two failures are kept apart. **Known tenant, unknown thread** — the common case,
-- a curator replying from a colleague's address or forwarding — lands here, tenant-scoped
-- and linted. **Unknown tenant** can only happen on the platform path, where a reply
-- arrived bearing a `reply_token` that matches nothing; there the raw message is already
-- durably in the S3 bucket the SES receipt rule wrote it to before any of our code ran, so
-- nothing is lost by declining to copy it into the database, and the copy is what would
-- have cost us the lint.
CREATE TABLE IF NOT EXISTS inbound_unmatched (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    -- The provider's own id for the message. UNIQUE per tenant so that re-polling a
    -- mailbox — which happens on every cursor reset, and after any adapter crash — does
    -- not accumulate duplicates of the same unmatched reply. The same discipline
    -- `message.idempotency_key` applies on the outbound side.
    provider_message_id STRING NOT NULL,

    from_address STRING NOT NULL DEFAULT '',
    to_address   STRING NOT NULL DEFAULT '',
    subject      STRING NOT NULL DEFAULT '',
    body         STRING NOT NULL DEFAULT '',

    -- Which rung of the ladder was reached and why it stopped, in words. This is read by a
    -- person deciding whether to attach the message to a thread by hand, and "no match"
    -- would not help them do it.
    reason       STRING NOT NULL,

    -- Set when an operator files it against a thread from the console, which is what takes
    -- it out of the unmatched view. Kept rather than deleted: what arrived and could not
    -- be placed is evidence about the correlation ladder, and a table that empties itself
    -- cannot tell you the ladder is getting worse.
    resolved_thread_id UUID REFERENCES thread(id) ON DELETE SET NULL,
    resolved_at        TIMESTAMPTZ,

    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT inbound_unmatched_has_reason CHECK (reason != ''),
    UNIQUE (tenant_id, provider_message_id)
);

-- The console's unmatched view: still unresolved, newest first.
CREATE INDEX IF NOT EXISTS inbound_unmatched_open
    ON inbound_unmatched (tenant_id, received_at DESC)
 WHERE resolved_at IS NULL;


-- ------------------------------------------------------------ agent_manifest

-- `010` declared the Inbox agent and left it unimplemented, with `enabled = false` and the
-- comment that an agent with a manifest and no code reads as "declared" rather than as one
-- that merely happens to be off. The code now exists. It stays `enabled = false` here
-- regardless, because turning it on is an operator's decision made per deployment through
-- the fleet view — shipping a migration that starts a worker reading people's mail is
-- exactly the kind of side effect `plans.py` argues against for sending.
--
-- `adapters` changes from `openai` to the three transports it actually speaks. The
-- classification the manifest originally anticipated is a separate agent's job: this one
-- records what arrived, and moves the thread off `awaiting_reply`. Reading intent out of
-- the text is unwritten work and the manifest should not claim otherwise.
UPSERT INTO agent_manifest (kind, work_table, claim_state, scope_kinds, adapters, writes,
                            batch_size, per_artist_cap, requires_human, enabled)
VALUES
    ('inbox', 'mail_account', 'connected', ARRAY['recording'],
     ARRAY['gmail', 'imap', 'ses'], ARRAY['message', 'thread', 'inbound_unmatched'],
     5, 3, false, false);
