---
title: "Hackathon audit — what the cluster says, nine days out"
subtitle: "The submission scored against the CockroachDB × AWS criteria, with every claim checked against the running cluster rather than the documentation. One finding is a data loss; one is a scoring gap that no amount of writing can close."
status: "FINDINGS — 2026-08-10. Deadline 2026-08-18 17:00 EDT, nine days out. Three decisions are outstanding, listed in §7."
date: "2026-08-10"
---

## The one-sentence result

**The architecture is the best thing here and the evidence for it is missing:** the
memory layer that the headline criterion asks about is designed correctly, indexed
correctly, tested correctly, and holds **zero rows**.

Everything the technical third of the video claims is true about the *code*. Two of the
things it claims are true about the *cluster* are not, and one of them is the money shot.

---

## 1. Hard requirements — met

Verified this session against cluster `respect-the-funk`
(`ae38b92e-c1ad-4a06-a247-489cd5ce9964`), CockroachDB **v26.2.5**, AWS `us-east-1`,
plan BASIC, `node_count: 0`.

**CockroachDB — at least 2 of 4 required. Two verified.**

| Tool | State | Evidence |
|---|---|---|
| **Distributed Vector Indexing** | **verified** | Three vector indexes exist and are *used*. `EXPLAIN` on the live cluster returns a `vector search` node with `prefix spans` for both R1 and lessons — captured in §3. |
| **Cloud Managed MCP Server** | **verified** | `.mcp.json` is configured and the server answered `list_clusters`, `list_databases` and `list_tables` during this audit. |
| `ccloud` CLI | claimed, unverified | Provisioning path. Not exercised this session. |
| Agent Skills repo | not used | Optional. |

**AWS — at least 1 required. One verified.**

`aws_lambda_function_url.console` in `platform/infra/main.tf:152`, served by Mangum
(`platform/web/rtf_platform/handler.py`). The console runs *on* Lambda; this is not a
bolt-on.

> **One correction to `docs/reference/HACKATHON.md`.** It records the intent as
> *"Bedrock as agent runtime and embedding provider."* The cluster disagrees: the only
> embedded party carries `embedding_model = 'openai:text-embedding-3-small'`.
> `BedrockEmbedder` exists in `embed.py` and has never produced a row here. The AWS
> requirement is satisfied by **Lambda**, not Bedrock — say Lambda in the submission and
> do not say Bedrock unless a Bedrock-embedded row exists by shoot day.

**Submission checklist**

- [x] Public repository, **Apache-2.0**, licence auto-detected
- [x] Pre-existing code disclosed in `NOTICE` (RemixKit, `content/` and `app/`)
- [x] Functional demo URL — Lambda Function URL, `function_url_auth_type = "NONE"`
- [ ] **Video under 3:00, public** — not shot
- [ ] **Documentation naming the CockroachDB and AWS tools used and how** — scattered
      across `PLATFORM-SPEC.md` and `reference/`; there is no single page a judge can be
      pointed at. Cheap to fix and explicitly scored.
- [x] Architectural diagram (optional) — generated, seven pages

---

## 2. THE FINDING — eighteen counterparties were deleted, and they are recoverable

At **2026-08-10 ~04:40 UTC** every counterparty in the cluster was deleted, with its
entire footprint. Discovered by time-travelling the `party` table, not by any alarm.

| table | now | −60m | delta |
|---|---|---|---|
| `party` | 3 | 21 | **−18** |
| `party_role` | 3 | 21 | **−18** |
| `presence` | 3 | 21 | **−18** |
| `party_document` | 2 | 20 | **−18** |
| `lead` | 13 | 31 | **−18** |
| `agent_run` | 52 | 68 | −16 |
| `thread` | 0 | 1 | −1 |

What remains is three `roster` parties — Amanda Kurt, Hallow Youth, Just One Branch —
and nothing to take them to. **`party_class` is now 100% `roster`. The shortlist has
zero candidates and returns an empty list.**

**A full snapshot was taken before the GC window closed** and is at
`/home/mattricks/rtf-snapshot-2026-08-10/` — 30 tables, 222 rows, one JSON file per
table, vectors serialised as strings. The `AS OF SYSTEM TIME` window measured ~75–80
minutes (`-75m` resolved, `-90m` failed the GC threshold), so the cluster itself can no
longer be asked. **The snapshot is now the only copy.** Do not delete it.

### The deletion was plausibly deliberate, which is why it has not been undone

Of the 18 deleted counterparties, **five are real Deezer editors** and thirteen are
junk from a name-match harvest against the artist "Amanda Kurt":

| Keep — real editors | Junk — name-match noise |
|---|---|
| `Laeti - Deezer Dance & EDM Editor` | `Amanda` ×2, `Amanda Goncalves`, `Amanda Gonçalves` |
| `Laeti - Deezer Pop Editor` | `Amanda Rocha da Silva`, `Petra Liina Amanda Suokorpi` |
| `Alexandre - Pop & Hits Editor` | `Kurt`, `marinen30`, `fredmorin`, `Adr_07` |
| `Camojada - Deezer Latin Editor` | `tasqquechosedslesoreilles`, and two more |
| `Rudy - Deezer Moods Editor` | |

That pattern reads as a **deliberate cleanup of a bad harvest**, not an accident — which
is exactly why restoring all 18 unilaterally would be wrong. See §7 decision 1.

`Laeti - Deezer Dance & EDM Editor` is the row the video script names by name.

---

## 3. What is verified true, and worth filming

These were checked against the cluster this session and hold.

**Serializable by default.** `SHOW default_transaction_isolation` → `serializable`.

**Three vector indexes, with the business filters inside the index prefix.** This is the
strongest technical asset in the repository and the script undersells it:

```
party.party_shortlist  (tenant_id, embedding_model, party_class, contact_state,
                        profile_embedding vector_cosine_ops)
party_chunk.chunk_semantic  (tenant_id, model, embedding vector_cosine_ops)
lesson.lesson_semantic      (tenant_id, model, scope_kind, embedding vector_cosine_ops)
```

Live `EXPLAIN` for R1 — the filters are *spans on the index*, not a post-filter:

```
• vector search
    table: party@party_shortlist
    target count: 20
    prefix spans: [/'1f9e6dd3-…'/'openai:text-embedding-3-small'/'counterparty'/'contactable'
                 - /'1f9e6dd3-…'/'openai:text-embedding-3-small'/'counterparty'/'contactable']
```

**`AS OF SYSTEM TIME` works, and the demo is now genuinely dramatic** — because of §2.
`SELECT count(*) FROM party AS OF SYSTEM TIME '-60m'` returned **21** while the same
query at `-30s` returned **3**. Real time travel, over real destroyed data, with no audit
table. It was a "needs one console control to be showable" item; it is now the most
compelling thirty seconds available.

**Scale to zero.** BASIC plan, `node_count: 0`. Total spend across all 52 agent runs
ever recorded: **$0.0053**.

**The tests are real.** 239 passed + 14 subtests, in 5m58s, **against this cluster** —
including `test_vector_plans.py`, which asserts the `EXPLAIN` shape and is the reason the
R1 plan regression of migration 012 was caught at all.

**The suggestion queue is live and filmable today.** Eight rows, confidence 0.3 and 0.7,
rationale *"deezer matched on name — needs a human to confirm"*, in `accepted`,
`rejected` and `superseded` states. The "inferred never becomes a contact" beat needs no
restore.

---

## 4. Scored against the five criteria

### Agentic Memory Design — the tie-breaker, and the biggest gap

The *design* is the best answer in the repository to the brief's load-bearing clause
(*"memory as integral to agent functionality — not supplementary"*):

- Agents never call each other. A lead becomes work when `next_action_at` passes and a
  worker claims it. `fleet.expedite`'s refusal to run an agent inside an HTTP request is
  this rule being enforced against its most tempting violation.
- Work is claimed by lease on the memory row itself, `FOR UPDATE SKIP LOCKED`.
- **An agent's memory write, its `agent_run` record, and its lead's completion commit in
  one transaction** (`194b972`). This is the sharpest available statement of the brief:
  *the work is not complete until the memory is.*

The *evidence* is absent:

| Table | Rows | What it means |
|---|---|---|
| `lesson` | **0** | The accumulation loop has never run. The `lesson_semantic` index has never held a row. |
| `party_chunk` | **0** | R2 corpus retrieval has never returned anything. |
| `outbox` / `message` / `thread` | **0** | Nothing has ever been sent. |

**The entire thesis — "release *n+1* is cheaper because something accumulated" — has zero
rows behind it.** A judge who opens the console sees an empty lessons screen. This is the
single highest-value thing to fix before the 18th, and it is worth more than any
rewrite of the video.

### Technical Implementation — strong

Filtered vector retrieval, lease claiming, EXPLAIN regression tests, serializable
transactions, a tenant-scoping lint that is a real predicate check. One correction:

**The changefeed does not exist.** `SHOW CHANGEFEED JOBS` returns **zero rows**. The
script's line *"A changefeed on that table wakes the next agent. No broker."* is false
today. It is also the one claim a judge can check in ten seconds. **Build it or cut the
line — do not ship it as written.**

### Real-World Impact — real premise, thin evidence

A real label, real artists, real ISRCs (`QT6F62677752`, `QT3FB2669818`), real Deezer
editors. But zero counterparties, zero messages, one campaign, no thread. The story is
true and the database does not yet corroborate it.

### Production Readiness — strong

Terraform, Lambda, scale-to-zero, a spend gate that fails closed, cost per agent run,
`tenant_id` as partition key everywhere, 239 tests against a real cluster. One blemish
a judge could see: of 52 agent runs, **14 failed and 3 were refused** — 33% non-ok,
mostly the two Spotify `map_source` leads failing for want of credentials. Either fix
the credentials or clear the failed runs before filming `/runs`.

### Creativity & Originality — strong

A fleet that takes irreversible real-world actions, in music, not another dev tool. The
provenance discipline (measured / inferred / asserted) is the most distinctive product
idea in the repository and the one a judge is most likely to repeat out loud.

---

## 5. Carried forward from the 2026-08-09 audit

Re-checked against `main` at `accee7e`:

| Prior CRITICAL | State |
|---|---|
| `agents.retrieve()` full-scans — JOIN steers the planner off `chunk_semantic` | **FIXED** — CTE shape, with a docstring saying not to simplify it back |
| `research.budgets()` omits `tenant_id`, defeating `run_spend` | **FIXED** — both subqueries now carry `r.tenant_id = a.tenant_id` |
| `statements.load()` N+1 inside one SERIALIZABLE transaction | **STILL PRESENT** — `_ensure_recordings` does SELECT+INSERT per distinct ISRC. Latent only: `statement_import` has 0 rows, so it has never run. Not a demo blocker. |
| 3 duplicate `party_metric` rows from the fleet bug | **STILL PRESENT** — 3 rows, unchanged. Owner's call. |

---

## 6. Ranked, nine days out

1. **Seed the lessons loop so `lesson` is non-empty.** Highest score-per-hour in the
   whole project. It is the tie-breaker criterion and it currently reads as unbuilt.
2. **Decide the restore** (§7.1). Without it the shortlist — the product's money shot and
   the vector index's only visible proof — cannot be filmed.
3. **Build the changefeed, or cut the line from the script.** Ten-second falsifiable.
4. **One documentation page naming the CockroachDB and AWS tools and how they are used.**
   Explicitly on the checklist, currently scattered.
5. **Surface `AS OF SYSTEM TIME` in the console.** One control. §2 just made it the best
   demo in the deck.
6. Clear or explain the 14 failed agent runs before filming `/runs`.
7. `statements.load` N+1 — after the 18th.

---

## 7. Decisions outstanding

1. **The restore.** Recommended: **restore the five real Deezer editors and their
   `party_role` / `presence` / `party_document` / `lead` rows, and leave the thirteen
   name-match profiles deleted.** That makes the shortlist filmable and honest, and keeps
   the cleanup that appears to have been intended. Restoring all 18 would put
   `tasqquechosedslesoreilles` on screen in a shortlist of music curators.
2. **The changefeed** — build, or cut from the script.
3. **`party_metric`'s 3 duplicate rows** — still awaiting the owner, carried from the
   2026-08-09 audit.

---

## Method

Every claim in §1–§3 was executed against the live cluster during this audit, via the
Cloud MCP server and via `DATABASE_URL` where MCP blocked the statement class
(`crdb_internal`, `SHOW CHANGEFEED JOBS`, `SHOW INDEXES FROM DATABASE`). Row counts are
`count(*)`, not `estimated_row_count` — the two disagreed on every table checked, and the
estimates were stale in both directions. Nothing in this document is read from a
docstring.
