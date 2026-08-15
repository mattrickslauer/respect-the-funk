"""Who is asking.

Two principals, where there were three:

  * **anonymous** — the landing page, the manual, the sign-in pages, and one public POST
    to ask for a demo call. Nothing else.
  * **tenant** — a per-tenant token from `account.token_hash`, resolved by hash lookup,
    carrying a real `tenant_id`, the tenant's `plan`, and its slug.

The token in the cookie is minted by `accounts.sign_in`, which runs only after
`otp.verify_code` has proved the person can read mail at the address. That is the whole
of authentication here: an emailed six-digit code, and a session token that is the
account's token.

## The operator is gone, and this file used to argue the opposite

Until 2026-08-15 there was a third principal — a shared `PLATFORM_ADMIN_TOKEN` in a
cookie, carrying `tenant_id = None`, checked *before* the database was touched. This file
defended that ordering in strong terms, and the argument was a good one:

> It is the one credential that must keep working under every failure of everything else:
> if the `account` table is missing, if migration 033 has not been applied, if the
> resolver raises, the operator still gets in — because the operator is who fixes it.
> Reversing the order would put the recovery path behind the thing being recovered.

That argument has not become wrong. It has been **overruled, knowingly**, and the cost is
real and worth naming rather than quietly dropping along with the code: sign-in now
depends on the database *and* on SES, and when either is down nobody enters the console,
including whoever would repair it. Recovery moves to `psql` and the AWS console, which is
where the credentials for both already live.

What was bought for it: one way in instead of three, no shared secret sitting in an
environment variable and in somebody's password manager, no principal that is exempt from
the tenant boundary, and — because every principal now carries a real `tenant_id` — the
deletion of the `PLATFORM_TENANT_SLUG` fallbacks in `routes._tenant_id` and
`api/deps.current_tenant`, which existed solely to answer "which tenant is the operator".
Those were the last places a request could be scoped to a tenant nobody signed in as.

## Why the tenant lookup is still injected rather than imported

`principal_from_cookie` takes a `resolve` callable and this module imports no database
driver, no settings and no `accounts` module.

  * **The dependency direction stays right.** `accounts` imports `repo` and `plans`, and
    `repo` is SQL. If `auth` imported `accounts`, the module that answers "who is this"
    would drag the database layer into every import of it.
  * **The comparison stays testable without a cluster.** The interesting behaviour here is
    resolution and refusal, and both are provable against a dict.

The cost is that a caller has to remember to pass the resolver. With the admin-token path
gone this is no longer a quiet footgun — a caller that forgets now authenticates *nobody*
rather than silently authenticating only operators, which fails loudly on the first
request instead of looking like a working console with no tenants in it. Both composition
roots pass it, and `tests/test_accounts.py` asserts that they do by calling them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

COOKIE_NAME = "rtf_session"


@dataclass(frozen=True)
class Principal:
    tenant_id: str | None
    subject: str
    authenticated: bool

    #: The tenant's tier key, or `""` for anonymous.
    plan: str = ""

    #: The tenant's slug, carried so the console header and the account block can name the
    #: label without a second query on every page render. `""` for anonymous, which never
    #: renders either of them.
    tenant_slug: str = ""

    @property
    def may_write(self) -> bool:
        return self.authenticated


ANONYMOUS = Principal(tenant_id=None, subject="anonymous", authenticated=False)


def principal_from_cookie(
    token: str | None,
    resolve: Callable[[str], dict[str, Any] | None] | None = None,
) -> Principal:
    """Resolve a session cookie into whoever holds it.

    An absent token is anonymous without a round trip. So is an absent resolver: a caller
    that did not supply one cannot authenticate anybody, and that is the honest answer
    rather than a partial one.

    `accounts.account_for_token` refuses an empty token before it hashes it, so an empty
    cookie cannot match a stored digest even though `hash_token("")` is a perfectly valid
    digest that a row could in principle carry.

    **A resolver that raises is not caught here.** A database failure during
    authentication must surface as a failure, not as "you are anonymous" — the latter
    renders as the console silently signing everybody out during an incident, which is the
    single most misleading thing this function could do.

    There is no constant-time comparison in this function any more, and its absence is
    correct rather than forgotten. The removed admin path compared the *secret itself*
    byte by byte in this process, where a timing leak walks an attacker through it. The
    tenant path compares two SHA-256 digests inside the database, on an index probe;
    `accounts.hash_token` sets out why a timing signal there is not invertible into a
    token.
    """
    if not token:
        return ANONYMOUS
    if resolve is None:
        return ANONYMOUS
    account = resolve(token)
    if account is None:
        return ANONYMOUS
    return Principal(
        tenant_id=str(account["tenant_id"]),
        # The email, because a console header and an account block saying who is signed in
        # are worth more than ones saying "tenant". It is not a secret to the person
        # holding the cookie.
        subject=str(account["email"]),
        authenticated=True,
        plan=str(account["plan"]),
        # `.get`, not `[...]`: a resolver is free to return an account row without the
        # tenant join — `accounts.account_for_token` includes it, a test fake need not.
        # This is the one display-only value tolerated as absent, and it is tolerated
        # because it is a label, not a permission.
        tenant_slug=str(account.get("tenant_slug") or ""),
    )
