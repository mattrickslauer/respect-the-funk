"""The spend gate. Nothing in this system calls a metered API without passing through here.

The rule this module exists to make mechanical: **default deny.** A missing environment
variable means paid calls are off, not on. A missing rate card entry means the call is
refused, not billed at an unknown price. A ceiling that cannot be read means zero, not
infinity. Every failure mode points at not spending money.

That inversion is the whole design. The usual shape — call the API, record the cost,
alert when a dashboard crosses a line — discovers the overspend after it happened, which
for a token API can be a four-figure afternoon. Here the estimate is computed *before*
the call and the call does not happen if it would breach what is left.

Three things are deliberately not here:

  * **No retries inside the gate.** A retry is a second call at a second price, so it is
    the caller's decision and it passes through the gate again.
  * **No "just this once" override in code.** The only way to spend more is to raise a
    number in the environment, which is an act somebody performs on purpose and can see
    in a diff.
  * **No silent truncation.** A refusal is returned as a refusal and recorded in
    `agent_run` with what it would have cost. `infra/MEMORY-WORKLOAD.md`'s house rule —
    mark what you did not do rather than let the absence read as completeness.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg


class SpendRefused(RuntimeError):
    """A call was not made because it would have cost money we had not allowed.

    Carries the estimate so the caller can record what it declined to spend rather than
    logging "skipped" and losing the number.
    """

    def __init__(self, message: str, *, estimate_usd: Decimal, reason: str) -> None:
        super().__init__(message)
        self.estimate_usd = estimate_usd
        self.reason = reason


# ----------------------------------------------------------------- rate card

@dataclass(frozen=True)
class Rate:
    """What one provider call costs. Per million tokens, or flat per request.

    Rates are recorded here rather than fetched, because a gate that has to make a
    network call to learn a price cannot be trusted to run before the call it is
    gating. They will drift from the vendor's published numbers; that is acceptable in
    the conservative direction only, so **round up when updating them.**
    """

    label: str
    usd_per_mtok_in: Decimal = Decimal("0")
    usd_per_mtok_out: Decimal = Decimal("0")
    usd_per_request: Decimal = Decimal("0")
    note: str = ""


#: Every metered thing this system can call. A provider absent from this table cannot be
#: called at all — `estimate()` raises rather than guessing, because an unknown price is
#: the one case where guessing is unbounded.
#:
#: Rates checked 2026-08-07 and rounded up. Verify before relying on them for anything
#: larger than a smoke test.
RATES: dict[str, Rate] = {
    "bedrock:amazon.titan-embed-text-v2:0": Rate(
        "Titan Embed v2", usd_per_mtok_in=Decimal("0.02"),
        note="On-demand quota on this account is 0 RPM — unusable regardless of price.",
    ),
    "bedrock:anthropic.claude-sonnet-4-5": Rate(
        "Claude Sonnet 4.5 via Bedrock",
        usd_per_mtok_in=Decimal("3.00"), usd_per_mtok_out=Decimal("15.00"),
        note="On-demand quota on this account is 0 RPM.",
    ),
    "openai:text-embedding-3-small": Rate(
        "OpenAI embeddings small", usd_per_mtok_in=Decimal("0.02"),
    ),
    "openai:gpt-4o-mini": Rate(
        "GPT-4o mini",
        usd_per_mtok_in=Decimal("0.15"), usd_per_mtok_out=Decimal("0.60"),
    ),
    "aws:ce:GetCostAndUsage": Rate(
        "AWS Cost Explorer query", usd_per_request=Decimal("0.01"),
        note="Checking the bill costs money. Priced here so nobody calls it in a loop.",
    ),
}

#: Free. Listed explicitly rather than assumed, so adding a source is a decision about
#: whether it is metered rather than a silence.
FREE: frozenset[str] = frozenset({
    "musicbrainz", "coverartarchive", "wikidata", "web_page", "manual",
})


# ------------------------------------------------------------------ the gate

@dataclass(frozen=True)
class Policy:
    """Read from the environment on every construction, never cached across a process.

    Cached policy is how a kill switch fails: somebody sets the variable to zero,
    nothing restarts, and the long-running worker keeps spending against a limit it
    read an hour ago.
    """

    paid_enabled: bool
    daily_ceiling_usd: Decimal
    per_call_ceiling_usd: Decimal
    dry_run: bool

    @classmethod
    def load(cls) -> "Policy":
        return cls(
            # Default off. "1", "true" and "yes" enable it; anything else, including
            # an unset variable, a typo or an empty string, does not.
            paid_enabled=os.environ.get("RTF_PAID_ENABLED", "").strip().lower()
            in {"1", "true", "yes"},
            daily_ceiling_usd=_money(os.environ.get("RTF_DAILY_CEILING_USD"), "0"),
            per_call_ceiling_usd=_money(os.environ.get("RTF_PER_CALL_CEILING_USD"), "0.05"),
            dry_run=os.environ.get("RTF_DRY_RUN", "").strip().lower()
            in {"1", "true", "yes"},
        )


def _money(raw: str | None, default: str) -> Decimal:
    """A malformed ceiling is zero, not the default and never infinity.

    Somebody typing `RTF_DAILY_CEILING_USD=5.00 USD` should get a system that refuses to
    spend, not one that falls back to a number they did not choose.
    """
    if raw is None or not raw.strip():
        return Decimal(default)
    try:
        value = Decimal(raw.strip())
    except Exception:  # noqa: BLE001 — any parse failure is the same answer
        return Decimal("0")
    return value if value >= 0 else Decimal("0")


def estimate(key: str, *, tokens_in: int = 0, tokens_out: int = 0,
             requests: int = 1) -> Decimal:
    """What a call would cost, before making it.

    Raises for an unpriced key. That is the point: a provider nobody has priced is a
    provider nobody has bounded, and the safe answer to "how much is this?" when the
    answer is unknown is to not find out by doing it.
    """
    if key in FREE:
        return Decimal("0")
    rate = RATES.get(key)
    if rate is None:
        raise SpendRefused(
            f"{key!r} has no rate card entry, so its cost is unknown.",
            estimate_usd=Decimal("0"), reason="unpriced",
        )
    million = Decimal(1_000_000)
    return (
        rate.usd_per_mtok_in * Decimal(tokens_in) / million
        + rate.usd_per_mtok_out * Decimal(tokens_out) / million
        + rate.usd_per_request * Decimal(requests)
    ).quantize(Decimal("0.000001"))


def spent_today(conn: psycopg.Connection, tenant_id: str) -> Decimal:
    """Actual spend in the last 24 hours, summed from `agent_run`.

    Summed rather than decremented from a counter: a counter row is a serialization
    point under `SERIALIZABLE`, and ten workers on one launching artist would retry
    against each other on exactly the row that is supposed to be protecting them.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT coalesce(sum(cost_micro_usd), 0) AS micro
                 FROM agent_run
                WHERE tenant_id = %s AND started_at > now() - INTERVAL '24 hours'""",
            (tenant_id,),
        )
        row = cur.fetchone()
    return (Decimal(row["micro"]) / Decimal(1_000_000)).quantize(Decimal("0.000001"))


@dataclass
class Gate:
    """Ask before you spend.

    Construct one per run, check every metered call through it, and record the result.
    Holding it for the length of a run rather than a process means the ceiling is
    re-read often enough for a change to take effect without a restart, and rarely
    enough that a tight loop does not hammer the database for it.
    """

    policy: Policy
    already_spent_usd: Decimal
    refused: list[tuple[str, Decimal, str]]

    @classmethod
    def open(cls, conn: psycopg.Connection | None, tenant_id: str | None) -> "Gate":
        policy = Policy.load()
        spent = Decimal("0")
        if conn is not None and tenant_id and policy.paid_enabled:
            spent = spent_today(conn, tenant_id)
        return cls(policy=policy, already_spent_usd=spent, refused=[])

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal("0"), self.policy.daily_ceiling_usd - self.already_spent_usd)

    def check(self, key: str, *, tokens_in: int = 0, tokens_out: int = 0,
              requests: int = 1) -> Decimal:
        """Return the estimate if the call is allowed. Raise `SpendRefused` otherwise.

        Free keys short-circuit before any policy check, so turning paid calls off never
        stops MusicBrainz or a plain page fetch from working. Losing the free half of
        the system when the paid half is disabled would make the kill switch too
        expensive to ever use, which is how kill switches end up never being used.
        """
        cost = estimate(key, tokens_in=tokens_in, tokens_out=tokens_out, requests=requests)
        if cost == 0:
            return cost

        def refuse(reason: str, message: str) -> None:
            self.refused.append((key, cost, reason))
            raise SpendRefused(message, estimate_usd=cost, reason=reason)

        if self.policy.dry_run:
            refuse("dry_run", f"Dry run: {key} would have cost ${cost}.")
        if not self.policy.paid_enabled:
            refuse("disabled",
                   f"Paid calls are off. {key} would have cost ${cost}. "
                   f"Set RTF_PAID_ENABLED=1 to allow it.")
        if cost > self.policy.per_call_ceiling_usd:
            refuse("per_call",
                   f"{key} estimates ${cost}, over the per-call ceiling of "
                   f"${self.policy.per_call_ceiling_usd}.")
        if cost > self.remaining_usd:
            refuse("daily",
                   f"{key} estimates ${cost} but only ${self.remaining_usd} of the "
                   f"${self.policy.daily_ceiling_usd} daily ceiling is left.")
        return cost

    def record(self, cost_usd: Decimal) -> None:
        """Called after a paid call actually happened.

        Kept separate from `check` so an estimate is never mistaken for a charge: a call
        that was allowed and then failed on the network cost nothing, and counting it
        would spend the ceiling on requests that never billed.
        """
        self.already_spent_usd += cost_usd

    def summary(self) -> dict[str, Any]:
        """What to write into `agent_run`. Refusals are data, not a log line."""
        return {
            "paid_enabled": self.policy.paid_enabled,
            "dry_run": self.policy.dry_run,
            "ceiling_usd": str(self.policy.daily_ceiling_usd),
            "spent_usd": str(self.already_spent_usd),
            "refused": [
                {"key": k, "would_have_cost_usd": str(c), "reason": r}
                for k, c, r in self.refused
            ],
        }
