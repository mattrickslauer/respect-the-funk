"""The half of the conversation this system could not previously hear.

Two layers, the same split `test_outreach.py` makes and for its reason.

  * **Offline** — parsing an RFC 822 message and building or reading a plus-addressed
    Reply-To. Pure text handling; a cluster would prove nothing about it.

  * **Against the cluster** — the correlation ladder, because every rung of it is a
    query. Which thread a reply belongs to is a question only the database can answer,
    and a fake returning rows from a dict would prove the fake works.

The property that matters most here is the mirror of the one `test_outreach.py` names.
That file protects *an approved draft produces exactly one outbox row, or none*. This one
protects **a reply that arrives is either attached to exactly one thread or recorded as
unattached — never silently dropped, and never attached to the wrong tenant's thread.**
The second half is not paranoia: on the platform path every tenant's replies land in one
mailbox, so the only thing standing between them is the token lookup below.
"""

from __future__ import annotations

import os
import unittest
import uuid

from spindle import inbound, outreach

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


# ------------------------------------------------------------------------ parsing

RAW_SIMPLE = b"""From: Curator <curator@example.org>
To: spindle+abc123@mattrickslauer.com
Subject: Re: Hallow Youth - Losing Sleep
Message-ID: <reply-001@mail.example.org>
In-Reply-To: <0100018f0000@us-east-1.amazonses.com>
References: <0100018f0000@us-east-1.amazonses.com>
Content-Type: text/plain; charset="utf-8"

Love this one. Adding it to the Friday show.
"""

RAW_MULTIPART = b"""From: "Curator, A." <curator@example.org>
To: spindle+abc123@mattrickslauer.com
Subject: Re: pitch
Message-ID: <reply-002@mail.example.org>
Content-Type: multipart/alternative; boundary="BOUND"

--BOUND
Content-Type: text/plain; charset="utf-8"

the plain part
--BOUND
Content-Type: text/html; charset="utf-8"

<html><body>the html part</body></html>
--BOUND--
"""


class Parsing(unittest.TestCase):

    def test_the_sender_is_the_address_and_not_the_display_name(self) -> None:
        """`From: Curator <curator@example.org>` must yield the address alone — the
        third rung of the ladder matches it against `contact_route.value`, which holds
        bare addresses."""
        msg = inbound.parse(RAW_SIMPLE)
        self.assertEqual(msg.from_address, "curator@example.org")

    def test_a_display_name_containing_a_comma_does_not_split_the_address(self) -> None:
        """`"Curator, A." <curator@example.org>` is one address, not two. Pinned because
        a naive split on ',' passes every other test in this class."""
        msg = inbound.parse(RAW_MULTIPART)
        self.assertEqual(msg.from_address, "curator@example.org")

    def test_the_subject_survives(self) -> None:
        msg = inbound.parse(RAW_SIMPLE)
        self.assertEqual(msg.subject, "Re: Hallow Youth - Losing Sleep")

    def test_the_provider_message_id_is_stripped_of_its_angle_brackets(self) -> None:
        """Stored bare, so it compares equal to what the outbound side recorded."""
        msg = inbound.parse(RAW_SIMPLE)
        self.assertEqual(msg.provider_message_id, "reply-001@mail.example.org")

    def test_in_reply_to_and_references_are_both_captured(self) -> None:
        msg = inbound.parse(RAW_SIMPLE)
        self.assertEqual(msg.in_reply_to, "0100018f0000@us-east-1.amazonses.com")
        self.assertIn("0100018f0000@us-east-1.amazonses.com", msg.references)

    def test_the_body_is_the_text_part(self) -> None:
        msg = inbound.parse(RAW_SIMPLE)
        self.assertIn("Adding it to the Friday show", msg.body)

    def test_multipart_prefers_plain_text_over_html(self) -> None:
        """A curator's client sends both. The plain part is what a person wrote; the
        HTML part is what their client made of it, and storing markup would put tags
        into the thread view and into anything that later reads the text."""
        msg = inbound.parse(RAW_MULTIPART)
        self.assertIn("the plain part", msg.body)
        self.assertNotIn("<html>", msg.body)

    def test_every_recipient_is_captured(self) -> None:
        """The token can arrive in `To`, and on a forward it arrives in `Delivered-To`
        instead — so the ladder needs all of them, not just the first."""
        msg = inbound.parse(RAW_SIMPLE)
        self.assertIn("spindle+abc123@mattrickslauer.com", msg.to_addresses)


class PlusAddressing(unittest.TestCase):
    """The platform path's routing, which is the whole reason a shared mailbox can serve
    every tenant without mixing them up."""

    def test_a_reply_address_carries_the_token_in_the_local_part(self) -> None:
        self.assertEqual(
            inbound.reply_address("spindle@mattrickslauer.com", "abc123"),
            "spindle+abc123@mattrickslauer.com")

    def test_a_base_address_that_already_has_a_tag_is_refused(self) -> None:
        """Two tags would produce `spindle+old+new@`, whose token nothing can read back.
        Fail where it is built rather than where it fails to match."""
        with self.assertRaises(ValueError):
            inbound.reply_address("spindle+old@mattrickslauer.com", "new")

    def test_the_token_is_read_back_out_of_the_recipients(self) -> None:
        self.assertEqual(
            inbound.token_in(("spindle+abc123@mattrickslauer.com",)), "abc123")

    def test_an_address_with_no_tag_yields_no_token(self) -> None:
        self.assertEqual(inbound.token_in(("spindle@mattrickslauer.com",)), "")

    def test_no_recipients_yields_no_token(self) -> None:
        self.assertEqual(inbound.token_in(()), "")

    def test_the_token_is_found_wherever_in_the_recipient_list_it_sits(self) -> None:
        """A reply-all puts the artist's own address first and ours third."""
        self.assertEqual(
            inbound.token_in(("someone@else.org", "cc@example.org",
                              "spindle+abc123@mattrickslauer.com")), "abc123")

    def test_a_minted_token_is_not_guessable_from_another(self) -> None:
        self.assertNotEqual(inbound.mint_reply_token(), inbound.mint_reply_token())

    def test_a_minted_token_survives_a_round_trip_through_an_address(self) -> None:
        token = inbound.mint_reply_token()
        built = inbound.reply_address("spindle@mattrickslauer.com", token)
        self.assertEqual(inbound.token_in((built,)), token)


class MessageIdCandidates(unittest.TestCase):
    """SES hands back a bare `MessageId`, and the header it actually puts on the wire is
    `<{MessageId}@{region}.amazonses.com>`. So what a curator's client quotes back in
    `In-Reply-To` is not string-equal to what `sender.py` stored, and the second rung of
    the ladder has to know that or it never matches anything."""

    def test_the_bare_id_is_a_candidate(self) -> None:
        self.assertIn("0100018f0000",
                      inbound.message_id_candidates("0100018f0000@us-east-1.amazonses.com"))

    def test_the_full_form_is_also_a_candidate(self) -> None:
        self.assertIn("0100018f0000@us-east-1.amazonses.com",
                      inbound.message_id_candidates("0100018f0000@us-east-1.amazonses.com"))

    def test_angle_brackets_are_tolerated(self) -> None:
        self.assertIn("0100018f0000",
                      inbound.message_id_candidates("<0100018f0000@us-east-1.amazonses.com>"))


# --------------------------------------------------------------- against the cluster

@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL — these test the database, not the code")
class TheCorrelationLadder(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-inbound-{self.tenant[:8]}", "inbound test"))
        self.artist = self._party("Test Artist", "roster")
        self.curator = self._party("Test Curator", "counterparty")
        self.campaign = str(outreach.create_campaign(
            self.conn, self.tenant, party_id=self.artist, name="inbound test")["id"])
        self.thread = str(outreach.open_thread(
            self.conn, self.tenant, campaign_id=self.campaign,
            counterparty_id=self.curator)["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    # ------------------------------------------------------------------- helpers

    def _party(self, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, self.tenant, f"{name.lower().replace(' ', '-')}-{party_id[:8]}",
                 name, party_class))
        return party_id

    def _route(self, party_id: str, address: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO contact_route (tenant_id, party_id, channel, value, state, "
                "provenance) VALUES (%s, %s, 'email', %s, 'verified', 'asserted')",
                (self.tenant, party_id, address))

    def _sent(self, provider_message_id: str) -> str:
        """An outbound message already on the wire, which is what a reply replies to."""
        message_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO message (id, tenant_id, thread_id, direction, subject,
                                        body, idempotency_key, provider_message_id, sent_at)
                   VALUES (%s, %s, %s, 'outbound', 'pitch', 'body', %s, %s, now())""",
                (message_id, self.tenant, self.thread, f"k-{message_id}",
                 provider_message_id))
            cur.execute(
                "UPDATE thread SET state = 'awaiting_reply' "
                "WHERE tenant_id = %s AND id = %s", (self.tenant, self.thread))
        return message_id

    def _token(self) -> str:
        token = inbound.mint_reply_token()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE thread SET reply_token = %s WHERE tenant_id = %s AND id = %s",
                        (token, self.tenant, self.thread))
        return token

    def _msg(self, **overrides) -> inbound.InboundMessage:
        base = dict(provider_message_id=f"in-{uuid.uuid4()}",
                    from_address="curator@example.org",
                    to_addresses=("spindle@mattrickslauer.com",),
                    subject="Re: pitch", body="yes please",
                    in_reply_to="", references=())
        base.update(overrides)
        return inbound.InboundMessage(**base)

    def _state(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT state FROM thread WHERE id = %s", (self.thread,))
            return cur.fetchone()["state"]

    def _inbound_count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM message WHERE tenant_id = %s "
                        "AND direction = 'inbound'", (self.tenant,))
            return cur.fetchone()["n"]

    def _unmatched_count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM inbound_unmatched WHERE tenant_id = %s",
                        (self.tenant,))
            return cur.fetchone()["n"]

    # -------------------------------------------------------- rung 1: the token

    def test_a_reply_bearing_the_thread_token_lands_on_that_thread(self) -> None:
        self._sent("ses-1")
        token = self._token()
        msg = self._msg(to_addresses=(f"spindle+{token}@mattrickslauer.com",))

        got = inbound.deliver(self.conn, msg)

        self.assertEqual(got.thread_id, self.thread)
        self.assertEqual(got.matched_by, "reply_token")

    def test_the_token_resolves_the_tenant_without_being_told_it(self) -> None:
        """The platform path's whole premise: one mailbox, many tenants, and the token
        is the only thing that says whose reply this is."""
        self._sent("ses-1")
        token = self._token()
        msg = self._msg(to_addresses=(f"spindle+{token}@mattrickslauer.com",))

        got = inbound.deliver(self.conn, msg)

        self.assertEqual(got.tenant_id, self.tenant)

    def test_a_delivered_reply_moves_the_thread_off_awaiting_reply(self) -> None:
        """The symptom that started this: a thread that had been answered still saying
        it was waiting."""
        self._sent("ses-1")
        token = self._token()
        self.assertEqual(self._state(), "awaiting_reply")

        inbound.deliver(self.conn, self._msg(
            to_addresses=(f"spindle+{token}@mattrickslauer.com",)))

        self.assertEqual(self._state(), "replied")

    # ------------------------------------------------- rung 2: the threading headers

    def test_a_reply_is_matched_by_in_reply_to_when_there_is_no_token(self) -> None:
        self._sent("0100018f0000")
        msg = self._msg(in_reply_to="<0100018f0000@us-east-1.amazonses.com>")

        got = inbound.deliver(self.conn, msg, tenant_id=self.tenant)

        self.assertEqual(got.thread_id, self.thread)
        self.assertEqual(got.matched_by, "in_reply_to")

    def test_references_are_tried_when_in_reply_to_is_absent(self) -> None:
        """Some clients send `References` and no `In-Reply-To`. The oldest reference is
        the original pitch, which is the one we sent."""
        self._sent("0100018f0000")
        msg = self._msg(references=("<0100018f0000@us-east-1.amazonses.com>",))

        got = inbound.deliver(self.conn, msg, tenant_id=self.tenant)

        self.assertEqual(got.thread_id, self.thread)

    # ---------------------------------------------------- rung 3: the sender address

    def test_a_reply_is_matched_by_sender_address_as_a_last_resort(self) -> None:
        """Safe because `one_open_thread_per_counterparty` guarantees at most one open
        thread per party — so this cannot be ambiguous while the index holds."""
        self._sent("ses-1")
        self._route(self.curator, "curator@example.org")
        msg = self._msg(from_address="curator@example.org")

        got = inbound.deliver(self.conn, msg, tenant_id=self.tenant)

        self.assertEqual(got.thread_id, self.thread)
        self.assertEqual(got.matched_by, "from_address")

    def test_the_sender_match_is_case_insensitive(self) -> None:
        """Addresses are quoted back with whatever capitalisation the client felt like.
        `Curator@Example.org` is the same mailbox."""
        self._sent("ses-1")
        self._route(self.curator, "curator@example.org")

        got = inbound.deliver(self.conn, self._msg(from_address="Curator@Example.ORG"),
                              tenant_id=self.tenant)

        self.assertEqual(got.thread_id, self.thread)

    # ------------------------------------------------------- rung 4: no match at all

    def test_an_unattributable_reply_is_recorded_rather_than_dropped(self) -> None:
        got = inbound.deliver(self.conn, self._msg(from_address="stranger@nowhere.org"),
                              tenant_id=self.tenant)

        self.assertIsNone(got.thread_id)
        self.assertEqual(self._unmatched_count(), 1)

    def test_an_unattributable_reply_writes_no_message_row(self) -> None:
        """A message row needs a thread. Inventing one would be the drift failure this
        whole ladder is written to avoid."""
        inbound.deliver(self.conn, self._msg(from_address="stranger@nowhere.org"),
                        tenant_id=self.tenant)

        self.assertEqual(self._inbound_count(), 0)

    def test_the_unmatched_row_says_why(self) -> None:
        """Read by a person deciding where to file it by hand."""
        inbound.deliver(self.conn, self._msg(from_address="stranger@nowhere.org"),
                        tenant_id=self.tenant)
        with self.conn.cursor() as cur:
            cur.execute("SELECT reason FROM inbound_unmatched WHERE tenant_id = %s",
                        (self.tenant,))
            self.assertTrue(cur.fetchone()["reason"].strip())

    def test_an_unknown_tenant_and_an_unknown_thread_refuses_rather_than_guessing(self) -> None:
        """No token, no tenant told to us. There is nowhere to put this row and no way
        to know whose it is — so it raises, and the SES path leaves the raw message in
        the S3 bucket it is already durably in."""
        with self.assertRaises(inbound.Unattributable):
            inbound.deliver(self.conn, self._msg())

    # ----------------------------------------------------------------- idempotency

    def test_the_same_message_delivered_twice_writes_one_row(self) -> None:
        """Every adapter re-reads. A cursor reset, a crash between fetch and commit, an
        IMAP UIDVALIDITY bump — all of them replay messages we have already seen."""
        self._sent("ses-1")
        token = self._token()
        msg = self._msg(to_addresses=(f"spindle+{token}@mattrickslauer.com",))

        inbound.deliver(self.conn, msg)
        inbound.deliver(self.conn, msg)

        self.assertEqual(self._inbound_count(), 1)

    def test_the_same_unmatched_message_twice_writes_one_row(self) -> None:
        msg = self._msg(from_address="stranger@nowhere.org")

        inbound.deliver(self.conn, msg, tenant_id=self.tenant)
        inbound.deliver(self.conn, msg, tenant_id=self.tenant)

        self.assertEqual(self._unmatched_count(), 1)

    # ------------------------------------------------------------ the tenant boundary

    def test_a_token_from_another_tenant_does_not_deliver_into_this_one(self) -> None:
        """The single most important test in this file. On the platform path every
        tenant's replies arrive in one mailbox, so if a stated `tenant_id` could
        override what the token says, one label's curator reply would be filed into
        another label's thread — and the console would show it as theirs."""
        self._sent("ses-1")
        token = self._token()

        other_tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (other_tenant, f"test-other-{other_tenant[:8]}", "other"))
        try:
            msg = self._msg(to_addresses=(f"spindle+{token}@mattrickslauer.com",))
            got = inbound.deliver(self.conn, msg, tenant_id=other_tenant)

            # The token wins, and it names the tenant that owns the thread.
            self.assertEqual(got.tenant_id, self.tenant)
            self.assertEqual(got.thread_id, self.thread)
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM tenant WHERE id = %s", (other_tenant,))

    def test_headers_are_not_matched_across_a_tenant_boundary(self) -> None:
        """Rung two matches on a provider id. Told the wrong tenant, it must find
        nothing rather than reach into the tenant that owns the message."""
        self._sent("0100018f0000")

        other_tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (other_tenant, f"test-other-{other_tenant[:8]}", "other"))
        try:
            msg = self._msg(in_reply_to="<0100018f0000@us-east-1.amazonses.com>")
            got = inbound.deliver(self.conn, msg, tenant_id=other_tenant)
            self.assertIsNone(got.thread_id)
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM tenant WHERE id = %s", (other_tenant,))


if __name__ == "__main__":
    unittest.main()
