# Root-cause remediation — invariants that execute

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Replace five classes of defect at their root, rather than patching sixteen sites.

**The thesis:** every finding in `.superpowers/audit/` is a place where a document states a rule and nothing enforces it. The remediation is to move each rule out of prose and into a type, a constraint, or a test. Where a rule cannot be enforced, the code must raise rather than continue.

**Tech Stack:** Python 3.13/3.14, psycopg 3, CockroachDB v26.2.5, unittest under pytest.

## Global Constraints

- **NO FALLBACKS.** No silent default, no `.get(k, default)` covering a broken contract, no `getattr(o,'a',d)`, no `or ""`, no `except: pass`, no quiet `return` on an unexpected state, no clamp standing in for a rejection. Fail loudly.
- **Fail-CLOSED refusal is the opposite and is wanted.** `spend.Gate` refusing a paid call, `auth` returning ANONYMOUS with no token, `TransitionRefused` — all correct, none are defects.
- **Do not add prose that asserts an invariant.** If you cannot enforce it in code, say so in the report instead of writing a comment that claims it.
- Tests: `cd platform/web && .venv/bin/python -m pytest tests -q`. Baseline entering this plan: **98 passed, 20 skipped**.
- The cluster is SHARED and LIVE. Migrations additive only.

## Scope boundary — read before starting

This worktree contains `agents.py`, `repo.py`, `fleet.py`, `lessons.py`, `distributors/`, `schema/`. Those are safe to change.

`outreach.py`, `research.py` and `routes.py` exist ONLY in the main tree, uncommitted, owned by another session. **They are out of scope.** Their findings are recorded in §6 for whoever owns that tree. Do not edit them, do not recreate them here.

---

### Task A — a parse boundary for adapter output (Root A)

**Kills:** provenance defaulting to `measured`/`inferred`, `unit` defaulting to `count`, `release_type` defaulting to `single`, `presence.mode` defaulting to `owned`, and `_to_int`/`_to_money` returning 0.

**The design.** Adapters currently return `dict` and callers guess at missing keys. Introduce one module, `rtf_platform/harvested.py`, holding frozen dataclasses for each item an adapter can emit — `Identifier`, `Fact`, `Metric`, `Recording`, `Release`, `Presence`. Each has a classmethod `parse(raw: dict, *, adapter: str)` that:

- requires every field the database requires, and raises `HarvestInvalid(adapter, field, raw)` naming the adapter and the missing field when one is absent;
- **never defaults a label.** `provenance`, `unit`, `release_type` and `mode` are required. An adapter that cannot say what it measured has produced an item we cannot store;
- validates `provenance` against the three legal values and raises otherwise.

`map_source` and `repo.accept_suggestion` then consume parsed objects, not dicts. The `.get(..., default)` calls disappear because there is nothing left to default.

`distributors/base.py`'s `_to_int`/`_to_money` raise `StatementUnparseable(column, cell)` instead of returning 0. `statements.load` already refuses to write unless an operator has confirmed the format; an unparseable cell now fails that import loudly instead of recording zero revenue as measured fact.

**Why a dataclass and not a dict with validation.** A validated dict is still a dict at the next call site, and the next author will `.get()` it. A type makes the missing field unrepresentable rather than merely checked once.

- [ ] Write `tests/test_harvested.py` first: each parse rejects a missing required field with the adapter and field named; each accepts a complete record; provenance outside the three values raises; `_to_int`/`_to_money` raise on unparseable input and still parse the formats real statements use (thousands separators, currency symbols, parentheses-negatives).
- [ ] Run them; watch them fail.
- [ ] Implement `harvested.py`, then rewrite `map_source`, `repo.accept_suggestion` and `distributors/base.py` to use it.
- [ ] Grep the touched files for `.get(` and justify every survivor in the report.
- [ ] Full suite green; commit.

---

### Task B — the fleet's unit of work (Root C)

**Kills:** the triple execution. Measured on the live cluster: three identical `party_metric` rows (`fans=0`, provenance `measured`) for one party, and one `find_counterparties` lead with three `agent_run` "ok" rows before reaching `done`.

**The mechanism.** `db.connect` is autocommit. `fleet.work_once` claims a lead, runs the agent, calls `record_run`, then calls `complete`. Those are separate commits. A failure or a slow path between them leaves the lead claimable again while its work has already been committed — so it runs again, and its writes are duplicated.

**The fix.** The agent's writes, the `agent_run` row and the lead's completion commit together or not at all. Restructure `work_once` so a single `with conn.transaction()` spans agent execution, `record_run` and `complete`. Where an agent performs a non-transactional side effect (a network fetch), that side effect must happen *before* the transaction opens and its result be passed in — the transaction may not span a network call.

**Also required, and it is the part that makes this stick:** a test that proves the duplicate cannot recur. Insert a lead whose agent writes one row and then raises; assert that after the failure the row is absent AND the lead is claimable — not that the row exists and the lead is stuck.

- [ ] Write the failing test first, against the cluster, in a tenant dropped in tearDown.
- [ ] Fix `work_once`. Do not add a `try/except` that swallows — a failure must propagate after rollback.
- [ ] Verify no lease is held across a network call.
- [ ] Report whether the existing three duplicate `party_metric` rows should be cleaned up; do NOT delete live data without asking.

---

### Task C — make the vector-index rule executable (Root E)

**Kills:** `agents.retrieve()`'s full scan, and the whole class.

Two vector queries have now been found full-scanning because a JOIN was attached to the vector-searched table. The constraint is documented in `docs/reference/COCKROACHDB-AI.md` and the codebase hit it twice regardless. Documentation is not a guard.

- [ ] Fix `agents.retrieve()` (agents.py:172-183) with the same CTE restructure already proven in `shortlist()`: the vector search runs alone, `party_document.title`/`url` come from scalar subqueries over the already-limited rows.
- [ ] Add `tests/test_vector_plans.py`: for EVERY query in the codebase using `<=>`, run `EXPLAIN` against the cluster and assert the plan contains `vector search`. Cluster-gated by `DATABASE_URL`, tenant dropped in tearDown. Cover `agents.shortlist`, `agents.retrieve`, `lessons.retrieve_for`.
- [ ] Prove each new test fails against the pre-fix query shape before accepting it.
- [ ] The test must FAIL, not skip, if a query it names has been renamed or deleted — a plan assertion that silently stops covering anything is worse than none.

---

### Task D — tenant scoping and relationship coherence (Roots B and D)

- [ ] **Tenant guard.** Three lookups in `agents.py` fetch by bare `id` without `tenant_id`. Add the predicate. Then add `tests/test_tenant_scoping.py`: parse every `cur.execute` string in `rtf_platform/*.py`, and for each naming a tenant-scoped table, assert `tenant_id` appears in the statement. Maintain the tenant-scoped table list in one place. This is a lint expressed as a test — it is the only thing that stops the next omission.
- [ ] **`delete_party` clears lessons.** `011_lesson.sql`'s comment already claims it does; make the claim true. Add a test asserting a deleted party leaves no `lesson` row with `scope_kind='party'` and its id.
- [ ] **`supersedes_id` is populated.** `map_source` marks rows `status='superseded'` and never sets `supersedes_id`, leaving orphans — measured: 3 of 4 live `party_fact` rows. Set it in the same statement that supersedes, so status and relationship cannot disagree.
- [ ] **Refuse a self-supersede.** `ALTER TABLE party_fact ADD CONSTRAINT fact_no_self_supersede CHECK (supersedes_id IS NULL OR supersedes_id != id)`, same for `lesson`, as migration `013_supersede_integrity.sql`. A cycle needs more than a CHECK; a self-reference does not, and `lessons.heads()` drops every row on one.

---

### §6 — Out of scope here, for the owner of the main tree

Recorded so they are not lost. All in files this worktree does not contain.

| Finding | File | Severity |
|---|---|---|
| `advance()` reaches `queued` without an outbox row — `ALLOWED` says which transitions are legal but not which require a side effect. `queued` should be reachable only via `approve()`. | `outreach.py`, `routes.py:978` | Critical (integrity) |
| `campaigns()`, `threads()`, `approvals()` — `message`/`outbox` subqueries omit `tenant_id`; EXPLAIN confirms FULL SCAN | `research.py` | Critical |
| `budgets()` — `coalesce(..., 20000)` fabricates a spend cap indistinguishable on screen from a configured one | `research.py` | Important |
| `budgets()` — `agent_run` subqueries omit `tenant_id`, defeating `run_spend` | `research.py` | Critical |
| `artists()` — N+1, two queries per roster row | `research.py` | Important |
| `record_reply()` — `(cur.fetchone() or {}).get("state","")` silently no-ops a transition | `outreach.py:359` | Important |
| `create_campaign`/`open_thread` accept `party_id`/`counterparty_id` without checking tenant ownership | `outreach.py:105,141` | Important |
