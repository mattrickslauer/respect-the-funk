"""The public counterparty index: what it counts, and what it must never hand out.

`platforms.py` draws the world map on `/` and answers `/public/search` for readers with
no account. Two of its properties are worth a test rather than a docstring:

  * **Every counterparty lands in exactly one platform bucket.** 384 stations carry both
    an `fcc_facility_id` and a `radiobrowser_uuid`, and counted per-identifier they
    appear twice — the platform strip then sums to more than the counter above it, and
    the page's own arithmetic contradicts itself in public.
  * **A contact route's value never leaves the search endpoint.** The index is assembled
    from public record and showing what is in it costs nothing; the addresses are the
    asset. `platforms.search` says so in prose, and prose is not a guarantee, so
    `test_search_never_returns_a_contact_value` asserts it against a route planted for
    the purpose.

The cluster-backed tests build their own tenant and drop it, which cascades. The rest
need nothing but the module.
"""

from __future__ import annotations

import json
import os
import re
import unittest
import uuid
from pathlib import Path

from spindle import platforms

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

TEMPLATES = Path(__file__).resolve().parent.parent / "spindle" / "templates"


class TheTable(unittest.TestCase):
    """The platform catalogue, checked the way `plans.py`'s tiers are."""

    def test_keys_are_unique_and_match_the_index(self) -> None:
        keys = [p.key for p in platforms.PLATFORMS]
        self.assertEqual(len(keys), len(set(keys)), "two platforms share a key")
        self.assertEqual(platforms.KEYS, frozenset(keys))
        self.assertEqual(set(platforms.BY_KEY), set(keys))

    def test_other_is_not_offerable_as_a_filter(self) -> None:
        """`other` is a bucket for rows we could not classify, not a surface.

        Offering it on `/public/search?platform=` would invite a reader to browse our
        own classification failures, which is neither useful nor something to publish.
        """
        self.assertNotIn("other", platforms.KEYS)

    def test_the_classification_covers_every_offerable_key(self) -> None:
        """Every key a caller may filter on must be a branch the SQL can produce.

        A key in `PLATFORMS` with no branch in `PLATFORM_CASE` is a filter that is
        accepted, runs, and silently matches nothing — the worst of the three possible
        behaviours, because it looks like an answer. The not-yet-indexed platforms are
        exempt: they have no branch precisely because nothing is indexed on them, and
        `search` refusing them would be wrong too, so they are asserted to return zero
        rather than to be unreachable.
        """
        for p in platforms.PLATFORMS:
            if p.indexed:
                self.assertIn(f"'{p.key}'", platforms.PLATFORM_CASE,
                              f"{p.key} is indexed but has no branch in PLATFORM_CASE")

    def test_public_dimensions_carry_nothing_contact_shaped(self) -> None:
        """The allow-list is the whole of what a stranger may read.

        Checked by name as well as by membership: a dimension called `contact_email`
        added to `PUBLIC_DIMENSIONS` in a hurry would pass a membership test written
        against today's list, and this is the one place where the failure is a leak
        rather than a bug.
        """
        for dim in platforms.PUBLIC_DIMENSIONS:
            for banned in ("contact", "email", "route", "phone", "address", "token"):
                self.assertNotIn(banned, dim,
                                 f"{dim!r} looks contact-shaped and is publicly readable")

    def test_marks_all_exist_in_the_sprite(self) -> None:
        """Each platform's `mark` names a `<symbol>` the landing page actually defines.

        A missing one renders as an empty box next to a real count, which reads as a
        broken image rather than as a missing icon.
        """
        html = (TEMPLATES / "landing.html").read_text(encoding="utf-8")
        for p in platforms.PLATFORMS:
            self.assertIn(f'id="mk-{p.mark}"', html,
                          f"landing.html has no sprite for mark {p.mark!r}")


class TheRefusals(unittest.TestCase):

    def test_unknown_platform_is_refused_not_ignored(self) -> None:
        """An unknown filter raises rather than falling through to "no filter".

        Falling back would answer a question nobody asked with the entire index, which
        is the failure mode `no-fallbacks` exists to prevent: the caller gets a
        plausible page and never learns their filter was discarded.
        """
        with self.assertRaises(ValueError) as caught:
            platforms.search(None, "t", platform="myspace")  # type: ignore[arg-type]
        self.assertIn("myspace", str(caught.exception))
        self.assertIn("fm_radio", str(caught.exception))


@unittest.skipUnless(HAVE_DB, "needs DATABASE_URL — these test the database, not the code")
class AgainstTheCluster(unittest.TestCase):
    """A tenant built per test and dropped after. Everything else cascades from it."""

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-reach-{self.tenant[:8]}", "reach test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    # ------------------------------------------------------------------ helpers

    def _party(self, name: str, *, party_class: str = "counterparty") -> str:
        pid = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (id, tenant_id, slug, name, kind, party_class,
                                      contact_state, status)
                   VALUES (%s, %s, %s, %s, 'organisation', %s, 'contactable', 'active')""",
                (pid, self.tenant, f"p-{pid[:12]}", name, party_class))
        return pid

    def _ident(self, party_id: str, kind: str, value: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_identifier (tenant_id, party_id, kind, value,
                                                 provenance)
                   VALUES (%s, %s, %s, %s, 'measured')""",
                (self.tenant, party_id, kind, value))

    def _fact(self, party_id: str, dimension: str, value: str,
              provenance: str = "measured") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_fact (tenant_id, party_id, dimension, value_text,
                                           provenance, status, source, written_by)
                   VALUES (%s, %s, %s, %s, %s, 'live', 'test', 'test')""",
                (self.tenant, party_id, dimension, value, provenance))

    # -------------------------------------------------------------------- tests

    def test_a_party_with_both_identifiers_is_counted_once_as_fm(self) -> None:
        """The 384-station case: FM wins, and the totals do not double-count.

        FM is the stronger claim — a government record of a licensed transmitter, where
        the Radio Browser row is a community entry about a stream. A station that is
        both is a licensed station we also have a stream for.
        """
        both = self._party("Both Station")
        self._ident(both, "fcc_facility_id", "99001")
        self._ident(both, "radiobrowser_uuid", str(uuid.uuid4()))
        self._fact(both, "country_code", "US")

        out = platforms.totals(self.conn, self.tenant)
        counts = {p["key"]: p["n"] for p in out["platforms"]}
        self.assertEqual(out["total"], 1)
        self.assertEqual(counts["fm_radio"], 1)
        self.assertEqual(counts["internet_radio"], 0)
        self.assertEqual(sum(counts.values()), 1, "platform counts sum past the total")

    def test_total_equals_a_plain_count(self) -> None:
        """`totals()["total"]` must be checkable against `SELECT count(*)`.

        It only is if unclassifiable rows are counted rather than dropped — which is
        why `other` exists as a bucket at all.
        """
        for i in range(4):
            self._party(f"Station {i}")
        stray = self._party("No Identifiers At All")   # falls to `other`
        self.assertTrue(stray)

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) AS n FROM party
                    WHERE tenant_id = %s AND party_class = 'counterparty'""",
                (self.tenant,))
            expected = cur.fetchone()["n"]
        self.assertEqual(platforms.totals(self.conn, self.tenant)["total"], expected)

    def test_roster_parties_are_never_counted(self) -> None:
        """Shortlisting our own artists to ourselves is the failure 009 was built to
        prevent, and the public map must not advertise them as counterparties either."""
        self._party("Our Own Act", party_class="roster")
        self.assertEqual(platforms.totals(self.conn, self.tenant)["total"], 0)

    def test_measured_country_beats_asserted(self) -> None:
        """Both can be live at once, and the provenance rule decides which is read.

        `fact_one_live_per_dimension` is unique on (tenant, party, dimension,
        provenance), so a party known to both the FCC and Radio Browser legitimately
        carries two live `country_code` facts. `SCOPE-RESET §2a` says an inferred value
        may never overwrite a measured one; alphabetical ordering would pick `asserted`.
        """
        p = self._party("Disputed Country")
        self._ident(p, "fcc_facility_id", "99002")
        self._fact(p, "country_code", "US", provenance="measured")
        self._fact(p, "country_code", "CA", provenance="asserted")

        out = platforms.totals(self.conn, self.tenant)
        self.assertEqual([c["cc"] for c in out["countries"]], ["US"])

    def test_a_party_with_no_country_is_counted_but_not_placed(self) -> None:
        """`total` and `located` differ, and the gap is reported rather than hidden.

        Bucketing an unplaceable row into some default country would put a shape on the
        map for a counterparty we cannot place.
        """
        placed = self._party("Has A Country")
        self._ident(placed, "fcc_facility_id", "99003")
        self._fact(placed, "country_code", "US")
        self._party("Nowhere In Particular")

        out = platforms.totals(self.conn, self.tenant)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["located"], 1)
        self.assertEqual(sum(c["n"] for c in out["countries"]), 1)

    def test_search_never_returns_a_contact_value(self) -> None:
        """The one leak that matters, asserted against a planted route.

        The address is a string that appears nowhere else, so finding it anywhere in the
        serialised response — in any field, at any depth — fails. The count is asserted
        in the same test so a future change cannot pass this by simply dropping contact
        information from the payload altogether.
        """
        p = self._party("Contactable Station")
        self._ident(p, "fcc_facility_id", "99004")
        self._fact(p, "country_code", "US")
        secret = f"do-not-publish-{uuid.uuid4().hex}@example.invalid"
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO contact_route (tenant_id, party_id, channel, value,
                                              provenance, source, written_by)
                   VALUES (%s, %s, 'email', %s, 'measured', 'test', 'test')""",
                (self.tenant, p, secret))

        out = platforms.search(self.conn, self.tenant, country="US")
        blob = json.dumps(out)
        self.assertNotIn(secret, blob)
        self.assertNotIn("do-not-publish", blob)
        # No address of any shape, not merely not *this* one.
        self.assertEqual(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", blob), [])
        self.assertEqual(out["rows"][0]["contacts"], 1)

    def test_search_filters_by_country_and_platform(self) -> None:
        us = self._party("US Station")
        self._ident(us, "fcc_facility_id", "99005")
        self._fact(us, "country_code", "US")
        de = self._party("DE Stream")
        self._ident(de, "radiobrowser_uuid", str(uuid.uuid4()))
        self._fact(de, "country_code", "DE", provenance="asserted")

        self.assertEqual(
            [r["name"] for r in platforms.search(self.conn, self.tenant,
                                                 country="DE")["rows"]],
            ["DE Stream"])
        self.assertEqual(
            [r["name"] for r in platforms.search(self.conn, self.tenant,
                                                 platform="fm_radio")["rows"]],
            ["US Station"])
        self.assertEqual(
            platforms.search(self.conn, self.tenant,
                             country="DE", platform="fm_radio")["rows"], [])

    def test_search_orders_by_contact_routes_first(self) -> None:
        """Names in this index start with quotes and hashes, so alphabetical order puts
        the least useful rows first. The counterparties we have done work on lead."""
        quiet = self._party("aaa first alphabetically")
        self._ident(quiet, "fcc_facility_id", "99006")
        self._fact(quiet, "country_code", "US")
        worked = self._party("zzz last alphabetically")
        self._ident(worked, "fcc_facility_id", "99007")
        self._fact(worked, "country_code", "US")
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO contact_route (tenant_id, party_id, channel, value,
                                              provenance, source, written_by)
                   VALUES (%s, %s, 'email', %s, 'measured', 'test', 'test')""",
                (self.tenant, worked, "someone@example.invalid"))

        rows = platforms.search(self.conn, self.tenant, country="US")["rows"]
        self.assertEqual(rows[0]["name"], "zzz last alphabetically")
        self.assertEqual(rows[0]["contacts"], 1)

    def test_matched_is_the_full_count_not_the_page(self) -> None:
        """The window count is computed before `LIMIT`, so it reports every match."""
        for i in range(7):
            p = self._party(f"Station {i:02d}")
            self._ident(p, "fcc_facility_id", f"9910{i}")
            self._fact(p, "country_code", "US")

        out = platforms.search(self.conn, self.tenant, country="US", limit=3)
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["matched"], 7)

    def test_search_returns_only_public_dimensions(self) -> None:
        p = self._party("Fact Heavy")
        self._ident(p, "fcc_facility_id", "99200")
        self._fact(p, "country_code", "US")
        self._fact(p, "market", "Mankato, MN")
        self._fact(p, "station_kind", "college", provenance="inferred")
        self._fact(p, "bpm", "128")               # not on the allow-list

        row = platforms.search(self.conn, self.tenant, country="US")["rows"][0]
        self.assertIn("market", row["facts"])
        self.assertNotIn("bpm", row["facts"])
        # Provenance travels with the value so a client can render the three classes
        # distinguishably, which `SCOPE-RESET §2a` requires.
        self.assertEqual(row["facts"]["station_kind"]["provenance"], "inferred")
        self.assertEqual(row["facts"]["market"]["provenance"], "measured")

    def test_backfill_fcc_country_is_targeted_and_idempotent(self) -> None:
        """Writes US for FCC stations with no country, leaves everything else alone."""
        from spindle import ingest

        fcc = self._party("FCC Station")
        self._ident(fcc, "fcc_facility_id", "99300")
        stream = self._party("Just A Stream")
        self._ident(stream, "radiobrowser_uuid", str(uuid.uuid4()))
        already = self._party("Already Known")
        self._ident(already, "fcc_facility_id", "99301")
        self._fact(already, "country_code", "CA", provenance="asserted")

        self.assertEqual(ingest.backfill_fcc_country(self.conn, self.tenant), 1)
        # Re-running writes nothing: the frontier excludes rows that now have a country.
        self.assertEqual(ingest.backfill_fcc_country(self.conn, self.tenant), 0)

        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT party_id, value_text, provenance FROM party_fact
                    WHERE tenant_id = %s AND dimension = 'country_code'""",
                (self.tenant,))
            got = {str(r["party_id"]): (r["value_text"], r["provenance"])
                   for r in cur.fetchall()}
        self.assertEqual(got[fcc], ("US", "measured"))
        self.assertEqual(got[already], ("CA", "asserted"), "overwrote a known country")
        self.assertNotIn(stream, got, "gave a country to a party with no FCC licence")


if __name__ == "__main__":
    unittest.main()
