"""What a new tenant has not done yet, derived rather than stored.

A console whose every screen is empty is indistinguishable from a console that is
broken. `today.html` says *"Nothing needs you. Everything the fleet was allowed to
decide, it decided."* — which is true on the first morning of an account and useless,
because there is nothing to decide about a roster with nothing in it. This module is
what the console shows instead: three steps, each pointing at a control that already
exists, and it stops showing them the moment they are done.

## The one decision worth defending: nothing is stored

There is no `onboarding_step` column, no `completed_at`, no progress row. `state()` asks
the three tables the steps are *about* whether they have anything in them. A stored flag
and the catalogue can disagree — and when they do, the flag wins the render and lies:
it says "you have uploaded a master" to a tenant who deleted theirs, or keeps nagging
one who imported a catalogue by API and never touched the form. A count cannot be wrong
about this, because the count *is* the thing the step is asking about.

That also means the rails behave correctly under the cases nobody writes a migration
for. Delete your only artist and the step comes back, which is right: the console is
empty again and the sentence explaining why should be too. Arrive by MCP with two
hundred recordings already loaded and you never see the banner at all, because you were
never a beginner.

## Dismissal is a cookie, and is honestly only a cookie

The banner has an `×`, and what it sets is `spindle_start=dismissed` in the browser —
not a column on `account`. Two reasons, in order of weight:

  * **Migrations here are applied by hand.** `apply.py` is run by a human against a
    cluster, out of band from any deploy (see `docs/runbooks/environments.md`). Code
    that reads a column ships in one step and the column arrives in another, and if the
    order slips, *every console page* 500s on a `SELECT` of something that is not there
    — for a dismiss button. The blast radius is wildly out of proportion to the feature.
  * **It is genuinely a browser preference.** "Stop showing me this hint" is the same
    class of thing as a collapsed sidebar. Persisting it per account would claim a
    durability the setting does not need.

The cost, stated so it is not discovered: dismiss on a laptop and the banner is back on
a phone. That is bounded by the steps themselves — three of them, and finishing any one
is more progress than dismissing it — and dismissal only ever hides an *incomplete*
checklist, since a complete one is not rendered to anybody.

## The upsell is prose and a link, and never a gate

`upsell()` names the next tier a tenant could buy, and the banner renders one sentence
about it. It deliberately does not POST to `/billing/checkout`: `/account` is where the
checkout that actually works lives, and that page already renders the three truths this
one would otherwise have to restate — Stripe unconfigured, Stripe in test mode, and
sending disabled on every tier regardless. Duplicating them here is how the two come to
disagree, and the one that disagrees on a banner is the one nobody re-reads.

Every number in that sentence comes from `plans.TIERS`. `plans.py` opens with the
argument for why: a page that hardcoded "50 conversations" goes on saying 50 after the
tier changes, and the first symptom is a customer quoting it back.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from spindle import plans, repo

#: The cookie that hides an unfinished checklist. Read by `routes._ctx`, written by
#: `POST /start/dismiss`, and named here so the two cannot drift.
COOKIE_NAME = "spindle_start"

#: The only value that means anything. Any other content — a stale cookie from a future
#: version, something a person typed into devtools — is treated as absent, which errs
#: toward showing the hint rather than hiding it. That is the safe direction for a
#: banner: being shown something you have finished with is a mild annoyance, and being
#: silently denied the only guidance in the product is not.
DISMISSED = "dismissed"

#: A year. Long enough that dismissing means dismissing, short enough that it is not a
#: permanent record — and it does not need to outlive the session cookie by much, since
#: an account that has been away for a year and still has no artist is one the hint is
#: arguably for again.
COOKIE_SECONDS = 60 * 60 * 24 * 365


@dataclass(frozen=True)
class Step:
    """One rung, and whether the tenant is standing on it.

    Frozen for the same reason `plans.Tier` is: this describes what is true, and code
    that can flip `done` on the object it is rendering is code that can congratulate
    somebody for work they have not done.
    """

    #: Stable identifier. Used by the template for nothing but a CSS hook, and by the
    #: tests to assert the order has not changed under a copy edit.
    key: str

    #: The imperative, in the banner's chip. Short — this renders in a strip.
    label: str

    #: One clause of why, shown on the step that is next. Not shown on the others: a
    #: strip that explains three things at once has explained none of them.
    blurb: str

    #: Where the control is. Always a real address in this console — see `ready`.
    href: str

    #: The button's words, when this is the next step.
    cta: str

    done: bool

    #: Whether `href` addresses something that exists yet.
    #:
    #: Only `master` is ever `False`: the audio upload lives in the Tracks inspector for
    #: a *selected* track, so before there is a recording there is no panel to link to.
    #: The alternative was linking to `/tracks` bare and letting the operator work out
    #: that they were meant to make a track first, which is the kind of dead end that
    #: makes a tutorial worse than no tutorial. An unready step renders as text.
    ready: bool


@dataclass(frozen=True)
class Rails:
    """The whole banner's state: the steps, and what could be sold to whoever sees them."""

    steps: tuple[Step, ...]

    #: What the tenant is on now. Never `None` for a signed-in principal — `plans.tier`
    #: raises on a key it does not define rather than guessing, and this holds the
    #: resolved result.
    tier: plans.Tier

    #: The cheapest tier above `tier` that `POST /billing/checkout` can actually sell,
    #: or `None` when there is nothing above it to buy. See `upsell`.
    upgrade: plans.Tier | None

    @property
    def complete(self) -> bool:
        return all(step.done for step in self.steps)

    @property
    def done_count(self) -> int:
        return sum(1 for step in self.steps if step.done)

    @property
    def next_step(self) -> Step | None:
        """The first unfinished step, or `None` when there is none.

        First rather than "first that is ready": if the next thing to do is the master
        and there is no track, the honest answer is to show that step as the one in
        front of you, unlinked, rather than to skip past it to something further down
        the list. `ready` is what the template branches on to decide whether to draw a
        button, and skipping the step here would hide the ordering that makes the
        checklist make sense.
        """
        return next((step for step in self.steps if not step.done), None)


def upsell(current: plans.Tier) -> plans.Tier | None:
    """The cheapest purchasable tier above `current`, or `None`.

    `None` has two causes and they are deliberately not distinguished, because the
    banner does the same thing for both — say nothing:

      * The tenant is already on the most expensive purchasable tier. There is nothing
        to sell them from a strip at the top of a page.
      * The tenant is on Catalogue, which is negotiated. `plans.Tier.purchasable` is
        `False` for it and its price is `None`; an automated nudge at somebody with a
        contract is at best noise.

    Order comes from `plans.TIERS`, which is a tuple precisely so that "cheapest first"
    is part of the definition rather than a property of a dict that somebody relies on
    by accident. So "above" is positional, and it is checked against price below rather
    than assumed: a tier that is later in the tuple but not more expensive would mean
    `TIERS` had been reordered, and quietly recommending a downgrade is worse than
    recommending nothing.
    """
    if current.price_usd_month is None:
        # Negotiated. There is no number to compare against, and inventing one to make
        # the loop below work would be inventing a price.
        return None
    for tier in plans.TIERS:
        if not tier.purchasable or tier.price_usd_month is None:
            continue
        if tier.price_usd_month > current.price_usd_month:
            return tier
    return None


#: One round trip for four facts. Written as scalar subqueries rather than four
#: statements for the reason `routes._nav` gives about the badges: this renders on every
#: console page, and a cluster that scales to zero bills per round trip.
#:
#: Every subquery is scoped by `tenant_id` — the leading column of every index here since
#: migration 001 — so this is four index lookups and not four scans.
#:
#: The first one is a join rather than a count of a table called `artist`, because there
#: is no such table: migration 005 replaced it with `party` plus a role. "The roster is
#: `party_role`, not a table," as `repo` puts it, and the join here is character for
#: character the one `repo.list_parties` uses — deliberately, because this step is a
#: claim about that screen. The step is done exactly when `/artists` has a row on it, and
#: the only way to keep that true through a schema change is to ask the same question.
#:
#: Counting `party` bare would be the tempting simplification and it is wrong. A label
#: that has harvested creators, or imported counterparties off a statement, has `party`
#: rows and an empty roster; the bare count ticks their first step and then sends them to
#: a screen with nothing on it — the precise failure the checklist exists to prevent.
_COUNTS = """
SELECT
  (SELECT count(*) FROM party p
     JOIN party_role r ON r.tenant_id = p.tenant_id
                      AND r.party_id  = p.id
                      AND r.role      = %(role)s
    WHERE p.tenant_id = %(tenant)s)                             AS artists,
  (SELECT count(*) FROM recording WHERE tenant_id = %(tenant)s) AS recordings,
  (SELECT count(*) FROM recording_asset
     WHERE tenant_id = %(tenant)s
       AND kind  = 'master'
       AND state = 'stored')                                    AS masters,
  (SELECT id FROM recording
     WHERE tenant_id = %(tenant)s
     ORDER BY created_at, id
     LIMIT 1)                                                   AS first_recording
"""


def state(conn: psycopg.Connection, tenant_id: str, plan_key: str) -> Rails | None:
    """The rails for one tenant, or `None` if there is nothing to show.

    `None` means *do not render the banner*, and it is returned for the one case that
    matters: every step is done. A tenant with an artist, a track and a master is not a
    beginner and the console owes them the space back.

    Note what is **not** caught here. `routes._nav` swallows `psycopg.OperationalError`
    because a rail without badge numbers is still a rail; this raises, because a banner
    is not load-bearing chrome and a caller that cannot reach the database has a bigger
    problem than a missing hint. The caller decides — see `routes._ctx`, which treats an
    unreachable cluster as "no banner" at exactly one place rather than having this
    module invent a policy for it.

    `plan_key` is passed rather than read, because the principal already carries it on
    every request and a second lookup for a value we hold would be a query per page for
    nothing. It is resolved through `plans.tier`, which raises on a key this build does
    not define — the loud direction, and the one `plans.py` argues for at length.
    """
    with conn.cursor() as cur:
        # `repo.ROSTER_ROLE` rather than the literal it currently holds, for the reason
        # `COOKIE_NAME` is defined once above: two spellings of the same constant drift,
        # and this one drifts silently — a renamed role would not fail, it would just
        # count zero and show a finished tenant a beginner's checklist forever.
        cur.execute(_COUNTS, {"tenant": tenant_id, "role": repo.ROSTER_ROLE})
        counts = cur.fetchone() or {}

    artists = int(counts.get("artists") or 0)
    recordings = int(counts.get("recordings") or 0)
    masters = int(counts.get("masters") or 0)
    first_recording = counts.get("first_recording")

    # Counted regardless of `status`. Both `party` and `recording` default to 'active'
    # and can be withdrawn, and a withdrawn row is still a row the operator created —
    # the step asks "have you added one of these", not "do you have a live one". Reading
    # it the other way would resurrect the whole checklist for a label that archived its
    # back catalogue, which is the opposite of what the state means.
    steps = (
        Step(
            key="artist",
            label="Add an artist",
            blurb="An act you release. Everything else in the console hangs off one.",
            href="/artists?sel=new",
            cta="Add an artist",
            done=artists > 0,
            ready=True,
        ),
        Step(
            key="track",
            label="Add a track",
            blurb="A recording, with its ISRC if you have one. Leave it blank if not.",
            href="/tracks?sel=new",
            cta="Add a track",
            done=recordings > 0,
            ready=True,
        ),
        Step(
            key="master",
            label="Upload the master",
            # Said plainly because it is the step people skip: the audio is not
            # decoration, it is what several of the fleet's agents actually read.
            blurb="The audio itself. It is what the analysis reads — without it, "
                  "the fleet is working from titles.",
            # Addresses the *first* recording rather than the newest. On this screen
            # there is normally exactly one, and "the track you just made" and "the
            # oldest track" are the same row; where they are not, the operator has more
            # than one track and does not need this hint to find the panel.
            href=f"/tracks?sel={first_recording}" if first_recording else "/tracks",
            cta="Open the track",
            done=masters > 0,
            ready=first_recording is not None,
        ),
    )

    if all(step.done for step in steps):
        return None

    tier = plans.tier(plan_key)
    return Rails(steps=steps, tier=tier, upgrade=upsell(tier))
