"""The one property a changefeed must not break: waking is not claiming.

    cd platform/web && .venv/bin/python -m pytest tests/test_changefeed.py -q

`fleet.py` earns the right to run agents in parallel with exactly one mechanism — an
`UPDATE … FOR UPDATE SKIP LOCKED` that stamps a `lease_token` — and every fence in the
system is that token carried back. A changefeed arrives on top of that and brings a
temptation with it: the event already contains the row, so why claim at all? Because a
webhook sink is **at-least-once by specification**. CockroachDB re-delivers any batch it
did not get a 2xx for, Lambda can invoke twice for one delivery, and two workers holding
the same event is the ordinary case rather than the exotic one. A wake that carried work
would turn every redelivery into a duplicate agent run, duplicate provider spend, and —
for `outbox` — a duplicate pitch to a curator, which `sender.py` exists to make
impossible.

So this file is one argument in three parts.

## 1. The statement, read as text

No cluster is involved and none is needed: `create_statement` is a pure function and its
dangerous properties are textual. `initial_scan = 'no'` is the one that would have been a
live incident — the default replays every existing row of all three tables as though it
had just changed, waking a `distil_lesson` claim for every thread ever closed, each of
which is a paid embedding call. `lead` being absent from `TABLES` is the other: the fleet
UPDATEs `lead` on every claim, renew and completion, so a feed that watched it would wake
workers whose own writes wake more workers.

## 2. The routing, read as data

Payloads in, wakes out, no database. Every refusal here is a *raise*, not a skip: an
unknown topic, a payload with no `tenant_id`, a body that is not JSON. The house rule is
that a failure points at not acting, and the specific thing not-acting protects against is
a wake with no tenant in it — the only two ways to invent one are "the configured tenant"
and "all of them", which are a cross-tenant wake and a cross-tenant claim respectively.

## 3. The property, against a fake cluster that models exactly one thing

`_Cluster` below is not a database. It is `lead`'s claim semantics and nothing else, and
the reason it is trustworthy is that **it raises `NotImplementedError` on any statement it
does not recognise**. `fleet.work_once` is run unmodified against it, so if the SQL in
`fleet.py` changes shape, these tests fail loudly with the statement attached rather than
quietly continuing to pass against a fixture that has drifted from the code it is
standing in for. It is not here to prove CockroachDB works — `tests/test_lease_race.py`
does that adversarially against the live cluster, which is where that belongs. It is here
to prove that the *changefeed path* reaches the work through `fleet.claim` and not around
it, for three interleavings a real cluster makes hard to arrange on demand:

  * two wakes, one after the other;
  * a second wake arriving while the first worker is mid-fetch;
  * a second wake arriving after the first worker's lease has lapsed — where the honest
    answer is that the work is *fetched* twice and *completed* once, and this file asserts
    exactly that rather than the flattering version.

Nothing here touches AWS, a cluster, or the network. `lambda_handler` is exercised with a
`db.connect` that raises, so a test that accidentally reached the database would fail
rather than connect.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import unittest
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from rtf_platform import changefeed as cf, fleet

MODULE = Path(cf.__file__)

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def _thread_event(state: str = "closed_won", tenant: str = TENANT,
                  updated: str = "1770000000.0000000000") -> cf.Event:
    return cf.Event(table="thread", updated=updated,
                    after={"id": str(uuid.uuid4()), "tenant_id": tenant, "state": state})


def _webhook_body(*messages: dict[str, Any]) -> str:
    return json.dumps({"payload": list(messages), "length": len(messages)})


# ------------------------------------------------------------------- 1. the statement

class TheStatement(unittest.TestCase):
    """What the DDL says, checked as text because that is what it is."""

    def setUp(self) -> None:
        self.sql = cf.create_statement("https://x.lambda-url.us-east-1.on.aws/",
                                       "s3cret", reveal=True)

    def test_lead_is_not_in_the_feed(self) -> None:
        """The feedback loop this design would have if it watched its own work table.

        `fleet.claim` UPDATEs `lead`. So does `renew`, once per lead per turn, and so does
        `complete`. A feed over `lead` would emit an event for each of those, each event
        would wake a worker, and each woken worker would claim — emitting more events. The
        steady state is bounded by nothing this module controls.
        """
        self.assertNotIn("lead", cf.TABLES)
        self.assertNotIn(" lead", self.sql)

    def test_it_does_not_replay_history_as_though_it_were_news(self) -> None:
        """`initial_scan` defaults to *on*, and on is wrong here — see the file docstring."""
        self.assertIn("initial_scan = 'no'", self.sql)

    def test_the_secret_is_redacted_unless_asked_for(self) -> None:
        redacted = cf.create_statement("https://x.lambda-url.us-east-1.on.aws/", "s3cret")
        self.assertNotIn("s3cret", redacted)
        self.assertIn(cf.REDACTED, redacted)
        self.assertIn("s3cret", self.sql)

    def test_a_plaintext_sink_is_refused_rather_than_warned_about(self) -> None:
        """The auth header travels on every batch. There is no deployment where the
        answer to sending it in clear is a log line."""
        with self.assertRaises(cf.ChangefeedRefused) as caught:
            cf.create_statement("http://x.example/", "s3cret")
        self.assertIn("HTTPS", str(caught.exception))

    def test_a_feed_with_no_secret_is_refused(self) -> None:
        """The sink is a public Function URL. Without the header, anybody who finds it
        can wake this fleet as often as they like, which on a metered cluster is a bill."""
        with self.assertRaises(cf.ChangefeedRefused):
            cf.create_statement("https://x.lambda-url.us-east-1.on.aws/", "  ")

    def test_it_batches(self) -> None:
        """One invocation per burst rather than per row — the single biggest lever on
        what this costs, and therefore something a refactor must not quietly drop."""
        self.assertIn("webhook_sink_config", self.sql)
        self.assertIn(f'"Messages": {cf.FLUSH_MESSAGES}', self.sql)

    def test_every_watched_table_has_a_declared_wake(self) -> None:
        """A table added to the feed without deciding what it wakes fails here.

        An empty tuple is a legitimate answer — `message` is one today — and it is a
        *declaration*, which is the difference between "nothing claims this yet" and
        "somebody forgot".
        """
        self.assertEqual(set(cf.TABLES), set(cf.WAKES))

    def test_nothing_in_this_module_executes_the_create(self) -> None:
        """The module composes the DDL and hands it to a human. Prove it from the AST.

        Read the module docstring for the three reasons. The one this test can enforce is
        the mechanical one: the only statements reaching `cursor.execute` in this file are
        two read-only `SHOW`s, and both are literals — so `tests/test_tenant_scoping.py`
        can resolve them, and so no code path can be talked into creating a job that
        spends request units continuously.
        """
        tree = ast.parse(MODULE.read_text(), filename=str(MODULE))
        executed: list[str] = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "execute"):
                self.assertTrue(
                    node.args and isinstance(node.args[0], ast.Constant),
                    f"{MODULE.name}:{node.lineno}: a non-literal statement reaches "
                    "`.execute` — `tests/test_tenant_scoping.py` raises on statements it "
                    "cannot resolve, and this module must not be the reason it starts")
                executed.append(node.args[0].value)
        self.assertEqual(sorted(executed),
                         ["SHOW CHANGEFEED JOBS",
                          "SHOW CLUSTER SETTING kv.rangefeed.enabled"])


# --------------------------------------------------------------------- 2. the routing

class Parsing(unittest.TestCase):
    """Payload in, events out. Every unfamiliar shape raises."""

    def test_a_webhook_batch_becomes_events(self) -> None:
        events = cf.parse_webhook(_webhook_body(
            {"topic": "thread", "updated": "1.0",
             "after": {"tenant_id": TENANT, "state": "closed_won"}},
            {"topic": "outbox", "updated": "2.0",
             "after": {"tenant_id": TENANT, "state": "pending"}}))
        self.assertEqual([e.table for e in events], ["thread", "outbox"])

    def test_a_resolved_heartbeat_is_not_a_change(self) -> None:
        """Recognised explicitly rather than falling through an empty `payload`: "the
        feed is alive and nothing happened" and "the payload key was missing" must not be
        the same answer."""
        self.assertEqual(cf.parse_webhook(json.dumps({"resolved": "1770000000.0"})), [])

    def test_a_delete_wakes_nothing(self) -> None:
        """`after` is null on a delete. None of these tables is deleted from by any code
        path in this build, so a delete is a tenant cascade or a hand-typed statement —
        neither is work."""
        self.assertEqual(cf.parse_webhook(_webhook_body(
            {"topic": "thread", "after": None})), [])

    def test_an_unknown_topic_raises(self) -> None:
        """Either the running feed watches more tables than `TABLES` names, or a table
        was added to `TABLES` without deciding what it wakes. Both are worth a page."""
        with self.assertRaises(cf.ChangefeedRefused) as caught:
            cf.parse_webhook(_webhook_body(
                {"topic": "party", "after": {"tenant_id": TENANT}}))
        self.assertIn("party", str(caught.exception))

    def test_a_body_that_is_not_json_raises(self) -> None:
        with self.assertRaises(cf.ChangefeedRefused):
            cf.parse_webhook("<html>404</html>")

    def test_a_core_row_parses_the_same_way(self) -> None:
        """Both sinks converge on `Event`, so the routing below is written once."""
        row = {"table": b"thread", "key": b'["x"]',
               "value": b'{"after": {"tenant_id": "%s", "state": "closed_lost"}, '
                        b'"updated": "3.0"}' % TENANT.encode()}
        event = cf.parse_core_row(row)
        self.assertIsNotNone(event)
        self.assertEqual(event.table, "thread")
        self.assertEqual(event.updated, "3.0")

    def test_a_core_resolved_row_is_a_heartbeat(self) -> None:
        self.assertIsNone(cf.parse_core_row(
            {"table": None, "key": None, "value": b'{"resolved": "1770000000.0"}'}))

    def test_latency_is_measured_or_refused_never_guessed(self) -> None:
        event = _thread_event(updated="1770000000.0000000000")
        self.assertAlmostEqual(cf.age_seconds(event, now=1770000012.0), 12.0, places=3)
        with self.assertRaises(cf.ChangefeedRefused):
            cf.age_seconds(cf.Event(table="thread", after={}, updated="not-a-timestamp"))


class Routing(unittest.TestCase):
    """Which changes justify a wake, and which do not."""

    def test_a_wake_carries_no_row(self) -> None:
        """The field list is the guarantee, so the field list is the assertion.

        A `Wake` with the changed row on it would let a woken worker act on that row
        directly — skipping `fleet.claim`, which is the only thing that makes two workers
        holding one event produce one unit of work. Adding a payload field here should
        fail a test with the argument attached rather than review as a convenience.
        """
        self.assertEqual({f.name for f in dataclasses.fields(cf.Wake)},
                         {"table", "tenant_id", "kinds", "drains_outbox", "reason"})

    def test_a_closed_thread_wakes_the_lesson_agent(self) -> None:
        item = cf.wake_for(_thread_event("closed_lost"))
        self.assertEqual(item.kinds, ("distil_lesson",))
        self.assertEqual(item.tenant_id, TENANT)
        self.assertFalse(item.drains_outbox)

    def test_an_ordinary_transition_wakes_nothing(self) -> None:
        """`shortlisted` writes no lead, so a wake for it is a claim query that can only
        ever return zero rows — paid for, forever, on a metered cluster."""
        self.assertIsNone(cf.wake_for(_thread_event("shortlisted")))

    def test_a_pending_outbox_row_wakes_the_sender_and_no_lead_kind(self) -> None:
        """The Sender does not work the `lead` table at all — `agent_manifest` has said
        so since 010 — so its wake is a flag and not a kind."""
        item = cf.wake_for(cf.Event(
            table="outbox", after={"tenant_id": TENANT, "state": "pending"}))
        self.assertTrue(item.drains_outbox)
        self.assertEqual(item.kinds, ())

    def test_a_claimed_outbox_row_does_not_wake_the_sender_again(self) -> None:
        """`sender.claim` moves the row to `claimed`, which is another update and
        therefore another event. Waking on it is the fleet chasing its own tail."""
        self.assertIsNone(cf.wake_for(cf.Event(
            table="outbox", after={"tenant_id": TENANT, "state": "claimed"})))

    def test_a_row_with_no_tenant_raises(self) -> None:
        """Every table in the feed declares `tenant_id NOT NULL`, so its absence is proof
        that what arrived is not what this module thinks it is parsing. The two ways to
        invent one are a cross-tenant wake and a cross-tenant claim."""
        with self.assertRaises(cf.ChangefeedRefused) as caught:
            cf.wake_for(cf.Event(table="thread", after={"state": "closed_won"}))
        self.assertIn("tenant_id", str(caught.exception))

    def test_the_wake_carries_the_tenant_from_the_row(self) -> None:
        """Not the configured one. A feed is cluster-wide; the tenant scoping this
        architecture relies on is restored here and nowhere else."""
        item = cf.wake_for(_thread_event(tenant=OTHER_TENANT))
        self.assertEqual(item.tenant_id, OTHER_TENANT)

    def test_an_outbox_wake_with_no_sender_attached_is_reported_not_sent(self) -> None:
        """`ingest.py` keeps `--send` separate from draining because preparing a send and
        performing one are different decisions and only the second is irreversible. A
        changefeed must not be the thing that quietly makes them one decision."""
        item = cf.wake_for(cf.Event(
            table="outbox", after={"tenant_id": TENANT, "state": "pending"}))

        def must_not_run(conn: Any, tenant: str, kinds: Any) -> int:
            raise AssertionError("an outbox wake must not reach the fleet worker")

        woken = cf.wake(object(), item, work=must_not_run, send=None)
        self.assertFalse(woken.handled)
        self.assertEqual(woken.worked, 0)
        self.assertIn("irreversible", woken.note)


# ------------------------------------------------------------------ the fake cluster

#: What `fleet.claim`'s `RETURNING` hands back. Kept here rather than inlined so that a
#: change to the real statement shows up as a `KeyError` naming the missing column.
_RETURNING = ("id", "tenant_id", "scope_kind", "party_id", "recording_id", "kind", "mode",
              "adapter", "target", "platform", "depth", "reason", "score", "attempts",
              "cadence_seconds", "lease_token")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Cluster:
    """`lead`'s claim semantics, and deliberately nothing else.

    This models one rule: a claimable lead is `pending`, due, and either unowned or held
    under an expired lease; taking it stamps a fresh token; and every fence matches on
    that token. That rule is the whole of what the tests below are about, so it is the
    whole of what this fake implements.

    **Any statement it does not recognise raises `NotImplementedError` with the SQL.**
    That is what makes it safe to run `fleet.work_once` unmodified against it: if the
    fleet's SQL changes shape, these tests fail with the new statement in the message
    rather than passing against a fixture that has silently stopped standing for the
    code. A fake that shrugged at an unfamiliar statement would be a test suite that
    stopped covering the thing it was written for without ever going red.

    Rollback **is** modelled, by snapshot, and that is not gold-plating: `fleet.work_once`
    writes `agent_run` and then completes the lead inside one transaction, so a fake that
    committed the run row before discovering the lease was lost would report two
    successful runs for one lead — the exact duplicate these tests exist to deny. A fake
    that cannot roll back cannot be used to prove anything about a transaction.

    Not modelled, and not needed by any test here: `SKIP LOCKED`'s behaviour under genuine
    concurrency (these tests interleave deterministically, which is stronger for this
    purpose — the race is arranged rather than hoped for), and every table except `lead`
    and an append-only `agent_run`.
    """

    def __init__(self) -> None:
        self.leads: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self._snapshots: list[tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]] = []

    def add_lead(self, *, kind: str, tenant_id: str = TENANT) -> str:
        lead_id = str(uuid.uuid4())
        self.leads[lead_id] = {
            "id": lead_id, "tenant_id": tenant_id, "state": "pending", "kind": kind,
            "next_action_at": _now() - timedelta(seconds=1), "owner_agent": None,
            "lease_token": None, "lease_expires_at": None, "attempts": 0,
            "last_error": "", "scope_kind": "party", "party_id": None,
            "recording_id": None, "mode": "auto", "adapter": "", "target": "",
            "platform": "", "depth": 0, "reason": "", "score": 0.5,
            "cadence_seconds": None,
        }
        return lead_id

    def cursor(self) -> "_Cursor":
        return _Cursor(self)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Snapshot in, restore on the way out if the block raised.

        A stack rather than a single snapshot, so a nested block behaves like the
        savepoint psycopg opens for one: an inner block that succeeded inside an outer
        block that did not is undone with the outer, which is what `SERIALIZABLE` does
        and what `fleet._record_lease_lost` relies on.
        """
        self._snapshots.append((deepcopy(self.leads), list(self.runs)))
        try:
            yield
        except BaseException:
            self.leads, self.runs = self._snapshots.pop()
            raise
        else:
            self._snapshots.pop()


class _Cursor:

    def __init__(self, cluster: _Cluster) -> None:
        self.cluster = cluster
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    # -- the dispatch -------------------------------------------------------------

    def execute(self, sql: str, params: Any = None) -> None:
        norm = " ".join(sql.split())
        if norm.startswith("UPDATE lead") and "gen_random_uuid()" in norm:
            return self._claim(params)
        if norm.startswith("UPDATE lead SET lease_expires_at = now()"):
            return self._renew(params)
        if norm.startswith("SELECT 1 FROM lead"):
            return self._reacquire(params)
        if norm.startswith("INSERT INTO agent_run"):
            return self._record_run(params)
        if norm.startswith("UPDATE lead SET state = 'done'"):
            return self._complete(params)
        if "lease_expires_at <= now()" in norm and norm.startswith("UPDATE lead"):
            return self._reschedule(params)
        raise NotImplementedError(
            "`_Cluster` does not model this statement, so a test that reached it would "
            "be proving nothing. Teach it the statement, or ask whether the test belongs "
            f"against the live cluster instead:\n\n{norm}")

    def _claim(self, params: dict[str, Any]) -> None:
        now = _now()
        claimable = [
            lead for lead in self.cluster.leads.values()
            if lead["tenant_id"] == params["tenant"]
            and lead["state"] == "pending"
            and lead["kind"] in params["kinds"]
            and lead["next_action_at"] <= now
            and (lead["owner_agent"] is None
                 or (lead["lease_expires_at"] is not None
                     and lead["lease_expires_at"] < now))
        ]
        claimable.sort(key=lambda lead: lead["next_action_at"])
        taken = claimable[:int(params["batch"])]
        for lead in taken:
            lead["owner_agent"] = params["agent"]
            lead["lease_token"] = str(uuid.uuid4())
            lead["lease_expires_at"] = now + timedelta(seconds=int(params["lease"]))
        self._rows = [{column: lead[column] for column in _RETURNING} for lead in taken]
        self.rowcount = len(taken)

    def _owned(self, tenant: str, lead_id: str, agent: str, token: Any, *,
               live: bool | None) -> dict[str, Any] | None:
        """The row this fence matches, or `None`. `live` selects the expiry direction —
        `True` for the `> now()` the fences that raise `LeaseLost` carry, `False` for the
        `<= now()` complement `_reschedule_after_lease_loss` uses, `None` for `renew`,
        which deliberately carries no expiry predicate at all."""
        lead = self.cluster.leads.get(lead_id)
        if lead is None or lead["tenant_id"] != tenant:
            return None
        if lead["owner_agent"] != agent or str(lead["lease_token"]) != str(token):
            return None
        if live is None:
            return lead
        expires = lead["lease_expires_at"]
        if expires is None:
            return None
        return lead if (expires > _now()) is live else None

    def _renew(self, params: tuple) -> None:
        seconds, tenant, lead_id, agent, token = params
        lead = self._owned(tenant, str(lead_id), agent, token, live=None)
        if lead is not None:
            lead["lease_expires_at"] = _now() + timedelta(seconds=int(seconds))
        self.rowcount = 1 if lead is not None else 0

    def _reacquire(self, params: tuple) -> None:
        tenant, lead_id, agent, token = params
        lead = self._owned(tenant, str(lead_id), agent, token, live=True)
        self._rows = [{"?column?": 1}] if lead is not None else []
        self.rowcount = len(self._rows)

    def _complete(self, params: tuple) -> None:
        tenant, lead_id, agent, token = params
        lead = self._owned(tenant, str(lead_id), agent, token, live=True)
        if lead is not None:
            lead.update(state="done", owner_agent=None, lease_expires_at=None,
                        lease_token=None, last_error="")
        self.rowcount = 1 if lead is not None else 0

    def _reschedule(self, params: tuple) -> None:
        attempts, error, backoff, tenant, lead_id, agent, token = params
        lead = self._owned(tenant, str(lead_id), agent, token, live=False)
        if lead is not None:
            lead.update(state="pending", owner_agent=None, lease_expires_at=None,
                        lease_token=None, attempts=int(attempts), last_error=error,
                        next_action_at=_now() + timedelta(seconds=int(backoff)))
        self.rowcount = 1 if lead is not None else 0

    def _record_run(self, params: tuple) -> None:
        self.cluster.runs.append({"tenant_id": params[0], "agent_kind": params[2],
                                  "lead_id": params[3], "state": params[4],
                                  "error": params[6]})
        self.rowcount = 1


def _worker(name: str, agent: Any) -> cf.Worker:
    """A `Worker` in exactly the shape `fleet_worker` builds: `fleet.work_once`, nothing
    else. The agent is injected rather than looked up in `agents.REGISTRY` so that these
    tests are about the claim and not about what `distil_lesson` does with an embedding
    provider."""
    def work(conn: Any, tenant_id: str, kinds: Any) -> int:
        return fleet.work_once(conn, tenant_id, name, agent, kinds=list(kinds))
    return work


# ------------------------------------------------------------------- 3. the property

class WakingIsNotClaiming(unittest.TestCase):
    """One event, two woken workers, exactly one completion.

    Every test runs with a cleared environment, as `test_spend` and `test_sender` do: a
    developer with `RTF_PAID_ENABLED=1` in their shell would otherwise send
    `spend.Gate.open` to the database for the day's spend, against a fake that models the
    `lead` table and would — correctly — refuse to answer.
    """

    def setUp(self) -> None:
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cluster = _Cluster()
        self.lead_id = self.cluster.add_lead(kind="distil_lesson")
        self.item = cf.wake_for(_thread_event("closed_won"))
        #: Who *fetched* — the half that costs money and cannot be rolled back.
        self.ran: list[str] = []
        #: Who *wrote* — the half a lost lease discards. The two lists are separate
        #: because the whole honest claim of this file is that they can differ.
        self.wrote: list[str] = []

    def _agent(self, name: str, during_fetch: Any = None) -> fleet.NetworkAgent:
        """A `NetworkAgent`, because the fetch/write split is where every race lives.

        `during_fetch` runs with no transaction open, which is precisely the window
        `fleet.work_once`'s docstring says it cannot protect: an HTTP call to a provider
        can outlive `LEASE_SECONDS`, and a second worker may legitimately claim the lead
        while the first is still fetching. Arranging the interleaving here is what makes
        it a test rather than a hope.
        """
        def fetch(conn: Any, lead: dict[str, Any], gate: Any) -> None:
            self.ran.append(name)
            if during_fetch is not None:
                during_fetch(lead)

        def write(conn: Any, lead: dict[str, Any], gate: Any,
                  prepared: Any) -> fleet.Outcome:
            self.wrote.append(name)
            return fleet.Outcome(summary=f"{name} worked it")

        return fleet.NetworkAgent(fetch=fetch, write=write)

    def test_two_wakes_in_sequence_produce_one_run(self) -> None:
        """The plain case: the feed delivers, and delivers again because it did not see a
        2xx. The second worker's claim finds the lead already done and it does nothing."""
        first = cf.wake(self.cluster, self.item, work=_worker("A", self._agent("A")))
        second = cf.wake(self.cluster, self.item, work=_worker("B", self._agent("B")))

        self.assertEqual(first.worked, 1)
        self.assertEqual(second.worked, 0)
        self.assertEqual(self.ran, ["A"])
        self.assertEqual(self.wrote, ["A"])
        self.assertEqual(self.cluster.leads[self.lead_id]["state"], "done")
        self.assertEqual([run["state"] for run in self.cluster.runs], ["ok"])

    def test_a_second_wake_while_the_first_worker_is_mid_flight_gets_nothing(self) -> None:
        """The interleaving a real cluster makes hard to arrange on demand.

        B is woken from inside A's fetch, so B's `claim` runs while A holds a live lease
        on the only claimable lead. `claim`'s `owner_agent IS NULL OR lease_expires_at <
        now()` is what makes B's batch empty — B's agent is never handed a lead, so it
        never fetches, so it never spends.
        """
        woke_b: list[cf.Woken] = []

        def wake_b(lead: dict[str, Any]) -> None:
            woke_b.append(cf.wake(self.cluster, self.item,
                                  work=_worker("B", self._agent("B"))))

        first = cf.wake(self.cluster, self.item,
                        work=_worker("A", self._agent("A", during_fetch=wake_b)))

        self.assertEqual(first.worked, 1)
        self.assertEqual([w.worked for w in woke_b], [0])
        self.assertEqual(self.ran, ["A"], "B must never have fetched at all")
        self.assertEqual(self.wrote, ["A"])
        self.assertEqual(self.cluster.leads[self.lead_id]["state"], "done")

    def test_a_lapsed_lease_costs_a_second_fetch_and_never_a_second_completion(self) -> None:
        """The honest version of the guarantee, and the reason it is worth stating.

        A's lease expires mid-fetch — the case `fleet.work_once`'s docstring says it
        cannot prevent, because `fetch` can outlive `LEASE_SECONDS` and a paid provider
        has already been called by then. B is woken, claims legitimately, and finishes. A
        then opens its write transaction, `_reacquire` finds its token is no longer the
        one on the row, raises `LeaseLost`, and the whole write phase rolls back.

        So the work is *fetched* twice and *written* once. This asserts both halves:
        asserting only the second would be a flattering test of a real cost.
        """
        def lose_the_lease_then_wake_b(lead: dict[str, Any]) -> None:
            self.cluster.leads[self.lead_id]["lease_expires_at"] = (
                _now() - timedelta(seconds=1))
            cf.wake(self.cluster, self.item, work=_worker("B", self._agent("B")))

        cf.wake(self.cluster, self.item,
                work=_worker("A", self._agent(
                    "A", during_fetch=lose_the_lease_then_wake_b)))

        self.assertEqual(self.ran, ["A", "B"], "both fetched — that is the real cost")
        self.assertEqual(self.wrote, ["B"], "and only the current claim wrote anything")
        self.assertEqual(self.cluster.leads[self.lead_id]["state"], "done")

        states = [run["state"] for run in self.cluster.runs]
        self.assertEqual(states.count("ok"), 1, "exactly one run completed the lead")
        self.assertIn("lease_lost", states,
                      "and the loser is recorded, with its cost, rather than vanishing")

    def test_a_wake_claims_only_within_its_own_tenant(self) -> None:
        """The feed is cluster-wide and the claim is not. A closed thread in one tenant
        must not hand a worker another tenant's lead."""
        other = _Cluster()
        other_lead = other.add_lead(kind="distil_lesson", tenant_id=OTHER_TENANT)
        other.leads[other_lead]["tenant_id"] = OTHER_TENANT

        woken = cf.wake(other, cf.wake_for(_thread_event(tenant=TENANT)),
                        work=_worker("A", self._agent("A")))

        self.assertEqual(woken.worked, 0)
        self.assertEqual(self.ran, [])
        self.assertEqual(other.leads[other_lead]["state"], "pending")

    def test_a_batch_of_events_wakes_each_of_them(self) -> None:
        """`wake_all` over a real webhook batch — the delivery shape `webhook_sink_config`
        produces — with the outbox half unattached and therefore reported, not sent."""
        events = cf.parse_webhook(_webhook_body(
            {"topic": "thread", "updated": "1.0",
             "after": {"tenant_id": TENANT, "state": "closed_won"}},
            {"topic": "message", "updated": "1.1",
             "after": {"tenant_id": TENANT, "direction": "inbound"}},
            {"topic": "outbox", "updated": "1.2",
             "after": {"tenant_id": TENANT, "state": "pending"}}))

        woke = cf.wake_all(self.cluster, events,
                           work=_worker("A", self._agent("A")))

        self.assertEqual([w.wake.table for w in woke], ["thread", "outbox"],
                         "a message change wakes nothing in this build, by declaration")
        self.assertEqual(woke[0].worked, 1)
        self.assertFalse(woke[1].handled)


# ------------------------------------------------------------------ the webhook sink

class Authentication(unittest.TestCase):
    """Nothing unauthenticated reaches a database connection.

    The sink is a public Function URL — CockroachDB cannot sign SigV4 — so the shared
    header is the entire boundary. `db.connect` is patched to raise in every test here, so
    a regression that opened a connection before checking the header fails rather than
    quietly spending request units on a stranger's POST.
    """

    def setUp(self) -> None:
        self.connect = mock.patch(
            "rtf_platform.db.connect",
            side_effect=AssertionError("an unauthenticated delivery reached the database"))
        self.connect.start()
        self.addCleanup(self.connect.stop)

    def _post(self, body: str, token: str | None = "s3cret",
              configured: str | None = "s3cret") -> dict[str, Any]:
        env = {} if configured is None else {"PLATFORM_CHANGEFEED_TOKEN": configured}
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        with mock.patch.dict(os.environ, env, clear=True):
            return cf.lambda_handler({"headers": headers, "body": body})

    def test_a_wrong_secret_is_401(self) -> None:
        response = self._post(_webhook_body(
            {"topic": "thread", "after": {"tenant_id": TENANT, "state": "closed_won"}}),
            token="wrong")
        self.assertEqual(response["statusCode"], 401)

    def test_no_secret_configured_refuses_everything(self) -> None:
        """Fail closed, exactly as `spend.Policy.load` does. An unset variable meaning
        "accept everything" is a public endpoint that wakes a fleet on demand and looks
        perfectly healthy in every log."""
        response = self._post(_webhook_body(
            {"topic": "thread", "after": {"tenant_id": TENANT, "state": "closed_won"}}),
            configured=None)
        self.assertEqual(response["statusCode"], 401)

    def test_a_missing_header_is_401(self) -> None:
        self.assertEqual(self._post("{}", token=None)["statusCode"], 401)

    def test_an_authenticated_batch_reaches_the_claim_and_nothing_else(self) -> None:
        """The handler end to end: a real webhook body, through parsing and routing, into
        `fleet.work_once` against `_Cluster`.

        `db.connect` is redirected at the fake and `fleet_worker` at an injected agent, so
        the only things not stubbed are the two this test is about — the wiring and the
        claim. The assertion that matters is the second one: the handler answers 200
        *after* the claim has been attempted, so a batch is never acknowledged on the
        strength of having been parsed.
        """
        cluster = _Cluster()
        lead_id = cluster.add_lead(kind="distil_lesson")
        ran: list[str] = []

        def agent(conn: Any, lead: dict[str, Any], gate: Any) -> fleet.Outcome:
            ran.append("distil_lesson")
            return fleet.Outcome(summary="lesson distilled")

        body = _webhook_body(
            {"topic": "thread", "updated": "1.0",
             "after": {"tenant_id": TENANT, "state": "closed_won"}})

        with mock.patch("rtf_platform.db.connect", return_value=cluster), \
             mock.patch.object(cf, "fleet_worker",
                               return_value=_worker("changefeed-lambda", agent)), \
             mock.patch.dict(os.environ,
                             {"PLATFORM_CHANGEFEED_TOKEN": "s3cret",
                              "DATABASE_URL": "postgresql://stub/none"}, clear=True):
            response = cf.lambda_handler(
                {"headers": {"authorization": "Bearer s3cret"}, "body": body})

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["worked"], 1)
        self.assertEqual(ran, ["distil_lesson"])
        self.assertEqual(cluster.leads[lead_id]["state"], "done")

    def test_an_authenticated_heartbeat_needs_no_database(self) -> None:
        """A resolved message is the majority of deliveries on a quiet day. Answering it
        without a connection is why the RU cost of the heartbeat is a Lambda invocation
        and not a query."""
        response = self._post(json.dumps({"resolved": "1770000000.0"}))
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["woke"], 0)


if __name__ == "__main__":
    unittest.main()
