"""Money per track: what a placement was worth, and when the agent may back it further.

The system already bounds spending three ways — `spend.Policy` bounds the deployment,
`tenant_budget` bounds the label, `party_budget` bounds effort per artist. None of them
can say *this single is working, put more behind it*, because none of them is keyed on a
recording and none of them can move on its own.

This module is the one that can, and the whole of its design is an argument about not
being trusted:

  * **A raise is bounded by a number a person chose.** `recording_budget.ceiling_usd` is
    the highest `allocated_usd` may reach unattended. An agent that can raise its own
    budget without a stop does not have a budget.
  * **A raise is bounded by a policy.** `autonomy` decides whether this raise happens or
    goes in a queue, using the same estimate-versus-ceiling rule every other capability
    uses.
  * **A raise records the instant it was computed at.** Not here — in `decision`, the
    ledger `035_decision_ledger.sql` builds, whose `at_hlc` is
    `cluster_logical_timestamp()` from the deciding transaction. `replay()` reads it back
    through `budget_raise.decision_id` and time-travels to it, so the metrics and thread
    states can be read exactly as they stood. They do not survive otherwise:
    `party_metric` is re-observed, `thread.state` moves through fourteen values, and
    Friday cannot reconstruct Tuesday from either.

    The instant lives there and not here so that exactly one table can be wrong about
    when something happened. This module owns the *workflow* — the state machine, the two
    amounts, the valuation basis — and the ledger owns the *provenance*. `036`'s header
    argues the split; `035`'s argues how many ledger rows a raise writes, which is one if
    it ran unattended and two if a person was asked.

## What a placement is worth, and what it is not

`value_of` reads `party_metric`. It returns `None` — never a number — for a counterparty
with no audience measurement, and every caller is written around that `None` rather than
coalescing it away. This is the no-fallbacks rule at its most load-bearing: a default
audience of "average" applied to the 43,165 counterparties that currently carry no metric
at all would manufacture a valuation out of nothing and then spend real money against it.

The cost of that honesty is that a valuation is a **floor**, not a total, and every
`Valuation` says how many counterparties it had to exclude. A reader who sees
`unvalued=7` knows the number is low and knows by how many rows. `MEMORY.md`'s house rule
— mark what you did not do rather than let the absence read as completeness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from . import autonomy

#: Metric names that measure how many people a counterparty reaches, and the platform
#: they come from. Not every number in `party_metric` is an audience — a bitrate is not
#: reach — so this is an allowlist rather than "whatever is in the table".
#:
#: `clickcount` is Radio Browser's tune-in count. `radiobrowser.py` already *sorts* its
#: fetch by it and then discards the value; storing it is what makes most of the
#: counterparty index valuable at all.
AUDIENCE_METRICS: frozenset[str] = frozenset(
    {"clickcount", "fans", "listeners", "subscribers", "monthly_listeners"})

#: Dollars we are willing to have behind one track per thousand people its won placements
#: reach. A policy number, not a measurement, and it is here rather than in the
#: environment because changing it changes every future raise and should appear in a
#: diff with a reviewer on it.
#:
#: Deliberately small. This repository's entire spend budget is a hobby number (see
#: `plans.py`), and a rate that reads as ambitious would produce raises that
#: `tenant_budget` refuses anyway — a proposal nothing can act on is worse than a modest
#: one that lands.
USD_PER_THOUSAND_AUDIENCE = Decimal("0.50")

#: Thread states that mean the placement actually happened. A raise is justified by what
#: a track has *won*, not by what it has been pitched to: 'sent' is an outbox row and
#: costs money rather than earning any.
WON_STATES: tuple[str, ...] = ("agreed", "delivered", "verified", "closed_won")

#: The smallest raise worth recording. Without it a valuation that moves by a cent
#: proposes a raise on every run, and the queue an operator is supposed to read fills
#: with noise until they stop reading it.
MINIMUM_STEP_USD = Decimal("1.00")

#: `AS OF SYSTEM TIME` takes a constant expression, not a placeholder, so the HLC is
#: interpolated — and therefore validated first. Copied in shape from
#: `agents.shortlist_as_of`, which makes the same argument at length: this is the
#: difference between a replay and a SQL injection with a database-shaped hole in it.
#: Here the value comes from our own `DECIMAL` column rather than a request, which is a
#: reason to expect it to be well-formed and not a reason to skip checking.
_HLC = re.compile(r"\d{1,19}(\.\d{1,10})?")


class RaiseRefused(RuntimeError):
    """A raise was not proposed, and why. Carries the reason as a stable slug."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Valuation:
    """What one track's won placements are worth, and what could not be counted.

    `unvalued` is not a diagnostic afterthought — it is why `total_usd` must be read as a
    floor. A valuation of $12 across three counterparties with four more unvalued is a
    different fact from $12 across seven, and collapsing them loses the only signal that
    the number is incomplete.
    """

    recording_id: str
    total_usd: Decimal
    audience: int
    valued: int
    unvalued: int
    #: Which metric each counted counterparty was valued from, so a replay can be diffed
    #: per row rather than only on the total. A total that matches by coincidence while
    #: the composition changed is a replay that looks correct and is not.
    contributions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether every won placement carried an audience measurement."""
        return self.unvalued == 0

    def basis(self) -> dict[str, Any]:
        """What goes in `budget_raise.basis_json`. Numbers, not prose."""
        return {
            "total_usd": str(self.total_usd),
            "audience": self.audience,
            "valued": self.valued,
            "unvalued": self.unvalued,
            "rate_usd_per_thousand": str(USD_PER_THOUSAND_AUDIENCE),
            "won_states": list(WON_STATES),
            "contributions": self.contributions,
        }


def value_of(conn: psycopg.Connection, tenant_id: str, party_id: str, *,
             as_of: str = "") -> tuple[Decimal, int, str] | None:
    """`(dollars, audience, metric)` for one counterparty, or `None` if unmeasured.

    `None` is the entire point and every caller must handle it. There is no "assume the
    average" branch here and there must not be one: a manufactured audience becomes a
    manufactured valuation becomes real money moved for a reason that was invented.

    The most recent observation of the largest audience metric wins. Most is right rather
    than first because a counterparty measured on two platforms reaches the union of
    them, not the one we happened to record first; recent is right because an audience is
    a moving number and an old one is a worse answer than a new one.
    """
    _check_as_of(as_of)
    clause = f"AS OF SYSTEM TIME '{as_of}'" if as_of else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT metric, value FROM party_metric {clause}
                 WHERE tenant_id = %s AND party_id = %s AND metric = ANY(%s)
                 ORDER BY value DESC, observed_at DESC
                 LIMIT 1""",
            (tenant_id, party_id, list(AUDIENCE_METRICS)))
        row = cur.fetchone()
    if row is None:
        return None
    audience = int(row["value"])
    if audience <= 0:
        # A measured zero is a measurement, and it is worth zero. Returned rather than
        # treated as missing so the caller counts it as valued — "we asked and they
        # reach nobody" is knowledge, and `presence.absent` makes the same distinction.
        return Decimal("0"), 0, row["metric"]
    dollars = (Decimal(audience) * USD_PER_THOUSAND_AUDIENCE
               / Decimal(1000)).quantize(Decimal("0.000001"))
    return dollars, audience, row["metric"]


def _check_as_of(as_of: str) -> None:
    """Raise unless `as_of` is empty or a cluster logical timestamp.

    Validation only — the clause is built inline at each call site rather than returned
    from here, and that shape is not an accident. `test_tenant_scoping.py` statically
    resolves every statement in this package to prove it carries `tenant_id`, and it can
    trace an f-string assigned to a local but not a value returned from a function. A
    helper that returned the finished clause would make both statements below unreadable
    to it, and an unreadable statement is one the tenant-scoping guarantee stops covering.
    `agents.shortlist_as_of` is written this way for the same reason.

    Anchored via `fullmatch`, digits and one dot only, so what reaches the SQL cannot
    carry a quote, a semicolon or whitespace. That property is the whole security
    argument and is asserted by tests rather than left to inspection.
    """
    if as_of and not _HLC.fullmatch(as_of):
        raise ValueError(
            f"{as_of!r} is not a cluster logical timestamp like "
            "'1786644836824776765.0000000000'")


def valuation(conn: psycopg.Connection, tenant_id: str, recording_id: str, *,
              as_of: str = "") -> Valuation:
    """What this track's won placements are worth, optionally as they stood.

    Read-only, and that is structural rather than incidental: a read-write transaction
    cannot carry `AS OF SYSTEM TIME`, so a valuation that wrote anything could never be
    replayed. `agents.shortlist_as_of` splits itself from `agents.shortlist` over exactly
    this constraint.

    The join is track → campaign → thread → counterparty. `campaign.recording_id` is
    nullable because an artist-level push is a real campaign with no single record behind
    it; those campaigns simply do not match here, which is correct — their spend is not
    this track's spend.
    """
    _check_as_of(as_of)
    clause = f"AS OF SYSTEM TIME '{as_of}'" if as_of else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT DISTINCT t.counterparty_id AS party_id, p.name AS name
                  FROM thread t {clause}
                  JOIN campaign c
                    ON c.tenant_id = t.tenant_id AND c.id = t.campaign_id
                  JOIN party p
                    ON p.tenant_id = t.tenant_id AND p.id = t.counterparty_id
                 WHERE t.tenant_id = %s
                   AND c.recording_id = %s
                   AND t.state = ANY(%s)""",
            (tenant_id, recording_id, list(WON_STATES)))
        won = cur.fetchall()

    total = Decimal("0")
    audience = 0
    valued = 0
    unvalued = 0
    contributions: list[dict[str, Any]] = []
    for row in won:
        got = value_of(conn, tenant_id, str(row["party_id"]), as_of=as_of)
        if got is None:
            unvalued += 1
            contributions.append({
                "party_id": str(row["party_id"]), "name": row["name"],
                "valued": False, "metric": None, "audience": None, "usd": None})
            continue
        dollars, reach, metric = got
        total += dollars
        audience += reach
        valued += 1
        contributions.append({
            "party_id": str(row["party_id"]), "name": row["name"],
            "valued": True, "metric": metric, "audience": reach, "usd": str(dollars)})

    # Sorted so two runs over the same data produce byte-identical basis JSON. An
    # unordered list would make every replay comparison a set operation, and a diff that
    # has to be told to ignore ordering is a diff people stop trusting.
    contributions.sort(key=lambda c: c["party_id"])
    return Valuation(
        recording_id=str(recording_id),
        total_usd=total.quantize(Decimal("0.000001")),
        audience=audience, valued=valued, unvalued=unvalued,
        contributions=contributions)


def current(conn: psycopg.Connection, tenant_id: str,
            recording_id: str) -> dict[str, Any] | None:
    """This track's budget row, or `None` if it has none.

    `None` is not "unlimited" and not "zero" — it is "nobody has given this track a
    budget", which is the state every recording is in until somebody does. `propose_raise`
    refuses on it rather than inventing a ceiling, because a ceiling this module chose
    would be the exact thing `recording_budget.ceiling_usd` exists to prevent.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT allocated_usd, ceiling_usd, paused
                 FROM recording_budget
                WHERE tenant_id = %s AND recording_id = %s""",
            (tenant_id, recording_id))
        return cur.fetchone()


def propose_raise(conn: psycopg.Connection, tenant_id: str, recording_id: str, *,
                  written_by: str = "") -> dict[str, Any]:
    """Value the track, decide who may act, and either raise it or queue it.

    Everything happens in one transaction, and `cluster_logical_timestamp()` is read
    inside it. That is the same argument `agents.justify` makes: called separately under
    autocommit, the timestamp and the valuation would be two instants a few milliseconds
    apart, and a replay would reconstruct the metrics as they were very slightly before
    or after the read that actually happened. Near enough is not an audit trail.

    Returns what happened rather than raising on the ordinary outcomes, because "it went
    to a human" is a normal result and not an error. `RaiseRefused` is kept for the cases
    where no raise is *possible* — no budget row, paused, already at the ceiling — which
    a caller genuinely cannot proceed past.
    """
    with conn.transaction():
        budget = current(conn, tenant_id, recording_id)
        if budget is None:
            raise RaiseRefused(
                "this track has no budget row, so it has no ceiling to raise within; "
                "give it one before asking an agent to move it",
                reason="no_budget")
        if budget["paused"]:
            raise RaiseRefused(
                "this track's budget is paused, so somebody stopped it on purpose",
                reason="paused")

        allocated = Decimal(budget["allocated_usd"])
        ceiling = Decimal(budget["ceiling_usd"])
        if allocated >= ceiling:
            raise RaiseRefused(
                f"already at the ceiling a person set (${allocated} of ${ceiling}); "
                "raising it further is their decision, not this agent's",
                reason="at_ceiling")

        with conn.cursor() as cur:
            cur.execute("SELECT cluster_logical_timestamp() AS hlc")
            hlc = cur.fetchone()["hlc"]

        value = valuation(conn, tenant_id, recording_id)

        # The target is what the won placements justify, stopped at the ceiling. `min`
        # and never `max`: the ceiling is the person's number and this function's whole
        # licence to run unattended is that it cannot exceed it.
        target = min(value.total_usd, ceiling)
        step = target - allocated
        if step < MINIMUM_STEP_USD:
            raise RaiseRefused(
                f"the valuation justifies ${target}, which is ${step} above the current "
                f"${allocated} — under the ${MINIMUM_STEP_USD} step that makes a raise "
                "worth recording",
                reason="below_step")

        policy = autonomy.read(conn, tenant_id, "budget_raise")
        # The estimate being judged is the size of the raise: the money the agent is
        # asking to be allowed to commit. Not the valuation, which is what justifies it,
        # and not the resulting allocation, which includes money already approved.
        decision = autonomy.decide(policy, step)

        state = "applied" if decision.unattended else "awaiting_human"
        basis = value.basis()
        basis["autonomy"] = decision.summary()

        # `035_decision_ledger.sql`'s rule, and the whole reason this writes one row
        # here rather than two: the proposal is always a decision, the resolution is a
        # decision if and only if a person made one. Unattended, the agent values,
        # consults the policy and moves the money against a single timestamp — one act,
        # one row, `stage = 'applied'`. Queued, this is `proposed` and `resolve` writes
        # the answer later.
        stage = "applied" if decision.unattended else "proposed"
        actor = written_by or "budget_raise"
        summary = (f"budget for this recording {'raised' if decision.unattended else 'proposed to rise'} "
                   f"from ${allocated} to ${target}")

        # `inputs` carries the *check*, never the answer. Two scalars rather than one:
        # `total_usd` alone is a floor and would agree with itself in the case where the
        # composition changed completely — seven counterparties valued and none dropped
        # produces the identical figure to three valued and four dropped. `unvalued` is
        # what gives the comparison something to fail on. The per-counterparty breakdown
        # stays in `budget_raise.basis_json`, because a check as big as the answer has
        # become the answer.
        inputs = {
            "check": {"total_usd": str(value.total_usd), "unvalued": value.unvalued},
            "autonomy_mode": policy.mode,
            "unattended_ceiling_usd": str(policy.ceiling_usd),
            "step_usd": str(step),
        }

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO decision (tenant_id, kind, stage, at_hlc, subject_kind,
                                         subject_id, actor, summary, inputs)
                   VALUES (%s, 'budget_increase', %s, %s, 'recording', %s, %s, %s, %s)
                   RETURNING id""",
                (tenant_id, stage, hlc, recording_id, actor, summary, Jsonb(inputs)))
            decision_id = cur.fetchone()["id"]

            cur.execute(
                """INSERT INTO budget_raise
                       (tenant_id, recording_id, from_usd, to_usd, state,
                        decision_id, basis_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (tenant_id, recording_id, allocated, target, state, decision_id,
                 Jsonb(basis)))
            raise_id = cur.fetchone()["id"]

            if decision.unattended:
                # The allocation and the record of why it moved commit together. A raise
                # that landed with no row explaining it is exactly the thing this module
                # exists to make impossible.
                cur.execute(
                    """UPDATE recording_budget
                          SET allocated_usd = %s, written_by = %s, updated_at = now()
                        WHERE tenant_id = %s AND recording_id = %s""",
                    (target, written_by or "budget_raise", tenant_id, recording_id))

    return {
        "raise_id": str(raise_id),
        "decision_id": str(decision_id),
        "state": state,
        "from_usd": allocated,
        "to_usd": target,
        "step_usd": step,
        "decided_at_hlc": str(hlc),
        "unattended": decision.unattended,
        "reason": decision.reason,
        "valuation": value,
    }


def resolve(conn: psycopg.Connection, tenant_id: str, raise_id: str, *,
            approved: bool, approved_by: str) -> dict[str, Any]:
    """A person answers a queued raise.

    `approved_by` is required and checked here as well as by
    `budget_raise_resolution_is_attributed`, for the reason `autonomy.set_policy` gives:
    a constraint name is not an explanation, and the caller deserves the sentence.

    The raise is re-read inside the transaction and its state re-checked, so two operators
    clicking the same queued row at once cannot both apply it. `SERIALIZABLE` would catch
    the write conflict anyway; the explicit check turns a retry into a clear "no longer
    waiting", which is what `routes.approvals_approve` already does for messages.
    """
    if not approved_by.strip():
        raise ValueError("a resolved raise needs a name against it")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT recording_id, from_usd, to_usd, state
                     FROM budget_raise
                    WHERE tenant_id = %s AND id = %s""",
                (tenant_id, raise_id))
            row = cur.fetchone()
            if row is None:
                raise RaiseRefused("no such raise", reason="missing")
            if row["state"] != "awaiting_human":
                raise RaiseRefused(
                    f"this raise is {row['state']}, not waiting for anybody",
                    reason="not_waiting")

            # The person's answer is its own act at its own instant, so it gets its own
            # `decision` row rather than mutating the proposal's. A queued raise sits for
            # days; stamping the answer with the proposal's timestamp would say somebody
            # approved it before they had seen it.
            cur.execute("SELECT cluster_logical_timestamp() AS hlc")
            hlc = cur.fetchone()["hlc"]

            if not approved:
                # `refused` has no other trace anywhere. The cap did not move, so nothing
                # downstream records that a person ever considered moving it — and a
                # declined raise is the decision somebody is most likely to ask about.
                cur.execute(
                    """INSERT INTO decision (tenant_id, kind, stage, at_hlc,
                                             subject_kind, subject_id, actor, summary,
                                             inputs)
                       VALUES (%s, 'budget_increase', 'refused', %s, 'recording', %s,
                               %s, %s, %s)
                       RETURNING id""",
                    (tenant_id, hlc, row["recording_id"], approved_by.strip(),
                     f"declined raising this recording's budget from "
                     f"${row['from_usd']} to ${row['to_usd']}",
                     Jsonb({"raise_id": str(raise_id)})))
                refusal_id = cur.fetchone()["id"]
                cur.execute(
                    """UPDATE budget_raise
                          SET state = 'rejected', approved_by = %s,
                              resolution_decision_id = %s, resolved_at = now()
                        WHERE tenant_id = %s AND id = %s""",
                    (approved_by.strip(), refusal_id, tenant_id, raise_id))
                return {"raise_id": str(raise_id), "state": "rejected",
                        "resolution_decision_id": str(refusal_id)}

            # Re-read the ceiling now rather than trusting the one the proposal saw. A
            # queued raise can sit for days, and a person lowering the ceiling in the
            # meantime meant it — applying a stale target would let an old proposal walk
            # through a limit that was tightened precisely to stop it.
            budget = current(conn, tenant_id, str(row["recording_id"]))
            if budget is None:
                raise RaiseRefused("the budget this raise targets is gone",
                                   reason="no_budget")
            target = min(Decimal(row["to_usd"]), Decimal(budget["ceiling_usd"]))
            if target <= Decimal(budget["allocated_usd"]):
                raise RaiseRefused(
                    "the ceiling moved under this raise and it would no longer raise "
                    "anything", reason="ceiling_moved")

            cur.execute(
                """INSERT INTO decision (tenant_id, kind, stage, at_hlc, subject_kind,
                                         subject_id, actor, summary, inputs)
                   VALUES (%s, 'budget_increase', 'applied', %s, 'recording', %s, %s,
                           %s, %s)
                   RETURNING id""",
                (tenant_id, hlc, row["recording_id"], approved_by.strip(),
                 f"approved raising this recording's budget from ${row['from_usd']} "
                 f"to ${target}",
                 Jsonb({"raise_id": str(raise_id), "proposed_to_usd": str(row["to_usd"])})))
            approval_id = cur.fetchone()["id"]

            cur.execute(
                """UPDATE recording_budget
                      SET allocated_usd = %s, written_by = %s, updated_at = now()
                    WHERE tenant_id = %s AND recording_id = %s""",
                (target, approved_by.strip(), tenant_id, row["recording_id"]))
            cur.execute(
                """UPDATE budget_raise
                      SET state = 'applied', approved_by = %s, to_usd = %s,
                          resolution_decision_id = %s, resolved_at = now()
                    WHERE tenant_id = %s AND id = %s""",
                (approved_by.strip(), target, approval_id, tenant_id, raise_id))

    return {"raise_id": str(raise_id), "state": "applied", "to_usd": target,
            "resolution_decision_id": str(approval_id)}


def replay(conn: psycopg.Connection, tenant_id: str, raise_id: str) -> dict[str, Any]:
    """Re-run the valuation that caused a raise, against the facts as they stood.

    This is the question the whole design is built to answer: *what did this track look
    like when you put another two hundred dollars behind it?* The numbers that justified
    it are gone by the time anybody asks — `party_metric` is re-observed and
    `thread.state` has moved on — so the answer cannot come from today's tables.

    Four extra words of SQL against the ledger's `at_hlc` read them back at the exact
    logical instant the raise was computed. No snapshot table, no versioned copy of the
    metrics.

    `matched` is the part worth arguing for. The replay alone is a strong claim; on its
    own it is also unfalsifiable, because nothing checks it against what was recorded at
    the time. Comparing the replayed total against `basis_json` turns it into a claim
    that can fail — and a failure is informative rather than embarrassing. It means the
    valuation code changed under a stored decision, which is exactly the drift somebody
    should be told about. `outreach.justification` makes this argument for rankings.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.recording_id, r.from_usd, r.to_usd, r.state, r.basis_json,
                      r.approved_by, r.created_at, d.at_hlc, d.stage, d.actor,
                      d.inputs AS decision_inputs
                 FROM budget_raise r
                 JOIN decision d
                   ON d.tenant_id = r.tenant_id AND d.id = r.decision_id
                WHERE r.tenant_id = %s AND r.id = %s""",
            (tenant_id, raise_id))
        row = cur.fetchone()
    if row is None:
        raise RaiseRefused("no such raise", reason="missing")
    if row["basis_json"] is None:
        # Not a failure. A person typed this number and no valuation justified it. The
        # act still has an instant — `035` records the human as `actor` — but there is no
        # computation to re-run against it, and manufacturing one to replay would be the
        # fabrication `025` refuses. What is absent is the reason, not the timestamp.
        return {"raise_id": str(raise_id), "replayable": False,
                "why": "a person set this budget; no valuation is recorded to replay",
                "decided_at_hlc": str(row["at_hlc"]),
                "actor": row["actor"],
                "approved_by": row["approved_by"]}

    hlc = str(row["at_hlc"])
    try:
        then = valuation(conn, tenant_id, str(row["recording_id"]), as_of=hlc)
    except psycopg.errors.Error as exc:
        # A garbage-collected history is a specific, expected failure with a specific
        # remedy, and it must not read as "the valuation was wrong". `agents.py` draws
        # the same line around its own GC boundary.
        raise RaiseRefused(
            f"the cluster no longer holds history back to {hlc}: {exc}",
            reason="gc_boundary") from exc

    # Checked against `decision.inputs.check` — the ledger's two scalars — rather than
    # against `basis_json`. Both were written in one transaction and should agree, but
    # the ledger is the canonical record and is the thing an auditor reads; checking the
    # workflow row against itself would prove only that this module is self-consistent.
    #
    # Both scalars, never just the total. `total_usd` is a floor, because a counterparty
    # with no audience measurement is excluded rather than averaged — so three valued
    # with four dropped can total exactly what seven valued and none dropped totals. A
    # check on the figure alone would agree in precisely the case where the composition
    # changed completely, which reads as verification while verifying nothing.
    check = (row["decision_inputs"] or {}).get("check", {})
    matched = (str(then.total_usd) == str(check.get("total_usd"))
               and then.unvalued == check.get("unvalued"))
    return {
        "raise_id": str(raise_id),
        "replayable": True,
        "decided_at_hlc": hlc,
        "stage": row["stage"],
        "actor": row["actor"],
        "from_usd": Decimal(row["from_usd"]),
        "to_usd": Decimal(row["to_usd"]),
        "state": row["state"],
        "check": check,
        "recorded": row["basis_json"] or {},
        "replayed": then.basis(),
        #: True when the replay reproduces both recorded scalars. False is a finding, not
        #: an error — it means the valuation code moved under a stored decision, which is
        #: drift somebody should be told about.
        "matched": matched,
    }
