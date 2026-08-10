"""Executable enforcement of the constraint `docs/reference/COCKROACHDB-AI.md` documents:
CockroachDB accelerates a vector-index filter only on prefix columns, with equality or
`IN`. A `JOIN` attached to the vector-searched table, a range predicate, or a subquery
makes the planner abandon the index and full-scan.

That constraint was documented once and violated twice anyway. `agents.shortlist()` was
caught full-scanning on a `LEFT JOIN presence` and rewritten into a CTE. `agents.retrieve()`
repeated the exact same mistake with `JOIN party_document` and sat that way until an audit
ran `EXPLAIN` against it — the documentation alone did not stop either regression, and a
docstring saying "this plans as a vector search" is not evidence that it still does.

This file is the thing a docstring cannot be: it runs `EXPLAIN` against the live cluster
for every query in the codebase that uses `<=>`, and fails if the plan does not contain a
`vector search` node.

## Why this cannot quietly stop covering anything

A test that names a function by string and skips when the function is missing degrades
the moment somebody renames it — silently, which is worse than no test, because the
`CHANGELOG` still says the class of bug cannot come back. Two mechanisms close that:

  * Every cluster-gated test below calls the real function through its real, hard
    attribute reference (`agents.retrieve`, not `getattr(agents, "retrieve", None)`).
    A rename or deletion raises `AttributeError` from inside the test method, which
    unittest reports as an ERROR — a loud failure, not a skip.
  * `VectorQueryCensus`, below, does not name functions by string at all. It walks the
    AST of every module in `rtf_platform/` on every run, finds every string literal
    containing `<=>`, and records which function encloses it. That *discovered* set is
    diffed against `EXPECTED_VECTOR_QUERY_SITES`. If a covered query is deleted, rewritten
    to no longer be a vector search, or moved to a different function, the discovered set
    changes and the diff fails. If a *new* `<=>` query appears anywhere in the package
    without a matching entry here (and a matching `EXPLAIN` assertion below), the diff
    fails too — so a query cannot ship uncovered by omission. This test needs no database
    and is never skipped.

The two mechanisms cover different failures: the census catches a query changing shape or
appearing/disappearing in the source; the per-query `EXPLAIN` tests catch a query that
still uses `<=>` but has regressed to a full scan (the actual bug this file was written
for). Neither one alone would have caught `agents.retrieve()`'s bug and then guarded
against its return — the census does not run `EXPLAIN`, and a hand-picked list of `EXPLAIN`
tests does not notice a new, uncovered query.
"""

from __future__ import annotations

import ast
import os
import unittest
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

import psycopg
from psycopg.rows import dict_row

from rtf_platform import agents, embed, lessons, spend

HAVE_DB = bool(os.environ.get("DATABASE_URL"))

RTF_PLATFORM_DIR = Path(__file__).resolve().parent.parent / "rtf_platform"

#: Checked against the live cluster before writing any literal into a query below:
#: `SELECT DISTINCT embedding_model FROM party WHERE profile_embedding IS NOT NULL`
#: returns exactly this one string, written by `embed.OpenAIEmbedder.model` — the only
#: provider that has ever actually run against this cluster. An earlier audit got a
#: misleading plan by filtering on a model string that matched nothing real; every
#: literal below is this one, or a value this file itself just inserted and is
#: filtering back out, so every predicate matches real rows by construction.
REAL_EMBEDDING_MODEL = "openai:text-embedding-3-small"

#: Every (file, enclosing function) pair known to contain a `<=>` query today, one entry
#: per query the codebase actually runs against the vector indexes migrations 007, 009
#: and 010 built. `test_every_vector_query_site_is_accounted_for` recomputes the *actual*
#: set from the AST on every run and fails on any difference from this dict — added,
#: removed, renamed, or moved to a different function — so this list is never trusted on
#: its own. Changing it is only correct in the same change that adds or removes the
#: matching `EXPLAIN` assertion in `VectorSearchPlans` below.
EXPECTED_VECTOR_QUERY_SITES: set[tuple[str, str]] = {
    ("agents.py", "retrieve"),    # R2 over party_chunk — this file's Task 1.
    ("agents.py", "shortlist"),   # R1 over party — already fixed; the model to copy.
    ("lessons.py", "retrieve_for"),  # R2's general pass over lesson.
}


def _vector_query_sites() -> set[tuple[str, str]]:
    """Every (filename, enclosing function) with a string literal containing `<=>`,
    found by walking the AST of every module in `rtf_platform/`.

    AST rather than grep so a `<=>` inside a comment or a docstring quoting the operator
    (this file has several) cannot be mistaken for an executable query, and so the
    enclosing function is attributed structurally rather than by "whatever def appeared
    last" — the failure mode of a line-oriented scan once a query's SQL spans several
    lines, which every one of these does.
    """
    sites: set[tuple[str, str]] = set()
    for path in sorted(RTF_PLATFORM_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def enclosing_function(node: ast.AST) -> str:
            names: list[str] = []
            cur: ast.AST | None = node
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(cur.name)
                cur = parents.get(cur)
            return ".".join(reversed(names)) if names else "<module>"

        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "<=>" in node.value):
                sites.add((path.name, enclosing_function(node)))
    return sites


class VectorQueryCensus(unittest.TestCase):
    """Pure source inspection. Runs with or without `DATABASE_URL` — skipping the one
    check that catches a query silently falling out of coverage would defeat the entire
    point of this file, so it is not gated on the cluster at all.
    """

    def test_every_vector_query_site_is_accounted_for(self) -> None:
        found = _vector_query_sites()
        self.assertEqual(
            found, EXPECTED_VECTOR_QUERY_SITES,
            "the set of functions containing a `<=>` query has changed. If a query was "
            "added, add its (file, function) here AND a matching EXPLAIN assertion in "
            "VectorSearchPlans below. If one was removed, renamed or rewritten to no "
            "longer use `<=>`, remove it here AND remove or update the assertion that "
            "named it -- an assertion left behind for a query that no longer exists is "
            "exactly the silently-stopped-covering-anything failure this file exists to "
            f"prevent.\n  expected: {sorted(EXPECTED_VECTOR_QUERY_SITES)}\n"
            f"  found:    {sorted(found)}")


@dataclass
class _FakeEmbedder:
    """Stands in for `embed.OpenAIEmbedder`: same `key`/`model` a real call would use —
    both drawn from `spend.RATES` and matched to `REAL_EMBEDDING_MODEL` — but `.embed`
    never touches the network. What is under test here is the shape of the SQL
    `agents.retrieve` sends to the cluster, not OpenAI's API, and a cluster test that
    depended on a live API key would stop being a database test the day the key expired.
    """

    key: str = REAL_EMBEDDING_MODEL
    model: str = REAL_EMBEDDING_MODEL

    def embed(self, texts):
        return [embed.Vector([0.1] * embed.DIMENSIONS, self.model) for _ in texts]


def _open_gate() -> spend.Gate:
    """A gate that allows the one embedding call `agents.retrieve` makes, independent of
    whatever `RTF_PAID_ENABLED` happens to be in this shell. Spend policy has its own
    suite in `test_spend.py`; this file is testing query plans, not the gate, and gating
    it on the ambient environment would make this suite flaky for a reason that has
    nothing to do with what it is checking.
    """
    return spend.Gate(
        policy=spend.Policy(paid_enabled=True, daily_ceiling_usd=Decimal("999"),
                            per_call_ceiling_usd=Decimal("999"), dry_run=False),
        already_spent_usd=Decimal("0"), refused=[])


def _capture_vector_queries(fn, *args, **kwargs) -> tuple[Any, list[tuple[str, Any]]]:
    """Call `fn(*args, **kwargs)` for real, and return its result alongside every SQL
    statement it executed that contains `<=>`, verbatim with its parameters.

    This is what makes the `EXPLAIN` below provably the plan for the query the
    application actually runs, rather than a hand-copied duplicate that can drift out of
    sync with it the next time somebody edits `agents.py` and forgets this file. The spy
    wraps `psycopg.Cursor.execute` for the duration of the call and restores it
    unconditionally, so a raised exception from `fn` cannot leave every cursor in the
    process spied on afterward.
    """
    captured: list[tuple[str, Any]] = []
    original = psycopg.Cursor.execute

    def spy(self, query, params=None, **kw):
        text = query if isinstance(query, str) else str(query)
        if "<=>" in text:
            captured.append((text, params))
        return original(self, query, params, **kw)

    with mock.patch.object(psycopg.Cursor, "execute", spy):
        result = fn(*args, **kwargs)
    return result, captured


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class VectorSearchPlans(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test.

    An `EXPLAIN` plan is not something a fake connection can produce meaningfully — the
    property under test is what CockroachDB's real optimizer does with a real vector
    index, which is exactly the thing `docs/reference/COCKROACHDB-AI.md` warns is
    syntax-sensitive in a way no amount of reading the query can substitute for checking.
    """

    def setUp(self) -> None:
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-vecplan-{self.tenant[:8]}", "vector plan test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _vec(self, fill: float) -> str:
        return "[" + ",".join([str(fill)] * embed.DIMENSIONS) + "]"

    def _assert_plans_as_vector_search(self, captured: list[tuple[str, Any]], *,
                                       expected_queries: int = 1) -> None:
        """The one assertion every test below drives at. Fails loudly, with the full
        plan and the query, rather than a bare `False`, because the whole reason this
        suite exists is so the *next* person to see it fail can tell what regressed
        without re-deriving the investigation an audit already did once.
        """
        self.assertEqual(
            len(captured), expected_queries,
            f"expected {expected_queries} `<=>` statement(s) executed, found "
            f"{len(captured)} — either the function stopped being a vector query, or it "
            "now runs more of them than this test accounts for")
        for text, params in captured:
            with self.conn.cursor() as cur:
                cur.execute("EXPLAIN " + text, params)
                plan = "\n".join(row["info"] for row in cur.fetchall())
            self.assertIn(
                "vector search", plan,
                f"plan has no `vector search` node — the planner abandoned the index "
                f"and full-scanned instead:\n\n{plan}\n\nquery:\n{text}")

    # ------------------------------------------------------------ agents.shortlist

    def test_shortlist_plans_as_a_vector_search(self) -> None:
        """R1. Already fixed — this is the regression guard for `agents.shortlist`,
        proving the CTE shape it was rewritten into keeps planning correctly, not just
        that it once did.

        Two `<=>` statements land here, not one: `shortlist`'s own CTE over `party`, and
        `lessons.retrieve_for`'s general pass over `lesson`, which `shortlist` calls
        internally as R2. Both must plan as a vector search — `retrieve_for` has its own
        dedicated test too, but only over a `lesson` table it seeded itself; this is the
        one place that proves the *call from inside `shortlist`* still reaches it.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state,
                                      profile_embedding, embedding_model)
                   VALUES (%s, 'artist', 'the artist', 'roster', 'contactable',
                           %s::VECTOR(1024), %s)
                RETURNING id""",
                (self.tenant, self._vec(0.1), REAL_EMBEDDING_MODEL),
            )
            artist_id = str(cur.fetchone()["id"])
            for i in range(3):
                cur.execute(
                    """INSERT INTO party (tenant_id, slug, name, party_class,
                                          contact_state, profile_embedding, embedding_model)
                       VALUES (%s, %s, %s, 'counterparty', 'contactable',
                               %s::VECTOR(1024), %s)""",
                    (self.tenant, f"curator-{i}", f"curator {i}",
                     self._vec(0.1 * i), REAL_EMBEDDING_MODEL),
                )

        gate = _open_gate()
        _, captured = _capture_vector_queries(
            agents.shortlist, self.conn, self.tenant, artist_id, gate=gate)
        self._assert_plans_as_vector_search(captured, expected_queries=2)

    # ------------------------------------------------------------- agents.retrieve

    def test_retrieve_plans_as_a_vector_search(self) -> None:
        """R2 over evidence. Task 1's fix: `agents.retrieve` used to `JOIN party_document`
        onto the vector-searched `party_chunk` and full-scan. Run against the pre-fix
        shape, `_assert_plans_as_vector_search` fails with `spans: FULL SCAN` in the
        plan it prints — see the report for the verbatim output.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party_document (tenant_id, platform, url, title, body,
                                               content_hash)
                   VALUES (%s, 'internal', '', 'a document', 'body text', %s)
                RETURNING id""",
                (self.tenant, uuid.uuid4().hex),
            )
            document_id = str(cur.fetchone()["id"])
            for i in range(3):
                cur.execute(
                    """INSERT INTO party_chunk (tenant_id, document_id, ordinal, text,
                                                embedding, model)
                       VALUES (%s, %s, %s, %s, %s::VECTOR(1024), %s)""",
                    (self.tenant, document_id, i, f"chunk {i}",
                     self._vec(0.1 * i), REAL_EMBEDDING_MODEL),
                )

        gate = _open_gate()
        with mock.patch.object(embed, "load", return_value=_FakeEmbedder()):
            _, captured = _capture_vector_queries(
                agents.retrieve, self.conn, self.tenant, "what did we learn",
                gate=gate, limit=5)
        self._assert_plans_as_vector_search(captured)

    # ------------------------------------------------------- lessons.retrieve_for

    def test_retrieve_for_general_pass_plans_as_a_vector_search(self) -> None:
        """R2's ANN pass: `party_kind`/`channel`/`global` lessons by similarity. The
        "named" pass in the same function looks lessons up by id and never uses `<=>`,
        so it is outside this file's scope by construction — the census in
        `VectorQueryCensus` is what would catch it acquiring one.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text, valence,
                                       confidence, embedding, model)
                   VALUES (%s, 'global', '', 'a lesson worth remembering', 0.5, 0.9,
                           %s::VECTOR(1024), %s)""",
                (self.tenant, self._vec(0.2), REAL_EMBEDDING_MODEL),
            )

        _, captured = _capture_vector_queries(
            lessons.retrieve_for, self.conn, self.tenant,
            query_vector_literal=self._vec(0.1), model=REAL_EMBEDDING_MODEL,
            candidate_ids=[])
        self._assert_plans_as_vector_search(captured)


if __name__ == "__main__":
    unittest.main()
