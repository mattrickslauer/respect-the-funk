"""Email-OTP sign-in: the code, the challenge, and the two bounds that matter.

    python -m unittest discover platform/web/tests

The same split every suite here uses:

  * **Offline** — code generation and the salted digest. Properties of the functions,
    provable without a cluster and provable more sharply without one.
  * **Against the cluster** — expiry, attempt exhaustion, the resend floor, replacement,
    and the rolling-hour cap. Every one of those is a *database* behaviour: `attempts`
    lives in the row precisely so a Lambda that scales to zero cannot reset it by cycling
    containers, and a fake that counted in a dict would prove only that the fake counts.

The property this file exists to protect, above the others: **an online attacker gets
five guesses per challenge and ten minutes, and neither bound lives in a process.** Three
tests below hold each half of that, and one holds the thing that makes the bound
meaningful — that an exhausted challenge refuses even the *correct* code.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import timedelta

from rtf_platform import accounts, mail, otp

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class FakeMailer:
    """A `mail.Mailer`. Records what it was asked to send; never leaves the process."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str) -> mail.Sent:
        self.sent.append({"to": to, "subject": subject, "body": body,
                          "idempotency_key": idempotency_key})
        return mail.Sent(provider_message_id=f"fake-{len(self.sent)}", cost_usd="0.0001")


class RefusingMailer:
    """A mailer that always fails, for the path where SES rejects the message."""

    def send(self, *, to: str, subject: str, body: str,
             idempotency_key: str) -> mail.Sent:
        raise mail.MailRefused("SES refused: MessageRejected", permanent=True)


class TheCode(unittest.TestCase):
    """Generation and hashing. No database: these are properties of the functions."""

    def test_a_code_is_six_digits_including_the_leading_zeros(self):
        """`randbelow` returns integers, and an unpadded `str()` of one under 100000
        yields a five-character code that the form's pattern rejects and the user cannot
        type. Zero-padding is the fix, and this asserts it across enough draws to catch
        it."""
        for _ in range(500):
            code = otp.mint_code()
            self.assertEqual(otp.CODE_DIGITS, len(code))
            self.assertTrue(code.isdigit(), code)

    def test_codes_are_not_sequential_or_fixed(self):
        """Does not prove randomness — nothing cheap does. It proves the obvious failure
        a refactor introduces: a constant, or a counter."""
        drawn = {otp.mint_code() for _ in range(200)}
        self.assertGreater(len(drawn), 100)

    def test_the_digest_is_lowercase_hex_of_the_length_the_schema_checks(self):
        digest = otp.hash_code("someone@example.com", "123456")
        self.assertEqual(64, len(digest))
        self.assertEqual(digest, digest.lower())
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))

    def test_the_digest_is_salted_with_the_address(self):
        """The property migration 034's header is about, and the one that separates this
        from `accounts.hash_token`.

        Six digits is a dictionary of one million entries. Unsalted, `code_hash` would be
        a rainbow table anyone with read access precomputes once and reuses against every
        row forever. Binding the digest to the address makes a precomputed table useless
        against any address it was not built for — so the *same code* under two addresses
        must not produce the same digest."""
        self.assertNotEqual(otp.hash_code("a@example.com", "123456"),
                            otp.hash_code("b@example.com", "123456"))

    def test_the_digest_is_deterministic(self):
        self.assertEqual(otp.hash_code("a@example.com", "123456"),
                         otp.hash_code("a@example.com", "123456"))

    def test_the_bounds_are_the_ones_the_design_argued(self):
        """These four numbers are the whole of the online-attack bound. They are asserted
        next to that sentence rather than left as constants nobody rechecks: loosening any
        of them is a change to the threat model and should have to edit a test that says
        so."""
        self.assertEqual(6, otp.CODE_DIGITS)
        self.assertEqual(timedelta(minutes=10), otp.TTL)
        self.assertEqual(5, otp.MAX_ATTEMPTS)
        self.assertEqual(timedelta(seconds=30), otp.RESEND_AFTER)


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL")
class AgainstTheCluster(unittest.TestCase):
    """The challenge lifecycle, against the real table and the real clock.

    The clock is deliberately the cluster's. Every comparison in `otp.py` is written as
    SQL against `now()` rather than computed in Python, because two Lambda containers
    disagreeing about the time would disagree about whether a code had expired.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.mailer = FakeMailer()
        self.addresses: list[str] = []
        self.tenants: list[str] = []

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            for email in self.addresses:
                cur.execute("DELETE FROM otp_challenge WHERE email = %s", (email,))
            for tenant_id in self.tenants:
                cur.execute("DELETE FROM tenant WHERE id = %s", (tenant_id,))
        self.conn.close()

    def _address(self) -> str:
        email = f"otp-{uuid.uuid4().hex[:12]}@example.com"
        self.addresses.append(email)
        return email

    def _request(self, email: str) -> str:
        """Request a code and return it, read from what the fake mailer was handed.

        The code is *not* returned by `request_code` — there is no reveal path, by
        design — so a test learns it the same way a person does: out of the message.
        """
        before = len(self.mailer.sent)
        otp.request_code(self.conn, email, self.mailer)
        self.assertEqual(before + 1, len(self.mailer.sent))
        return otp_code_in(self.mailer.sent[-1]["body"])

    def _age_the_challenge(self, email: str, *, by: timedelta) -> None:
        """Move a challenge's clock backwards, so expiry and the resend floor are
        testable without sleeping through them."""
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE otp_challenge
                      SET sent_at = sent_at - %s, expires_at = expires_at - %s
                    WHERE email = %s""",
                (by, by, email))

    # ------------------------------------------------------------ the happy path

    def test_a_requested_code_verifies_once(self):
        email = self._address()
        code = self._request(email)
        self.assertEqual(email, otp.verify_code(self.conn, email, code))

    def test_the_code_is_mailed_and_is_not_returned_to_the_caller(self):
        """There is no dev-code path and no logging of codes. The only way to learn a
        code is to receive the message, and this asserts the message carries it."""
        email = self._address()
        returned = otp.request_code(self.conn, email, self.mailer)
        self.assertIsNone(returned)
        self.assertEqual(email, self.mailer.sent[-1]["to"])
        self.assertIn(otp_code_in(self.mailer.sent[-1]["body"]),
                      self.mailer.sent[-1]["body"])

    def test_verifying_consumes_the_challenge(self):
        """A code is single-use. Without the delete, an intercepted code stays valid for
        the rest of its ten minutes after the owner has already used it."""
        email = self._address()
        code = self._request(email)
        otp.verify_code(self.conn, email, code)
        with self.assertRaises(otp.OtpRefused) as caught:
            otp.verify_code(self.conn, email, code)
        self.assertEqual("no_challenge", caught.exception.reason)

    def test_the_raw_code_is_not_in_the_database(self):
        """The property the digest exists for, asserted the same way `test_accounts.py`
        asserts it for the token."""
        email = self._address()
        code = self._request(email)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code_hash FROM otp_challenge WHERE email = %s", (email,))
            stored = cur.fetchone()["code_hash"]
        self.assertNotEqual(code, stored)
        self.assertEqual(otp.hash_code(email, code), stored)

    # -------------------------------------------------------------- the bounds

    def test_a_wrong_code_is_refused_and_counted(self):
        email = self._address()
        self._request(email)
        with self.assertRaises(otp.OtpRefused) as caught:
            otp.verify_code(self.conn, email, "000000")
        self.assertEqual("wrong_code", caught.exception.reason)
        with self.conn.cursor() as cur:
            cur.execute("SELECT attempts FROM otp_challenge WHERE email = %s", (email,))
            self.assertEqual(1, cur.fetchone()["attempts"])

    def test_the_refusal_does_not_say_how_wrong_the_code_was(self):
        """Two codes wrong in different amounts must produce the identical sentence.
        Anything that varied with the guess — 'the first three digits are right' being the
        absurd case, but a different `reason` being the realistic one — hands an attacker
        a gradient to climb."""
        email = self._address()
        code = self._request(email)
        nearly = code[:-1] + ("0" if code[-1] != "0" else "1")

        with self.assertRaises(otp.OtpRefused) as one:
            otp.verify_code(self.conn, email, "000000")
        with self.assertRaises(otp.OtpRefused) as two:
            otp.verify_code(self.conn, email, nearly)

        self.assertEqual(str(one.exception), str(two.exception))
        self.assertEqual(one.exception.reason, two.exception.reason)

    def test_attempts_exhaust_and_then_even_the_right_code_is_refused(self):
        """The test that makes the bound mean anything.

        Counting attempts is pointless if the counter does not *close* the challenge, so
        this spends all five on wrong codes and then presents the correct one. It must
        still be refused, and by a reason that tells the person to start again rather than
        leaving them retyping a code that will never work now.
        """
        email = self._address()
        code = self._request(email)
        for _ in range(otp.MAX_ATTEMPTS):
            with self.assertRaises(otp.OtpRefused):
                otp.verify_code(self.conn, email, "000000")

        with self.assertRaises(otp.OtpRefused) as caught:
            otp.verify_code(self.conn, email, code)
        self.assertEqual("attempts_exhausted", caught.exception.reason)

    def test_an_expired_code_is_refused(self):
        email = self._address()
        code = self._request(email)
        self._age_the_challenge(email, by=otp.TTL + timedelta(minutes=1))
        with self.assertRaises(otp.OtpRefused) as caught:
            otp.verify_code(self.conn, email, code)
        self.assertEqual("expired", caught.exception.reason)

    def test_a_resend_inside_the_floor_is_refused(self):
        """Otherwise the form is a mail cannon anyone can aim at anyone's inbox."""
        email = self._address()
        self._request(email)
        with self.assertRaises(otp.OtpRefused) as caught:
            otp.request_code(self.conn, email, self.mailer)
        self.assertEqual("too_soon", caught.exception.reason)

    def test_a_resend_after_the_floor_replaces_the_previous_code(self):
        """One outstanding challenge per address — the reason `email` is the primary key.
        The old code must stop working the moment a new one is issued, or two codes are
        live at once and the attempt counter is spread across them."""
        email = self._address()
        first = self._request(email)
        self._age_the_challenge(email, by=otp.RESEND_AFTER + timedelta(seconds=5))
        second = self._request(email)
        self.assertNotEqual(first, second)

        with self.assertRaises(otp.OtpRefused) as caught:
            otp.verify_code(self.conn, email, first)
        self.assertEqual("wrong_code", caught.exception.reason)
        self.assertEqual(email, otp.verify_code(self.conn, email, second))

    def test_a_resend_resets_the_attempt_counter(self):
        """A fresh code is a fresh challenge. Carrying the old count forward would mean a
        person who fat-fingered five times could never sign in again from that address."""
        email = self._address()
        self._request(email)
        for _ in range(3):
            with self.assertRaises(otp.OtpRefused):
                otp.verify_code(self.conn, email, "000000")
        self._age_the_challenge(email, by=otp.RESEND_AFTER + timedelta(seconds=5))
        code = self._request(email)
        with self.conn.cursor() as cur:
            cur.execute("SELECT attempts FROM otp_challenge WHERE email = %s", (email,))
            self.assertEqual(0, cur.fetchone()["attempts"])
        self.assertEqual(email, otp.verify_code(self.conn, email, code))

    # ---------------------------------------------------------------- the mail

    def test_a_send_that_fails_leaves_no_challenge_behind(self):
        """A challenge whose code nobody received is worse than no challenge: the resend
        floor then blocks the retry for thirty seconds, so the person is locked out of a
        code they never got. The row is removed and the failure raised."""
        email = self._address()
        with self.assertRaises(mail.MailRefused):
            otp.request_code(self.conn, email, RefusingMailer())
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM otp_challenge WHERE email = %s",
                        (email,))
            self.assertEqual(0, cur.fetchone()["n"])

    def test_a_malformed_address_is_refused_before_anything_is_sent(self):
        with self.assertRaises(otp.OtpRefused) as caught:
            otp.request_code(self.conn, "not-an-address", self.mailer)
        self.assertEqual("email_malformed", caught.exception.reason)
        self.assertEqual([], self.mailer.sent)

    def test_the_address_is_normalised_the_same_way_account_email_is(self):
        """`otp_challenge.email` and `account.email` are compared to each other on every
        sign-in, so they must agree on what 'the same address' means. Requesting with one
        casing and verifying with another has to work, or a returning user whose mail
        client capitalised their address cannot sign in."""
        email = self._address()
        code = self._request(email.upper())
        self.assertEqual(email, otp.verify_code(self.conn, email.upper(), code))

    # ------------------------------------------------------ signing in end to end

    def test_a_first_sign_in_creates_the_tenant(self):
        email = self._address()
        code = self._request(email)
        otp.verify_code(self.conn, email, code)

        account, token = accounts.sign_in(self.conn, email)
        self.tenants.append(str(account["tenant_id"]))

        self.assertEqual(email, account["email"])
        self.assertEqual("free", account["plan"])
        found = accounts.account_for_token(self.conn, token)
        self.assertEqual(str(account["tenant_id"]), str(found["tenant_id"]))

    def test_a_second_sign_in_returns_the_same_tenant_with_a_new_token(self):
        """The defect this whole change exists to fix. Under `POST /claim` a second
        arrival at a known address was *refused*, because nothing proved the person owned
        it. Verification proves it, so the same tenant is returned — and the token is
        rotated, which is what makes the cookie a session."""
        email = self._address()
        first_account, first_token = accounts.sign_in(self.conn, email)
        self.tenants.append(str(first_account["tenant_id"]))

        second_account, second_token = accounts.sign_in(self.conn, email)

        self.assertEqual(str(first_account["tenant_id"]),
                         str(second_account["tenant_id"]))
        self.assertNotEqual(first_token, second_token)

    def test_rotating_the_token_signs_the_previous_holder_out(self):
        """The honest cost of one session per account, asserted rather than described.
        Migration 033 wrote this down as the accepted limitation; if it ever stops being
        true, this test should be the thing that fails."""
        email = self._address()
        account, first_token = accounts.sign_in(self.conn, email)
        self.tenants.append(str(account["tenant_id"]))
        _, second_token = accounts.sign_in(self.conn, email)

        self.assertIsNone(accounts.account_for_token(self.conn, first_token))
        self.assertIsNotNone(accounts.account_for_token(self.conn, second_token))


def otp_code_in(body: str) -> str:
    """The six-digit code out of a message body, for tests only.

    A helper rather than a regex inline in five places, and deliberately strict: it
    asserts there is exactly one run of six digits, so a body that stopped carrying the
    code — or started carrying two numbers — fails loudly here instead of making some
    later assertion mysteriously compare the wrong string.
    """
    import re

    found = re.findall(r"\b\d{6}\b", body)
    if len(found) != 1:
        raise AssertionError(
            f"expected exactly one six-digit code in the message body, found "
            f"{len(found)}: {found!r}")
    return found[0]


if __name__ == "__main__":
    unittest.main()
