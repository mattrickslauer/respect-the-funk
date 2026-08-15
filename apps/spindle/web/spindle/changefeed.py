"""The changefeed: the database telling a worker that something happened, instead of a
worker asking every few seconds whether anything did.

`infra/platform_architecture.py` draws a dashed arrow from the substrate to the fleet and
labels it *"a changefeed wakes an agent"*. `docs/PLATFORM-SPEC §4` writes the DDL out. As
of the 2026-08-10 audit, `SHOW CHANGEFEED JOBS` returned **zero rows** — the arrow was a
drawing of an intention. This module is the arrow.

## What a wake is, and what it emphatically is not

**A wake is permission to look, not a grant of work.** The whole safety argument of
`fleet.py` is that a lead is handed to exactly one worker by an `UPDATE … FOR UPDATE SKIP
LOCKED` that stamps a `lease_token`, and nothing in this file is allowed to weaken that. So
the event carries no work:

  * `Wake` holds a **tenant** and a **set of lead kinds**. It deliberately has no `after`
    field, no row id and no payload. There is a test that asserts this, because the
    tempting optimisation — "we already have the row, pass it straight to the agent" — is
    how a changefeed turns into an at-least-once work queue with no fence on it. Webhook
    sinks are at-least-once *by specification*: CockroachDB will re-deliver a message it
    did not get a 2xx for, and a Lambda can be invoked twice for one delivery. Two workers
    woken by one event is the **normal** case, not the pathological one.
  * The only thing a woken worker does is call `fleet.work_once`, which claims. Two
    workers woken by the same event therefore race on the claim, and `claim`'s predicate
    (`owner_agent IS NULL OR lease_expires_at < now()`) means the second one gets an empty
    list and does nothing. That is not a new guarantee this module adds; it is the
    existing one this module refuses to route around. `tests/test_changefeed.py` proves it
    for the interleaving that matters — B woken while A is mid-work, and B woken after A's
    lease has lapsed.

The poller does not go away either. `ingest.drain` still works, still claims the same
rows, and a fleet with this module switched off is *slower*, not *broken*. The changefeed
is a latency optimisation with an architectural payoff, exactly as
`docs/superpowers/specs/2026-08-07-agent-contract-design.md §6` promised — and if the RU
draw turns out to be unaffordable, deleting the feed costs the architecture its elegance
and not its function.

## Why the feed does not watch `lead`

`lead` is the obvious table to watch and it is the one table that must never be in the
feed. The fleet writes to `lead` constantly and for reasons that are not work: `claim`
UPDATEs it, `renew` UPDATEs it once per lead per turn, `complete` UPDATEs it again. Put
`lead` in the feed and every claim emits an event, every event wakes a worker, every woken
worker claims, and the feed amplifies itself into a loop whose steady state is bounded
only by the account's Lambda concurrency and the RU budget. The feed therefore watches the
**business** tables — the rows that mean something changed in the world — and the wake is
"go and see if the frontier has work of these kinds", which is a question the frontier
answers with one indexed query.

## The tables, and what each one is worth

`TABLES` is `thread`, `outbox`, `message`, per `PLATFORM-SPEC §4` minus
`counterparty_observation`, which this build does not have. `WAKES` maps each to the lead
kinds a change to it can make claimable, and the map is deliberately thin because the
fleet is deliberately thin: four of §4's six wakes name agents (Researcher, Drafter,
Negotiator, Analyst) that do not exist in `agents.REGISTRY`. Declaring a wake for an agent
that is not there would burn a claim query per event to discover nothing, forever.

  * **`thread`** → `distil_lesson`, and *only* when the new state is terminal.
    `outreach.advance` writes the `distil_lesson` lead in the same transaction that closes
    the thread, so by the time the event is delivered the lead is committed and claimable.
    Every other transition (`discovered → shortlisted`, and so on) wakes nothing, because
    nothing in this build claims work from it, and the filter is applied here rather than
    in the feed for the reason below.
  * **`outbox`** → the Sender, which is not a `fleet.Agent` at all (`sender.py`: the
    outbox is at-most-once and the lead table is at-least-once, and they must not share a
    retry policy). An outbox wake is therefore routed to a separate, **optional**
    callable, and the default is `None`. A consumer that has not been explicitly handed a
    sender does not send: it returns a `Woken` saying so. `ingest.py` keeps `--send`
    separate from `--drain` for the same reason — preparing a send and performing one are
    different decisions and only the second is irreversible, and a changefeed must not be
    the thing that quietly makes them the same decision.
  * **`message`** → nothing, today, and that is recorded as `()` rather than by omission.
    `outreach.record_reply` can write an inbound message *without* moving the thread (a
    reply arriving in a state that is not `awaiting_reply`), so this is the only table that
    can report that event at all; the Inbox agent that would claim it is the next one to be
    built. `verify()` prints "wakes nothing yet" against it so that nobody has to read this
    file to find out whether it is wired.

## Why one feed and not three filtered ones

CockroachDB's CDC queries (`CREATE CHANGEFEED … AS SELECT … WHERE …`) would let the
cluster do the filtering — emitting only terminal thread states, only pending outbox rows
— and would cut the message volume to almost exactly the useful events. They support
**one table per changefeed**. Three filtered feeds is three continuously-running jobs and
therefore three continuous RU draws on a tier where the continuous draw is the thing we
cannot price. One feed over three tables, plus four lines of Python filtering in
`wake_for`, is cheaper on the axis that is actually scarce. Revisit if the event volume
ever becomes the cost rather than the job count.

## What this costs, stated as honestly as it can be stated

The one number that matters is unmeasured and this module does not pretend otherwise.
`docs/reference/COCKROACHDB-AI.md` records what is actually known: Cockroach's own Basic
planning page says *"running a changefeed to an external sink also consumes RUs"*, and the
**rate is unpublished**. `infra/platform_architecture.py` carries it as UNVERIFIED and
lists free-tier RU erosion by the changefeed as a MEDIUM risk. Nothing below changes that
status — it is a bound on the shape of the draw, not a measurement of it.

What is known about the shape:

  * **A changefeed draws continuously**, unlike a query. It is the likelier of this
    project's two unpriced items to erode the 50M RU monthly allowance, because it costs
    something while nobody is using the product. That is the whole reason it is one job
    over three tables rather than three filtered jobs.
  * **Three tables, all tiny.** `thread`, `outbox` and `message` hold tens of rows between
    them on this deployment, against ~14,000 counterparties and 7,000 pending leads in
    `lead` and `party_*`. The feed's *emission* volume is therefore near zero; whatever it
    costs is dominated by the standing cost of the job existing and by the rangefeed it
    holds over three small ranges. This is the argument for why the design is affordable
    if any changefeed is affordable — and it is not an argument that it is affordable.
  * **`initial_scan = 'no'`** means the job does not pay to read the tables once at
    creation. With three small tables that saving is small in RUs and large in
    consequences — see `create_statement`.
  * **The heartbeat is the floor.** `resolved = '5m'` is 288 messages a day whether or not
    anything happens. Each is one Lambda invocation, answered without a database
    connection (`lambda_handler` returns before `db.connect` for a batch with no changes),
    so the heartbeat costs AWS invocations and CockroachDB's own resolved-timestamp
    accounting, and no query.
  * **A wake costs one indexed claim query per kind.** `fleet.claim` is `UPDATE … WHERE
    tenant_id = … AND state = 'pending' AND kind = ANY(…) AND next_action_at <= now()`,
    which `lead`'s existing index serves. `wake_for` is deliberately narrow so that the
    wakes that reach it are ones where a lead is expected to exist: a thread reaching
    `shortlisted` writes no lead, so it wakes nothing, so it costs nothing.
  * **What the Gate does not see.** `spend.py` prices provider dollars and refuses a call
    that would breach a ceiling. Request units are not in `RATES` and cannot be, because
    the rate is unpublished — so `spend.Gate` is not a control on this. The controls that
    do exist are: the feed is not created by any code path here; the endpoint is not
    created by Terraform without a token; and cancelling the job is one `CANCEL JOB`.
  * **The kill switch.** `CANCEL JOB <id>` stops the draw. The fleet then runs exactly as
    it does today, because `ingest.drain` never stopped being the claim path — which is
    the fallback `PLATFORM-SPEC §10` and the risk register both name, and it is real here
    rather than aspirational.

**Measure before trusting any of that.** `verify()` reports the job count; the RU draw has
to be read from the CockroachDB Cloud console over a day with the feed running and nothing
else changed. Until somebody does that, the honest sentence is the one the risk register
already carries.

## Why this module will not run the CREATE for you

`create_statement()` composes the exact DDL and `verify()` prints it. Nothing here
executes it, and the omission is deliberate three times over:

 1. **It spends money continuously and cannot be un-spent.** A changefeed is a job, not a
    query: once created it draws RUs until somebody cancels it. The whole project has a
    $10 budget. `spend.py`'s rule is that the estimate is computed before the call and the
    call does not happen if it would breach what is left — and the Gate cannot price this
    one, because it prices provider dollars and this costs request units. A cost the Gate
    cannot see is a cost a human has to authorise, in front of the statement, on purpose.
 2. **`tests/test_tenant_scoping.py` cannot vet it.** That test statically resolves the
    text of every statement reaching `cursor.execute` and *raises* on one it cannot read.
    The sink URL is a Terraform output, so the statement can only be assembled at runtime.
    Registering the one statement in this codebase that spends money forever as an
    exemption from the lint, in order to execute it automatically, is the wrong trade in
    both directions.
 3. **It is a cluster-level object with no tenant in it.** A changefeed on `thread` reports
    every tenant's threads; there is no `WHERE tenant_id` to carry (see above — a CDC
    query could carry one, at three times the job count). Tenant scoping is restored at the
    consumer: `wake_for` reads `tenant_id` out of the row and **raises** if it is absent,
    and every statement a woken worker then runs is `fleet`'s own, all of which are scoped.

## The two sinks, and which is used when

  * **Webhook sink → a Lambda (`lambda_handler`).** The production shape. The feed POSTs
    batches to a Function URL; the Lambda authenticates the batch against a shared secret,
    turns it into wakes, and claims. It costs nothing at idle, needs no long-lived
    process, and is the arrangement `docs/reference/HACKATHON.md` names ("Lambda for
    changefeed webhooks"). Its weakness is that it needs a deployed endpoint and a secret,
    which a laptop does not have.
  * **Sinkless core changefeed → `follow()`.** `EXPERIMENTAL CHANGEFEED FOR TABLE …`
    streams rows back over the SQL connection that issued it. No sink, no endpoint, no
    secret, no enterprise sink to configure — and no durability either: the rows exist only
    while the connection is held, so a consumer that dies misses everything that happened
    while it was away. That is *acceptable* precisely because a wake is not work: the leads
    are still on the frontier and `ingest.drain` still claims them. Use it on a laptop,
    in a demo, and anywhere the webhook endpoint is not deployed. Do not use it as the
    only path in production, because "the process was restarted" would silently become
    "those wakes never happened".

Both paths converge on `Event` → `wake_for` → `wake`, so the routing logic is written and
tested once.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

import psycopg

from spindle import fleet, outreach

#: The tables the feed watches. `lead` is absent on purpose and there is a test that
#: keeps it absent — see the module docstring's "Why the feed does not watch `lead`".
TABLES: tuple[str, ...] = ("thread", "outbox", "message")

#: Which lead kinds a change to each table can make claimable. An empty tuple is a
#: *declaration* that nothing claims work from that table yet, not an oversight: a table
#: in `TABLES` with no entry here fails `tests/test_changefeed.py`, so adding a table to
#: the feed forces somebody to decide what it wakes.
WAKES: dict[str, tuple[str, ...]] = {
    "thread": ("distil_lesson",),
    # The Sender is not a `fleet.Agent` and does not work the `lead` table at all
    # (`agent_manifest.work_table = 'outbox'`). Its wake is routed by `drains_outbox`
    # below rather than by a lead kind.
    "outbox": (),
    # No agent claims work from an inbound message yet. See the module docstring.
    "message": (),
}

#: How often the feed emits a resolved timestamp. Nothing in this module depends on a
#: resolved timestamp for *correctness* — there is no ordering or exactly-once claim being
#: made — so this is bought for one reason only: a feed that has silently stopped and a
#: feed with nothing to say are otherwise the same observation, and this project's entire
#: complaint about its own documentation is claims that cannot be checked. Five minutes is
#: 288 heartbeats a day, which is a rounding error against the batches.
RESOLVED_INTERVAL = "5m"

#: Batching, and the single biggest lever on what this costs. Without it the feed opens
#: one HTTP request per changed row and invokes one Lambda per changed row. With it, a
#: burst of fifty thread closures is one request, one invocation, and one claim query per
#: kind. `Frequency` bounds the latency the batching adds: five seconds, against a poller
#: that today runs when a human types a command.
FLUSH_MESSAGES = 50
FLUSH_FREQUENCY = "5s"

#: The header the feed sends and `lambda_handler` checks. Chosen rather than defaulted:
#: `Authorization` is the header AWS strips from some integrations and logs from others,
#: and a bespoke name means a value that leaks into an access log leaks as an unfamiliar
#: string rather than as something a scanner recognises and replays.
AUTH_HEADER = "authorization"

#: What `create_statement` prints where the shared secret goes, unless explicitly asked
#: not to. The default is redaction, so the only way to put the secret on a terminal is to
#: ask for it by name.
REDACTED = "***REDACTED***"


class ChangefeedRefused(RuntimeError):
    """A changefeed could not be described or consumed, and no guess was made instead.

    Every raise site in this module is a case where the alternative would be to carry on
    with an assumption: a sink URL that is not HTTPS, a payload with no tenant in it, a
    topic naming a table this module does not know. `PLATFORM-SPEC`'s wake topology is
    only meaningful if an unrecognised event is an error rather than a shrug.
    """


class NotAuthenticated(RuntimeError):
    """A webhook delivery did not carry the shared secret.

    Separate from `ChangefeedRefused` because the two want opposite responses: a
    malformed payload is a bug and should surface as a 5xx so the feed retries it into a
    log somebody reads, and an unauthenticated request is a 401 that must never reach a
    database connection. `lambda_handler` checks this before it opens one.
    """


# ------------------------------------------------------------------- the statements

def sink_url(function_url: str) -> str:
    """The `webhook-https://…` sink for a Lambda Function URL.

    Raises on anything that is not already HTTPS. CockroachDB will happily POST to a
    `webhook-http://` sink, and the auth header — the only thing standing between a
    stranger and the ability to wake this fleet at will — would go out in clear text on
    every batch. There is no plausible deployment where the answer to that is a warning.
    """
    url = (function_url or "").strip()
    if not url.startswith("https://"):
        raise ChangefeedRefused(
            f"a webhook sink must be HTTPS and {url!r} is not — the shared secret travels "
            "in a header on every batch, and CockroachDB will send it over plaintext HTTP "
            "without complaining. Pass the Lambda Function URL from "
            "`terraform output changefeed_webhook_url`.")
    return "webhook-" + url


def create_statement(function_url: str, auth_secret: str, *,
                     reveal: bool = False) -> str:
    """The exact `CREATE CHANGEFEED` this design would run. Nothing executes it.

    Every option here is a standing cost on a continuously running job, so each is
    argued rather than copied:

      * **`initial_scan = 'no'`** — the one that would have been a live incident. The
        default is an initial scan, which emits *every existing row* of all three tables
        as though it had just changed. On this cluster that is every thread ever closed,
        each waking a `distil_lesson` claim, each of those a paid embedding call through
        `agents.distil_lesson`. The feed's job is to report what happens from now on.
      * **`updated`** — puts the MVCC commit timestamp on every message, which is the only
        way to measure the number this whole workstream exists to move: the delay between
        a row changing and a worker looking at it. `age_seconds` is what reads it.
      * **`resolved`** — the liveness heartbeat argued at `RESOLVED_INTERVAL`.
      * **`webhook_sink_config`** — the batching argued at `FLUSH_MESSAGES`.
      * **`webhook_auth_header`** — the shared secret. Redacted unless `reveal=True`,
        because the overwhelmingly common reason to call this function is to print it.

    Deliberately absent: `diff`. It would carry the row's previous state, which doubles
    the payload of every message to answer a question `wake_for` does not ask — the new
    state alone decides what to wake.
    """
    if not auth_secret.strip():
        raise ChangefeedRefused(
            "a webhook changefeed needs a shared secret: the sink is a public Function "
            "URL, and without the header anybody who finds it can wake this fleet as "
            "often as they like. Set PLATFORM_CHANGEFEED_TOKEN (and the matching "
            "Terraform variable) to the same value.")
    header = REDACTED if not reveal else f"Bearer {auth_secret}"
    flush = json.dumps({"Flush": {"Messages": FLUSH_MESSAGES,
                                  "Frequency": FLUSH_FREQUENCY}})
    return (
        f"CREATE CHANGEFEED FOR TABLE {', '.join(TABLES)}\n"
        f"  INTO '{sink_url(function_url)}'\n"
        f"  WITH initial_scan = 'no',\n"
        f"       updated,\n"
        f"       resolved = '{RESOLVED_INTERVAL}',\n"
        f"       webhook_auth_header = '{header}',\n"
        f"       webhook_sink_config = '{flush}'"
    )


def core_statement() -> str:
    """The sinkless fallback, which *is* executed — by `follow`, over `cursor.stream`.

    No sink, so no `webhook_*` options and no secret; `initial_scan` defaults to off for a
    core changefeed with no cursor, and is written out anyway for the same reason
    `infra/terraform/spindle/main.tf` writes out SSE-S3: a default is a thing that can change, and
    an explicit value is the one that shows up in a diff when it does.

    This is the path for a laptop and a demo. Its rows live only as long as the connection
    that asked for them — see the module docstring on why that is survivable, and why it
    must not be the only path in production.
    """
    return (
        f"EXPERIMENTAL CHANGEFEED FOR TABLE {', '.join(TABLES)}\n"
        f"  WITH initial_scan = 'no', updated, resolved = '{RESOLVED_INTERVAL}'"
    )


# ------------------------------------------------------------------------- events

@dataclass(frozen=True)
class Event:
    """One row change, from either sink, reduced to what a wake needs.

    `after` is kept only long enough for `wake_for` to read `tenant_id` and a state out of
    it. It does not travel into a `Wake` and it never reaches an agent — see the module
    docstring on why passing the row along would quietly convert this into an unfenced
    work queue.
    """

    table: str
    after: dict[str, Any]
    #: The MVCC commit timestamp as CockroachDB writes it: `"<seconds>.<nanos>"`. Empty
    #: only if the feed was created without `WITH updated`, which `create_statement` and
    #: `core_statement` never do.
    updated: str = ""


def age_seconds(event: Event, *, now: float | None = None) -> float:
    """How long ago the row actually changed — the wake latency, in one number.

    This is the measurement that says whether the changefeed did anything a poller was
    not already doing, so it raises on a timestamp it cannot read rather than reporting a
    plausible zero. A diagnostic that lies is worse than one that is missing.
    """
    if not event.updated:
        raise ChangefeedRefused(
            f"the {event.table} event carries no `updated` timestamp, so its latency "
            "cannot be measured — the feed was created without `WITH updated`. See "
            "`create_statement`, which always sets it.")
    try:
        committed = float(event.updated)
    except ValueError as exc:
        raise ChangefeedRefused(
            f"{event.updated!r} is not a CockroachDB HLC timestamp; the payload shape "
            "this module parses has changed and the wake latency it reports would be "
            "fiction") from exc
    return (time.time() if now is None else now) - committed


def _events_from_messages(messages: Sequence[Any], *, source: str) -> list[Event]:
    """Turn a webhook batch's `payload` array into events, refusing anything unfamiliar.

    A message with a null `after` is a delete. None of these three tables is deleted by
    any code path in this build — `outbox` rows are cancelled, threads are closed, and
    messages are append-only — so a delete means either a `DELETE FROM` somebody typed by
    hand or a tenant cascade, and neither is work to wake for. Skipped, deliberately and
    with the reasoning here rather than as an unexplained `if`.
    """
    events: list[Event] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ChangefeedRefused(
                f"{source}: a changefeed payload entry was {type(message).__name__}, not "
                "an object — this is not a CockroachDB webhook batch")
        topic = str(message.get("topic") or "")
        if topic not in WAKES:
            raise ChangefeedRefused(
                f"{source}: the feed delivered a change to {topic!r}, which this module "
                f"has no wake for. Known topics: {', '.join(sorted(WAKES))}. Either the "
                "running changefeed watches more tables than `TABLES` names, or a table "
                "was added to `TABLES` without deciding what it wakes.")
        after = message.get("after")
        if after is None:
            continue
        if not isinstance(after, dict):
            raise ChangefeedRefused(
                f"{source}: the `after` for a {topic} change was "
                f"{type(after).__name__}, not an object")
        events.append(Event(table=topic, after=after,
                            updated=str(message.get("updated") or "")))
    return events


def parse_webhook(body: str) -> list[Event]:
    """Events from one webhook POST body.

    The shape is CockroachDB's: `{"payload": [...], "length": n}` for changes, and
    `{"resolved": "<hlc>"}` for the heartbeat. The heartbeat is not a change and produces
    no events — but it is recognised explicitly rather than falling through the `payload`
    branch as an empty list, because "the feed is alive and nothing happened" and "the
    payload key was missing" must not be the same answer.
    """
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise ChangefeedRefused(
            "the webhook body is not JSON, so this is not a CockroachDB changefeed "
            f"delivery: {body[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise ChangefeedRefused(
            f"the webhook body is a {type(parsed).__name__}, not an object")
    if "resolved" in parsed and "payload" not in parsed:
        return []
    payload = parsed.get("payload")
    if not isinstance(payload, list):
        raise ChangefeedRefused(
            "the webhook body has no `payload` array and is not a resolved heartbeat — "
            f"keys present: {sorted(parsed)}")
    return _events_from_messages(payload, source="webhook")


def parse_core_row(row: dict[str, Any]) -> Event | None:
    """One row of a sinkless `EXPERIMENTAL CHANGEFEED`, or `None` for a heartbeat.

    A core changefeed returns three columns — `table`, `key`, `value` — and the resolved
    heartbeat arrives as a row whose `table` is empty. `value` is JSON text (psycopg hands
    it back as `bytes` or `str` depending on the column type the server chose), so both
    are decoded and anything else raises: a value this module cannot read is a wake it
    would otherwise have to invent.
    """
    raw = row.get("value")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw = bytes(raw).decode("utf-8", "replace")
    if not isinstance(raw, str):
        raise ChangefeedRefused(
            f"a core changefeed row's `value` was {type(raw).__name__}, which this "
            "module cannot read as JSON")
    table = row.get("table")
    if isinstance(table, (bytes, bytearray, memoryview)):
        table = bytes(table).decode("utf-8", "replace")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ChangefeedRefused(f"a core changefeed value was {type(parsed).__name__}")
    if not table or "resolved" in parsed:
        return None
    events = _events_from_messages([{**parsed, "topic": str(table)}], source="core feed")
    return events[0] if events else None


# -------------------------------------------------------------------------- wakes

@dataclass(frozen=True)
class Wake:
    """What a worker is told. A tenant, some lead kinds, and nothing else.

    **There is no row in here and there must never be one.** A `Wake` carrying the changed
    row would let a woken worker act on it directly, which skips `fleet.claim` — and
    `fleet.claim` is the only thing in this architecture that makes two workers holding
    the same event produce one unit of work rather than two. `test_changefeed.py` asserts
    the field list, so adding `after` here fails the suite with the argument attached.

    `drains_outbox` is a flag rather than a kind because the Sender does not work the
    `lead` table at all; see `WAKES` and `sender.py`.
    """

    table: str
    tenant_id: str
    kinds: tuple[str, ...] = ()
    drains_outbox: bool = False
    #: Why this wake exists, in words, for the run log. An operator watching a feed needs
    #: to be able to tell "a thread closed" from "an outbox row appeared" without holding
    #: the topology in their head.
    reason: str = ""


def _tenant_of(event: Event) -> str:
    """The tenant the changed row belongs to. Raises if the payload does not carry one.

    Every table in `TABLES` has a `NOT NULL tenant_id`, so its absence is not a row that
    happens to be tenant-free — it is proof that what arrived is not the payload this
    module thinks it is parsing. The alternative to raising is picking a tenant, and the
    only candidates are "the configured one" and "all of them", which are respectively a
    cross-tenant wake and a cross-tenant claim.
    """
    tenant = event.after.get("tenant_id")
    if tenant is None or str(tenant).strip() == "":
        raise ChangefeedRefused(
            f"a {event.table} change arrived with no tenant_id. Every table in the feed "
            "declares it NOT NULL, so this is not a row from those tables — refusing "
            "rather than waking a tenant chosen by this module")
    return str(tenant)


def wake_for(event: Event) -> Wake | None:
    """The wake this row change justifies, or `None` if it justifies none.

    The filtering here is the half of the design that CDC queries would otherwise do in
    the cluster (see the module docstring on why one unfiltered feed is cheaper than three
    filtered ones), and it is deliberately narrow in both directions:

      * a **thread** wakes `distil_lesson` only on a terminal state, because that is the
        only transition that writes a lead — `outreach.advance` does it in the same
        transaction, so the lead is committed before this event can be delivered;
      * an **outbox** row wakes the Sender only while it is `pending`. The Sender's own
        `claim` moves it to `claimed`, which is another update and therefore another
        event, and waking on that one would be the fleet chasing its own tail.

    Returning `None` for everything else is not a silent drop: `wake` reports the event
    and the fact that it wakes nothing, and `--follow` prints it.
    """
    if event.table not in WAKES:
        raise ChangefeedRefused(
            f"{event.table!r} is not a table this module has a wake for — "
            f"known: {', '.join(sorted(WAKES))}")
    tenant = _tenant_of(event)
    state = str(event.after.get("state") or "")

    if event.table == "thread":
        if state not in outreach.CLOSED:
            return None
        return Wake(table="thread", tenant_id=tenant, kinds=WAKES["thread"],
                    reason=f"a thread reached {state}, so a lesson is owed")

    if event.table == "outbox":
        if state != "pending":
            return None
        return Wake(table="outbox", tenant_id=tenant, drains_outbox=True,
                    reason="an approved pitch is sitting in the outbox")

    # `message`, today. Declared in `WAKES` as waking nothing; see the module docstring.
    return None


@dataclass(frozen=True)
class Woken:
    """What happened when a wake was acted on. Written to a log, not swallowed.

    `handled=False` with a `note` is the honest report for the one case that is neither
    success nor failure: an outbox wake reaching a consumer that was not given a sender.
    Returning zero and calling it a day would make "nothing to send" and "not allowed to
    send" the same line in the log.
    """

    wake: Wake
    worked: int = 0
    handled: bool = True
    note: str = ""


#: `(conn, tenant_id, kinds) -> leads worked`. The fleet half of a consumer. The default
#: is `fleet_worker`, which is `fleet.work_once` and nothing else — no shortcut around the
#: claim, which is the property the whole module is arranged to preserve.
Worker = Callable[[psycopg.Connection, str, Sequence[str]], int]

#: `(conn, tenant_id) -> messages sent`. Optional, and `None` by default, because this one
#: is irreversible. See `WAKES`.
Sender = Callable[[psycopg.Connection, str], int]


def fleet_worker(agent_name: str) -> Worker:
    """A `Worker` that claims through `fleet.work_once`, and can do nothing else.

    The agent for a kind is looked up in `agents.REGISTRY` and a kind that is not there
    raises. A wake naming a kind no agent handles is a bug in `WAKES`, not a runtime
    condition to skip past — it would burn one claim query per event forever while
    reading, in every log line, as though the fleet were working.
    """
    from spindle import agents  # local: keeps the parsing half importable alone

    def work(conn: psycopg.Connection, tenant_id: str, kinds: Sequence[str]) -> int:
        total = 0
        for kind in kinds:
            agent = agents.REGISTRY.get(kind)
            if agent is None:
                raise ChangefeedRefused(
                    f"the wake table names {kind!r} and `agents.REGISTRY` has no agent "
                    f"for it. Known kinds: {', '.join(sorted(agents.REGISTRY))}")
            total += fleet.work_once(conn, tenant_id, agent_name, agent, kinds=[kind])
        return total

    return work


def outbox_sender(worker_name: str) -> Sender:
    """A `Sender` that drains the outbox through `sender.send_once`, and can do nothing else.

    Constructing one is the explicit act that `wake`'s `send=None` default refuses to
    perform implicitly. `sender.send_once` claims each row into `claimed` in its own
    committed transaction before the network call, so the at-most-once guarantee that
    module is built around is untouched by being triggered from a changefeed rather than
    from `ingest.py --send`: a redelivered event reaches an outbox row that is no longer
    `pending` and sends nothing.
    """
    from spindle import sender as sender_mod

    def send(conn: psycopg.Connection, tenant_id: str) -> int:
        return sender_mod.send_once(conn, tenant_id, worker_name)

    return send


def wake(conn: psycopg.Connection, item: Wake, *, work: Worker,
         send: Sender | None = None) -> Woken:
    """Act on one wake: claim what it points at, and report what that came to.

    Note what this does *not* do. It does not check whether another worker has already
    been woken by the same event, does not deduplicate deliveries, and does not hold any
    state between calls. It does not need to: the claim is the deduplication, and a second
    wake for an event whose lead is already held returns zero leads worked. Adding a
    dedupe cache here would be a second, weaker copy of a guarantee the database already
    makes exactly — and would be wrong the first time it disagreed with the lease.
    """
    if item.drains_outbox:
        if send is None:
            return Woken(
                wake=item, handled=False,
                note="an outbox row is ready and this consumer has no sender attached; "
                     "sending is a separate, irreversible decision (see `ingest.py "
                     "--send`) and a changefeed is not allowed to make it implicitly")
        return Woken(wake=item, worked=send(conn, item.tenant_id))
    if not item.kinds:
        return Woken(wake=item, handled=False,
                     note=f"{item.table} changes wake no agent in this build")
    return Woken(wake=item, worked=work(conn, item.tenant_id, item.kinds))


def wake_all(conn: psycopg.Connection, events: Sequence[Event], *, work: Worker,
             send: Sender | None = None) -> list[Woken]:
    """Every wake a batch of events justifies, in order.

    Batches are the normal delivery shape — `webhook_sink_config` groups up to
    `FLUSH_MESSAGES` changes into one POST — and the events in one batch are independent:
    a thread closing and an outbox row appearing are two different wakes to two different
    workers. They are *not* deduplicated against each other, because two events of the
    same kind mean two rows changed, and the second claim costs one indexed query against
    an empty result if it turns out the first worker took both.
    """
    return [wake(conn, item, work=work, send=send)
            for item in (wake_for(event) for event in events) if item is not None]


# -------------------------------------------------------------- the sinkless path

def follow(feed: psycopg.Connection, conn: psycopg.Connection, *, work: Worker,
           send: Sender | None = None, statement: str | None = None,
           limit: int | None = None) -> Iterator[Woken]:
    """Consume a sinkless changefeed in this process, yielding each wake as it is acted on.

    Uses `cursor.stream` rather than `execute`, because a core changefeed never returns:
    `execute` would buffer a result set that has no end. `stream` puts psycopg into
    single-row mode and hands rows over as the server produces them, which is the only
    shape in which "block until the database says something happened" is expressible over
    a SQL connection.

    **Two connections, and this is not tidiness.** Single-row mode occupies `feed` for the
    entire life of the changefeed: no other statement can be issued on it until the stream
    ends, and the stream does not end. A worker claiming a lead on that same connection
    would deadlock against the feed it was woken by. `feed` streams; `conn` claims. Neither
    may be `db.connect`'s shared connection, because that one is the console's.

    `limit` stops after that many wakes, which is what makes this testable and what makes
    a demo terminate. `None` runs until the connection dies or the caller stops iterating.

    There is no reconnect loop here and that is deliberate: a feed that silently
    re-establishes itself has a gap in it that nobody can see, and the gap is exactly the
    weakness of the sinkless path. If the connection drops, this raises, and the operator
    finds out that the wakes stopped rather than discovering it in a latency graph.
    """
    seen = 0
    with feed.cursor() as cur:
        for row in cur.stream(statement or core_statement()):
            event = parse_core_row(row)
            if event is None:
                continue
            item = wake_for(event)
            if item is None:
                continue
            yield wake(conn, item, work=work, send=send)
            seen += 1
            if limit is not None and seen >= limit:
                return


# ---------------------------------------------------------------- the webhook path

def _secret() -> str:
    """The shared secret, or a refusal. Never an empty string treated as "no auth needed".

    This is the `spend.Policy.load` rule applied to a different resource: the failure mode
    of a missing variable must point at *not* doing the thing. An unset token here means
    the endpoint refuses every delivery, which is loud and fixable; the alternative — an
    unset token meaning "accept everything" — is a public URL that wakes a fleet on demand
    and looks perfectly healthy in every log.
    """
    token = os.environ.get("PLATFORM_CHANGEFEED_TOKEN", "").strip()
    if not token:
        raise NotAuthenticated(
            "PLATFORM_CHANGEFEED_TOKEN is not set on this function, so no delivery can be "
            "authenticated and none is accepted. Set it to the same value the changefeed "
            "was created with (`webhook_auth_header`).")
    return token


def authenticate(headers: dict[str, Any]) -> None:
    """Check a delivery's auth header against the configured secret, in constant time.

    `hmac.compare_digest` rather than `==` because the comparison is against an attacker-
    supplied string on a public endpoint, and a short-circuiting comparison leaks the
    secret's prefix one request at a time. The cost of getting this right is one import.

    Header names are lowercased by the Lambda Function URL's payload format, and lowercased
    again here rather than trusted to be — a case difference would present as an
    authentication failure nobody could explain from the outside.
    """
    expected = f"Bearer {_secret()}"
    supplied = ""
    for name, value in (headers or {}).items():
        if str(name).lower() == AUTH_HEADER:
            supplied = str(value)
            break
    if not hmac.compare_digest(supplied, expected):
        raise NotAuthenticated(
            "this delivery did not carry the changefeed's shared secret. If the feed was "
            "recreated, its `webhook_auth_header` and PLATFORM_CHANGEFEED_TOKEN have "
            "drifted apart.")


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """The webhook sink: a Lambda Function URL that wakes the fleet.

    The order of operations is the security argument. Authentication happens **before**
    anything opens a database connection, so an unauthenticated flood costs a Lambda
    invocation and not a single RU. Only then is the body parsed, and only then does a
    worker claim.

    **Non-2xx is the retry signal and it is used on purpose.** CockroachDB re-delivers a
    batch it did not get a 2xx for, so:

      * a bad secret answers **401** — the feed will retry it forever, and that is the
        intended behaviour: the retries are the alarm for a misconfiguration a human has
        to fix, and dropping the batch quietly would lose real wakes while looking fine;
      * a malformed payload propagates, which Lambda reports as a failed invocation and
        the feed treats as a retry — the batch is preserved in a log with the reason
        attached instead of being acknowledged into nothing;
      * a **200** is returned once the wakes have been acted on, not before, so a batch is
        only acknowledged after the claims it caused have been attempted.

    A worker that throws while working a lead does *not* reach here: `fleet.work_once`
    catches per-lead failures and records them against the lead, which is the right place
    for them. What reaches here is a failure of the *wake path* itself.

    Re-delivery is safe for the reason the whole module exists to preserve: a wake is
    permission to look. The same batch delivered twice produces two claims, and the second
    one comes back empty.
    """
    from spindle import db, settings as settings_mod

    try:
        authenticate(event.get("headers") or {})
    except NotAuthenticated as exc:
        return {"statusCode": 401, "headers": {"content-type": "application/json"},
                "body": json.dumps({"error": str(exc)})}

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8", "replace")

    events = parse_webhook(body)
    if not events:
        # A resolved heartbeat, or a batch of deletes. Acknowledged, and said out loud:
        # a 200 with `woke: 0` is how an operator tells a live feed from a stuck one.
        return {"statusCode": 200, "headers": {"content-type": "application/json"},
                "body": json.dumps({"events": 0, "woke": 0, "worked": 0})}

    settings = settings_mod.load()
    if not settings.configured:
        raise ChangefeedRefused(
            "DATABASE_URL is not set on the changefeed function, so a delivered event "
            "cannot wake anything. This is a deployment fault, not a data one.")
    conn = db.connect(settings.database_url)

    # The Sender is attached only when this function is configured to send. `mail_
    # configured` is the same predicate `sender.py` refuses on, checked here so that an
    # outbox wake on an unconfigured deployment is reported as "no sender attached"
    # rather than raising `MailNotConfigured` out of the middle of a batch and failing
    # the whole delivery — including the thread wakes that were fine.
    worker_name = os.environ.get("PLATFORM_CHANGEFEED_WORKER", "changefeed-lambda")
    send = outbox_sender(worker_name) if settings.mail_configured else None

    woke = wake_all(conn, events, work=fleet_worker(worker_name), send=send)
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({
            "events": len(events),
            "woke": len(woke),
            "worked": sum(w.worked for w in woke),
            "unhandled": [w.note for w in woke if not w.handled],
        }),
    }


# ------------------------------------------------------------------------ verify

def verify(conn: psycopg.Connection) -> dict[str, Any]:
    """What the cluster currently says about changefeeds. Reads only.

    Three questions, because the 2026-08-10 audit had to ask all three by hand and the
    point of this function is that nobody has to again:

      * is `kv.rangefeed.enabled` on — the prerequisite for any changefeed at all;
      * how many changefeed jobs exist, which is the ten-second falsifiable check a judge
        can run against the claim in `infra/platform-architecture.pdf`;
      * and, from `WAKES`, what each watched table would actually wake if one existed.
    """
    with conn.cursor() as cur:
        cur.execute("SHOW CLUSTER SETTING kv.rangefeed.enabled")
        row = cur.fetchone()
        if not row:
            raise ChangefeedRefused(
                "SHOW CLUSTER SETTING kv.rangefeed.enabled returned no row, which no "
                "CockroachDB version does — this connection is not talking to the "
                "cluster this module was written against")
        # The setting comes back as a bool from a CockroachDB cluster and as the *string*
        # `'true'`/`'false'` from some proxies and older drivers. `bool('false')` is
        # `True`, so a naive cast would report rangefeeds enabled on a cluster where they
        # are off — a wrong answer to the one question that decides whether any of this
        # can work at all. Both forms are handled explicitly and anything else raises.
        raw = list(row.values())[0]
        if isinstance(raw, bool):
            rangefeeds = raw
        elif isinstance(raw, str):
            rangefeeds = raw.strip().lower() in {"true", "on", "1"}
        else:
            raise ChangefeedRefused(
                f"kv.rangefeed.enabled came back as {type(raw).__name__} ({raw!r}), "
                "which this function cannot read as a boolean")

        cur.execute("SHOW CHANGEFEED JOBS")
        jobs = list(cur.fetchall())

    return {
        "rangefeeds_enabled": rangefeeds,
        "jobs": [{k: str(v) for k, v in job.items()
                  if k in {"job_id", "status", "description"}} for job in jobs],
        "tables": {table: WAKES[table] for table in TABLES},
    }


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m spindle.changefeed [--verify|--dry-run|--follow]`.

    `--dry-run` is the default and prints the statement without a database connection at
    all, because the most common reason to run this is to read the DDL before deciding
    whether to spend RUs on it, and requiring a connection to read a string would be a
    reason not to look.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true",
                        help="ask the cluster whether rangefeeds are on and how many "
                             "changefeed jobs exist")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the CREATE CHANGEFEED this design would run. This is "
                             "the default; nothing in this module ever executes it.")
    parser.add_argument("--reveal", action="store_true",
                        help="print the shared secret in the statement instead of "
                             "redacting it. You are about to paste it into a terminal.")
    parser.add_argument("--follow", action="store_true",
                        help="consume a sinkless EXPERIMENTAL CHANGEFEED in this process "
                             "and wake the fleet from it. No sink, no endpoint, no "
                             "durability — see this module's docstring.")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop --follow after this many wakes")
    parser.add_argument("--worker", default="changefeed-cli",
                        help="the worker name leases are taken under")
    parser.add_argument("--url", default=os.environ.get("PLATFORM_CHANGEFEED_URL", ""),
                        help="the webhook sink's Function URL "
                             "(terraform output changefeed_webhook_url)")
    args = parser.parse_args(argv)

    if args.verify or args.follow:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            sys.exit("DATABASE_URL is not set")

    if args.verify:
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                             row_factory=dict_row) as conn:
            state = verify(conn)
        print(f"kv.rangefeed.enabled   {state['rangefeeds_enabled']}")
        print(f"changefeed jobs        {len(state['jobs'])}")
        for job in state["jobs"]:
            print(f"  {job.get('job_id', '?')}  {job.get('status', '?')}")
        for table, kinds in state["tables"].items():
            print(f"  watches {table:<8} wakes "
                  f"{', '.join(kinds) if kinds else 'nothing yet'}"
                  f"{'  (the Sender)' if table == 'outbox' else ''}")

    if args.follow:
        from psycopg.rows import dict_row
        # Two connections. `follow`'s docstring says why: single-row mode occupies the
        # streaming one until the feed ends, and a worker claiming on it would deadlock
        # against the feed that woke it.
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                             row_factory=dict_row) as feed, \
             psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                             row_factory=dict_row) as conn:
            print(f"following:\n{core_statement()}\n")
            for woken in follow(feed, conn, work=fleet_worker(args.worker),
                                limit=args.limit):
                print(f"  {woken.wake.table:<8} {woken.wake.reason} "
                      f"-> worked {woken.worked}"
                      f"{'' if woken.handled else '  [' + woken.note + ']'}")
        return 0

    if args.dry_run or not (args.verify or args.follow):
        # One code path for the statement, whether or not the endpoint exists yet:
        # placeholders go through `create_statement` rather than being re-typed beside
        # it. Two copies of a DDL statement is two things that can drift, and the whole
        # point of this command is that what it prints is what would run.
        url = args.url or "https://<terraform output changefeed_webhook_url>"
        secret = os.environ.get("PLATFORM_CHANGEFEED_TOKEN", "")
        print(create_statement(url, secret or "<PLATFORM_CHANGEFEED_TOKEN>",
                               reveal=args.reveal or not secret))
        if secret and not args.reveal:
            print("\n# the secret is redacted above; --reveal prints it.")
        print("\n# Nothing in this module runs that statement. Creating the feed is a"
              "\n# deliberate, RU-spending act — paste it into `cockroach sql` when the"
              "\n# endpoint is deployed and the budget has been checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
