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
        "Titan Embed v2 (on-demand)", usd_per_mtok_in=Decimal("0.02"),
        note="Priced, and unreachable. The on-demand RPM quota on account 821135790223 "
             "is 0.0 and Service Quotas L-26C560CE reports it as NOT adjustable "
             "(measured 2026-08-13), so there is no increase to wait for. The entry "
             "stays because the adapter stays: a rate card with a hole in it refuses "
             "the call for the wrong reason. Use the ':batch' key below.",
    ),
    "bedrock:amazon.titan-embed-text-v2:0:batch": Rate(
        "Titan Embed v2 (batch inference)", usd_per_mtok_in=Decimal("0.01"),
        note="Half the on-demand rate, which is Bedrock's standard batch discount and "
             "the published Titan Embed v2 batch price. Two entries for one model "
             "because the price differs and the gate must estimate the real one; the "
             "`embedding_model` column deliberately does NOT distinguish them, because "
             "the vectors are identical and splitting the party_shortlist index prefix "
             "over a billing detail would halve every shortlist. Priced but not yet "
             "reachable: account 821135790223 is not entitled to CreateModelInvocationJob "
             "and AWS releases that through a support case. The 100k-records-per-job "
             "quota is a default row published for every account and is not evidence the "
             "capability is on (measured 2026-08-13).",
    ),
    "bedrock:cohere.embed-english-v3": Rate(
        "Cohere Embed English v3 (Bedrock, on-demand)",
        usd_per_mtok_in=Decimal("0.10"),
        note="The published Bedrock on-demand price, $0.0001 per 1K input tokens. Five "
             "times OpenAI's small embedder per token and still $0.0001 per counterparty "
             "at these profile lengths — the reason to be on it is that it runs on this "
             "account, which Titan does not. Unlike the two Titan rows above this one is "
             "reachable: measured 2026-08-16, InvokeModel returns 1024-wide unit-norm "
             "vectors, 96 texts per call. Cost is charged against Bedrock's own "
             "x-amzn-bedrock-input-token-count header rather than the estimator, so this "
             "rate is applied to a measured number and not a guessed one.",
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
    "aws:ses:SendEmail": Rate(
        "Amazon SES outbound email", usd_per_request=Decimal("0.0001"),
        note="$0.10 per thousand. The money is not the point — this entry exists so "
             "sending passes through the same gate as everything else, which means "
             "RTF_PAID_ENABLED=0 is a working kill switch for outbound mail and "
             "RTF_DAILY_CEILING_USD is a hard cap on how many strangers this system "
             "can contact in a day. A runaway loop that mails ten thousand curators "
             "costs $1 and is unrecoverable; the ceiling stops it at the ceiling.",
    ),
}

#: Free. Listed explicitly rather than assumed, so adding a source is a decision about
#: whether it is metered rather than a silence.
#:
#: These must agree with `source_manifest.cost_class = 'free'`, and today they are two
#: places recording one fact — the manifest is operational and lives in the database, this
#: is a code-level guard that must be readable without a connection. When they disagree
#: the gate wins, because the gate is what actually stops a call: a source the manifest
#: calls free but that is missing here is refused as unpriced, which is the safe way round.
FREE: frozenset[str] = frozenset({
    "musicbrainz", "coverartarchive", "wikidata", "web_page", "manual",
    # Public catalogue APIs with no metering. Deezer needs no credential at all;
    # Spotify's client-credentials flow is rate-limited but not billed.
    "deezer", "spotify",
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


def tenant_ceiling(conn: psycopg.Connection,
                   tenant_id: str) -> tuple[Decimal | None, bool]:
    """`(daily ceiling, paused)` for one tenant, or `(None, False)` if it has no budget row.

    `None` is "this tenant has no ceiling of its own", not "unlimited" and not "zero".
    The distinction decides `Gate.remaining_usd`: no row means the environment policy
    applies alone, which is exactly what happened before per-tenant budgets existed, and
    is the state the operator's tenant and every pre-migration-033 tenant are in.

    That is not a silent default. The path that can create a tenant from the public
    internet — `accounts.claim` — always writes this row, in the same transaction as the
    account, so a tenant that a stranger caused to exist is bounded from the instant it
    exists. The only tenants without one are the ones an operator made deliberately, and
    the environment ceiling those fall back to is itself default-deny.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT daily_ceiling_usd, paused FROM tenant_budget WHERE tenant_id = %s",
            (tenant_id,))
        row = cur.fetchone()
    if row is None:
        return None, False
    return Decimal(row["daily_ceiling_usd"]), bool(row["paused"])


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
    #: This tenant's own daily ceiling, from `tenant_budget`, or `None` when it has no
    #: row. Defaulted so every existing construction of a `Gate` in this codebase and its
    #: tests keeps meaning exactly what it meant — a gate built without a tenant budget
    #: behaves identically to one built before the column existed.
    #:
    #: It **narrows** the environment ceiling and can never widen it: `remaining_usd`
    #: takes the smaller of the two. A per-tenant number that could raise the deployment's
    #: ceiling would make `RTF_DAILY_CEILING_USD` advisory, and that variable is the kill
    #: switch — the one thing an operator can change without a deploy to stop all spending
    #: everywhere. A row in a table a webhook writes must not be able to override it.
    tenant_ceiling_usd: Decimal | None = None
    #: The per-tenant kill switch, from the same row. Refuses every priced call with its
    #: own reason, kept distinct from a $0 ceiling because "no allowance today" and
    #: "somebody stopped this tenant" are different facts to whoever reads the run log.
    tenant_paused: bool = False
    #: What *this* gate has actually spent via `record`, separate from
    #: `already_spent_usd`'s running total (which starts from the day's prior spend and
    #: also grows by the same amount). A caller that fails partway through a run — an
    #: agent that embeds three batches and then hits `SpendRefused` on the fourth, or
    #: raises `LeadFailed` after — needs *this* number: the cost already incurred, not
    #: yet reflected in any committed row, that must still land in `agent_run` even
    #: though the write it would have paired with is about to roll back. See
    #: `fleet.work_once`.
    incurred_usd: Decimal = Decimal("0")

    @classmethod
    def open(cls, conn: psycopg.Connection | None, tenant_id: str | None) -> "Gate":
        """Read the policy, the day's spend and the tenant's own ceiling.

        Both reads happen only when there is a connection, a tenant and paid calls are
        enabled — the second condition is what keeps the added round trip off the path of
        a deployment with spending switched off, where the answer cannot change the
        outcome. That is the same reason `spent_today` was already conditional.
        """
        policy = Policy.load()
        spent = Decimal("0")
        ceiling: Decimal | None = None
        paused = False
        if conn is not None and tenant_id and policy.paid_enabled:
            spent = spent_today(conn, tenant_id)
            ceiling, paused = tenant_ceiling(conn, tenant_id)
        return cls(policy=policy, already_spent_usd=spent, refused=[],
                   tenant_ceiling_usd=ceiling, tenant_paused=paused)

    @property
    def ceiling_usd(self) -> Decimal:
        """The ceiling actually in force: the smaller of the deployment's and the tenant's.

        `min`, never `max`, and never the tenant's alone. `RTF_DAILY_CEILING_USD` is the
        operator's kill switch and a row in a table cannot be allowed to raise it — the
        webhook writes that row, so the alternative is a payment provider granting itself
        a higher spending limit on this project's AWS account.
        """
        if self.tenant_ceiling_usd is None:
            return self.policy.daily_ceiling_usd
        return min(self.policy.daily_ceiling_usd, self.tenant_ceiling_usd)

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal("0"), self.ceiling_usd - self.already_spent_usd)

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
        if self.tenant_paused:
            # Checked before the ceilings, so a paused tenant is told it is paused rather
            # than being told it is out of allowance. The two have completely different
            # remedies and the second one sends somebody to look at a number that is fine.
            refuse("tenant_paused",
                   f"This tenant is paused, so {key} was not called. It would have cost "
                   f"${cost}. Clear tenant_budget.paused to resume.")
        if cost > self.policy.per_call_ceiling_usd:
            refuse("per_call",
                   f"{key} estimates ${cost}, over the per-call ceiling of "
                   f"${self.policy.per_call_ceiling_usd}.")
        if cost > self.remaining_usd:
            # Names `ceiling_usd`, the one in force, rather than the environment's. When a
            # tenant's own ceiling is the binding one, quoting the deployment's would send
            # somebody to raise a variable that is not what stopped them.
            refuse("daily",
                   f"{key} estimates ${cost} but only ${self.remaining_usd} of the "
                   f"${self.ceiling_usd} daily ceiling is left.")
        return cost

    def record(self, cost_usd: Decimal) -> None:
        """Called after a paid call actually happened.

        Kept separate from `check` so an estimate is never mistaken for a charge: a call
        that was allowed and then failed on the network cost nothing, and counting it
        would spend the ceiling on requests that never billed. Updates both totals:
        `already_spent_usd` so the *next* `check` in this run sees it, and
        `incurred_usd` so a caller that fails later can still recover exactly how much
        this run spent, independent of whatever it started the day already owing.
        """
        self.already_spent_usd += cost_usd
        self.incurred_usd += cost_usd

    def summary(self) -> dict[str, Any]:
        """What to write into `agent_run`. Refusals are data, not a log line."""
        return {
            "paid_enabled": self.policy.paid_enabled,
            "dry_run": self.policy.dry_run,
            # The ceiling in force, which is what a person reading this row wants to know.
            # The deployment's own number is kept alongside it rather than replaced,
            # because "the tenant was the binding limit" and "the deployment was" are the
            # two different diagnoses, and one number cannot say which.
            "ceiling_usd": str(self.ceiling_usd),
            "policy_ceiling_usd": str(self.policy.daily_ceiling_usd),
            "tenant_ceiling_usd": (None if self.tenant_ceiling_usd is None
                                   else str(self.tenant_ceiling_usd)),
            "tenant_paused": self.tenant_paused,
            "spent_usd": str(self.already_spent_usd),
            "incurred_usd": str(self.incurred_usd),
            "refused": [
                {"key": k, "would_have_cost_usd": str(c), "reason": r}
                for k, c, r in self.refused
            ],
        }
