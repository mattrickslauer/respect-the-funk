"""The two adapters, and the cursor discipline that decides whether they lose mail.

An adapter's job is small — turn what a provider hands back into `InboundMessage` —
and almost all of the risk is in one place: **where it resumes from**. Get that wrong in
one direction and every poll re-delivers the whole mailbox; wrong in the other and
replies are skipped silently, which is the failure this entire feature exists to end.
So most of this file is about cursors.

Both adapters take their client as an argument. That is not only for testability: the
Gmail one is an HTTP session and the IMAP one is a socket, and a module that constructs
its own connection cannot be driven by a poller that wants to reuse one.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid

from spindle import inbound, mailbox
from spindle.mailbox import gmail, imap

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

RAW = b"""From: Curator <curator@example.org>
To: label@example.com
Subject: Re: pitch
Message-ID: <r-1@example.org>

yes please
"""


def _account(**overrides) -> mailbox.Account:
    base = dict(tenant_id="t-1", provider="imap", address="label@example.com",
                secret_arn="arn:secret", imap_host="imap.example.com", imap_port=993,
                smtp_host="smtp.example.com", smtp_port=587, sync_cursor="",
                state="connected")
    base.update(overrides)
    return mailbox.Account(**base)


# ------------------------------------------------------------------------- IMAP

class FakeIMAP:
    """Enough of `imaplib.IMAP4_SSL` for the adapter, and nothing else.

    Records the search it was asked to run, because *which* search runs is the whole
    cursor question and asserting on the returned messages alone would not distinguish
    "resumed correctly" from "re-read everything and happened to get the same answer".
    """

    def __init__(self, *, uidvalidity: str = "111",
                 messages: dict[str, bytes] | None = None) -> None:
        self.uidvalidity = uidvalidity
        self.messages = messages if messages is not None else {"5": RAW}
        self.searches: list[str] = []
        self.logged_in: tuple[str, str] | None = None
        self.logged_out = False

    def login(self, user, password):
        self.logged_in = (user, password)
        return ("OK", [b""])

    def select(self, mailbox_name, readonly=False):
        return ("OK", [str(len(self.messages)).encode()])

    def status(self, mailbox_name, what):
        return ("OK", [f'"INBOX" (UIDVALIDITY {self.uidvalidity})'.encode()])

    def uid(self, command, *args):
        if command.upper() == "SEARCH":
            criterion = " ".join(str(a) for a in args if a is not None)
            self.searches.append(criterion)
            return ("OK", [" ".join(sorted(self.messages)).encode()])
        if command.upper() == "FETCH":
            uid = str(args[0])
            if uid not in self.messages:
                return ("OK", [None])
            return ("OK", [(b"1 (RFC822 {n}", self.messages[uid]), b")"])
        raise AssertionError(f"unexpected IMAP command {command!r}")

    def logout(self):
        self.logged_out = True


class IMAPCursors(unittest.TestCase):

    def test_a_first_poll_does_not_import_the_whole_mailbox(self) -> None:
        """Connecting a mailbox that has ten years of mail in it must not file a decade
        of messages against threads that did not exist. An empty cursor means *start
        here*, so the first poll establishes a position and delivers nothing."""
        client = FakeIMAP()
        found, cursor = imap.fetch(_account(sync_cursor=""), "pw", client=client)

        self.assertEqual(found, [])
        self.assertTrue(cursor)

    def test_the_first_poll_records_where_it_started(self) -> None:
        client = FakeIMAP(uidvalidity="111", messages={"5": RAW})
        _, cursor = imap.fetch(_account(sync_cursor=""), "pw", client=client)
        self.assertEqual(cursor, "111:5")

    def test_a_later_poll_asks_only_for_what_is_new(self) -> None:
        client = FakeIMAP(uidvalidity="111", messages={"6": RAW})
        imap.fetch(_account(sync_cursor="111:5"), "pw", client=client)

        # UID 6 onwards — not 5, which we already have, and not 1.
        self.assertIn("UID 6:*", client.searches[0])

    def test_messages_after_the_cursor_are_returned(self) -> None:
        client = FakeIMAP(uidvalidity="111", messages={"6": RAW})
        found, cursor = imap.fetch(_account(sync_cursor="111:5"), "pw", client=client)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].from_address, "curator@example.org")
        self.assertEqual(cursor, "111:6")

    def test_a_uidvalidity_change_resets_rather_than_replaying(self) -> None:
        """UIDVALIDITY changing means the server has renumbered everything, so the old
        UID is meaningless. Treating it as a number to resume from would deliver
        arbitrary mail; the safe reading is to re-establish position and deliver nothing
        this round."""
        client = FakeIMAP(uidvalidity="222", messages={"3": RAW})
        found, cursor = imap.fetch(_account(sync_cursor="111:5"), "pw", client=client)

        self.assertEqual(found, [])
        self.assertEqual(cursor, "222:3")

    def test_the_credential_is_what_is_presented(self) -> None:
        client = FakeIMAP()
        imap.fetch(_account(sync_cursor="111:1"), "hunter2", client=client)
        self.assertEqual(client.logged_in, ("label@example.com", "hunter2"))

    def test_the_connection_is_closed_even_when_parsing_explodes(self) -> None:
        """A poller that leaks sockets dies after a few hundred tenants."""
        class Exploding(FakeIMAP):
            def uid(self, command, *args):
                raise RuntimeError("boom")

        client = Exploding()
        with self.assertRaises(RuntimeError):
            imap.fetch(_account(sync_cursor="111:1"), "pw", client=client)
        self.assertTrue(client.logged_out)


# ------------------------------------------------------------------------ Gmail

class FakeGmail:
    """The two Gmail REST calls the adapter makes, plus the raw fetch."""

    def __init__(self, *, history_id: str = "9000", ids: list[str] | None = None,
                 raw: bytes = RAW, fail_history: bool = False) -> None:
        self.history_id = history_id
        self.ids = ids if ids is not None else []
        self.raw = raw
        self.fail_history = fail_history
        self.calls: list[str] = []

    def profile(self):
        self.calls.append("profile")
        return {"historyId": self.history_id}

    def history(self, start):
        self.calls.append(f"history:{start}")
        if self.fail_history:
            raise gmail.HistoryExpired("historyId too old")
        return {"ids": self.ids, "historyId": self.history_id}

    def recent(self):
        self.calls.append("recent")
        return {"ids": self.ids, "historyId": self.history_id}

    def raw_message(self, message_id):
        self.calls.append(f"raw:{message_id}")
        return self.raw


class GmailCursors(unittest.TestCase):

    def test_a_first_poll_establishes_a_history_id_and_delivers_nothing(self) -> None:
        client = FakeGmail(history_id="9000", ids=["m1"])
        found, cursor = gmail.fetch(_account(provider="gmail", sync_cursor=""),
                                    "token", client=client)

        self.assertEqual(found, [])
        self.assertEqual(cursor, "9000")
        self.assertIn("profile", client.calls)

    def test_a_later_poll_asks_history_from_the_cursor(self) -> None:
        client = FakeGmail(history_id="9100", ids=["m1"])
        found, cursor = gmail.fetch(_account(provider="gmail", sync_cursor="9000"),
                                    "token", client=client)

        self.assertIn("history:9000", client.calls)
        self.assertEqual(len(found), 1)
        self.assertEqual(cursor, "9100")

    def test_an_expired_history_id_falls_back_to_a_recent_listing(self) -> None:
        """Gmail expires historyIds after about a week. A mailbox nobody polled while a
        tenant was on holiday must recover rather than stop forever — and it must not
        silently skip, which is why the fallback lists recent messages instead of only
        resetting the cursor."""
        client = FakeGmail(history_id="9100", ids=["m1"], fail_history=True)
        found, cursor = gmail.fetch(_account(provider="gmail", sync_cursor="1"),
                                    "token", client=client)

        self.assertIn("recent", client.calls)
        self.assertEqual(len(found), 1)
        self.assertEqual(cursor, "9100")

    def test_the_parsed_message_carries_the_provider_id(self) -> None:
        client = FakeGmail(history_id="9100", ids=["m1"])
        found, _ = gmail.fetch(_account(provider="gmail", sync_cursor="9000"),
                               "token", client=client)
        self.assertEqual(found[0].provider_message_id, "r-1@example.org")


# ------------------------------------------------------------- account persistence

@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL")
class TheAccountRow(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-mbx-{self.tenant[:8]}", "mailbox test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def test_an_unconnected_tenant_has_no_account(self) -> None:
        """`None`, and the caller decides what that means. `mail.identity_for` turns it
        into the platform identity; the poller skips it. Neither is a default invented
        here."""
        self.assertIsNone(mailbox.load_account(self.conn, self.tenant))

    def test_connecting_stores_what_is_needed_to_reconnect(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap",
                        address="label@example.com", secret_arn="arn:x",
                        imap_host="imap.example.com", smtp_host="smtp.example.com")
        got = mailbox.load_account(self.conn, self.tenant)

        self.assertEqual(got.provider, "imap")
        self.assertEqual(got.address, "label@example.com")
        self.assertEqual(got.imap_host, "imap.example.com")

    def test_reconnecting_replaces_rather_than_duplicating(self) -> None:
        """One row per tenant — the primary key says so. A second connect is a change of
        mailbox, not a second mailbox."""
        mailbox.connect(self.conn, self.tenant, provider="imap",
                        address="a@example.com", secret_arn="arn:x",
                        imap_host="h", smtp_host="s")
        mailbox.connect(self.conn, self.tenant, provider="imap",
                        address="b@example.com", secret_arn="arn:y",
                        imap_host="h", smtp_host="s")

        self.assertEqual(mailbox.load_account(self.conn, self.tenant).address,
                         "b@example.com")

    def test_a_reconnect_clears_a_previous_failure(self) -> None:
        """Otherwise a tenant fixes their password and the poller keeps skipping them."""
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        mailbox.mark_failed(self.conn, self.tenant, "bad password")
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x2", imap_host="h", smtp_host="s")

        got = mailbox.load_account(self.conn, self.tenant)
        self.assertEqual(got.state, "connected")
        self.assertEqual(got.last_error, "")

    def test_a_failure_is_recorded_with_its_reason(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        mailbox.mark_failed(self.conn, self.tenant, "authentication rejected")

        got = mailbox.load_account(self.conn, self.tenant)
        self.assertEqual(got.state, "failed")
        self.assertIn("authentication", got.last_error)

    def test_a_failed_account_is_not_due_for_polling(self) -> None:
        """The whole point of the `failed` state: stop replaying a credential the
        provider has already rejected, which is how a mailbox gets locked at the far
        end."""
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        mailbox.mark_failed(self.conn, self.tenant, "nope")

        due = [a.tenant_id for a in mailbox.due(self.conn)]
        self.assertNotIn(self.tenant, due)

    def test_a_connected_account_is_due(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        due = [a.tenant_id for a in mailbox.due(self.conn)]
        self.assertIn(self.tenant, due)

    def test_recording_a_poll_advances_the_cursor(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        mailbox.record_poll(self.conn, self.tenant, "111:9")

        self.assertEqual(mailbox.load_account(self.conn, self.tenant).sync_cursor,
                         "111:9")

    def test_disconnecting_removes_the_row(self) -> None:
        mailbox.connect(self.conn, self.tenant, provider="imap", address="a@example.com",
                        secret_arn="arn:x", imap_host="h", smtp_host="s")
        mailbox.disconnect(self.conn, self.tenant)

        self.assertIsNone(mailbox.load_account(self.conn, self.tenant))


if __name__ == "__main__":
    unittest.main()
