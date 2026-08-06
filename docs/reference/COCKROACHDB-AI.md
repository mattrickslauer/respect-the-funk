---
title: "Reference — CockroachDB's AI surface, and the constraints it actually imposes"
subtitle: "The product features, the real vector-index syntax, and the one documented limitation that changes a query in PLATFORM-SPEC. Captured locally so the build is not designed against the marketing page."
status: "REFERENCE — captured 2026-08-06 from cockroachlabs.com. Not a plan. Vendor docs move; re-check before relying on a limit."
date: "2026-08-06"
---

## ⚠️ Read this first — the constraint that changes our design

CockroachDB's vector index accelerates a filter **only when the filter is on a prefix
column and uses equality or `IN`**. From the vector index documentation:

> "Index acceleration with filters is only supported if the filters match prefix columns."
> Range comparisons prevent index use: `WHERE category_id >= 200` will not utilize the
> vector index.

[`PLATFORM-SPEC §6`](../PLATFORM-SPEC.md)'s R1 query was specified as an ANN search filtered
by tenant, kind, **freshness**, and **the absence of an open thread**. Of those:

| Predicate | Form | Index-accelerated? |
|---|---|---|
| `tenant_id = $1` | equality | ✅ prefix |
| `kind = 'creator'` | equality | ✅ prefix |
| `last_refreshed_at > now() - '90 days'` | **range** | ❌ post-filter only |
| no open thread (`NOT EXISTS …`) | **subquery** | ❌ post-filter only |
| measured demographics (join) | **join** | ❌ post-filter only |

Left as written, three of five predicates degrade to post-filters, which means over-fetching
`LIMIT k × n` and discarding — the classic filtered-ANN problem, and it gets worse as the
index grows.

**The fix is to denormalise the predicates into one equality column** maintained in the same
transaction that already opens and closes threads:

```sql
ALTER TABLE counterparty ADD COLUMN contact_state STRING NOT NULL DEFAULT 'contactable';
-- contactable | in_thread | stale | declined | unusable

CREATE VECTOR INDEX counterparty_shortlist
    ON counterparty (tenant_id, kind, contact_state, profile_embedding
                     vector_cosine_ops);
```

R1 then becomes all-equality and fully accelerated:

```sql
SELECT id, handle, display_name
  FROM counterparty
 WHERE tenant_id = $1 AND kind = 'creator' AND contact_state = 'contactable'
 ORDER BY profile_embedding <=> $2
 LIMIT 50;
```

`contact_state` flips to `in_thread` in the transaction that inserts the `thread` row — the
same transaction the partial unique index already guards — and back on close. `stale` is set
by a sweeper as `last_refreshed_at` ages out. This trades a denormalised column for index
acceleration, and the denormalisation is safe precisely because it is written in the same
serializable transaction as the fact it mirrors.

## Vector indexes — the real syntax

```sql
-- required before any vector index can be created
SET CLUSTER SETTING feature.vector_index.enabled = true;
-- on a non-empty table, also:
SET sql_safe_updates = false;

CREATE VECTOR INDEX ON items (embedding);                          -- simplest form
CREATE VECTOR INDEX ON items (prefix_a, prefix_b, embedding);      -- with prefix columns
CREATE VECTOR INDEX embed_idx (embedding vector_cosine_ops) ON items;
CREATE VECTOR INDEX ON items (category, embedding)
  WITH (min_partition_size=16, max_partition_size=128);
```

**Column type** `VECTOR(n)`. Documented examples use `VECTOR(512)` and `VECTOR(1536)`; no
maximum is stated. Our schema uses `VECTOR(1024)` for text and `VECTOR(512)` for faces.

**Distance operators**

| Operator | Metric | Use |
|---|---|---|
| `<->` | L2 | true geometric distance — spatial models |
| `<=>` | **cosine** | **semantic similarity and RAG — this is ours** |
| `<#>` | negative inner product | when magnitude *and* direction matter |

`PLATFORM-SPEC` did not name an operator. It is **cosine** (`vector_cosine_ops`, `<=>`) for
both `profile_embedding` and `lesson.embedding`.

**Documented limitations**

- Large batch inserts degrade the index — **do not batch vector writes.** This affects the
  day 3–5 backfill of the synthetic counterparty corpus; insert in small batches.
- `IMPORT INTO` is **unsupported** on tables carrying a vector index. Backfill via `INSERT`.
- Filter acceleration only on prefix columns with equality/`IN` — see above.
- `vector_l1_ops`, `bit_hamming_ops`, `bit_jaccard_ops` are not implemented.
- Index recommendations are unavailable for vector indexes.

⚠️ **Unverified:** whether `SET CLUSTER SETTING feature.vector_index.enabled` is permitted on
a **Basic** (serverless) cluster, where cluster settings are generally restricted. If it is
not settable, either it is on by default there or the tier is wrong. **Check on day 1 —
this is a go/no-go for the whole retrieval design.**

## Managed MCP Server

| | |
|---|---|
| Endpoint | `https://cockroachlabs.cloud/mcp` |
| Auth | Native CockroachDB Cloud authentication, RBAC and SQL proxy. Operated by Cockroach Labs. |
| Setup | A single config snippet from the Cloud Console |
| Clients | Claude Code, Cursor, VS Code / GitHub Copilot |
| Default posture | **Read-only**, with full audit logging and no custom proxy |
| Optional | When explicitly enabled: create databases, create tables, insert rows |

Exposed tools: list databases and tables, describe schemas and indexes, inspect cluster
health and running queries, run read-only SQL and `EXPLAIN`.

**For us** this is the label-facing natural-language read path over the spine — *which tracks
have no campaign, which threads are stalled, what did this channel cost per agreement.*
Read-only default is the correct posture for that and needs no relaxing.

## The rest of the AI surface

| Feature | What it is | Use here |
|---|---|---|
| **Distributed vector indexing** | Semantic search that stays fast as data scales, without single-node degradation | R1, R2 — core |
| **Agentic Skills repo** | Curated, machine-executable CockroachDB capabilities for agents | Optional; the 4th tool if time allows |
| **`ccloud` CLI** | Cluster provisioning, backups, networking. Noun-verb, JSON output on every command. | Day-1 provisioning — free to claim |
| Developer plugins | For Claude and for Cursor, on GitHub | Development convenience, not submission surface |
| LangChain provider | CockroachDB vector store + **chat message history** | ⚠️ Evaluate before writing our own persistence — but note their chat-history abstraction models a conversation, not our thread state machine, so it likely does not fit. |
| Google MCP Toolbox | Multi-source reasoning across databases | Not needed |

**Vendor claims worth not repeating uncritically:** "seamless scale from first agent to
fleet, without re-architecting" and "unmatched resilience for agent memory that never goes
down." Both may be true and neither is load-bearing for us — our argument is consolidation
and correctness, and borrowing a scale claim we cannot demonstrate would undercut it.

## Free-tier facts, carried from the workload model

- Basic **starts at $0/month** and **scales to zero**.
- **50M Request Units + 10 GiB storage free per month** ($15 of consumption).
- Basic is sized for "smaller, bursty applications which require up to 30K RU/second."
- Distributed vector index is available on Basic.
- **Changefeeds are available on Basic and consume RUs** — verified 2026-08-06 against the
  Basic cluster planning page. Third-party claims that serverless disables changefeeds date
  from 2021–22 and are stale.
- Regions cannot currently be removed once added.

⚠️ Still unpublished, and therefore still stated as headroom rather than spend: the RU cost
of a filtered vector scan, and the RU cost of a continuously running changefeed. Both are
day-1 measurements. Full model in [`infra/MEMORY-WORKLOAD.md`](../../infra/MEMORY-WORKLOAD.md).

## Sources

*All read 2026-08-06.*

- <https://www.cockroachlabs.com/product/ai/> — the AI product surface
- <https://www.cockroachlabs.com/docs/stable/vector-indexes> — index syntax, operators, limitations
- <https://www.cockroachlabs.com/docs/cockroachcloud/plan-your-cluster-basic> — Basic tier, free allowance, changefeed RU consumption
- <https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/> — MCP endpoint, auth, tools
- <https://www.cockroachlabs.com/docs/stable/cockroachdb-and-ai> — overview
