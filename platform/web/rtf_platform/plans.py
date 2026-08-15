"""What the tiers are. One definition, and everything else reads it.

The billable unit is **an open conversation**, counted per calendar month. Not a seat,
not an artist, not an API call, not a message. That choice is the product's whole
pricing argument and it is worth stating here rather than in a marketing page, because
the number in the marketing page has to come from this file:

  * A **seat** prices the wrong thing. This is an agentic pipeline; a label with one
    operator and four hundred conversations is getting four hundred conversations of
    value, and charging them for one seat prices the human rather than the work.
  * A **message** prices the wrong thing in the other direction. It rewards us for
    sending more mail to more strangers, which is precisely the incentive this system
    is built to refuse — `sender.py`, the approvals gate and `contact_route.opted_out`
    all exist to send *less*.
  * An **open conversation** is the unit the operator already thinks in, it is the unit
    the schema already enforces (`one_open_thread_per_counterparty`), and it is the unit
    whose cost to us actually scales: a thread means research, embeddings, drafting and
    an operator's attention, all of which are metered by `spend.py`.

## Why this module exists at all rather than four numbers in a template

Because they would be four numbers in a template *and* four numbers in the enforcement
path, and the first time somebody raised the Label tier from 50 to 75 they would change
one of them. A pricing page that promises more than the gate allows is a support ticket
that reads as fraud; a gate that allows more than the page promises is revenue nobody
charged for. `outreach.open_thread` and the landing page import the same `Tier`.

## The period, stated once

Every allowance below is **per calendar month, UTC**, counted as *conversations opened*
within that month — not as conversations currently open.

That distinction is the one non-obvious decision in this file. "Currently open" is the
friendlier reading and it is the wrong meter: closing a thread would refund the
allowance, so a tenant on the free tier could open five conversations, close them, and
open five more, forever. The meter would measure their tidiness rather than their usage.
Counting what was *opened* means the number only ever goes up within a month and resets
on the first, which is what "50 conversations a month" means to the person buying it.

The cost of that choice, said out loud: a tenant who opens a conversation and closes it
by mistake has spent one. There is no credit mechanism and inventing one before anybody
has asked would be inventing a policy from imagination.

## What is deliberately not here

**No trial, no annual discount, no proration, no currency other than USD.** Each is a
real thing to want; none has been decided, and a constant invented now would be
permanent and wrong. Stripe holds the price objects — these are the numbers we *display*
and enforce against, and `settings.stripe_price_*` holds the ids of what is actually
charged. The two are checked against each other by nothing, which is a real gap and is
recorded in `billing.py` rather than papered over.

**No claim that any of this has been paid.** No customer has been billed through this
code. It is wired against Stripe test mode and `billing.py` refuses a live key.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The window every allowance is measured over, in the words a UI should use. One string
#: so the pricing page, the console's usage line and this module's refusal message cannot
#: describe three different periods.
PERIOD = "calendar month"


@dataclass(frozen=True)
class Tier:
    """One row of the pricing table, and the enforcement behind it.

    Frozen because a tier is a fact about what was sold, and code that can mutate the
    plan it is checking against is code that can raise its own limit.

    `None` means different things in different fields and never means "unset". It is
    spelled out per field below, because a `None` whose meaning has to be guessed is the
    silent default this repository refuses everywhere else.
    """

    #: Stored in `account.plan`, and the key the CHECK constraint in migration 033
    #: enumerates. Changing one of these is a migration, not an edit.
    key: str

    #: What a human reads on the pricing page.
    name: str

    #: USD per month. `0` is genuinely free. `None` means *not self-serve* — the price is
    #: negotiated, and the UI shows "contact" rather than a number.
    price_usd_month: int | None

    #: Conversations that may be opened per `PERIOD`. `None` means the allowance is
    #: contractual rather than numeric — see `conversation_refusal`, which does not
    #: silently treat it as infinity.
    open_conversations: int | None

    #: Roster size. `None` means unlimited, which is a real product decision on the two
    #: paid-and-metered tiers: the meter is conversations, so an artist who is not being
    #: pitched costs us nothing and charging for the row would be charging for storage
    #: nobody uses.
    artists: int | None

    #: Whether outbound mail is permitted for this tier *as a matter of plan*. Note that
    #: this is the second of two locks and not the only one: `api/reads.sender_wired` is
    #: hardcoded `False` and no Sender claims the outbox on any tier, so nothing sends
    #: today regardless of what this says. This flag exists so that when sending is
    #: eventually wired, turning it on does not turn it on for free accounts as a side
    #: effect.
    sends_enabled: bool

    #: Seeded into `tenant_budget.daily_ceiling_usd` when the plan is written. Dollars per
    #: rolling 24 hours of metered API spend — embeddings, the Ask screen's router call,
    #: eventually SES. `spend.Gate` is what enforces it; this is only where the number
    #: comes from. Deliberately small on every tier: this whole repository's spend budget
    #: is $10, and a tier whose ceiling exceeds what the project can pay is a promise the
    #: cluster cannot keep.
    daily_spend_cap_usd: Decimal

    #: The attribute on `settings.Settings` holding this tier's Stripe price id, or `""`
    #: when there is nothing to buy. A *name* rather than the id itself, because the id
    #: is configuration and this file is code — a price id compiled into the source is a
    #: price id that cannot differ between test and live, which is exactly the mistake
    #: `settings.py`'s opening paragraph exists to prevent.
    stripe_price_setting: str

    #: One line for the pricing page. Prose lives here rather than in the template for
    #: the same reason the numbers do.
    summary: str

    @property
    def unlimited_artists(self) -> bool:
        return self.artists is None

    @property
    def purchasable(self) -> bool:
        """Whether `POST /billing/checkout` can sell this tier.

        Free is not purchasable because there is nothing to charge; Catalogue is not
        purchasable because it is a conversation with a human. A UI can branch on this
        instead of hardcoding which two of the four have buttons.
        """
        return bool(self.stripe_price_setting)

    def conversation_refusal(self, opened_this_period: int) -> str | None:
        """`None` if another conversation may be opened, or the sentence explaining why not.

        A sentence and not a boolean, because the sentence is the product — `api/errors.py`
        opens with that argument and this is where the wording for the plan ceiling lives,
        so the console, the JSON API and any future client all refuse in the same words.

        **A `None` allowance permits the conversation, and that is a decision, not a
        fallback.** Catalogue is negotiated in a contract, so there is no number here to
        check against; refusing would break a tenant whose limit this code does not know,
        and inventing a number would enforce a limit nobody agreed to. It is safe to let
        through because `catalogue` is unreachable from any public path — the claim
        endpoint only ever writes `free`, and the Stripe webhook only ever writes a tier
        it has a price id for. Somebody with SQL access set it deliberately.
        """
        if self.open_conversations is None:
            return None
        if opened_this_period < self.open_conversations:
            return None
        return (
            f"The {self.name} plan allows {self.open_conversations} open conversations "
            f"per {PERIOD}, and {opened_this_period} have been opened this one. Close "
            f"nothing — closing does not give the allowance back, because the meter "
            f"counts conversations started. Upgrade, or wait for the first of the month."
        )


#: Every tier, in the order a pricing page should show them. A tuple because the order is
#: part of the definition: cheapest first is how a reader compares them, and a dict's
#: order is an implementation detail somebody will eventually rely on by accident.
TIERS: tuple[Tier, ...] = (
    Tier(
        key="free",
        name="Free / Judge",
        price_usd_month=0,
        open_conversations=5,
        artists=1,
        sends_enabled=False,
        # Twenty cents a day. Enough for the Ask screen and a few hundred embeddings,
        # which is enough to see the product work; nowhere near enough for an anonymous
        # signup to cost this project money. `POST /claim` is public on a scale-to-zero
        # cluster, and this number is one of the four things standing between that
        # endpoint and a bill.
        daily_spend_cap_usd=Decimal("0.20"),
        stripe_price_setting="",
        summary="Everything the console does, on one artist, with sending off. "
                "Enough to judge the product without a card.",
    ),
    Tier(
        key="label",
        name="Label",
        price_usd_month=49,
        open_conversations=50,
        artists=3,
        sends_enabled=True,
        daily_spend_cap_usd=Decimal("2.00"),
        stripe_price_setting="stripe_price_label",
        summary="An independent label running three acts. Fifty conversations a month "
                "is roughly one campaign per act per quarter, worked properly.",
    ),
    Tier(
        key="roster",
        name="Roster",
        price_usd_month=199,
        open_conversations=250,
        artists=None,
        sends_enabled=True,
        daily_spend_cap_usd=Decimal("8.00"),
        stripe_price_setting="stripe_price_roster",
        summary="A full roster, no cap on how many acts. The meter is conversations, "
                "so an artist you are not pitching costs nothing.",
    ),
    Tier(
        key="catalogue",
        name="Catalogue",
        price_usd_month=None,
        open_conversations=None,
        artists=None,
        sends_enabled=True,
        # Not zero, and not a number pretending to be a negotiated ceiling. A Catalogue
        # tenant's real ceiling is written into `tenant_budget` by hand as part of the
        # agreement; this is the value that row is seeded with if somebody forgets, and
        # it is deliberately the same as Roster's rather than larger, so forgetting is
        # conservative instead of expensive.
        daily_spend_cap_usd=Decimal("8.00"),
        stripe_price_setting="",
        summary="Back catalogue, multiple labels, or a shape none of the above fits. "
                "Priced against the work; talk to us.",
    ),
)

#: By key, for the resolution every request path does. Built from `TIERS` so the two can
#: never hold different sets.
BY_KEY: dict[str, Tier] = {t.key: t for t in TIERS}

#: The closed set, for the test that checks it against migration 033's CHECK constraint.
#: Two places declare which plans exist — the database, so a bad row cannot be written,
#: and this file, so the code can resolve one — and a test is what keeps them equal.
KEYS: frozenset[str] = frozenset(BY_KEY)

#: What a claimed account starts on. Named rather than spelled `"free"` at the call site,
#: because "which tier does signup grant" is a product decision and should be greppable.
DEFAULT_KEY = "free"


def tier(key: str) -> Tier:
    """The tier for a stored plan key, or a loud failure.

    Raises rather than returning the free tier for an unknown key, and the temptation to
    do otherwise is worth naming: falling back to free looks safe — it is the *most*
    restrictive tier — but it would silently downgrade a paying tenant to five
    conversations because of a typo in a webhook handler, and the first report would be
    a customer saying the product stopped working. A key that is not in `KEYS` means the
    database and this file disagree, which is a deployment fault, and a deployment fault
    that renders as a product limit is the worst of both.

    Migration 033's CHECK constraint makes an unknown key unwritable through SQL, so
    reaching this raise means either the constraint was dropped or a tier was deleted
    from `TIERS` while rows still referenced it.
    """
    found = BY_KEY.get(key)
    if found is None:
        raise KeyError(
            f"{key!r} is not a plan this build defines. Known plans: "
            f"{', '.join(sorted(KEYS))}. A stored plan with no definition means "
            f"plans.py and migration 033's account_plan_known CHECK have diverged."
        )
    return found


#: The free tier, resolved once. Used by the claim flow and by the tests that assert a
#: claimed account is genuinely bounded.
FREE: Tier = tier(DEFAULT_KEY)
