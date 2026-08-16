"""Whose address a pitch goes out from, and the two transports that can send it.

`identity_for` is the single decision point. Everything that sends outreach asks it, and
it answers with one of exactly two things: the platform's SES identity, or the tenant's
own connected mailbox. There is deliberately no third answer and no silent degradation
between them — a tenant whose mailbox has failed gets a refusal naming the reason, never
a quiet fallback onto the platform address. Sending a label's pitch from somebody else's
address is the failure `040_mailbox.sql` exists to prevent, so it must not be the error
path of the module that prevents it.
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from spindle import mail, mailbox, plans
from spindle.mailbox import identity as identity_mod

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class SMTPSending(unittest.TestCase):
    """The transport for every provider that is not Google."""

    def _mailer(self):
        return mail.SMTPMailer(sender="label@example.com", host="smtp.example.com",
                               port=587, password="pw")

    def test_the_message_is_sent_from_the_tenants_own_address(self) -> None:
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                sent["endpoint"] = (host, port)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                sent["tls"] = True

            def login(self, user, password):
                sent["login"] = (user, password)

            def send_message(self, message):
                sent["message"] = message

        with mock.patch("smtplib.SMTP", FakeSMTP):
            self._mailer().send(to="curator@example.org", subject="s", body="b",
                                idempotency_key="k")

        self.assertEqual(sent["message"]["From"], "label@example.com")
        self.assertEqual(sent["endpoint"], ("smtp.example.com", 587))

    def test_tls_is_always_started(self) -> None:
        """A password over a cleartext SMTP session is the tenant's mailbox password.
        Pinned so nobody makes this conditional on a config flag."""
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): sent["tls"] = True
            def login(self, u, p): pass
            def send_message(self, m): pass

        with mock.patch("smtplib.SMTP", FakeSMTP):
            self._mailer().send(to="c@example.org", subject="s", body="b",
                                idempotency_key="k")

        self.assertTrue(sent.get("tls"))

    def test_a_per_message_reply_to_is_carried(self) -> None:
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, u, p): pass
            def send_message(self, m): sent["message"] = m

        with mock.patch("smtplib.SMTP", FakeSMTP):
            self._mailer().send(to="c@example.org", subject="s", body="b",
                                idempotency_key="k", reply_to="label+tok@example.com")

        self.assertEqual(sent["message"]["Reply-To"], "label+tok@example.com")

    def test_a_rejected_login_is_permanent_rather_than_retried(self) -> None:
        """Same rule the IMAP adapter follows: a provider asked repeatedly for a password
        it has refused will lock the mailbox at its end."""
        import smtplib

        class FakeSMTP:
            def __init__(self, host, port, timeout=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, u, p):
                raise smtplib.SMTPAuthenticationError(535, b"nope")
            def send_message(self, m): pass

        with mock.patch("smtplib.SMTP", FakeSMTP):
            with self.assertRaises(mail.MailRefused) as caught:
                self._mailer().send(to="c@example.org", subject="s", body="b",
                                    idempotency_key="k")

        self.assertTrue(caught.exception.permanent)

    def test_the_password_never_reaches_the_error_message(self) -> None:
        import smtplib

        class FakeSMTP:
            def __init__(self, host, port, timeout=None): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): pass
            def login(self, u, p):
                raise smtplib.SMTPAuthenticationError(535, b"nope")
            def send_message(self, m): pass

        mailer = mail.SMTPMailer(sender="label@example.com", host="h", port=587,
                                 password="SUPERSECRET")
        with mock.patch("smtplib.SMTP", FakeSMTP):
            with self.assertRaises(mail.MailRefused) as caught:
                mailer.send(to="c@example.org", subject="s", body="b",
                            idempotency_key="k")

        self.assertNotIn("SUPERSECRET", str(caught.exception))


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL")
class WhichIdentitySends(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-id-{self.tenant[:8]}", "identity test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _connect_mailbox(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap",
                        address="label@example.com", secret_arn="arn:x",
                        imap_host="imap.example.com", smtp_host="smtp.example.com")

    def test_a_tenant_with_no_mailbox_sends_as_the_platform(self) -> None:
        got = identity_mod.identity_for(self.conn, self.tenant, plans.tier("free"))
        self.assertEqual(got.kind, "platform")

    def test_a_connected_tenant_sends_as_itself(self) -> None:
        self._connect_mailbox()
        got = identity_mod.identity_for(self.conn, self.tenant, plans.tier("label"))

        self.assertEqual(got.kind, "tenant")
        self.assertEqual(got.sender, "label@example.com")

    def test_a_failed_mailbox_refuses_rather_than_falling_back(self) -> None:
        """The most important test here. Silently sending from the platform address
        because a tenant's own mailbox is broken would put their pitch out under a From
        they never agreed to, and they would find out from the curator."""
        self._connect_mailbox()
        mailbox.mark_failed(self.conn, self.tenant, "password rejected")

        with self.assertRaises(identity_mod.MailboxUnavailable) as caught:
            identity_mod.identity_for(self.conn, self.tenant, plans.tier("label"))

        self.assertIn("password rejected", str(caught.exception))

    def test_a_downgraded_tenant_refuses_rather_than_silently_using_the_platform(self) -> None:
        """A tenant who connected a mailbox on a paid plan and then dropped to free. The
        row is still there and the entitlement is gone; that disagreement is a decision
        for a person, not something to paper over on a send path."""
        self._connect_mailbox()

        with self.assertRaises(identity_mod.MailboxUnavailable):
            identity_mod.identity_for(self.conn, self.tenant, plans.tier("free"))

    def test_a_tier_that_may_not_send_at_all_is_refused_first(self) -> None:
        """`sends_enabled` is checked before anything else, so a plan change that turns
        sending off is not defeated by a mailbox that is still connected."""
        no_send = plans.tier("free").__class__(
            **{**plans.tier("free").__dict__, "sends_enabled": False})
        with self.assertRaises(identity_mod.SendingNotPermitted):
            identity_mod.identity_for(self.conn, self.tenant, no_send)


if __name__ == "__main__":
    unittest.main()
