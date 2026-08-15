#!/usr/bin/env python3
"""Create the Stripe products and prices the tiers describe, and print their ids.

    cd platform/web
    STRIPE_SECRET_KEY=sk_test_... .venv/bin/python ../bin/stripe_setup.py

    # see what it would do without touching Stripe
    STRIPE_SECRET_KEY=sk_test_... .venv/bin/python ../bin/stripe_setup.py --dry-run

## Why this is a script and not a page in the Stripe dashboard

Because the amounts are already written down. `spindle.plans.TIERS` is what the
landing page renders and what `outreach.open_thread` enforces against, so a price
typed into a dashboard by hand is a fourth copy of a number that already has three
homes and no way to check them against each other. This reads the tiers and creates
exactly what they say.

It does not close that loop entirely, and the gap is worth naming: nothing verifies
afterwards that `STRIPE_PRICE_LABEL` still points at a price whose `unit_amount`
matches `plans.BY_KEY["label"].price_usd_month`. Somebody can still edit the price in
the dashboard. `billing.py` says the same thing about itself. A reconciliation check is
the obvious next thing and has not been written.

## Why it never sees your key beyond this process

It reads `STRIPE_SECRET_KEY` from the environment, uses it, and prints nothing derived
from it. It does not write `.env` — the price ids are printed for you to paste, because
a tool that edits a file full of your secrets without being asked is a surprise, and
surprises around credentials are the expensive kind.

## Why it refuses a live key

`billing.checkout_session` refuses one too, and for the same reason: this code has never
taken a payment. Creating live products is harmless in itself, but a live key in this
process is a live key in a shell's history and in whatever CI later runs it. Test mode
mirrors to live in the dashboard in one click when there is something real to charge for.

## Idempotent, via lookup keys

Each price gets a `lookup_key` derived from the tier and the amount. Re-running finds
the existing price and reports it rather than creating a second one — which matters,
because Stripe prices are immutable and a duplicate is not something you can tidy up
afterwards, only archive.

The amount is *in* the lookup key on purpose. Change a tier's price in `plans.py`, and
this creates a new price object rather than silently reusing one that charges the old
amount — which is the failure that would otherwise be invisible until a customer's card
was charged the wrong number.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))

from spindle import plans  # noqa: E402

#: Bumped by hand if a tier's *shape* changes in a way the amount does not capture —
#: monthly to annual, say. The amount is appended separately below.
LOOKUP_VERSION = "v1"


def lookup_key(tier: plans.Tier) -> str:
    """Stable per (tier, amount). See the module docstring on why the amount is in it."""
    return f"rtf_{tier.key}_monthly_{tier.price_usd_month}usd_{LOOKUP_VERSION}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created and exit without calling Stripe")
    args = ap.parse_args()

    purchasable = [t for t in plans.TIERS if t.purchasable]
    if not purchasable:
        print("No purchasable tiers in plans.TIERS. Nothing to create.", file=sys.stderr)
        return 1

    print("Tiers that need a Stripe price:\n")
    for t in purchasable:
        print(f"  {t.name:<12} ${t.price_usd_month}/month"
              f"   {t.open_conversations} conversations per {plans.PERIOD}")
        print(f"  {'':12} lookup_key={lookup_key(t)}")
        print(f"  {'':12} env var    {t.stripe_price_setting.upper()}")
        print()

    if args.dry_run:
        print("--dry-run: nothing was sent to Stripe.")
        return 0

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        print("STRIPE_SECRET_KEY is unset. Set it in the environment for this one "
              "command; this script does not read .env and does not write it.",
              file=sys.stderr)
        return 2

    # The same rule `billing.require_configured` applies, applied earlier so the refusal
    # costs a round trip rather than a live product.
    if key.startswith("sk_live_"):
        print("That is a live key and this script refuses live keys.\n\n"
              "This code has never taken a payment. Create the products in test mode,\n"
              "and copy them to live from the Stripe dashboard when there is something\n"
              "real to charge for.", file=sys.stderr)
        return 3
    if not key.startswith("sk_test_"):
        print("That does not look like a Stripe secret key — expected an sk_test_ "
              "prefix.", file=sys.stderr)
        return 3

    try:
        import stripe  # noqa: PLC0415 — imported here so --dry-run needs no dependency
    except ModuleNotFoundError:
        print("The `stripe` package is not installed in this environment.\n"
              "  cd platform/web && .venv/bin/pip install stripe", file=sys.stderr)
        return 4

    stripe.api_key = key
    lines: list[str] = []

    for t in purchasable:
        lk = lookup_key(t)

        # Existing first. A duplicate price cannot be deleted, only archived, so the
        # cost of getting this wrong is permanent clutter in somebody's dashboard.
        found = stripe.Price.list(lookup_keys=[lk], limit=1, expand=["data.product"])
        if found.data:
            price = found.data[0]
            print(f"{t.name}: reusing existing price {price.id}")
        else:
            product = stripe.Product.create(
                name=f"Spindle — {t.name}",
                description=t.summary,
                metadata={"tier": t.key, "created_by": "platform/bin/stripe_setup.py"},
            )
            price = stripe.Price.create(
                product=product.id,
                currency="usd",
                unit_amount=t.price_usd_month * 100,
                recurring={"interval": "month"},
                lookup_key=lk,
                transfer_lookup_key=True,
                metadata={"tier": t.key,
                          "open_conversations": str(t.open_conversations)},
            )
            print(f"{t.name}: created product {product.id} and price {price.id}")

        lines.append(f"{t.stripe_price_setting.upper()}={price.id}")

    print("\nAdd these to .env:\n")
    for line in lines:
        print(f"  {line}")
    print("\nStill needed, from the Stripe dashboard:\n")
    print("  STRIPE_WEBHOOK_SECRET=whsec_...   Developers -> Webhooks -> your endpoint")
    print("\nWebhook endpoint to register:\n")
    print("  URL     <your Lambda Function URL>/billing/webhook")
    print("  Events  checkout.session.completed")
    print("\nTo verify it end to end before deploying:\n")
    print("  stripe listen --forward-to localhost:8000/billing/webhook")
    print("  stripe trigger checkout.session.completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
