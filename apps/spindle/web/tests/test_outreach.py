"""Campaigns, threads, the state machine and the send gate.

    python -m unittest discover apps/spindle/web/tests

Same two layers as `test_fleet`, for the same reason.

  * **Offline** — the transition table itself. It is pure data and the properties worth
    holding are structural: every state reachable, nothing stranded, no path to `queued`
    that skips the human.

  * **Against the cluster** — everything else, because everything else *is* a database
    behaviour. The partial unique index, the transactional outbox and `FOR UPDATE` are
    guarantees CockroachDB makes; a fake that returns rows from a dict would prove only
    that the fake works. These skip when `DATABASE_URL` is unset and run inside a
    dedicated tenant that is deleted in `tearDown`.

The property that matters most here is the one `PLATFORM-SPEC §3b` is written to
protect: **an approved draft produces exactly one outbox row, or none.** Sending is the
only irreversible act in the product, and a second row is a relationship burned twice.
"""

from __future__ import annotations

import os
import unittest
import uuid

from spindle import outreach

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class StateMachine(unittest.TestCase):
    """The transition table as data. No database, no fixtures — these are properties of
    the shape, and a shape that is wrong here is wrong everywhere it is used."""

    def test_every_state_the_check_constraint_allows_is_in_the_table(self):
        # Migration 010's `thread_state_known` and `ALLOWED` are two statements of the
        # same set. If they drift, a thread can legally hold a state the machine has no
        # exits for, and it is stuck with nothing to say why.
        declared = {
            "discovered", "shortlisted", "approved", "drafted", "awaiting_human",
            "queued", "sent", "awaiting_reply", "replied", "negotiating", "agreed",
            "delivered", "verified", "closed_won", "closed_lost", "closed_no_reply",
        }
        self.assertEqual(set(outreach.ALLOWED), declared)

    def test_every_target_is_itself_a_known_state(self):
        for was, targets in outreach.ALLOWED.items():
            for to in targets:
                self.assertIn(to, outreach.ALLOWED,
                              f"{was} -> {to} names a state the machine does not define")

    def test_closed_states_are_terminal(self):
        for state in outreach.CLOSED:
            self.assertEqual(outreach.ALLOWED[state], frozenset(),
                             f"{state} is closed and must not lead anywhere")

    def test_every_open_state_can_be_abandoned(self):
        """An operator walking away is always legal. A machine that made you drive a
        dead conversation to `verified` to be rid of it would be driven around."""
        for state, targets in outreach.ALLOWED.items():
            if state in outreach.CLOSED:
                continue
            self.assertTrue(targets & outreach.CLOSED,
                            f"{state} cannot be closed, so it is a trap")

    def test_queued_is_reachable_only_through_the_human_gate(self):
        """The property the approvals screen exists to enforce. If any other state can
        reach `queued`, a draft can be prepared for sending without anybody reading it,
        and the gate is decoration."""
        sources = [s for s, targets in outreach.ALLOWED.items() if "queued" in targets]
        self.assertEqual(sources, [outreach.NEEDS_HUMAN])

    def test_no_state_is_stranded(self):
        """Every state except the entry point is reachable from somewhere. An
        unreachable state is dead code that a CHECK constraint still permits."""
        reachable = {to for targets in outreach.ALLOWED.values() for to in targets}
        for state in outreach.ALLOWED:
            if state == "discovered":
                continue
            self.assertIn(state, reachable, f"nothing leads to {state}")

    def test_the_happy_path_walks_end_to_end(self):
        path = ["discovered", "shortlisted", "approved", "drafted", "awaiting_human",
                "queued", "sent", "awaiting_reply", "replied", "negotiating", "agreed",
                "delivered", "verified", "closed_won"]
        for was, to in zip(path, path[1:]):
            self.assertIn(to, outreach.ALLOWED[was], f"{was} -> {to} is not allowed")


class IdempotencyKey(unittest.TestCase):

    def test_the_same_draft_produces_the_same_key(self):
        """A retry must collide with the send it is retrying, or the provider has no way
        to deduplicate and the operator sends twice."""
        a = outreach.idempotency_key("t-1", 0, "hello")
        b = outreach.idempotency_key("t-1", 0, "hello")
        self.assertEqual(a, b)

    def test_a_redraft_produces_a_different_key(self):
        """Rewriting the pitch is a genuinely different send. Colliding with the draft it
        replaced would make the provider silently drop the new one."""
        first = outreach.idempotency_key("t-1", 0, "hello")
        second = outreach.idempotency_key("t-1", 1, "hello, again")
        self.assertNotEqual(first, second)

    def test_different_threads_do_not_collide(self):
        self.assertNotEqual(outreach.idempotency_key("t-1", 0, "x"),
                            outreach.idempotency_key("t-2", 0, "x"))


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL — these test the database, not the code")
class AgainstTheCluster(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-outreach-{self.tenant[:8]}", "outreach test"))
        self.artist = self._party("Test Artist", "roster")
        self.curator = self._party("Test Curator", "counterparty")

    def tearDown(self) -> None:
        # Everything below cascades from the tenant, which is why the fixture is a
        # tenant rather than a pile of rows somebody has to remember to delete.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _party(self, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, self.tenant, f"{name.lower().replace(' ', '-')}-{party_id[:8]}",
                 name, party_class))
        return party_id

    def _campaign(self, name: str = "test campaign") -> str:
        return str(outreach.create_campaign(
            self.conn, self.tenant, party_id=self.artist, name=name)["id"])

    def _thread_state(self, thread_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM thread WHERE id = %s", (thread_id,))
            return cur.fetchone()["state"]

    def _contact_state(self, party_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT contact_state FROM party WHERE id = %s", (party_id,))
            return cur.fetchone()["contact_state"]

    def _count(self, table: str, where: str, params: tuple) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {where}", params)
            return cur.fetchone()["n"]

    # ------------------------------------------------------------ §3c, the collision

    def test_one_open_thread_per_counterparty_across_campaigns(self):
        """`PLATFORM-SPEC §3c`. A creator who is also a curator cannot be worked by two
        fleets at once, and the second fleet does not need to know the first exists."""
        import psycopg

        a, b = self._campaign("curator push"), self._campaign("ugc push")
        outreach.open_thread(self.conn, self.tenant, campaign_id=a,
                             counterparty_id=self.curator)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            outreach.open_thread(self.conn, self.tenant, campaign_id=b,
                                 counterparty_id=self.curator)

    def test_closing_a_thread_releases_the_counterparty(self):
        """The partial predicate is load-bearing in both directions. Without the release
        half, one abandoned conversation would lock somebody out forever."""
        a, b = self._campaign("first"), self._campaign("second")
        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=a,
                                          counterparty_id=self.curator)["id"])
        outreach.advance(self.conn, self.tenant, thread, "closed_lost")

        # Now the second campaign may have them.
        outreach.open_thread(self.conn, self.tenant, campaign_id=b,
                             counterparty_id=self.curator)

    def test_opening_and_closing_keep_contact_state_in_step_with_the_index(self):
        """`contact_state` is the shortlist's accelerated view of the index. A cache that
        disagrees with the thing it caches would put somebody mid-conversation back on
        the shortlist, which is worse than no cache at all."""
        campaign = self._campaign()
        self.assertEqual(self._contact_state(self.curator), "contactable")

        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                          counterparty_id=self.curator)["id"])
        self.assertEqual(self._contact_state(self.curator), "in_thread")

        outreach.advance(self.conn, self.tenant, thread, "closed_lost")
        # A no is worth remembering, so it is `declined` rather than `contactable`.
        self.assertEqual(self._contact_state(self.curator), "declined")

    # --------------------------------------------------------------- the state machine

    def test_a_skipped_step_is_refused(self):
        campaign = self._campaign()
        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                          counterparty_id=self.curator)["id"])
        with self.assertRaises(outreach.TransitionRefused):
            outreach.advance(self.conn, self.tenant, thread, "queued")
        self.assertEqual(self._thread_state(thread), "discovered",
                         "a refused transition must not move the row")

    def test_the_refusal_names_both_ends(self):
        """The message is the product here — "invalid transition" makes somebody open
        the source, and this screen is read under time pressure."""
        campaign = self._campaign()
        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                          counterparty_id=self.curator)["id"])
        with self.assertRaises(outreach.TransitionRefused) as caught:
            outreach.advance(self.conn, self.tenant, thread, "sent")
        message = str(caught.exception)
        self.assertIn("discovered", message)
        self.assertIn("sent", message)
        self.assertIn("shortlisted", message, "it should say what IS allowed")

    # ------------------------------------------------------------------- the send gate

    def _walk_to_the_gate(self) -> tuple[str, str]:
        campaign = self._campaign()
        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                          counterparty_id=self.curator)["id"])
        outreach.advance(self.conn, self.tenant, thread, "shortlisted")
        outreach.advance(self.conn, self.tenant, thread, "approved")
        message = str(outreach.draft(self.conn, self.tenant, thread,
                                     subject="a subject", body="a body")["id"])
        return thread, message

    def test_a_draft_stops_at_the_human(self):
        thread, _ = self._walk_to_the_gate()
        self.assertEqual(self._thread_state(thread), "awaiting_human")

    def test_a_draft_writes_no_outbox_row(self):
        """Writing the pitch is not preparing the send. If drafting queued anything, the
        gate would be after the irreversible step rather than before it."""
        thread, _ = self._walk_to_the_gate()
        self.assertEqual(self._count("outbox", "thread_id = %s", (thread,)), 0)

    def test_approving_writes_exactly_one_outbox_row(self):
        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        self.assertEqual(self._count("outbox", "message_id = %s", (message,)), 1)
        self.assertEqual(self._thread_state(thread), "queued")

    def test_approving_twice_does_not_queue_a_second_copy(self):
        """The property §3b exists for. A double-click must be refused by the database,
        not merely discouraged by the interface."""
        import psycopg

        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        self.assertEqual(self._count("outbox", "message_id = %s", (message,)), 1)

    def test_approving_sends_nothing(self):
        """Nothing claims the outbox and no provider is wired. The row is a send that is
        fully prepared and has not happened, and that must be visible as such."""
        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        self.assertEqual(
            self._count("outbox", "message_id = %s AND state = 'pending' "
                                  "AND sent_at IS NULL", (message,)), 1)
        self.assertEqual(
            self._count("message", "id = %s AND sent_at IS NULL", (message,)), 1)

    def test_approving_records_who(self):
        """A send with no approver is what the gate exists to make impossible, and the
        record is how you show it did not happen."""
        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="anthony")
        self.assertEqual(
            self._count("message", "id = %s AND approved_by = 'anthony' "
                                   "AND approved_at IS NOT NULL", (message,)), 1)

    def test_rejecting_keeps_the_draft_and_reopens_it(self):
        """What the drafter wrote and what an operator refused is the only training
        signal this part of the product produces. Deleting it to tidy the screen would
        throw it away."""
        thread, message = self._walk_to_the_gate()
        outreach.reject(self.conn, self.tenant, thread, message, reason="too long")
        self.assertEqual(self._thread_state(thread), "drafted")
        self.assertEqual(self._count("message", "id = %s", (message,)), 1)

    def test_a_redraft_is_a_second_row_with_its_own_key(self):
        thread, first = self._walk_to_the_gate()
        outreach.reject(self.conn, self.tenant, thread, first)
        second = str(outreach.draft(self.conn, self.tenant, thread,
                                    subject="shorter", body="much shorter")["id"])
        self.assertNotEqual(first, second)
        self.assertEqual(
            self._count("message", "thread_id = %s AND direction = 'outbound'", (thread,)), 2)

    # ------------------------------------------------------------------------ the inbox

    def test_a_reply_lands_and_moves_the_thread(self):
        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        outreach.advance(self.conn, self.tenant, thread, "sent")
        outreach.advance(self.conn, self.tenant, thread, "awaiting_reply")

        outreach.record_reply(self.conn, self.tenant, thread, subject="re: a subject",
                              body="sure, send it", intent="interested", confidence=0.9)
        self.assertEqual(self._thread_state(thread), "replied")

    def test_a_decline_closes_the_thread_and_releases_them(self):
        thread, message = self._walk_to_the_gate()
        outreach.approve(self.conn, self.tenant, thread, message, approver="tester")
        outreach.advance(self.conn, self.tenant, thread, "sent")
        outreach.advance(self.conn, self.tenant, thread, "awaiting_reply")

        outreach.record_reply(self.conn, self.tenant, thread, subject="re:",
                              body="not for us", intent="declined", confidence=0.8)
        self.assertEqual(self._thread_state(thread), "closed_lost")
        self.assertEqual(self._contact_state(self.curator), "declined")

    def test_a_reply_survives_a_transition_that_cannot_apply(self):
        """`record_reply` tries to close on a decline and swallows the refusal. The
        savepoint is what makes that safe — without it the failed transition would poison
        the transaction and lose the reply that was just recorded."""
        campaign = self._campaign()
        thread = str(outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                          counterparty_id=self.curator)["id"])
        # Close it first, so the decline below arrives at a terminal state and the
        # attempted `closed_lost` transition is genuinely refused. `closed_lost` is the
        # only closing state `discovered` can reach — a thread nobody has written to
        # cannot have failed to get a reply.
        outreach.advance(self.conn, self.tenant, thread, "closed_lost")
        outreach.record_reply(self.conn, self.tenant, thread, subject="late",
                              body="sorry, no", intent="declined")
        self.assertEqual(
            self._count("message", "thread_id = %s AND direction = 'inbound'", (thread,)), 1,
            "the reply must be kept even though the thread could not move")

    # ------------------------------------------------------------------------- counting

    def test_counts_report_what_the_badges_show(self):
        thread, _ = self._walk_to_the_gate()
        counts = outreach.counts(self.conn, self.tenant)
        self.assertEqual(counts["awaiting_human"], 1)
        self.assertEqual(counts["open_threads"], 1)
        self.assertEqual(counts["queued"], 0)


if __name__ == "__main__":
    unittest.main()
