"""Per-tenant identity: minting a credential, resolving one, and bounding how many exist.

`auth.py` answers *who is asking*. This module answers the question underneath it —
**which tenant is that** — and it is the piece the schema has been waiting for since
migration 001 put `tenant_id` on every table. Until now the answer came from
`PLATFORM_TENANT_SLUG`, an environment variable, which is a correct answer for exactly
one deployment.

Three separable things live here, and they are together because they are the same
lifecycle:

  * **The credential.** Mint, hash, look up. `auth.py` stays free of SQL — it takes a
    resolver function — so the comparison logic remains testable without a cluster and
    this module owns the round trip.
  * **The claim.** Tenant, account, budget row, in one transaction, from one email.
  * **The bounds.** What stops `POST /claim` from being a way to fill a database.

## No fallbacks, in the two places it would be tempting

**A malformed email is refused, not cleaned up.** `normalise_email` lowercases and strips
whitespace — that is normalisation, and it is done in one place so the UNIQUE index sees
what a human means by "the same address". Anything it cannot normalise into a plausible
address raises. It does not drop the bad part and keep going, and it does not store what
it was given "so support can sort it out later": an account row whose email is `steve`
is an account nobody can be contacted about, and the row would outlive everybody's memory
of why it looked like that.

**A plan key that this build does not define raises** — `plans.tier` argues that one at
length. This module never coerces a stored plan into the free tier.

## What this module deliberately does not do

**No email verification, and therefore no re-issue.** Nothing here proves the person
typing an address owns it. That is a real limitation and it decides the shape of
`claim()`: a *second* claim on a known address is refused rather than served a fresh
token, because "type an address, receive a working credential for the tenant that
address already owns" is account takeover with a form in front of it. The refusal is the
whole mitigation. When there is an email sender that is allowed to mail humans — there
is not; `sender_wired` is `False` and the mail settings are unset — the correct fix is a
signed, expiring link, and it is a different function.

**No password, no session table, no logout-everywhere.** The cookie *is* the credential,
as it already is for the operator token. Revoking means rewriting `account.token_hash`,
which is one statement and signs out the one holder. `auth.py` has carried the same
argument since it was written: a shared secret is the honest amount of auth for a single
operator, and this is that argument with a tenant attached.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

import psycopg

from rtf_platform import plans, repo

#: Bytes of entropy in a minted token, before base64url encoding. 32 bytes is 256 bits,
#: which is the number that makes `hash_token`'s use of a fast hash correct — see
#: migration 033's header for why a slow KDF would buy nothing here. Lowering this is not
#: a tuning knob; it is a change to the threat model.
TOKEN_BYTES = 32

#: How many accounts may be created cluster-wide in one rolling hour, and how many may
#: exist at all.
#:
#: **What actually stops abuse, stated plainly because a reviewer will look for it:**
#: `POST /claim` is unauthenticated, on a Lambda Function URL, in front of a CockroachDB
#: Basic cluster that scales to zero and bills by request unit. Four things bound it, and
#: they are deliberately different *kinds* of bound so that defeating one does not defeat
#: the others:
#:
#:   1. **One tenant per email address** (`account_by_email`, UNIQUE). A script must
#:      supply a distinct address per account, which is cheap for an attacker and free
#:      for us to enforce — it is an index, not a check.
#:   2. **This rolling-hour cap.** Counted in the database, not in memory. An in-process
#:      token bucket is the textbook answer and it is worthless here: every Lambda
#:      container would hold its own bucket, and the platform makes as many containers as
#:      there are concurrent requests, so the effective limit would be the cap times the
#:      concurrency. The count is a `SELECT` against `account_recent` because that is the
#:      only place the containers share.
#:   3. **A hard ceiling on how many accounts exist at all.** The hourly cap bounds the
#:      rate; this bounds the total. Without it, twenty an hour is a hundred and seventy
#:      thousand a year, and the second bound is the one that means "this endpoint cannot
#:      quietly become the largest table in the database".
#:   4. **The free tier's own budget row.** Even an account that gets created cannot
#:      spend: `tenant_budget.daily_ceiling_usd` is seeded at twenty cents and
#:      `spend.Gate` is default-deny. This is the bound that matters most, because it is
#:      the only one that holds if the other three are wrong.
#:
#: **What is deliberately not done: per-IP limiting.** Behind a Function URL the client
#: address arrives in the request context and is trivially rotated — any cloud provider
#: rents a fresh one per request for a fraction of a cent — so a per-IP bucket would stop
#: an honest person retrying a typo and not stop anybody else. Worse, it reads like
#: protection in a code review. A global bound is weaker in one specific way and honest
#: about it: **one abuser can lock out legitimate signups for an hour.** On a
#: single-operator project with a $10 total spend budget that is the right way round, and
#: the refusal says so rather than pretending to be a generic error. A CAPTCHA or a
#: verified email is the real answer and neither is built.
MAX_CLAIMS_PER_HOUR = 20
MAX_ACCOUNTS = 500

#: The longest address RFC 5321 allows in a `MAIL FROM`. Enforced so a megabyte of text
#: cannot be posted into a STRING column by a form.
MAX_EMAIL_LENGTH = 254

#: Deliberately not an RFC 5322 grammar. That grammar accepts things no mail server will
#: deliver to and rejects things that work, and every attempt to write it as one regex is
#: a well-known joke. What is checked is the shape a human means by "an email address":
#: something, one at-sign, something with a dot in it, no whitespace anywhere. Anything
#: subtler than that is the mail server's job, and this build has no mail server.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ClaimRefused(RuntimeError):
    """A claim the server declined, carrying a machine-readable reason.

    `reason` is a short stable string the caller maps onto its own refusal vocabulary —
    the console renders a sentence, and if this ever reaches the JSON API it becomes a
    declared code in `api/errors.py`. Modelled on `spend.SpendRefused`, which carries
    `reason` alongside its message for exactly this: the sentence is for a person and may
    be rewritten in any commit, the reason is what a program is allowed to branch on.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# ------------------------------------------------------------------ the token

def mint_token() -> str:
    """A fresh bearer token. `secrets`, never `random`.

    `random` is a Mersenne Twister seeded from the clock; observing a handful of its
    outputs reveals its state and therefore every token it will ever produce. That is not
    a theoretical distinction for a value that is somebody's entire session.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """What goes in the database: lowercase hex sha256 of the token as UTF-8.

    Deterministic and unsalted, which is the point — the lookup is `WHERE token_hash =
    hash_token(presented)`, an index probe, and a per-row salt would turn that into a
    scan of every account comparing one at a time. The usual reason for a salt is to stop
    one precomputed table cracking many rows at once, and it does not apply: these are
    256-bit random values, so there is nothing to precompute against.

    No constant-time comparison, and its absence is deliberate rather than forgotten.
    `auth.py` uses `hmac.compare_digest` on the operator token because that path compares
    the *secret itself* byte by byte, where a timing leak walks an attacker through it.
    Here the database compares two hashes, and a timing signal about how far two SHA-256
    digests match tells an attacker nothing they can invert into a token — they would have
    to find a preimage to use it.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_email(raw: str) -> str:
    """One canonical form, or a refusal. Never a best guess.

    Lowercased because `Steve@Example.com` and `steve@example.com` are one mailbox to
    every provider anybody uses, and storing both would let one person hold two tenants
    while `account_by_email` looked like it was preventing exactly that. The local part is
    case-sensitive per the RFC; no deployed mail service treats it that way, and matching
    the RFC here would mean the uniqueness constraint enforces something other than what
    a human means by "the same address".

    Not normalised: dots and `+tags` in Gmail local parts. `s.t.e.v.e+a@gmail.com` and
    `steve@gmail.com` are the same mailbox at one provider and different mailboxes at
    others, so stripping them would silently merge two people's accounts at every host
    that is not Gmail. Refusing to guess is the whole rule here.
    """
    email = raw.strip().lower()
    if not email:
        raise ClaimRefused(
            "An email address is required. It is the only way to reach you about the "
            "account, and the only handle on a tenant if the cookie is lost.",
            reason="email_missing")
    if len(email) > MAX_EMAIL_LENGTH:
        raise ClaimRefused(
            f"That address is {len(email)} characters; the longest a mail server will "
            f"accept is {MAX_EMAIL_LENGTH}.",
            reason="email_too_long")
    if not _EMAIL.match(email):
        raise ClaimRefused(
            f"{raw.strip()!r} does not look like an email address — it needs one @ and "
            f"a dot in the domain. Nothing here guesses at what was meant.",
            reason="email_malformed")
    return email


# --------------------------------------------------------------- reading them

#: Selected everywhere, so a caller cannot come to depend on a column one query happens
#: to return. `token_hash` is *not* in it: nothing outside this module has any business
#: reading the hash, and leaving it out means it cannot reach a template or a JSON
#: response by somebody spreading a row into a context dict.
#:
#: The tenant's slug and name are joined in rather than fetched separately. Both are read
#: on the authentication path of every request — the console header names the label the
#: cookie belongs to — and a second round trip per request to learn a string that sits in
#: the row the foreign key already points at is a round trip billed by a cluster that
#: scales to zero.
_ACCOUNT_COLUMNS = ("a.tenant_id, a.email, a.plan, a.stripe_customer_id, "
                    "a.stripe_subscription_id, a.created_at, a.updated_at, "
                    "t.slug AS tenant_slug, t.name AS tenant_name")

_ACCOUNT_FROM = "FROM account a JOIN tenant t ON t.id = a.tenant_id"


def account_for_token(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """The account a bearer token belongs to, or `None`.

    The only unscoped read in the codebase, and `repo.py`'s opening rule — every query
    carries `tenant_id` — is not being broken so much as bottomed out: this is the query
    that *produces* the tenant id every other query then carries. Migration 033 argues
    the same point at the index.

    An empty or absent token returns `None` without a round trip. Not an optimisation:
    `hash_token("")` is a perfectly valid digest, and a row could in principle be written
    carrying it, at which point an empty cookie would authenticate somebody. The guard
    makes that unreachable. `auth.principal_from_cookie` refuses an unset admin token for
    the identical reason.
    """
    if not token:
        return None
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_ACCOUNT_COLUMNS} {_ACCOUNT_FROM} WHERE a.token_hash = %s",
            (hash_token(token),))
        return cur.fetchone()


def get_account(conn: psycopg.Connection, tenant_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_ACCOUNT_COLUMNS} {_ACCOUNT_FROM} WHERE a.tenant_id = %s",
            (tenant_id,))
        return cur.fetchone()


def account_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM account")
        return int((cur.fetchone() or {}).get("n", 0))


def claims_since(conn: psycopg.Connection, *, hours: int = 1) -> int:
    """How many accounts were created in the last `hours`. The rolling-hour bound.

    A rolling window rather than a fixed one because a fixed hourly bucket lets twice the
    cap through across a boundary — nineteen at 10:59 and twenty at 11:01 — and the
    endpoint being bounded is the whole point of the number.
    """
    with conn.cursor() as cur:
        # The window is passed as a `timedelta`, which psycopg adapts to an INTERVAL,
        # rather than formatted into the SQL. Formatting an integer into a query is safe
        # today and is the habit that writes the injection next year, and the repository
        # has no precedent for it: every statement here is parameterised.
        cur.execute(
            "SELECT count(*) AS n FROM account WHERE created_at > now() - %s",
            (timedelta(hours=hours),))
        return int((cur.fetchone() or {}).get("n", 0))


def plan_for_tenant(conn: psycopg.Connection, tenant_id: str) -> plans.Tier | None:
    """The tier a tenant is on, or `None` when the tenant is not plan-metered at all.

    **`None` is a real answer and not a missing one**, in the sense migration 025 uses
    the phrase. Two kinds of tenant have no `account` row and neither is an error:

      * the operator's own tenant, reached with `PLATFORM_ADMIN_TOKEN` and named by
        `PLATFORM_TENANT_SLUG`, which existed before this table and is not a customer;
      * every tenant created before migration 033, including the ones the test suite
        makes and drops.

    Returning `None` for those means the plan meter applies to plan-metered tenants and
    to nobody else, which is the behaviour that keeps the operator console and the
    existing suite working unchanged. The alternative — treating an absent row as the
    free tier — would silently cap the operator at five conversations a month, and the
    symptom would be the console refusing work for a reason nobody configured.

    A row whose `plan` names a tier this build does not define raises, via `plans.tier`.
    That is the loud direction and it is the right one: it means the database and the
    code disagree about what exists.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT plan FROM account WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return plans.tier(str(row["plan"]))


# ------------------------------------------------------------------ the meter

def conversations_opened_this_period(conn: psycopg.Connection, tenant_id: str) -> int:
    """Threads this tenant has *opened* since the start of the current calendar month.

    Opened, not currently open — `plans.py` argues why at length, and the short version is
    that counting open threads would refund the allowance on close and make the meter
    measure tidiness. `date_trunc` is computed by the database rather than in Python so
    that the boundary is the cluster's clock and not a Lambda container's, which matters
    because two containers disagreeing about which month it is would produce two different
    answers to a question the customer is billed on.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS n FROM thread
                WHERE tenant_id = %s
                  AND created_at >= date_trunc('month', now())""",
            (tenant_id,))
        return int((cur.fetchone() or {}).get("n", 0))


# -------------------------------------------------------------------- writing

def write_budget(conn: psycopg.Connection, tenant_id: str, *,
                 daily_ceiling_usd: Decimal, written_by: str,
                 paused: bool = False) -> None:
    """Seed or rewrite the tenant's money ceiling — the row `spend.Gate` reads.

    `written_by` is required and has no default. A ceiling that changed with nobody's name
    against it is a ceiling nobody can argue with later, and this row is what a billing
    dispute would be settled from.
    """
    if not written_by:
        raise ValueError("write_budget needs to know who is writing it")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tenant_budget (tenant_id, daily_ceiling_usd, paused,
                                          written_by, updated_at)
               VALUES (%s, %s, %s, %s, now())
          ON CONFLICT (tenant_id) DO UPDATE
                  SET daily_ceiling_usd = excluded.daily_ceiling_usd,
                      paused = excluded.paused,
                      written_by = excluded.written_by,
                      updated_at = now()""",
            (tenant_id, daily_ceiling_usd, paused, written_by))


def set_plan(conn: psycopg.Connection, tenant_id: str, plan_key: str, *,
             written_by: str, stripe_customer_id: str = "",
             stripe_subscription_id: str = "") -> plans.Tier:
    """Move a tenant onto a tier, and move its money ceiling with it. One transaction.

    The two writes are inseparable and that is the reason for the transaction block:
    `db.connect` runs autocommit, so adjacency guarantees nothing. A plan committed
    without its ceiling leaves a tenant who has paid for Roster spending against the free
    tier's twenty cents a day, and a ceiling committed without its plan leaves the
    opposite. Either is a bug that only shows up as a customer complaint hours later.

    Returns the resolved tier so the caller does not look it up a second time and get a
    different answer.
    """
    target = plans.tier(plan_key)           # raises on an unknown key. See plans.tier.
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE account
                      SET plan = %s,
                          stripe_customer_id = CASE WHEN %s != '' THEN %s
                                                    ELSE stripe_customer_id END,
                          stripe_subscription_id = CASE WHEN %s != '' THEN %s
                                                        ELSE stripe_subscription_id END,
                          updated_at = now()
                    WHERE tenant_id = %s""",
                (target.key,
                 stripe_customer_id, stripe_customer_id,
                 stripe_subscription_id, stripe_subscription_id,
                 tenant_id))
            if cur.rowcount == 0:
                # Not a no-op to be shrugged at. A webhook that names a tenant with no
                # account row means Stripe holds metadata this cluster cannot resolve —
                # a tenant deleted between checkout and callback, or a webhook from a
                # different deployment pointed at this endpoint. Either way somebody has
                # paid and nothing was granted, which must be loud.
                raise ClaimRefused(
                    f"No account exists for tenant {tenant_id!r}, so its plan cannot be "
                    f"set. Nothing was changed.",
                    reason="no_account")
        write_budget(conn, tenant_id,
                     daily_ceiling_usd=target.daily_spend_cap_usd,
                     written_by=written_by)
    return target


def claim(conn: psycopg.Connection, raw_email: str) -> tuple[dict[str, Any], str]:
    """Create a tenant, an account and a budget, and return `(account, raw_token)`.

    The raw token is returned **once**, here, and never again — it is not stored and
    cannot be recovered. The caller's only job with it is to put it in an httpOnly cookie.

    Order of operations, and why the checks come before the writes: the bounds are read
    first so that a refused claim costs one round trip and writes nothing, and the whole
    creation is one transaction so a crash between the tenant and its budget row cannot
    strand a tenant with no ceiling — which is the one partial state that would be
    genuinely dangerous, since an absent `tenant_budget` row means the environment policy
    alone applies. `repo.create_party` uses the same block for the same class of reason.

    **This function does not touch Stripe and must not learn to.** The free tier is the
    tier a judge with no card gets, and a signup path that called out to a payment
    provider would fail whenever that provider was unconfigured, unreachable, or having
    an incident. `billing.py` is a separate module for exactly this reason.
    """
    email = normalise_email(raw_email)

    # The bounds, checked in the order that gives the most useful refusal. "The address
    # is already taken" is actionable; "the service is full" is not, so the specific
    # check comes first even though it is the less likely one to fire.
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM account WHERE email = %s", (email,))
        if cur.fetchone() is not None:
            raise ClaimRefused(
                f"{email} already has an account. A second token cannot be issued for "
                f"it, because nothing here verifies that the person asking owns the "
                f"address — that would be a way to take over somebody else's tenant by "
                f"typing their email. Sign in with the token you were given.",
                reason="email_taken")

    if account_count(conn) >= MAX_ACCOUNTS:
        raise ClaimRefused(
            f"This deployment is capped at {MAX_ACCOUNTS} accounts and has reached it. "
            f"That is a deliberate ceiling on a public signup endpoint in front of a "
            f"cluster with a small budget, not an outage.",
            reason="accounts_full")

    recent = claims_since(conn, hours=1)
    if recent >= MAX_CLAIMS_PER_HOUR:
        raise ClaimRefused(
            f"{recent} accounts have been created in the last hour, which is the limit. "
            f"The bound is global rather than per-address, so somebody else's burst can "
            f"delay you; try again shortly.",
            reason="rate_limited")

    token = mint_token()
    slug = _tenant_slug(email)
    free = plans.FREE

    try:
        with conn.transaction():
            tenant = repo.ensure_tenant(conn, slug, _tenant_name(email))
            tenant_id = str(tenant["id"])
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO account (tenant_id, token_hash, email, plan)
                       VALUES (%s, %s, %s, %s)""",
                    (tenant_id, hash_token(token), email, free.key))
            write_budget(conn, tenant_id,
                         daily_ceiling_usd=free.daily_spend_cap_usd,
                         written_by="claim")
    except psycopg.errors.UniqueViolation as exc:
        # The `account_by_email` index, catching what the read above raced with. Two
        # requests can both find the address free and both proceed; the index is the
        # guard that actually holds and the check is only there to give the common case a
        # better message. Reported as the same refusal so the loser of the race is told
        # the true thing rather than shown a 500 — and the transaction has already rolled
        # back, so no tenant survives it.
        raise ClaimRefused(
            f"{email} already has an account — two claims for it arrived at once and "
            f"the database kept the first. Sign in with the token you were given.",
            reason="email_taken") from exc

    created = get_account(conn, tenant_id)
    assert created is not None            # the insert above guarantees it
    return created, token


def _tenant_slug(email: str) -> str:
    """A URL-safe key for the new tenant, unique by construction.

    The local part is used because a slug a human recognises is worth something in a URL
    and in the console's header, and six random hex characters are appended because two
    people at different domains share a local part constantly (`info@`, `hello@`,
    `label@`) and `repo.ensure_tenant` is `ON CONFLICT DO NOTHING` — a collision would
    silently hand the second claimant the first one's tenant. That is the one outcome
    this whole module exists to make impossible, so the suffix is load-bearing rather
    than cosmetic.

    A local part with no ASCII form slugifies to nothing and becomes `label-xxxxxx`. That
    is not a silent default for a missing configuration — it is a display name for a row
    that must have one, and the identity of the tenant is the UUID and the random suffix
    either way.

    The full address is deliberately not used: a tenant slug appears in the console
    header, and putting somebody's email in it publishes it to anyone looking over their
    shoulder.
    """
    base = repo.slugify(email.split("@", 1)[0])[:24] or "label"
    return f"{base}-{secrets.token_hex(3)}"


def _tenant_name(email: str) -> str:
    """What the console calls the label until somebody renames it.

    The local part, unmodified — not title-cased, because `RTF` would become `Rtf` and
    guessing at somebody's capitalisation is worse than leaving theirs alone.
    """
    return email.split("@", 1)[0] or email
