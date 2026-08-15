"""What the agent may do without asking, and what it must queue for a person.

One question, asked the same way by every capability: *this action will cost about $X —
do I do it, or does a human decide?* Before this module the answer was hardcoded and
opposite in the two places it mattered. `sender.py` refuses any message a person has not
approved, so outreach could never be unattended. `spend.Gate` refuses over a ceiling and
has no way to ask anybody, so spending could never be attended. Neither was chosen; each
was the only shape the module it lived in happened to grow.

The policy lives in `autonomy`, one row per (tenant, capability), and
`036_autonomy_and_track_budget.sql` argues the schema. What is worth repeating here is
the part callers get wrong:

**A missing row means `human`.** Not "unconfigured", not "inherit something". A tenant
nobody has set a policy for asks a person about everything, which is the only default
whose failure mode is inaction. `MEMORY.md`'s no-fallbacks rule bars *silent* defaults
that hide an error — this one is stated, tested, and rendered in the console as "asks
every time" rather than as a blank.

**An unknown capability raises.** It does not return `human`. Denying by misspelling is
the safe direction and an unreadable one: the caller sees actions being queued forever
and the policy row it meant to read sitting right there looking correct. A typo should
be a stack trace at the call site, not a mystery in a queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

#: The closed set from `autonomy_capability_known`. Duplicated from the CHECK rather
#: than read from it, and the duplication is deliberate: the database refuses a bad
#: write, and this refuses a bad *read* — `policy()` for a capability that does not
#: exist would otherwise return the default `human` forever and look like configuration
#: nobody had got round to.
CAPABILITIES: frozenset[str] = frozenset(
    {"outreach", "spend", "content", "budget_raise"})

#: Ask a person every time, whatever it costs.
HUMAN = "human"
#: Act unattended at or under `ceiling_usd`; ask above it.
AUTO = "auto"
#: Never ask. Has to be typed — see the migration on why a very large ceiling is not
#: an acceptable way to spell this.
ALWAYS = "always"

MODES: frozenset[str] = frozenset({HUMAN, AUTO, ALWAYS})


class UnknownCapability(KeyError):
    """A capability name that is not in `CAPABILITIES`.

    A `KeyError` subclass because that is what it is — a lookup of something absent —
    and a named one so a caller that genuinely wants to probe can catch this without
    swallowing every other `KeyError` in the block.
    """


@dataclass(frozen=True)
class Policy:
    """One tenant's answer for one capability.

    Frozen because a decision is made against the policy as read, and a caller mutating
    it between `read` and `decide` would produce a decision no stored row justifies.
    """

    capability: str
    mode: str
    ceiling_usd: Decimal
    #: False when no row existed and this is the default-deny policy. Kept because the
    #: console needs to say "asks every time (not configured)" differently from "asks
    #: every time (somebody chose that)", and because a test asserting default-deny
    #: should be able to tell that it got the default rather than a lucky row.
    configured: bool = True

    @property
    def unattended_possible(self) -> bool:
        """Whether any action at all could run unattended under this policy.

        `human` never acts alone. `auto` with a positive ceiling might — and the ceiling
        is guaranteed positive by `autonomy_auto_ceiling_positive`, so this is not
        checking for the zero case, it is stating the rule the CHECK enforces.
        """
        return self.mode in (AUTO, ALWAYS)


@dataclass(frozen=True)
class Decision:
    """What to do with one proposed action, and why.

    `reason` is a stable slug, not a sentence, for the same reason `SpendRefused` carries
    one: it lands in `agent_run` and in the raise record, and a message that gets reworded
    breaks every query counting how often a thing happened.
    """

    #: True to act now. False to queue it for a person. There is no third answer — a
    #: refusal is `spend.Gate`'s job and a different question; this module only ever
    #: decides *who* acts, never whether the action is affordable.
    unattended: bool
    reason: str
    policy: Policy
    estimate_usd: Decimal

    @property
    def queued(self) -> bool:
        return not self.unattended

    def summary(self) -> dict[str, Any]:
        """What to record next to the action. Decisions are data, not a log line."""
        return {
            "capability": self.policy.capability,
            "mode": self.policy.mode,
            "configured": self.policy.configured,
            "ceiling_usd": str(self.policy.ceiling_usd),
            "estimate_usd": str(self.estimate_usd),
            "unattended": self.unattended,
            "reason": self.reason,
        }


def _check_capability(capability: str) -> None:
    if capability not in CAPABILITIES:
        raise UnknownCapability(
            f"{capability!r} is not an autonomy capability. Known: "
            f"{', '.join(sorted(CAPABILITIES))}")


def read(conn: psycopg.Connection, tenant_id: str, capability: str) -> Policy:
    """This tenant's policy for one capability, or the default-deny one.

    A single-row primary-key read, so it is cheap enough to do per action rather than
    per run. That matters more than it looks: a policy cached for the length of a run is
    a policy an operator cannot tighten while a long run is in flight, which is exactly
    when they would want to. `spend.Policy` makes the same argument about not caching a
    kill switch across a process.
    """
    _check_capability(capability)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mode, unattended_ceiling_usd FROM autonomy "
            "WHERE tenant_id = %s AND capability = %s",
            (tenant_id, capability))
        row = cur.fetchone()
    if row is None:
        return Policy(capability=capability, mode=HUMAN, ceiling_usd=Decimal("0"),
                      configured=False)
    return Policy(capability=capability, mode=row["mode"],
                  ceiling_usd=Decimal(row["unattended_ceiling_usd"]))


def decide(policy: Policy, estimate_usd: Decimal) -> Decision:
    """Act now, or queue for a person?

    Pure, and takes the policy rather than a connection, so the rule can be tested
    exhaustively without a cluster and so a caller that already read the policy does not
    read it twice inside one transaction.

    The comparison is `<=`, not `<`: a ceiling of five dollars permits a five-dollar
    action. A ceiling that silently excludes its own value is the kind of off-by-one
    that surfaces as an operator swearing the number is set correctly.

    A negative estimate is refused rather than treated as free. Nothing in `spend.py`
    can produce one — `estimate` sums non-negative rates — so a negative here means the
    caller computed it some other way and has a sign error, and letting it through would
    make every action unattended.
    """
    if estimate_usd < 0:
        raise ValueError(
            f"a negative estimate ({estimate_usd}) cannot be judged; "
            "an action that pays us is not an action this module knows how to allow")

    if policy.mode == ALWAYS:
        return Decision(unattended=True, reason="always", policy=policy,
                        estimate_usd=estimate_usd)
    if policy.mode == HUMAN:
        return Decision(
            unattended=False,
            reason="not_configured" if not policy.configured else "human",
            policy=policy, estimate_usd=estimate_usd)
    if policy.mode == AUTO:
        if estimate_usd <= policy.ceiling_usd:
            return Decision(unattended=True, reason="under_ceiling", policy=policy,
                            estimate_usd=estimate_usd)
        return Decision(unattended=False, reason="over_ceiling", policy=policy,
                        estimate_usd=estimate_usd)

    # Unreachable while `autonomy_mode_known` holds. Loud rather than defaulting to
    # `human`, because a mode this code does not understand means the schema moved and
    # somebody needs to know, not a system that quietly starts queueing everything.
    raise ValueError(
        f"{policy.mode!r} is not a known autonomy mode ({', '.join(sorted(MODES))}); "
        "the schema and this module disagree")


def allows(conn: psycopg.Connection, tenant_id: str, capability: str,
           estimate_usd: Decimal) -> Decision:
    """`read` then `decide`, for the common caller that wants one answer.

    Kept as a thin pair rather than the only entry point so that a caller inside a
    transaction that already holds the policy — `budgets.propose_raise` does — is not
    forced into a second read to get a decision.
    """
    return decide(read(conn, tenant_id, capability), estimate_usd)


def set_policy(conn: psycopg.Connection, tenant_id: str, capability: str, *,
               mode: str, ceiling_usd: Decimal = Decimal("0"),
               written_by: str) -> Policy:
    """Write a policy, attributed.

    `written_by` is keyword-only and has no default on purpose. This is the row a dispute
    about an unattended action is settled from, and a blank name in it makes the row
    useless for the one job it has. `tenant_budget.written_by` exists for the same reason
    and is defaulted in the schema only because migration 033 had rows to backfill.

    The mode and ceiling are validated here as well as by the CHECK constraints. Not
    redundancy for its own sake: a `CheckViolation` names a constraint, and the caller
    that wrote `auto` with no ceiling deserves the sentence explaining that `auto` with a
    zero ceiling is `human` spelled twice, not a constraint name to go and look up.
    """
    _check_capability(capability)
    if mode not in MODES:
        raise ValueError(f"{mode!r} is not an autonomy mode ({', '.join(sorted(MODES))})")
    if not written_by.strip():
        raise ValueError("an autonomy policy needs a name against it")
    if mode == AUTO and ceiling_usd <= 0:
        raise ValueError(
            "mode 'auto' with a ceiling of zero is 'human' written a second way; "
            "say 'human' if a person should decide everything")
    if mode != AUTO and ceiling_usd != 0:
        raise ValueError(
            f"a ceiling is only read under mode 'auto'; {mode!r} with a ceiling of "
            f"{ceiling_usd} would look like a limit and be none")

    with conn.cursor() as cur:
        cur.execute(
            """UPSERT INTO autonomy
                   (tenant_id, capability, mode, unattended_ceiling_usd,
                    written_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, now())""",
            (tenant_id, capability, mode, ceiling_usd, written_by.strip()))
    return Policy(capability=capability, mode=mode, ceiling_usd=Decimal(ceiling_usd))
