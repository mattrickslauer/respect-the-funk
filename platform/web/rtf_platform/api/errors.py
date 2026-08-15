"""Refusals, in a shape a program can branch on and a person can read.

The console's character is that it explains a refusal in plain words. `/approvals`
does not say `409 UniqueViolation`, it says *"Already queued — the first click prepared
the send, and the database refused a second copy."* That sentence is the product. A
JSON API that replaced it with an error code would be throwing away the most carefully
written text in the repository.

But a sentence alone is not enough for a client either. A React console that has to
branch on English is a console that breaks when the English improves, and it will
improve — every one of these messages has been rewritten at least once.

So every refusal carries both, and neither is optional:

    {"error": {"code": "already_queued",
               "message": "Already queued — the first click prepared the send, ..."}}

`code` is the contract. It is a closed set, declared below as constants so a handler
cannot invent one by typo and a test can enumerate them. It never changes meaning; a
new situation gets a new code rather than a widened old one.

`message` is for the human at the other end and is explicitly *not* a contract. It may
be rewritten in any commit. A client that pattern-matches on it has taken a dependency
the server never offered.

## What is deliberately absent

**No `details` blob, no field-level error map, no stack identifier.** Each of those is
a real thing to want and none of them is wanted *yet*: there is no client, so a shape
invented now would be invented from imagination and then be permanent. Adding a key to
this envelope later is backwards-compatible; guessing at one now and getting it wrong
is not.

**No pass-through of driver text.** `psycopg` names constraints, and a constraint name
tells an operator nothing while telling anybody else more about the schema than a form
post should reveal. Every refusal below is written here, by hand, in the terms of the
thing the caller was trying to do. `routes.py` reached the same conclusion for the same
reason — see the comment in `approvals_approve`.

**No 500 with an envelope.** An unhandled exception is not a refusal, and dressing one
as a refusal would tell a client that the server decided something when in fact it
broke. Bugs surface as bugs.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------- the code set
#
# Grouped by what the caller can do about it, which is the only grouping a client
# author actually needs: fix your credentials, fix your request, wait, or give up.

#: The caller is not signed in. A 401 — deliberately not the console's 303 to `/`. See
#: `deps.require_operator` for the argument.
NOT_AUTHENTICATED = "not_authenticated"

#: Signed in, but this principal may not write. Today `may_write` is `authenticated`,
#: so this is unreachable through the cookie path; it is checked anyway because the
#: gate is the guarantee and not the arithmetic that currently satisfies it.
READ_ONLY = "read_only"

#: `DATABASE_URL` is not set on the server. The deployment is misconfigured; nothing
#: the caller does will help.
NOT_CONFIGURED = "not_configured"

#: No tenant row exists yet. A fresh deployment before the first artist was saved.
#: Distinct from an empty result: "there is nowhere to look" and "we looked and found
#: nothing" are different answers and the console renders them differently too.
NO_TENANT = "no_tenant"

#: The thing named in the path does not exist in this tenant. Also what a caller gets
#: for an object belonging to somebody else, and the two are indistinguishable on
#: purpose — a distinct "exists but not yours" is an existence oracle.
NOT_FOUND = "not_found"

#: A parameter is outside the closed set the server accepts. The set is named in the
#: message, because a client author guessing at a vocabulary is the failure this
#: prevents.
NOT_ALLOWED_VALUE = "not_allowed_value"

#: `outreach.TransitionRefused` — a legal state reached out of order. The message names
#: both ends and what the current state may legally become.
TRANSITION_REFUSED = "transition_refused"

#: `UNIQUE (message_id)` on `outbox` refused a second row. A double approve. Not an
#: error: the first one worked, and this is the idempotency guarantee doing its job.
ALREADY_QUEUED = "already_queued"

#: `one_open_thread_per_counterparty` refused the insert. Somebody else is already
#: talking to this person, across every campaign. Also not an error.
THREAD_OCCUPIED = "thread_occupied"

#: `outreach.PlanLimitReached` — the tenant's plan allows no more open conversations
#: this month. Kept strictly separate from `THREAD_OCCUPIED` above, and the separation is
#: the point: both refuse the same request with a 409 and they mean opposite things.
#: `thread_occupied` says *this counterparty is taken, pick another*; `plan_limit_reached`
#: says *you have used the month up, no counterparty will work*. A client that collapsed
#: them would either offer an upgrade for a name clash or suggest a different name to
#: somebody who has hit their ceiling. The numbers are in the message, and `plans.py`
#: composes it so the console and this API cannot describe one limit two ways.
PLAN_LIMIT_REACHED = "plan_limit_reached"

#: `billing.BillingRefused` where the cause is configuration: Stripe variables unset, a
#: live key where only a test key is accepted, or a tier with no price id. The deployment
#: is not finished; nothing the caller does will help, and the message names the exact
#: environment variables — the same contract `settings.stripe_missing` and the Ask
#: screen's `mcp_missing` already keep. Never returned as a success with a fake session.
BILLING_NOT_CONFIGURED = "billing_not_configured"

#: A webhook whose `Stripe-Signature` is absent, malformed, stale or wrong. One code for
#: all four, and that is on purpose: the caller is either Stripe (in which case the
#: deployment's secret is wrong and the message says which) or somebody who is not
#: Stripe, and telling the second one *which part* of their forgery failed is help they
#: have not earned. The distinction is preserved where it is useful — `BillingRefused`
#: carries the specific `reason`, which is logged rather than returned.
BILLING_SIGNATURE_INVALID = "billing_signature_invalid"

#: `accounts.ClaimRefused` — a claim the server declined. The address is malformed, the
#: address already has an account, or a signup bound was reached. One code because every
#: one of them is "your request was understood and refused for a stated reason", and the
#: sentence is what differs; `accounts.ClaimRefused.reason` carries the machine-readable
#: half for anything that needs to branch.
CLAIM_REFUSED = "claim_refused"

#: There is no draft on this thread waiting for a decision — it was approved, rejected
#: or withdrawn between the read and the write.
NO_DRAFT_WAITING = "no_draft_waiting"

#: Nothing was queued, and the reason is one of two the caller can act on. See
#: `actions.analyse_recording`.
NOTHING_QUEUED = "nothing_queued"

#: There is no stored master to analyse.
NO_MASTER = "no_master"

#: `repo.SuggestionUnacceptable` — a payload `harvested.Presence.parse` rejects, or a
#: suggestion kind with no accept path.
SUGGESTION_UNACCEPTABLE = "suggestion_unacceptable"

#: `outreach.NotJustified` — this thread records no shortlist behind it. Nobody ever
#: recorded a reason.
NOT_JUSTIFIED = "not_justified"

#: `agents.HistoryExpired` — a reason *was* recorded and the cluster no longer retains
#: the memory to reconstruct it. Kept distinct from `NOT_JUSTIFIED`, and the distinction
#: is the whole point: "we never had one" and "we had one and it aged out" are different
#: answers to somebody asking why they were contacted.
HISTORY_EXPIRED = "history_expired"

#: The request did not parse: a missing body field, a value of the wrong type, a query
#: parameter that is not the boolean it claims. Raised by FastAPI's validation rather
#: than by a handler — see `handle_validation` for why it is translated rather than left
#: in the framework's own shape.
MALFORMED_REQUEST = "malformed_request"

#: Every code above, for the test that proves no handler raises one that is not here.
CODES: frozenset[str] = frozenset({
    NOT_AUTHENTICATED, READ_ONLY, NOT_CONFIGURED, NO_TENANT, NOT_FOUND,
    NOT_ALLOWED_VALUE, TRANSITION_REFUSED, ALREADY_QUEUED, THREAD_OCCUPIED,
    NO_DRAFT_WAITING, NOTHING_QUEUED, NO_MASTER, SUGGESTION_UNACCEPTABLE,
    NOT_JUSTIFIED, HISTORY_EXPIRED, MALFORMED_REQUEST,
    PLAN_LIMIT_REACHED, BILLING_NOT_CONFIGURED, BILLING_SIGNATURE_INVALID,
    CLAIM_REFUSED,
})


class Refusal(Exception):
    """The server declining to do something, with both halves of the reason.

    An `Exception` rather than an `HTTPException` subclass, and the difference matters.
    FastAPI renders `HTTPException` as `{"detail": ...}`; wrapping this envelope in
    that key would produce `{"detail": {"error": {...}}}`, and unwrapping it needs a
    handler anyway. Going straight to a handler keeps the wire shape the one written
    down at the top of this file.

    Raisable from a dependency as well as a handler, which is what lets the auth gate
    be a dependency at all — the whole argument `routes.py` gives for `require_operator`
    being `Depends` rather than a call at the top of each function.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        if code not in CODES:
            # Not a fallback to a generic code. An unknown code is a programming
            # mistake in this package, and a mistake that produced a plausible-looking
            # refusal would be invisible — which is exactly how a client ends up
            # branching on a code the server never meant to have.
            raise ValueError(f"{code!r} is not a declared refusal code; add it to CODES")
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    @property
    def body(self) -> dict[str, Any]:
        """The wire shape.

        `error.message` is canonical. The top-level `message` is the same string,
        mirrored — one line, derived here so the two cannot drift, and it exists
        because every HTTP client already has a habit for where the human-readable
        half lives. A client that reaches for a top-level field finds the sentence
        rather than an object it will render as `[object Object]`, and one that
        branches on `error.code` is unaffected.
        """
        return {
            "error": {"code": self.code, "message": self.message},
            "message": self.message,
        }


def handle(request: Request, exc: Exception) -> JSONResponse:
    """The handler, registered on the API application in `api/__init__.py`.

    Registered on the *sub*-application rather than on the console's, so the two
    contracts cannot leak into each other: an unauthenticated browser still gets the
    console's 303 to a page that explains what this is, and an unauthenticated API
    client gets a 401 it can branch on. One shared handler would have to discriminate
    by path prefix, which is a worse version of two applications.
    """
    assert isinstance(exc, Refusal)      # only ever registered for Refusal
    return JSONResponse(exc.body, status_code=exc.status_code)


def handle_validation(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own request-validation failures, translated into the envelope above.

    Without this there would be **two error shapes on one API**: everything a handler
    refuses would arrive as `{"error": {...}}`, and everything the framework rejected
    before reaching a handler — a missing body field, a string where a boolean was
    asked for — would arrive as `{"detail": [ ... ]}` with a 422. A client would need
    two parsers, and would find that out from the second one in production.

    The framework's own list is preserved under `error.fields` rather than discarded.
    It is the only part of the response that says *which* field was wrong, and this
    module's rule about not passing driver text through does not apply to it: it
    describes the caller's own request, not the schema.

    The status stays 422. It is the correct code and clients already know it.
    """
    from fastapi.exceptions import RequestValidationError

    assert isinstance(exc, RequestValidationError)
    fields = exc.errors()
    named = ", ".join(
        ".".join(str(part) for part in err.get("loc", ()) if part != "body")
        for err in fields) or "the request"
    refusal = Refusal(
        422, MALFORMED_REQUEST,
        f"The request could not be read: {named}. Check the shape against "
        f"docs/reference/api-v1.md.")
    body = refusal.body
    # `jsonable_encoder` because a validation error can carry a `ValueError` in `ctx`,
    # which `json.dumps` cannot serialise — the same class of failure `shapes.scalar`
    # raises on, handled here rather than allowed to turn a 422 into a 500.
    from fastapi.encoders import jsonable_encoder

    body["error"]["fields"] = jsonable_encoder(fields)
    return JSONResponse(body, status_code=422)
