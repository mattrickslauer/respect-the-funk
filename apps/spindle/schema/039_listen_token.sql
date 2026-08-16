-- =============================================================================
-- 039_listen_token.sql — a pitch can carry the record
-- =============================================================================
--
-- Until now a pitch offered the master and asked the curator to reply for it. This is
-- the pair of tables that lets it carry one instead: a per-recipient URL on this
-- deployment's own domain which, when clicked, resolves to a freshly-signed S3 URL for
-- the audio in `recording_asset`.
--
--
-- ## Why a token table and not a presigned URL in the body
--
-- The obvious implementation is `storage.presign_get` at draft time, pasted into the
-- email. It does not work, for two reasons that are only visible in production:
--
--   1. **A presigned URL made with temporary credentials dies with them.** The console
--      runs as a Lambda execution role, whose credentials are a session token lasting
--      hours. SigV4 signs that token into the URL, so `expires_in = 7 days` silently
--      yields a URL that stops working when the session does — and it works perfectly
--      on a laptop, where the credentials are long-lived. `GET_TTL` is 900 seconds
--      anyway, which is dead before most cold email is opened.
--
--   2. **It cannot be withdrawn.** Once mailed, a presigned URL is valid until it
--      expires, for anybody holding it, with no record that it was used.
--
-- A token indirects both problems away. The URL in the email is ours and permanent-ish;
-- the S3 signature is minted at click time and lives for minutes; and the row here is
-- the thing that can be revoked, expired and counted.
--
--
-- ## The token is a bearer credential in a forwardable medium
--
-- Stated plainly because the design accepts it: anyone who receives a forwarded pitch
-- can download the master. That is the consequence of putting a download button behind
-- an unauthenticated link, and it was chosen knowingly over making a curator sign in to
-- hear a record they did not ask for.
--
-- What the schema does about it is bound the damage rather than pretend otherwise:
-- `expires_at` ends the exposure, `state = 'revoked'` ends it early, and `listen_event`
-- makes a leak *visible* — one token fetched from eleven different address hashes is a
-- forwarded email, and it is legible as one.
--
--
-- ## `token_hash`, never the token
--
-- Same rule and the same constraint as `account.token_hash` in migration 033: lowercase
-- hex sha256, checked by the database. A read of this table must not yield anything that
-- can be replayed against the console, because the interesting rows here are exactly the
-- ones pointing at masters.
--
-- No salt and no KDF, deliberately, and for the reason `otp.py` gives about its own
-- codes: this is a 32-byte random value, not a human-chosen secret. There is no
-- dictionary to attack and stretching it would only slow the lookup.
--
--
-- ## Two states, and expiry is not one of them
--
-- `active` and `revoked`. **There is no `expired` state**, and that is migration 016's
-- argument applied again: a state nothing writes is a column that lies by omission.
-- Marking a token expired would need a sweep that runs, and no such sweep exists — so
-- every row would read `active` including the long-dead ones. Expiry is `expires_at`
-- compared against `now()` at read time, which is always true and needs no cron.
--
--
-- ## Composite foreign keys, for the reason 016 gave
--
-- `(tenant_id, message_id)`, `(tenant_id, recording_asset_id)` and
-- `(tenant_id, counterparty_id)` are single composite references, not pairs of
-- independent ones. Migration 016 introduced this because independent constraints let
-- tenant A's row attach to tenant B's recording and only a source lint would notice.
-- The stake is higher here than it was there: getting it wrong does not leak a row, it
-- mints a working public download link for another tenant's master.
--
-- Verified against CockroachDB CCL v26.2.5 on respect-the-funk-31317.
-- =============================================================================


-- The referenceable targets for the composite keys below. Each adds no uniqueness that
-- was not already true — every one of these columns is its table's primary key — and
-- exists solely so `(tenant_id, id)` is something a FOREIGN KEY can name. 016 created
-- `recording_by_tenant_and_id` for exactly this reason and said so.
CREATE UNIQUE INDEX IF NOT EXISTS message_by_tenant_and_id
    ON message (tenant_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS party_by_tenant_and_id
    ON party (tenant_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS recording_asset_by_tenant_and_id
    ON recording_asset (tenant_id, id);


CREATE TABLE IF NOT EXISTS listen_token (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    -- sha256 of the URL-safe token, lowercase hex. Never the token itself.
    token_hash  STRING NOT NULL,

    -- Which pitch carried this link. The join that answers "did the message we sent on
    -- Tuesday get opened", and the reason a token is per-message rather than per-asset:
    -- two pitches about the same record to two curators must be distinguishable, or the
    -- attribution is worthless.
    message_id  UUID NOT NULL,

    -- What it serves. A specific asset, not a recording — a recording may hold a master,
    -- an instrumental and stems, and the link points at one of them.
    recording_asset_id UUID NOT NULL,

    -- Who it was minted for. Denormalised from the message's thread deliberately: a
    -- revocation sweep driven by `contact_route` going `opted_out` needs to find every
    -- live token for a counterparty without walking threads to do it.
    counterparty_id UUID NOT NULL,

    state       STRING NOT NULL DEFAULT 'active',

    -- When the link stops working. NOT NULL: an immortal public download URL for a
    -- commercial master is not a thing to create by omission.
    expires_at  TIMESTAMPTZ NOT NULL,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,

    CONSTRAINT listen_token_state_known
        CHECK (state IN ('active', 'revoked')),
    CONSTRAINT listen_token_hash_is_sha256
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    -- A revoked row must say when, and an active one must not — the timestamp and the
    -- state are two spellings of one fact, and a row where they disagree is a row that
    -- cannot be audited after the incident that made somebody look.
    CONSTRAINT listen_token_revoked_at_matches_state
        CHECK ((state = 'revoked') = (revoked_at IS NOT NULL)),

    CONSTRAINT listen_token_message_fk
        FOREIGN KEY (tenant_id, message_id)
        REFERENCES message (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT listen_token_asset_fk
        FOREIGN KEY (tenant_id, recording_asset_id)
        REFERENCES recording_asset (tenant_id, id) ON DELETE CASCADE,
    CONSTRAINT listen_token_counterparty_fk
        FOREIGN KEY (tenant_id, counterparty_id)
        REFERENCES party (tenant_id, id) ON DELETE CASCADE
);

-- The entry point. A request carries a token, hashes it, and arrives here; UNIQUE so one
-- URL can never resolve to two rows. Token-leading because the token is what discovers
-- the tenant — `account_by_token` in migration 033 is the same shape for the same reason.
CREATE UNIQUE INDEX IF NOT EXISTS listen_token_by_hash
    ON listen_token (token_hash);

-- What the revocation sweep reads: every live token for a counterparty who has just
-- opted out or bounced.
CREATE INDEX IF NOT EXISTS listen_token_by_counterparty
    ON listen_token (tenant_id, counterparty_id, state);

-- What the console's thread view reads.
CREATE INDEX IF NOT EXISTS listen_token_by_message
    ON listen_token (tenant_id, message_id);

-- The target for `listen_event`'s composite key, and it has to exist before the table
-- that names it — a FOREIGN KEY cannot reference columns that are not already a unique
-- index, so declaration order is load-bearing here rather than stylistic.
CREATE UNIQUE INDEX IF NOT EXISTS listen_token_by_tenant_and_id
    ON listen_token (tenant_id, id);


-- Every resolution of a token, which is the whole attribution story. Append-only: there
-- is no update path and no delete path, because the value of this table is that it is a
-- record of what happened rather than a summary somebody maintained.
CREATE TABLE IF NOT EXISTS listen_event (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    listen_token_id UUID NOT NULL,

    -- `view` is the landing page; `audio` is the player asking for bytes; `download` is
    -- the button. Three and not one, because they mean genuinely different things about
    -- a curator's interest and collapsing them would lose the distinction that matters
    -- most — a download is a programmer taking the file somewhere.
    kind        STRING NOT NULL,

    -- sha256 of the client address, not the address. Enough to tell "the same person
    -- reloaded four times" from "this was forwarded to four people", which is the only
    -- question this column is asked. Storing the address itself would make this table
    -- personal data about people who never signed up for anything, in exchange for an
    -- answer nobody needs.
    ip_hash     STRING NOT NULL DEFAULT '',
    user_agent  STRING NOT NULL DEFAULT '',

    at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT listen_event_kind_known
        CHECK (kind IN ('view', 'audio', 'download')),
    CONSTRAINT listen_event_token_fk
        FOREIGN KEY (tenant_id, listen_token_id)
        REFERENCES listen_token (tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS listen_event_by_token
    ON listen_event (tenant_id, listen_token_id, at DESC);
