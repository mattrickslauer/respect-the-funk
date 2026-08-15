"""The `/tracks` inspector's write surface — `repo`'s recording half.

`/tracks` was a read-only view with three inert buttons: the template renders a bare
string in an `actions` section as a `<button>` with no form around it, so "Analyse",
"Seed frontier" and "Edit" looked like controls and posted nowhere. The artist inspector
had already solved the same problem by absorbing the editor that lived at `/roster`.
These are the tests for the equivalent move on recordings.

What is worth testing here is not the CRUD. It is the two things the database will not
do on its own, both of which are silent when wrong:

  * **The polymorphic rows.** `presence` and `party_credit` address their subject as
    `(subject_kind, subject_id)` with no foreign key — the price of one table serving
    parties, recordings and releases — so `ON DELETE CASCADE` never fires for them. A
    delete that leaves them behind keeps the probe reconciler fetching a track that no
    longer exists and keeps an artist's track count claiming a catalogue it does not
    have. Neither surfaces as an error.
  * **The orphaned objects.** `recording_asset` *does* cascade, which is the problem:
    the rows vanish in the same statement as the recording and take with them the only
    record of which S3 objects they were holding. A master is 50–100MB and the bucket
    bills for it forever, so `delete_recording` collects the keys before the delete and
    returns them.

Cluster-gated in a tenant created and dropped per test, the pattern `test_repo.py` and
`test_integrity_constraints.py` use.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

from spindle import domain, repo

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class RecordingWrites(unittest.TestCase):

    # One tenant for the class, not one per test. This cluster is CockroachDB Basic and
    # scales to zero, so a round trip is tens of milliseconds and a cascading
    # `DELETE FROM tenant` is seconds — per-test teardown put this file over ten minutes
    # on its own. Tests stay independent by never sharing a title: `_recording` makes
    # every slug unique, which is the only cross-test coupling the shared tenant creates.
    @classmethod
    def setUpClass(cls) -> None:
        cls.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                   row_factory=dict_row)
        cls.tenant = str(uuid.uuid4())
        with cls.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (cls.tenant, f"test-rec-{cls.tenant[:8]}", "recording test"))
        cls.party = repo.create_party(cls.conn, cls.tenant, name="Test Act",
                                      type_="solo")

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (cls.tenant,))
        cls.conn.close()

    def _recording(self, title: str = "A Track", *, unique: bool = True, **kw) -> dict:
        """A recording whose slug cannot collide with another test's, unless the test
        is specifically about collisions and passes `unique=False`."""
        if unique:
            title = f"{title} {uuid.uuid4().hex[:8]}"
        return repo.create_recording(self.conn, self.tenant, title=title, **kw)

    def _count(self, sql: str, *params) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]

    # ------------------------------------------------------------------ create

    def test_create_derives_a_slug_and_round_trips(self) -> None:
        made = repo.create_recording(self.conn, self.tenant, title="Losing Sleep")
        self.assertEqual(made["slug"], "losing-sleep")
        again = repo.get_recording(self.conn, self.tenant, str(made["id"]))
        self.assertEqual(again["title"], "Losing Sleep")

    def test_a_duplicate_slug_raises_rather_than_silently_adopting_the_other_row(self):
        title = f"Same Name {uuid.uuid4().hex[:8]}"
        self._recording(title, unique=False)
        # Not `ON CONFLICT DO NOTHING`: swallowing this hands the operator a row they
        # did not create as though they had.
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._recording(title, unique=False)

    def test_a_duplicate_isrc_raises(self) -> None:
        self._recording("One", isrc="QZABC2500001", isrc_raw="QZ-ABC-25-00001")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self._recording("Two", isrc="QZABC2500001", isrc_raw="QZABC2500001")

    def test_the_blank_isrc_index_is_partial_so_several_may_have_none(self) -> None:
        # `recording_by_isrc` is `WHERE isrc != ''`. Without that, the second untitled
        # import would collide with the first, which is the common case on a fresh
        # roster — and every other test in this class relies on it too.
        before = self._count(
            "SELECT count(*) AS n FROM recording WHERE tenant_id = %s AND isrc = ''",
            self.tenant)
        self._recording("No Identifier One")
        self._recording("No Identifier Two")
        self.assertEqual(
            self._count(
                "SELECT count(*) AS n FROM recording WHERE tenant_id = %s AND isrc = ''",
                self.tenant),
            before + 2)

    # ------------------------------------------------------------------ update

    def test_update_reslugs_and_keeps_both_isrc_forms(self) -> None:
        made = self._recording("Old Title")
        # A different ISRC from the duplicate test above: one tenant serves the class,
        # so `recording_by_isrc` is shared state and two tests claiming one identifier
        # would make their order significant.
        repo.update_recording(self.conn, self.tenant, str(made["id"]),
                              title="New Title", isrc="QZABC2500002",
                              isrc_raw="QZ-ABC-25-00002", released_on=None,
                              status="paused")
        row = repo.get_recording(self.conn, self.tenant, str(made["id"]))
        self.assertEqual(row["slug"], repo.slugify(row["title"]))
        self.assertEqual(row["title"], "New Title")
        self.assertEqual(row["status"], "paused")
        # The canonical form joins; the raw form is what a source actually said, and
        # the provenance model needs to be able to show it.
        self.assertEqual(row["isrc"], "QZABC2500002")
        self.assertEqual(row["isrc_raw"], "QZ-ABC-25-00002")

    def test_update_of_another_tenants_recording_touches_nothing(self) -> None:
        made = self._recording("Mine")
        other = str(uuid.uuid4())
        self.assertIsNone(repo.update_recording(self.conn, other, str(made["id"]),
                                                title="Theirs"))
        self.assertEqual(
            repo.get_recording(self.conn, self.tenant, str(made["id"]))["title"],
            made["title"])

    # ----------------------------------------------------------------- credits

    def test_a_credit_is_asserted_and_idempotent(self) -> None:
        made = self._recording()
        rid = str(made["id"])
        self.assertTrue(repo.add_credit(self.conn, self.tenant, rid,
                                        party_id=str(self.party["id"]),
                                        role="main_artist"))
        # The same credit twice is not an error and not a second row.
        self.assertFalse(repo.add_credit(self.conn, self.tenant, rid,
                                         party_id=str(self.party["id"]),
                                         role="main_artist"))
        with self.conn.cursor() as cur:
            cur.execute("""SELECT provenance, split_percent FROM party_credit
                            WHERE tenant_id = %s AND subject_id = %s""",
                        (self.tenant, rid))
            row = cur.fetchone()
        # An operator typing a name is asserting, not inferring — `SCOPE-RESET §2a`
        # forbids the second wearing the first's clothes.
        self.assertEqual(row["provenance"], "asserted")
        # NULL, not 100 and not an even split: a fabricated number in a column people
        # will eventually trust with money.
        self.assertIsNone(row["split_percent"])

    def test_deleting_a_credit_is_scoped_by_its_subject(self) -> None:
        one, two = self._recording("One"), self._recording("Two")
        repo.add_credit(self.conn, self.tenant, str(one["id"]),
                        party_id=str(self.party["id"]), role="main_artist")
        with self.conn.cursor() as cur:
            cur.execute("""SELECT id FROM party_credit
                            WHERE tenant_id = %s AND subject_kind = 'recording'
                              AND subject_id = %s""",
                        (self.tenant, str(one["id"])))
            credit_id = str(cur.fetchone()["id"])
        # The id alone is enough to find the row, which is why it is not enough to
        # authorise deleting it.
        self.assertFalse(repo.delete_credit(self.conn, self.tenant, str(two["id"]),
                                            credit_id))
        self.assertTrue(repo.delete_credit(self.conn, self.tenant, str(one["id"]),
                                           credit_id))

    # ------------------------------------------------------------------ delete

    def _presence(self, recording_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO presence (tenant_id, subject_kind, subject_id, platform,
                                         mode, state)
                   VALUES (%s, 'recording', %s, 'spotify', 'owned', 'present')""",
                (self.tenant, recording_id))

    def _asset(self, recording_id: str, object_key: str, content_hash: str) -> None:
        with self.conn.cursor() as cur:
            # `bucket` is stored rather than derived from configuration, so changing the
            # configured bucket leaves existing objects addressable — see migration 016.
            cur.execute(
                # `uploaded_at` is not decoration: a CHECK ties it to `state = 'stored'`,
                # so a row cannot claim to hold bytes without saying when they landed.
                """INSERT INTO recording_asset (tenant_id, recording_id, kind, bucket,
                                                content_hash, object_key, mime, bytes,
                                                state, uploaded_at)
                   VALUES (%s, %s, 'master', 'test-bucket', %s, %s, 'audio/wav', 1024,
                           'stored', now())""",
                (self.tenant, recording_id, content_hash, object_key))

    def test_delete_removes_the_rows_the_database_cannot_cascade(self) -> None:
        made = self._recording()
        rid = str(made["id"])
        self._presence(rid)
        repo.add_credit(self.conn, self.tenant, rid,
                        party_id=str(self.party["id"]), role="main_artist")

        repo.delete_recording(self.conn, self.tenant, rid)

        # Both are polymorphic and neither cascades. Left behind, the presence row keeps
        # the probe reconciler fetching a track that is gone, and the credit keeps the
        # artist's track count wrong — silently, in both cases.
        self.assertEqual(self._count(
            "SELECT count(*) AS n FROM presence WHERE tenant_id = %s "
            "AND subject_kind = 'recording' AND subject_id = %s", self.tenant, rid), 0)
        self.assertEqual(self._count(
            "SELECT count(*) AS n FROM party_credit WHERE tenant_id = %s "
            "AND subject_kind = 'recording' AND subject_id = %s", self.tenant, rid), 0)

    def test_delete_reports_the_object_keys_the_caller_must_now_remove(self) -> None:
        made = self._recording()
        rid = str(made["id"])
        self._asset(rid, "masters/one.wav", "a" * 64)

        orphaned = repo.delete_recording(self.conn, self.tenant, rid)

        # `recording_asset` cascades, so the row is gone — and with it the only record
        # of which object it was holding. 50–100MB billed forever if this returns [].
        self.assertEqual(orphaned, ["masters/one.wav"])

    def test_an_object_another_row_still_points_at_is_not_reported_as_orphaned(self):
        keeper, doomed = self._recording("Keeper"), self._recording("Doomed")
        # `object_key` carries no unique index, so a backfill or an importer adopting
        # existing objects can legitimately point a second row at one file.
        self._asset(str(keeper["id"]), "masters/shared.wav", "b" * 64)
        self._asset(str(doomed["id"]), "masters/shared.wav", "c" * 64)

        self.assertEqual(repo.delete_recording(self.conn, self.tenant,
                                               str(doomed["id"])), [])

    def test_deleting_a_recording_that_is_not_there_reports_nothing(self) -> None:
        self.assertEqual(
            repo.delete_recording(self.conn, self.tenant, str(uuid.uuid4())), [])

    def test_delete_is_scoped_by_tenant(self) -> None:
        made = self._recording()
        self.assertEqual(repo.delete_recording(self.conn, str(uuid.uuid4()),
                                               str(made["id"])), [])
        self.assertIsNotNone(
            repo.get_recording(self.conn, self.tenant, str(made["id"])))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class InspectorStates(unittest.TestCase):
    """The three inspector states `EveryInspectorRenders` cannot reach.

    That test walks every builder's *first row* in its default state, which is the right
    sweep and misses exactly what this change added: the creation form, the delete
    confirmation, and a row with credits and measurements on it. `_inspector.html`
    dispatches on `s.kind`, so a section kind it has no branch for renders as silence and
    a malformed item tuple raises inside Jinja — neither is visible until a page loads.

    Rendered through the template directly rather than the app, for the reason
    `test_research_views.py` gives: `starlette.testclient` needs httpx and nothing else
    in this project does.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                   row_factory=dict_row)
        cls.tenant = str(uuid.uuid4())
        with cls.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (cls.tenant, f"test-insp2-{cls.tenant[:8]}", "inspector"))
        cls.party = repo.create_party(cls.conn, cls.tenant, name="Test Act",
                                      type_="solo")

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (cls.tenant,))
        cls.conn.close()

    def setUp(self) -> None:
        """A recording per test, a tenant per class. One INSERT is cheap; a cascading
        tenant delete is seconds. It has to be per test rather than per class because
        credits are what several of these assert on — one test adding a credit would
        otherwise decide what the next one sees."""
        self.rec = repo.create_recording(
            self.conn, self.tenant, title=f"A Track {uuid.uuid4().hex[:8]}")
        self.rid = str(self.rec["id"])

    def _render(self, row: dict) -> str:
        from spindle import routes

        return routes.templates.env.get_template("console/_inspector.html").render(
            sel=row, insp_kicker="track", insp_title=row.get("title", "—"),
            insp_new=None)

    def _row(self, **kw) -> dict:
        """This test's own row, found by id — the tenant holds every other test's too."""
        from spindle import research

        rows = research.tracks(self.conn, self.tenant, **kw).rows
        return next(r for r in rows if r["id"] == self.rid)

    def test_the_editor_posts_somewhere(self) -> None:
        """The whole point. These three controls used to be bare strings, which the
        template draws as buttons with no form around them — they looked like controls
        and posted nowhere."""
        html = self._render(self._row())
        self.assertIn(f'action="/tracks/{self.rid}"', html)
        self.assertIn("Delete track", html)
        self.assertIn('name="isrc"', html)
        # A release date typed into a text box arrives in whatever format the operator's
        # habits supply; the constraint belongs on the input.
        self.assertIn('type="date"', html)

    def test_the_new_track_form_renders(self) -> None:
        from spindle import research

        html = self._render({"id": "new", "title": "New track",
                             "insp": research.new_recording_sections()})
        self.assertIn('action="/tracks"', html)
        self.assertIn("Add track", html)
        # Status is not offered: a recording nobody has saved cannot be archived.
        self.assertNotIn('name="status"', html)

    def test_the_delete_confirmation_names_what_goes_with_it(self) -> None:
        html = self._render(self._row(editing_id=self.rid, confirm_delete=True))
        self.assertIn(f"/tracks/{self.rid}/delete", html)
        self.assertIn("cannot be undone", html)
        # Including the bucket. The rows cascade; the objects do not, and an operator
        # who is not told is one who finds out on the invoice.
        self.assertIn("bucket", html)

    def test_an_uncredited_track_says_analysis_is_impossible_rather_than_offering_it(self):
        html = self._render(self._row())
        self.assertNotIn(f"/tracks/{self.rid}/analyse", html,
                         "offered an analysis that queue_analysis would silently drop")
        self.assertIn("Nobody is credited", html)

    def test_a_credit_renders_with_its_own_delete_control(self) -> None:
        repo.add_credit(self.conn, self.tenant, self.rid,
                        party_id=str(self.party["id"]), role="main_artist")
        html = self._render(self._row())
        self.assertIn("Test Act", html)
        self.assertIn(f"/tracks/{self.rid}/credits/", html)
        # Now credited but still no master, so analysis is still not offered — and the
        # reason given must be the *other* one.
        self.assertIn("No master is stored", html)

    def test_the_credit_form_offers_the_roster(self) -> None:
        html = self._render(self._row())
        self.assertIn("Credit an artist", html)
        self.assertIn(f'value="{self.party["id"]}"', html)

    def test_a_validation_error_is_rendered_beside_the_field(self) -> None:
        html = self._render(self._row(editing_id=self.rid, error="that ISRC is wrong"))
        self.assertIn("that ISRC is wrong", html)

    def test_an_error_is_not_smeared_across_every_other_row(self) -> None:
        """`editing_id` is what keeps a rejected write attached to the row it came
        from. Without it the message renders on whichever row the operator clicks."""
        other_id = str(repo.create_recording(
            self.conn, self.tenant, title=f"B Track {uuid.uuid4().hex[:8]}")["id"])
        from spindle import research

        view = research.tracks(self.conn, self.tenant, editing_id=self.rid,
                               error="that ISRC is wrong")
        other = next(r for r in view.rows if r["id"] == other_id)
        self.assertNotIn("that ISRC is wrong", self._render(other))


class IsrcValidation(unittest.TestCase):
    """The route refuses a malformed ISRC rather than storing the blank.

    `canonical_isrc` returns "" for anything it will not vouch for. Storing that turns
    "I typed the ISRC wrong" into "this recording has no ISRC" — a different and
    entirely plausible state, which is exactly why it cannot be the fallback.
    """

    def test_a_malformed_isrc_canonicalises_to_blank(self) -> None:
        self.assertEqual(domain.canonical_isrc("NOT-AN-ISRC"), "")
        self.assertEqual(domain.canonical_isrc("QZABC250000"), "")   # one digit short

    def test_the_route_refuses_it_instead_of_filing_it_as_absent(self) -> None:
        from spindle.routes import _validated_recording

        with self.assertRaises(ValueError) as caught:
            _validated_recording("Title", "NOT-AN-ISRC", "")
        self.assertIn("not a well-formed ISRC", str(caught.exception))

    def test_a_blank_isrc_is_allowed_because_no_identifier_is_a_real_state(self) -> None:
        from spindle.routes import _validated_recording

        title, isrc, released = _validated_recording("Title", "  ", "")
        self.assertEqual((title, isrc, released), ("Title", "", None))

    def test_spellings_of_one_isrc_converge(self) -> None:
        from spindle.routes import _validated_recording

        for spelling in ("QZ-ABC-25-00001", "qzabc2500001", "QZ ABC 25 00001"):
            self.assertEqual(_validated_recording("T", spelling, "")[1], "QZABC2500001")

    def test_a_title_that_has_no_url_safe_form_is_refused(self) -> None:
        from spindle.routes import _validated_recording

        with self.assertRaises(ValueError):
            _validated_recording("", "", "")
        with self.assertRaises(ValueError):
            _validated_recording("日本語", "", "")   # slugify reduces this to nothing

    def test_a_release_date_must_be_a_date(self) -> None:
        from spindle.routes import _validated_recording

        with self.assertRaises(ValueError) as caught:
            _validated_recording("T", "", "last tuesday")
        self.assertIn("not a date", str(caught.exception))
        self.assertEqual(str(_validated_recording("T", "", "2025-03-01")[2]),
                         "2025-03-01")


if __name__ == "__main__":
    unittest.main()
