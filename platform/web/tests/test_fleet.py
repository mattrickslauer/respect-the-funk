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
from decimal import Decimal

from rtf_platform import fleet, spend

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
        fleet.complete(self.conn, lead, fleet.Outcome(summary="did the thing"))
        row = self._state(lead_id)
        self.assertEqual(row["state"], "done")
        self.assertIsNone(row["owner_agent"])
        self.assertIsNone(row["lease_expires_at"])

    def test_a_lead_with_a_cadence_reschedules_instead_of_finishing(self):
        """One nullable column is the difference between a crawler and a frontier."""
        lead_id = self._lead(cadence_seconds=900)
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.complete(self.conn, lead, fleet.Outcome())
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
        ]))
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
            ]))
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM lead WHERE target_hash = %s", (shared,))
            self.assertEqual(cur.fetchone()["n"], 1)

    # ---------------------------------------------------------------------- failure

    def test_failure_backs_off_and_stays_pending(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.fail(self.conn, lead, "provider said 503")
        row = self._state(lead_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("503", row["last_error"])
        self.assertEqual(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"]), [],
                         "a backed-off lead was immediately reclaimable")

    def test_a_permanent_failure_parks_immediately(self):
        lead_id = self._lead()
        lead = fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0]
        fleet.fail(self.conn, lead, "malformed target", permanent=True)
        self.assertEqual(self._state(lead_id)["state"], "failed")

    def test_a_poisoned_lead_parks_rather_than_retrying_forever(self):
        lead_id = self._lead()
        lead = dict(fleet.claim(self.conn, self.tenant, "a", kinds=["probe"])[0])
        for attempt in range(fleet.MAX_ATTEMPTS):
            lead["attempts"] = attempt
            fleet.fail(self.conn, lead, "always throws")
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


if __name__ == "__main__":
    unittest.main()
