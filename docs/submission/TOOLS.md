---
title: "Which CockroachDB and AWS tools this uses, and how"
subtitle: "The submission checklist asks for one page naming the tools. This is that page. Every claim on it was executed against the running cluster and the deployed account on the date below, not read from a spec."
status: "SUBMISSION — verified 2026-08-11 against cluster `respect-the-funk` and AWS account 821135790223; revised 2026-08-13, and every revision is a downgrade. The MCP server stops being called core, Bedrock's unavailability is measured rather than asserted, and three things that exist as code are listed as not running."
date: "2026-08-13"
---

## The system in one paragraph

**Spindle** is an agentic OS for a record label. A fleet of agents takes an
artist's catalogue to the people who can place it — playlist editors, radio
programmers, curators. No agent calls another agent. Work exists because a row in
CockroachDB says it does, and an agent picks it up by taking a lease on that row. The
database is not where the agents keep their notes; it is the thing that makes them run.

**Cluster:** `respect-the-funk` · `ae38b92e-c1ad-4a06-a247-489cd5ce9964` ·
CockroachDB **v26.2.5** · AWS `us-east-1` · plan **BASIC**, `node_count: 0` (scales to zero).

**Live demo:** <https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/>

---

## CockroachDB — 3 of the 4 listed tools

### 1. Distributed Vector Indexing — **core**

Four vector indexes, all cosine, all with the business filters *inside the index prefix*
rather than applied afterwards:

| Index | Table | Prefix columns before the vector |
|---|---|---|
| `party_shortlist` | `party` | `tenant_id`, `embedding_model`, `party_class`, `contact_state` |
| `chunk_semantic` | `party_chunk` | `tenant_id`, `model` |
| `lesson_semantic` | `lesson` | `tenant_id`, `model`, `scope_kind` |
| `fact_semantic` | `party_fact` | `tenant_id`, `model`, `status` |

This is the load-bearing design decision, so it is worth being precise about what it
buys. "Find me curators for this artist" is never a pure nearest-neighbour question — it
is *nearest neighbour among this tenant's contactable counterparties, embedded by this
model*. Putting those four predicates in the index prefix means the vector search is
performed **inside** the filtered subspace. A post-filter would search the whole space
and then throw most of it away, which at multi-tenant scale is both slower and wrong: it
returns fewer than `k` rows whenever the filter is selective.

`EXPLAIN` on the live cluster, run today, showing the filters as *spans on the index*:

```
• lookup join
│ table: party@party_pkey
└── • vector search
      table: party@party_shortlist
      target count: 20
      prefix spans: [/'1f9e6dd3-…'/'openai:text-embedding-3-small'/'counterparty'/'contactable'
                   - /'1f9e6dd3-…'/'openai:text-embedding-3-small'/'counterparty'/'contactable']
```

**This shape is asserted by a test, not by a comment.** `apps/spindle/web/tests/test_vector_plans.py`
parses `EXPLAIN` output and fails if the plan stops using the vector index — which is how a
plan regression introduced by migration 012 was caught. A query that silently degrades to a
full scan still returns correct-looking rows, so the only way to defend it is to assert the
plan.

Retrieval in practice — the live shortlist for the artist *Hallow Youth*, top 5 of 20:

```
0.5445  Yannick - Soul & Funk Deezer Editor
0.5654  Laeti - Deezer Dance & EDM Editor
0.5658  Yannick - Deezer Jazz & Blues Editor
0.5843  Yannick - Deezer Deutschland Editor
0.5880  Laeti - Deezer Pop Editor
```

### 2. Cloud Managed MCP Server — **used in development, not part of the application**

`.mcp.json` points an MCP client at `https://cockroachlabs.cloud/mcp`, scoped to this
cluster by the `mcp-cluster-id` header. It answered `list_clusters`, `list_databases` and
`list_tables` during the 2026-08-10 audit, and it is how a developer asks this cluster
questions in English instead of writing the query. Authentication is OAuth, held in the
client's own auth store, so no bearer token is committed.

**It is deliberately not billed as core, and the earlier version of this page billed it
as core.** Nothing the fleet does and nothing the console serves passes through MCP; take
it away and the application is unchanged. A judge who tested that would find the gap, and
a development convenience presented as a product capability is exactly the move that
loses the sponsor-relevance argument this whole page is making. The load-bearing
CockroachDB capability is the one above — plus the four beneath the fold in *features
beyond the four*, which are the actual reason this is not Postgres.

### 3. `ccloud` CLI — **used for something real**

`apps/spindle/bin/ccloud-mcp-setup.sh` installs `ccloud`, uses it to **resolve this project's
cluster ID by name**, and writes the `.mcp.json` above from the result. The cluster ID is
a thing the MCP configuration actually needs, so `ccloud` is on the setup path rather than
being a command run once in order to claim it.

### 4. Agent Skills repository — **not used**

Optional, and not used. Recorded here so the list is complete rather than flattering.

### CockroachDB features beyond the four

These are not on the hackathon's tool list, but they are why the memory layer is correct:

- **Serializable isolation, by default.** `SHOW default_transaction_isolation` →
  `serializable`. Agents race for the same rows; the fleet's correctness argument rests on
  the database's, not on application-level locking.
- **`SELECT … FOR UPDATE SKIP LOCKED`** — how a worker claims a lead without blocking every
  other worker behind it. This is the whole scheduler.
- **`AS OF SYSTEM TIME`** — time-travel queries. On 2026-08-10 eighteen counterparties were
  deleted, and the loss was diagnosed by querying the table as it stood an hour earlier. No
  audit table, no snapshot restore, no support ticket.
- **Multi-column partitioning on `tenant_id`**, which is the leading column of every index in
  the schema, enforced by a lint that is a real predicate check rather than a substring match.

---

## AWS — 2 of the listed services, both deployed

### AWS Lambda — the application runs *on* it

Two functions, both live in `us-east-1`:

| Function | Package | Memory | Role |
|---|---|---|---|
| `spindle-prod-console` | Zip, `python3.13` | 512 MB | The console and its API. FastAPI, served through Mangum. |
| `spindle-prod-classifier` | Container image (ECR) | 3008 MB | Discogs-EffNet genre classification of masters. |

The console is a Lambda **Function URL** (`aws_lambda_function_url.console`,
`function_url_auth_type = "NONE"`), so the demo URL above is the function itself — there is
no API Gateway and no always-on container in front of it. Combined with a CockroachDB BASIC
cluster at `node_count: 0`, the entire system costs **cents** while nobody is using it —
and the cents are worth naming rather than rounding away, because they are the whole of the
idle bill: ECR charges per GB-month for the classifier image whether or not the function is
ever invoked. Everything else genuinely is zero at rest. Total measured agent spend across
every run this system has ever made is $0.005296.

The classifier is a container Lambda because the model needs 3 GB and native dependencies
that do not fit a zip bundle. Terraform: `aws_ecr_repository.classifier`,
`aws_lambda_function.classifier`, with an IAM policy letting the console invoke it and read
the masters bucket.

### Amazon S3 — where the audio lives

`spindle-prod-masters`, defined in `infra/terraform/spindle/main.tf`: public access blocked,
server-side encryption, versioning, a lifecycle policy, and a CORS rule without which the
browser's presigned upload fails.

Masters are **content-addressed by SHA-256** — the object key is the hash of the audio, so
uploading the same master twice cannot produce two rows, and the `analyse_recording` agent
re-verifies the hash after download before it will write a fact derived from those bytes.

```
masters/1f9e6dd3-…/master/e7ade43c6be692dd25c06f127a4a28165eda68eb14a7affc8e47a254973d34be.mp3
```

### Supporting AWS, not on the list

IAM (per-function roles, least privilege), CloudWatch Logs (one log group per function),
ECR (the classifier image). All of it is Terraform in `infra/terraform/spindle/` — there is no
console-clicked resource in the deployment.

### Amazon Bedrock — written, and unreachable on this account

Earlier planning documents in this repository say *"Bedrock as agent runtime and embedding
provider."* **That is not what runs, and it is not something a support ticket away from
running by the deadline.** Measured on 2026-08-13 against account `821135790223`, not read
from documentation:

- **On-demand inference is hard zero.** Service Quotas `L-26C560CE` — *on-demand model
  inference requests per minute for Amazon Titan Text Embeddings V2* — has an applied value
  of `0.0`, and is marked **`Adjustable: false`**. There is no pending increase because
  there is no increase to request. Model access is granted and the model is `ACTIVE`; the
  capacity behind it is nil, and 6/6 invocations throttled in `us-east-1`.
- **Batch inference has quotas and no entitlement.** `CreateModelInvocationJob` returns
  `ValidationException: Your account is not authorized to perform this action. Please
  create a support case` — reproduced in `us-east-1` and `us-west-2`, with a real IAM role
  and a real bucket, and with the caller's IAM simulated as `allowed`. The gate fires
  before AWS looks at the role, so it is an entitlement and not a trust policy. Service
  Quotas publishes the batch limits for every account whether or not the capability is
  switched on, which is why a quota table reads as capability and is not.

`spindle/bedrock.py` implements both paths anyway, because the entitlement is a switch
AWS throws rather than work anyone here can do, and an untested S3-and-polling pipeline is
the wrong thing to be writing on the day it clears. But **no Bedrock call has ever produced
a row on this cluster.** The only embedding model that has is
`openai:text-embedding-3-small`. The AWS requirement is satisfied by **Lambda and S3**, and
this page says so rather than claiming a service the account would contradict.

---

## How memory is integral rather than supplementary

The brief's load-bearing clause is *"memory as integral to agent functionality — not
supplementary."* Three properties of this system are the answer:

**1. Agents never call each other.** There is no queue, no broker and no orchestrator. An
agent finishes by writing rows — including, often, a new `lead` row with a `next_action_at`.
That row *is* the message. A lead becomes work when its time passes and a worker claims it
with `FOR UPDATE SKIP LOCKED`. Take the database away and the fleet does not slow down; it
ceases to have a control flow at all.

**2. The work is not done until the memory is.** An agent's memory write, its `agent_run`
record, and its lead's completion land in **one transaction**. A crash cannot mark work
complete while losing what the work learned, and it cannot record a lesson for work that did
not finish. This is enforced by structure: `NetworkAgent` splits into a `fetch` that may do
I/O and a `write` that may not, and `write_prepared` deliberately opens no transaction of its
own so it composes into the caller's.

**3. Refusing to run agents inside a request.** `fleet.expedite` will not execute an agent in
an HTTP handler, even though that is the most tempting shortcut in the codebase. "Run now" in
the console moves a lead's `next_action_at` forward; it does not run anything. The rule that
work happens only via the memory row is enforced against its own worst case.

**Provenance is a first-class column.** Every fact is `measured`, `inferred` or `asserted`,
and an inferred party never becomes a contact without a human accepting it from the
`suggestion` queue. The memory records not just what is known but on what standing it is
known — which is what makes it safe for an agent to act on.

---

## What is honestly not there yet

Stated here because a page like this is worth nothing if a judge finds the gap themselves:

- **The lesson-accumulation loop has not run in production.** `lesson`, `party_chunk` and
  `thread` hold zero rows. The write path exists (`agents.distil_lesson`), the retrieval and
  arithmetic re-ranking exist and are unit-tested (`apps/spindle/web/tests/test_lessons.py`), and
  `lesson_semantic` is indexed and ready — but no closed thread has yet taught a shortlist,
  because nothing has been sent.
- **The changefeed is built and not created.** `SHOW CHANGEFEED JOBS` returns **zero rows**,
  and that is the deliberate state rather than an unfinished one.
  `spindle/changefeed.py` composes the exact `CREATE CHANGEFEED` (three tables,
  `initial_scan = 'no'`, a batched webhook sink and a shared secret), parses the delivered
  batches, maps a change to the lead kinds it can make claimable, and ships the Lambda
  handler and a `--verify`. Nothing in it runs the statement — it prints it. Creating the
  feed draws request units *continuously* against a free allowance, which is a spend a
  human authorises rather than a step in a deploy. Until then the fleet wakes on
  `next_action_at` and a lease, which is slower and not weaker: the feed carries no work,
  only permission to look, so a fleet with it switched off is the same fleet.
- **Multi-region domiciling is written and not provisioned.** `contact_route` holds personal
  data for named individuals, migration `024_regional_by_row.sql` makes it `REGIONAL BY ROW`,
  and `infra/terraform/multiregion/` provisions the three-region Standard cluster it needs.
  `terraform validate` passes; nothing has been applied. **`SHOW REGIONS` on this cluster
  returns `aws-us-east-1` and nothing else.** It stays unclaimed for a reason worth stating:
  a region cannot be removed from a CockroachDB Cloud cluster once added, so demonstrating
  this means a throwaway cluster and never a conversion of the one holding the system of
  record. It is the strongest architectural argument this project does not have.
- **Podcasts are a channel with an adapter and no worker.** `podcastindex.py` and migration
  `023_podcast_source.sql` are in; the `source_manifest` row lands disabled because the
  source cannot be called without a key, and the `agent_manifest` row lands disabled because
  the stage has no entry in `agents.REGISTRY` yet. Nothing has been ingested. Radio is the
  channel that is real.
- **Small N, and the one number that is not small has never been worked.** One tenant,
  three roster artists, two recordings. The counterparty index is the exception —
  fourteen thousand rows from public registers — and it is also the part with nothing
  downstream of it: `thread` and `lesson` hold zero rows, so not one of those fourteen
  thousand has been contacted, and nothing has been learned from contacting them. A large
  index is not evidence of a working loop, and this page is not going to present it as
  one. Every number here is labelled with its N where it appears, and no improvement curve
  is drawn through any of them.

## How to verify any of this yourself

```bash
# The plan really is a vector search, not a scan
psql "$DATABASE_URL" -c "EXPLAIN SELECT id FROM party@party_shortlist
  WHERE tenant_id=… AND embedding_model='openai:text-embedding-3-small'
    AND party_class='counterparty' AND contact_state='contactable'
  ORDER BY profile_embedding <=> '[…]'::vector LIMIT 20;"

# Isolation is not a claim
psql "$DATABASE_URL" -c "SHOW default_transaction_isolation;"

# The two things this page says are NOT running. Both are one command to falsify,
# which is why they are named here rather than left for you to find.
psql "$DATABASE_URL" -c "SHOW CHANGEFEED JOBS;"   # zero rows
psql "$DATABASE_URL" -c "SHOW REGIONS;"           # aws-us-east-1, and nothing else

# The changefeed statement that would be run, printed rather than executed
python -m spindle.changefeed --verify

# The shortlist, end to end
python -m spindle.ingest --shortlist hallow-youth

# What every agent run has ever cost, in total
psql "$DATABASE_URL" -c "SELECT sum(cost_micro_usd)/1e6 FROM agent_run;"   -- $0.005296
```
