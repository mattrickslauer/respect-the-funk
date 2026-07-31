"""The price book — one dated source of truth for what a shot costs.

This module exists because of a live incident, and the incident is worth recording
because the failure was silent in both directions.

A validation run against GMI burned real money while every manifest recorded
`cost_usd=None` and the console's ledger showed `$0.00`. Neither number was a bug in the
usual sense. Genblaze's README states it plainly:

    "Pricing is user-registered (the SDK ships zero hardcoded prices as of 0.3.0)."

So a provider never reports a price, and `step.cost_usd` stays `None` until *somebody*
registers a rate. Meanwhile `services/kits.ESTIMATE_CENTS` carried a second, unrelated
table of illustrative prices — 42¢ for a video — used only for the pre-flight budget
check. The two numbers had no relationship to each other and neither had a relationship
to the invoice: the estimate said a three-video kit cost $1.26, the ledger said $0.00,
and the actual charge was several dollars.

One table, registered into Genblaze itself, fixes all three at once:

* `Pipeline.estimated_cost()` becomes real, so the budget gate checks money.
* `step.cost_usd` becomes real, so the ledger records what was spent.
* The estimate and the ledger cannot drift, because they read the same rates.

**Price it, date it** (BUILD-SPEC §6). Every rate below carries the date it was checked
and where it came from. These are *list prices from provider documentation*, not measured
invoices; a rate nobody has reconciled against a bill is a guess with a decimal point, and
`UNVERIFIED` marks the ones still in that state.

**Failed steps still cost money.** The incident's sharpest finding: two `seedance-2`
video steps failed, produced no asset, and were billed anyway. Genblaze's documentation
says nothing about billing on failure, so the safe assumption is that a submitted step is
a charged step. `estimate_for` therefore prices what is *submitted*, not what succeeds,
and the ceiling in `generator_genblaze` is applied before the run rather than after.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from remixkit.domain.models import Modality

# When these rates were last checked against provider documentation.
PRICED_ON = "2026-07-31"

# Rates that have never been reconciled against an actual invoice. Named rather than
# silently blended in, because the whole point of this module is that an unverified
# number should not read like a measured one.
UNVERIFIED = frozenset({"seedance-2-0-260128", "seedance-1-0-pro-fast-251015"})

# Fallback used when a model has no registered rate. Deliberately not zero: an unknown
# price that prices as free is how the incident above stayed invisible. This is a
# per-call figure high enough that an unpriced model trips the budget gate rather than
# sliding under it.
UNKNOWN_CALL_USD = Decimal("2.00")


def per_second(rate_usd: float):
    """Price by output duration, falling back to the duration that was requested.

    Video is billed by length everywhere, and `PricingContext.output_duration_s` is only
    populated once assets carry duration metadata. A step that failed has no asset and
    therefore no measured duration — and it is still billable — so the requested
    `duration` param is the fallback rather than zero.
    """
    rate = Decimal(str(rate_usd))

    def strategy(ctx: Any) -> Decimal:
        seconds = getattr(ctx, "output_duration_s", None)
        if not seconds:
            params = dict(getattr(getattr(ctx, "step", None), "params", None) or {})
            seconds = params.get("duration") or 0
        return rate * Decimal(str(seconds or 0))

    return strategy


def per_call(rate_usd: float):
    """Flat price per emitted output, minimum one — a failed step still submitted work."""
    rate = Decimal(str(rate_usd))

    def strategy(ctx: Any) -> Decimal:
        return rate * Decimal(max(1, getattr(ctx, "output_count", 1) or 1))

    return strategy


# ---------------------------------------------------------------- the book
# model_id -> (strategy, note). The note is what a person reading the ledger needs in
# order to know how much to trust the number.
# Slugs are the *canonical* casing Genblaze uses internally. Registering the lowercase
# form still works via alias, but logs a `canonical-slug rewrite` line per model on
# every boot — four lines of noise in the prod log for no reason.
PRICE_BOOK: dict[str, tuple[Any, str]] = {
    # -- video (per second of output) ------------------------------------------------
    # Measured, not quoted: two failed 6s runs on 2026-07-31 moved the GMI balance by
    # more than $2 combined, which is the only hard datapoint here. Held deliberately
    # high until an invoice is read — under-pricing video is what caused the incident.
    "seedance-2-0-260128": (per_second(0.20), f"~$1.20/6s, measured {PRICED_ON}, UNVERIFIED"),
    "seedance-1-0-pro-fast-251015": (per_second(0.09), f"fast tier, list {PRICED_ON}, UNVERIFIED"),
    "Kling-Text2Video-V2.1-Master": (per_second(0.09), f"list {PRICED_ON}"),
    "Kling-Image2Video-V2.1-Master": (per_second(0.09), f"list {PRICED_ON}"),
    "Veo3-fast": (per_second(0.15), f"list {PRICED_ON}"),
    "Veo3": (per_second(0.40), f"list {PRICED_ON}"),
    "veo-3.0-generate-001": (per_second(0.40), f"Vertex list {PRICED_ON}"),
    "sora-2": (per_second(0.10), f"OpenAI list {PRICED_ON}"),
    "sora-2-pro": (per_second(0.30), f"OpenAI list {PRICED_ON}"),
    # -- image (per output) ----------------------------------------------------------
    "gpt-image-1": (per_call(0.19), f"OpenAI list, high quality 1024x1536, {PRICED_ON}"),
    "seedream-5.0-lite": (per_call(0.03), f"list {PRICED_ON}"),
    "imagen-4.0-generate-001": (per_call(0.04), f"Vertex list {PRICED_ON}"),
    # -- audio -----------------------------------------------------------------------
    "eleven_multilingual_v2": (per_call(0.02), f"est. per short TTS clip, {PRICED_ON}"),
    "gpt-4o-mini-tts": (per_call(0.02), f"OpenAI list, per short clip, {PRICED_ON}"),
    # -- mock ------------------------------------------------------------------------
    # Illustrative, so the ledger has something honest to show with no provider called.
    "mock-video": (per_call(0.42), "synthetic"),
    "mock-image": (per_call(0.03), "synthetic"),
    "mock-audio": (per_call(0.02), "synthetic"),
}


def priced_registry(provider_cls: Any):
    """A fork of a provider's default registry with our rates registered on it.

    Forked rather than mutated: `models_default()` is shared class state, and registering
    onto it would leak this app's price opinions into every other consumer in the process.
    """
    registry = provider_cls.models_default().fork()
    for model_id, (strategy, _note) in PRICE_BOOK.items():
        try:
            registry.register_pricing(model_id, strategy)
        except Exception:
            # A registry that refuses a model it does not know is fine — the model
            # simply is not served by this provider.
            continue
    return registry


def note_for(model_id: str) -> str:
    """What a reader of the ledger should know about this number."""
    entry = PRICE_BOOK.get(model_id)
    if entry is None:
        return f"no registered price — assumed ${UNKNOWN_CALL_USD}/call"
    return entry[1]


def is_verified(model_id: str) -> bool:
    return model_id in PRICE_BOOK and model_id not in UNVERIFIED


def estimate_cents(modality: Modality, model_id: str | None, seconds: float | None) -> int:
    """Pre-flight price for one shot, in cents, before anything is submitted.

    Used by the budget gate, which runs before a kit is queued and therefore cannot ask
    Genblaze — there is no `Step` yet. It reads the same book the registry does, so the
    number quoted to the operator and the number recorded on the run agree.
    """
    entry = PRICE_BOOK.get(model_id or "")
    if entry is None:
        return int(UNKNOWN_CALL_USD * 100)

    strategy = entry[0]

    class _Shot:
        params = {"duration": seconds or 0}

    class _Ctx:
        step = _Shot()
        output_count = 1
        output_duration_s = seconds if modality is Modality.VIDEO else None

    try:
        return int(round(float(strategy(_Ctx())) * 100))
    except Exception:
        return int(UNKNOWN_CALL_USD * 100)
