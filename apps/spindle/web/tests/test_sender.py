"""The Sender's at-most-once guarantee, proved rather than asserted in a docstring.

    cd apps/spindle/web && .venv/bin/python -m pytest tests/test_sender.py -q

Every other worker in this system is at-*least*-once. `fleet.claim` has a lease-expiry
clause, `fleet.fail` backs a lead off and hands it back, and a crashed worker's work is
picked up by the next one with nobody noticing. That is correct for a lead, because
re-reading a MusicBrainz page twice costs a round trip and produces the same row.

`sender.py` is the one module where that policy is wrong, and it is wrong in a direction
that cannot be undone. There is no API call that unsends an email. So the Sender inverts
the default: the outbox row is moved to `claimed` in its own committed transaction
*before* the network call, and a row left in `claimed` is never picked up again by any
code path. A worker that dies between the claim and the send loses that send, forever,
until a human looks at it.

That is the trade this file exists to hold in place:

    a lost pitch costs nothing.
    a duplicate pitch costs the label a curator.

The class of bug these tests prevent is a well-meaning tidy-up. `sender.claim` and
`fleet.claim` are near-identical SQL, and the difference between them is one absent
predicate. Somebody reading the two side by side, noticing that outbox rows can get
"stuck", and adding `OR claimed_at < now() - INTERVAL '5 minutes'` to make the Sender
"self-healing like the fleet" would convert at-most-once into at-least-once in a
one-line diff that looks like a bug fix and reviews like one. `test_a_stranded_claimed_
row_is_never_handed_out_again` is the test that fails when they do, and
`test_only_the_fleets_claim_has_a_lease_expiry_clause` is the one that fails even if the
stranded row is reached some other way.

## Two layers, the same split as `test_fleet` and `test_outreach`

  * **Offline** — the shape of the two claim queries, read out of the source. No
    database, never skipped, and it is the guard that survives a rewrite of the fixture.

  * **Against the cluster** — everything else, because everything else *is* a database
    behaviour. `UNIQUE (message_id)`, `FOR UPDATE SKIP LOCKED` and the one-transaction
    record are guarantees CockroachDB makes; a fake that returns rows from a dict would
    prove only that the fake works.

## Two safety rules this file follows without exception

**Nothing here can send a real email.** Every call to `sender.send_once` passes an
explicit stub `mailer=`, so `mail.load()` is never reached on any path that could
construct a boto3 SES client. `PLATFORM_MAIL_SENDER` and friends are deliberately *never*
set by any test in this file, not even to a fake value: if a future edit dropped the
`mailer=` argument, `mail.load()` would raise `MailNotConfigured` rather than build a real
client, so the failure mode of a mistake here is a red test and not a message to a
stranger. `MailerThatMustNotBeCalled` is used wherever the point of the test is that
nothing goes out — an assertion after the fact would still have sent it.

**Nothing here touches a pre-existing row.** The fixture is a tenant, created in `setUp`
and deleted in `tearDown`; `party`, `campaign`, `thread`, `message`, `outbox`,
`contact_route` and `agent_run` all cascade from it. No test in this file issues an
UPDATE or DELETE whose predicate is not scoped to a row it created itself, and the tenant
is resolved by the id this file minted rather than by a slug lookup — a concurrent test
run's transient tenant is invisible to it by construction.
"""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

from spindle import fleet, mail, sender

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


# --------------------------------------------------------------------- the environment

def _env(**overrides: str):
    """Replace the environment wholesale, as `test_spend` does and for its reason.

    `clear=True` matters twice over here. A developer with `RTF_PAID_ENABLED=1` in their
    shell would otherwise get a green run for a kill switch that is wide open — and,
    worse for this module, a developer with a configured `PLATFORM_MAIL_SENDER` would
    have `mail.load()` succeed in a file whose entire safety argument is that it cannot.
    """
    return mock.patch.dict(os.environ, overrides, clear=True)


def _paid_calls_allowed():
    """A ceiling that comfortably covers one $0.0001 send, and nothing else configured.

    Note what is absent: `PLATFORM_MAIL_SENDER`. The postal address and reply-to *are*
    set, because `send_once` refuses to claim without them — they are what makes the body
    lawful, and every send path here exercises `compliant_body`. The sender identity is
    still withheld, which is what keeps the module header's promise intact: a dropped
    `mailer=` argument raises `MailNotConfigured` from `mail.load()` rather than quietly
    constructing a real SES client.
    """
    return _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="1.00",
                RTF_PER_CALL_CEILING_USD="0.05",
                PLATFORM_MAIL_REPLY_TO="tests@respect-the-funk.invalid",
                PLATFORM_MAIL_POSTAL_ADDRESS="1 Test Way, Providence RI 02903")


def _paid_calls_off():
    """The default posture, and the kill switch.

    Paid calls off, but the message settings present — otherwise `send_once` would refuse
    on compliance before ever reaching the gate, and the test would pass for the wrong
    reason.
    """
    return _env(PLATFORM_MAIL_REPLY_TO="tests@respect-the-funk.invalid",
                PLATFORM_MAIL_POSTAL_ADDRESS="1 Test Way, Providence RI 02903")


# -------------------------------------------------------------------------- the mailers

class StubMailer:
    """Records what it was asked to send and returns a provider id. No network, ever.

    Deliberately not a `unittest.mock.Mock`: a Mock satisfies any call signature, so a
    change to the `Mailer` Protocol would pass silently here and fail in production. This
    implements the Protocol's keyword-only signature exactly, and a drift in either
    direction is a `TypeError` in the test.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self._raises = raises

    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str, reply_to: str = "") -> mail.Sent:
        # `reply_to` arrived with `040_mailbox.sql`: the platform identity sends every
        # tenant's mail from one address, so the thread is encoded in a plus-addressed
        # Reply-To per message. This stub gaining the parameter is the mechanism its own
        # docstring describes working — the protocol changed and this failed loudly.
        self.calls.append({"to": to, "subject": subject, "body": body,
                           "idempotency_key": idempotency_key, "reply_to": reply_to})
        if self._raises is not None:
            raise self._raises
        return mail.Sent(provider_message_id="stub-provider-id", cost_usd="0.0001")


class MailerThatMustNotBeCalled:
    """Fails the test from inside the send, not from an assertion afterwards.

    Where the property is "this must not go out", checking a call log after the fact
    proves it a moment too late to be a guarantee about anything. Neither `send_once` nor
    `_record_failure` catches `AssertionError`, so this propagates all the way out and the
    test errors loudly.
    """

    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str, reply_to: str = "") -> mail.Sent:
        raise AssertionError(
            f"the Sender called the provider for {to!r} — this path must never send")


# ------------------------------------------------------------------ the shape of a claim

class ClaimPredicates(unittest.TestCase):
    """The one-line diff that would silently convert at-most-once into at-least-once.

    Read out of the source rather than exercised, because the point is not what the query
    returns today for a particular fixture — the cluster test below covers that — but that
    the *predicate* stays absent. A lease-expiry clause added to `sender.claim` would make
    a stranded row reappear only after the lease elapsed, so a cluster test with a
    fixture that is claimed-and-immediately-checked could keep passing while the guarantee
    was gone. This one cannot.
    """

    @staticmethod
    def _claimable_predicate(func) -> str:
        """The subquery's WHERE clause, with comments stripped.

        Comments have to go: `sender.claim`'s docstring and inline comments discuss the
        expiry clause at length precisely because it is absent, and a naive substring
        search over the raw source would find the prose and not the code. `test_fleet`
        learned the same lesson in `AgentRunNamesTheWork`.
        """
        source = inspect.getsource(func)
        body = source.split("SELECT id FROM", 1)[1].split("FOR UPDATE", 1)[0]
        return "\n".join(line for line in body.splitlines()
                         if not line.strip().startswith("#"))

    def test_the_fleets_claim_does_have_a_lease_expiry_clause(self):
        """The control. If this stops being true the contrast below means nothing, and
        the fleet has quietly lost the property that makes leads survive a dead worker."""
        predicate = self._claimable_predicate(fleet.claim)
        self.assertIn("lease_expires_at", predicate,
                      "fleet.claim no longer reclaims work from a dead worker")

    def test_only_the_fleets_claim_has_a_lease_expiry_clause(self):
        """The Sender's claim must select on `state = 'pending'` and due-ness and nothing
        else. Any mention of the claim's own bookkeeping columns in the selection
        predicate means somebody has taught it to pick a `claimed` row back up."""
        predicate = self._claimable_predicate(sender.claim)
        self.assertIn("state = 'pending'", predicate)
        for forbidden in ("claimed_at", "claimed_by", "lease", "expire"):
            self.assertNotIn(
                forbidden, predicate,
                f"sender.claim's predicate mentions {forbidden!r} — a claimed row that "
                f"can be re-selected is a double send, which is the one failure this "
                f"module exists to make impossible")

    def test_the_batch_is_small_and_the_attempt_ceiling_is_below_the_fleets(self):
        """Every claimed row is an irreversible act in flight, and the batch size is the
        width of the window a crash strands. Both numbers being lower than the fleet's is
        the policy difference made numeric; a merge that harmonised them would widen the
        blast radius without anybody deciding to."""
        self.assertLessEqual(sender.BATCH, fleet.BATCH)
        self.assertLess(sender.MAX_ATTEMPTS, fleet.MAX_ATTEMPTS)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class AgainstTheCluster(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test.

    The fixture is one campaign, one counterparty with one usable email route, one thread
    parked in `queued` — the state `/approvals` leaves a thread in — and whatever
    messages and outbox rows the test needs. Row counts are kept deliberately small: this
    is a scale-to-zero cluster with real business data on it, and a test that inserted a
    hundred rows to prove a property that two rows prove is a test that costs the project
    money to run.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-sender-{self.tenant[:8]}", "sender test"))
        self.artist = self._party("Test Sender Artist", "roster")
        self.campaign = self._campaign()
        self.curator, self.thread = self._counterparty_with_thread("Test Sender Curator")
        self._route(self.curator, "curator@example.invalid")

    def tearDown(self) -> None:
        # Party, campaign, thread, message, outbox, contact_route and agent_run all
        # cascade from the tenant, which is why the fixture is a tenant rather than a
        # pile of rows somebody has to remember to delete — and why this file can never
        # leave a stray row behind on a cluster that holds real data.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    # ------------------------------------------------------------------------- fixtures

    def _party(self, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, self.tenant, f"test-sender-{party_id[:8]}", name, party_class))
        return party_id

    def _campaign(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO campaign (tenant_id, party_id, name) "
                "VALUES (%s, %s, 'test sender campaign') RETURNING id",
                (self.tenant, self.artist))
            return str(cur.fetchone()["id"])

    def _counterparty_with_thread(self, name: str) -> tuple[str, str]:
        """A counterparty and a thread sitting in `queued`, which is where `/approvals`
        leaves one. Each counterparty gets its own, because `one_open_thread_per_
        counterparty` is a real index and two open threads on one party is a constraint
        violation rather than a fixture."""
        party_id = self._party(name, "counterparty")
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO thread (tenant_id, campaign_id, counterparty_id, state) "
                "VALUES (%s, %s, %s, 'queued') RETURNING id",
                (self.tenant, self.campaign, party_id))
            return party_id, str(cur.fetchone()["id"])

    def _route(self, party_id: str, value: str, *, state: str = "verified",
               provenance: str = "asserted") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO contact_route (tenant_id, party_id, channel, value,
                                              provenance, state)
                   VALUES (%s, %s, 'email', %s, %s, %s)""",
                (self.tenant, party_id, value, provenance, state))

    def _queued(self, thread_id: str | None = None) -> tuple[str, str]:
        """An approved message and the single outbox row that delivers it.

        Written directly rather than by walking `outreach.advance` thirteen times: the
        property under test is the Sender's, and every extra round trip on this cluster
        is a real cost. `approved_by` is populated because `_record_sent` reads it into
        the `agent_run` summary, and a send with no approver is the thing the gate exists
        to make impossible.
        """
        thread_id = thread_id or self.thread
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO message (tenant_id, thread_id, direction, subject, body,
                                        idempotency_key, approved_by, approved_at)
                   VALUES (%s, %s, 'outbound', 'a subject', 'a body', %s,
                           'test-sender', now())
                RETURNING id""",
                (self.tenant, thread_id, str(uuid.uuid4())))
            message_id = str(cur.fetchone()["id"])
            cur.execute(
                "INSERT INTO outbox (tenant_id, thread_id, message_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                (self.tenant, thread_id, message_id))
            return message_id, str(cur.fetchone()["id"])

    # -------------------------------------------------------------------------- readers

    def _outbox(self, outbox_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM outbox WHERE tenant_id = %s AND id = %s",
                        (self.tenant, outbox_id))
            return cur.fetchone()

    def _message(self, message_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM message WHERE tenant_id = %s AND id = %s",
                        (self.tenant, message_id))
            return cur.fetchone()

    def _thread_state(self, thread_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM thread WHERE tenant_id = %s AND id = %s",
                        (self.tenant, thread_id))
            return cur.fetchone()["state"]

    # --------------------------------------------------- 1. one outbox row per message

    def test_a_second_outbox_row_for_one_message_is_structurally_impossible(self):
        """`UNIQUE (message_id)`. A second outbox row for one message *is* the double
        send, expressed in the schema rather than left to whoever writes the next caller.

        `test_outreach` already proves `approve()` cannot write two. This proves the
        stronger thing: nothing can, including a hand-written INSERT, a replayed
        migration, or a future retry helper that decides the tidy way to re-send is to
        queue it again. The constraint is what makes the discipline in `sender.py`
        unnecessary to trust.
        """
        import psycopg

        message_id, _ = self._queued()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO outbox (tenant_id, thread_id, message_id) "
                    "VALUES (%s, %s, %s)",
                    (self.tenant, self.thread, message_id))

        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM outbox WHERE message_id = %s",
                        (message_id,))
            self.assertEqual(cur.fetchone()["n"], 1)

    # ------------------------------------------------------ 2. never two workers, one row

    def test_two_senders_never_claim_the_same_outbox_row(self):
        """`FOR UPDATE SKIP LOCKED`, on the table where losing this race means one
        curator receives the same pitch twice.

        Two connections rather than one, because that is the deployment: a Lambda on a
        schedule and somebody draining from a laptop, or simply two invocations that
        overlap. Three rows and a batch of two so the split is uneven — an assertion that
        merely counted would pass on an implementation that handed both workers
        everything.
        """
        import psycopg
        from psycopg.rows import dict_row

        queued = [self._queued()[1] for _ in range(3)]

        other = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                row_factory=dict_row)
        try:
            first = sender.claim(self.conn, self.tenant, "sender-a", batch=2)
            second = sender.claim(other, self.tenant, "sender-b", batch=2)
        finally:
            other.close()

        ids_a = {str(r["id"]) for r in first}
        ids_b = {str(r["id"]) for r in second}
        self.assertEqual(ids_a & ids_b, set(),
                         "the same outbox row was handed to two senders — that is a "
                         "curator receiving the pitch twice")
        self.assertEqual(ids_a | ids_b, set(queued),
                         "some queued row was never handed out at all")

    def test_a_row_already_claimed_is_not_returned_to_the_next_caller(self):
        """The sequential half of the same property, which is the shape a retry loop
        actually takes: one worker, two passes, because the first pass crashed before it
        could record anything."""
        _, outbox_id = self._queued()
        first = sender.claim(self.conn, self.tenant, "sender-a")
        self.assertEqual([str(r["id"]) for r in first], [outbox_id])
        self.assertEqual(sender.claim(self.conn, self.tenant, "sender-a"), [],
                         "a claimed row was handed out a second time")

    # --------------------------------------------- 3. the at-most-once choice, in the SQL

    def test_a_stranded_claimed_row_is_never_handed_out_again(self):
        """**The most important test in this file.**

        The scenario: a worker claimed the row, called SES, and the process died before
        `_record_sent` committed. Nobody knows whether the message went out. `fleet` would
        answer this by letting the lease expire and handing the lead to the next worker,
        and for a lead that is exactly right. Here it would mean mailing a curator who may
        already have the message.

        So the row stays in `claimed` forever and surfaces for a human. The row below is
        aged a full day and its `not_before` dragged into the past, so nothing except its
        state can be the reason it is skipped — and it must still be skipped. A future
        edit that adds an expiry clause "to stop rows getting stuck" fails here, and the
        failure message is the argument against the change.
        """
        _, outbox_id = self._queued()
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE outbox
                      SET state = 'claimed', claimed_by = 'worker-that-died',
                          claimed_at = now() - INTERVAL '1 day',
                          not_before = now() - INTERVAL '1 day'
                    WHERE tenant_id = %s AND id = %s""",
                (self.tenant, outbox_id))

        self.assertEqual(
            sender.claim(self.conn, self.tenant, "the-next-worker"), [],
            "a stranded `claimed` row was re-claimed. The worker that held it may "
            "already have called SES, so this is a second send to a curator who has the "
            "message. At-most-once is the deliberate choice here; see mail.py's header")

        # And nothing rescues it later either — this is not a long lease, it is no lease.
        self.assertEqual(sender.claim(self.conn, self.tenant, "the-next-worker"), [])
        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "claimed")
        self.assertEqual(row["claimed_by"], "worker-that-died",
                         "the stranded row must keep naming the worker that stranded it, "
                         "because that name is all a human has to go on")

    def test_a_send_that_is_wholly_prepared_still_needs_a_mailer_before_anything_is_claimed(self):
        """`send_once` resolves the mailer *first*, and that ordering is load-bearing.

        Claiming and then discovering there is no mailer would strand rows in `claimed`
        that, by the test above, nothing will ever pick up again — an unconfigured
        deployment would silently destroy its own outbox one batch at a time. This is the
        only test in the file that lets `mail.load()` run, and it runs it against a
        cleared environment where the only thing it can do is raise.
        """
        _, outbox_id = self._queued()
        with _paid_calls_allowed(), self.assertRaises(mail.MailNotConfigured):
            sender.send_once(self.conn, self.tenant, "sender-a")
        self.assertEqual(self._outbox(outbox_id)["state"], "pending",
                         "the outbox was claimed before the mailer was resolved")

    # --------------------------------------------------------- 4. the record is one write

    def test_a_successful_send_is_recorded_atomically(self):
        """Outbox, message, thread and `agent_run` in one transaction.

        The state this must make unreachable is "the provider has the message and the
        database does not know" — a thread still reading `queued`, a message with no
        provider id, and an operator who resends it by hand. Each assertion below is one
        half of a pair that has to move together; any of them lagging is a row that
        describes a send that did not happen the way the row says it did.
        """
        message_id, outbox_id = self._queued()
        mailer = StubMailer()

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)

        self.assertEqual(delivered, 1)
        self.assertEqual(len(mailer.calls), 1, "the provider was called more than once")
        self.assertEqual(mailer.calls[0]["to"], "curator@example.invalid")

        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "sent")
        self.assertIsNotNone(row["sent_at"])
        self.assertEqual(row["last_error"], "")

        message = self._message(message_id)
        self.assertEqual(message["provider_message_id"], "stub-provider-id",
                         "the provider's id is the only handle a bounce notification "
                         "arrives with — losing it loses the feedback loop")
        self.assertIsNotNone(message["sent_at"])

        self.assertEqual(self._thread_state(self.thread), "awaiting_reply",
                         "the thread must advance past `queued`, or the next drain sees "
                         "a conversation that still looks like it needs sending")

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT state, agent_kind, cost_micro_usd, summary FROM agent_run "
                "WHERE tenant_id = %s", (self.tenant,))
            runs = cur.fetchall()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["state"], "ok")
        self.assertEqual(runs[0]["agent_kind"], "sender")
        self.assertEqual(runs[0]["cost_micro_usd"], 100,
                         "$0.0001 in micro-dollars — the ceiling is summed from this "
                         "column, so a send that records zero is a send the daily cap "
                         "cannot see")
        self.assertIn("test-sender", runs[0]["summary"],
                      "the run must name who approved the send")

    def test_the_idempotency_key_reaches_the_provider(self):
        """Not a guarantee — SES has no idempotency parameter and `mail.py` says so. It
        is a forensic aid: two copies of one pitch arriving in a curator's inbox can be
        identified as one message rather than argued about. Dropping it here would cost
        nothing until the day it was the only evidence available."""
        message_id, _ = self._queued()
        mailer = StubMailer()
        with _paid_calls_allowed():
            sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)
        self.assertEqual(mailer.calls[0]["idempotency_key"],
                         self._message(message_id)["idempotency_key"])

    # ------------------------------------------------------- 5. the compliance boundary

    def test_an_opted_out_route_is_never_selected(self):
        """`opted_out` is what migration 018 made unoverwritable by any discovery stage,
        and this is the other half of that promise: a state no harvester can erase is
        worth nothing if the sender does not read it.

        Mailing somebody who asked to be removed is a legal defect in every jurisdiction
        this would send into, and it is the single most expensive mistake this table can
        make. `MailerThatMustNotBeCalled` means a regression fails inside the send rather
        than being detected after it.
        """
        curator, thread = self._counterparty_with_thread("Asked To Be Removed")
        self._route(curator, "stop@example.invalid", state="opted_out")
        _, outbox_id = self._queued(thread)

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "failed",
                         "an unsendable row must park for a human, not sit pending")
        self.assertIn("opted out", row["last_error"])

    def test_a_bounced_route_is_never_selected(self):
        """A route that hard-bounced is a route the provider has already told us does not
        resolve. Sending to it again is not a retry, it is a deliberate contribution to
        the sending domain's own reputation problem — the exact behaviour that gets a
        domain blocklisted and takes every future pitch with it."""
        curator, thread = self._counterparty_with_thread("Address Does Not Exist")
        self._route(curator, "gone@example.invalid", state="bounced")
        _, outbox_id = self._queued(thread)

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        self.assertEqual(self._outbox(outbox_id)["state"], "failed")

    def test_a_usable_route_is_still_found_when_an_unusable_one_exists_alongside_it(self):
        """The predicate has to exclude routes, not parties. A counterparty whose general
        address bounced but whose music director's address is verified is still
        reachable, and a sender that gave up on the whole party would quietly lose the
        best contacts in the database — the ones with enough history to have a bounce."""
        self._route(self.curator, "bounced@example.invalid", state="bounced")
        self._queued()
        mailer = StubMailer()
        with _paid_calls_allowed():
            sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)
        self.assertEqual([c["to"] for c in mailer.calls], ["curator@example.invalid"])

    def test_a_party_with_no_route_at_all_fails_with_a_reason_a_human_can_act_on(self):
        """The temptation this refuses is inventing an address. `info@` at a domain
        somebody scraped is one keystroke away and it is how a system starts mailing
        strangers who never appeared in anybody's shortlist.

        The reason string is the product here, not the refusal — this row parks for a
        human, and "failed" with no explanation is a row nobody can clear.
        """
        curator, thread = self._counterparty_with_thread("Nobody Knows How To Reach Them")
        _, outbox_id = self._queued(thread)

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "failed")
        self.assertIn("Nobody Knows How To Reach Them", row["last_error"],
                      "the reason must name the counterparty — an operator reading the "
                      "parked queue cannot look up a UUID for every row")
        self.assertIn("no sendable email route", row["last_error"])

    def test_a_guessed_address_is_refused(self):
        """**Known defect, pinned deliberately as an expected failure.**

        `018_contact_route.sql` states the contract in as many words: *"a guessed address
        is the single fastest way to earn a spam complaint, so it is labelled and the
        sender is expected to refuse it."* `provenance = 'inferred'` is that label —
        above all a pattern guess like `music@<domain>`.

        `sender._prepare` does not refuse it. Its allowlist is on `state` only, and
        `provenance` appears in the `ORDER BY` and nowhere else, so an inferred route is
        merely *deprioritised*: with no better route on the party it is selected and
        mailed. A counterparty discovered by a harvester that guessed at an address gets
        the pitch, and the complaint lands against a verified sending identity.

        This is one predicate — `AND provenance != 'inferred'` alongside the state
        allowlist, or a `verified`-only rule for inferred routes. It is left failing
        rather than asserted-as-is because a test that recorded the current behaviour as
        correct would make the migration's stated contract unenforceable.
        """
        curator, thread = self._counterparty_with_thread("Guessed At From A Domain")
        self._route(curator, "music@example.invalid",
                    state="unverified", provenance="inferred")
        _, outbox_id = self._queued(thread)

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        self.assertEqual(self._outbox(outbox_id)["state"], "failed")

    # ---------------------------------------------------------- 6. refusals, by kind

    def test_a_permanent_refusal_parks_the_row_and_never_returns_it_to_pending(self):
        """A hard bounce is information, not a transient condition. Retrying it is how a
        sending domain's reputation is destroyed — every repeat to a known-bad address is
        another signal to the receiving side that this sender does not read its own
        feedback.

        The assertion that matters is the last one: not merely that the state reads
        `failed`, but that `claim` will not pick it up. A state string nothing enforces
        is a comment.
        """
        _, outbox_id = self._queued()
        mailer = StubMailer(raises=mail.MailRefused(
            "SES refused: MessageRejected: address does not exist", permanent=True))

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)

        self.assertEqual(delivered, 0)
        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], 1,
                         "a permanent refusal must park on the first one, not consume "
                         "the attempt budget on its way there")
        self.assertIn("MessageRejected", row["last_error"])

        with self.conn.cursor() as cur:
            cur.execute("UPDATE outbox SET not_before = now() - INTERVAL '1 day' "
                        "WHERE tenant_id = %s AND id = %s", (self.tenant, outbox_id))
        self.assertEqual(sender.claim(self.conn, self.tenant, "sender-b"), [],
                         "a hard bounce came back round for another attempt")

    def test_a_transient_refusal_returns_the_row_to_pending_behind_a_backoff(self):
        """Throttling is the other kind of refusal and it must not park anything. The
        distinction lives in one boolean on `MailRefused`, and both halves have to be
        real or the flag is decoration: the row goes back to `pending`, unowned, and is
        genuinely not claimable until the backoff elapses."""
        _, outbox_id = self._queued()
        mailer = StubMailer(raises=mail.MailRefused(
            "SES refused: Throttling: maximum sending rate exceeded", permanent=False))

        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)

        self.assertEqual(delivered, 0)
        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertIsNone(row["claimed_by"], "the deferred row is still owned by nobody")
        self.assertIsNone(row["claimed_at"])
        self.assertGreater(row["not_before"], datetime.now(timezone.utc),
                           "a refusal with no backoff is a loop against a provider that "
                           "is already telling us to slow down")
        self.assertEqual(sender.claim(self.conn, self.tenant, "sender-b"), [],
                         "a backed-off row was immediately re-claimable")

    def test_repeated_transient_refusals_eventually_park_rather_than_loop(self):
        """`MAX_ATTEMPTS` has to engage on this path too. A provider that refuses
        forever, retried forever, is a row that never reaches a human and a bill that
        never stops — and every attempt here is a *possible* real delivery, which is why
        this ceiling is lower than the fleet's.

        `not_before` is dragged back between rounds; that stands in for the backoff
        elapsing rather than routing around it, and the assertion above already proved
        the backoff is applied.
        """
        _, outbox_id = self._queued()
        mailer = StubMailer(raises=mail.MailRefused("SES refused: Throttling: slow down"))

        for _ in range(sender.MAX_ATTEMPTS):
            with self.conn.cursor() as cur:
                cur.execute("UPDATE outbox SET not_before = now() "
                            "WHERE tenant_id = %s AND id = %s", (self.tenant, outbox_id))
            with _paid_calls_allowed():
                sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)

        row = self._outbox(outbox_id)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], sender.MAX_ATTEMPTS)
        self.assertLessEqual(len(mailer.calls), sender.MAX_ATTEMPTS,
                             "the provider was called more times than the attempt "
                             "ceiling allows")

    # -------------------------------------------------------------- 7. the kill switch

    def test_the_spend_gate_stops_the_send_before_the_provider_is_called(self):
        """`RTF_PAID_ENABLED=0` — the default — must be a working kill switch for
        outbound mail, and the rate card entry for `aws:ses:SendEmail` exists for exactly
        this. The money is not the point: $0.10 per thousand means a runaway loop that
        mails ten thousand curators costs a dollar and is unrecoverable. The gate is the
        only thing standing between a bug and that dollar's worth of damage.

        The check has to come *before* the provider call, not after it, which is what
        `MailerThatMustNotBeCalled` proves — an assertion on a call log afterwards would
        be checking the wreckage.
        """
        _, outbox_id = self._queued()

        with _paid_calls_off():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        row = self._outbox(outbox_id)
        self.assertNotEqual(row["state"], "sent")
        self.assertIn("Paid calls are off", row["last_error"])

    def test_a_daily_ceiling_already_spent_stops_the_send_too(self):
        """The other half of the kill switch, and the one that bounds a runaway rather
        than an experiment. `RTF_DAILY_CEILING_USD` is a hard cap on how many strangers
        this system can contact in a day, enforced by summing `agent_run.cost_micro_usd`
        — so a ceiling below one send's cost must refuse the first send, not the last."""
        _, outbox_id = self._queued()

        with _env(RTF_PAID_ENABLED="1", RTF_DAILY_CEILING_USD="0.00001",
                  RTF_PER_CALL_CEILING_USD="0.05",
                  PLATFORM_MAIL_REPLY_TO="tests@respect-the-funk.invalid",
                  PLATFORM_MAIL_POSTAL_ADDRESS="1 Test Way, Providence RI 02903"):
            delivered = sender.send_once(self.conn, self.tenant, "sender-a",
                                         mailer=MailerThatMustNotBeCalled())

        self.assertEqual(delivered, 0)
        self.assertIn("daily ceiling", self._outbox(outbox_id)["last_error"])

    def test_the_kill_switch_is_recoverable_by_raising_the_ceiling(self):
        """Turning the switch back on must be enough to make the send happen. A gate that
        left permanent damage behind would be one operators learn not to touch, and a
        kill switch nobody dares use is not a kill switch."""
        message_id, outbox_id = self._queued()

        with _paid_calls_off():
            sender.send_once(self.conn, self.tenant, "sender-a",
                             mailer=MailerThatMustNotBeCalled())
        with self.conn.cursor() as cur:  # the backoff elapsing, not routing around it
            cur.execute("UPDATE outbox SET not_before = now() "
                        "WHERE tenant_id = %s AND id = %s", (self.tenant, outbox_id))

        mailer = StubMailer()
        with _paid_calls_allowed():
            delivered = sender.send_once(self.conn, self.tenant, "sender-a", mailer=mailer)

        self.assertEqual(delivered, 1)
        self.assertEqual(self._outbox(outbox_id)["state"], "sent")
        self.assertIsNotNone(self._message(message_id)["sent_at"])

    def test_the_kill_switch_does_not_consume_the_attempt_budget(self):
        """**Known defect, pinned deliberately as an expected failure.**

        `send_once`'s own comment on this path says: *"Not a failure of the send; the
        send never happened. Back to pending untouched so raising the ceiling is all it
        takes to resume."* It is not untouched. `_record_failure(..., permanent=False)`
        increments `attempts` on every call and parks the row as `failed` once
        `attempts >= MAX_ATTEMPTS`, so three passes with the kill switch in its **default
        position** turn every human-approved outbox row into a parked failure. Measured:
        pending/1, pending/2, failed/3.

        `fleet` already settled this policy in the other direction — `test_fleet`'s
        `test_a_refused_spend_defers_without_burning_an_attempt` asserts `attempts == 0`
        after a refusal, on the argument that raising the ceiling should let the work run
        rather than find it parked. The Sender diverging from that is worse here than it
        was there, because these rows carry an approver's name and clearing them is
        manual work somebody has to do row by row.

        The fix is to defer without going through `_record_failure`: leave `attempts`
        alone, push `not_before` out, and record the run as `refused` the way `fleet`
        does. Left failing rather than rewritten to match, so the divergence stays
        visible.
        """
        _, outbox_id = self._queued()

        for _ in range(sender.MAX_ATTEMPTS):
            with self.conn.cursor() as cur:
                cur.execute("UPDATE outbox SET not_before = now() "
                            "WHERE tenant_id = %s AND id = %s", (self.tenant, outbox_id))
            with _paid_calls_off():
                sender.send_once(self.conn, self.tenant, "sender-a",
                                 mailer=MailerThatMustNotBeCalled())

        row = self._outbox(outbox_id)
        self.assertEqual(row["attempts"], 0,
                         "the kill switch charged an attempt for a send that never "
                         "happened")
        self.assertEqual(row["state"], "pending",
                         "an approved send was parked as failed because paid calls were "
                         "off — the default posture of every fresh deployment")


if __name__ == "__main__":
    unittest.main()
