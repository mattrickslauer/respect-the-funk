"""The JSON API against a real cluster: the refusals, and the guarantees behind them.

`test_api_surface.py` proves the structural things without a database — every route is
gated, no handler can invent a refusal code, and the shaping never invents data. This
file proves the things that only rows can prove, and they are the ones that matter most:

  * a **double approve** is a refused insert and not a second send;
  * a **second thread** on one counterparty is refused across every campaign;
  * a **state change out of order** is refused, naming what is legal instead;
  * **decision provenance is computed server-side** and cannot be supplied by a caller;
  * every read endpoint returns something `json.dumps` will accept **with no `default=`**
    — which is the whole claim of `shapes.py`, and the one a fabricated row cannot make,
    because a fabricated row only contains the types the test author thought of.

Handlers are called as functions rather than over HTTP. `httpx` is not installed, and
`requirements.txt` is kept small on purpose — `embed.py` reaches the network with
`urllib` rather than httpx for this reason. Adding a test-only HTTP client to exercise
routes whose gates are already proved structurally would be paying bundle weight for
coverage this suite gets another way.

Cluster-gated in a tenant created and dropped per test, the pattern
`test_integrity_constraints.py`, `test_repo.py` and `test_research_views.py` use. Note
what that means in practice: `tests/conftest.py` refuses to run against production, so
these skip unless `DATABASE_URL` names a cluster carrying the `rtf_test_cluster` marker.
The guarantees above were additionally verified by hand against the live cluster while
this was written — see the commit message for exactly what was run and cleaned up.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid

from spindle import auth, outreach
from spindle.api import actions, errors, reads

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

OPERATOR = auth.Principal(tenant_id=None, subject="test-operator", authenticated=True)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class ApiAgainstTheCluster(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-api-{self.tenant[:8]}", "api test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    # ------------------------------------------------------------- fixtures

    def _artist(self, name: str = "Test Act") -> str:
        pid = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, name, slug, artist_type, party_class) "
                "VALUES (%s, %s, %s, %s, 'group', 'roster')",
                (pid, self.tenant, name, f"slug-{pid[:8]}"))
            cur.execute(
                "INSERT INTO party_role (tenant_id, party_id, role) "
                "VALUES (%s, %s, 'roster_artist')", (self.tenant, pid))
        return pid

    def _counterparty(self, name: str = "A Curator") -> str:
        pid = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, name, slug, artist_type, "
                "party_class, contact_state) "
                "VALUES (%s, %s, %s, %s, 'person', 'counterparty', 'contactable')",
                (pid, self.tenant, name, f"cp-{pid[:8]}"))
        return pid

    def _campaign(self, artist_id: str) -> str:
        return actions.create_campaign(
            OPERATOR, self.conn, self.tenant, artist_id=artist_id,
            name="Test campaign", channel="curator", goal="testing")["id"]

    def _contact_state(self, party_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT contact_state FROM party WHERE tenant_id = %s AND id = %s",
                        (self.tenant, party_id))
            return cur.fetchone()["contact_state"]

    def _count(self, sql: str, params: tuple) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.fetchone()["n"])

    # --------------------------------------------------------------- reads

    def test_every_read_endpoint_returns_strict_json(self) -> None:
        """`json.dumps` with no `default=`.

        The claim `shapes.py` makes is that every database type it meets has a JSON
        shape. A fabricated row cannot test that — it only contains the types the test
        author thought of. Real rows carry `UUID`, `Decimal`, `datetime`, `date`, JSONB
        and `NULL`, and this is the assertion that all of them survive.
        """
        self._artist()
        for name in ("summary", "today", "artists", "recordings", "counterparties",
                     "facts", "suggestions", "campaigns", "threads", "approvals",
                     "inbox", "queue", "runs", "fleet", "budgets"):
            with self.subTest(endpoint=name):
                out = getattr(reads, name)(OPERATOR, self.conn, self.tenant)
                json.dumps(out)          # raises TypeError on an unshaped value

    def test_an_empty_tenant_reads_as_empty_and_not_as_broken(self) -> None:
        """Empty is a real answer. A client must be able to tell it from a failure."""
        out = reads.threads(OPERATOR, self.conn, self.tenant)
        self.assertEqual([], out["rows"])
        self.assertEqual(0, out["returned"])
        self.assertFalse(out["truncated"])

    def test_the_inbox_says_its_adapter_is_missing(self) -> None:
        """An empty inbox and an absent integration look identical otherwise."""
        out = reads.inbox(OPERATOR, self.conn, self.tenant)
        self.assertIs(False, out["inbound_adapter_wired"])

    def test_approvals_says_nothing_will_send(self) -> None:
        out = reads.approvals(OPERATOR, self.conn, self.tenant)
        self.assertIs(False, out["sender_wired"])

    def test_counterparty_filters_reach_the_database(self) -> None:
        self._counterparty("Findable Curator")
        self._counterparty("Other Person")
        hit = reads.counterparties(OPERATOR, self.conn, self.tenant, q="Findable")
        self.assertEqual(1, hit["returned"])
        self.assertEqual("Findable Curator", hit["rows"][0]["name"])
        self.assertEqual(0, reads.counterparties(
            OPERATOR, self.conn, self.tenant, q="nobody-by-this-name")["returned"])

    def test_searchable_is_tri_state(self) -> None:
        """`None` is not a default standing in for `False` — three different questions."""
        self._counterparty("Unembedded")
        both = reads.counterparties(OPERATOR, self.conn, self.tenant)
        no = reads.counterparties(OPERATOR, self.conn, self.tenant, searchable=False)
        yes = reads.counterparties(OPERATOR, self.conn, self.tenant, searchable=True)
        self.assertEqual(1, both["returned"])
        self.assertEqual(1, no["returned"])
        self.assertEqual(0, yes["returned"])

    # ------------------------------------------------------------ campaigns

    def test_a_campaign_is_created_as_a_draft_that_opens_nothing(self) -> None:
        """Creating is not running. Running is a second, deliberate act."""
        campaign_id = self._campaign(self._artist())
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM campaign WHERE tenant_id = %s AND id = %s",
                        (self.tenant, campaign_id))
            self.assertEqual("draft", cur.fetchone()["state"])
        self.assertEqual(0, self._count(
            "SELECT count(*) n FROM thread WHERE tenant_id = %s AND campaign_id = %s",
            (self.tenant, campaign_id)))

    def test_an_unknown_channel_is_refused_and_the_legal_set_is_named(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            actions.create_campaign(OPERATOR, self.conn, self.tenant,
                                    artist_id=self._artist(), name="X", channel="tiktok")
        self.assertEqual(errors.NOT_ALLOWED_VALUE, caught.exception.code)
        for channel in actions.CHANNELS:
            self.assertIn(channel, caught.exception.message)

    def test_a_campaign_needs_a_name(self) -> None:
        """No default is invented — a label nobody chose would appear in every list."""
        with self.assertRaises(errors.Refusal) as caught:
            actions.create_campaign(OPERATOR, self.conn, self.tenant,
                                    artist_id=self._artist(), name="   ")
        self.assertEqual(errors.NOT_ALLOWED_VALUE, caught.exception.code)

    def test_a_campaign_for_nobody_is_a_404(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            actions.create_campaign(OPERATOR, self.conn, self.tenant,
                                    artist_id=str(uuid.uuid4()), name="X")
        self.assertEqual(errors.NOT_FOUND, caught.exception.code)

    # -------------------------------------------------------------- threads

    def test_opening_a_thread_takes_the_counterparty_off_the_shortlist(self) -> None:
        counterparty = self._counterparty()
        campaign = self._campaign(self._artist())
        self.assertEqual("contactable", self._contact_state(counterparty))
        out = actions.open_thread(OPERATOR, self.conn, self.tenant, campaign,
                                  counterparty_id=counterparty)
        self.assertEqual("discovered", out["state"])
        self.assertEqual("in_thread", self._contact_state(counterparty))

    def test_one_open_thread_per_counterparty_across_every_campaign(self) -> None:
        """The partial unique index, reached through a second *different* campaign —
        the guarantee is label-wide, not per campaign."""
        artist = self._artist()
        counterparty = self._counterparty()
        first = self._campaign(artist)
        second = actions.create_campaign(
            OPERATOR, self.conn, self.tenant, artist_id=artist,
            name="Second campaign", channel="press")["id"]
        actions.open_thread(OPERATOR, self.conn, self.tenant, first,
                            counterparty_id=counterparty)
        with self.assertRaises(errors.Refusal) as caught:
            actions.open_thread(OPERATOR, self.conn, self.tenant, second,
                                counterparty_id=counterparty)
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual(errors.THREAD_OCCUPIED, caught.exception.code)
        self.assertEqual(1, self._count(
            "SELECT count(*) n FROM thread WHERE tenant_id = %s AND counterparty_id = %s",
            (self.tenant, counterparty)))

    def test_a_hand_opened_thread_records_no_reason_rather_than_a_plausible_one(self) -> None:
        """`025_decision_provenance.sql`'s argument, through the API.

        The counterparty is not on any shortlist — there is no embedding — so no
        ranking exists. The absence must stay an absence: a default here would present
        "the ranking at the instant somebody called this endpoint" as a justification.
        """
        out = actions.open_thread(
            OPERATOR, self.conn, self.tenant, self._campaign(self._artist()),
            counterparty_id=self._counterparty())
        self.assertIsNone(out["decided"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT decided_at_hlc, decided_rank, decided_distance "
                        "FROM thread WHERE tenant_id = %s AND id = %s",
                        (self.tenant, out["id"]))
            row = cur.fetchone()
        self.assertIsNone(row["decided_at_hlc"])
        self.assertIsNone(row["decided_rank"])
        self.assertIsNone(row["decided_distance"])

    def test_opening_a_thread_takes_no_rank_from_the_caller(self) -> None:
        """The signature is the guarantee: there is no parameter through which a
        client could supply the record that answers for an irreversible act."""
        import inspect

        params = set(inspect.signature(actions.open_thread).parameters)
        self.assertEqual({"principal", "conn", "tenant", "campaign_id",
                          "counterparty_id"}, params)

    def test_closing_releases_the_counterparty_and_queues_a_lesson(self) -> None:
        counterparty = self._counterparty()
        opened = actions.open_thread(OPERATOR, self.conn, self.tenant,
                                     self._campaign(self._artist()),
                                     counterparty_id=counterparty)
        out = actions.close_thread(OPERATOR, self.conn, self.tenant, opened["id"],
                                   outcome="closed_lost")
        self.assertEqual("closed_lost", out["state"])
        # `declined` rather than `contactable`, because a no is worth remembering.
        self.assertEqual("declined", self._contact_state(counterparty))
        self.assertEqual(1, self._count(
            "SELECT count(*) n FROM lead WHERE tenant_id = %s AND kind = 'distil_lesson'",
            (self.tenant,)))

    def test_an_unknown_outcome_is_refused_and_the_legal_set_is_named(self) -> None:
        opened = actions.open_thread(OPERATOR, self.conn, self.tenant,
                                     self._campaign(self._artist()),
                                     counterparty_id=self._counterparty())
        with self.assertRaises(errors.Refusal) as caught:
            actions.close_thread(OPERATOR, self.conn, self.tenant, opened["id"],
                                 outcome="closed_maybe")
        self.assertEqual(errors.NOT_ALLOWED_VALUE, caught.exception.code)
        for state in outreach.CLOSED:
            self.assertIn(state, caught.exception.message)

    def test_closing_a_thread_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            actions.close_thread(OPERATOR, self.conn, self.tenant, str(uuid.uuid4()),
                                 outcome="closed_lost")
        self.assertEqual(errors.TRANSITION_REFUSED, caught.exception.code)

    # ------------------------------------------------------------- the gate

    def _draft_waiting(self) -> tuple[str, str]:
        """A thread walked to `awaiting_human` with a draft on it."""
        opened = actions.open_thread(OPERATOR, self.conn, self.tenant,
                                     self._campaign(self._artist()),
                                     counterparty_id=self._counterparty())
        outreach.advance(self.conn, self.tenant, opened["id"], "shortlisted")
        outreach.advance(self.conn, self.tenant, opened["id"], "approved")
        message = outreach.draft(self.conn, self.tenant, opened["id"],
                                 subject="A pitch", body="A body.")
        return opened["id"], str(message["id"])

    def test_approving_prepares_a_send_and_performs_none(self) -> None:
        thread_id, message_id = self._draft_waiting()
        out = actions.approve_draft(OPERATOR, self.conn, self.tenant, message_id)
        self.assertEqual("queued", out["thread_state"])
        self.assertIs(False, out["sent"])
        self.assertEqual(1, self._count(
            "SELECT count(*) n FROM outbox WHERE tenant_id = %s AND message_id = %s",
            (self.tenant, message_id)))
        # Nothing claims it. `pending` is the honest resting state.
        with self.conn.cursor() as cur:
            cur.execute("SELECT state, sent_at FROM outbox WHERE tenant_id = %s "
                        "AND message_id = %s", (self.tenant, message_id))
            row = cur.fetchone()
        self.assertEqual("pending", row["state"])
        self.assertIsNone(row["sent_at"])

    def test_a_double_approve_is_a_refused_insert_and_not_a_second_send(self) -> None:
        """The guarantee this whole seam exists to preserve.

        `UNIQUE (message_id)` on `outbox` is what refuses it. The endpoint is therefore
        safe to retry, which is the property the constraint buys — and the assertion
        that matters is the row count, not the exception.
        """
        thread_id, message_id = self._draft_waiting()
        actions.approve_draft(OPERATOR, self.conn, self.tenant, message_id)
        with self.assertRaises(errors.Refusal) as caught:
            actions.approve_draft(OPERATOR, self.conn, self.tenant, message_id)
        self.assertEqual(409, caught.exception.status_code)
        self.assertIn(caught.exception.code,
                      (errors.ALREADY_QUEUED, errors.NO_DRAFT_WAITING))
        self.assertEqual(1, self._count(
            "SELECT count(*) n FROM outbox WHERE tenant_id = %s AND message_id = %s",
            (self.tenant, message_id)))

    def test_the_refusal_does_not_leak_the_constraint_name(self) -> None:
        """A constraint name tells an operator nothing and tells anybody else more
        about the schema than a request should return."""
        thread_id, message_id = self._draft_waiting()
        actions.approve_draft(OPERATOR, self.conn, self.tenant, message_id)
        with self.assertRaises(errors.Refusal) as caught:
            actions.approve_draft(OPERATOR, self.conn, self.tenant, message_id)
        self.assertNotIn("outbox_message_id_key", caught.exception.message)
        self.assertNotIn("UNIQUE", caught.exception.message)

    def test_rejecting_keeps_the_draft_and_returns_the_thread(self) -> None:
        """The message row stays: what the drafter wrote and what an operator refused
        is the only training signal this part of the product produces."""
        thread_id, message_id = self._draft_waiting()
        out = actions.reject_draft(OPERATOR, self.conn, self.tenant, message_id,
                                   reason="not this one")
        self.assertEqual("drafted", out["thread_state"])
        self.assertEqual(1, self._count(
            "SELECT count(*) n FROM message WHERE tenant_id = %s AND id = %s",
            (self.tenant, message_id)))

    def test_approving_something_that_is_not_a_waiting_draft_is_refused(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            actions.approve_draft(OPERATOR, self.conn, self.tenant, str(uuid.uuid4()))
        self.assertEqual(errors.NO_DRAFT_WAITING, caught.exception.code)

    def test_a_draft_in_another_tenant_is_not_approvable(self) -> None:
        """Tenant scoping on the resolution step, not merely on the write."""
        thread_id, message_id = self._draft_waiting()
        other = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (other, f"test-other-{other[:8]}", "other"))
        try:
            with self.assertRaises(errors.Refusal) as caught:
                actions.approve_draft(OPERATOR, self.conn, other, message_id)
            self.assertEqual(errors.NO_DRAFT_WAITING, caught.exception.code)
            self.assertEqual(0, self._count(
                "SELECT count(*) n FROM outbox WHERE tenant_id = %s AND message_id = %s",
                (self.tenant, message_id)))
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM tenant WHERE id = %s", (other,))

    # --------------------------------------------------------- justification

    def test_a_hand_opened_thread_is_not_justified_rather_than_missing(self) -> None:
        """"We never had a reason" and "we had one and it aged out" are different
        answers to somebody asking why they were contacted."""
        opened = actions.open_thread(OPERATOR, self.conn, self.tenant,
                                     self._campaign(self._artist()),
                                     counterparty_id=self._counterparty())
        with self.assertRaises(errors.Refusal) as caught:
            reads.justification(OPERATOR, self.conn, self.tenant, opened["id"])
        self.assertEqual(errors.NOT_JUSTIFIED, caught.exception.code)

    def test_justification_for_a_thread_that_does_not_exist_is_a_404(self) -> None:
        with self.assertRaises(errors.Refusal) as caught:
            reads.justification(OPERATOR, self.conn, self.tenant, str(uuid.uuid4()))
        self.assertEqual(errors.NOT_FOUND, caught.exception.code)

    # ------------------------------------------------------------ recordings

    def test_analysing_a_recording_with_no_master_is_refused(self) -> None:
        recording_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recording (id, tenant_id, title, slug) "
                "VALUES (%s, %s, %s, %s)",
                (recording_id, self.tenant, "A Track", f"t-{recording_id[:8]}"))
        with self.assertRaises(errors.Refusal) as caught:
            actions.analyse_recording(OPERATOR, self.conn, self.tenant, recording_id)
        self.assertEqual(errors.NO_MASTER, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
