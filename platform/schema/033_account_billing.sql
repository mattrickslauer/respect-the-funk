-- =============================================================================
-- 033_account_billing.sql — the tenant column finally has somebody behind it
-- =============================================================================
--
-- `tenant_id` has been the leading column of every index in this schema since
-- migration 001, and `001_tenant_artist.sql` explained why it went in before there was
-- anything to hang off it: partition-key-class columns are "near-impossible to
-- retrofit". That bet has been carried, unpaid, for thirty-two migrations. Multi-tenancy
-- has been real in the schema and false in the application the entire time —
-- `auth.principal_from_cookie` compared one shared `PLATFORM_ADMIN_TOKEN` and returned a
-- principal with `tenant_id = None`, and the tenant every request actually used came
-- from `PLATFORM_TENANT_SLUG`, a fixed environment variable. One deployment, one label.
--
-- This migration is where that stops. Two tables, and neither of them invents a new
-- boundary — they attach a credential and a plan to the boundary that was already there.
--
--
-- ## `account`: one row per tenant, not one row per user
--
-- The obvious shape is `account (id, tenant_id, email, token_hash, ...)` with many rows
-- per tenant, because that is what every SaaS eventually needs. It is deliberately not
-- what is built here, and the reason is `plan`.
--
-- A plan is a property of the *tenant* — of who is being billed — not of a credential.
-- With many accounts per tenant, `plan` either lives on this table and is duplicated
-- across every token the tenant holds (and then two tokens can disagree about what the
-- tenant is paying for, which is a bug that surfaces as a billing dispute), or it lives
-- somewhere else and this table is a join away from the thing every request needs. One
-- row per tenant makes the question unaskable: `PRIMARY KEY (tenant_id)` is the
-- statement that a tenant has exactly one account, one email and one token.
--
-- The honest cost, stated rather than discovered later: **there is no way to give two
-- people separate logins to the same label.** Rotating a token is `UPDATE account SET
-- token_hash = ...`, which signs the previous holder out. When a real label needs two
-- operators, the change is a second table — `account_token (tenant_id, token_hash,
-- label, created_at, revoked_at)` — with `plan` staying exactly where it is. That is an
-- additive migration, which is the property being bought here. `auth.py` already says
-- the same thing about OTP: a shared secret is the honest amount of auth for one
-- operator, and the shape to grow into is written down rather than pre-built.
--
--
-- ## Why a hash and not the token
--
-- The raw token is never stored. `account_by_token` is a lookup on
-- `sha256(presented token)`, so a dump of this table — a backup on somebody's laptop, a
-- support engineer with read access, a `SELECT *` pasted into a chat — hands over
-- nothing that can be presented as a credential.
--
-- SHA-256 rather than bcrypt/argon2/scrypt, and this is a real decision and not
-- laziness. A slow KDF exists to make *guessing* expensive, and guessing is only a
-- threat when the secret has low entropy — which is true of passwords a human chose and
-- false of `secrets.token_urlsafe(32)`, which is 256 bits from the OS CSPRNG. There is
-- no dictionary for that and no rainbow table, so the work factor would buy nothing
-- while costing a deliberately-slow hash on *every authenticated request*, in a Lambda
-- billed by the millisecond, on a cluster that scales to zero. If this table ever holds
-- a hash of something a human typed, the algorithm has to change with it, and the CHECK
-- below (64 hex characters) is what makes such a change visible rather than silent.
--
--
-- ## The one index whose leading column is not `tenant_id`
--
-- `repo.py`'s opening rule is that every query is scoped by `tenant_id`, without
-- exception, and every index in this schema leads with it. `account_by_token` cannot,
-- and it is worth being explicit about why rather than letting it look like an
-- oversight: the token is *how the tenant is discovered*. A request arrives carrying a
-- cookie and nothing else. Requiring `tenant_id` to look up the credential that yields
-- `tenant_id` is circular. This index is the one entry point into the tenant boundary,
-- and everything downstream of it is scoped normally.
--
-- It is UNIQUE, which is not decoration: a duplicate `token_hash` across two tenants
-- would make one cookie resolve to two tenants, and whichever row the scan returned
-- first would decide whose data a stranger reads. The uniqueness constraint means that
-- collision cannot be committed. (At 256 bits it will not happen by chance; it could
-- happen by a bad `INSERT ... SELECT` during some future migration, which is exactly the
-- kind of mistake constraints are for.)
--
-- `account_by_email` is UNIQUE for a different reason, argued in full in `accounts.py`:
-- one tenant per email address, because a second claim on a known address must refuse
-- rather than mint a token — there is no email verification in this build, so a
-- re-claim that handed out a working credential would be an account takeover reachable
-- by typing somebody else's address into a public form. Email is stored lowercased by
-- the writer so the index does the comparison people expect; a plain column index is
-- used rather than an expression index on `lower(email)` because normalising on write
-- is one rule in one place and needs no feature of the database to be true.
--
--
-- ## `tenant_budget`: the money ceiling, per tenant, where the gate can read it
--
-- `spend.py` is default-deny and reads its ceilings from the environment — one
-- `RTF_DAILY_CEILING_USD` for the whole deployment. That is correct for one tenant and
-- meaningless for many: a free account and a paying one cannot share a ceiling, and a
-- runaway on the free tier would spend the paying tenant's allowance.
--
-- So the ceiling becomes per-tenant, and it lives in a row rather than in a plan
-- constant, because **the row is what the gate reads and therefore the row is what is
-- true**. `plans.py` holds the number a tier is *sold with*; this table holds the number
-- that is *enforced*. They are seeded equal at claim time and the webhook rewrites this
-- one on a plan change. An operator may lower a specific tenant's ceiling without
-- editing Python and redeploying, which is the whole reason a kill switch belongs in
-- data. `spend.py` already made this argument once, about `FREE` versus
-- `source_manifest.cost_class`: when two places record one fact, the one that actually
-- stops the call wins.
--
-- **Absence of a row is not a permission.** A tenant with no row here falls back to
-- nothing at all — `spend.Gate` applies the environment policy alone, which is exactly
-- what it did before this migration and which itself defaults to $0 with paid calls off.
-- That is the pre-existing behaviour preserved for the operator tenant, not a hole: the
-- claim flow always writes a row, so every tenant that can be created through the public
-- endpoint is bounded from the instant it exists.
--
-- Note what is deliberately **not** here: `max_open_conversations`. The conversation
-- allowance is a property of the tier and lives in exactly one place, `plans.TIERS`, so
-- that the pricing page and the enforcement read the same number. Copying it into a row
-- would let a tenant's stored allowance drift from the tier they are being charged for,
-- and the first symptom would be somebody paying for 250 conversations and getting 50.
-- Money is different — see above — because money is the thing an operator has to be able
-- to clamp *now*, for one tenant, without a deploy.
--
-- Verified for syntax against CockroachDB CCL v26.2.5 conventions used throughout this
-- directory. Additive only: no table is dropped, no column is removed, nothing is
-- backfilled, and every existing row in every existing table is untouched. The operator
-- path (`PLATFORM_ADMIN_TOKEN` + `PLATFORM_TENANT_SLUG`) keeps working with no row in
-- either of these tables, which is what makes this migration safe to apply before the
-- code that reads it is deployed.
-- =============================================================================


CREATE TABLE IF NOT EXISTS account (
    -- The primary key, and the statement that a tenant has one account. See the header
    -- for the multi-user shape this grows into and why it is not built yet.
    tenant_id   UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,

    -- sha256 of the bearer token, lowercase hex. Never the token itself.
    token_hash  STRING NOT NULL,

    -- Lowercased by `accounts.normalise_email` before it gets here. Required, because an
    -- account nobody can be contacted about is an account nobody can be told their plan
    -- changed — and because it is the only handle a human has on a tenant they created
    -- through a form and then lost the cookie for.
    email       STRING NOT NULL,

    -- The tier key, and the join between this row and `plans.TIERS`. The CHECK is the
    -- closed set: a plan the code does not define must not be storable, because
    -- `plans.tier()` raises on an unknown key and a row that cannot be resolved would
    -- take the tenant's every request down rather than degrade. Adding a tier means
    -- adding it here and in `plans.py`, in one commit, on purpose.
    plan        STRING NOT NULL DEFAULT 'free',

    -- Stripe's identifiers, empty until a checkout completes. Empty is the normal state:
    -- the free tier never touches Stripe, and the claim flow is required to work with no
    -- Stripe configuration present at all.
    stripe_customer_id     STRING NOT NULL DEFAULT '',
    stripe_subscription_id STRING NOT NULL DEFAULT '',

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT account_plan_known
        CHECK (plan IN ('free', 'label', 'roster', 'catalogue')),
    -- 64 lowercase hex characters is what sha256 produces and nothing else does. This is
    -- the constraint that makes a change of hash algorithm — or an accidental write of a
    -- raw token into this column — fail at the database instead of being stored and
    -- believed.
    CONSTRAINT account_token_hash_is_sha256
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT account_email_present
        CHECK (email != '')
);

-- The entry point into the tenant boundary. UNIQUE so one cookie can never resolve to
-- two tenants; token-leading because the token is what discovers the tenant. See header.
CREATE UNIQUE INDEX IF NOT EXISTS account_by_token ON account (token_hash);

-- One tenant per email address. The claim endpoint refuses a second claim rather than
-- re-issuing a credential to whoever typed the address, because nothing here verifies
-- that the person typing it owns it.
CREATE UNIQUE INDEX IF NOT EXISTS account_by_email ON account (email);

-- What the signup rate limit counts. `accounts.claims_since` sums this window on every
-- claim, so it is an index and not a scan; the bound is global rather than per-IP, and
-- `accounts.py` argues why that is the honest choice behind a Function URL.
CREATE INDEX IF NOT EXISTS account_recent ON account (created_at DESC);


CREATE TABLE IF NOT EXISTS tenant_budget (
    tenant_id  UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,

    -- Dollars per rolling 24 hours, compared against the same `agent_run` sum
    -- `spend.spent_today` already computes. DECIMAL and not FLOAT for the reason every
    -- schema gives: money that has been through a binary float is money that no longer
    -- adds up, and this number is compared against an estimate before a paid call.
    --
    -- Default 0 is default-deny, matching `spend.Policy`: a row that exists and says
    -- nothing allows nothing. A tenant that should be unbounded has no row, not a huge
    -- number — see the header on why absence is the pre-existing behaviour rather than a
    -- permission.
    daily_ceiling_usd DECIMAL(12, 6) NOT NULL DEFAULT 0,

    -- The per-tenant kill switch. Distinct from a $0 ceiling, and the distinction is the
    -- point: $0 is "this tenant has no allowance today" and paused is "stop, somebody
    -- decided". They read identically to the caller and completely differently to whoever
    -- has to work out later why a tenant stopped spending. `party_budget.paused` carries
    -- the same flag one level down for the same reason.
    paused     BOOL NOT NULL DEFAULT false,

    -- Who last wrote this and when. 'claim', 'stripe_webhook' or an operator's name. A
    -- ceiling that changed with nobody's name against it is a ceiling nobody can argue
    -- with, and this row is the one a billing dispute would be settled from.
    written_by STRING NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- The access path for the plan meter. `outreach.open_thread` now counts the tenant's
-- conversations opened since the start of the month before it inserts, on every insert,
-- so this count is on the hot path of the one write the product charges for.
--
-- Without it the planner's only options are `thread_claimable (tenant_id, state,
-- next_action_at)` — which leads with the wrong second column and would scan every
-- thread the tenant has in any state — or the primary key. Neither is a range read of
-- one month. `tenant_id` leads, as everywhere else in this schema; `created_at DESC`
-- follows because the month being counted is always the most recent slice, so the rows
-- wanted are at the front of the index and a bounded scan stops early.
--
-- Deliberately not a partial index on "this month": a predicate containing `now()` is
-- not immutable and the index would be wrong the moment the month turned.
CREATE INDEX IF NOT EXISTS thread_opened_at ON thread (tenant_id, created_at DESC);
