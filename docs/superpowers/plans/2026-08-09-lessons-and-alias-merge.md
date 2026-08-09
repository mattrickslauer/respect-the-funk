# Lessons and the Alias Merge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform a memory that compounds — a `lesson` table the shortlist reads — and a deduplication agent whose merges are reversible.

**Architecture:** Steps 1 and 2 of `docs/superpowers/specs/2026-08-09-outreach-loop-design.md`. Two migrations, one new module (`lessons.py`), one new agent (`dedup_party`), and two repo functions. R1's vector shortlist gains a second pass that adjusts candidate scores by retrieved lessons and records which lesson caused each adjustment. Deduplication proposes merges into the existing `suggestion` queue and never merges by itself; an accepted merge flags one row `party_class = 'alias'` and rewrites nothing.

**Tech Stack:** Python 3.13 (Lambda) / 3.14 (dev), `psycopg` 3, CockroachDB v26.2.5, `unittest` run under `pytest`, no new dependencies.

## Global Constraints

Every task's requirements implicitly include these. All are existing house rules, quoted from the sources that set them.

- **An embedding that cannot name its model is refused by a `CHECK`, and `model` is an equality predicate in every retrieval.** Set by migration `007`. Two models in one index is a cosine distance that is a well-formed float and pure noise.
- **Every vector query's predicates are equality or `IN` on prefix columns**, in prefix order. Range comparisons and subqueries are not accelerated — `docs/reference/COCKROACHDB-AI.md`.
- **Revisions are appended via `supersedes_id`, never `UPDATE`d in place.** `SCOPE-RESET §2a` rule 1.
- **A guess is a suggestion, never a fact.** Anything a model inferred lands in `suggestion` as `pending` for a human. `agents.map_source`'s docstring states this as a rule the agents do not get to break.
- **Tuning constants are named module-level constants with the reasoning in a comment, never literals inline.** Spec §10 item 1.
- **No test touches the network.** Cluster tests skip when `DATABASE_URL` is unset, via `@unittest.skipUnless(HAVE_DB, …)`, and clean up through a tenant dropped in `tearDown` — the pattern in `tests/test_fleet.py`.
- **Run tests with** `cd platform/web && .venv/bin/python -m pytest tests -q`. Baseline in this worktree: **77 passed, 16 skipped.** The main tree reports 87/33 — it has the outreach tests this branch does not. Do not "fix" the difference.
- **Migrations are applied with** `python platform/schema/apply.py <file>.sql`. Neither migration here is destructive, so neither is added to `apply.DESTRUCTIVE`.

## Read this before Task 1 — the branch and the cluster disagree, on purpose

**Revised 2026-08-09, after a parallel session shipped outreach.** This plan was written when `thread` did not exist. It does now, and the revision changes four things. Work through this section before starting; it is the difference between the plan applying and the plan corrupting a live cluster.

**1. This plan runs in a git worktree branched from `96a5c29`.** That commit does **not** contain the parallel session's outreach work, which is uncommitted in the main working tree. So inside this worktree:

- `platform/schema/` holds `001`–`009` and **no `010`**. That gap is expected. `010_outreach.sql` exists only in the other tree.
- `rtf_platform/outreach.py` does not exist. Nothing in this plan imports it. Do not create it, and do not "restore" it.
- **The test baseline is 77 passed, 16 skipped** — the numbers in this plan are correct for this branch. The main tree reports 87/33 because it has tests this branch does not.

**2. The cluster is shared, and it is ahead of this branch.** `campaign`, `thread`, `message` and `outbox` are live on `defaultdb` right now, created by a migration that is in no commit. Task 5a therefore `ALTER`s a table whose `CREATE` this branch cannot see. That is correct and deliberate — migrations run against the cluster, not against the branch — but it means:

> **If `010_outreach.sql` is never committed, `012_party_alias.sql` references a table with no creating migration in history.** Task 5a's comment says so. Do not work around it by recreating the table.

**3. Migrations are numbered `011` and `012`, skipping `010`.** Not because this branch has an `010`, but because the other tree does, and two files claiming `010` is a merge conflict in the one place a merge conflict is most dangerous. Applied order after both branches merge is `010` → `011` → `012`, which is also the dependency order.

**4. The obligation this plan used to defer is now in scope.** The §3c index shipped as `one_open_thread_per_counterparty` on `thread (tenant_id, counterparty_id)`. It is **live and, once aliases exist, unmet**: an alias and the party it aliases are two `counterparty_id` values, so both could hold an open thread — the exact double-contact the index exists to prevent, arriving by the back door as spec §4a-i predicted. **Task 5a closes it, and must land before Task 7 puts a merge suggestion in front of anybody.**

## File structure

| File | Responsibility |
|---|---|
| `platform/schema/011_lesson.sql` | **create** — the `lesson` table, its `CHECK`s, and `lesson_semantic` |
| `platform/schema/012_party_alias.sql` | **create** — `party.alias_of`, `party_class = 'alias'` |
| `platform/web/rtf_platform/lessons.py` | **create** — write, retrieve, resolve a supersession chain, and the pure rerank |
| `platform/web/rtf_platform/agents.py` | **modify** — `shortlist` gains the rerank; `dedup_party` is added; `REGISTRY` gains it |
| `platform/web/rtf_platform/repo.py` | **modify** — `merge_party`, `unmerge_party`, `resolve_canonical`; `delete_party` clears lessons |
| `platform/web/tests/test_lessons.py` | **create** — the pure rerank and the chain resolution, offline |
| `platform/web/tests/test_merge.py` | **create** — merge round-trip and alias invisibility, against the cluster |
| `platform/web/tests/test_dedup.py` | **create** — the agent proposes and never merges |
| `platform/README.md` | **modify** — correct three false claims, record the new state |

`lessons.py` is a new module rather than more of `agents.py` because that file is already 680 lines and holds five agents; lesson persistence and scoring are neither an agent nor coordination, and the rerank has to be a pure function to be testable without a cluster.

---

### Task 1: Migration 011 — the `lesson` table

**Files:**
- Create: `platform/schema/011_lesson.sql`
- Modify: `docs/superpowers/specs/2026-08-09-outreach-loop-design.md` (§3a DDL — add `valence`)

**Interfaces:**
- Consumes: nothing.
- Produces: table `lesson` with columns `id, tenant_id, scope_kind, scope_id, text, evidence_json, confidence, valence, embedding, model, model_version, supersedes_id, hit_count, created_at`; vector index `lesson_semantic`; plain index `lesson_by_scope`.

**Why `valence` is in the migration but not in the spec's §3a DDL.** The spec's rerank (§6 step 2) says a ghosting lesson sinks a candidate and a landed pitch lifts one, but every column in §3a is unsigned — `confidence` says how sure we are, not which direction. Reranking arithmetically needs a signed term, and deriving the sign from `text` at query time would mean a model call inside the system's hottest query, which §6 forbids. So the sign is stored. This is a refinement of the spec, not a departure from it, and Step 1 amends the spec so the two agree.

- [ ] **Step 1: Amend the spec's §3a DDL so it matches what gets built**

In `docs/superpowers/specs/2026-08-09-outreach-loop-design.md`, inside the `CREATE TABLE lesson` block, add after the `confidence` line:

```sql
    valence       FLOAT NOT NULL DEFAULT 0,   -- -1 discouraging … +1 encouraging
```

And add this paragraph immediately after the three "decisions" paragraphs in §3a:

```markdown
**`valence` is stored, not derived.** `confidence` says how sure we are; it does not say
which way the lesson points. §6's rerank needs a signed term, and inferring the sign from
`text` at query time would put a model call inside the hottest query in the system, which
§6 rules out. A lesson writer states the direction once, at write time, where it is cheap
and auditable.
```

- [ ] **Step 2: Write the migration**

Create `platform/schema/011_lesson.sql`:

```sql
-- 011 — the lesson: the only table in this schema whose job is to make the next run
-- better than this one.
--
-- `SCOPE-RESET §1` justifies the party being the root of the spine on one claim: that
-- release n+1 is cheaper and lands harder than release n, because something accumulates
-- between them. Up to this migration, nothing did. Facts accumulate about a party, and
-- documents accumulate about a party, but nothing recorded what *worked* — so every
-- shortlist was computed as though the label had never pitched anybody.
--
-- This is that table, and `PLATFORM-SPEC §6` calls the query over it R2.
--
--
-- ## Scope, and why `scope_id` is a STRING with no foreign key
--
-- A lesson is about one curator ("replies within a day, never to a template"), or about
-- a kind of counterparty ("mood editors want the playlist named"), or about a channel,
-- or about everything. Those are four different things for `scope_id` to hold, and only
-- the first is a UUID.
--
-- This is the same polymorphism `presence` and `party_credit` already carry, and it has
-- the same price: `ON DELETE CASCADE` cannot fire, so `repo.delete_party` deletes
-- lessons itself. That is now three tables a party deleter must clear by hand, and the
-- periodic orphan sweep `platform/README.md` says is worth writing covers all three.
--
--
-- ## `supersedes_id` rather than UPDATE
--
-- `SCOPE-RESET §2a` rule 1. A lesson that turns out to be wrong is superseded and stays
-- readable, because why we used to believe something is itself evidence — and because an
-- inferred value may never overwrite anything in place.
--
--
-- ## `hit_count` is not telemetry
--
-- It is how an operator sees which lessons earn their place. A lesson retrieved in fifty
-- drafts and a lesson retrieved never should not look the same in the console, and the
-- second is a candidate for supersession rather than a permanent thumb on the scale.


CREATE TABLE IF NOT EXISTS lesson (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,

    scope_kind    STRING NOT NULL,
    scope_id      STRING NOT NULL DEFAULT '',

    text          STRING NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}',
    confidence    FLOAT NOT NULL DEFAULT 0.5,
    valence       FLOAT NOT NULL DEFAULT 0,

    embedding     VECTOR(1024),
    model         STRING NOT NULL DEFAULT '',
    model_version STRING NOT NULL DEFAULT '',

    supersedes_id UUID REFERENCES lesson(id) ON DELETE SET NULL,
    hit_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT lesson_scope_known
        CHECK (scope_kind IN ('party', 'party_kind', 'channel', 'global')),

    -- The rule migration 007 set for chunks and 009 set for parties: an embedding that
    -- cannot name its model can never satisfy the equality predicate a retrieval
    -- carries, so it is unsearchable and must not be written.
    CONSTRAINT lesson_embedding_has_a_model
        CHECK (embedding IS NULL OR model != ''),

    CONSTRAINT lesson_confidence_is_a_probability
        CHECK (confidence >= 0 AND confidence <= 1),

    -- Signed, and bounded, because the rerank multiplies by it. An unbounded valence is
    -- a single lesson that can dominate every shortlist forever.
    CONSTRAINT lesson_valence_is_bounded
        CHECK (valence >= -1 AND valence <= 1)
);


-- R2 — what have we learned that applies here? Every prefix column is equality or IN:
--
--     WHERE tenant_id = $1 AND model = $2 AND scope_kind IN ('channel','party_kind','global')
--     ORDER BY embedding <=> $3 LIMIT 10
--
CREATE VECTOR INDEX IF NOT EXISTS lesson_semantic
    ON lesson (tenant_id, model, scope_kind, embedding vector_cosine_ops);


-- Lessons about one named party are looked up by id, not by similarity — we already
-- know who we mean. The vector index cannot serve that and should not be asked to.
CREATE INDEX IF NOT EXISTS lesson_by_scope
    ON lesson (tenant_id, scope_kind, scope_id, created_at DESC)
    STORING (text, confidence, valence, supersedes_id);
```

- [ ] **Step 3: Apply it against the cluster**

Run: `cd "$(git rev-parse --show-toplevel)" && python platform/schema/apply.py 011_lesson.sql`

Expected: each statement reported applied, no error. If `CREATE VECTOR INDEX` errors with a feature-disabled message, stop — `feature.vector_index.enabled` was verified `t` on this cluster and a change to that is a go/no-go, not a workaround.

- [ ] **Step 4: Verify the constraints are real, not just written**

Run:

```bash
psql "$DATABASE_URL" -c "INSERT INTO lesson (tenant_id, scope_kind, text, embedding) \
  SELECT id, 'global', 'no model', '$(python3 -c "print('[' + ','.join(['0.1']*1024) + ']')")'::VECTOR(1024) FROM tenant LIMIT 1"

`ARRAY_FILL()` does **not** exist on this CockroachDB build — verified 2026-08-09, it
errors with `unknown function: array_fill()`. Generate the literal instead, as above.
```

Expected: FAILS with `lesson_embedding_has_a_model`. If it succeeds, the `CHECK` is not doing its job and the index will fill with unsearchable rows.

Then run the same insert with `'global', 'no model'` and **no** embedding column. Expected: succeeds. Delete it afterwards:

```bash
psql "$DATABASE_URL" -c "DELETE FROM lesson WHERE text = 'no model'"
```

- [ ] **Step 5: Commit**

```bash
git add platform/schema/011_lesson.sql docs/superpowers/specs/2026-08-09-outreach-loop-design.md
git commit -m "platform: the lesson, and a valence so the rerank can have a sign"
```

---

### Task 2: `lessons.py` — the pure rerank

Written before persistence, because it is the part with actual logic and it needs no database at all.

**Files:**
- Create: `platform/web/rtf_platform/lessons.py`
- Test: `platform/web/tests/test_lessons.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `LESSON_WEIGHT: float`
  - `applies(lesson: dict[str, Any], candidate: dict[str, Any]) -> bool`
  - `rerank(candidates: list[dict[str, Any]], lessons: list[dict[str, Any]], *, weight: float = LESSON_WEIGHT) -> list[dict[str, Any]]` — returns new dicts, each carrying every original key plus `adjusted: float` and `applied: list[dict]` where each entry is `{"lesson_id": str, "text": str, "shift": float}`. Sorted ascending by `adjusted`. Does not mutate its inputs.

- [ ] **Step 1: Write the failing test**

Create `platform/web/tests/test_lessons.py`:

```python
"""The rerank, offline.

This is the arithmetic that turns "who resembles this artist" into "who resembles this
artist and has not ignored us twice", and it is a pure function precisely so it can be
tested without a cluster, a model, or a network. `PLATFORM-SPEC §6`'s R1 is the search;
this is the part that makes the search remember.

The property under test throughout: **an adjustment must be traceable to the lesson that
caused it.** A rerank that produces a better order and cannot say why is not usable by
the console's inspector, and the inspector is the reason the product claims every object
has a why.
"""

from __future__ import annotations

import unittest

from rtf_platform import lessons


def candidate(cid: str, distance: float) -> dict:
    return {"id": cid, "name": f"party {cid}", "distance": distance}


def lesson(scope_kind: str, scope_id: str, valence: float,
           confidence: float = 1.0, lid: str = "l1", text: str = "a lesson") -> dict:
    return {"id": lid, "scope_kind": scope_kind, "scope_id": scope_id,
            "valence": valence, "confidence": confidence, "text": text}


class Applies(unittest.TestCase):

    def test_a_party_lesson_applies_only_to_that_party(self):
        les = lesson("party", "a", -1.0)
        self.assertTrue(lessons.applies(les, candidate("a", 0.5)))
        self.assertFalse(lessons.applies(les, candidate("b", 0.5)))

    def test_a_general_lesson_applies_to_everyone_in_the_run(self):
        # Retrieval already scoped these by channel and kind; if it came back, it applies.
        for kind in ("party_kind", "channel", "global"):
            les = lesson(kind, "curator", 0.5)
            self.assertTrue(lessons.applies(les, candidate("anyone", 0.5)),
                            f"{kind} lesson should apply to every candidate")


class Rerank(unittest.TestCase):

    def test_no_lessons_preserves_distance_order(self):
        cands = [candidate("far", 0.9), candidate("near", 0.1)]
        out = lessons.rerank(cands, [])
        self.assertEqual([c["id"] for c in out], ["near", "far"])
        self.assertEqual(out[0]["adjusted"], 0.1)

    def test_a_negative_lesson_sinks_the_candidate_it_names(self):
        cands = [candidate("ghoster", 0.10), candidate("quiet", 0.12)]
        out = lessons.rerank(cands, [lesson("party", "ghoster", -1.0)],
                             weight=0.05)
        self.assertEqual([c["id"] for c in out], ["quiet", "ghoster"],
                         "a curator who ignored us twice must not outrank a fresh one")

    def test_a_positive_lesson_lifts_the_candidate_it_names(self):
        cands = [candidate("replied", 0.12), candidate("unknown", 0.10)]
        out = lessons.rerank(cands, [lesson("party", "replied", 1.0)], weight=0.05)
        self.assertEqual([c["id"] for c in out], ["replied", "unknown"])

    def test_confidence_scales_the_shift(self):
        cands = [candidate("a", 0.5)]
        sure = lessons.rerank(cands, [lesson("party", "a", -1.0, confidence=1.0)],
                              weight=0.05)[0]["adjusted"]
        unsure = lessons.rerank(cands, [lesson("party", "a", -1.0, confidence=0.25)],
                                weight=0.05)[0]["adjusted"]
        self.assertGreater(sure, unsure,
                           "a lesson we are sure of must move the needle further")

    def test_a_general_lesson_shifts_everyone_and_changes_no_order(self):
        cands = [candidate("a", 0.10), candidate("b", 0.20)]
        out = lessons.rerank(cands, [lesson("channel", "email", 1.0)], weight=0.05)
        self.assertEqual([c["id"] for c in out], ["a", "b"])

    def test_every_adjustment_names_the_lesson_that_caused_it(self):
        les = lesson("party", "a", -1.0, lid="L-42", text="ghosted twice")
        out = lessons.rerank([candidate("a", 0.5)], [les], weight=0.05)
        applied = out[0]["applied"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["lesson_id"], "L-42")
        self.assertEqual(applied[0]["text"], "ghosted twice")
        self.assertAlmostEqual(applied[0]["shift"], 0.05)

    def test_an_unaffected_candidate_reports_no_lessons(self):
        out = lessons.rerank([candidate("a", 0.5), candidate("b", 0.5)],
                             [lesson("party", "a", -1.0)])
        by_id = {c["id"]: c for c in out}
        self.assertEqual(by_id["b"]["applied"], [])

    def test_inputs_are_not_mutated(self):
        cands = [candidate("a", 0.5)]
        lessons.rerank(cands, [lesson("party", "a", -1.0)])
        self.assertNotIn("adjusted", cands[0],
                         "rerank must not write into the caller's rows")

    def test_many_lessons_cannot_drive_a_score_below_zero(self):
        # Cosine distance is non-negative. A score that goes negative is not a distance
        # any more, and anything reading it as one is now wrong.
        piles = [lesson("party", "a", 1.0, lid=f"l{i}") for i in range(50)]
        out = lessons.rerank([candidate("a", 0.1)], piles, weight=0.05)
        self.assertGreaterEqual(out[0]["adjusted"], 0.0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests/test_lessons.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'rtf_platform.lessons'`

- [ ] **Step 3: Write the implementation**

Create `platform/web/rtf_platform/lessons.py`:

```python
"""R2 — what have we learned that applies here — and the rerank that spends it.

`PLATFORM-SPEC §6` names this the query that makes campaign n+1 cheaper than campaign n,
which is `SCOPE-RESET §1`'s entire justification for the party being the root of the
spine. Until migration 010 there was no table to ask, so every shortlist was computed as
though the label had never pitched anybody.

## Why the scoring is arithmetic and not a model call

The obvious version asks a model to rank the candidates given the lessons. It is worse in
three separate ways: it is unexplainable, so the console's inspector has nothing to
render; it is unmeasurable, so §6a's before-and-after cannot be computed; and it puts a
paid, rate-limited, occasionally-wrong call in the middle of the hottest query in the
system.

So the shift is a documented formula over stored numbers. A lesson has a `valence` (which
way it points) and a `confidence` (how sure we are), and the product of the two, times a
weight, moves the candidate's distance. Every shift is returned alongside the lesson id
that produced it.

## Why the shift is small

`LESSON_WEIGHT` is deliberately a fraction of the distances involved. Cosine distances
between genuinely similar and genuinely dissimilar parties differ by tenths; a weight
that could reorder the whole list would mean lessons had replaced the search rather than
informed it, and the first badly-written lesson would outrank musical fit forever.
"""

from __future__ import annotations

from typing import Any

#: How far one fully-confident, fully-signed lesson moves a candidate's cosine distance.
#:
#: 0.05 against distances that typically run 0.1–0.6: enough to reorder neighbours that
#: were close anyway, not enough to lift a poor match over a good one. This is a starting
#: value chosen before there were lessons to tune it against — spec §10 item 1 — and it
#: is a named constant so that changing it is a visible change rather than a silent one.
LESSON_WEIGHT = 0.05

#: Scopes that apply to every candidate a retrieval returned. A lesson at one of these
#: scopes was already filtered by channel and kind when it was fetched; if it came back,
#: it is in scope for the whole run.
GENERAL_SCOPES = ("party_kind", "channel", "global")


def applies(lesson: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Does this lesson bear on this candidate?

    A `party`-scoped lesson names one party and applies to it alone. Everything else
    applies to the whole run, because the retrieval that produced it did the scoping.
    """
    if lesson["scope_kind"] == "party":
        return str(lesson["scope_id"]) == str(candidate["id"])
    return lesson["scope_kind"] in GENERAL_SCOPES


def rerank(candidates: list[dict[str, Any]], lessons: list[dict[str, Any]], *,
           weight: float = LESSON_WEIGHT) -> list[dict[str, Any]]:
    """Adjust R1's candidates by what we have learned, and say what did the adjusting.

    Returns new rows — the caller's are not touched — each carrying the original keys
    plus `adjusted` and `applied`. Sorted ascending, because these are distances and
    nearer is better.

    A positive valence *reduces* the distance. That inversion is the one place this
    function is easy to get backwards, which is why the tests assert the ordering rather
    than the arithmetic.
    """
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        shifts: list[dict[str, Any]] = []
        total = 0.0
        for lesson in lessons:
            if not applies(lesson, candidate):
                continue
            shift = float(lesson["valence"]) * float(lesson["confidence"]) * weight
            if shift == 0.0:
                continue
            total += shift
            shifts.append({"lesson_id": str(lesson["id"]),
                           "text": lesson["text"],
                           "shift": abs(shift)})

        # Clamped at zero: a cosine distance is non-negative, and anything downstream
        # reading a negative one as a distance is now quietly wrong.
        adjusted = max(0.0, float(candidate["distance"]) - total)
        out.append({**candidate, "adjusted": adjusted, "applied": shifts})

    out.sort(key=lambda row: row["adjusted"])
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests/test_lessons.py -q`

Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add platform/web/rtf_platform/lessons.py platform/web/tests/test_lessons.py
git commit -m "platform: the rerank is arithmetic, and it says which lesson moved what"
```

---

### Task 3: `lessons.py` — write, and resolve a supersession chain

**Files:**
- Modify: `platform/web/rtf_platform/lessons.py`
- Modify: `platform/web/tests/test_lessons.py`

**Interfaces:**
- Consumes: `lessons.LESSON_WEIGHT` (Task 2); `embed.load()`, `embed.embed_batch(gate, provider, texts) -> (list[Vector], Decimal)`, `Vector.literal()`; `spend.Gate`.
- Produces:
  - `write(conn, tenant_id: str, *, scope_kind: str, scope_id: str, text: str, valence: float, confidence: float, evidence: dict, gate: spend.Gate, supersedes_id: str | None = None) -> str` — returns the new lesson's id. Embeds the text and writes the row in one transaction.
  - `heads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]` — pure; drops every row that another row in the list supersedes.

- [ ] **Step 1: Write the failing test**

Append to `platform/web/tests/test_lessons.py`:

```python
class Heads(unittest.TestCase):
    """A superseded lesson must never reach the rerank.

    `SCOPE-RESET §2a` rule 1 says revisions are appended and the current value is the
    head of the chain. If retrieval returns both a lesson and the correction that
    replaced it, the rerank applies both and the correction is worth nothing.
    """

    def test_an_unsuperseded_row_is_a_head(self):
        rows = [{"id": "a", "supersedes_id": None}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["a"])

    def test_a_superseded_row_is_dropped(self):
        rows = [{"id": "old", "supersedes_id": None},
                {"id": "new", "supersedes_id": "old"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["new"])

    def test_a_chain_of_three_resolves_to_one_head(self):
        rows = [{"id": "v1", "supersedes_id": None},
                {"id": "v2", "supersedes_id": "v1"},
                {"id": "v3", "supersedes_id": "v2"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["v3"])

    def test_a_row_superseding_something_absent_is_still_a_head(self):
        # Retrieval is a top-k: the row it replaced may simply not have scored. The
        # replacement is still current, and dropping it would lose the lesson entirely.
        rows = [{"id": "v2", "supersedes_id": "v1-not-in-this-result"}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["v2"])

    def test_order_is_preserved(self):
        rows = [{"id": "a", "supersedes_id": None}, {"id": "b", "supersedes_id": None}]
        self.assertEqual([r["id"] for r in lessons.heads(rows)], ["a", "b"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests/test_lessons.py::Heads -q`

Expected: FAIL — `AttributeError: module 'rtf_platform.lessons' has no attribute 'heads'`

- [ ] **Step 3: Implement `heads` and `write`**

Add to the imports at the top of `platform/web/rtf_platform/lessons.py`:

```python
import psycopg

from rtf_platform import embed, spend
```

Append to `platform/web/rtf_platform/lessons.py`:

```python
def heads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every row that another row in this result supersedes.

    Resolved within the result set rather than by a query, because retrieval is a top-k
    and the superseded row may not have scored at all. A row whose `supersedes_id` points
    outside the set is still current — it replaced something that simply is not here.
    """
    replaced = {str(row["supersedes_id"]) for row in rows if row.get("supersedes_id")}
    return [row for row in rows if str(row["id"]) not in replaced]


def write(conn: psycopg.Connection, tenant_id: str, *, scope_kind: str, scope_id: str,
          text: str, valence: float, confidence: float, evidence: dict[str, Any],
          gate: spend.Gate, supersedes_id: str | None = None) -> str:
    """Record something learned, embedded and searchable at commit.

    The embedding and the row are written in one transaction, which is
    `PLATFORM-SPEC §1`'s stated reason for this database over a separate vector service:
    there is no window in which a lesson exists but cannot be retrieved. An agent that
    writes a lesson and immediately reranks sees it.

    `valence` is clamped rather than rejected. A caller computing it from a ratio can
    land at 1.0000001, and refusing the whole lesson over float noise loses the lesson.
    """
    provider = embed.load()
    vectors, _ = embed.embed_batch(gate, provider, [text])

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       evidence_json, confidence, valence,
                                       embedding, model, model_version, supersedes_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::VECTOR(1024), %s, %s, %s)
                RETURNING id""",
                (tenant_id, scope_kind, scope_id, text,
                 psycopg.types.json.Jsonb(evidence),
                 max(0.0, min(1.0, float(confidence))),
                 max(-1.0, min(1.0, float(valence))),
                 vectors[0].literal(), provider.model,
                 getattr(provider, "model_version", ""), supersedes_id),
            )
            return str(cur.fetchone()["id"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests/test_lessons.py -q`

Expected: PASS, 16 tests.

- [ ] **Step 5: Confirm `model_version` exists on the provider, and fix if not**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -c "from rtf_platform import embed; print([f for f in embed.OpenAIEmbedder.__dataclass_fields__])"`

If `model_version` is not among the fields, the `getattr(provider, "model_version", "")` above already returns `""` — which the schema permits. Leave it. Do **not** add a field to `OpenAIEmbedder` for this; `007`'s rule is that `model` must be nameable, and it is.

- [ ] **Step 6: Commit**

```bash
git add platform/web/rtf_platform/lessons.py platform/web/tests/test_lessons.py
git commit -m "platform: a lesson is searchable at commit, and a chain resolves to its head"
```

---

### Task 4: `shortlist` reads the lessons

**Files:**
- Modify: `platform/web/rtf_platform/lessons.py` (add `retrieve_for`)
- Modify: `platform/web/rtf_platform/agents.py:625-670` (`shortlist`)
- Test: `platform/web/tests/test_lessons.py` (cluster test)

**Interfaces:**
- Consumes: `lessons.rerank`, `lessons.heads` (Tasks 2–3); `agents.shortlist(conn, tenant_id, party_id, *, gate, limit)`.
- Produces:
  - `lessons.retrieve_for(conn, tenant_id: str, *, query_vector_literal: str, model: str, candidate_ids: list[str], limit: int = 10) -> list[dict[str, Any]]`
  - `agents.shortlist` unchanged in signature, now returning rows that additionally carry `adjusted: float` and `applied: list[dict]`.
  - `agents.SHORTLIST_CANDIDATES: int`

- [ ] **Step 1: Write the failing cluster test**

Append to `platform/web/tests/test_lessons.py`:

```python
import os
import uuid

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class RetrievalIsScoped(unittest.TestCase):
    """Against the real cluster, in a tenant created and dropped per test.

    What cannot be faked: that a lesson written under one embedding model is invisible to
    a retrieval carrying another. A fake returning rows from a dict proves the fake works.
    """

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-lesson-{self.tenant[:8]}", "lesson test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _vector(self, fill: float) -> str:
        return "[" + ",".join([str(fill)] * 1024) + "]"

    def _insert(self, *, model: str, scope_kind: str = "global",
                scope_id: str = "", text: str = "a lesson") -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence, embedding, model)
                   VALUES (%s, %s, %s, %s, 0.5, 0.9, %s::VECTOR(1024), %s)
                RETURNING id""",
                (self.tenant, scope_kind, scope_id, text, self._vector(0.1), model),
            )
            return str(cur.fetchone()["id"])

    def test_a_lesson_from_another_model_is_invisible(self):
        from rtf_platform import lessons as mod
        self._insert(model="other-model")
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[])
        self.assertEqual(found, [],
                         "a vector from a different model is noise, not a near neighbour")

    def test_a_lesson_from_the_same_model_is_found(self):
        from rtf_platform import lessons as mod
        self._insert(model="the-model", text="found me")
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[])
        self.assertIn("found me", [row["text"] for row in found])

    def test_a_party_lesson_is_fetched_by_id_not_by_similarity(self):
        # A party-scoped lesson with a deliberately distant embedding must still come
        # back when that party is a candidate — we know who we mean.
        from rtf_platform import lessons as mod
        party_id = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO lesson (tenant_id, scope_kind, scope_id, text,
                                       valence, confidence, embedding, model)
                   VALUES (%s, 'party', %s, 'ghosted twice', -1, 0.9,
                           %s::VECTOR(1024), 'the-model')""",
                (self.tenant, party_id, self._vector(0.9)),
            )
        found = mod.retrieve_for(self.conn, self.tenant,
                                 query_vector_literal=self._vector(0.1),
                                 model="the-model", candidate_ids=[party_id])
        self.assertIn("ghosted twice", [row["text"] for row in found])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_lessons.py::RetrievalIsScoped -q`

Expected: FAIL — `AttributeError: module 'rtf_platform.lessons' has no attribute 'retrieve_for'`

- [ ] **Step 3: Implement `retrieve_for`**

Append to `platform/web/rtf_platform/lessons.py`:

```python
def retrieve_for(conn: psycopg.Connection, tenant_id: str, *,
                 query_vector_literal: str, model: str,
                 candidate_ids: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """Every lesson bearing on this shortlist: the general ones and the named ones.

    Two queries, because they are two different questions and only one of them is a
    similarity question.

      * **General** — `party_kind`, `channel` and `global` lessons, by ANN over
        `lesson_semantic`. `scope_kind IN (…)` is accelerated on a prefix column the same
        way equality is; `docs/reference/COCKROACHDB-AI.md` has the constraint.
      * **Named** — lessons about the specific parties R1 returned, by id over
        `lesson_by_scope`. We already know who we mean; asking the vector index to
        rediscover them would be slower and could miss one.

    Superseded rows are dropped from each result separately, so a correction retrieved
    without its predecessor still counts.
    """
    general: list[dict[str, Any]] = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, scope_kind, scope_id, text, valence, confidence,
                      supersedes_id, embedding <=> %s::VECTOR(1024) AS distance
                 FROM lesson
                WHERE tenant_id = %s AND model = %s
                  AND scope_kind IN ('party_kind', 'channel', 'global')
                ORDER BY embedding <=> %s::VECTOR(1024)
                LIMIT %s""",
            (query_vector_literal, tenant_id, model, query_vector_literal, limit),
        )
        general = list(cur.fetchall())

    named: list[dict[str, Any]] = []
    if candidate_ids:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, scope_kind, scope_id, text, valence, confidence,
                          supersedes_id, NULL::FLOAT AS distance
                     FROM lesson
                    WHERE tenant_id = %s AND scope_kind = 'party'
                      AND scope_id = ANY(%s)
                    ORDER BY created_at DESC""",
                (tenant_id, [str(cid) for cid in candidate_ids]),
            )
            named = list(cur.fetchall())

    return heads(general) + heads(named)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_lessons.py -q`

Expected: PASS, 19 tests.

- [ ] **Step 5: Wire the rerank into `shortlist`**

In `platform/web/rtf_platform/agents.py`, add to the imports:

```python
from rtf_platform import embed, fleet, lessons, repo, sources, spend
```

Add beside `CHUNK_CHARS` near the top of the file:

```python
#: How many candidates R1 fetches before the rerank sees them. Wider than the caller's
#: `limit`, because a rerank that can only reorder the rows it is going to return cannot
#: promote anybody into them — and promoting a good match that similarity alone ranked
#: 24th is the entire point of reading the lessons.
SHORTLIST_CANDIDATES = 50
```

Replace the second half of `shortlist` — everything from the second `with conn.cursor() as cur:` to the end of the function (`agents.py:645-670`) — with:

```python
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.name, p.contact_state,
                      p.profile_embedding <=> %s::VECTOR(1024) AS distance,
                      pr.url
                 FROM party p
                 LEFT JOIN presence pr ON pr.subject_kind = 'party' AND pr.subject_id = p.id
                WHERE p.tenant_id = %s
                  AND p.embedding_model = %s
                  AND p.party_class = 'counterparty'
                  AND p.contact_state = 'contactable'
                ORDER BY p.profile_embedding <=> %s::VECTOR(1024)
                LIMIT %s""",
            (artist["vec"], tenant_id, artist["embedding_model"], artist["vec"],
             SHORTLIST_CANDIDATES),
        )
        candidates = [dict(row) for row in cur.fetchall()]

    # R2. The second pass, and the reason `SCOPE-RESET §1` puts the party at the root:
    # without it this function returns the same answer on the hundredth campaign as on
    # the first.
    applicable = lessons.retrieve_for(
        conn, tenant_id,
        query_vector_literal=artist["vec"],
        model=artist["embedding_model"],
        candidate_ids=[str(row["id"]) for row in candidates],
    )
    ranked = lessons.rerank(candidates, applicable)[:limit]

    # `hit_count` is what tells an operator which lessons earn their place. Incremented
    # here rather than in the rerank, because the rerank is pure and must stay that way.
    spent = {entry["lesson_id"] for row in ranked for entry in row["applied"]}
    if spent:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE lesson SET hit_count = hit_count + 1 WHERE id = ANY(%s)",
                (list(spent),),
            )

    return ranked
```

Update the `shortlist` docstring by appending this paragraph before the closing `"""`:

```
    Two passes. R1 is the vector search above — every predicate an equality on a prefix
    column, resolving to a `vector search` node with `prefix spans`. R2 is
    `lessons.retrieve_for`, and the rerank it feeds is what makes this function's answer
    depend on what happened last time. A row comes back carrying `adjusted` and
    `applied`, and `applied` names every lesson that moved it, because the console's
    inspector has to be able to say why.
```

- [ ] **Step 6: Verify the whole suite still passes**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests -q`

Expected: PASS. **93 passed, 19 skipped** — the 77-test baseline plus the 16 offline tests from Tasks 2–3, with Task 4's 3 cluster tests joining the 16 already-skipped ones.

- [ ] **Step 7: Verify R1 still uses the index after the change**

Run:

```bash
cd "$(git rev-parse --show-toplevel)"
psql "$DATABASE_URL" -c "EXPLAIN SELECT p.id FROM party p \
  WHERE p.tenant_id = (SELECT id FROM tenant LIMIT 1) \
    AND p.embedding_model = 'text-embedding-3-large' \
    AND p.party_class = 'counterparty' AND p.contact_state = 'contactable' \
  ORDER BY p.profile_embedding <=> (SELECT profile_embedding FROM party \
    WHERE profile_embedding IS NOT NULL LIMIT 1) LIMIT 50"
```

Expected: the plan contains a `vector search` node with `prefix spans`. If it shows a full scan with a post-filter, the widening to 50 has not broken it — check the `embedding_model` literal matches what is actually in the column first:

```bash
psql "$DATABASE_URL" -c "SELECT DISTINCT embedding_model FROM party WHERE profile_embedding IS NOT NULL"
```

- [ ] **Step 8: Commit**

```bash
git add platform/web/rtf_platform/lessons.py platform/web/rtf_platform/agents.py platform/web/tests/test_lessons.py
git commit -m "platform: the shortlist reads what we learned, and says which lesson moved what"
```

---

### Task 5: Migration 012 — the alias

**Files:**
- Create: `platform/schema/012_party_alias.sql`

**Interfaces:**
- Consumes: `party.party_class` and its `party_class_known` constraint from `009`.
- Produces: `party.alias_of UUID`; `party_class` accepts `'alias'`; constraint `party_alias_is_classed`; index `party_aliases_of`.

- [ ] **Step 1: Write the migration**

Create `platform/schema/012_party_alias.sql`:

```sql
-- 012 — a merge that can be undone.
--
-- The live index already holds `Amanda`, `Amanda` again, `Amanda Goncalves`,
-- `Amanda Gonçalves`, `Amanda Rocha da Silva` and `Petra Liina Amanda Suokorpi`. Five
-- or six rows that are probably two or three people. Deduplication is not hypothetical
-- here, and it becomes load-bearing the moment discovery adds rows faster than a human
-- reconciles them.
--
--
-- ## Why an alias flag and not a rewrite
--
-- The obvious merge repoints every reference from the duplicate to the survivor and
-- deletes the duplicate. It is irreversible in the way that matters: two curators
-- collapsed into one is a mistake you cannot undo, because you no longer know there
-- were two.
--
-- So nothing is rewritten. The duplicate keeps its row, its presence, its documents and
-- its facts; it gains `alias_of` pointing at the survivor and `party_class = 'alias'`.
-- Accepting a merge is two column writes. Reversing it is the same two backwards.
--
--
-- ## Why the flag costs nothing to read
--
-- `party_class` is already an equality prefix column on the `party_shortlist` vector
-- index, and R1 filters `party_class = 'counterparty'`. So an alias drops out of the
-- shortlist by construction — no new predicate, no index change, and none of the
-- acceleration `009` was written to preserve. This is the third use of the same trick
-- `009` argued for `contact_state`, and it is the reason that argument was worth making
-- once properly.
--
-- The cost lands on reads that want everything known about a person: they must union
-- over the alias chain. That is one join in `repo`, and it is the better trade.
--
--
-- ## The hole this opens in `thread`, closed by 012a
--
-- `thread` already exists on the cluster, with a partial unique index
-- `one_open_thread_per_counterparty` on `(tenant_id, counterparty_id)` enforcing
-- `PLATFORM-SPEC §3c`: one open conversation per person, across every channel.
--
-- An alias and the party it aliases are **two different `counterparty_id` values**. So
-- the moment this migration makes aliases possible, that index stops meaning what it
-- says — both rows can hold an open thread, and the label contacts one person twice
-- through the back door the index was built to lock.
--
-- `012a_thread_canonical.sql` closes it, in the same session as this file, by adding a
-- denormalised `thread.canonical_party_id` and moving the index onto that column. The
-- two migrations are separable only in the sense that they are separate files; shipping
-- this one without that one is a regression in a correctness guarantee that already
-- holds today.


ALTER TABLE party ADD COLUMN IF NOT EXISTS alias_of UUID REFERENCES party(id) ON DELETE SET NULL;

ALTER TABLE party DROP CONSTRAINT IF EXISTS party_class_known;
ALTER TABLE party ADD CONSTRAINT party_class_known
    CHECK (party_class IN ('roster', 'counterparty', 'alias'));

-- The two halves of the flag cannot drift apart: an alias without a target is
-- unresolvable, and a target without the class would still be shortlisted.
ALTER TABLE party ADD CONSTRAINT party_alias_is_classed
    CHECK ((party_class = 'alias') = (alias_of IS NOT NULL));

-- Resolving a canonical party to its aliases — for the union read, and for the console
-- showing what was merged into what.
CREATE INDEX IF NOT EXISTS party_aliases_of
    ON party (tenant_id, alias_of) STORING (name, party_class);
```

- [ ] **Step 2: Apply it**

Run: `cd "$(git rev-parse --show-toplevel)" && python platform/schema/apply.py 012_party_alias.sql`

Expected: applied without error. The 21 existing rows all have `alias_of IS NULL` and `party_class IN ('roster','counterparty')`, so `party_alias_is_classed` validates against them.

- [ ] **Step 3: Verify the constraint refuses a half-set alias**

Run:

```bash
psql "$DATABASE_URL" -c "UPDATE party SET party_class = 'alias' \
  WHERE id = (SELECT id FROM party LIMIT 1)"
```

Expected: FAILS with `party_alias_is_classed` — the class was set without a target. If it succeeds, an alias can exist that points nowhere and `repo.resolve_canonical` will return an id that is not contactable.

- [ ] **Step 4: Commit**

```bash
git add platform/schema/012_party_alias.sql
git commit -m "platform: a merge keeps both rows, and an alias falls out of R1 for free"
```

---

### Task 5a: Migration 012a — the collision index follows the canonical

**Do not stop between Task 5 and this one.** Task 5 makes aliases possible; until this lands, `one_open_thread_per_counterparty` no longer enforces what it claims.

**Files:**
- Create: `platform/schema/012a_thread_canonical.sql`

**Interfaces:**
- Consumes: `party.alias_of` (Task 5); the live `thread` table and its `one_open_thread_per_counterparty` index, created by `010_outreach.sql` — **which is not in this branch**, only on the cluster.
- Produces: `thread.canonical_party_id UUID NOT NULL`; index `one_open_thread_per_canonical`; `one_open_thread_per_counterparty` dropped.

- [ ] **Step 1: Confirm the cluster is in the state this migration assumes**

Run:

```bash
psql "$DATABASE_URL" -c "SELECT count(*) AS threads FROM thread" \
                     -c "SELECT indexdef FROM pg_indexes WHERE tablename = 'thread'"
```

Expected: `one_open_thread_per_counterparty` is present. Note the thread count — Step 3's backfill has to cover it. **If `thread` does not exist, stop and report:** the parallel session's work has been reverted and this task's premise is gone.

- [ ] **Step 2: Write the migration**

Create `platform/schema/012a_thread_canonical.sql`:

```sql
-- 012a — the collision index has to follow the person, not the row.
--
-- Numbered `012a` rather than `013` because it is not a separable change: `012` makes
-- aliases possible and this repairs what that breaks. A cluster carrying `012` without
-- this file has a `PLATFORM-SPEC §3c` guarantee that reads as enforced and is not.
--
--
-- ## What breaks
--
-- `010_outreach.sql` shipped the §3c guarantee as a partial unique index:
--
--     one_open_thread_per_counterparty ON thread (tenant_id, counterparty_id)
--       WHERE state NOT IN ('closed_won','closed_lost','closed_no_reply')
--
-- One open conversation per person, across every channel — the constraint that makes
-- running channels in parallel safe, and the sharpest thing the architecture does.
--
-- `012` makes an alias and the party it aliases two rows for one person, with two ids.
-- The index counts ids. So the UGC fleet opens a thread with `Amanda Gonçalves` and the
-- curator fleet opens one with `Amanda Goncalves`, and the database — correctly, by its
-- own lights — permits both. The person gets contacted twice, which is the failure the
-- index exists to make impossible.
--
--
-- ## The fix, and why it is a denormalised column and not a join
--
-- The index needs a single value per person. `alias_of` lives on `party`, and a partial
-- unique index cannot reach across a join to find it. So the canonical id is carried on
-- `thread`, written in the same serializable transaction as the insert.
--
-- This is the third application of the argument `009` made for `contact_state` and `012`
-- made for `party_class`: **the denormalisation is safe because it is written in the same
-- transaction as the fact it mirrors.** It is not a cache and it cannot go stale within a
-- transaction boundary.
--
-- Both columns are kept. `counterparty_id` is who the thread is literally with — which
-- row the operator clicked, whose handle is on the message. `canonical_party_id` is who
-- that turns out to be. Collapsing them would lose the first, and the first is what the
-- inbox has to show.
--
--
-- ## Accepting a merge now touches threads
--
-- `repo.merge_party` repoints `canonical_party_id` for the alias's open threads. That
-- write can violate the new index — which is the correct outcome, not an error to
-- swallow: it means both rows already had open threads, so the label has been talking to
-- one person twice and somebody needs to know before the rows are joined.


ALTER TABLE thread ADD COLUMN IF NOT EXISTS canonical_party_id UUID
    REFERENCES party(id) ON DELETE CASCADE;

-- Backfill before the NOT NULL: every existing thread predates aliases, so its
-- counterparty is its own canonical.
UPDATE thread SET canonical_party_id = counterparty_id WHERE canonical_party_id IS NULL;

ALTER TABLE thread ALTER COLUMN canonical_party_id SET NOT NULL;

-- Order matters. Create the replacement before dropping the incumbent, so there is no
-- window in which §3c is unenforced — a window a concurrent fleet could open two threads
-- through.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_thread_per_canonical
    ON thread (tenant_id, canonical_party_id)
 WHERE state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply');

DROP INDEX IF EXISTS one_open_thread_per_counterparty;

CREATE INDEX IF NOT EXISTS thread_by_canonical
    ON thread (tenant_id, canonical_party_id, state);
```

- [ ] **Step 3: Apply it**

Run: `cd "$(git rev-parse --show-toplevel)" && python platform/schema/apply.py 012a_thread_canonical.sql`

Expected: applied without error. If `ALTER COLUMN … SET NOT NULL` fails, the backfill missed rows — inspect with `SELECT count(*) FROM thread WHERE canonical_party_id IS NULL` rather than dropping the `NOT NULL`.

- [ ] **Step 4: Prove the guarantee survived the swap**

```bash
psql "$DATABASE_URL" -c "SELECT indexdef FROM pg_indexes \
  WHERE tablename = 'thread' AND indexname LIKE 'one_open%'"
```

Expected: exactly one row, `one_open_thread_per_canonical`, on `canonical_party_id`. Two rows means the drop did not fire and merges will now fail against the stale index; zero means §3c is unenforced and the cluster must not be left in that state.

- [ ] **Step 5: Commit**

```bash
git add platform/schema/012a_thread_canonical.sql
git commit -m "platform: one open thread per person, not per row"
```

---

### Task 6: `repo.merge_party` and `repo.unmerge_party`

**Files:**
- Modify: `platform/web/rtf_platform/repo.py` (add three functions; amend `delete_party` at `repo.py:144-167`)
- Test: `platform/web/tests/test_merge.py`

**Interfaces:**
- Consumes: migration `011` (Task 5).
- Produces:
  - `repo.resolve_canonical(conn, tenant_id: str, party_id: str) -> str` — follows `alias_of` one hop; returns `party_id` unchanged when it is not an alias. Named `resolve_canonical` rather than `canonical_id` because `merge_party` takes a `canonical_id` **parameter**, and a function shadowed by an argument in its only caller is a bug waiting to be written.
  - `repo.merge_party(conn, tenant_id: str, *, alias_id: str, canonical_id: str) -> None` — raises `MergeRefused` on a self-merge or when `alias_id` already has aliases of its own.
  - `repo.unmerge_party(conn, tenant_id: str, alias_id: str) -> None`
  - `repo.MergeRefused(RuntimeError)`

- [ ] **Step 1: Write the failing test**

Create `platform/web/tests/test_merge.py`:

```python
"""Merging, against the real cluster.

Everything worth testing here is a database behaviour — a `CHECK` that refuses a
half-set alias, a vector index that stops returning a row because one column changed,
a foreign key that survives a merge because nothing was rewritten. A fake proves none
of it.

The property that matters: **a merge is exactly reversible.** Accept it, reverse it, and
every row reads as it did before. That is the whole justification for the alias flag over
the rewrite, and if it is not true the flag is just a slower rewrite.
"""

from __future__ import annotations

import os
import unittest
import uuid

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Merging(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-merge-{self.tenant[:8]}", "merge test"))
        self.keeper = self._party("Amanda Gonçalves")
        self.dupe = self._party("Amanda Goncalves")

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _party(self, name: str) -> str:
        vec = "[" + ",".join(["0.1"] * 1024) + "]"
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state,
                                      profile_embedding, embedding_model)
                   VALUES (%s, %s, %s, 'counterparty', 'contactable',
                           %s::VECTOR(1024), 'test-model')
                RETURNING id""",
                (self.tenant, f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                 name, vec),
            )
            return str(cur.fetchone()["id"])

    def _row(self, party_id: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT party_class, alias_of, name FROM party WHERE id = %s",
                        (party_id,))
            return dict(cur.fetchone())

    def _shortlist_ids(self) -> set[str]:
        vec = "[" + ",".join(["0.1"] * 1024) + "]"
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM party
                    WHERE tenant_id = %s AND embedding_model = 'test-model'
                      AND party_class = 'counterparty' AND contact_state = 'contactable'
                    ORDER BY profile_embedding <=> %s::VECTOR(1024) LIMIT 50""",
                (self.tenant, vec),
            )
            return {str(r["id"]) for r in cur.fetchall()}

    def test_both_rows_survive_a_merge(self):
        from rtf_platform import repo
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        self.assertIsNotNone(self._row(self.dupe), "the duplicate row must not be deleted")
        self.assertEqual(self._row(self.dupe)["party_class"], "alias")
        self.assertEqual(str(self._row(self.dupe)["alias_of"]), self.keeper)
        self.assertEqual(self._row(self.keeper)["party_class"], "counterparty")

    def test_an_alias_disappears_from_the_shortlist(self):
        from rtf_platform import repo
        self.assertIn(self.dupe, self._shortlist_ids())
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        found = self._shortlist_ids()
        self.assertNotIn(self.dupe, found, "an alias must never be shortlisted")
        self.assertIn(self.keeper, found, "the survivor must still be shortlisted")

    def test_a_merge_round_trips(self):
        from rtf_platform import repo
        before = (self._row(self.dupe), self._row(self.keeper))
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        repo.unmerge_party(self.conn, self.tenant, self.dupe)
        self.assertEqual((self._row(self.dupe), self._row(self.keeper)), before)

    def test_resolve_canonical_follows_the_alias(self):
        from rtf_platform import repo
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        self.assertEqual(repo.resolve_canonical(self.conn, self.tenant, self.dupe),
                         self.keeper)
        self.assertEqual(repo.resolve_canonical(self.conn, self.tenant, self.keeper),
                         self.keeper)

    def test_merging_a_party_into_itself_is_refused(self):
        from rtf_platform import repo
        with self.assertRaises(repo.MergeRefused):
            repo.merge_party(self.conn, self.tenant,
                             alias_id=self.dupe, canonical_id=self.dupe)

    def test_alias_chains_stay_one_level_deep(self):
        # Merging B into A, then C into B, must land C on A — not on B, which is no
        # longer a person we would ever contact.
        from rtf_platform import repo
        third = self._party("Amanda")
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        repo.merge_party(self.conn, self.tenant,
                         alias_id=third, canonical_id=self.dupe)
        self.assertEqual(str(self._row(third)["alias_of"]), self.keeper)

    def test_merging_a_party_that_has_aliases_is_refused(self):
        # A has B merged into it; merging A into C would strand B pointing at a
        # non-canonical row, which is the chain the previous test forbids.
        from rtf_platform import repo
        third = self._party("Amanda Rocha da Silva")
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        with self.assertRaises(repo.MergeRefused):
            repo.merge_party(self.conn, self.tenant,
                             alias_id=self.keeper, canonical_id=third)

    def _campaign(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO campaign (tenant_id, party_id, channel, goal, state)
                   VALUES (%s, %s, 'email', 'test', 'active') RETURNING id""",
                (self.tenant, self.keeper),
            )
            return str(cur.fetchone()["id"])

    def _thread(self, campaign_id: str, party_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO thread (tenant_id, campaign_id, counterparty_id,
                                       canonical_party_id, state)
                   VALUES (%s, %s, %s, %s, 'discovered') RETURNING id""",
                (self.tenant, campaign_id, party_id, party_id),
            )
            return str(cur.fetchone()["id"])

    def test_an_open_thread_follows_the_merge(self):
        from rtf_platform import repo
        thread_id = self._thread(self._campaign(), self.dupe)
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        with self.conn.cursor() as cur:
            cur.execute("""SELECT counterparty_id, canonical_party_id
                             FROM thread WHERE id = %s""", (thread_id,))
            row = cur.fetchone()
        self.assertEqual(str(row["canonical_party_id"]), self.keeper,
                         "the thread is now with the surviving party")
        self.assertEqual(str(row["counterparty_id"]), self.dupe,
                         "but it still records which row we actually wrote to")

    def test_merging_two_parties_that_both_have_open_threads_is_refused(self):
        # The §3c guarantee, arriving late: if both are open, we already contacted one
        # person twice, and that is the operator's problem to see rather than ours to
        # paper over.
        from rtf_platform import repo
        campaign = self._campaign()
        self._thread(campaign, self.dupe)
        self._thread(campaign, self.keeper)
        with self.assertRaises(repo.MergeRefused) as caught:
            repo.merge_party(self.conn, self.tenant,
                             alias_id=self.dupe, canonical_id=self.keeper)
        self.assertIn("open thread", str(caught.exception))

    def test_a_closed_thread_does_not_block_a_merge(self):
        from rtf_platform import repo
        campaign = self._campaign()
        self._thread(campaign, self.dupe)
        closed = self._thread(campaign, self.keeper)
        with self.conn.cursor() as cur:
            cur.execute("UPDATE thread SET state = 'closed_lost' WHERE id = %s",
                        (closed,))
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        self.assertEqual(self._row(self.dupe)["party_class"], "alias")

    def test_unmerging_puts_the_thread_back(self):
        from rtf_platform import repo
        thread_id = self._thread(self._campaign(), self.dupe)
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        repo.unmerge_party(self.conn, self.tenant, self.dupe)
        with self.conn.cursor() as cur:
            cur.execute("SELECT canonical_party_id FROM thread WHERE id = %s",
                        (thread_id,))
            self.assertEqual(str(cur.fetchone()["canonical_party_id"]), self.dupe)

    def test_the_alias_keeps_its_presence_rows(self):
        # The whole argument for the flag: nothing is rewritten, so nothing is lost.
        from rtf_platform import repo
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO presence (tenant_id, subject_kind, subject_id, platform, url)
                   VALUES (%s, 'party', %s, 'deezer', 'https://example.invalid/a')""",
                (self.tenant, self.dupe),
            )
        repo.merge_party(self.conn, self.tenant,
                         alias_id=self.dupe, canonical_id=self.keeper)
        with self.conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM presence
                            WHERE subject_kind = 'party' AND subject_id = %s""",
                        (self.dupe,))
            self.assertEqual(cur.fetchone()["n"], 1)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_merge.py -q`

Expected: FAIL — `AttributeError: module 'rtf_platform.repo' has no attribute 'merge_party'`

Note: `presence` column names are assumed to be `(tenant_id, subject_kind, subject_id, platform, url)`. If the insert in the last test errors on a column, run `psql "$DATABASE_URL" -c "\d presence"` and correct the test to the real columns before implementing — the test is wrong in that case, not the schema.

- [ ] **Step 3: Implement the three functions**

Add to `platform/web/rtf_platform/repo.py`, above `delete_party`:

```python
class MergeRefused(RuntimeError):
    """A merge that would lose information or leave a chain, refused before it happens.

    Distinct from a constraint violation because these are all cases a human can act on:
    the message says what to do instead.
    """


def resolve_canonical(conn: psycopg.Connection, tenant_id: str, party_id: str) -> str:
    """The party this id ultimately refers to. One hop, because chains are forbidden.

    `merge_party` resolves the target before writing, so `alias_of` never points at
    another alias and a single hop is always enough. If that invariant is ever broken,
    this returns the intermediate rather than looping forever — the wrong answer, but a
    terminating one, and `test_alias_chains_stay_one_level_deep` is what keeps it honest.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT alias_of FROM party WHERE tenant_id = %s AND id = %s",
                    (tenant_id, party_id))
        row = cur.fetchone()
    if row is None or row["alias_of"] is None:
        return party_id
    return str(row["alias_of"])


def merge_party(conn: psycopg.Connection, tenant_id: str, *,
                alias_id: str, canonical_id: str) -> None:
    """Declare that two rows are one person, reversibly.

    Nothing is rewritten. The duplicate keeps every row that references it and gains
    `alias_of` plus `party_class = 'alias'`, which drops it out of R1 because
    `party_class` is an equality prefix column on `party_shortlist`.

    Merging into an alias resolves to that alias's canonical first, so chains never form.
    Merging a party that already has aliases of its own is refused rather than resolved,
    because the alternative is silently repointing somebody else's merge.

    Open threads follow the merge: `canonical_party_id` is repointed for the alias's
    threads, inside the same transaction. If both parties hold an open thread the unique
    index from `012a` refuses it, and that refusal is re-raised as `MergeRefused` with
    the collision named — because it means the label has been contacting one person
    twice, which the operator needs to know *before* the rows are joined, not after.
    """
    target = resolve_canonical(conn, tenant_id, canonical_id)
    if str(alias_id) == str(target):
        raise MergeRefused("a party cannot be merged into itself")

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM party WHERE tenant_id = %s AND alias_of = %s",
                (tenant_id, alias_id),
            )
            if cur.fetchone()["n"]:
                raise MergeRefused(
                    "this party already has aliases of its own — merge them into the "
                    "same survivor instead, or reverse those merges first")

            cur.execute(
                """UPDATE party SET party_class = 'alias', alias_of = %s
                    WHERE tenant_id = %s AND id = %s AND party_class != 'alias'""",
                (target, tenant_id, alias_id),
            )
            if cur.rowcount == 0:
                raise MergeRefused("no such party, or it is already an alias")

            # The threads follow the person. `012a` put §3c's unique index on
            # `canonical_party_id`, so this UPDATE is where a double-contact that
            # already happened finally surfaces.
            try:
                cur.execute(
                    """UPDATE thread SET canonical_party_id = %s, updated_at = now()
                        WHERE tenant_id = %s AND canonical_party_id = %s""",
                    (target, tenant_id, alias_id),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise MergeRefused(
                    "both of these parties have an open thread — if they are the same "
                    "person, we have contacted them twice. Close one thread before "
                    "merging, and read it first: what it says is the reason to check."
                ) from exc


def unmerge_party(conn: psycopg.Connection, tenant_id: str, alias_id: str) -> None:
    """Undo a merge. Two column writes, the same two that made it.

    The class returns to `counterparty` rather than to whatever it was, because `roster`
    is a deliberate operator act and an alias is by definition not one of our own
    artists. A roster party merged by mistake comes back as a counterparty and is
    reclassified by hand — rare, visible, and better than guessing.

    The thread repoint is reversed too, and `counterparty_id` is what it reverses *to* —
    the column that never changed is how the original value is recovered without
    journalling it anywhere.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE party SET party_class = 'counterparty', alias_of = NULL
                    WHERE tenant_id = %s AND id = %s AND party_class = 'alias'""",
                (tenant_id, alias_id),
            )
            if cur.rowcount == 0:
                return                     # not an alias; nothing to undo

            cur.execute(
                """UPDATE thread SET canonical_party_id = counterparty_id,
                                     updated_at = now()
                    WHERE tenant_id = %s AND counterparty_id = %s""",
                (tenant_id, alias_id),
            )
```

- [ ] **Step 4: Amend `delete_party` to clear lessons**

In `platform/web/rtf_platform/repo.py`, inside `delete_party`'s transaction, immediately after the `DELETE FROM presence` statement, add:

```python
            cur.execute(
                """DELETE FROM lesson
                    WHERE tenant_id = %s AND scope_kind = 'party' AND scope_id = %s""",
                (tenant_id, str(party_id)),
            )
```

And extend the docstring's second paragraph to read:

```
    `presence` is polymorphic — `subject_id` carries no foreign key, which is the
    price of one table serving parties, recordings and releases — so `ON DELETE
    CASCADE` never fires for it. `lesson.scope_id` is polymorphic for the same reason
    and pays the same price. Left behind, those rows are not merely untidy: the probe
    reconciler works from presence, so a deleted artist would keep being fetched
    forever and nothing would explain why; and an orphaned lesson keeps adjusting
    shortlists on behalf of somebody who no longer exists.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_merge.py -q`

Expected: PASS, 12 tests.

- [ ] **Step 6: Run the whole suite**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests -q`

Expected: **93 passed, 31 skipped** with `DATABASE_URL` unset — Task 6's 12 cluster tests are all skips in that mode.

- [ ] **Step 7: Commit**

```bash
git add platform/web/rtf_platform/repo.py platform/web/tests/test_merge.py
git commit -m "platform: a merge is two writes, and reversing it is the same two backwards"
```

---

### Task 7: The `dedup_party` agent

**Files:**
- Modify: `platform/web/rtf_platform/agents.py` (add `dedup_party`, extend `REGISTRY`)
- Test: `platform/web/tests/test_dedup.py`

**Interfaces:**
- Consumes: `fleet.Outcome`, `fleet.LeadFailed`, `spend.Gate`, migration `011`'s `party_class = 'alias'` (Tasks 5–6).
- Produces:
  - `agents.MERGE_DISTANCE: float`
  - `agents.dedup_party(conn, lead: dict[str, Any], gate: spend.Gate) -> fleet.Outcome`
  - `agents.REGISTRY["dedup_party"]`

- [ ] **Step 1: Write the failing test**

Create `platform/web/tests/test_dedup.py`:

```python
"""The deduplication agent, against the cluster.

One property above all others: **this agent proposes and never merges.** An automatic
identity merge is irreversible in the way that matters — two curators collapsed into one
is a mistake you cannot detect afterwards, because you no longer know there were two.
`agents.map_source` already states the rule this follows: a guess is a suggestion, never
a fact.
"""

from __future__ import annotations

import os
import unittest
import uuid

HAVE_DB = bool(os.environ.get("DATABASE_URL"))


@unittest.skipUnless(HAVE_DB, "DATABASE_URL unset — cluster tests skipped")
class Deduplicating(unittest.TestCase):

    def setUp(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True,
                                    row_factory=dict_row)
        self.tenant = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO tenant (id, slug, name) VALUES (%s, %s, %s)",
                        (self.tenant, f"test-dedup-{self.tenant[:8]}", "dedup test"))

    def tearDown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tenant WHERE id = %s", (self.tenant,))
        self.conn.close()

    def _party(self, name: str, fill: float) -> str:
        vec = "[" + ",".join([str(fill)] * 1024) + "]"
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class, contact_state,
                                      profile_embedding, embedding_model)
                   VALUES (%s, %s, %s, 'counterparty', 'contactable',
                           %s::VECTOR(1024), 'test-model')
                RETURNING id""",
                (self.tenant, f"{uuid.uuid4().hex[:10]}", name, vec),
            )
            return str(cur.fetchone()["id"])

    def _lead(self, party_id: str) -> dict:
        return {"id": None, "tenant_id": self.tenant, "party_id": party_id,
                "target": party_id, "kind": "dedup_party"}

    def _suggestions(self) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, party_id, payload, confidence, state, rationale
                     FROM suggestion WHERE tenant_id = %s AND kind = 'merge'""",
                (self.tenant,),
            )
            return [dict(r) for r in cur.fetchall()]

    def test_a_near_duplicate_produces_a_pending_suggestion(self):
        from rtf_platform import agents, spend
        subject = self._party("Amanda Gonçalves", 0.1)
        self._party("Amanda Goncalves", 0.1)          # identical vector
        agents.dedup_party(self.conn, self._lead(subject), spend.Gate.open(None, None))
        found = self._suggestions()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["state"], "pending")

    def test_the_agent_never_merges_by_itself(self):
        from rtf_platform import agents, spend
        subject = self._party("Amanda Gonçalves", 0.1)
        other = self._party("Amanda Goncalves", 0.1)
        agents.dedup_party(self.conn, self._lead(subject), spend.Gate.open(None, None))
        with self.conn.cursor() as cur:
            cur.execute("""SELECT count(*) AS n FROM party
                            WHERE tenant_id = %s AND party_class = 'alias'""",
                        (self.tenant,))
            self.assertEqual(cur.fetchone()["n"], 0,
                             "dedup_party must propose, never merge")
            cur.execute("""SELECT party_class, alias_of FROM party WHERE id = %s""",
                        (other,))
            row = cur.fetchone()
        self.assertEqual(row["party_class"], "counterparty",
                         "the near-duplicate must be left exactly as it was found")
        self.assertIsNone(row["alias_of"])

    def test_a_distant_party_produces_nothing(self):
        from rtf_platform import agents, spend
        subject = self._party("Amanda Gonçalves", 0.1)
        self._party("Rudy - Deezer Moods Editor", -0.9)
        agents.dedup_party(self.conn, self._lead(subject), spend.Gate.open(None, None))
        self.assertEqual(self._suggestions(), [])

    def test_the_suggestion_carries_both_ids_and_the_distance(self):
        from rtf_platform import agents, spend
        subject = self._party("Amanda Gonçalves", 0.1)
        twin = self._party("Amanda Goncalves", 0.1)
        agents.dedup_party(self.conn, self._lead(subject), spend.Gate.open(None, None))
        payload = self._suggestions()[0]["payload"]
        self.assertEqual({str(payload["alias_id"]), str(payload["canonical_id"])},
                         {subject, twin})
        self.assertIn("distance", payload)

    def test_an_existing_alias_is_not_proposed_again(self):
        from rtf_platform import agents, repo, spend
        subject = self._party("Amanda Gonçalves", 0.1)
        twin = self._party("Amanda Goncalves", 0.1)
        repo.merge_party(self.conn, self.tenant, alias_id=twin, canonical_id=subject)
        agents.dedup_party(self.conn, self._lead(subject), spend.Gate.open(None, None))
        self.assertEqual(self._suggestions(), [],
                         "a party already merged must not be proposed a second time")

    def test_a_party_with_no_embedding_fails_permanently(self):
        from rtf_platform import agents, fleet, spend
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO party (tenant_id, slug, name, party_class)
                   VALUES (%s, %s, 'No Vector', 'counterparty') RETURNING id""",
                (self.tenant, uuid.uuid4().hex[:10]),
            )
            bare = str(cur.fetchone()["id"])
        with self.assertRaises(fleet.LeadFailed) as caught:
            agents.dedup_party(self.conn, self._lead(bare), spend.Gate.open(None, None))
        self.assertTrue(caught.exception.permanent,
                        "retrying will not conjure an embedding")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_dedup.py -q`

Expected: FAIL — `AttributeError: module 'rtf_platform.agents' has no attribute 'dedup_party'`

- [ ] **Step 3: Implement the agent**

Add beside `SHORTLIST_CANDIDATES` in `platform/web/rtf_platform/agents.py`:

```python
#: Cosine distance below which two parties are proposed as the same person.
#:
#: Deliberately conservative, and asymmetric on purpose: a missed duplicate costs an
#: operator one glance at a list, while a merge proposed between two real people costs
#: their trust in the whole queue — after which they stop reading it and every
#: suggestion is worthless. Chosen before the index was large enough to tune against
#: (spec §10 item 1) and named here so that changing it is a visible change.
MERGE_DISTANCE = 0.08
```

Add before `shortlist` in `platform/web/rtf_platform/agents.py`:

```python
def dedup_party(conn: psycopg.Connection, lead: dict[str, Any],
                gate: spend.Gate) -> fleet.Outcome:
    """R3 — is this party somebody we already know?

    `PLATFORM-SPEC §6` deferred R3 as "useful, not load-bearing". Acquisition by scraper
    makes it load-bearing: a discovery process that adds rows faster than a human
    reconciles them degrades the very shortlist it exists to improve. The live index
    already holds five or six rows that are probably two or three people.

    **This agent proposes and never merges.** It writes `suggestion(kind='merge')` and a
    human accepts, through the same queue and state machine the `presence` suggestions
    already use. That is `map_source`'s rule — a guess is a suggestion, never a fact —
    and here it is also the difference between a reversible act and an undetectable one.

    Name similarity is deliberately not the trigger. `Amanda Goncalves` and
    `Amanda Rocha da Silva` share a token and are probably different people; what they
    curate, as a vector, is the stronger signal. The name is carried in the rationale so
    the operator sees it, and it decides nothing.

    Costs nothing metered: the subject's embedding already exists, so there is no model
    call and the gate is never asked. It is still in the signature because the fleet
    passes it to every agent and an agent that quietly diverges from that contract is
    the one nobody remembers to budget for.
    """
    party_id = lead.get("party_id") or lead["target"]

    with conn.cursor() as cur:
        cur.execute(
            """SELECT profile_embedding::STRING AS vec, embedding_model, name,
                      party_class
                 FROM party WHERE tenant_id = %s AND id = %s""",
            (lead["tenant_id"], party_id),
        )
        subject = cur.fetchone()

    if subject is None:
        raise fleet.LeadFailed("no such party", permanent=True)
    if not subject["vec"]:
        raise fleet.LeadFailed("this party has no profile embedding yet — embed it first",
                               permanent=True)
    if subject["party_class"] == "alias":
        # Already resolved to somebody. Not a failure: the lead was queued before the
        # merge was accepted, and there is simply nothing left to do.
        return fleet.Outcome(summary="already an alias")

    # Equality on every prefix column, so this is a `vector search` with `prefix spans`.
    # `party_class = 'counterparty'` excludes aliases for free — the same property that
    # keeps them out of R1.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, name,
                      profile_embedding <=> %s::VECTOR(1024) AS distance
                 FROM party
                WHERE tenant_id = %s
                  AND embedding_model = %s
                  AND party_class = 'counterparty'
                  AND contact_state = 'contactable'
                  AND id != %s
                ORDER BY profile_embedding <=> %s::VECTOR(1024)
                LIMIT 10""",
            (subject["vec"], lead["tenant_id"], subject["embedding_model"],
             party_id, subject["vec"]),
        )
        neighbours = list(cur.fetchall())

    written = 0
    for neighbour in neighbours:
        if float(neighbour["distance"]) > MERGE_DISTANCE:
            break                      # ordered by distance; the rest are further still

        payload = {"alias_id": str(neighbour["id"]), "canonical_id": str(party_id),
                   "alias_name": neighbour["name"], "canonical_name": subject["name"],
                   "distance": float(neighbour["distance"])}

        # Confidence falls off across the window rather than being a flat number, so an
        # operator sorting the queue sees the near-certain ones first.
        confidence = max(0.5, 1.0 - float(neighbour["distance"]) / MERGE_DISTANCE * 0.5)

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO suggestion (tenant_id, party_id, kind, payload,
                                           confidence, rationale, source_lead_id)
                   VALUES (%s, %s, 'merge', %s, %s, %s, %s)""",
                (lead["tenant_id"], party_id, psycopg.types.json.Jsonb(payload),
                 confidence,
                 f"profiles are {neighbour['distance']:.3f} apart — "
                 f"'{neighbour['name']}' may be '{subject['name']}'",
                 lead["id"]),
            )
        written += 1

    return fleet.Outcome(
        summary=f"{written} merge{'' if written == 1 else 's'} proposed",
        facts=0, dropped=len(neighbours) - written)
```

Then extend `REGISTRY` at the bottom of `agents.py`:

```python
REGISTRY: dict[str, fleet.Agent] = {
    "embed_document": embedder,
    "map_source": map_source,
    "find_counterparties": find_counterparties,
    "profile_party": profile_party,
    "embed_party": embed_party,
    "dedup_party": dedup_party,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python -m pytest tests/test_dedup.py -q`

Expected: PASS, 6 tests.

- [ ] **Step 5: Have `embed_party` queue a dedup check**

A newly embedded party is exactly when a duplicate check is worth running, and `Outcome.follow_on` is the only way one agent reaches another. In `embed_party`, replace the final `return` statement with:

```python
    return fleet.Outcome(
        summary=f"embedded {provider.model}", facts=0, calls=1,
        tokens_in=embed.estimate_tokens([row["body"]]), cost_usd=cost,
        leads=1,
        # A party is worth checking for duplicates exactly when it becomes comparable,
        # which is now. No agent is named here: a row is written and whoever handles
        # `dedup_party` claims it.
        follow_on=[{"kind": "dedup_party", "party_id": party_id,
                    "target": str(party_id), "scope_kind": "party",
                    "reason": "newly embedded — check for an existing row"}])
```

- [ ] **Step 6: Confirm the follow-on lead's shape matches what `fleet.complete` inserts**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && grep -n "for follow in outcome.follow_on" -A 25 rtf_platform/fleet.py`

Check every column the `INSERT` names is a key the dict above provides or that the insert defaults. If `fleet.complete` requires a key not present — `adapter`, `platform`, `mode` — add it to the dict with the value the other agents use for a party-scoped lead. Do not change `fleet.complete`.

- [ ] **Step 7: Run the whole suite**

Run: `cd "$(git rev-parse --show-toplevel)/platform/web" && .venv/bin/python -m pytest tests -q`

Expected: **93 passed, 37 skipped** with `DATABASE_URL` unset. With `DATABASE_URL` set all 37 run: **130 passed, 0 skipped**.

- [ ] **Step 8: Commit**

```bash
git add platform/web/rtf_platform/agents.py platform/web/tests/test_dedup.py
git commit -m "platform: R3 proposes, a human disposes, and an embedding queues the check"
```

---

### Task 8: Run it against the real roster, and correct the README

The plan's only task with no new code. It is here because the three false claims in `platform/README.md` are the kind that get copied into a submission.

**Files:**
- Modify: `platform/README.md`

- [ ] **Step 1: Run the deduplicator over the live counterparties**

```bash
cd "$(git rev-parse --show-toplevel)/platform/web"
DATABASE_URL="$(grep -m1 '^DATABASE_URL=' ../../.env | cut -d= -f2-)" .venv/bin/python - <<'PY'
import os, psycopg
from psycopg.rows import dict_row
from rtf_platform import agents, spend

conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row)
gate = spend.Gate.open(None, None)
with conn.cursor() as cur:
    cur.execute("""SELECT id, tenant_id, name FROM party
                    WHERE party_class = 'counterparty' AND profile_embedding IS NOT NULL""")
    parties = list(cur.fetchall())

for p in parties:
    lead = {"id": None, "tenant_id": str(p["tenant_id"]), "party_id": str(p["id"]),
            "target": str(p["id"]), "kind": "dedup_party"}
    print(f"{p['name']:45} {agents.dedup_party(conn, lead, gate).summary}")
PY
```

Expected: the Amanda rows propose merges; the Deezer editors do not propose merges with each other. **If every party proposes a merge with every other, `MERGE_DISTANCE` is too loose for this corpus** — record the actual distances and tighten it before continuing, rather than shipping a queue that is noise.

- [ ] **Step 2: Read the proposals**

```bash
psql "$DATABASE_URL" -c "SELECT confidence, rationale FROM suggestion \
  WHERE kind = 'merge' AND state = 'pending' ORDER BY confidence DESC"
```

- [ ] **Step 3: Correct `platform/README.md`**

In the "Where we are" table, replace the `Embeddings` and `Retrieval` rows with:

```markdown
| Embeddings — 18 counterparty profiles | **live**, via the OpenAI adapter |
| `party_chunk` — the document corpus | **empty.** `005` dropped `artist_chunk` and nothing has re-ingested; `chunk_semantic` indexes 0 rows |
| Retrieval — R1 shortlist, R2 lessons | **live**, both reranked together in `agents.shortlist` |
| Deduplication — R3 | **live**, proposes into `suggestion`; merges are reversible |
| Merge safety — §3c across aliases | **live**, `012a`: the collision index is on `canonical_party_id`, not the row |
```

Under "Still open", replace the bullet beginning `**`party_fact.embedding` is still NULL.**` with:

```markdown
- **`party_chunk` is empty, and an earlier version of this file said otherwise.**
  The line claiming "856 chunks across 17 documents carry real vectors" described
  `artist_chunk`, which migration `005` dropped. `party_chunk` has never been
  populated: `SELECT count(*)` returns 0, and `chunk_semantic` therefore indexes
  nothing. `party_document` has 20 rows, so the corpus exists and only the ingest
  needs re-running. **The claim was wrong for two days and was found by counting, not
  by reading** — which is the second time on this project that a verification done in
  a database that was later dropped outlived the database. Verify against the cluster
  you are shipping.

- **`party_fact.embedding` is still NULL** — 4 rows, none embedded.

- **The §3c collision index is shipped, and it took two migrations to stay true.**
  `010_outreach.sql` created it on `thread (tenant_id, counterparty_id)`. `012` then
  made an alias and the party it aliases two rows for one person — at which point an
  index counting rows stopped enforcing a rule about people, and both could hold an
  open thread. `012a` moved it to `canonical_party_id`. The lesson is worth keeping
  visible: **a uniqueness guarantee is only as good as the identity it counts**, and
  identity in this schema is now a resolved value rather than a primary key.
```

- [ ] **Step 4: Verify no other file repeats the corrected claims**

Run: `cd "$(git rev-parse --show-toplevel)" && grep -rn "856" --include="*.md" --include="*.py" . | grep -v node_modules | grep -v "/build/"`

Fix every hit, including the architecture poster generator if it names the number.

- [ ] **Step 5: Commit**

```bash
git add platform/README.md
git commit -m "docs: what the cluster actually contains, counted rather than remembered"
```

---

## Self-review

**Spec coverage.** §3a `lesson` → Task 1. §6 rerank and its explainability → Tasks 2, 4. §4a `dedup_party` → Task 7. §4a-i reversible alias → Tasks 5, 6. §2a's three false claims → Task 8. §8's testing list, for the items in scope: `supersedes_id` chains → Task 3; `model` equality → Task 4; propose-never-merge → Task 7; merge round-trip, alias invisibility, one-level chains → Task 6.

**Pulled into scope by the revision.** `thread.canonical_party_id`, the index move, and `merge_party`'s open-thread conflict check were deferred when `thread` did not exist. It does, so they are Task 5a and Task 6, and the deferral notes that used to carry them have been deleted rather than left to read as still-true.

**Not in this plan, and not deferred to a comment either** — `message.cites_lesson_ids`. Spec §3b wants a draft to name the lessons that produced it, and `message` shipped without the column while `outreach.draft()` shipped without the concept. Adding the column here would leave it permanently empty, because the only writer lives in the other branch. It belongs to whichever session next touches `draft()`, and it is recorded in the spec rather than here.

**Not covered by any task, and named so it is not mistaken for covered:** the union-over-aliases read in `repo` (spec §4a-i) has no consumer until the console renders a merged party, so it is not built here. `party_chunk` repopulation is spec §7 step 6.
