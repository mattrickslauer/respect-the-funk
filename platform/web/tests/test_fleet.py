"""The coordination primitive, tested where it can be and against the cluster where it must.

    python -m unittest discover platform/web/tests

Two layers, deliberately:

  * **Offline** — the pure decisions (backoff schedule, parking threshold). Runs
    everywhere, costs nothing, and is the majority of the logic.

  * **Against the cluster** — the claim itself. `FOR UPDATE SKIP LOCKED`, lease expiry
    and `ON CONFLICT DO NOTHING` are *database* behaviours; a fake that returns rows
    from a dict proves only that the fake works. These skip when `DATABASE_URL` is
    unset, so the default run stays free, and they clean up after themselves through a
    dedicated tenant that is dropped in `tearDown`.

The property under test is the one the whole architecture rests on: **two workers asking
for work at the same time never get the same row.** If that is not true, every other
guarantee in `PLATFORM-SPEC` is decoration.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from spindle import fleet, spend

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class Backoff(unittest.TestCase):

    def test_first_failure_waits_the_shortest_interval(self):
        self.assertEqual(fleet.backoff_seconds(1), fleet.BACKOFF[0])

    def test_backoff_is_monotonic(self):
        waits = [fleet.backoff_seconds(n) for n in range(1, 10)]
        self.assertEqual(waits, sorted(waits), "a backoff that shrinks is not a backoff")

    def test_backoff_saturates_rather_than_indexing_off_the_end(self):
        self.assertEqual(fleet.backoff_seconds(99), fleet.BACKOFF[-1])

    def test_zero_attempts_still_returns_a_wait(self):
        # Defensive: a caller passing 0 should not get an IndexError or a 0-second retry.
        self.assertGreater(fleet.backoff_seconds(0), 0)

    def test_parking_threshold_is_reachable_before_the_backoff_runs_out(self):
        # If MAX_ATTEMPTS exceeded the schedule length, leads would retry at the longest
        # interval forever instead of parking. Cheap to assert, expensive to discover.
        self.assertLessEqual(fleet.MAX_ATTEMPTS, len(fleet.BACKOFF) + 2)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Claiming(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        self.slug = f"test-fleet-{self.tenant[:8]}"
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, self.slug, "fleet test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _lead(self, kind: str = "probe", **over) -> str:
        lead_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lead (id, tenant_id, scope_kind, kind, adapter, target,
                                     target_hash, next_action_at, cadence_seconds)
                   VALUES (%s, %s, 'tenant', %s, 'test', %s, %s,
                           now() + (%s || ' seconds')::INTERVAL, %s)""",
                (lead_id, self.tenant, kind, over.get("target", lead_id),
                 over.get("target_hash", lead_id), str(over.get("due_in", 0)),
                 over.get("cadence_seconds")),
            )
        return lead_id

    def _state(self, lead_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lead WHERE id = %s", (lead_id,))
            return cur.fetchone()

    # ------------------------------------------------------------------ the property

    def test_two_workers_never_claim_the_same_lead(self):
        """The one that matters. Everything else assumes this holds."""
        import psycopg
        from psycopg.rows import dict_row

        for _ in range(12):
            self._lead()

        other = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                row_factory=dict_row)
        try:
            a = fleet.claim(self.conn, self.tenant, "worker-a", kinds=["probe"], batch=6)
            b = fleet.claim(other, self.tenant, "worker-b", kinds=["probe"], batch=6)
        finally:
            other.close()

        ids_a = {r["id"] for r in a}
        ids_b = {r["id"] for r in b}
        self.assertEqual(ids_a & ids_b, set(), "the same lead was handed to two workers")
        self.assertEqual(len(ids_a) + len(ids_b), 12, "some leads were never handed out")

    def test_a_claimed_lead_is_not_reclaimed_while_its_lease_holds(self):
        self._lead()
        first = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])
        second = fleet.claim(self.conn, self.tenant, "b", kinds=["probe"])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [], "a live lease did not protect the row")

    def test_an_expired_lease_returns_the_work_with_nobody_noticing(self):
        """The restart story: no supervisor, no heartbeat, just a timestamp."""
        lead_id = self._lead()
        fleet.claim(self.conn, self.tenant, "dead-worker", kinds=["probe"])
        with self.conn.cursor() as cur:  # simulate the worker having died 10m ago
            cur.execute(
                "UPDATE lead SET lease_expires_at = now() - INTERVAL '10 minutes' "
                "WHERE id = %s", (lead_id,))
        again = fleet.claim(self.conn, self.tenant, "fresh-worker", kinds=["probe"])
        self.assertEqual(len(again), 1)
        self.assertEqual(str(again[0]["id"]), lead_id)

    def test_a_lead_not_yet_due_is_not_claimed(self):
        self._lead(due_in=3600)
        self.assertEqual(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"]), [])

    def test_kind_filters_the_claim(self):
        self._lead(kind="probe")
        self._lead(kind="other", target_hash=str(uuid.uuid4()))
        claimed = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["kind"], "probe")

    # ------------------------------------------------------------------- completion

    def test_completing_a_one_shot_lead_marks_it_done_and_clears_the_lease(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.complete(self.conn, lead, fleet.Outcome(summary="did the thing"),
                      agent_name="a")
        row = self._state(lead_id)
        self.assertEqual(row["state"], "done")
        self.assertIsNone(row["owner_agent"])
        self.assertIsNone(row["lease_expires_at"])

    def test_a_lead_with_a_cadence_reschedules_instead_of_finishing(self):
        """One nullable column is the difference between a crawler and a frontier."""
        lead_id = self._lead(cadence_seconds=900)
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.complete(self.conn, lead, fleet.Outcome(), agent_name="a")
        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending")
        self.assertIsNone(row["owner_agent"])
        # And it is not immediately claimable again.
        self.assertEqual(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"]), [])

    def test_follow_on_leads_are_inserted_in_the_same_transaction(self):
        self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        child_hash = str(uuid.uuid4())
        fleet.complete(self.conn, lead, fleet.Outcome(follow_on=[
            {"kind": "downstream", "target_hash": child_hash, "target": "x",
             "adapter": "test", "reason": "because the parent said so"},
        ]), agent_name="a")
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lead WHERE target_hash = %s", (child_hash,))
            child = cur.fetchone()
        self.assertIsNotNone(child, "the follow-on lead was not written")
        self.assertEqual(child["depth"], 1)
        self.assertEqual(str(child["parent_lead_id"]), str(lead["id"]))

    def test_a_duplicate_follow_on_does_not_fan_out(self):
        """Idempotence lives in the unique constraint, not in the agent remembering."""
        shared = str(uuid.uuid4())
        for _ in range(2):
            self._lead(target_hash=str(uuid.uuid4()))
            lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
            fleet.complete(self.conn, lead, fleet.Outcome(follow_on=[
                {"kind": "downstream", "target_hash": shared, "target": "x"},
            ]), agent_name="a")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s", (shared,))
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_completing_with_an_expired_lease_raises_instead_of_marking_it_done(self):
        """The fence `complete`, `fail` and `_defer` share: a worker whose lease already
        expired must not be able to silently finish a lead it no longer owns — that
        silent no-op is exactly how the measured bug produced `agent_run` rows nobody
        could explain and duplicate `party_metric` rows behind them.
        """
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        with self.conn.cursor() as cur:
            cur.execute("UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                        "WHERE id = %s", (lead_id,))
        with self.assertRaises(fleet.LeaseLost):
            fleet.complete(self.conn, lead, fleet.Outcome(), agent_name="a")
        row = self._state(lead_id)
        self.assertNotEqual(row["state"], "done",
                            "an expired lease must not be able to complete the lead")

    def test_failing_with_an_expired_lease_raises_instead_of_touching_it(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        with self.conn.cursor() as cur:
            cur.execute("UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                        "WHERE id = %s", (lead_id,))
        with self.assertRaises(fleet.LeaseLost):
            fleet.fail(self.conn, lead, "provider said 503", agent_name="a")
        row = self._state(lead_id)
        self.assertEqual(row["attempts"], 0,
                         "an expired lease must not be able to record a failure either")

    # ---------------------------------------------------------------------- failure

    def test_failure_backs_off_and_stays_pending(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.fail(self.conn, lead, "provider said 503", agent_name="a")
        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("503", row["last_error"])
        self.assertEqual(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"]), [],
                         "a backed-off lead was immediately reclaimable")

    def test_a_permanent_failure_parks_immediately(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.fail(self.conn, lead, "malformed target", agent_name="a", permanent=True)
        self.assertEqual(self._state(lead_id)["state"], "failed")

    def test_a_poisoned_lead_parks_rather_than_retrying_forever(self):
        lead_id = self._lead()
        lead = dict(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0])
        for attempt in range(fleet.MAX_ATTEMPTS):
            lead["attempts"] = attempt
            # `fail` clears the claim on every non-parked call (it puts the lead back to
            # `pending`, unowned, for backoff), so calling it again on the same captured
            # `lead` — deliberately skipping the backoff-delayed re-claim, to exercise
            # just the parking-threshold logic — needs the claim re-established each
            # time: the name, the lease *and* the token, or the fence sees a lead this
            # claim no longer holds and raises `LeaseLost` instead of the parking
            # behaviour under test.
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE lead SET owner_agent = 'a', lease_token = %s, "
                    "lease_expires_at = now() + INTERVAL '120 seconds' WHERE id = %s",
                    (lead["lease_token"], lead_id),
                )
            fleet.fail(self.conn, lead, "always throws", agent_name="a")
        self.assertEqual(self._state(lead_id)["state"], "failed")

    # ------------------------------------------------------------------- the loop

    def test_work_once_records_a_run_per_lead(self):
        self._lead()
        self._lead(target_hash=str(uuid.uuid4()))

        def agent(conn, lead, gate):
            return fleet.Outcome(summary="ok", facts=1)

        worked = fleet.work_once(self.conn, self.tenant, "prober", agent, kinds=["probe"])
        self.assertEqual(worked, 2)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM agent_run WHERE tenant_id = %s "
                        "AND state = 'ok'", (self.tenant,))
            self.assertEqual(cur.fetchone()["n"], 2)

    def test_a_network_agents_successful_write_commits_with_completion(self):
        """`fleet.NetworkAgent` is the shape a real agent that calls out to a source
        adapter or an embedding provider uses. `fetch` does the network I/O; `write`
        does the database writes and runs inside `work_once`'s transaction alongside
        `record_run` and `complete`, exactly like a plain `Agent`.
        """
        lead_id = self._lead()

        def fetch(conn, lead, gate):
            return {"value": 42}

        def write(conn, lead, gate, prepared):
            return fleet.Outcome(summary=f"wrote {prepared['value']}", facts=1)

        worked = fleet.work_once(self.conn, self.tenant, "prober",
                                 fleet.NetworkAgent(fetch=fetch, write=write),
                                 kinds=["probe"])
        self.assertEqual(worked, 1)
        self.assertEqual(self._state(lead_id)["state"], "done")
        with self.conn.cursor() as cur:
            cur.execute("SELECT state, facts FROM agent_run WHERE lead_id = %s", (lead_id,))
            run = cur.fetchone()
        self.assertEqual(run["state"], "ok")
        self.assertEqual(run["facts"], 1)

    def test_a_network_agents_fetch_runs_outside_the_transaction_its_write_runs_inside(self):
        """The reason `NetworkAgent` exists: a slow network call must never sit inside the
        transaction that also writes `agent_run` and completes the lead. Prove both
        halves — whatever `fetch` writes on its own is durable even when `write` later
        fails, because `fetch` ran with no transaction open; whatever `write` does rolls
        back together with its own failure, because it runs inside `work_once`'s
        transaction.
        """
        lead_id = self._lead()
        fetch_marker = str(uuid.uuid4())
        write_marker = str(uuid.uuid4())

        def fetch(conn, lead, gate):
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                         target_hash, next_action_at)
                       VALUES (%s, 'tenant', 'marker', 'test', %s, %s, now())""",
                    (self.tenant, fetch_marker, fetch_marker),
                )
            return {"ok": True}

        def write(conn, lead, gate, prepared):
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                         target_hash, next_action_at)
                       VALUES (%s, 'tenant', 'marker', 'test', %s, %s, now())""",
                    (self.tenant, write_marker, write_marker),
                )
            raise fleet.LeadFailed("write blew up after its own insert")

        agent = fleet.NetworkAgent(fetch=fetch, write=write)
        fleet.work_once(self.conn, self.tenant, "prober", agent, kinds=["probe"])

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s",
                        (fetch_marker,))
            self.assertEqual(
                cur.fetchone()["n"], 1,
                "fetch's own write should survive — it ran with no transaction open")
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s",
                        (write_marker,))
            self.assertEqual(
                cur.fetchone()["n"], 0,
                "write's insert should have rolled back with its own failure")

        self.assertEqual(self._state(lead_id)["state"], "pending")

    def test_completes_refusal_rolls_back_whatever_was_written_before_it(self):
        """The scenario the shared transaction exists for, reduced to the two statements
        that matter: something is written, then `complete` refuses, and the write must go
        with it. Pre-`194b972` the connection was in autocommit and those were two
        commits, so the write survived a refusal that was itself a silent no-op.

        This drives `complete` directly rather than through `work_once`, because the
        route `work_once` would take to reach the same point no longer exists. A second
        worker cannot steal the lead mid-write-phase: `_reacquire` holds
        `SELECT … FOR UPDATE` on the row for the whole phase, so a second connection
        trying it would block on our lock while we block on its reply — a hang, not a
        race. And the clock cannot run out mid-phase either, because CockroachDB's `now()`
        is the *transaction's* timestamp, so `complete` and `_reacquire` read the same
        instant by construction. What is left to prove is the rollback itself, and a
        stale token proves it without needing either.
        """
        lead_id = self._lead()
        lead = dict(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0])
        marker_hash = str(uuid.uuid4())
        # A token from a claim that is no longer the current one. `complete` must refuse
        # on it however live the lease looks.
        lead["lease_token"] = str(uuid.uuid4())

        with self.assertRaises(fleet.LeaseLost):
            with self.conn.transaction():
                with self.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                             target_hash, next_action_at)
                           VALUES (%s, 'tenant', 'marker', 'test', %s, %s, now())""",
                        (self.tenant, marker_hash, marker_hash),
                    )
                fleet.complete(self.conn, lead, fleet.Outcome(summary="stale claim"),
                               agent_name="a")

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s",
                        (marker_hash,))
            self.assertEqual(
                cur.fetchone()["n"], 0,
                "the write should have rolled back with `complete`'s refusal")
        self.assertEqual(self._state(lead_id)["state"], "pending",
                         "a stale claim marked the lead done anyway")

    def test_a_lease_lost_to_a_second_worker_writes_nothing_and_leaves_the_lead_alone(self):
        """The contended half. A second worker claims the lead while the first is still
        fetching — the window where the first holds no lock on the row, which is the only
        window in which this can happen at all now that `_reacquire` locks the row for the
        write phase.

        Nothing of the first worker's lands, and — the part the uncontended path gets
        wrong if you are not careful — the lead is left exactly as the second worker set
        it. Backing it off here would clear a live lease out from under a run that is
        going fine.
        """
        import psycopg
        from psycopg.rows import dict_row

        lead_id = self._lead()
        marker_hash = str(uuid.uuid4())
        other = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                row_factory=dict_row)

        def fetch(conn, lead, gate):
            with other.cursor() as cur:
                cur.execute(
                    "UPDATE lead SET owner_agent = 'someone-else', "
                    "lease_token = gen_random_uuid(), "
                    "lease_expires_at = now() + INTERVAL '120 seconds' WHERE id = %s",
                    (lead_id,),
                )
            return {"stale": True}

        def write(conn, lead, gate, prepared):
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                         target_hash, next_action_at)
                       VALUES (%s, 'tenant', 'marker', 'test', %s, %s, now())""",
                    (self.tenant, marker_hash, marker_hash),
                )
            return fleet.Outcome(summary="should never land")

        try:
            fleet.work_once(self.conn, self.tenant, "prober",
                            fleet.NetworkAgent(fetch=fetch, write=write),
                            kinds=["probe"])
        finally:
            other.close()

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s",
                        (marker_hash,))
            self.assertEqual(cur.fetchone()["n"], 0,
                             "a worker that had lost its lead still wrote for it")

        row = self._state(lead_id)
        self.assertEqual(row["owner_agent"], "someone-else",
                         "the lead must be left exactly as the new owner set it")
        self.assertEqual(row["attempts"], 0,
                         "the loser of the race charged an attempt to the winner's lead")
        self.assertEqual(row["state"], "pending")

        with self.conn.cursor() as cur:
            cur.execute("SELECT state, summary FROM agent_run WHERE lead_id = %s",
                        (lead_id,))
            runs = cur.fetchall()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["state"], "lease_lost")
        self.assertIn("another claim holds", runs[0]["summary"])

    def test_one_bad_lead_does_not_stop_the_batch(self):
        good = self._lead()
        bad = self._lead(target_hash=str(uuid.uuid4()))

        def agent(conn, lead, gate):
            if str(lead["id"]) == bad:
                raise fleet.LeadFailed("nope")
            return fleet.Outcome(summary="fine")

        fleet.work_once(self.conn, self.tenant, "prober", agent, kinds=["probe"])
        self.assertEqual(self._state(good)["state"], "done")
        self.assertEqual(self._state(bad)["state"], "pending")
        self.assertEqual(self._state(bad)["attempts"], 1)

    def test_a_refused_spend_defers_without_burning_an_attempt(self):
        """Raising the ceiling should let the work run, not find it parked."""
        lead_id = self._lead()

        def agent(conn, lead, gate):
            raise spend.SpendRefused("paid calls are off",
                                     estimate_usd=Decimal("0.01"), reason="disabled")

        fleet.work_once(self.conn, self.tenant, "prober", agent, kinds=["probe"])
        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 0, "a refusal counted as a failure")
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM agent_run WHERE lead_id = %s", (lead_id,))
            self.assertEqual(cur.fetchone()["state"], "refused")

    def test_an_agents_write_rolls_back_with_its_failure(self):
        """The property this fix exists for. `db.py` opens the connection in autocommit,
        so an agent's write and the failure that follows it are two separate commits
        unless `work_once` puts them in the same transaction. Measured on the live
        cluster as three `agent_run` rows with `state = 'ok'` for one lead before it
        reached `done`, and three duplicate `party_metric` rows alongside them — a crash
        (or here, a raise) between the write and the lead's completion left the write
        durable and the lead still claimable, so the next worker ran it again.

        A fake agent that writes a row and then raises reproduces the same shape without
        needing an actual crash: on the current, unfixed code the row survives the
        failure, because the write already committed by the time `LeadFailed` is raised.
        """
        lead_id = self._lead()
        marker_hash = str(uuid.uuid4())

        def bad_agent(conn, lead, gate):
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO lead (tenant_id, scope_kind, kind, adapter, target,
                                         target_hash, next_action_at)
                       VALUES (%s, 'tenant', 'marker', 'test', %s, %s, now())""",
                    (self.tenant, marker_hash, marker_hash),
                )
            raise fleet.LeadFailed("wrote something, then blew up")

        fleet.work_once(self.conn, self.tenant, "prober", bad_agent, kinds=["probe"])

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s",
                        (marker_hash,))
            self.assertEqual(
                cur.fetchone()["n"], 0,
                "the agent's write survived a failure it should have rolled back with")

        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending",
                         "a failed lead must be claimable again or parked, "
                         "not stuck as though nothing happened")
        self.assertEqual(row["attempts"], 1)

    def test_an_unexpected_exception_is_contained(self):
        lead_id = self._lead()

        def agent(conn, lead, gate):
            raise ZeroDivisionError("agents have bugs")

        # The fleet must survive an agent that does not raise LeadFailed.
        fleet.work_once(self.conn, self.tenant, "prober", agent, kinds=["probe"])
        self.assertEqual(self._state(lead_id)["state"], "pending")
        with self.conn.cursor() as cur:
            cur.execute("SELECT state, error FROM agent_run WHERE lead_id = %s", (lead_id,))
            run = cur.fetchone()
        self.assertEqual(run["state"], "error")
        self.assertIn("ZeroDivisionError", run["error"])

    # ------------------------------------------------- losing a lease to nobody

    def _lease_burner(self, lead_id: str):
        """An agent whose fetch outlives its own lease, with no second worker involved.

        Stands in for the real thing: `embed_batch` and the source adapters make one HTTP
        call that can run twenty or thirty seconds, and `claim` stamps one deadline across
        a whole `BATCH = 5`. Expiring the lease from inside `fetch` reproduces that in a
        millisecond and without a network.
        """
        def fetch(conn, lead, gate):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                    "WHERE id = %s", (lead_id,))
            return {"fetched": True}

        def write(conn, lead, gate, prepared):
            return fleet.Outcome(summary="came back to a lease that had run out")

        return fleet.NetworkAgent(fetch=fetch, write=write)

    def test_a_lease_lost_to_nobody_backs_off_instead_of_spinning(self):
        """The failure the ownership fence introduced, and the reason for this round.

        On the no-contender path the fence raises `LeaseLost` and the first version of
        the handler deliberately did not touch the lead: `state` stayed `pending`,
        `next_action_at` stayed in the past, `attempts` stayed put and no backoff was
        applied. `ingest.drain` loops while `work_once` returns non-zero and `work_once`
        returns `len(leads)`, so the same lead is re-claimed and re-fetched immediately,
        forever, paying the embedding or HTTP provider on every pass — and `MAX_ATTEMPTS`
        never parks it because `fail` is never reached. Silent duplication traded for a
        money-burning livelock.

        Nobody else claims this lead at any point. The row is still ours, and the only
        correct thing to do with it is what `fail` would do.
        """
        lead_id = self._lead()
        fleet.work_once(self.conn, self.tenant, "solo",
                        self._lease_burner(lead_id), kinds=["probe"])

        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending",
                         "an uncontended lease loss is a retry, not a completion")
        self.assertEqual(row["attempts"], 1,
                         "the lease loss did not count as an attempt — nothing will ever "
                         "park this lead")
        self.assertIsNone(row["owner_agent"], "the lost lease was not released")
        self.assertIsNone(row["lease_token"], "the stale claim token was left on the row")
        self.assertEqual(
            fleet.claim(self.conn, self.tenant, "solo", kinds=["probe"]), [],
            "the lead is immediately re-claimable — this is the livelock, and the next "
            "pass pays the provider again")

        with self.conn.cursor() as cur:
            cur.execute("SELECT state, summary FROM agent_run WHERE lead_id = %s",
                        (lead_id,))
            run = cur.fetchone()
        self.assertEqual(run["state"], "lease_lost")
        self.assertIn("no other claimant", run["summary"],
                      "the run does not say which kind of lease loss this was")

    def test_repeated_lease_loss_parks_the_lead_rather_than_spinning(self):
        """Backoff alone would still retry forever, just more slowly. `MAX_ATTEMPTS` has
        to engage on this path exactly as it does for any other repeated failure.

        `next_action_at` is dragged back to now between rounds — that is standing in for
        the backoff elapsing, not routing around it; the previous assertion already
        proved the backoff is applied.
        """
        lead_id = self._lead()
        agent = self._lease_burner(lead_id)
        for _ in range(fleet.MAX_ATTEMPTS):
            with self.conn.cursor() as cur:
                cur.execute("UPDATE lead SET next_action_at = now() WHERE id = %s",
                            (lead_id,))
            worked = fleet.work_once(self.conn, self.tenant, "solo", agent,
                                     kinds=["probe"])
            self.assertEqual(worked, 1, "the lead stopped being picked up mid-way")

        row = self._state(lead_id)
        self.assertEqual(row["state"], "failed",
                         "a lead that repeatedly outlives its lease must park for a "
                         "human, not retry until the spend ceiling stops it")
        self.assertEqual(row["attempts"], fleet.MAX_ATTEMPTS)
        self.assertEqual(fleet.claim(self.conn, self.tenant, "solo", kinds=["probe"]), [],
                         "a parked lead was still claimable")

    def test_a_lease_lost_while_recording_a_failure_does_not_erase_the_real_error(self):
        """The agent's real error must survive even when recording it loses the race.

        `LeadFailed` is raised by `fetch`, same as `_lease_burner`, but `fetch` also
        expires the lease first — so by the time `work_once`'s `except LeadFailed`
        handler tries to run `fail`, the lease is already gone, `fail`'s fence raises
        `LeaseLost`, and the `record_run` that carried the agent's real message rolls
        back with it. Before this fix, only the lease-loss text survived into
        `agent_run.error` and `lead.last_error` — an operator reading the row to find out
        why this lead failed would see "lease expired" and never learn it was actually
        "document body is empty".
        """
        lead_id = self._lead()

        def fetch(conn, lead, gate):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                    "WHERE id = %s", (lead_id,))
            raise fleet.LeadFailed("document body is empty", permanent=True)

        def write(conn, lead, gate, prepared):
            raise AssertionError("write must never run — fetch already raised")

        fleet.work_once(self.conn, self.tenant, "solo",
                        fleet.NetworkAgent(fetch=fetch, write=write), kinds=["probe"])

        row = self._state(lead_id)
        self.assertIn("document body is empty", row["last_error"],
                      "the real cause of the failure was lost behind the lease-loss text")

        with self.conn.cursor() as cur:
            cur.execute("SELECT state, error FROM agent_run WHERE lead_id = %s", (lead_id,))
            run = cur.fetchone()
        self.assertEqual(run["state"], "lease_lost")
        self.assertIn("document body is empty", run["error"],
                      "agent_run.error should still say what the agent actually raised")

    # ------------------------------------------ two workers, one name, one lead

    def test_two_workers_sharing_a_name_cannot_both_pass_the_fence(self):
        """`owner_agent` is a worker *name*, and `ingest.py`'s CLI default for it is the
        constant `"ingest-cli"`. Two drains started from two terminals are both called
        that, so a fence on the name plus a live lease cannot tell them apart: the first
        worker's `complete` matches on the *second* worker's fresh lease and both write.
        That is the duplication the fence was added to stop, surviving through it.

        `claim` mints a `lease_token` per claim (migration 013), so the stale claim is
        distinguishable from the live one even though the two share every other column.
        """
        import psycopg
        from psycopg.rows import dict_row

        name = "ingest-cli"
        lead_id = self._lead()
        other = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                row_factory=dict_row)
        try:
            first = fleet.claim(self.conn, self.tenant, name, kinds=["probe"])[0]
            # The first worker is still fetching when its lease runs out.
            with self.conn.cursor() as cur:
                cur.execute("UPDATE lead SET lease_expires_at = now() - INTERVAL '1 min' "
                            "WHERE id = %s", (lead_id,))
            # A second drain, under the same name, legitimately picks the lead back up.
            second = fleet.claim(other, self.tenant, name, kinds=["probe"])[0]
            self.assertEqual(str(second["id"]), lead_id, "the re-claim did not happen")

            # The first worker comes back and tries to finish. It must not be able to.
            with self.assertRaises(fleet.LeaseLost):
                fleet.complete(self.conn, first, fleet.Outcome(summary="stale"),
                               agent_name=name)
            self.assertNotEqual(
                self._state(lead_id)["state"], "done",
                "a stale claim completed a lead the live claim under the same name owns")
            self.assertNotEqual(first["lease_token"], second["lease_token"],
                                "two claims of one lead produced the same token")

            # And the live claim is unharmed by the loser's attempt.
            fleet.complete(other, second, fleet.Outcome(summary="live"), agent_name=name)
        finally:
            other.close()

        row = self._state(lead_id)
        self.assertEqual(row["state"], "done")
        self.assertIsNone(row["lease_token"])

    def test_a_lead_dict_with_no_lease_token_is_refused_rather_than_defaulted(self):
        """No fallbacks. A token of `NULL` would fence against nothing and read as a lost
        lease; no token at all would reopen the hole. Neither is a default worth having.
        """
        lead_id = self._lead()
        lead = dict(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0])
        del lead["lease_token"]
        with self.assertRaises(ValueError):
            fleet.complete(self.conn, lead, fleet.Outcome(), agent_name="a")
        self.assertEqual(self._state(lead_id)["state"], "pending")

    # ------------------------------------------------- the batch's shared deadline

    def test_each_lead_gets_its_lease_measured_from_its_own_turn(self):
        """`claim` stamps one `lease_expires_at` across the whole batch in one `UPDATE`,
        and `work_once` then works those leads one at a time. At twenty or thirty seconds
        of HTTP per fetch, the last lead of a `BATCH = 5` pass would start on a deadline
        that was set four fetches ago. `renew`, called at the top of each lead's turn,
        is what stops the shared deadline from being the thing that decides.
        """
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                "WHERE id = %s", (lead_id,))

        # Uncontended: the token still matches, so this claim is still current and the
        # lapsed deadline is renewable rather than fatal.
        fleet.renew(self.conn, lead, "a")
        row = self._state(lead_id)
        self.assertGreater(row["lease_expires_at"], datetime.now(timezone.utc),
                           "an uncontended lapsed lease was not renewed")
        self.assertEqual(str(row["lease_token"]), str(lead["lease_token"]),
                         "renewing rotated the token, breaking the fence's identity")
        # …and the renewal is enough for the claim to finish normally.
        fleet.complete(self.conn, lead, fleet.Outcome(), agent_name="a")
        self.assertEqual(self._state(lead_id)["state"], "done")

    def test_renewing_a_lead_another_claim_took_is_refused(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE lead SET lease_expires_at = now() - INTERVAL '1 minute' "
                "WHERE id = %s", (lead_id,))
        fleet.claim(self.conn, self.tenant, "b", kinds=["probe"])
        with self.assertRaises(fleet.LeaseLost):
            fleet.renew(self.conn, lead, "a")
        self.assertEqual(self._state(lead_id)["owner_agent"], "b",
                         "a stale claim's renewal disturbed the live one")


if __name__ == "__main__":
    unittest.main()


class AgentRunNamesTheWork(unittest.TestCase):
    """`agent_run.agent_kind` must hold the kind of work, not the worker's name.

    `work_once` takes an `agent_name` that is the *worker* identity — `ingest-cli`,
    `masters-cli`, whatever `--worker` was set to — because that is what the lease
    fences on. `record_run` used to write that string into a column called
    `agent_kind`, and the effect was not cosmetic: `research.fleet` cross-references
    `agent_manifest.kind` against this column to decide whether an agent is running,
    merely declared, or code with no manifest row. With worker names in it, every real
    agent showed zero runs and every worker name appeared in the anomaly branch — the
    console reporting the shell invocation as though it were an agent.

    Source-level, because the alternative is a cluster round trip to assert one
    argument position, and the bug was always visible in the argument list.
    """

    def test_record_run_takes_the_kind_from_the_lead(self) -> None:
        import inspect

        from spindle import fleet

        source = inspect.getsource(fleet.record_run)
        params = source.split("VALUES")[1]
        self.assertIn('lead.get("kind")', params,
                      "agent_kind is not being taken from the lead")

    def test_the_worker_name_is_only_a_last_resort(self) -> None:
        """`agent_name` may remain as a fallback for a lead with no kind, but it must
        not be what the column normally receives.

        Comment lines are stripped before comparing, which the first version of this
        test did not do — and it failed against a correct fix, because the comment
        explaining *why* `agent_name` is wrong contains the word `agent_name` earlier in
        the source than the expression does. A source-reading test has to read the code
        and not the prose around it.
        """
        import inspect

        from spindle import fleet

        params = "\n".join(
            line for line in inspect.getsource(fleet.record_run).split("VALUES")[1].splitlines()
            if not line.strip().startswith("#"))

        self.assertIn('lead.get("kind")', params)
        self.assertLess(params.index('lead.get("kind")'), params.index("agent_name"),
                        "the worker name is being preferred over the lead's kind")
