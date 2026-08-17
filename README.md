<p align="center">
  <img src="./docs/brand/thumbnail.png" width="760"
       alt="Spindle — a vinyl record drawn as a radial audio spectrum, over the wordmark Spindle and the line The tool that makes your music go round">
</p>

<p align="center">
  <b>An agentic OS for a record label.</b><br>
  <a href="https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/"><b>Live demo</b></a> ·
  <a href="./docs/submission/SUBMISSION.md">Submission narrative</a> ·
  <a href="./docs/2026-08-11-sponsor-audit.md">Adversarial sponsor audit</a> ·
  <a href="./docs/architecture/spindle-architecture.pdf">Architecture, in five pages (PDF)</a> ·
  <a href="./docs/PLATFORM-SPEC.md">Platform spec</a>
</p>

<p align="center"><sub>
  CockroachDB × AWS Hackathon — Agentic Memory · cluster <code>respect-the-funk</code>,
  CockroachDB v26.2.5, AWS <code>us-east-1</code>, plan BASIC (scales to zero) ·
  no sign-in needed for anything linked below
</sub></p>

---

## The one sentence

> **We map the music industry into a vector index, mail real human beings one at a time,
> and CockroachDB is the only reason we can prove why we did it.**

A label with a new single has to find the few hundred people, out of tens of thousands,
who might actually play it. Get that wrong and you are spam. Spindle indexes those people
— **43,191 radio stations, curators and shows, from public registers** — works out who
should hear a given record, and writes to each of them individually. The fleet takes
irreversible real-world actions: it emails named music directors. So the interesting
question is never *what does the agent know*. It is **why did the agent decide that,
then** — and that question is what the database is for.

## For a judge, in five minutes

Everything here is public, live against the cluster, and needs no account. Numbers are
queried on page load, not typed into the page.

| Look at | What it is | What it proves |
|---|---|---|
| **[`/`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/)** | Landing page. Every counter across the top is a query run as the page loads. | The index is real: 43,191 counterparties, 14,139 embedded, 29,667 carrying a genre (measured 2026-08-15). |
| **[`/#tune`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/#tune)** | Move the dial, watch a shortlist rerank. | Decision provenance. The ranking is a vector query you are running, not a recorded animation. |
| **[`/#plan`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/#plan)** | The `EXPLAIN` output, in full. | `vector search` with **prefix spans** — the tenant, model and contact-state filters are *inside* the index, not a post-filter. |
| **[`/#send`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/#send)** | The send gate and the outbox. | An email is the one thing that cannot be taken back. This is the schema that makes a double-send impossible rather than unlikely. |
| **[`/#verdict`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/#verdict)** | Where Postgres would have done fine, feature by feature. | We are only claiming the parts that are actually CockroachDB-shaped. |
| **[`/manual`](https://3r6xfixrxgw3tzzvnpmmqkrbsy0egypm.lambda-url.us-east-1.on.aws/manual)** | The operator manual, written for label staff. | What the product is when it is not being pitched. |

If you read one document, read
**[`docs/2026-08-11-sponsor-audit.md`](./docs/2026-08-11-sponsor-audit.md)** — it is this
project arguing *against itself* about whether CockroachDB is load-bearing here, and it is
where the claims below were cut down to the ones that survived.

## Why CockroachDB, in one query

Memory here is not static. Lessons accumulate and rerank shortlists, genres land and change
embeddings, `contact_state` moves as threads open. A station ranked 3rd on Tuesday and 15th
today — and the fleet emailed a real person on the strength of the Tuesday ranking.

Answering *"why did you email this station?"* in Postgres means building an event-sourcing
subsystem: temporal tables, a versioned copy of every embedding, an audit log of every
rerank — and it can only ever answer the questions you thought to log in advance.

Here it is the same query with four words added:

```sql
SELECT name FROM party@party_shortlist AS OF SYSTEM TIME '-10m'
 WHERE tenant_id = $1 AND embedding_model = 'openai:text-embedding-3-small'
   AND party_class = 'counterparty' AND contact_state = 'contactable'
 ORDER BY profile_embedding <=> $2 LIMIT 3;
```

A **filtered vector search against the index as it existed at a past timestamp.** Not a
snapshot table, not an audit trail — the actual index, actual embeddings, actual `lesson`
rows, as of then, with no schema written to support it and no write-path cost. pgvector
cannot do this at any price. That is the argument, and it is the whole argument.

The second piece is the send gate, and it is deliberately ordinary SQL carrying the whole
load. `outbox` carries `UNIQUE (message_id)`, so a message cannot be queued twice;
`message` carries `UNIQUE (tenant_id, idempotency_key)`; and a partial unique index allows
one open thread per counterparty. Senders claim rows under a lease, so two workers cannot
take the same one. `apps/spindle/web/tests/test_sender.py` asserts this by name —
`test_a_second_outbox_row_for_one_message_is_structurally_impossible`,
`test_two_senders_never_claim_the_same_outbox_row`.

## What is actually running

Three states, and the third is the one that matters. **live** — running against the
cluster. **built** — the code exists, is tested, and nothing has run it in anger.
**written, not created** — the code exists and turning it on is a deliberate act somebody
has decided not to take, for a reason recorded in the audit. The third is not a softer
version of the second: it means one command finds nothing, and every document here is
required to agree with what that command returns.

| Piece | State |
|---|---|
| The counterparty index — FCC, Radio Browser, Wikipedia | **live** — 43,191 rows |
| Vector indexes on `party`, `party_chunk`, `party_fact`, `lesson` | **live**, cosine, prefix-filtered; the plan is asserted by `test_vector_plans.py`, not by a comment |
| Time-travel retrieval (`AS OF SYSTEM TIME` over the vector index) | **live** — the query above, verified on the cluster |
| The console — thirteen views, every one reading the cluster | **live**, behind sign-in |
| Outreach: `campaign`, `thread`, `message`, `outbox`, and the send gate | **live** |
| The agent fleet — lease claiming, backoff, follow-on leads, `agent_run` | **live**, 15 agents in `agents.REGISTRY` |
| Masters — content-addressed by SHA-256 in S3, hash verified on read | **live** |
| Genre classification — Discogs-EffNet in a container Lambda | **live**, validated against six reference tracks |
| Embeddings | **live**, via OpenAI. Not Bedrock — see the audit for why not |
| The changefeed — statement, consumer, Lambda handler, `--verify` | **written, not created.** `SHOW CHANGEFEED JOBS` returns zero rows |
| `REGIONAL BY ROW` on `contact_route` (migration 024, `infra/terraform/multiregion/`) | **written, not created.** `SHOW REGIONS` returns `aws-us-east-1` and nothing else |

### What we are not claiming

**Multi-region data domiciling.** It would be the strongest architectural argument
available and it is not available on this cluster: BASIC is single-region, and CockroachDB
Cloud cannot remove a region once added, so converting the cluster holding the system of
record would be a one-way door taken for a demo. The migration and the Terraform exist and
validate; nothing has been applied. A claim a judge can falsify with one `SHOW REGIONS` is
worse than no claim. Same rule for the changefeed. Both are in the roadmap, named as
future work, in [the audit](./docs/2026-08-11-sponsor-audit.md) §3.

## On AWS

One deploy, and it is free at idle. FastAPI + Jinja + htmx in a **Lambda behind a Function
URL** — no ALB, no always-on container. Masters live in **S3**, content-addressed. The
genre classifier is a **container-image Lambda** from **ECR**, because `essentia-tensorflow`
does not fit a zip and the work is seconds of CPU per track, sporadically — so it scales to
zero like the rest. Sign-in codes and outreach go out over **SES**. Terraform for all of it
is in [`infra/terraform/spindle/`](./infra/terraform/spindle/), and the priced workload
models that chose the shape are in [`infra/`](./infra/).

## The repository

```
apps/spindle/     the platform — schema/, web/ (console + fleet), bin/
apps/remixkit/    the asset generator, a subproject
content/          the RemixKit asset pipeline and its five format specs
infra/            Terraform, and the priced workload models
docs/             specs, research, audits, submission, showcase
```

| | What's in it | Start at |
|---|---|---|
| **[`apps/spindle/`](./apps/spindle/)** | The platform. Migrations in `schema/`, applied in order by `apply.py`; the console and the agent fleet in `web/`. | [`apps/spindle/README.md`](./apps/spindle/README.md) |
| **[`apps/remixkit/`](./apps/remixkit/)** | The asset generator — FastAPI, ports and adapters. A subproject, not the product. | [`apps/remixkit/README.md`](./apps/remixkit/README.md) |
| **[`infra/`](./infra/)** | AWS as Terraform, plus [`platform-architecture.pdf`](./infra/platform-architecture.pdf) — the design on eight pages, ending in the risk register. | [`infra/README.md`](./infra/README.md) |
| **[`docs/`](./docs/)** | [`SCOPE-RESET.md`](./docs/SCOPE-RESET.md) and [`PLATFORM-SPEC.md`](./docs/PLATFORM-SPEC.md) are the two binding documents. [`research/`](./docs/research/) is 14 numbered reports on the outside world. [`showcase/`](./docs/showcase/) is fifteen screens of the running console. | [`docs/SCOPE-RESET.md`](./docs/SCOPE-RESET.md) |

Specs live next to what they describe: `content/`'s five format specs stay in `content/`
because `content/bin/` parses them, and `infra/`'s workload models stay in `infra/`.
`docs/` is for what describes the product.

## Running it

```bash
cp .env.example .env          # OPENAI_API_KEY, AWS creds, and the rest

# DATABASE_URL is deliberately not in .env.example — it is a cluster you create.
# docs/runbooks/environments.md §3 has the `ccloud` commands that make one.
export DATABASE_URL='...'
for f in apps/spindle/schema/0*.sql; do
    python apps/spindle/schema/apply.py "$(basename "$f")" || break
done

cd apps/spindle/web
python -m venv .venv && .venv/bin/pip install -r requirements.txt
./dev.sh                                     # http://localhost:8099
```

`apply.py` is the only thing that runs a migration, and it refuses any `DROP` not
declared in its `DESTRUCTIVE` table — so a broken ordering stops rather than eats
something.

Tests:

```bash
cd apps/spindle/web && .venv/bin/python -m pytest -q
# 788 passed, 342 skipped — the skipped ones need a disposable cluster.
```

The suite refuses to start against the production cluster and says so; the fence and the
reasoning are in [`apps/spindle/web/tests/conftest.py`](./apps/spindle/web/tests/conftest.py).
`docs/runbooks/environments.md` has the one command that creates the cluster it will run
against.

## Three names, and they are not the same thing

- **Spindle** is the platform — what this repository builds.
- **Respect the Funk** is the record label that runs on it: tenant #1, not the product.
  It is why `tenant_id` and the live cluster still carry that name.
- **RemixKit** is the asset generator inside the platform, a subproject.

Renamed 2026-08-15. Before that the repository, the product and the label shared one name,
which made every sentence about "Respect the Funk" ambiguous between a company and a
codebase.

## For maintainers

[`docs/SCOPE-RESET.md`](./docs/SCOPE-RESET.md) is binding and says which of the older
documents are reference and which are void — take nothing in a void one as fact or plan.
`PRODUCT.md`, `BUILD-SPEC.md`, `MEMORY-SPEC.md`, `MINDS-SPEC.md` and `PIPELINE-SPEC.md`
were deleted on 2026-08-15; source comments still cite them by section for design
rationale, and `git show e6c6bd4:docs/PRODUCT.md` recovers one when you need to know *why
existing code looks the way it does*. Never for what to build next.

The mark above is `docs/brand/thumbnail.svg` — see [`docs/brand/README.md`](./docs/brand/README.md)
before re-rendering it, and `apps/spindle/web/spindle/templates/_mark.html` for the same
mark as it appears in the product.
