"""Every console view builder, executed against the live cluster.

`/tracks` returned a 500 for a class of bug no DB-free test can see: a `GROUP BY` that
does not list a column the statement's correlated subqueries reference. It is legal
Python, the SQL parses as a string, and the only thing that rejects it is the database
planner —

    psycopg.errors.GroupingError:
      subquery uses ungrouped column "tenant_id" from outer query

— which nothing ran until someone loaded the page. It arrived with the sweep that put
`tenant_id` into every subquery predicate (`eaed4fe`): before it, `tracks`'s three
scalar subqueries correlated on `t.id` alone, which *is* in the `GROUP BY`.
`test_tenant_scoping.py` is what demands those predicates and is right to; it parses
source and cannot know whether the result plans. This is the other half.

So the assertion is deliberately shallow — each builder runs, and no exception escapes.
Not row contents: the point is that the statement reaches the planner and survives it.
That makes an **empty tenant sufficient**, because a grouping error is raised at plan
time and does not care whether any row exists, and it keeps the test cheap enough to
cover all thirteen views rather than only the one that broke.

Cluster-gated in a tenant created and dropped per test, the pattern
`test_integrity_constraints.py`/`test_repo.py` use.
"""

from __future__ import annotations

import os
import unittest
import uuid

import psycopg
from psycopg.rows import dict_row

from spindle import research

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

#: Every builder taking `(conn, tenant_id)`. `today`, `approvals` and `inbox` return
#: cards rather than a `View`; they run the same queries and belong here just as much.
BUILDERS = (
    research.facts, research.queue, research.runs, research.budgets,
    research.tracks, research.imports, research.counterparties, research.artists,
    research.today, research.fleet, research.campaigns, research.threads,
    research.approvals, research.inbox,
)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class EveryViewPlans(unittest.TestCase):

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-views-{self.tenant[:8]}", "view test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def test_every_builder_executes(self) -> None:
        for build in BUILDERS:
            with self.subTest(view=build.__name__):
                build(self.conn, self.tenant)


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class EveryInspectorRenders(unittest.TestCase):
    """The other half of the gap above: a builder can return a perfectly good `View`
    whose sections the inspector template cannot draw.

    `EveryViewPlans` proves the SQL survives the planner. Nothing proved the *result*
    survives Jinja — and `_inspector.html` dispatches on `s.kind`, so a builder emitting
    a kind the template has no branch for produces a section that silently renders as
    nothing, while a malformed item tuple raises inside the template and the page 500s.
    Neither is visible until somebody loads the page.

    This renders the partial directly rather than going through the app, because
    `starlette.testclient` needs httpx and nothing else in this project does. The
    template is the thing under test; the routing around it is not.
    """

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-insp-{self.tenant[:8]}", "inspector test"))
            cur.execute("""INSERT INTO recording (tenant_id, slug, title)
                           VALUES (%s, 'a-track', 'A Track') RETURNING id""",
                        (self.tenant,))
            self.recording = str(cur.fetchone()["id"])

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()
        os.environ.pop("PLATFORM_MASTERS_BUCKET", None)

    def _render(self, row: dict) -> str:
        from spindle import routes

        return routes.templates.env.get_template("console/_inspector.html").render(
            sel=row, insp_kicker="track", insp_title=row.get("title", "—"),
            insp_new=None)

    def test_every_view_renders_its_first_row(self) -> None:
        """One pass over every builder that returns a `View`, drawing the inspector for
        a real row. An empty tenant has no rows for most of them, which is why the
        recording above exists — `tracks` is the view this change touched."""
        for build in BUILDERS:
            view = build(self.conn, self.tenant)
            rows = getattr(view, "rows", None)
            if not rows:
                continue
            with self.subTest(view=build.__name__):
                self.assertIn("insp", self._render(rows[0]))

    def test_the_upload_control_renders_when_a_bucket_is_configured(self) -> None:
        """The masters uploader is the only JavaScript in this console and the only
        section kind that is not a form post. If `_inspector.html` ever loses its
        `upload` branch, the section vanishes from the page silently — an upload
        control that is simply absent reads as a feature nobody built."""
        os.environ["PLATFORM_MASTERS_BUCKET"] = "rtf-masters-test"
        view = research.tracks(self.conn, self.tenant)
        html = self._render(view.rows[0])

        self.assertIn("upform", html)
        self.assertIn("crypto.subtle", html, "the hashing step is gone")
        self.assertIn(f"/tracks/{self.recording}/masters", html)
        # The signed Content-Type must be the one the PUT sends or S3 rejects it.
        self.assertIn("'Content-Type': file.type", html)

    def test_no_upload_control_is_offered_without_a_bucket(self) -> None:
        """`NO FALLBACKS` at the UI layer. There is no local storage adapter to fall
        back to, so an unconfigured console says so rather than rendering a control
        whose submit would raise after the operator picked a 90MB file."""
        os.environ.pop("PLATFORM_MASTERS_BUCKET", None)
        view = research.tracks(self.conn, self.tenant)
        html = self._render(view.rows[0])

        self.assertNotIn("upform", html)
        self.assertIn("PLATFORM_MASTERS_BUCKET", html,
                      "the console did not say why uploads are unavailable")

    def test_a_track_with_no_master_says_what_that_costs(self) -> None:
        view = research.tracks(self.conn, self.tenant)
        self.assertEqual(view.rows[0]["audio"], "none")
        self.assertIn("No master yet", self._render(view.rows[0]))


if __name__ == "__main__":
    unittest.main()
