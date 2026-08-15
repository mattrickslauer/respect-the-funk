"""The gates, the connection and the tenant — as dependencies, never as calls.

`routes.py` states the rule this file follows and the reason for it: the gate is a
`Depends` so that a new route is private *by the act of annotating its principal*,
because the failure mode of a `_require(...)` call at the top of each handler is the
one route where somebody forgets it. That argument does not weaken when the response
is JSON, so nothing here is a helper a handler has to remember to call.

Two annotations carry the whole auth model:

  * `Operator` — signed in. Every read.
  * `Writer` — signed in *and* `may_write`. Every action. It depends on the operator
    gate rather than repeating it, so a write route cannot accidentally get the weaker
    check; there is no way to be a `Writer` without having passed `require_operator`.

`tests/test_api_surface.py` walks every route on the router and asserts one of those
two is in its dependency tree. That test is the actual guarantee — this docstring is
only the reason for it.

## Where this deliberately differs from the console, and why that is not a weakening

**401, not 303.** `require_operator` in `routes.py` raises a 303 to `/` because an
unauthenticated *browser* should land on the page that explains what this is rather
than on a wall. An API client is not a browser: a 303 to an HTML landing page is
either followed — yielding a 200 full of markup, which reads as success — or reported
as a redirect the client cannot act on. Both are worse than being told, in one field,
that the credential was missing. The *gate* is identical; only what it says when it
closes is different, and the gate is the guarantee.

**A bearer token is accepted as well as the cookie.** Same secret, same
`hmac.compare_digest` in `auth.principal_from_cookie` — no second credential, no
second comparison, nothing new to get wrong. It is here because a client that is not
a same-origin browser has no way to present a cookie, and because an API nobody can
reach with `curl` is an API nobody can debug.

The honest cost, stated once here and again in `docs/reference/api-v1.md`: an httpOnly
cookie is unreadable by JavaScript and a bearer token has to be stored somewhere the
script can read it, which makes it reachable by XSS. For a same-origin React console
the cookie is the better choice and the bearer path should be left to scripts. The
server offers both and cannot enforce that judgement; the client author makes it.

**No CORS.** Not an oversight. Adding permissive CORS to an API that authenticates
with a cookie is how CSRF gets built by accident, and the correct allow-list depends
on where the console is actually served from — which nobody has decided yet. A client
served from the same origin needs nothing; a client served from elsewhere needs a
decision, not a default. See the note in `docs/reference/api-v1.md`.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import Cookie, Depends, Header

from rtf_platform import accounts, auth, db, repo, settings as settings_mod
from rtf_platform.api import errors

#: Read once at import, matching `routes.py`. A test that needs a different token
#: patches this attribute; there is no environment read at request time to race with.
SETTINGS = settings_mod.load()

_BEARER = "bearer "


def _token(cookie: str | None, authorization: str | None) -> str | None:
    """The credential, from either place it may arrive.

    The cookie wins when both are present. Not arbitrary: the cookie is the harder
    credential to steal, so when a caller offers two the safer one should be the one
    that decides — and a client that sends a stale header alongside a fresh cookie is
    a real situation, where preferring the header would sign somebody out for reasons
    they cannot see.
    """
    if cookie:
        return cookie
    if authorization and authorization[:len(_BEARER)].lower() == _BEARER:
        return authorization[len(_BEARER):].strip() or None
    return None


def _resolve_account(token: str) -> Any:
    """Turn a per-tenant token into its `account` row, for `auth.principal_from_cookie`.

    Opens the connection lazily — inside the function, only when the admin comparison has
    already failed — so an operator request costs no database work to authenticate and an
    unconfigured deployment does not raise while working out that somebody is anonymous.

    `SETTINGS.configured` being false returns `None` rather than raising, and that is the
    one judgement call here. A deployment with no `DATABASE_URL` cannot resolve a tenant
    token, so nobody holding one is authenticated — but the *operator* path never reaches
    this function, which means the console still admits the person who can go and set the
    variable. Raising instead would 500 every request on a fresh checkout, including the
    landing page, which resolves a principal like everything else. The refusal that
    matters is not lost: `connection()` below raises `NOT_CONFIGURED` the moment anything
    tries to read.
    """
    if not SETTINGS.configured:
        return None
    return accounts.account_for_token(connection(), token)


def current_principal(
    rtf_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> auth.Principal:
    """Who is asking. `auth.ANONYMOUS` when nobody is.

    Two credentials resolve here now, in this order: the shared operator token, compared
    in constant time against `PLATFORM_ADMIN_TOKEN` exactly as before, and — only if that
    fails — a per-tenant token looked up by hash in `account`. `auth.py` explains why the
    order is not negotiable and why the resolver is injected rather than imported.
    """
    return auth.principal_from_cookie(
        _token(rtf_session, authorization), SETTINGS.admin_token, _resolve_account)


Principal = Annotated[auth.Principal, Depends(current_principal)]


def require_operator(
    principal: Annotated[auth.Principal, Depends(current_principal)],
) -> auth.Principal:
    """The gate. Everything on this router is behind it; there are no public API routes.

    `/healthz` and the landing page stay where they are, on the console. An API with a
    public corner is an API where somebody has to remember which corner, and this one
    has no reason to have one.
    """
    if not principal.authenticated:
        raise errors.Refusal(
            401, errors.NOT_AUTHENTICATED,
            "Sign in first. Send the operator token as the rtf_session cookie, or as "
            "an Authorization: Bearer header.")
    return principal


Operator = Annotated[auth.Principal, Depends(require_operator)]


def require_writer(
    principal: Annotated[auth.Principal, Depends(require_operator)],
) -> auth.Principal:
    """The second gate, on top of the first rather than beside it.

    Depending on `require_operator` — instead of on `current_principal` and re-checking
    `authenticated` — is what makes it impossible to annotate a write route in a way
    that skips the read gate. There is no ordering for a caller to get wrong because
    there is no ordering to state.
    """
    if not principal.may_write:
        raise errors.Refusal(
            403, errors.READ_ONLY,
            "This session may read but not change anything.")
    return principal


Writer = Annotated[auth.Principal, Depends(require_writer)]


def connection() -> psycopg.Connection:
    """The shared connection, checked for life before it is handed over.

    The same shape as `routes._conn`, and the same reasoning: `db.connect` caches one
    connection across warm Lambda invocations, so the socket may have died while the
    container was frozen. `SELECT 1` costs a round trip and turns a stale socket into
    one reconnect instead of a 500 on whatever the handler was about to run.

    Not imported from `routes`: that module builds a Jinja environment at import and
    carries the console's HTTP contract, and an API package that imports the HTML layer
    to borrow five lines has coupled the two in the direction that will hurt later. The
    query logic is shared — that is what `research.py` is for — and this is not query
    logic.
    """
    if not SETTINGS.configured:
        raise errors.Refusal(
            503, errors.NOT_CONFIGURED,
            "DATABASE_URL is not set on this deployment, so there is nothing to read.")
    try:
        conn = db.connect(SETTINGS.database_url)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return conn
    except psycopg.Error:
        db.reset()                          # stale socket after an idle period
        return db.connect(SETTINGS.database_url)


Conn = Annotated[psycopg.Connection, Depends(connection)]


def current_tenant(
    conn: Conn,
    principal: Annotated[auth.Principal, Depends(current_principal)],
) -> str:
    """The tenant, or a refusal — never `None` passed onward.

    `routes.py` lets `_tenant_id` return `None` and renders an empty state, which is
    right for a page: a fresh deployment should be browsable before it has data. It is
    wrong for an API. A read that returned `{"rows": []}` for "there is no tenant" would
    be indistinguishable from "the tenant exists and has nothing", and a client would
    render an empty table for a deployment that has not been set up. Worse, an action
    would pass `None` into a query as a tenant id and quietly match nothing — a silent
    default in the exact place this project's standing rule forbids one.

    So the absence is a refusal with its own code, and the client can say what is
    actually true.

    **The principal's tenant wins when it has one, and this is the line that makes
    multi-tenancy real on this surface.** A per-tenant token resolves to a row in
    `account`, and that row's `tenant_id` is the only tenant that request may see; every
    read and every write on this router takes its scope from here, so there is no handler
    that could be written to see another tenant's rows without going around this
    dependency. `PLATFORM_TENANT_SLUG` remains the answer for the operator principal,
    which carries no tenant by construction — that is the superuser path, unchanged, and
    it is why `Principal.is_operator` is `tenant_id is None` rather than a flag.

    Note what is *not* re-checked here: whether the tenant row still exists. The account
    row has a foreign key to `tenant` with `ON DELETE CASCADE`, so a tenant that has been
    deleted takes its account with it and the token stops resolving one step earlier, at
    authentication. Verifying it again would be a second round trip to learn something
    the schema already guarantees.
    """
    if principal.tenant_id:
        return principal.tenant_id
    tenant = repo.get_tenant(conn, SETTINGS.tenant_slug)
    if tenant is None:
        raise errors.Refusal(
            409, errors.NO_TENANT,
            f"No tenant {SETTINGS.tenant_slug!r} exists yet. Nothing has been created "
            "on this deployment — save an artist and it will be.")
    return str(tenant["id"])


Tenant = Annotated[str, Depends(current_tenant)]


def not_found(what: str, ident: Any) -> errors.Refusal:
    """One sentence for every missing object, so the message cannot drift per handler.

    Names the kind and the id, and says nothing about whether it exists elsewhere.
    """
    return errors.Refusal(
        404, errors.NOT_FOUND,
        f"No {what} {str(ident)!r} in this tenant.")


def not_allowed(what: str, value: Any, allowed: Any) -> errors.Refusal:
    """A closed set, named. The point of the message is the list: a client author who
    guessed wrong should not have to read source to find the right answer."""
    return errors.Refusal(
        400, errors.NOT_ALLOWED_VALUE,
        f"{str(value)!r} is not a {what}. It must be one of: "
        f"{', '.join(sorted(str(a) for a in allowed))}.")
