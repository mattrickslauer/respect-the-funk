-- =============================================================================
-- 034_otp_challenge.sql — proving the address, which 033 admitted it could not
-- =============================================================================
--
-- `033_account_billing.sql` shipped `account_by_email` as a UNIQUE index and wrote down
-- the reason it had to be one: there was no email verification, so a second claim on a
-- known address had to be *refused* rather than served a fresh token, because "type an
-- address, receive a working credential for the tenant that address already owns" is
-- account takeover with a form in front of it. `accounts.py` said the same thing at
-- greater length and named the fix — "a signed, expiring link", needing "a mail sender
-- this build does not have".
--
-- This is that fix, in its six-digit form. One table, and what it buys is that the
-- refusal 033 depended on is no longer load-bearing: an address that proves itself may
-- sign in as often as it likes, because proving itself is the whole control.
--
--
-- ## Why `email` is the primary key
--
-- One outstanding challenge per address. Requesting a second code replaces the first
-- rather than adding a row beside it, which means there is never a *set* of
-- simultaneously-valid codes for one address and never a question about which one a
-- verification is checking against. The alternative — a row per request, newest wins —
-- has to answer that question in application code on every read, and answers it wrongly
-- the first time somebody forgets an ORDER BY.
--
-- It also makes the resend floor enforceable with no extra state: `sent_at` on the row
-- being replaced is exactly "when did we last mail this address".
--
--
-- ## The second table whose leading column is not `tenant_id`
--
-- `repo.py`'s opening rule is that every query is scoped by `tenant_id`, without
-- exception, and 033 had to carve out `account_by_token` from it. This table is the same
-- carve-out one step earlier, and for a sharper version of the same reason.
--
-- A sign-in request arrives carrying an email address and nothing else: no cookie, no
-- tenant, and — on a first sign-in — no account anywhere in the database. Requiring
-- `tenant_id` to look up the challenge that *establishes* `tenant_id` is circular. 033
-- made this argument for the credential lookup; 034 makes it for the step that precedes
-- the credential existing at all. Everything downstream of authentication is scoped
-- normally, and this table is never joined to tenant data.
--
--
-- ## `code_hash` is salted with the email, and 033's fast-hash argument does NOT carry
--
-- This is the part most worth reading, because copying 033's reasoning here would be
-- wrong in a way that looks right.
--
-- 033 stores `sha256(token)` and defends the absence of a slow KDF at length: a work
-- factor exists to make *guessing* expensive, guessing only threatens a low-entropy
-- secret, and `secrets.token_urlsafe(32)` is 256 bits from the OS CSPRNG. There is no
-- dictionary for that. Correct, and entirely specific to a 256-bit secret.
--
-- A six-digit code **is** a dictionary — the whole of it, one million entries, computable
-- on a laptop in under a second. So:
--
--   * **The digest is `sha256(email || code)`, not `sha256(code)`.** Unsalted, this
--     column would be a rainbow table with 1,000,000 rows that anyone with read access
--     precomputes once and reuses against every row in it forever. Binding the digest to
--     the address makes a precomputed table useless against any address it was not built
--     for, which is the most a fast hash can buy here.
--   * **A slow KDF would still not be the control.** It would raise the cost of an
--     offline attack against a leaked dump, which is worth something; it would do nothing
--     about the online attack, which is the one that matters, because that attack is
--     someone POSTing codes at the verify endpoint. What bounds *that* is `attempts` and
--     `expires_at` below, and those are the two columns to get right.
--
-- The 64-hex CHECK is carried over from 033 for the reason 033 gives: it makes a change
-- of algorithm, or an accidental write of a raw code into this column, fail at the
-- database rather than be stored and believed.
--
--
-- ## What actually bounds abuse of an unauthenticated endpoint that sends mail
--
-- `POST /signin/code` takes an email address from anybody and causes a message to be
-- sent to it. That is two distinct exposures and they need different bounds:
--
--   1. **Hammering one address** — a resend loop aimed at somebody else's inbox. Bounded
--      by the 30-second floor, which `sent_at` on the replaced row enforces for free.
--   2. **Cycling distinct addresses** — which defeats a per-address floor completely,
--      because every request is a different address. This is the one that grows the
--      table without limit and, far worse, mails strangers who never asked. Bounded by a
--      cluster-wide rolling-hour cap counted over `sent_at` in `otp.py`, in the database
--      and not in a process, for the reason `accounts.MAX_CLAIMS_PER_HOUR` already gives
--      at length: an in-memory counter in a Lambda that scales to zero is reset by
--      cycling containers, which an attacker can cause at will.
--
-- `otp_challenge_recent` is the index that makes that count cheap. It is the reason the
-- index exists; a sweep of expired rows can use it too.
--
-- Deliberately NOT done, and named so it is not mistaken for an oversight: **per-IP
-- limiting.** Behind a Lambda Function URL it would be theatre, which is the same
-- conclusion `accounts.py` reached and for the same reason.
--
--
-- ## No sweep job, and why that is bounded rather than ignored
--
-- Nothing deletes expired rows on a schedule. A verified challenge is deleted at
-- verification and a re-request replaces the row in place, so what accumulates is one row
-- per address that asked for a code and never used it. The rolling-hour cap bounds how
-- fast that can grow, and the row is five short columns. When it is worth sweeping, it is
-- one DELETE against `otp_challenge_recent` and it does not need a migration.
-- =============================================================================

CREATE TABLE IF NOT EXISTS otp_challenge (
    -- Lowercased and stripped by `accounts.normalise_email` before it arrives, the same
    -- function `account.email` goes through, so the two tables agree on what "the same
    -- address" means. They are compared to each other on every sign-in.
    email       STRING PRIMARY KEY,

    -- sha256(email || code), lowercase hex. Never the code. See the header for why the
    -- salt is not optional here even though 033 needed none.
    code_hash   STRING NOT NULL,

    -- Ten minutes after issue, set by `otp.py` rather than defaulted here: the TTL is a
    -- policy the application states and tests, and a default in the schema would be a
    -- second place for it to be true.
    expires_at  TIMESTAMPTZ NOT NULL,

    -- Wrong guesses so far. In the database and not in the process — the header says why
    -- that distinction is the whole of the online-attack bound.
    attempts    INT NOT NULL DEFAULT 0,

    -- When the code was last mailed. Enforces the resend floor with no extra state.
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT otp_challenge_code_hash_is_sha256
        CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT otp_challenge_email_present
        CHECK (email != ''),
    -- A negative attempt count would mean the increment went backwards, and the row would
    -- then never exhaust. Cheap to forbid, and it forbids a bug rather than a typo.
    CONSTRAINT otp_challenge_attempts_not_negative
        CHECK (attempts >= 0)
);

-- Serves the rolling-hour cap that bounds address-cycling, which is the exposure the
-- header calls out as the one a per-address floor cannot touch. DESC because every read
-- of it asks for the recent end.
CREATE INDEX IF NOT EXISTS otp_challenge_recent ON otp_challenge (sent_at DESC);
