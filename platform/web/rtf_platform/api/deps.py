"""The gates, the connection and the tenant — as dependencies, never as calls.

`routes.py` states the rule this file follows and the reason for it: the gate is a
`Depends` so that a new route is private *by the act of annotating its principal*,
because the failure mode of a `_require(...)` call at the top of each handler is the
one route where somebody forgets it. That argument does not weaken when the response
is JSON, so nothing here is a helper a handler has to remember to call.

Two annotations carry the whole auth model:

  * `SignedIn` — signed in. Every read.
  * `Writer` — signed in *and* `may_write`. Every action. It depends on the read gate
    rather than repeating it, so a write route cannot accidentally get the weaker
    check; there is no way to be a `Writer` without having passed `require_signed_in`.

Both were called `Operator`/`require_operator` until 2026-08-15. Nothing about the check
changed in the rename — it only ever tested `authenticated` — but the operator *role* it
was named after no longer exists, and a gate named for a role that is gone is how the
next reader concludes there is a superuser somewhere.

`tests/test_api_surface.py` walks every route on the router and asserts one of those
two is in its dependency tree. That test is the actual guarantee — this docstring is
only the reason for it.

## Where this deliberately differs from the console, and why that is not a weakening

**401, not 303.** `require_signed_in` in `routes.py` raises a 303 to `/` because an
unauthenticated *browser* should land on the page that explains what this is rather
than on a wall. An API client is not a browser: a 303 to an HTML landing page is
either followed — yielding a 200 full of markup, which reads as success — or reported
as a redirect the client cannot act on. Both are worse than being told, in one field,
that the credential was missing. The *gate* is identical; only what it says when it
closes is different, and the gate is the guarantee.

**A bearer token is accepted as well as the cookie.** The same token, resolved by the
same hash lookup in `accounts.account_for_token` — no second credential, no second
comparison, nothing new to get wrong. It is here because a client that is not a
same-origin browser has no way to present a cookie, and because an API nobody can
reach with `curl` is an API nobody can debug.

This survived the move to email-OTP sign-in without a line changing, which is the
property `accounts.sign_in` was shaped to give: the session token *is* the account
token, so verifying an emailed code mints exactly the credential this header already
accepted.

The honest cost, stated once here and again in `docs/reference/api-v1.md`: an httpOnly
cookie is unreadable by JavaScript and a bearer token has to be stored somewhere the
script can read it, which makes it reachable by XSS. A browser client should use the
cookie and leave the bearer path to scripts. The server offers both and cannot enforce
that judgement; the client author makes it.

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

    Opens the connection lazily — inside the function, and only when a token was actually
    presented — so an anonymous request costs no database work to be recognised as
    anonymous.

    `SETTINGS.configured` being false returns `None` rather than raising, and that is the
    one judgement call here. A deployment with no `DATABASE_URL` cannot resolve a token, so
    nobody is authenticated on it. Raising instead would 500 every request on a fresh
    checkout, including the landing page, which resolves a principal like everything else.
    The refusal that matters is not lost: `connection()` below raises `NOT_CONFIGURED` the
    moment anything tries to read.

    **This used to be the second of two credential paths, and is now the only one.** The
    note that once stood here — that the operator path never reaches this function, so an
    unconfigured deployment still admits the person who can go and set the variable — is no
    longer true of anybody. `auth.py` records that trade in full.
    """
    if not SETTINGS.configured:
        return None
    return accounts.account_for_token(connection(), token)


def current_principal(
    rtf_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> auth.Principal:
    """Who is asking. `auth.ANONYMOUS` when nobody is.

    One credential resolves here: a per-tenant token, looked up by hash in `account`. It
    is the same token `otp.py` and `accounts.sign_in` mint at the end of an email
    verification, which is what makes the bearer path below work unchanged across that
    change — the session token and the account token are one object.

    The shared operator token used to be compared first, before any database work.
    `auth.py` records what was given up when it went.
    """
    return auth.principal_from_cookie(
        _token(rtf_session, authorization), _resolve_account)


Principal = Annotated[auth.Principal, Depends(current_principal)]


def require_signed_in(
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
            "Sign in first. Send your account token as the rtf_session cookie, or as "
            "an Authorization: Bearer header. A token is issued when you sign in at "
            "/signin with an emailed code.")
    return principal


SignedIn = Annotated[auth.Principal, Depends(require_signed_in)]


def require_writer(
    principal: Annotated[auth.Principal, Depends(require_signed_in)],
) -> auth.Principal:
    """The second gate, on top of the first rather than beside it.

    Depending on `require_signed_in` — instead of on `current_principal` and re-checking
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
    principal: Annotated[auth.Principal, Depends(current_principal)],
) -> str:
    """The tenant, which is always the caller's own.

    **The principal's tenant is the only tenant this router will ever scope to, and that
    is now true by construction rather than by precedence.** A token resolves to a row in
    `account`, and that row's `tenant_id` is the only tenant the request may see; every
    read and every write takes its scope from here, so there is no handler that could be
    written to see another tenant's rows without going around this dependency.

    This function used to take a `Conn` and fall back to `repo.get_tenant(conn,
    SETTINGS.tenant_slug)` — the operator path, for the one principal that carried no
    tenant by construction — and to raise `NO_TENANT` when even that was absent. All of it
    is gone with the operator, and the whole dependency is now one field access:

      * the fallback was the last way a request could be scoped to a tenant nobody signed
        in as, which is precisely the thing this dependency exists to prevent;
      * `NO_TENANT` cannot arise. It meant "authenticated, but we cannot work out whose
        rows these are", and there is no longer a way to be authenticated without an
        `account` row naming a tenant. A declared refusal code that no code path can
        produce is worse than no code: a client author writes a branch for it and can
        never test the branch.
      * the `Conn` parameter went with the query, so resolving the tenant no longer costs
        a connection. Reads that need one still ask for `Conn` themselves.

    The gate above still runs first, so an unauthenticated caller gets `NOT_AUTHENTICATED`
    from `require_signed_in` and never reaches this. `tenant_id` on an authenticated
    principal is non-empty by construction in `auth.principal_from_cookie`; the assertion
    states that rather than trusting a reader to go and check.

    Note what is *not* re-checked here: whether the tenant row still exists. The account
    row has a foreign key to `tenant` with `ON DELETE CASCADE`, so a tenant that has been
    deleted takes its account with it and the token stops resolving one step earlier, at
    authentication. Verifying it again would be a second round trip to learn something
    the schema already guarantees.
    """
    assert principal.tenant_id, (
        "an authenticated principal always carries a tenant; see "
        "auth.principal_from_cookie")
    return principal.tenant_id


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
