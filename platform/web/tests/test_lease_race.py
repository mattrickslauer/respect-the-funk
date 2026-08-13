"""The fencing token, proved adversarially, in the tenant that actually has data in it.

    cd platform/web && set -a && . ../../.env && set +a \
      && .venv/bin/python -m pytest tests/test_lease_race.py -q

## What is being proved, and why it needed its own file

`platform/schema/013_lease_token.sql` added one nullable column and closed a hole that
three previous rounds of fencing had not. The hole is worth restating exactly, because
every test below is a sentence in the same argument:

`complete`, `fail` and `_defer` were fenced on `WHERE id = %s AND owner_agent = %s AND
lease_expires_at > now()`. The *intent* was right — a worker whose lease lapsed mid-fetch
must not finish a lead somebody else now owns. But `owner_agent` is not an identity. It is
the worker's **name**, and `ingest.py`'s CLI default for it is the constant `"ingest-cli"`
(`ingest.py`, `--worker`, line 465). Two drains started from two terminals are therefore
*the same string*, and the fence cannot tell them apart: worker A's `complete` matches on
worker B's fresh lease and both write. The duplication the fence existed to stop walked
straight through it.

`claim` now stamps a fresh `gen_random_uuid()` into `lead.lease_token` on every row it
takes and returns it with the row. A second claim — by anyone, under any name — overwrites
that column, so the first claim's token stops matching the *instant* its claim stops being
current. This is a capability, not an assertion: the caller cannot mint one, cannot guess
one, and cannot forget to pass a good one, because no caller supplies it at all.

`test_fleet.py` is the module's general behaviour suite — backoff arithmetic, batching,
`NetworkAgent` transaction shape, `agent_run` bookkeeping — and it works inside a
throwaway tenant it creates and drops. This file is deliberately not that. It is one
property, attacked from five directions, run against **`respect-the-funk`**: the live
tenant holding the real frontier. Proving the fence in an empty tenant of one's own
construction proves less than proving it beside seven thousand pending `embed_party`
leads, because the second is the configuration that actually ships.

## How this stays safe against a live shared cluster

Every test creates its own `lead` rows under a **kind generated per run**
(`lease_race_<uuid8>`), which no entry in `agents.REGISTRY` handles, so no drain can ever
claim them and no `claim` in this file can ever reach a real lead. Teardown deletes
exactly those rows, qualified on both `tenant_id` and that generated kind. There is no
unqualified `DELETE` or `UPDATE` anywhere in this file, and nothing here mutates a row it
did not itself insert.

The worker *names* are generated per run too, and that is not decoration — it is the same
defect this file reports, kept out of production. `fleet.worker`'s tidy-up updates every
pending lead in the tenant matching a worker name, with no `kind` in the predicate, so a
test invoking it under the literal `"ingest-cli"` would strip the leases of any real
`ingest.drain` that happened to be running while the suite ran. See `setUp`.

The generated kind also makes the file safe to run concurrently with itself, which matters
because a second `pytest` session on this cluster is a normal event. The tenant is
resolved by the slug `respect-the-funk` for the same reason — "the only tenant" is a
lookup that is true until somebody else's test run makes it false.

## The one thing this file found that is not watertight

See `WorkerShutdownIsNotFenced` at the bottom. `fleet.worker`'s clean-shutdown tidy-up
releases leases by **worker name alone** — the exact predicate `013` was written to
retire — and the tests there characterise what that does. It cannot produce a double
*write*, and the reasoning for why is in that class's docstring; it can produce a
duplicate paid *fetch*, which is a smaller bug and still a real one.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from rtf_platform import fleet

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

#: The tenant these tests run against, resolved by slug and never by "the only row".
#: A concurrent test run elsewhere in the repo creates its own throwaway tenants, so
#: `SELECT id FROM tenant` is a query with a race in it.
TENANT_SLUG = "respect-the-funk"


class _ClusterFixture(unittest.TestCase):
    """Fixtures shared by the classes below. Holds no tests of its own, deliberately.

    The two test classes are siblings on this rather than one inheriting the other,
    because a `TestCase` subclass inherits its parent's *test methods* — so making
    `WorkerShutdownIsNotFenced` extend `LeaseFencing` would silently run every fencing
    test a second time. On a scale-to-zero BASIC cluster that is not a stylistic
    complaint; it is double the connections and double the round trips for no extra
    coverage.

    `setUp` mints a lead `kind` unique to this test-case instance. Everything created
    here carries it, everything deleted in `tearDown` is selected by it, and nothing in
    `agents.REGISTRY` answers to it — so these rows are inert to the real fleet for as
    long as they exist, which is a few hundred milliseconds.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self._extra: list = []
        self.kind = f"lease_race_{uuid.uuid4().hex[:8]}"
        # The shared worker name for the two-drains-under-one-name tests. It is
        # `ingest-cli` *derived* rather than `ingest-cli` literal, and the difference is a
        # safety property rather than a cosmetic one: `fleet.worker`'s shutdown tidy-up
        # is an `UPDATE lead … WHERE tenant_id = %s AND owner_agent = %s AND state =
        # 'pending'`, scoped by name and *not* by kind. Run against this live tenant under
        # the literal default, it would strip the leases of a real `ingest.drain` that
        # happened to be running — the very bug `WorkerShutdownIsNotFenced` documents,
        # inflicted on production by the test for it. Scoping the name to this run means
        # that `UPDATE` can only ever match rows this test inserted.
        #
        # Nothing under test depends on the literal string. What the tests need is that
        # both workers share *one* name, so that the token is the only thing telling the
        # stale claim from the live one.
        self.shared_name = f"ingest-cli-{self.kind}"
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM tenant WHERE slug = %s", (TENANT_SLUG,))
            row = cur.fetchone()
        if row is None:
            self.skipTest(f"tenant {TENANT_SLUG!r} not present in this cluster")
        self.tenant = str(row["id"])

    def tearDown(self) -> None:
        """Delete exactly what this test inserted, and nothing that resembles it.

        Both predicates are load-bearing. `kind` alone would be enough in practice — it
        carries a UUID nobody else generated — but a `DELETE` on a shared cluster holding
        real business data should not rest on a single condition being unique in practice.
        `tenant_id` is the cheap second lock on the same door.

        `agent_run.lead_id` is `ON DELETE SET NULL`, so deleting the leads first would
        leave orphaned run rows behind rather than an error. They go first, selected by
        the same lead ids.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM agent_run
                        WHERE tenant_id = %s
                          AND lead_id IN (SELECT id FROM lead
                                           WHERE tenant_id = %s AND kind = %s)""",
                    (self.tenant, self.tenant, self.kind))
                cur.execute("DELETE FROM lead WHERE tenant_id = %s AND kind = %s",
                            (self.tenant, self.kind))
        finally:
            for conn in self._extra:
                conn.close()
            self.conn.close()

    # ------------------------------------------------------------------- fixtures

    def _second_connection(self):
        """A genuinely separate session, for the worker that is not `self.conn`.

        Two claims down one connection would be two statements in sequence and would
        prove nothing about `FOR UPDATE SKIP LOCKED`, which is a property of concurrent
        *transactions*. Closed in `tearDown` whatever the test does.
        """
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                               row_factory=dict_row)
        self._extra.append(conn)
        return conn

    def _lead(self) -> str:
        """One claimable fixture lead of this run's private kind."""
        lead_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lead (id, tenant_id, scope_kind, kind, adapter, target,
                                     target_hash, next_action_at, reason)
                   VALUES (%s, %s, 'tenant', %s, 'test', %s, %s, now(),
                           'lease fencing fixture — safe to delete')""",
                (lead_id, self.tenant, self.kind, lead_id, f"{self.kind}:{lead_id}"))
        return lead_id

    def _row(self, lead_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lead WHERE tenant_id = %s AND id = %s",
                        (self.tenant, lead_id))
            return cur.fetchone()

    def _expire(self, lead_id: str) -> None:
        """Drag one lead's lease into the past.

        Standing in for a `fetch` that outlived `LEASE_SECONDS` — `embed_batch` and the
        source adapters each make one HTTP call that can genuinely run that long — without
        making the test suite wait two minutes to observe it. The clock is the only thing
        being faked; every fence under test reads the same row afterwards as it would have
        read after a real slow fetch.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute'
                    WHERE tenant_id = %s AND id = %s AND kind = %s""",
                (self.tenant, lead_id, self.kind))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class LeaseFencing(_ClusterFixture):
    """One lead, two claims, and every way the loser might still try to write."""

    # --------------------------------------------------- 1. only one worker wins

    def test_two_concurrent_claims_of_one_lead_yield_exactly_one_winner(self) -> None:
        """`FOR UPDATE SKIP LOCKED`: the property every other guarantee is built on.

        Two separate sessions ask for work from the same single-row frontier at the same
        time. Exactly one row comes back in total. Without `SKIP LOCKED` the second
        claimant would block on the first's row lock and then — after the first commits —
        re-evaluate its subquery and find the row no longer claimable, which happens to
        give the same answer here but at the cost of serialising the entire fleet. With
        it, the second worker skips the contended row and returns immediately empty.

        Both claims are asserted, not just the winner's: a test that only checks
        `len(a) == 1` passes just as happily on a database that handed the row to both.
        """
        lead_id = self._lead()
        other = self._second_connection()

        a = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])
        b = fleet.claim(other, self.tenant, "worker-B", kinds=[self.kind])

        self.assertEqual(len(a) + len(b), 1,
                         "one claimable lead was handed out zero times or twice")
        winner = (a or b)[0]
        self.assertEqual(str(winner["id"]), lead_id)
        self.assertIsNotNone(winner["lease_token"],
                             "`claim` returned a row with no fencing token on it")

    def test_a_live_lease_is_not_reclaimable_by_a_second_worker(self) -> None:
        """The complement of the above, over time rather than in parallel.

        `claim`'s subquery takes rows where `owner_agent IS NULL OR lease_expires_at <
        now()`. A lead under a live lease satisfies neither, so the second claim is empty
        — no lock contention involved, just the predicate.
        """
        self._lead()
        first = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])
        second = fleet.claim(self._second_connection(), self.tenant, "worker-B",
                             kinds=[self.kind])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "a live lease did not protect the row")

    # ------------------------------- 2 & 3. the loser is refused, loudly, by name

    def test_a_reclaimed_lead_refuses_the_first_workers_complete(self) -> None:
        """The headline. A's lease lapses, B legitimately re-claims, A comes back to write.

        This is not a hypothetical ordering. `LEASE_SECONDS` bounds a *claim*, not a
        *fetch*: the embedding provider and every source adapter make one HTTP call per
        batch, and a large one can outlive the lease while the worker is blocked on the
        socket. When it returns, the worker believes it holds a lead that has been
        somebody else's for a minute.

        Three things are asserted, and dropping any one of them would let a real
        regression through:

          * A's `complete` raises `LeaseLost` — a *raise*, not a return.
          * The lead is not `done`. The refusal actually prevented the write, rather than
            being reported after it landed.
          * The row still belongs to B, with B's token. A's failed attempt did not
            disturb the claim that is going perfectly well.

        And then B completes normally, because a fence that also blocks the legitimate
        writer is not a fence, it is an outage.
        """
        lead_id = self._lead()
        other = self._second_connection()

        first = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0]
        self._expire(lead_id)
        second = fleet.claim(other, self.tenant, "worker-B", kinds=[self.kind])[0]
        self.assertEqual(str(second["id"]), lead_id, "the re-claim did not happen")

        with self.assertRaises(fleet.LeaseLost):
            fleet.complete(self.conn, first, fleet.Outcome(summary="stale worker's write"),
                           agent_name="worker-A")

        row = self._row(lead_id)
        self.assertNotEqual(row["state"], "done",
                            "a worker completed a lead it no longer held")
        self.assertEqual(row["owner_agent"], "worker-B",
                         "the loser's refused write disturbed the live claim")
        self.assertEqual(str(row["lease_token"]), str(second["lease_token"]))

        fleet.complete(other, second, fleet.Outcome(summary="the live claim's write"),
                       agent_name="worker-B")
        self.assertEqual(self._row(lead_id)["state"], "done",
                         "the fence refused the worker that actually held the lead")

    def test_a_reclaimed_lead_refuses_the_first_workers_fail(self) -> None:
        """`fail` is fenced identically, and for a reason that is easy to miss.

        A refusal that only guarded `complete` would look thorough and would still corrupt
        the frontier: the stale worker's `fail` writes `attempts`, `last_error` and a
        backoff `next_action_at` onto a lead the *new* owner is actively working. The
        visible damage is a lead that parks at `MAX_ATTEMPTS` on strikes it never earned,
        charged to it by workers that lost races.

        `attempts` is asserted rather than `state`, because a first failure leaves the
        lead `pending` — exactly what it already was — so `state` alone cannot distinguish
        "refused" from "recorded a failure".
        """
        lead_id = self._lead()
        other = self._second_connection()

        first = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0]
        self._expire(lead_id)
        fleet.claim(other, self.tenant, "worker-B", kinds=[self.kind])

        with self.assertRaises(fleet.LeaseLost):
            fleet.fail(self.conn, first, "provider said 503", agent_name="worker-A")

        row = self._row(lead_id)
        self.assertEqual(row["attempts"], 0,
                         "a stale claim charged an attempt to the live claim's lead")
        self.assertEqual(row["last_error"], "",
                         "a stale claim wrote its error onto somebody else's lead")
        self.assertEqual(row["owner_agent"], "worker-B")

    def test_a_reclaimed_lead_refuses_the_first_workers_defer(self) -> None:
        """`_defer` is the third transition, reached when `spend.Gate` refuses to pay.

        It is private, and it is tested directly anyway: a spend refusal is the one path
        where nothing went wrong, so a silent no-op here is the least likely to be noticed
        and produces the same corruption — a ten-minute deferral written over a live
        claim's lease, handing the lead to a third worker while the second is still
        fetching.
        """
        lead_id = self._lead()
        other = self._second_connection()

        first = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0]
        self._expire(lead_id)
        fleet.claim(other, self.tenant, "worker-B", kinds=[self.kind])

        with self.assertRaises(fleet.LeaseLost):
            fleet._defer(self.conn, first, agent_name="worker-A")
        self.assertEqual(self._row(lead_id)["owner_agent"], "worker-B",
                         "a stale claim deferred a lead the live claim holds")

    def test_the_refusal_is_a_raised_lease_lost_carrying_the_lead_and_the_worker(self) -> None:
        """The refusal has to be loud, and it has to be *identifiable*.

        "Zero rows updated" is not a result a caller checks. The measured consequence of
        treating it as one was three `agent_run` rows with `state = 'ok'` for a single
        lead and three duplicate `party_metric` rows behind them — nothing raised, so
        nothing rolled back, so `work_once`'s transaction committed a write for a lead it
        had already lost.

        So this asserts the exception *type*, because `work_once` dispatches on it —
        `except LeaseLost` routes to `_record_lease_lost` and `except LeadFailed` routes
        to `fail`, and those do opposite things to the lead. A bare `RuntimeError` would
        satisfy "it raised" and land in the generic handler, which calls `fail`, which
        would try to write a failure onto the winner's lead. The type is the routing.

        It also asserts the message names the lead and the worker. This exception is read
        by a human deciding whether a lease loss in the logs is one worker being slow or
        two workers colliding, and an unidentified "lease lost" tells them neither.
        """
        lead_id = self._lead()
        other = self._second_connection()

        first = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0]
        self._expire(lead_id)
        fleet.claim(other, self.tenant, "worker-B", kinds=[self.kind])

        with self.assertRaises(fleet.LeaseLost) as caught:
            fleet.complete(self.conn, first, fleet.Outcome(), agent_name="worker-A")

        self.assertIsInstance(caught.exception, fleet.LeaseLost)
        self.assertNotIsInstance(caught.exception, fleet.LeadFailed,
                                 "a lease loss must not be routed as an agent failure")
        message = str(caught.exception)
        self.assertIn(lead_id, message, "the refusal does not say which lead")
        self.assertIn("worker-A", message, "the refusal does not say whose claim it was")

    def test_a_lost_lease_is_discovered_before_the_agent_writes_anything(self) -> None:
        """The production path, end to end, through `work_once` — not a direct call.

        Everything above drives `complete` and `fail` by hand. That proves the fences, and
        it does not prove that the code an operator actually runs reaches them. `work_once`
        is what `ingest.drain` calls, and it has an *earlier* fence: `_reacquire` takes
        `SELECT … FOR UPDATE` on the row as the first statement of the write-phase
        transaction, so a lost lease is discovered before the agent's first `INSERT`
        rather than after its last.

        The agent here is a `NetworkAgent` whose `fetch` — the phase that runs with no
        transaction open, precisely so a slow HTTP call cannot hold one — arranges for a
        second worker to take the lead, on a genuinely separate connection. That is the
        real race, reproduced in a millisecond and with no network.

        What must hold afterwards is the whole guarantee in three rows:

          * the marker the write phase tried to insert does not exist — the loser's work
            never became durable;
          * the lead is untouched, still `worker-B`'s, `attempts` still zero — the loser
            did not back off a lead that is not its own;
          * an `agent_run` row exists in state `lease_lost` — because the fetch may have
            spent real money before the loss was discovered, and `spend.spent_today` sums
            that column. A ceiling that only sees the runs that finished is a ceiling the
            next retry walks straight through.
        """
        lead_id = self._lead()
        other = self._second_connection()
        marker = f"{self.kind}:marker:{uuid.uuid4()}"

        def fetch(conn, lead, gate):
            with other.cursor() as cur:
                cur.execute(
                    """UPDATE lead
                          SET owner_agent = 'worker-B', lease_token = gen_random_uuid(),
                              lease_expires_at = now() + INTERVAL '120 seconds'
                        WHERE tenant_id = %s AND id = %s AND kind = %s""",
                    (self.tenant, lead_id, self.kind))
            return {"fetched": "while the lead was being taken away"}

        def write(conn, lead, gate, prepared):
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                         target_hash, next_action_at)
                       VALUES (%s, 'tenant', %s, 'test', %s, %s, now())""",
                    (self.tenant, self.kind, marker, marker))
            return fleet.Outcome(summary="this must never land")

        fleet.work_once(self.conn, self.tenant, "worker-A",
                        fleet.NetworkAgent(fetch=fetch, write=write), kinds=[self.kind])

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE tenant_id = %s "
                        "AND target_hash = %s", (self.tenant, marker))
            self.assertEqual(cur.fetchone()["n"], 0,
                             "a worker that had lost its lead still wrote for it")

        row = self._row(lead_id)
        self.assertEqual(row["owner_agent"], "worker-B",
                         "the lead was not left exactly as its new owner set it")
        self.assertEqual(row["attempts"], 0,
                         "the loser of the race charged an attempt to the winner's lead")

        with self.conn.cursor() as cur:
            cur.execute("SELECT state, summary FROM agent_run WHERE tenant_id = %s "
                        "AND lead_id = %s", (self.tenant, lead_id))
            runs = cur.fetchall()
        self.assertEqual(len(runs), 1, "the lost run was not recorded at all")
        self.assertEqual(runs[0]["state"], "lease_lost")
        self.assertIn("another claim holds", runs[0]["summary"],
                      "the run does not distinguish 'somebody took it' from 'it lapsed'")

    # ------------------------------------- 4. the token is the identity, not the name

    def test_one_worker_name_claiming_twice_produces_two_different_tokens(self) -> None:
        """The exact hole `013` closes, isolated to a single assertion.

        One name. One lead. Two claims. Under the pre-`013` fence — `owner_agent = %s AND
        lease_expires_at > now()` — the two claims are **indistinguishable**: same lead,
        same name, and the second claim's fresh `lease_expires_at` satisfies the first
        claim's fence. Both workers pass. Both write.

        The name is `self.shared_name` — an `ingest-cli`-derived string scoped to this
        run, for the reason given where it is minted. The collision it stands for is not
        hypothetical: `ingest-cli` is the literal default of `ingest.py --worker`, so two
        drains started from two terminals *are* this test.

        `gen_random_uuid()` is volatile, so CockroachDB evaluates it per row and per
        statement — which is what makes the second claim's token necessarily different
        from the first's rather than merely usually different.
        """
        lead_id = self._lead()
        name = self.shared_name

        first = fleet.claim(self.conn, self.tenant, name, kinds=[self.kind])[0]
        self._expire(lead_id)
        second = fleet.claim(self._second_connection(), self.tenant, name,
                             kinds=[self.kind])[0]

        self.assertEqual(str(first["id"]), str(second["id"]), "not the same lead")
        self.assertEqual(first.get("owner_agent", name), second.get("owner_agent", name))
        self.assertNotEqual(str(first["lease_token"]), str(second["lease_token"]),
                            "two claims of one lead under one name produced one token — "
                            "the fence cannot tell the stale run from the live one")

    def test_the_stale_claim_under_a_shared_name_is_the_one_that_is_refused(self) -> None:
        """The token differing is only half of it — the fence has to *use* it.

        Same single shared worker name as above. Everything the pre-`013` fence could see
        is identical between the two workers; the only thing that distinguishes them is
        the token. So this is the assertion that the fence keys on the token: the stale
        claim is refused and the live one succeeds, with the name held constant across
        both.

        Held constant deliberately. A test that gave the two workers different names would
        pass against the *broken* fence too, and would have certified the bug.
        """
        lead_id = self._lead()
        name = self.shared_name
        other = self._second_connection()

        first = fleet.claim(self.conn, self.tenant, name, kinds=[self.kind])[0]
        self._expire(lead_id)
        second = fleet.claim(other, self.tenant, name, kinds=[self.kind])[0]

        with self.assertRaises(fleet.LeaseLost):
            fleet.complete(self.conn, first, fleet.Outcome(summary="stale"),
                           agent_name=name)
        self.assertNotEqual(self._row(lead_id)["state"], "done",
                            "a stale claim completed a lead its own namesake holds")

        fleet.complete(other, second, fleet.Outcome(summary="live"), agent_name=name)
        row = self._row(lead_id)
        self.assertEqual(row["state"], "done")
        self.assertIsNone(row["lease_token"],
                          "completion left a spent token on the row")

    def test_renew_is_fenced_on_the_token_and_not_on_the_clock(self) -> None:
        """`renew` is the one fence that deliberately omits `lease_expires_at > now()`.

        It has to. `renew` exists *because* a lease may already have lapsed — it is called
        at the top of each lead's turn so the deadline measures how long this lead has been
        worked rather than how long ago the batch was claimed. Requiring a live lease would
        refuse to renew in exactly the case renewal is for.

        That omission is only safe because the token carries the whole proof of ownership
        on its own, independent of the clock. Both halves are asserted here, because either
        alone would be satisfied by a wrong implementation: an uncontended lapsed lease
        renews (so the clock is genuinely not the gate), and a *re-claimed* one does not
        (so ownership still is).
        """
        lead_id = self._lead()
        mine = fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0]

        self._expire(lead_id)
        fleet.renew(self.conn, mine, "worker-A")
        renewed = self._row(lead_id)
        self.assertGreater(renewed["lease_expires_at"], datetime.now(timezone.utc),
                           "an uncontended lapsed lease was not renewed — `renew` is "
                           "gating on the clock it is supposed to be resetting")
        self.assertEqual(str(renewed["lease_token"]), str(mine["lease_token"]),
                         "renewal rotated the token, breaking the fence's identity")

        # Now somebody else really does take it, and the same call must refuse.
        self._expire(lead_id)
        fleet.claim(self._second_connection(), self.tenant, "worker-B", kinds=[self.kind])
        with self.assertRaises(fleet.LeaseLost):
            fleet.renew(self.conn, mine, "worker-A")
        self.assertEqual(self._row(lead_id)["owner_agent"], "worker-B",
                         "a stale claim's renewal disturbed the live one")

    # ---------------------------------------- 5. no token is an error, not a default

    def test_a_lead_with_no_lease_token_raises_rather_than_defaulting(self) -> None:
        """`fleet.lease_token` raises on a lead dict that has no token. No fallbacks.

        There is no safe value to substitute, and the two candidates fail in opposite
        directions. Fencing on `NULL` fails *closed* — `NULL = anything` is never true in
        SQL — but silently, and the caller would see `LeaseLost` for a lead nobody had
        contended, sending `work_once` down `_record_lease_lost` for a race that never
        happened. Fencing on nothing at all reopens `013`.

        A lead dict without a token did not come from `claim`, and that is a programming
        error, not a race — so it raises `ValueError` and not `LeaseLost`, which is the
        distinction the assertion below is actually making. The lead is checked afterwards
        to confirm the raise happened *before* any write, not after one.
        """
        lead_id = self._lead()
        claimed = dict(fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0])
        del claimed["lease_token"]

        with self.assertRaises(ValueError) as caught:
            fleet.complete(self.conn, claimed, fleet.Outcome(), agent_name="worker-A")
        self.assertNotIsInstance(caught.exception, fleet.LeaseLost,
                                 "a missing token is a bug in the caller, not a lost race")
        self.assertIn("lease_token", str(caught.exception))
        self.assertEqual(self._row(lead_id)["state"], "pending",
                         "the lead was written before the missing token was noticed")

    def test_an_explicitly_null_token_is_refused_too(self) -> None:
        """The other shape of the same mistake: the key is present and its value is `NULL`.

        Rows that predate migration `013` carry `lease_token IS NULL` — the migration is
        additive on purpose, because a `NOT NULL` column could not have been validated
        against a live cluster whose existing rows have none. So a `NULL` token is not a
        contrived value; it is what a real row looked like yesterday, and `lease_token`
        must reject it by the same route as a missing key rather than passing it through
        to a `WHERE lease_token = NULL` that quietly matches nothing.
        """
        lead_id = self._lead()
        claimed = dict(fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0])
        claimed["lease_token"] = None

        with self.assertRaises(ValueError):
            fleet.complete(self.conn, claimed, fleet.Outcome(), agent_name="worker-A")
        self.assertEqual(self._row(lead_id)["state"], "pending")

    def test_a_forged_token_does_not_pass_the_fence(self) -> None:
        """The capability argument, stated as an attack rather than as prose.

        A token the caller invented is a token no `claim` ever wrote, so it matches no
        row. The lease here is fully live and the name is correct — every other column the
        fence reads says yes — and the write is still refused. That is what makes
        `lease_token` a capability rather than a claim about identity: possession of the
        value returned by `claim` is the whole authority, and it cannot be asserted, only
        held.
        """
        lead_id = self._lead()
        claimed = dict(fleet.claim(self.conn, self.tenant, "worker-A", kinds=[self.kind])[0])
        claimed["lease_token"] = str(uuid.uuid4())

        with self.assertRaises(fleet.LeaseLost):
            fleet.complete(self.conn, claimed, fleet.Outcome(), agent_name="worker-A")
        self.assertEqual(self._row(lead_id)["state"], "pending",
                         "a token the caller made up completed a lead")


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class WorkerShutdownIsNotFenced(_ClusterFixture):
    """The one path this audit found that still fences on the worker *name* alone.

    `fleet.worker` is the context manager `ingest.drain` wraps its whole run in. On the
    way out of a clean shutdown it releases the leases the drain was holding, so a Ctrl-C
    during a demo does not leave rows looking claimed for two minutes:

        UPDATE lead SET owner_agent = NULL, lease_expires_at = NULL, lease_token = NULL
         WHERE tenant_id = %s AND owner_agent = %s AND state = 'pending'

    That `WHERE` is the exact predicate migration `013` was written to retire, still in
    service. `owner_agent` is a name, `ingest.py`'s default for it is `"ingest-cli"`, and
    `claim` does not move `state` off `pending` when it takes a row — so **a drain exiting
    cleanly releases the live leases of every other drain running under the same name**,
    including ones that are mid-fetch.

    ## Why this is a smaller bug than the one `013` closed, and still a real one

    It cannot cause a double *write*. The victim's token is set to `NULL` on the row, so
    its own fence — `lease_token = <mine>` — now matches nothing, and `complete`, `fail`,
    `_defer`, `renew` and `_reacquire` all refuse it. The failure is fail-closed in the
    direction that matters, which is why this is reported rather than treated as an
    emergency.

    What it does cause is a lead becoming claimable while a worker is still fetching for
    it. A third drain claims it, pays the embedding or HTTP provider for work already in
    flight, and the first worker's run lands in `agent_run` as `lease_lost` — a race
    reported where none was contended for, and real money spent twice. It also converts
    what would have been a clean `renew` into a lease loss, which
    `_reschedule_after_lease_loss` then declines to back off (the token no longer matches),
    leaving the lead to whoever grabbed it.

    ## Why these tests assert the defect instead of the fix

    They are characterisation tests: they pin the behaviour that is actually there, and
    each one says in its own body which assertion is expected to invert once the tidy-up
    is fenced. Writing them the other way round — asserting the correct behaviour and
    leaving them red — would put a failing suite in the repository and make the next
    person's real regression invisible in the noise.

    A fix is not one line, which is why it is not in this change. The tidy-up releases
    *whatever this worker holds*, and it does not know the tokens it minted — `claim`
    returns them to `work_once`, not to `worker`. Fencing it properly means the context
    manager tracking the tokens handed out under it, which is a change to the shape of the
    module and a decision for its owner.

    Shares `_ClusterFixture` with `LeaseFencing` — same private kind, same exact-delete
    teardown, so these are as safe against the live cluster as everything above.
    """

    def test_shutdown_releases_a_lead_held_by_a_different_worker_of_the_same_name(self) -> None:
        """The hole itself. Two drains, one name, and the wrong lease is released.

        Worker A exits — it holds nothing at all by this point — and worker B's live,
        unexpired, freshly-token'd claim is cleared out from under it. Nothing about the
        row says it was ever B's.

        **When `fleet.worker` is fenced, the assertions below invert**: `owner_agent`
        should still read `"ingest-cli"` and `lease_token` should still be B's.
        """
        lead_id = self._lead()
        name = self.shared_name
        other = self._second_connection()

        held = fleet.claim(other, self.tenant, name, kinds=[self.kind])[0]
        self.assertIsNotNone(held["lease_token"])

        # A second drain under the same name starts and stops without doing anything.
        with fleet.worker(self.conn, self.tenant, name):
            pass

        row = self._row(lead_id)
        self.assertIsNone(row["owner_agent"],
                          "EXPECTED TO INVERT once `fleet.worker` is token-fenced: a "
                          "namesake's shutdown released a live claim it never held")
        self.assertIsNone(row["lease_token"])

    def test_the_stripped_worker_still_cannot_write_so_no_row_is_duplicated(self) -> None:
        """The saving grace, asserted so it is not merely believed.

        Having had its lease stripped by a namesake's shutdown, worker B tries to complete.
        It is refused — `lease_token = <mine>` matches a row whose token is now `NULL`, and
        `NULL` equals nothing. So the unfenced tidy-up cannot produce the duplicate write
        that `013` exists to prevent; it can only strand the work.

        This assertion should hold before *and* after `fleet.worker` is fixed, for
        different reasons: today because the token was blanked, afterwards because it was
        never touched and B simply completes. It is the invariant, not the bug.
        """
        lead_id = self._lead()
        name = self.shared_name
        other = self._second_connection()
        held = fleet.claim(other, self.tenant, name, kinds=[self.kind])[0]

        with fleet.worker(self.conn, self.tenant, name):
            pass

        with self.assertRaises(fleet.LeaseLost):
            fleet.complete(other, held, fleet.Outcome(summary="mid-fetch, lease stripped"),
                           agent_name=name)
        self.assertNotEqual(self._row(lead_id)["state"], "done")

    def test_the_stripped_lead_becomes_immediately_claimable_by_a_third_worker(self) -> None:
        """The cost of the hole, made concrete: a duplicate paid fetch.

        The lead B is still fetching for is claimable the instant A exits — no lease left
        to stop it. A third drain takes it and does the work a second time, which for
        `embed_party` means paying the embedding provider again for the same party.

        **When `fleet.worker` is fenced, this claim should come back empty.**
        """
        self._lead()
        name = self.shared_name
        other = self._second_connection()
        fleet.claim(other, self.tenant, name, kinds=[self.kind])

        with fleet.worker(self.conn, self.tenant, name):
            pass

        third = fleet.claim(self.conn, self.tenant, "worker-C", kinds=[self.kind])
        self.assertEqual(len(third), 1,
                         "EXPECTED TO INVERT once `fleet.worker` is token-fenced: this "
                         "should be [] — the lead is still being worked")


if __name__ == "__main__":
    unittest.main()
