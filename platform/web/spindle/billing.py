"""Stripe: one place to buy a plan, one place to hear that somebody did.

## The design's best idea, stated first because everything else follows from it

**The webhook's only job is to write two values: the tenant's plan, and the tenant's
money ceiling. Nothing here enforces anything.**

The enforcement already exists and predates billing entirely. `spend.Gate` refuses a
metered call that would breach the ceiling, before the call, and fails closed on every
uncertainty. `outreach.open_thread` refuses a conversation past the plan's allowance.
Both were written for a single-tenant system with no payments in it, and neither knows
Stripe exists.

That separation is what makes this safe. The alternative — the shape most billing
integrations have — is for the payment layer to hold the entitlements and for the
application to ask it "may I?" at each call site. Then a webhook that never arrives, a
Stripe outage, a signature check that starts failing after a key rotation, or simply a
handler with a bug becomes an *authorisation* failure: the system either stops working
for people who have paid or keeps working for people who have not. Here a failed webhook
means one row is stale. The tenant stays on the tier the database last recorded, the gate
goes on enforcing that tier, and nothing is unbounded for a second. The worst case is
somebody paid and did not get their upgrade, which is a support conversation, not an
incident, and `tenant_budget.written_by` records that the last writer was `claim` rather
than `stripe_webhook` so it is diagnosable in one query.

It also means the money ceiling is enforceable when Stripe is not configured at all,
which is the state this deployment is in and the state the free tier must work in.

## Why the signature is verified here and not by the `stripe` library

`stripe.Webhook.construct_event` does exactly this check and would be the obvious call.
It is not used, for one reason that decided it: **the webhook must be verifiable without
the package being importable.** The scheme is HMAC-SHA256 over `"{timestamp}.{body}"`,
documented by Stripe and stable for years, and implementing it is twenty lines of
`hmac` — which means the endpoint that decides whether a stranger may grant themselves
a paid plan has no dependency on a vendored package surviving `build.sh`'s trimming, and
can be tested exhaustively offline. The rest of this module *does* use the library,
because creating a Checkout Session is an HTTP API with pagination, idempotency and
error taxonomy that would be silly to reimplement.

The risk of hand-rolling is that Stripe changes the scheme. It is mitigated by the
scheme being versioned in the header itself — `v1=` — and by this code refusing a header
with no `v1` element rather than accepting one it does not recognise.

## Test mode, throughout

`settings.stripe_mode` reads `test` or `live` off the key's own prefix, and
`checkout_session` **refuses a live key**. This code has never taken a payment and is not
built to; a live key present in this deployment would be a configuration mistake, and the
kind of configuration mistake whose first symptom is a real charge on a real card. The
refusal is the cheapest possible place to catch it. Remove that guard deliberately, in a
commit that says so, when somebody has decided to actually sell this.

No customer has been billed through this module. No revenue exists. The tiers in
`plans.py` are prices that have been written down, not prices anybody has paid.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import psycopg

from spindle import accounts, plans
from spindle.settings import Settings

#: How far a webhook's timestamp may be from now, in seconds. Stripe's own default, and
#: the reason it exists: without it a captured-and-replayed webhook stays valid forever,
#: because the signature over a fixed body never expires. Five minutes is enough slack for
#: a Lambda cold start plus ordinary clock drift and short enough that a replay has to be
#: nearly immediate.
SIGNATURE_TOLERANCE_SECONDS = 300

#: The only event this endpoint acts on. Everything else is acknowledged and ignored —
#: see `handle_event` for why acknowledging is right and why the list is this short.
CHECKOUT_COMPLETED = "checkout.session.completed"


class BillingRefused(RuntimeError):
    """Billing declined to do something, with a reason a program can branch on.

    Same shape as `accounts.ClaimRefused` and `spend.SpendRefused`: a sentence for the
    human, a stable `reason` for the caller. Not an `HTTPException`, because this module
    is called from the console today and could be called from the JSON API tomorrow, and
    a domain module that raises a framework's exception type has chosen one of them.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------- configuration

def require_configured(settings: Settings) -> None:
    """Raise unless every Stripe variable is set and the key is a test key.

    Two checks and they refuse for different reasons, so they are reported differently.
    A missing variable is a deployment nobody finished; a live key is a deployment
    somebody finished *too well*, and the message says so rather than reading as praise.
    """
    if not settings.stripe_configured:
        missing = ", ".join(settings.stripe_missing)
        plural = "is" if len(settings.stripe_missing) == 1 else "are"
        raise BillingRefused(
            f"Billing is not configured on this deployment: {missing} {plural} unset. "
            f"Nothing was charged and no checkout was created. The free tier does not "
            f"need any of these; POST /claim works without Stripe entirely.",
            reason="stripe_not_configured")
    if settings.stripe_mode != "test":
        raise BillingRefused(
            f"STRIPE_SECRET_KEY is a {settings.stripe_mode} key. This build refuses "
            f"anything but a test key, because it has never taken a real payment and a "
            f"live key here would make the first one an accident. See billing.py.",
            reason="stripe_not_test_mode")


def price_id_for(tier: plans.Tier, settings: Settings) -> str:
    """The configured Stripe price id for a tier, or a refusal naming what is absent.

    `plans.Tier.stripe_price_setting` holds the *name* of the settings attribute rather
    than the id, so this is a `getattr`. That indirection is what lets `plans.py` stay
    free of environment-specific values while still being the single place that knows
    which tiers are purchasable.
    """
    if not tier.purchasable:
        raise BillingRefused(
            f"The {tier.name} plan is not something checkout can sell. "
            + ("It is free — claim an account instead."
               if tier.price_usd_month == 0 else
               "It is priced against the work, so it starts with a conversation."),
            reason="tier_not_purchasable")
    price = getattr(settings, tier.stripe_price_setting, "")
    if not price:
        raise BillingRefused(
            f"No Stripe price is configured for the {tier.name} plan "
            f"({tier.stripe_price_setting.upper()} is unset), so there is nothing to "
            f"charge against.",
            reason="price_not_configured")
    return str(price)


def tier_for_key(key: str) -> plans.Tier:
    """A purchasable tier, or a refusal. Used on both sides of the round trip.

    Checked on the way *out* so a client cannot start a checkout for the free tier, and
    on the way *back* so a webhook carrying an unexpected plan in its metadata cannot
    grant one. The second check is the one that matters: the metadata is a string that
    travelled to Stripe and back, and trusting it because it arrived with a valid
    signature would be trusting Stripe to have preserved something this code put there —
    true in practice, and not a thing to build an entitlement on.
    """
    if key not in plans.KEYS:
        raise BillingRefused(
            f"{key!r} is not a plan this build defines. Known plans: "
            f"{', '.join(sorted(plans.KEYS))}.",
            reason="unknown_plan")
    tier = plans.tier(key)
    if not tier.purchasable:
        raise BillingRefused(
            f"The {tier.name} plan cannot be bought through checkout.",
            reason="tier_not_purchasable")
    return tier


# ------------------------------------------------------------------- checkout

def checkout_session(settings: Settings, *, tenant_id: str, email: str, plan_key: str,
                     success_url: str, cancel_url: str) -> dict[str, Any]:
    """Create a Stripe Checkout Session and return `{"id", "url", "mode", "test_mode"}`.

    Everything that can be refused is refused before the `stripe` import, so an
    unconfigured deployment never gets as far as needing the package. That ordering is
    the difference between "STRIPE_SECRET_KEY is unset" and "No module named 'stripe'",
    and only one of those tells the operator what to do.

    `client_reference_id` *and* `metadata.tenant_id` both carry the tenant. Not
    redundancy for its own sake: `client_reference_id` is the field Stripe's dashboard
    shows a human next to the payment, which is what somebody investigating a charge
    actually looks at, and `metadata` is the field that survives into every event shape
    the webhook might receive. `metadata.plan` carries the tier because a Checkout
    Session does not include its line items in the completed event, and expanding them
    would mean a second API call from inside the webhook — a network round trip on a path
    that must finish before Stripe's timeout or be retried.

    The API key is set on the module-level `stripe.api_key` because that is the library's
    interface. It is assigned on every call rather than once at import: a Lambda container
    is reused across invocations and a key read at import is a key that cannot be rotated
    without a deploy, which is the same argument `spend.Policy` makes about not caching a
    ceiling across a process.
    """
    require_configured(settings)
    tier = tier_for_key(plan_key)
    price = price_id_for(tier, settings)

    try:
        import stripe                       # noqa: PLC0415 — see the docstring
    except ImportError as exc:
        raise BillingRefused(
            "The `stripe` package is not installed in this environment, so no checkout "
            "can be created. It is pinned in platform/web/requirements.txt; run "
            "`pip install -r requirements.txt`.",
            reason="stripe_package_missing") from exc

    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=tenant_id,
        customer_email=email or None,
        metadata={"tenant_id": tenant_id, "plan": tier.key},
        # Stripe copies subscription metadata onto every future invoice, which is what
        # makes a renewal traceable back to a tenant without a lookup table here.
        subscription_data={"metadata": {"tenant_id": tenant_id, "plan": tier.key}},
    )
    return {
        "id": str(session["id"]),
        "url": str(session["url"]),
        "plan": tier.key,
        "mode": settings.stripe_mode,
        # Surfaced so any UI showing billing state can label it. It is never inferred
        # from the absence of something — see `settings.stripe_mode`.
        "test_mode": settings.stripe_test_mode,
    }


# -------------------------------------------------------------------- webhook

def _signature_parts(header: str) -> tuple[int, list[str]]:
    """`(timestamp, [v1 digests])` from a `Stripe-Signature` header.

    The header is `t=<unix>,v1=<hex>[,v1=<hex>...]`, and there can legitimately be
    several `v1` elements — during a secret rotation Stripe signs with both. Every
    element is collected and any one matching is enough, which is what makes rotation
    possible without a window where deliveries are refused.

    A malformed header raises rather than being partially understood. There is no shape
    of nonsense here that should end in a granted plan.
    """
    timestamp: int | None = None
    digests: list[str] = []
    for element in header.split(","):
        key, _, value = element.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise BillingRefused(
                    "The Stripe-Signature header's timestamp is not a number.",
                    reason="signature_malformed") from exc
        elif key == "v1":
            digests.append(value)
        # Any other scheme version is ignored rather than accepted. If Stripe introduces
        # v2, this code refuses (no v1 present) instead of guessing at a new algorithm —
        # a refusal is a page somebody reads; a guess is a plan somebody was granted.
    if timestamp is None or not digests:
        raise BillingRefused(
            "The Stripe-Signature header carries no timestamp and v1 signature pair, "
            "so the payload cannot be verified.",
            reason="signature_malformed")
    return timestamp, digests


def verify_signature(payload: bytes, header: str | None, secret: str, *,
                     now: float | None = None,
                     tolerance: int = SIGNATURE_TOLERANCE_SECONDS) -> None:
    """Raise `BillingRefused` unless `payload` was signed by `secret` and is recent.

    The payload must be the **raw request body**, byte for byte. Not the parsed JSON
    re-serialised: `json.dumps` of a parsed body differs from what was sent in key order,
    whitespace and unicode escaping, so a re-serialised body fails a signature that is
    perfectly valid. That is the classic way this check gets broken while looking right,
    and it is why the console handler reads `await request.body()` before parsing.

    `now` is injectable so the freshness window can be tested without sleeping or
    monkeypatching the clock.

    An empty `secret` refuses everything. That is the important line in this function: a
    deployment that has not configured `STRIPE_WEBHOOK_SECRET` must not have an endpoint
    that grants plans to anyone who posts to it, and the failure mode of "no secret means
    skip the check" is precisely that endpoint.
    """
    if not secret:
        raise BillingRefused(
            "STRIPE_WEBHOOK_SECRET is unset, so no webhook can be verified and none is "
            "accepted. An unverified billing webhook is an unauthenticated way to grant "
            "paid plans.",
            reason="webhook_secret_missing")
    if not header:
        raise BillingRefused(
            "No Stripe-Signature header, so this request is not from Stripe.",
            reason="signature_missing")

    timestamp, digests = _signature_parts(header)

    age = abs((time.time() if now is None else now) - timestamp)
    if age > tolerance:
        raise BillingRefused(
            f"That webhook's timestamp is {int(age)}s away from now, outside the "
            f"{tolerance}s tolerance. A signature over a fixed body never expires on its "
            f"own, so the timestamp is what makes a captured delivery unreplayable.",
            reason="signature_stale")

    signed = str(timestamp).encode("ascii") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    # `compare_digest` against each offered digest. The loop cannot short-circuit into a
    # timing oracle because every comparison is constant-time and the number of them is a
    # property of the header, not of the secret.
    if not any(hmac.compare_digest(expected, offered) for offered in digests):
        raise BillingRefused(
            "That webhook's signature does not match STRIPE_WEBHOOK_SECRET. Nothing was "
            "changed.",
            reason="signature_invalid")


def parse_event(payload: bytes) -> dict[str, Any]:
    """The event body as a dict, after the signature has been checked.

    Parsed here rather than at the handler so the ordering — verify, then parse — is
    visible in one module. Parsing first is harmless in itself and is how a handler ends
    up branching on unverified input by accident.
    """
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BillingRefused(
            "That webhook body is not JSON, which a signed Stripe delivery always is.",
            reason="body_malformed") from exc
    if not isinstance(event, dict):
        raise BillingRefused(
            "That webhook body is JSON but not an object.",
            reason="body_malformed")
    return event


def handle_event(conn: psycopg.Connection, event: dict[str, Any]) -> dict[str, Any]:
    """Act on a *verified* event. Writes the plan and the ceiling, and nothing else.

    Returns a small dict describing what was done, which the handler echoes back to
    Stripe. Stripe ignores the body; it is for the human reading a delivery in the
    dashboard, where "ignored: invoice.paid" and "applied: label" are the two things
    worth being able to tell apart at a glance.

    **Unknown event types are acknowledged, not refused.** A 4xx makes Stripe retry with
    backoff and eventually disable the endpoint, so refusing events we did not ask for
    would turn a dashboard misconfiguration into a dead webhook — and the events that
    matter would stop arriving because of the ones that never did. Acknowledging is not
    the same as pretending: the response says `ignored` and names the type.

    **Idempotency comes from the write being a set, not an increment.** Stripe delivers
    at least once and will replay this event on any timeout. Writing `plan = 'label'` and
    a fixed ceiling twice leaves exactly the same rows as writing it once, so no
    deduplication table is needed. This is the reason `set_plan` is an assignment rather
    than a state machine, and it is worth not breaking.
    """
    kind = str(event.get("type", ""))
    if kind != CHECKOUT_COMPLETED:
        return {"handled": False, "reason": "ignored", "type": kind}

    session = (event.get("data") or {}).get("object") or {}
    metadata = session.get("metadata") or {}
    tenant_id = str(metadata.get("tenant_id") or session.get("client_reference_id") or "")
    plan_key = str(metadata.get("plan") or "")

    if not tenant_id:
        raise BillingRefused(
            "That checkout session names no tenant, in metadata or in "
            "client_reference_id, so there is nobody to grant a plan to. Nothing was "
            "changed.",
            reason="no_tenant_in_event")

    tier = tier_for_key(plan_key)          # refuses free/catalogue and unknown keys

    # The whole job. Two values, one transaction, and the enforcement left exactly where
    # it already was — see this module's opening section.
    accounts.set_plan(
        conn, tenant_id, tier.key,
        written_by="stripe_webhook",
        stripe_customer_id=str(session.get("customer") or ""),
        stripe_subscription_id=str(session.get("subscription") or ""),
    )
    return {
        "handled": True,
        "type": kind,
        "tenant_id": tenant_id,
        "plan": tier.key,
        "daily_ceiling_usd": str(tier.daily_spend_cap_usd),
    }
