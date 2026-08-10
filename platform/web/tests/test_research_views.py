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

from rtf_platform import research

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


if __name__ == "__main__":
    unittest.main()
