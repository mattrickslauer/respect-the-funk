"""Listen links: the token, its lifetime, and the sentence it produces.

    python -m unittest discover apps/spindle/web/tests

The properties this file is for, in the order they would hurt if they broke:

  * **A token is stored as a hash and never as itself.** The rows here point at masters,
    so a read of this table must not yield anything replayable. Asserted against the
    column, not against the function that writes it.
  * **Unknown, revoked and expired are one outcome.** Three states, one exception, one
    404 — a caller must not be able to tell a guessed token from a withdrawn one, because
    the difference confirms the withdrawn one was real.
  * **A token from tenant A never resolves against tenant B's asset.** Migration 016's
    argument, at the point where getting it wrong mints a public download URL for
    somebody else's master rather than merely leaking a row.
  * **The pitch never claims a format it is not linking.** The wording is derived from
    `recording_asset.mime`; the copy this replaced promised a WAV on every send while the
    only stored asset on the cluster was an MP3.
  * **An unconfigured deployment raises rather than composing a broken link.** A pitch is
    in somebody's inbox and cannot be corrected afterwards.
"""

from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from spindle import listen

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class TokenValues(unittest.TestCase):
    """No database. These are properties of the two functions and nothing else."""

    def test_a_minted_token_is_wide_and_url_safe(self):
        token = listen.mint_token()
        # 32 random bytes, base64url — long enough that guessing is not a threat model,
        # which is what lets the link live ninety days with no attempt counter.
        self.assertGreaterEqual(len(token), 40)
        self.assertTrue(all(c.isalnum() or c in "-_" for c in token), token)

    def test_two_mints_differ(self):
        self.assertNotEqual(listen.mint_token(), listen.mint_token())

    def test_hash_is_lowercase_hex_sha256(self):
        """The shape the database CHECK enforces. If this function ever changed
        algorithm, every existing row would stop resolving and the constraint is what
        would catch it — so the constraint's shape is asserted here too."""
        digest = listen.hash_token("abc")
        self.assertEqual(digest, hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))


class FormatNaming(unittest.TestCase):
    """The half of this feature that is about not lying in a pitch."""

    def test_known_types_are_named(self):
        self.assertEqual(listen.format_name("audio/wav"), "WAV")
        self.assertEqual(listen.format_name("audio/mpeg"), "MP3")
        self.assertEqual(listen.format_name("audio/flac"), "FLAC")

    def test_an_unknown_type_is_not_guessed_at(self):
        """`storage._SUFFIX`'s argument, applied to prose: an unrecognised type yields no
        format claim rather than one invented from the string."""
        self.assertEqual(listen.format_name("audio/weird"), "audio")
        self.assertEqual(listen.format_name(""), "audio")


class BodyComposition(unittest.TestCase):

    def _link(self) -> listen.Link:
        return listen.Link(token="tok",
                           url="https://spindle.mattrickslauer.com/listen/tok",
                           expires_at=datetime.now(timezone.utc))

    def test_no_asset_leaves_the_pitch_exactly_as_it_was(self):
        """The common case today — one stored master exists across the whole cluster —
        and the one where the old 'on request' wording is still true."""
        body = "Hi,\n\nWe have a record.\n\nThanks,\nRespect the Funk"
        self.assertEqual(listen.append_link(body, None), body)

    def test_the_link_is_appended_with_the_named_format(self):
        out = listen.append_link("Hi,\n\nWe have a record.", self._link(),
                                 format_label="MP3")
        self.assertIn("https://spindle.mattrickslauer.com/listen/tok", out)
        self.assertIn("MP3", out)
        self.assertNotIn("WAV", out)

    def test_an_unnamed_format_says_master_rather_than_inventing_one(self):
        out = listen.append_link("Hi.", self._link())
        self.assertIn("master", out)
        self.assertNotIn("WAV", out)


class MintRefusals(unittest.TestCase):

    def test_no_base_url_raises_and_names_the_variable(self):
        """`NO FALLBACKS` at the point it matters most. A relative path in an email
        resolves against nothing, and a guess at the Function URL is a link the reader
        does not recognise on a domain whose reputation was fixed at some effort."""
        with self.assertRaises(listen.LinkUnconfigured) as caught:
            listen.mint(None, "t", message_id="m", recording_asset_id="a",  # type: ignore[arg-type]
                        counterparty_id="c", base_url="")
        self.assertIn("PLATFORM_PUBLIC_BASE_URL", str(caught.exception))

    def test_it_refuses_before_touching_the_connection(self):
        """`None` is passed as the connection above deliberately: if the check ever moved
        below the INSERT this test would raise AttributeError instead, which is the
        failure telling us the refusal stopped being the first thing that happens."""
        with self.assertRaises(listen.LinkUnconfigured):
            listen.mint(None, "t", message_id="m", recording_asset_id="a",  # type: ignore[arg-type]
                        counterparty_id="c", base_url="")


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL")
class TokenLifecycle(unittest.TestCase):
    """The token against a real cluster: what resolves, what does not, and who owns it."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = self._tenant()
        self.artist = self._party(self.tenant, "Test Artist", "roster")
        self.curator = self._party(self.tenant, "Test Curator", "counterparty")
        self.recording = self._recording(self.tenant, "Losing Sleep")
        self._credit(self.tenant, self.artist, self.recording)
        self.asset = self._asset(self.tenant, self.recording, "audio/mpeg")
        self.message = self._message(self.tenant, self.curator)

    def tearDown(self) -> None:
        # One DELETE, because everything cascades from the tenant. Same reason
        # `test_outreach.py` builds its fixture as a tenant rather than a pile of rows.
        with self.conn.cursor() as cur:
            for tenant in getattr(self, "_tenants", []):
                cur.execute("DELETE FROM tenant WHERE id = %s", (tenant,))
        self.conn.close()

    # ------------------------------------------------------------------ fixtures

    def _tenant(self) -> str:
        tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (tenant, f"test-listen-{tenant[:8]}", "listen test"))
        self._tenants = getattr(self, "_tenants", [])
        self._tenants.append(tenant)
        return tenant

    def _party(self, tenant: str, name: str, party_class: str) -> str:
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party (id, tenant_id, slug, name, party_class) "
                "VALUES (%s, %s, %s, %s, %s)",
                (party_id, tenant, f"p-{party_id[:12]}", name, party_class))
        return party_id

    def _recording(self, tenant: str, title: str) -> str:
        rec = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO recording (id, tenant_id, slug, title, duration_ms) "
                "VALUES (%s, %s, %s, %s, %s)",
                (rec, tenant, f"r-{rec[:12]}", title, 174000))
        return rec

    def _credit(self, tenant: str, party_id: str, recording_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO party_credit (tenant_id, party_id, subject_kind, "
                "subject_id, role) VALUES (%s, %s, 'recording', %s, 'main_artist')",
                (tenant, party_id, recording_id))

    def _asset(self, tenant: str, recording_id: str, mime: str,
               state: str = "stored") -> str:
        asset = str(uuid.uuid4())
        digest = hashlib.sha256(asset.encode()).hexdigest()
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO recording_asset (id, tenant_id, recording_id, kind,
                                                bucket, object_key, content_hash,
                                                mime, state, uploaded_at)
                   VALUES (%s, %s, %s, 'master', 'test-bucket', %s, %s, %s, %s,
                           CASE WHEN %s = 'stored' THEN now() ELSE NULL END)""",
                # `recording_asset` carries CHECK ((state = 'stored') = (uploaded_at IS
                # NOT NULL)) — the timestamp and the state are two spellings of one fact,
                # which is the same constraint `listen_token_revoked_at_matches_state`
                # applies to revocation. The fixture has to honour it or every row here
                # is rejected before the test gets to say anything.
                (asset, tenant, recording_id, f"masters/{tenant}/master/{digest}",
                 digest, mime, state, state))
        return asset

    def _message(self, tenant: str, counterparty_id: str) -> str:
        """A message needs a thread, and a thread needs a campaign. Built by hand rather
        than through `outreach` so this file tests the token and not the pipeline."""
        campaign, thread = str(uuid.uuid4()), str(uuid.uuid4())
        message = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO campaign (id, tenant_id, party_id, name) "
                "VALUES (%s, %s, %s, %s)",
                (campaign, tenant, self.artist, "listen test campaign"))
            cur.execute(
                "INSERT INTO thread (id, tenant_id, campaign_id, counterparty_id, state) "
                "VALUES (%s, %s, %s, %s, 'sent')",
                (thread, tenant, campaign, counterparty_id))
            # `idempotency_key` is NOT NULL and is what stops one approved draft being
            # mailed twice; a per-row unique value is what the sender writes, so the
            # fixture supplies one rather than leaving the column to a default.
            cur.execute(
                "INSERT INTO message (id, tenant_id, thread_id, direction, channel, "
                "subject, body, idempotency_key) "
                "VALUES (%s, %s, %s, 'outbound', 'email', %s, %s, %s)",
                (message, tenant, thread, "subject", "body", f"test-{message}"))
        return message

    def _mint(self, **overrides) -> listen.Link:
        kwargs = dict(message_id=self.message, recording_asset_id=self.asset,
                      counterparty_id=self.curator,
                      base_url="https://spindle.mattrickslauer.com")
        kwargs.update(overrides)
        return listen.mint(self.conn, self.tenant, **kwargs)  # type: ignore[arg-type]

    # --------------------------------------------------------------------- tests

    def test_the_plaintext_token_is_never_stored(self):
        """The property the whole table shape exists for."""
        link = self._mint()
        with self.conn.cursor() as cur:
            cur.execute("SELECT token_hash FROM listen_token WHERE tenant_id = %s",
                        (self.tenant,))
            stored = cur.fetchone()["token_hash"]
        self.assertNotEqual(stored, link.token)
        self.assertEqual(stored, listen.hash_token(link.token))

    def test_a_fresh_token_resolves_to_its_asset_and_artist(self):
        link = self._mint()
        got = listen.resolve(self.conn, link.token)
        self.assertEqual(got.artist_name, "Test Artist")
        self.assertEqual(got.recording_title, "Losing Sleep")
        self.assertEqual(got.mime, "audio/mpeg")
        self.assertEqual(got.format_name, "MP3")

    def test_the_url_is_absolute_and_carries_the_token(self):
        link = self._mint()
        self.assertTrue(link.url.startswith("https://spindle.mattrickslauer.com/listen/"))
        self.assertTrue(link.url.endswith(link.token))

    def test_a_trailing_slash_on_the_base_url_does_not_double(self):
        link = self._mint(base_url="https://spindle.mattrickslauer.com/")
        self.assertNotIn("//listen", link.url.replace("https://", ""))

    def test_an_unknown_token_does_not_resolve(self):
        with self.assertRaises(listen.TokenInvalid):
            listen.resolve(self.conn, "not-a-real-token")

    def test_a_revoked_token_does_not_resolve(self):
        link = self._mint()
        listen.revoke_for_counterparty(self.conn, self.tenant, self.curator)
        with self.assertRaises(listen.TokenInvalid):
            listen.resolve(self.conn, link.token)

    def test_revocation_is_the_same_refusal_as_a_wrong_token(self):
        """One exception type for both, so the route cannot accidentally render a
        different page for a withdrawn link and confirm it was once real."""
        link = self._mint()
        listen.revoke_for_counterparty(self.conn, self.tenant, self.curator)
        revoked = self.assertRaises(listen.TokenInvalid)
        unknown = self.assertRaises(listen.TokenInvalid)
        with revoked:
            listen.resolve(self.conn, link.token)
        with unknown:
            listen.resolve(self.conn, "nonsense")
        self.assertIs(type(revoked.exception), type(unknown.exception))

    def test_an_expired_token_does_not_resolve(self):
        """Expiry is a comparison, not a state — migration 016's argument about a state
        nothing writes. Proven by asking as of a date past `expires_at`."""
        link = self._mint()
        later = datetime.now(timezone.utc) + timedelta(days=listen.LINK_TTL_DAYS + 1)
        with self.assertRaises(listen.TokenInvalid):
            listen.resolve(self.conn, link.token, now=later)
        # and still fine today, so the test is about the clock and not the row
        self.assertIsNotNone(listen.resolve(self.conn, link.token))

    def test_a_pending_asset_does_not_resolve(self):
        """A row whose bytes were never PUT is a promise nobody kept. Presigning a GET
        for it yields a link that 404s at S3, which is worse than not linking."""
        pending = self._asset(self.tenant, self.recording, "audio/wav", state="pending")
        link = self._mint(recording_asset_id=pending)
        with self.assertRaises(listen.TokenInvalid):
            listen.resolve(self.conn, link.token)

    def test_revocation_only_touches_the_named_counterparty(self):
        other = self._party(self.tenant, "Other Curator", "counterparty")
        mine = self._mint()
        theirs = self._mint(counterparty_id=other,
                            message_id=self._message(self.tenant, other))
        listen.revoke_for_counterparty(self.conn, self.tenant, self.curator)
        with self.assertRaises(listen.TokenInvalid):
            listen.resolve(self.conn, mine.token)
        self.assertIsNotNone(listen.resolve(self.conn, theirs.token))

    def test_a_token_cannot_reach_another_tenants_asset(self):
        """Migration 016's composite foreign key, at the point where getting it wrong
        mints a working public download URL for somebody else's master."""
        import psycopg

        other = self._tenant()
        other_recording = self._recording(other, "Not Ours")
        other_asset = self._asset(other, other_recording, "audio/wav")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self._mint(recording_asset_id=other_asset)

    def test_events_are_recorded_against_the_token(self):
        link = self._mint()
        got = listen.resolve(self.conn, link.token)
        listen.record_event(self.conn, got, "view", ip="203.0.113.7", user_agent="curl")
        listen.record_event(self.conn, got, "download", ip="203.0.113.7")
        with self.conn.cursor() as cur:
            cur.execute("SELECT kind, ip_hash FROM listen_event "
                        "WHERE listen_token_id = %s ORDER BY kind", (got.token_id,))
            rows = cur.fetchall()
        self.assertEqual([r["kind"] for r in rows], ["download", "view"])
        # The address is hashed, not stored — the table answers "same person or
        # forwarded", and nothing that needs the address itself.
        self.assertNotIn("203.0.113.7", {r["ip_hash"] for r in rows})
        self.assertEqual(len({r["ip_hash"] for r in rows}), 1)

    def test_for_message_returns_nothing_when_there_is_no_asset(self):
        link, label = listen.for_message(
            self.conn, self.tenant, message_id=self.message,
            recording_asset_id=None, counterparty_id=self.curator,
            base_url="https://spindle.mattrickslauer.com")
        self.assertIsNone(link)
        self.assertEqual(label, "")

    def test_for_message_names_the_format_it_actually_linked(self):
        link, label = listen.for_message(
            self.conn, self.tenant, message_id=self.message,
            recording_asset_id=self.asset, counterparty_id=self.curator,
            base_url="https://spindle.mattrickslauer.com")
        self.assertIsNotNone(link)
        self.assertEqual(label, "MP3")
        self.assertIn("MP3", listen.append_link("Hi.", link, format_label=label))

    def test_stored_master_prefers_a_stored_row_over_a_pending_one(self):
        self._asset(self.tenant, self.recording, "audio/wav", state="pending")
        found = listen.stored_master_for_recording(self.conn, self.tenant, self.recording)
        self.assertIsNotNone(found)
        self.assertEqual(str(found["id"]), self.asset)


class DownloadFilename(unittest.TestCase):

    def _resolved(self, mime: str, artist: str = "Hallow Youth",
                  title: str = "Losing Sleep") -> listen.Resolved:
        return listen.Resolved(token_id="t", tenant_id="x", artist_name=artist,
                               recording_title=title, mime=mime, bucket="b",
                               object_key="k", duration_ms=None)

    def test_the_filename_carries_artist_title_and_a_real_suffix(self):
        self.assertEqual(self._resolved("audio/mpeg").download_filename,
                         "Hallow Youth - Losing Sleep.mp3")
        self.assertEqual(self._resolved("audio/wav").download_filename,
                         "Hallow Youth - Losing Sleep.wav")

    def test_an_unknown_mime_yields_no_suffix_rather_than_bin(self):
        self.assertEqual(self._resolved("audio/weird").download_filename,
                         "Hallow Youth - Losing Sleep")


if __name__ == "__main__":
    unittest.main()
