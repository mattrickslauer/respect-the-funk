---
title: "Audit — what the code did versus what it said"
subtitle: "A security, performance and integrity audit of platform/, its four root causes, and the remediation. Findings verified against the running cluster, not read from documentation."
status: "FINDINGS — remediation complete for everything reachable from the lessons-and-alias-merge branch. Four decisions are outstanding, listed at the end."
date: "2026-08-09"
---

## The one-sentence result

**This codebase states its invariants in prose — docstrings, migration comments, specs —
and the prose has been wrong repeatedly.** Sixteen findings reduced to four root causes,
and every one of them is a place where a document made a promise nothing enforced. The
writing is unusually good, which is exactly why it went unexamined: it reads as
enforcement.

Nine documented claims were found false during this audit. Four of them were inside
`harvested.py`, the file written to stop the problem. One was inside the tenant-scoping
lint, which as first written had precisely the hole it existed to close.



## Remediation progress

Plan: docs/superpowers/plans/2026-08-09-root-causes.md (32cde9a) — five roots, not
sixteen patches. Root under the roots: this codebase states its invariants in prose
(docstrings, migration comments, specs) rather than in types, constraints or tests, and
the prose has been wrong at least three times (README's 856 chunks, 011's delete_party
claim, map_source's "written through unchanged").

| Root fix | State |
|---|---|
| B — fleet unit of work | **DONE** — 194b972 + ee1337d + aaa4061, three rounds, final review: all findings addressed. Safe to run. |
| A — adapter parse boundary | **DONE** — e4c49f3 + 55abfb7, 2 rounds, re-review clean |
| C — EXPLAIN tests + agents.retrieve() | **DONE** — f60ee10/6f6cad3/16b1ed7 + 4868821, 2 rounds |
| D — tenant lint, constraints, delete_party, supersedes_id | in progress |

Tests: 77 at the start of the session → 147 offline / 187 against the cluster.

Eleven reviews, every one of which found something real. Two found defects that would
have shipped. One disproved a concern I raised (the index hint does NOT blind the plan
test — hint+JOIN hard-errors instead). One found four false claims inside `harvested.py`,
the file written to eliminate false claims.

The single most instructive finding: migration 012 — landed in this branch, hours before
— silently changed the query plan of R1, the headline retrieval. Nobody touched that
query. Only an EXPLAIN assertion noticed.

Deferred minors from root fix B, folded into root fix C's dispatch:
- `_reacquire`'s docstring still says it is "not required for the correctness
  `complete`'s own fence already provides". Round 3 proved the opposite: CockroachDB's
  `now()` is transaction-scoped, so `complete`'s expiry fence CANNOT fire on the
  `work_once` path and `_reacquire` is the real protection. A false claim in a docstring,
  in the module this audit's root cause is named after.
- When `fail`/`_defer` raise `LeaseLost`, the `agent_run` row carrying the agent's real
  error rolls back and `lead.last_error` is overwritten with lease-loss text. The
  operator loses the underlying cause from the one row they read.

### Root fix B, three rounds — a worked example of the whole problem

1. `194b972` — one transaction spans agent write + `record_run` + `complete`. Closed the
   measured crash-between-commits duplication. Review found it left a second path open
   with the identical symptom: lease expiry during `fetch`.
2. `ee1337d` — ownership fence on `complete`/`fail`/`_defer`, re-checked at the top of
   the write transaction; `Gate.incurred_usd` so cost survives a rollback. Review
   confirmed all four findings addressed, and found the fence introduced a LIVELOCK.
3. In progress — back off on lease-loss, per-lead leasing, and a per-claim lease token.

**The livelock, because it is instructive.** On lease-loss with no contender,
`_record_lease_lost` deliberately leaves the lead untouched: `pending`,
`next_action_at` in the past, `attempts` not incremented. `drain` loops while
`work_once` returns non-zero. So the lead is re-claimed and re-fetched immediately and
forever, paying the provider every pass, and `MAX_ATTEMPTS` never parks it because
`fail` is never reached. Not an edge case: `claim` stamps ONE expiry for a batch of 5
processed sequentially, so at 20-30s of HTTP per fetch the 4th and 5th lead of a normal
batch begin with a dead lease.

**The fence key is also not a worker identity.** It matches `owner_agent`, whose CLI
default is the constant `"ingest-cli"`. Two concurrent drains are indistinguishable, so
A's re-acquire passes on B's lease and both write — the original bug surviving through
the fence built to stop it.

Three rounds, each fixing the previous round's blind spot, all in the module whose
docstring calls itself "the coordination primitive". This is the cost of the root cause:
the guarantees were written in prose and never executed, so nobody knew which ones were
real until each was tested.

AWAITING THE OWNER:
- 3 duplicate `party_metric` rows and several leads with 2-3 `agent_run(ok)` rows remain
  on the live cluster — the corruption the fleet bug already caused. Not deleted; live
  data is the owner's call.
- §6 findings (research.py, outreach.py, routes.py) cannot be fixed from this worktree.
- Task 5a of the lessons plan is still hard-blocked.


Requested by the owner: "remove all fallbacks... audit the fuck out of every line of
code... security, and optimization audit or find otherwise bad architecting and more."

## Coverage, and a dispatch error worth recording

My shell session runs inside the worktree `.claude/worktrees/lessons-and-alias-merge`.
Subagents inherit that as their working directory, so the first three auditors read the
WORKTREE (an older checkout) even though their prompts named the main tree. One of them
explicitly discarded `outreach.py` as "contamination from a different checkout".

Consequence: findings on files common to both trees are valid. Findings on main-tree-only
work did not happen. Re-dispatched with a mandatory `cd` as the first command.

| Audit | Tree actually read | Status |
|---|---|---|
| fallbacks.md | worktree | valid for shared files; missed outreach.py |
| security.md | worktree | valid for shared files; missed outreach.py |
| performance.md | worktree | valid for shared files; missed outreach.py |
| data-integrity.md | unknown — still running | check its scope on return |
| security-outreach.md | main tree (hard-scoped) | running |
| outreach-fallbacks-perf.md | main tree (hard-scoped) | running |

## Confirmed findings so far

### CRITICAL — the code invents the most confident value when the source is silent
One pattern, five sites. Not one of them defaults to "unknown".

| Site | Invents | Why it is severe |
|---|---|---|
| `agents.py:241,255,264,277` | `provenance` = `measured` | highest-trust class; participates in the `fact_one_live_per_dimension` uniqueness key, and the supersede UPDATE uses the same defaulted value in its WHERE, so a missing label writes to the wrong slot AND supersedes the wrong row |
| `distributors/base.py:99-116` | unparseable money/counts = `0`, written `measured` | silently zeroes real revenue from the label's own distributor statements. README already says no column map has been checked against a real export, so the first real import is when this fires |
| `repo.py:307` | `presence.mode` = `owned` | strongest ownership claim in the system, invented from a missing key, in a rights context |
| `agents.py:341` | `release_type` = `single` | wrong classification into durable storage |
| `agents.py:276` | `metric.unit` = `count` | a number's dimension invented; followers, streams and percentages become indistinguishable |

### CRITICAL — a second vector query does not use its index
`agents.retrieve()` (agents.py:172-183), the R2 corpus search the README calls live,
full-scans because it JOINs `party_document` onto the vector-searched `party_chunk`.
Proved with three EXPLAINs. Identical bug class to the `shortlist()` one fixed in Task 4,
and the identical CTE fix applies. Wrong at any size. Masked today only because
`party_chunk` holds 0 rows.

### CRITICAL — tenant_id omitted, defeating an index on an unbounded table
`research.budgets()` (research.py:316-334): two `agent_run` subqueries filter on
`party_id` alone, so `run_spend` (leading column `tenant_id`) cannot be used. Full scan
of a table that grows with every agent run forever.

### CRITICAL — N+1 inside one SERIALIZABLE transaction
`statements.load()`: one SELECT/INSERT per line. A 3,000-line DSR statement is ~7,500
sequential round trips in a single transaction.

### Lower, but real
- `research.tracks()` — `lead.recording_id` and `party_fact.recording_id` have no index
  at all; guaranteed full scans.
- `research.artists()` — N+1 on presence and pending_suggestions per roster row, while
  `research.today()` in the same file does the equivalent correctly in one grouped query.
- `agents.py` — three lookups by bare `id` skip the `tenant_id` predicate every other
  query carries.
- `011_lesson.sql`'s comment claims `repo.delete_party` clears lesson rows. It does not.
  Scheduled as Task 6 Step 4, which is currently blocked behind 5a — so the comment is
  false in the meantime. Third time this repo has shipped a comment ahead of the code.
- `POST /demo` still unthrottled; `ccloud-mcp-setup.sh` downloads a binary with no
  checksum.

## Judged defensible, explicitly not defects
`spend.py` in full, `auth.py` (returning ANONYMOUS with no admin token configured is
fail-CLOSED and correct), `domain.py`, `fleet.py`'s per-lead fault isolation,
`lessons._bounded`, and `outreach.TransitionRefused`. Refusing to act is the opposite of
a fallback and is wanted.
