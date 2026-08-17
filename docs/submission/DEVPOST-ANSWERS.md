---
title: "Devpost submission form — the three short answers"
subtitle: "Integration, start date, pre-existing work. Sourced from TOOLS.md and NOTICE so the form and the repository cannot drift apart."
status: "SUBMISSION — every claim here is one already verified in docs/submission/TOOLS.md against cluster `respect-the-funk` and AWS account 821135790223."
date: "2026-08-16"
---

## How the CockroachDB and AWS components are integrated

**CockroachDB is the control plane, not the storage layer.** No agent in the fleet calls
another agent. There is no queue, no broker, no orchestrator. An agent finishes by writing
a row with a `next_action_at`, and that row *is* the message — the next agent picks it up
by taking a lease with `SELECT … FOR UPDATE SKIP LOCKED`. Take the database away and the
system doesn't slow down, it loses its control flow entirely. Each agent's memory write,
its run record and its lead completion land in one serializable transaction, so a crash
can't mark work done while losing what the work learned.

**Distributed vector indexing is the core tool.** Four cosine vector indexes, each with
the business filters *in the index prefix* rather than applied afterwards — `party_shortlist`
is keyed on `tenant_id`, `embedding_model`, `party_class`, `contact_state` before the vector.
"Find curators for this artist" is never a pure nearest-neighbour question; it's nearest
neighbour *inside this tenant's contactable counterparties*. Putting the predicates in the
prefix means the search happens inside the filtered subspace instead of scanning everything
and discarding it, which at multi-tenant scale is both slower and wrong — a post-filter
returns fewer than `k` rows whenever the filter bites. The plan shape is asserted by a test
that parses `EXPLAIN` and fails if it degrades to a scan (`test_vector_plans.py`), which is
how a regression from migration 012 got caught.

**`AS OF SYSTEM TIME` is what makes the system answerable.** Every outreach decision is
stamped with the hybrid logical clock at which it was made, so "why did you email this
person" replays the same shortlist against the memory as it actually stood that night —
not today's rankings wearing last night's clothes. No audit tables, no snapshots. When a
decision falls outside the GC window it refuses to answer and says why, rather than
improvising. Time travel also recovered 18 counterparty rows deleted on 2026-08-10 by
querying the table an hour earlier.

Also used: the **Cloud Managed MCP Server**, honestly — for natural-language inspection of
the cluster during development, not by the application; and the **`ccloud` CLI**, which
resolves the cluster ID that the MCP config needs, in `bin/ccloud-mcp-setup.sh`.

**AWS: Lambda and S3, both deployed and both load-bearing.** The whole console and its API
run as a Lambda behind a Function URL — the demo URL *is* the function, no API Gateway, no
always-on container. The genre classifier is a second, container-image Lambda (3 GB, ECR)
that the console invokes. With a BASIC cluster at `node_count: 0`, the system costs cents
at idle. S3 (`spindle-prod-masters`) holds the audio, content-addressed by SHA-256 so the
same master can't produce two rows, and the analysis agent re-verifies the hash after
download before writing any fact derived from those bytes. Supporting: IAM per-function
roles, CloudWatch Logs, ECR, SES for sign-in mail, Route 53 and ACM. All Terraform; nothing
was clicked in the console.

Bedrock is implemented in `spindle/bedrock.py` and doesn't run: on-demand inference for
Titan Embeddings V2 is quota-zero and non-adjustable on this account, and batch inference is
entitlement-gated behind a support case. No Bedrock call has ever produced a row here, and
we'd rather say that than claim a service the account would contradict. Embedding is a port
with two adapters; OpenAI is the live one.

## Start date

**07-26-26** — the repository's initial commit. The agentic-memory platform that is the
actual submission was first committed **08-06-26**. Both are inside the submission period.

## Pre-existing code

RemixKit — `content/` and `apps/remixkit/` — is an asset-generation pipeline and its
console, built for a different, unrelated hackathon and kept in this repository as a
subproject. It is not part of the agentic-memory work being submitted; the submission is
`apps/spindle/` and `infra/`. The split is written down in `NOTICE` and `docs/SCOPE-RESET.md`.

Otherwise: standard frameworks and libraries (FastAPI, Jinja, htmx, Mangum, psycopg,
Terraform, boto3, Stripe), AI coding assistants, and two third-party assets — Essentia's
pre-trained `discogs-effnet` / `genre_discogs400` models for genre classification, and
Natural Earth 1:110m country outlines (public domain) reduced and vendored as
`world.json`. The counterparty index is built from public registers, not scraped from
anyone's private data. No other pre-existing code.
