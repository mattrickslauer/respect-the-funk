#!/usr/bin/env python3
"""Create the Stripe products and prices the tiers describe, and print their ids.

    cd apps/spindle/web
    STRIPE_SECRET_KEY=sk_test_... .venv/bin/python ../bin/stripe_setup.py

    # see what it would do without touching Stripe
    .venv/bin/python ../bin/stripe_setup.py --dry-run

    # the live ledger, which needs saying out loud twice — see "Why --live exists"
    STRIPE_SECRET_KEY=sk_live_... .venv/bin/python ../bin/stripe_setup.py --live

    # register this deployment's webhook endpoint and print its signing secret
    STRIPE_SECRET_KEY=sk_test_... .venv/bin/python ../bin/stripe_setup.py \\
        --webhook-url https://example.lambda-url.us-east-1.on.aws/billing/webhook

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

The one exception is `--webhook-url`, which prints a `whsec_` secret. That is deliberate
and unavoidable: Stripe returns an endpoint's signing secret **only in the response that
creates it**, and there is no later API call that will tell you what it was. A script
that created the endpoint and swallowed the secret would leave you with an endpoint you
could only fix by deleting and recreating it.

## Why --live exists rather than the old refusal

This script refused `sk_live_` outright until 2026-08-15, when `billing.py` stopped
refusing live keys too. Creating a live product is in itself harmless — an unused product
in a dashboard costs nothing and charges nobody — so the refusal was never really about
the products. It was about the key.

The flag is what replaced it, and it guards a narrower thing on purpose: not "you may not
do this", but "you may not do this *by accident*". A live key exported in a shell for some
other reason should not silently create live products because you ran the same command you
always run. Naming `--live` next to an `sk_live_` key states the intent twice, and the two
have to agree — a test key with `--live`, or a live key without it, both refuse.

What that does not protect you from, said plainly: a live key in this process is a live
key in that shell's history and in whatever CI later runs it. That was true of the old
refusal too, for every command except this one.

## Idempotent, via lookup keys

Each price gets a `lookup_key` derived from the tier and the amount. Re-running finds
the existing price and reports it rather than creating a second one — which matters,
because Stripe prices are immutable and a duplicate is not something you can tidy up
afterwards, only archive.

Lookup keys are per-ledger, so the test and live ledgers each hold their own price under
the same key and neither run can see or disturb the other's.

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

#: Namespaces the lookup keys inside a Stripe account that holds more than this product.
#: It does: `acct_1Im6omLyohGVct3t` carries an unrelated project's prices alongside these,
#: and an unprefixed `label_monthly_49usd_v1` is exactly the sort of name two products
#: collide on. Was `rtf_` — the project's former name — and changing it was free only
#: because no price had been created under the old prefix yet. It is not free now: a
#: Stripe price cannot be renamed or deleted, so a future change to this string strands
#: the existing prices and creates a second set beside them.
LOOKUP_PREFIX = "spindle"


def lookup_key(tier: plans.Tier) -> str:
    """Stable per (tier, amount). See the module docstring on why the amount is in it."""
    return (f"{LOOKUP_PREFIX}_{tier.key}_monthly_"
            f"{tier.price_usd_month}usd_{LOOKUP_VERSION}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be created and exit without calling Stripe")
    ap.add_argument("--live", action="store_true",
                    help="permit an sk_live_ key and create products in the LIVE ledger. "
                         "Required with a live key and refused with a test one; see the "
                         "module docstring for what this does and does not protect.")
    ap.add_argument("--webhook-url", default="",
                    help="also register a webhook endpoint at this URL and print its "
                         "signing secret. Must end in /billing/webhook. Stripe reveals "
                         "the secret only when the endpoint is created.")
    args = ap.parse_args()

    purchasable = [t for t in plans.TIERS if t.purchasable]
    if not purchasable:
        print("No purchasable tiers in plans.TIERS. Nothing to create.", file=sys.stderr)
        return 1

    # The mode is needed to name the env vars, and `--dry-run` should name the ones the
    # run would actually produce rather than the test pair regardless.
    mode = "live" if args.live else "test"

    print(f"Tiers that need a Stripe price, in {mode.upper()} mode:\n")
    for t in purchasable:
        print(f"  {t.name:<12} ${t.price_usd_month}/month"
              f"   {t.open_conversations} conversations per {plans.PERIOD}")
        print(f"  {'':12} lookup_key={lookup_key(t)}")
        print(f"  {'':12} env var    {t.price_setting(mode).upper()}")
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

    # The key and the flag must agree, and disagreeing either way is refused. A live key
    # without `--live` is the accident the flag exists to catch. A test key *with*
    # `--live` is the other half of the same mistake and is refused rather than quietly
    # honoured: it would write `STRIPE_PRICE_LABEL_LIVE=price_…` for a price that exists
    # only in the test ledger, and that variable would then sit in a production .env
    # looking correct and failing at the first real checkout.
    if key.startswith("sk_live_") and not args.live:
        print("That is a live key and --live was not given.\n\n"
              "Pass --live if you mean to create products in the live ledger. The flag\n"
              "exists so that a live key exported for some other reason cannot create\n"
              "live products just because you ran your usual command.", file=sys.stderr)
        return 3
    if key.startswith("sk_test_") and args.live:
        print("--live was given but STRIPE_SECRET_KEY is a test key.\n\n"
              "This would create prices in the test ledger and print them as the _LIVE\n"
              "variables, which is a production .env that looks configured and fails at\n"
              "the first real checkout. Nothing was sent to Stripe.", file=sys.stderr)
        return 3
    if not key.startswith(("sk_test_", "sk_live_")):
        print("That does not look like a Stripe secret key — expected an sk_test_ or "
              "sk_live_ prefix.", file=sys.stderr)
        return 3

    if args.webhook_url and not args.webhook_url.endswith("/billing/webhook"):
        print(f"--webhook-url is {args.webhook_url!r}, which does not end in "
              f"/billing/webhook.\n\nThat is the only path routes.py serves the handler "
              f"on, and an endpoint registered anywhere else receives deliveries that "
              f"404. Nothing was sent to Stripe.", file=sys.stderr)
        return 3

    try:
        import stripe  # noqa: PLC0415 — imported here so --dry-run needs no dependency
    except ModuleNotFoundError:
        print("The `stripe` package is not installed in this environment.\n"
              "  cd apps/spindle/web && .venv/bin/pip install stripe", file=sys.stderr)
        return 4

    # The event name comes from the handler that acts on it rather than being spelled
    # again here, so an endpoint registered by this script cannot end up subscribed to
    # something `handle_event` ignores. Imported down here and not at the top because
    # `billing` pulls in psycopg, and `--dry-run` is meant to work in a bare checkout.
    from spindle.billing import CHECKOUT_COMPLETED  # noqa: PLC0415

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
                metadata={"tier": t.key, "created_by": "apps/spindle/bin/stripe_setup.py"},
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

        lines.append(f"{t.price_setting(mode).upper()}={price.id}")

    webhook_secret = ""
    if args.webhook_url:
        # Reuse an endpoint already pointing at this URL rather than stacking a second
        # one: two enabled endpoints on the same URL means every event delivered twice,
        # which `handle_event` survives (the write is a set, not an increment) but which
        # doubles the noise in the dashboard for no gain. The catch is that Stripe will
        # not re-reveal the existing endpoint's secret, so this can only tell you to go
        # and look — it cannot recover it.
        existing = [e for e in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter()
                    if e.url == args.webhook_url]
        if existing:
            print(f"\nwebhook: {existing[0].id} already points at that URL; leaving it "
                  f"alone.\n         Stripe does not re-reveal a signing secret after "
                  f"creation — if you\n         do not have it, roll it in the dashboard "
                  f"or delete and re-run.")
        else:
            endpoint = stripe.WebhookEndpoint.create(
                url=args.webhook_url,
                # Exactly the one event `handle_event` acts on. Subscribing to more would
                # mean paying delivery latency for events the handler immediately ignores,
                # and every one of them is a row in the dashboard that looks like traffic.
                enabled_events=[CHECKOUT_COMPLETED],
                description="Spindle console — plan and ceiling writeback",
            )
            webhook_secret = str(endpoint.secret)
            print(f"\nwebhook: created {endpoint.id} -> {args.webhook_url}")

    print("\nAdd these to .env:\n")
    for line in lines:
        print(f"  {line}")
    if webhook_secret:
        # Printed because there is no second chance to read it. See the module docstring.
        print(f"  STRIPE_WEBHOOK_SECRET={webhook_secret}")
    else:
        print("\nStill needed, from the Stripe dashboard:\n")
        print("  STRIPE_WEBHOOK_SECRET=whsec_...   Developers -> Webhooks -> endpoint")
        print("\nWebhook endpoint to register:\n")
        print("  URL     <your Lambda Function URL>/billing/webhook")
        print(f"  Events  {CHECKOUT_COMPLETED}")
        print("\n  ...or re-run this with --webhook-url to register it here.")
    print("\nTo verify it end to end before deploying:\n")
    print("  stripe listen --forward-to localhost:8000/billing/webhook")
    print(f"  stripe trigger {CHECKOUT_COMPLETED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
