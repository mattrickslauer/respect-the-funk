"""Run now, against the cluster.

The button existed from the wireframe commit (`b90107b`) and never did anything: the
queue view emitted it as a bare string, and the inspector renders a bare string as
`<button type="button">` with no form around it — the shape the template's own comment
labels "inert — a view with nothing to post to". Nothing was broken; nothing was ever
built. So the test that matters is not "the click posts" but "the post moves the row",
and that is a database question.

`expedite` is the whole of it. What Run now can honestly mean in this architecture is
*make this lead due*: there is no orchestrator to tell, only `lead.next_action_at` and a
worker that claims what has come due (`fleet.claim`). The assertions below are therefore
about claimability — the lead was not claimable, then it was — because that is the only
observable that the operator's click is supposed to produce.

Cluster-gated in a tenant created and dropped per test, the pattern `test_fleet.py` uses.
"""

from __future__ import annotations

import os
import unittest
import uuid

from spindle import fleet

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Expediting(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-runnow-{self.tenant[:8]}", "run now test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _lead(self, *, due_in: int = 0, state: str = "pending",
              attempts: int = 0, last_error: str = "") -> str:
        lead_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lead (id, tenant_id, scope_kind, kind, adapter, target,
                                     target_hash, state, attempts, last_error,
                                     next_action_at)
                   VALUES (%s, %s, 'tenant', 'probe', 'test', %s, %s, %s, %s, %s,
                           now() + (%s || ' seconds')::INTERVAL)""",
                (lead_id, self.tenant, lead_id, lead_id, state, attempts, last_error,
                 str(due_in)),
            )
        return lead_id

    def _row(self, lead_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM lead WHERE id = %s", (lead_id,))
            return cur.fetchone()

    def _claimable(self, lead_id: str) -> bool:
        claimed = fleet.claim(self.conn, self.tenant, "probe-worker", kinds=["probe"])
        return lead_id in {str(r["id"]) for r in claimed}

    # ------------------------------------------------------------ the property

    def test_a_lead_parked_until_tomorrow_becomes_claimable_now(self):
        """The case an operator actually clicks: backed off, and they will not wait."""
        lead_id = self._lead(due_in=86_400)
        self.assertFalse(self._claimable(lead_id), "a future lead was claimable already")

        fleet.expedite(self.conn, self.tenant, lead_id)

        self.assertTrue(self._claimable(lead_id),
                        "Run now did not make the lead claimable")

    def test_a_failed_lead_is_unparked_and_its_strikes_cleared(self):
        """Same reasoning as `ingest.retry_failed`: the operator is asserting the cause
        is gone, so carrying four strikes forward would park it again on one hiccup."""
        lead_id = self._lead(state="failed", attempts=fleet.MAX_ATTEMPTS,
                             last_error="no credential")

        fleet.expedite(self.conn, self.tenant, lead_id)

        row = self._row(lead_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 0)
        # The error text stays. It is the evidence of why the lead was parked, and the
        # next run overwrites it — blanking it here would erase the reason mid-diagnosis.
        self.assertEqual(row["last_error"], "no credential")
        self.assertTrue(self._claimable(lead_id))

    def test_a_finished_one_shot_lead_can_be_re_armed(self):
        lead_id = self._lead(state="done")

        fleet.expedite(self.conn, self.tenant, lead_id)

        self.assertEqual(self._row(lead_id)["state"], "pending")
        self.assertTrue(self._claimable(lead_id))

    # ------------------------------------------------------------- the refusals

    def test_a_lead_under_a_live_lease_is_refused_rather_than_stolen(self):
        """The one case where doing nothing is right — and saying so is the difference
        between this and the button that did nothing."""
        lead_id = self._lead()
        claimed = fleet.claim(self.conn, self.tenant, "busy-worker", kinds=["probe"])
        self.assertEqual(len(claimed), 1)

        with self.assertRaises(fleet.NotExpedited) as caught:
            fleet.expedite(self.conn, self.tenant, lead_id)

        self.assertIn("busy-worker", str(caught.exception))
        row = self._row(lead_id)
        # `claim` does not move `state`; the lease columns *are* the claim. So what must
        # survive an attempted expedite is the lease, not a state string.
        self.assertEqual(row["owner_agent"], "busy-worker",
                         "an expedite stole a live lease")
        self.assertEqual(row["lease_token"], claimed[0]["lease_token"],
                         "the lease token was reissued out from under a live claim")

    def test_an_expired_lease_does_not_protect_the_lead(self):
        """A lease is held by a clock. Once it lapses the row is nobody's, which is the
        same rule `claim` already applies — this must not be stricter than the claim."""
        lead_id = self._lead()
        fleet.claim(self.conn, self.tenant, "dead-worker", kinds=["probe"])
        with self.conn.cursor() as cur:
            # The worker died 10 minutes ago, and the lead is not due again until
            # tomorrow — so it is genuinely unclaimable until something moves the clock.
            cur.execute("""UPDATE lead
                              SET lease_expires_at = now() - INTERVAL '10 minutes',
                                  next_action_at = now() + INTERVAL '1 day'
                            WHERE id = %s""", (lead_id,))
        self.assertFalse(self._claimable(lead_id))

        fleet.expedite(self.conn, self.tenant, lead_id)

        row = self._row(lead_id)
        self.assertTrue(self._claimable(lead_id))
        # The dead worker's name and token stay on the row: they are what
        # `_reschedule_after_lease_loss` fences on to tell a lapsed lease from a stolen
        # one, and blanking them would make a straggler report a race that never happened.
        self.assertEqual(row["owner_agent"], "dead-worker")

    def test_a_lead_in_another_tenant_is_not_found(self):
        """The scoping check. `lead_id` comes off a URL, so an unscoped UPDATE here is
        one label re-arming another label's frontier."""
        lead_id = self._lead()
        other_tenant = str(uuid.uuid4())

        with self.assertRaises(fleet.NotExpedited):
            fleet.expedite(self.conn, other_tenant, lead_id)

        self.assertEqual(self._row(lead_id)["state"], "pending")


    # ------------------------------------------------------- what the view emits

    def test_the_queue_view_emits_run_now_as_a_form_and_not_a_label(self):
        """The original bug, asserted at the layer it lived in.

        `_inspector.html` renders a bare string as `<button type="button">` and a
        four-element tuple ending in `'post'` as a form. The queue emitted the first
        shape, so the control drew, hovered, depressed, and posted nowhere. Nothing
        about that is visible from a template test or a route test — it is the *shape
        of the tuple the builder returns*, so that is what is checked.
        """
        from spindle import research

        lead_id = self._lead()
        view = research.queue(self.conn, self.tenant)
        row = next(r for r in view.rows if r["id"] == lead_id)
        actions = next(s for s in row["insp"] if s.kind == "actions")

        run = next((a for a in actions.items
                    if not isinstance(a, str) and a[0] == "Run now"), None)
        self.assertIsNotNone(
            run, "'Run now' is still a bare string — the inert shape that started this")
        self.assertEqual(len(run), 4, "a three-element action renders as a link, not a form")
        self.assertEqual(run[3], "post")
        self.assertEqual(run[1], f"/queue/{lead_id}/run")


class TheRouteExists(unittest.TestCase):
    """No database: the button posts to a URL, and a URL nothing serves is a 405 that
    looks exactly like the inert button it replaced."""

    def test_queue_run_is_a_post_route(self):
        from spindle.routes import router

        posts = {r.path for r in router.routes if "POST" in getattr(r, "methods", ())}
        self.assertIn("/queue/{lead_id}/run", posts)
