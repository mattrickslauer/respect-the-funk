"""Who is asking, and what they may do.

The shape is lifted from `app/remixkit/auth/` — a Principal carrying `tenant_id`,
resolved on every request whether or not anyone is signed in. That app is frozen
during Backblaze judging, so this is a copy of the pattern rather than an import.

Three principals now, where there were two:

  * **anonymous** — gets the landing page, the sign-in form, the claim form and one
    public POST to ask for a demo call. Nothing else. An earlier build let anonymous
    readers browse the whole console so a hackathon judge would land on the product
    rather than a login box; that was reversed on purpose — the roster, the counterparty
    index and the campaign state are the label's own information, and judges are handed
    a token.
  * **operator** — the shared `PLATFORM_ADMIN_TOKEN` in an httpOnly cookie. Unchanged in
    every respect, including `tenant_id = None`, which is what makes the console fall
    back to `SETTINGS.tenant_slug` exactly as it always has.
  * **tenant** — a per-tenant token from `account.token_hash`, resolved by hash lookup.
    Carries a real `tenant_id` and the tenant's `plan`.

The token is not OTP, and is not pretending to be. `app/remixkit/auth/otp.py` is
61 lines and is where this goes when there is more than one operator; until then a
shared secret is the honest amount of auth for a single-operator console.


## Why the tenant lookup is injected rather than imported

`principal_from_cookie` takes an optional `resolve` callable and this module imports no
database driver, no settings and no `accounts` module. Three reasons, in the order they
mattered:

  * **The existing signature keeps working.** Every current caller passes two positional
    arguments and gets exactly today's behaviour: admin token or anonymous, no round
    trip, no import of anything. `routes.signin` uses that to validate a typed token
    without a connection; the tests construct principals directly. Adding a third
    parameter with a `None` default means none of that had to change to make
    multi-tenancy real, which is the property that let this land without touching
    forty-odd call sites.
  * **The comparison stays testable without a cluster.** The interesting behaviour here
    is precedence and refusal, and both are provable against a dict.
  * **The dependency direction stays right.** `accounts` imports `repo` and `plans`, and
    `repo` is SQL. If `auth` imported `accounts`, the module that answers "who is this"
    would drag the database layer into every import of it — including `routes.signin`,
    which deliberately answers the question offline.

The cost is that a caller has to *remember* to pass the resolver, and a caller that
forgets gets a working authentication path that silently never authenticates a tenant.
That is a genuine footgun, and it is mitigated in the only way that actually works: both
composition roots — `routes.current_principal` and `api/deps.current_principal` — pass
it, and `tests/test_accounts.py` asserts that they do, by calling them rather than by
reading them.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Callable

COOKIE_NAME = "rtf_session"

#: The `plan` an operator principal carries. Not a tier in `plans.TIERS` and deliberately
#: not `"catalogue"` or any other real key: the operator is not a customer, has no
#: subscription and is not metered by a plan, and giving them a plan key that resolves
#: would let `plans.tier(principal.plan)` succeed and quietly enforce a customer's
#: conversation limit on the operator console. Anything reading this value must treat it
#: as "not a tenant plan" — `outreach.open_thread` does, and says why.
OPERATOR_PLAN = "operator"


@dataclass(frozen=True)
class Principal:
    tenant_id: str | None
    subject: str
    authenticated: bool

    #: The tenant's tier key, or `OPERATOR_PLAN`, or `""` for anonymous. Defaulted so
    #: every existing `Principal(...)` construction — in `api/deps`, in the tests — keeps
    #: compiling and keeps meaning what it meant.
    plan: str = ""

    #: The tenant's slug, carried so the console header can name the label without a
    #: second query on every page render. `""` for the operator and for anonymous, and
    #: `routes._ctx` reads it as "fall back to `SETTINGS.tenant_slug`" — which is the
    #: correct answer for both of them and the same fallback `_tenant_id` makes, for the
    #: same reason.
    tenant_slug: str = ""

    @property
    def may_write(self) -> bool:
        return self.authenticated

    @property
    def is_operator(self) -> bool:
        """The superuser path: the shared admin token, scoped to no tenant.

        Derived from `tenant_id is None` rather than stored as a flag, because a stored
        flag is a second statement of the same fact and the two can be constructed apart.
        An authenticated principal either resolved to a row in `account` — in which case
        it has that row's tenant — or matched the admin token, in which case it has none.
        There is no third way to become authenticated, and the tests hold that.
        """
        return self.authenticated and self.tenant_id is None


ANONYMOUS = Principal(tenant_id=None, subject="anonymous", authenticated=False)


def principal_from_cookie(
    token: str | None,
    admin_token: str,
    resolve: Callable[[str], dict[str, Any] | None] | None = None,
) -> Principal:
    """Constant-time compare so the cookie cannot be brute-forced a byte at a time.

    An unset `admin_token` never authenticates anybody — otherwise a deployment
    that forgot to set it would accept an empty cookie as valid. The same guard applies
    to the tenant path by construction: `accounts.account_for_token` refuses an empty
    token before it hashes it, so an unset cookie cannot match a stored digest either.

    **The operator token is checked first, and the order is deliberate.** It is the one
    credential that must keep working under every failure of everything else: if the
    `account` table is missing, if migration 033 has not been applied, if the resolver
    raises, the operator still gets in — because the operator is who fixes it. Reversing
    the order would put the recovery path behind the thing being recovered.

    `resolve` is called only when the admin comparison fails, so the ordinary operator
    request costs no round trip. It returns an `account` row or `None`; anything else is
    a programming error in the caller and is left to raise rather than be interpreted.
    A resolver that raises is *not* caught here: a database failure during authentication
    must surface as a failure, not as "you are anonymous", which would render as the
    console silently signing everybody out during an incident.
    """
    if not token:
        return ANONYMOUS
    if admin_token and hmac.compare_digest(token, admin_token):
        # Unchanged from the day this file was written, `tenant_id = None` included. The
        # console reads that as "use SETTINGS.tenant_slug", which is what keeps the
        # existing deployment, the existing console and the existing tests identical.
        return Principal(tenant_id=None, subject="operator", authenticated=True,
                         plan=OPERATOR_PLAN)
    if resolve is None:
        return ANONYMOUS
    account = resolve(token)
    if account is None:
        return ANONYMOUS
    return Principal(
        tenant_id=str(account["tenant_id"]),
        # The email, because a console header saying who is signed in is worth more than
        # one saying "tenant". It is not a secret to the person holding the cookie.
        subject=str(account["email"]),
        authenticated=True,
        plan=str(account["plan"]),
        # `.get`, not `[...]`: a resolver is free to return an account row without the
        # tenant join — `accounts.account_for_token` includes it, a test fake need not —
        # and an empty slug degrades to the deployment default rather than raising during
        # authentication. This is the one place a missing value is tolerated, and it is
        # tolerated because the value is a display label, not a permission.
        tenant_slug=str(account.get("tenant_slug") or ""),
    )
