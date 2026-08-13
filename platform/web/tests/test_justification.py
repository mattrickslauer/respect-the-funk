"""Replaying the ranking that caused an email, and the three ways it can decline to.

This is the centrepiece claim of the whole submission: our agents contact real people, so
we can reconstruct the exact memory state that made them do it. `AS OF SYSTEM TIME` is
what makes it possible and `025_decision_provenance.sql` is what makes it *specific* —
without a stored instant there is no "then" to read.

The claim is only worth making if its failure modes are honest, so most of this file is
about those rather than about the happy path:

  * A thread nobody ranked must say so, and must not be confused with a thread whose
    ranking has aged out. "We never had a reason" and "we had one and the cluster no
    longer retains it" are different answers to somebody asking why they were contacted.
  * A replay past the GC window must raise. Falling back to the current ranking would
    return rows, render correctly, and be a fabrication — the single worst thing this
    feature could do, and the easiest to write by accident.
  * `as_of` reaches the SQL by interpolation, because CockroachDB will not take a
    placeholder for `AS OF SYSTEM TIME`. So the validation in front of it is load-bearing
    in the security sense, not the tidiness sense, and is asserted here rather than
    trusted to a regex somebody eyeballed.

The cluster-gated tests use the real database because an `EXPLAIN`-free fake cannot
produce a GC boundary, which is the interesting half. The validation tests need no
cluster and are never skipped — a guard that only runs when a database is configured is a
guard that does not run in CI.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

from rtf_platform import agents, outreach

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


class AsOfValidation(unittest.TestCase):
    """No database. `as_of` is interpolated into SQL, so this is the injection boundary."""

    #: Every one of these reached `AS OF SYSTEM TIME '<here>'` if the guard let it past.
    HOSTILE = (
        "-30m; DROP TABLE party",
        "1786644836824776765'; DROP TABLE party --",
        "' OR '1'='1",
        "now()",
        "-30m OR 1=1",
        "1786644836824776765 UNION SELECT 1",
        "-30m\n",
        "-30m ",
        "",  # handled separately below: empty means "live", not "invalid"
    )

    def test_hostile_values_are_refused(self) -> None:
        for bad in self.HOSTILE:
            if bad == "":
                continue
            with self.subTest(as_of=bad):
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    agents.shortlist_as_of(None, "t", "p", as_of=bad)  # type: ignore[arg-type]

    def test_the_two_legal_shapes_are_not_refused(self) -> None:
        """Reaching the connection means validation passed. `None` as the connection makes
        that the only thing this test can observe, which is exactly what it wants to
        observe — anything further needs a cluster and is covered below."""
        for good in ("-30m", "-1s", "-9999h", "1786644836824776765.0000000000",
                     "1786644836824776765"):
            with self.subTest(as_of=good):
                with self.assertRaises(AttributeError):
                    agents.shortlist_as_of(None, "t", "p", as_of=good)  # type: ignore[arg-type]

    def test_an_expired_history_is_not_a_validation_error(self) -> None:
        """`HistoryExpired` is deliberately not a `ValueError`. A console that renders it
        as one tells an operator their input was malformed when the truth is that they
        waited too long, and those want different words on screen."""
        self.assertFalse(issubclass(agents.HistoryExpired, ValueError))
        self.assertFalse(issubclass(outreach.NotJustified, ValueError))
        self.assertIsNot(agents.HistoryExpired, outreach.NotJustified)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class ReplayAgainstTheCluster(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-just-{self.tenant[:8]}", "justification test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _artist_and_campaign(self) -> tuple[str, str]:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state)
                   VALUES (%s, 'artist', 'the artist', 'roster', 'contactable')
                RETURNING id""", (self.tenant,))
            artist = str(cur.fetchone()["id"])
            cur.execute(
                """INSERT INTO campaign (tenant_id, party_id, name)
                   VALUES (%s, %s, 'test campaign') RETURNING id""",
                (self.tenant, artist))
            return artist, str(cur.fetchone()["id"])

    def _counterparty(self, slug: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state)
                   VALUES (%s, %s, %s, 'counterparty', 'contactable') RETURNING id""",
                (self.tenant, slug, slug))
            return str(cur.fetchone()["id"])

    def test_a_hand_opened_thread_says_nobody_ranked_it(self) -> None:
        """The NULL that `025` argues for, observed from the outside. A thread opened
        without a `decided` tuple must not acquire a plausible-looking justification."""
        _, campaign = self._artist_and_campaign()
        counterparty = self._counterparty("hand-picked")
        thread = outreach.open_thread(self.conn, self.tenant, campaign_id=campaign,
                                      counterparty_id=counterparty)
        self.assertIsNone(thread["decided_at_hlc"],
                          "a thread opened without a decision must record none")
        with self.assertRaises(outreach.NotJustified):
            outreach.justification(self.conn, self.tenant, str(thread["id"]))

    def test_a_decision_is_written_whole_or_refused(self) -> None:
        """`thread_decision_is_whole` from migration 025. A rank without the instant it
        was a rank at cannot be replayed; an instant without a rank cannot be checked."""
        _, campaign = self._artist_and_campaign()
        counterparty = self._counterparty("half-written")
        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO thread (tenant_id, campaign_id, counterparty_id,
                                           state, decided_rank)
                       VALUES (%s, %s, %s, 'discovered', 3)""",
                    (self.tenant, campaign, counterparty))

    def test_a_rank_must_be_a_position(self) -> None:
        _, campaign = self._artist_and_campaign()
        counterparty = self._counterparty("zero-rank")
        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO thread (tenant_id, campaign_id, counterparty_id, state,
                                           decided_at_hlc, decided_rank, decided_distance)
                       VALUES (%s, %s, %s, 'discovered',
                               cluster_logical_timestamp(), 0, 0.5)""",
                    (self.tenant, campaign, counterparty))

    def test_an_expired_instant_raises_rather_than_returning_today(self) -> None:
        """The failure this whole design exists to prevent.

        `-72h` is past `gc.ttlseconds`' reachable depth on this cluster (measured
        2026-08-13: -10h resolved, -18h did not), so the memory is genuinely gone. The
        only acceptable behaviour is to say so. Returning the current ranking would look
        identical to success from every angle except correctness.
        """
        artist = self._counterparty("artist-for-expiry")
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE party SET profile_embedding = %s::VECTOR(1024),
                                    embedding_model = 'openai:text-embedding-3-small'
                    WHERE tenant_id = %s AND id = %s""",
                ("[" + ",".join(["0.1"] * 1024) + "]", self.tenant, artist))
        with self.assertRaises(agents.HistoryExpired) as caught:
            agents.shortlist_as_of(self.conn, self.tenant, artist, as_of="-72h")
        self.assertIn("garbage-collection", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
