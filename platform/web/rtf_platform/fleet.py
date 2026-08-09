"""The fleet's one coordination primitive: claim a lead by lease, work it, write back.

`PLATFORM-SPEC §0` states the thesis this module is the whole implementation of:

> Agents do not call each other. They read shared memory, act, write the result back,
> and that write wakes the next agent.

There is no orchestrator here, no queue, no broker and no agent-to-agent call. There is
a table with a `next_action_at` and an `owner_agent`, and an `UPDATE … FOR UPDATE SKIP
LOCKED` that hands a row to exactly one worker. Everything else in the fleet is a
function that takes a claimed row and writes some other rows.

## Why a lease and not a lock

A lock is held by a process. When that process dies — an OOM, a Lambda timeout, a
`kill -9` mid-demo — the lock outlives it and the work is stranded until a human or a
supervisor notices. A lease is held by a *clock*: `lease_expires_at` passes and the row
becomes claimable again, by whoever asks next, with no supervisor in the topology at
all.

That is what makes the fleet restartable, and restartability is the demo's closing beat
in `PLATFORM-SPEC §8` — kill everything mid-run, start it again, watch every lead resume
from its row. It only works because the claim is a timestamp in the database rather than
state in a process.

## Why `SKIP LOCKED`

Without it, ten workers asking for work at once queue behind each other on the same rows
and the fleet runs at the speed of one worker. With it, each worker skips rows another
transaction is already touching and takes the next free ones. The lock and the data are
the same row, so there is no Redis to disagree with the database about who owns what —
`PLATFORM-SPEC §1`'s coordination row.

## Why failure is a backoff and not a retry loop

A lead that fails is rescheduled with exponential backoff and its `attempts` incremented.
Past `MAX_ATTEMPTS` it is parked in `failed` rather than retried forever. The failure
mode being avoided is a poisoned row — one lead that always throws, claimed and reclaimed
at full speed, burning the spend ceiling on an error that a human needs to look at. The
error text stays on the row so that human has something to look at.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Iterator, Sequence

import psycopg

from rtf_platform import spend

#: How long a claim is good for. Long enough that a slow agent does not lose its work
#: mid-flight, short enough that a dead worker's leads come back while somebody is still
#: watching. `PLATFORM-SPEC §3a` says five minutes; the agents here are much faster than
#: that, and a shorter lease makes the restart demo legible in a sixty-second clip.
LEASE_SECONDS = 120

#: Rows one worker takes per pass. Small: a big batch is a big lease, and a worker that
#: dies holding thirty leads strands all thirty for the full lease duration.
BATCH = 5

#: After this many failures a lead is parked rather than retried. A poisoned row that
#: retries forever spends real money on an error nobody has read.
MAX_ATTEMPTS = 5

#: Backoff schedule in seconds, indexed by attempt count. Past the end, the last value
#: repeats — but `MAX_ATTEMPTS` parks the lead before that matters.
BACKOFF = (30, 120, 600, 3600)


class LeadFailed(RuntimeError):
    """An agent could not do the work. Carries whether it is worth trying again.

    Distinguishing the two is the caller's job, not this module's: a 503 from a provider
    is transient and a malformed URL never will be, and only the agent knows which it
    just saw.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class LeaseLost(RuntimeError):
    """This worker's lease on a lead was gone by the time it tried to finish with it.

    `LEASE_SECONDS` bounds a `claim`, not a `fetch`: an HTTP call to a source adapter or
    an embedding provider can run long enough to outlive it, and a second worker can then
    legitimately claim the same lead while the first is still fetching. `complete`,
    `fail` and `_defer` each fence their `UPDATE … WHERE id = %s AND owner_agent = %s AND
    lease_expires_at > now()` and raise this the instant that matches zero rows, instead
    of returning as though nothing happened — a worker that has lost its lease and does
    not know it is exactly the silent-duplication failure this module exists to close.

    The row is no longer this worker's to touch. `work_once` catches this separately from
    `LeadFailed` and does not write to the lead at all — only to `agent_run`, to record
    whatever cost was actually incurred before the loss was discovered.
    """


@dataclass
class Outcome:
    """What one agent run produced, for `agent_run` and for the lead's next state.

    Counters rather than a log line, because `platform/README.md` and the console's
    `/runs` view both read these as columns. A run that wrote nothing and a run that
    never happened must not look the same.
    """

    summary: str = ""
    documents: int = 0
    facts: int = 0
    metrics: int = 0
    leads: int = 0
    dropped: int = 0
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")
    #: Leads to insert as a result of this run. This is the *only* way one agent
    #: reaches another: it writes a row, and the row is claimed by whoever handles
    #: that kind. No agent names another agent anywhere in this file.
    follow_on: list[dict[str, Any]] = field(default_factory=list)


Agent = Callable[[psycopg.Connection, dict[str, Any], spend.Gate], Outcome]
"""`(conn, lead, gate) -> Outcome`. Raise `LeadFailed` to reschedule or park the lead.

Use this shape only for an agent whose entire body is free of network I/O —
`agents.profile_party` is the only one. `work_once` runs this callable *inside* the same
transaction that writes `agent_run` and completes the lead, so anything it does — a
blocking HTTP call to a source adapter, a call to an embedding provider — would hold that
transaction open across the call. An agent that needs the network is a `NetworkAgent`
instead.
"""


@dataclass(frozen=True)
class NetworkAgent:
    """An agent split into a network fetch and a database write, because the two must not
    share a transaction.

    `fetch(conn, lead, gate)` runs first, with no transaction open. It may still read the
    database — `sources.enabled_for` looks up the manifest, `embedder`'s fetch reads the
    document it is about to chunk — because a read outside a transaction is just an
    autocommitted statement, not a held lock. What it returns is whatever `write` needs:
    a `sources.Harvest`, a batch of embedding vectors, anything already fetched and not
    going to change. `fetch` may raise `LeadFailed` or `spend.SpendRefused`, exactly as a
    plain `Agent` could.

    `write(conn, lead, gate, prepared)` runs second, inside the transaction `work_once`
    also uses for `record_run` and `complete`. It touches the database and nothing else —
    no adapter call, no embedding call — so holding a transaction around it costs nothing
    it was not already going to cost. This is what makes the fetch, the write, the run
    record and the lead's completion land together or not at all: a crash after the fetch
    but before the write commits leaves no trace of either, and the lease brings the lead
    back for the next worker to fetch again — repeating work, never duplicating it.
    """

    fetch: Callable[[psycopg.Connection, dict[str, Any], spend.Gate], Any]
    write: Callable[[psycopg.Connection, dict[str, Any], spend.Gate, Any], Outcome]


def backoff_seconds(attempts: int) -> int:
    """Seconds to wait before retrying a lead that has failed `attempts` times."""
    if attempts <= 0:
        return BACKOFF[0]
    return BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]


def claim(conn: psycopg.Connection, tenant_id: str, agent_name: str, *,
          kinds: Sequence[str], batch: int = BATCH,
          lease_seconds: int = LEASE_SECONDS) -> list[dict[str, Any]]:
    """Take up to `batch` claimable leads of these kinds, atomically.

    A lead is claimable when it is pending, its `next_action_at` has arrived, and either
    nobody owns it or the previous owner's lease has expired. That last clause is the
    whole restart story: nothing has to notice a worker died.

    `ORDER BY next_action_at` inside the subquery makes this a priority queue on time
    rather than on insertion order, which is what lets a backoff actually defer work
    instead of merely marking it.
    """
    if not kinds:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE lead
               SET owner_agent = %(agent)s,
                   lease_expires_at = now() + (%(lease)s || ' seconds')::INTERVAL,
                   updated_at = now()
             WHERE id IN (
                   SELECT id FROM lead
                    WHERE tenant_id = %(tenant)s
                      AND state = 'pending'
                      AND kind = ANY(%(kinds)s)
                      AND next_action_at <= now()
                      AND (owner_agent IS NULL OR lease_expires_at < now())
                    ORDER BY next_action_at
                    LIMIT %(batch)s
                    FOR UPDATE SKIP LOCKED)
         RETURNING id, tenant_id, scope_kind, party_id, recording_id, kind, mode,
                   adapter, target, platform, depth, reason, score, attempts,
                   cadence_seconds
            """,
            {"agent": agent_name, "lease": str(lease_seconds), "tenant": tenant_id,
             "kinds": list(kinds), "batch": batch},
        )
        return list(cur.fetchall())


def complete(conn: psycopg.Connection, lead: dict[str, Any], outcome: Outcome, *,
            agent_name: str) -> None:
    """Finish a lead, and insert whatever it decided should happen next.

    One transaction, so a follow-on lead cannot exist without the run that justified it
    and a completed lead cannot lose the work it spawned. This is the same argument
    `PLATFORM-SPEC §3b` makes for the outbox: the write that records the act and the
    write that schedules the consequence have to commit together or a crash between them
    invents or destroys work.

    A lead with a `cadence_seconds` goes back to `pending` for its next poll rather than
    to `done` — one nullable column is the whole difference between a crawler and a
    frontier, as migration 005 puts it.

    Fenced on `owner_agent` and `lease_expires_at`: `agent_name` must still be the owner,
    and the lease must still be live, or the `UPDATE` matches zero rows and this raises
    `LeaseLost` instead of returning. A lease that expired mid-fetch can be reclaimed and
    finished by someone else before this call runs — silently no-op'ing here is exactly
    how a lead ends up marked `done` twice, or marked by the wrong worker's data.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            if lead.get("cadence_seconds"):
                cur.execute(
                    """UPDATE lead
                          SET state = 'pending', owner_agent = NULL,
                              lease_expires_at = NULL, attempts = 0, last_error = '',
                              next_action_at = now() + (%s || ' seconds')::INTERVAL,
                              updated_at = now()
                        WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()""",
                    (str(lead["cadence_seconds"]), lead["id"], agent_name),
                )
            else:
                cur.execute(
                    """UPDATE lead
                          SET state = 'done', owner_agent = NULL,
                              lease_expires_at = NULL, last_error = '',
                              updated_at = now()
                        WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()""",
                    (lead["id"], agent_name),
                )
            if cur.rowcount == 0:
                raise LeaseLost(
                    f"lead {lead['id']} is no longer owned by {agent_name!r} — its "
                    "lease expired and it may already be claimed, or completed, by "
                    "another worker; not marking it done")
            for follow in outcome.follow_on:
                _insert_lead(cur, lead, follow)


def fail(conn: psycopg.Connection, lead: dict[str, Any], error: str,
         *, agent_name: str, permanent: bool = False) -> None:
    """Reschedule a failed lead with backoff, or park it once it has failed enough.

    The error text is written to the row rather than only logged, because the console's
    frontier view is where an operator finds out, and a CloudWatch log group is not.

    Fenced on `owner_agent` and `lease_expires_at`, exactly like `complete` — see its
    docstring. Raises `LeaseLost` rather than returning if this worker no longer owns
    the row by the time the failure is being recorded.
    """
    attempts = int(lead.get("attempts", 0)) + 1
    parked = permanent or attempts >= MAX_ATTEMPTS
    with conn.cursor() as cur:
        if parked:
            cur.execute(
                """UPDATE lead
                      SET state = 'failed', owner_agent = NULL, lease_expires_at = NULL,
                          attempts = %s, last_error = %s, updated_at = now()
                    WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()""",
                (attempts, error[:1000], lead["id"], agent_name),
            )
        else:
            cur.execute(
                """UPDATE lead
                      SET state = 'pending', owner_agent = NULL, lease_expires_at = NULL,
                          attempts = %s, last_error = %s,
                          next_action_at = now() + (%s || ' seconds')::INTERVAL,
                          updated_at = now()
                    WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()""",
                (attempts, error[:1000], str(backoff_seconds(attempts)), lead["id"],
                 agent_name),
            )
        if cur.rowcount == 0:
            raise LeaseLost(
                f"lead {lead['id']} is no longer owned by {agent_name!r} — its lease "
                "expired before the failure could be recorded against it; not "
                "touching it")


def _insert_lead(cur: psycopg.Cursor, parent: dict[str, Any],
                 follow: dict[str, Any]) -> None:
    """Insert a follow-on lead, ignoring one that already exists.

    `ON CONFLICT DO NOTHING` against `UNIQUE (tenant_id, target_hash)` is what makes an
    agent safe to re-run: the same discovery made twice produces one lead, so a retry
    after a partial failure does not fan out duplicate work. Idempotence lives in the
    constraint, not in the agent remembering to check.

    `scope_kind` is *derived* from which ids are present rather than defaulted, because
    migration 005's `lead_scope_shape` CHECK ties the two together and a caller supplying
    one without the other gets a constraint violation from inside a commit — far from the
    agent that chose the values. Deriving it means the shape is right by construction.
    """
    party = follow.get("party_id", parent.get("party_id"))
    recording = follow.get("recording_id", parent.get("recording_id"))
    if "scope_kind" in follow:
        scope = follow["scope_kind"]
    elif recording is not None:
        scope = "recording"
    elif party is not None:
        scope = "party"
    else:
        scope = "tenant"

    cur.execute(
        """INSERT INTO lead (tenant_id, scope_kind, party_id, recording_id, kind, mode,
                             adapter, target, target_hash, platform, parent_lead_id,
                             depth, reason, score, cadence_seconds, next_action_at)
           VALUES (%(tenant)s, %(scope)s, %(party)s, %(recording)s, %(kind)s, %(mode)s,
                   %(adapter)s, %(target)s, %(hash)s, %(platform)s, %(parent)s,
                   %(depth)s, %(reason)s, %(score)s, %(cadence)s, now())
           ON CONFLICT (tenant_id, target_hash) DO NOTHING""",
        {
            "tenant": parent["tenant_id"],
            "scope": scope,
            "party": party,
            "recording": recording,
            "kind": follow["kind"],
            "mode": follow.get("mode", "auto"),
            "adapter": follow.get("adapter", ""),
            "target": follow.get("target", ""),
            "hash": follow["target_hash"],
            "platform": follow.get("platform", ""),
            "parent": parent["id"],
            "depth": int(parent.get("depth", 0)) + 1,
            "reason": follow.get("reason", ""),
            "score": follow.get("score", 0.5),
            "cadence": follow.get("cadence_seconds"),
        },
    )


def record_run(conn: psycopg.Connection, lead: dict[str, Any], agent_name: str,
               outcome: Outcome, gate: spend.Gate, *, state: str, error: str,
               started: float) -> None:
    """Write the `agent_run` row.

    `PLATFORM-SPEC §2e`: this is not telemetry. It is the record that makes a fleet
    restartable and a decision explainable, and it is what `spend.spent_today` sums to
    decide whether the next call is allowed — so a run that is not recorded is a run
    that did not count against the ceiling.

    Written for failures too, with what they would have cost. A refusal that leaves no
    row is indistinguishable from a call that never happened.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO agent_run (tenant_id, party_id, agent_kind, lead_id, state,
                                      summary, error, source, calls, documents, facts,
                                      metrics, leads, dropped, tokens_in, tokens_out,
                                      cost_micro_usd, refused_json, duration_ms, ended_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, now())""",
            (
                lead["tenant_id"], lead.get("party_id"), agent_name, lead["id"], state,
                outcome.summary[:1000], error[:1000], lead.get("adapter", ""),
                outcome.calls, outcome.documents, outcome.facts, outcome.metrics,
                len(outcome.follow_on), outcome.dropped,
                outcome.tokens_in, outcome.tokens_out,
                int(outcome.cost_usd * 1_000_000),
                psycopg.types.json.Jsonb(gate.summary()),
                int((time.monotonic() - started) * 1000),
            ),
        )


def _writer(conn: psycopg.Connection, agent: Agent | NetworkAgent,
           lead: dict[str, Any], gate: spend.Gate) -> Callable[[], Outcome]:
    """Do whatever network I/O the agent needs, with no transaction open, and return a
    zero-argument callable that performs its database writes.

    The callable must only be invoked from inside the transaction that also writes
    `agent_run` and completes the lead — that is the whole point of splitting the two:
    `fetch` can block on an HTTP response for as long as it needs to without a
    transaction sitting open on the fleet's one connection, and everything the returned
    callable does is a database write, so wrapping it costs nothing extra.
    """
    if isinstance(agent, NetworkAgent):
        prepared = agent.fetch(conn, lead, gate)
        return lambda: agent.write(conn, lead, gate, prepared)
    return lambda: agent(conn, lead, gate)


def _reacquire(conn: psycopg.Connection, lead: dict[str, Any], agent_name: str) -> None:
    """Re-validate ownership of a lead, and lock its row, before the write phase touches
    anything.

    Not required for the correctness `complete`'s own fence already provides — that
    fence, checked at the end of the same transaction `_reacquire` opens, is what
    actually stops a lease-losing worker's writes from becoming durable: raising there
    rolls back the *whole* transaction, including everything the write phase already did.
    This check exists for a narrower, still-real reason. Without it, a worker whose lease
    already expired before `fetch` returned still runs its entire write phase — every
    `INSERT` an agent can produce — only to discover the loss and roll all of it back on
    the very last statement. That is real, wasted database work, every single time it
    happens. `FOR UPDATE` also takes a row lock that makes `claim`'s `FOR UPDATE SKIP
    LOCKED` skip this row for as long as this transaction holds it open, which closes the
    much narrower window — the write phase only, typically milliseconds — during which a
    second worker could claim it while this one is still writing. The long pole, the
    network fetch, already ran with no transaction and no lock at all, by design, and
    nothing here changes that: a lease lost *during* `fetch` is caught here, at the start
    of the very next statement, not prevented.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM lead
                WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()
                FOR UPDATE""",
            (lead["id"], agent_name),
        )
        if cur.fetchone() is None:
            raise LeaseLost(
                f"lead {lead['id']} is no longer owned by {agent_name!r} — its lease "
                "expired before any write was attempted; discarding this run without "
                "writing anything")


def _record_lease_lost(conn: psycopg.Connection, lead: dict[str, Any], agent_name: str,
                       gate: spend.Gate, started: float, exc: LeaseLost) -> None:
    """Record what happened when a lease was gone before a run could finish.

    Runs in a transaction of its own — whichever transaction discovered the loss has
    already rolled back by the time this is called. The lead itself is not touched: it
    belongs to whoever holds it now, and writing to it here would just be this same race
    again. `gate.incurred_usd`, though, is real money the network fetch already spent
    before the loss was discovered, and it must land in `agent_run` regardless —
    `spend.spent_today` sums that column, and a ceiling that only sees the runs that
    happened to finish is a ceiling the next retry can quietly walk through.
    """
    with conn.transaction():
        record_run(conn, lead, agent_name, Outcome(cost_usd=gate.incurred_usd), gate,
                   state="lease_lost", error=str(exc), started=started)


def work_once(conn: psycopg.Connection, tenant_id: str, agent_name: str,
              agent: Agent | NetworkAgent, *, kinds: Sequence[str],
              batch: int = BATCH) -> int:
    """Claim a batch, run the agent over each lead, write everything back.

    Returns how many leads were worked, so a caller can loop until it returns zero and
    know the frontier is drained rather than guessing at a sleep.

    Each lead is independent: one that throws is failed and the rest still run. A batch
    that aborts wholesale on the first bad row is how one malformed target stops a fleet.

    What this guarantees: the write phase, `record_run` and `complete`/`fail`/`_defer`
    commit together, in one `with conn.transaction()` per lead. `db.py` opens the
    connection in autocommit, so without this each of those was its own commit, and that
    gap is exactly what produced three `agent_run` rows with `state = 'ok'` for one lead
    before it finally reached `done`, and three duplicate `party_metric` rows alongside
    them — a crash, or a lost lease, between "the write ran" and "the lead is marked
    done" cannot leave a durable write with no completed lead: either the whole span
    lands or none of it does.

    What this does *not* guarantee: that the write phase only ever runs once per lead.
    `fetch` can outlive its own lease — `embed_batch` and the source adapters make one
    HTTP call per batch, and a big one can run past `LEASE_SECONDS` — and a second worker
    can legitimately claim the same lead while the first is still fetching. When that
    happens, `complete`/`fail`/`_defer` fence on `owner_agent` and `lease_expires_at`; the
    loser of the race finds its `UPDATE` matches zero rows, raises `LeaseLost`, and its
    entire write-phase transaction rolls back — so no duplicate row lands in the
    database. But the loser's `fetch` already ran and, for a paid provider, already spent
    money before that discovery; this function cannot prevent that fetch from happening,
    only make sure its cost is still recorded (`state = 'lease_lost'` in `agent_run`,
    via `gate.incurred_usd`) and that its writes never land.

    That transaction never spans a network call, because `_writer` runs the agent's fetch
    phase (if it has one) before the transaction opens. Only the write phase — database
    statements, nothing else — sits inside it alongside `record_run` and `complete`.
    """
    leads = claim(conn, tenant_id, agent_name, kinds=kinds, batch=batch)
    for lead in leads:
        started = time.monotonic()
        gate = spend.Gate.open(conn, str(lead["tenant_id"]))
        try:
            write = _writer(conn, agent, lead, gate)
            with conn.transaction():
                _reacquire(conn, lead, agent_name)
                outcome = write()
                record_run(conn, lead, agent_name, outcome, gate,
                           state="ok", error="", started=started)
                complete(conn, lead, outcome, agent_name=agent_name)
        except LeaseLost as exc:
            _record_lease_lost(conn, lead, agent_name, gate, started, exc)
        except LeadFailed as exc:
            try:
                with conn.transaction():
                    record_run(conn, lead, agent_name, Outcome(cost_usd=gate.incurred_usd),
                               gate, state="failed", error=str(exc), started=started)
                    fail(conn, lead, str(exc), agent_name=agent_name,
                        permanent=exc.permanent)
            except LeaseLost as lost:
                _record_lease_lost(conn, lead, agent_name, gate, started, lost)
        except spend.SpendRefused as exc:
            # Not a failure of the work — a decision not to pay for it. Reschedule
            # rather than counting it against `attempts`, because raising the ceiling
            # should let it run, not leave it parked at four strikes.
            try:
                with conn.transaction():
                    record_run(conn, lead, agent_name, Outcome(cost_usd=gate.incurred_usd),
                               gate, state="refused", error=str(exc), started=started)
                    _defer(conn, lead, agent_name=agent_name)
            except LeaseLost as lost:
                _record_lease_lost(conn, lead, agent_name, gate, started, lost)
        except Exception as exc:  # noqa: BLE001 — an agent must not take the fleet down
            try:
                with conn.transaction():
                    record_run(conn, lead, agent_name, Outcome(cost_usd=gate.incurred_usd),
                               gate, state="error", error=repr(exc), started=started)
                    fail(conn, lead, repr(exc), agent_name=agent_name)
            except LeaseLost as lost:
                _record_lease_lost(conn, lead, agent_name, gate, started, lost)
    return len(leads)


def _defer(conn: psycopg.Connection, lead: dict[str, Any], *, agent_name: str) -> None:
    """Put a lead back without counting a failure against it.

    Fenced exactly like `complete` and `fail` — see `complete`'s docstring. Raises
    `LeaseLost` rather than returning if this worker no longer owns the row.
    """
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE lead
                  SET state = 'pending', owner_agent = NULL, lease_expires_at = NULL,
                      next_action_at = now() + INTERVAL '10 minutes', updated_at = now()
                WHERE id = %s AND owner_agent = %s AND lease_expires_at > now()""",
            (lead["id"], agent_name),
        )
        if cur.rowcount == 0:
            raise LeaseLost(
                f"lead {lead['id']} is no longer owned by {agent_name!r} — its lease "
                "expired before the refusal could be deferred; not touching it")


@contextmanager
def worker(conn: psycopg.Connection, tenant_id: str, agent_name: str) -> Iterator[None]:
    """Release this worker's leases on the way out of a clean shutdown.

    Purely an optimisation — the lease expiry already covers an unclean exit, which is
    the case that matters. This just means a Ctrl-C during a demo does not leave rows
    looking claimed for two minutes.
    """
    try:
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE lead SET owner_agent = NULL, lease_expires_at = NULL
                        WHERE tenant_id = %s AND owner_agent = %s AND state = 'pending'""",
                    (tenant_id, agent_name),
                )
        except Exception:  # noqa: BLE001 — shutdown must not raise over a tidy-up
            pass
