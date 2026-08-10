# `platform/` — the substrate and the console

The platform described by [`docs/SCOPE-RESET.md`](../docs/SCOPE-RESET.md) and
[`docs/PLATFORM-SPEC.md`](../docs/PLATFORM-SPEC.md). Built one piece at a time.

> ## Nothing outside `platform/` may be touched.
>
> `app/`, `content/` and `infra/` are **frozen**. RemixKit is live in judging for the
> Backblaze generative-media hackathon, and the deployed console is what judges are
> looking at. This resolves `SCOPE-RESET.md` open decision 2 (repository topology) as
> *defer* — reconsider the platform-takes-over-the-repo move after both deadlines pass.

| | |
|---|---|
| `schema/` | Migrations, applied in order. Seven so far. |
| `web/` | The console and the fleet — FastAPI + Jinja + htmx, the same shape as `app/remixkit/ui/`. |
| `infra/` | Terraform. Lambda + Function URL, and nothing else that costs money. |
| `bin/` | Setup that has to touch a vendor account: `ccloud-mcp-setup.sh`. |

## Where we are

| Piece | State |
|---|---|
| `tenant`, `artist` | **live** in `defaultdb` |
| `artist.type` — band, dj, singer, orchestra, composer, … | **live** |
| Landing page at `/` + `demo_request` capture | **live** — the console is gated behind sign-in |
| Roster CRUD at `/roster` — list, search, add, edit, delete | **works locally**, not yet deployed |
| Console — thirteen views at `/`, `/facts`, `/fleet`, … | **live**, every view reads the cluster |
| Outreach — `campaign`, `thread`, `message`, `outbox` | **live**, migration 010 |
| The send gate at `/approvals` — approve, reject, queue | **live**; nothing claims the outbox, so nothing sends |
| The fleet — lease claiming, backoff, follow-on leads, `agent_run` | **live** in `web/rtf_platform/fleet.py` |
| Vector indexes on `party_chunk` and `party_fact` | **live**, cosine, prefix-filtered |
| Embeddings — 856 chunks over 17 documents | **live**, via the OpenAI adapter |
| Retrieval — R2 semantic search over the corpus | **live**, `python -m rtf_platform.ingest --search "…"` |
| Counterparties, threads, outreach | not started |

A table arrives when something needs it, not because `PLATFORM-SPEC §2` lists it.

## The console was a wireframe, and is not any more

Thirteen views in three panes — a rail with a scope switcher, a list, and a persistent
inspector. **Every one of them now reads the cluster**, and the fixtures they were built
against have been deleted rather than left beside the queries that replaced them.

The wireframe was built first on purpose: the tables did not exist, and a layout, an
information hierarchy and an inspector cannot be judged from a spec. Building the screens
first also settled what the queries had to return, which is cheaper than discovering it
after the migration.

**The bet paid.** The promise at the time was that when a table landed the fixture would
become a query and the templates would not change. Migration 010 landed the last four
tables, and not one line of `console/table.html`, `approvals.html` or `inbox.html` moved
to accommodate them. `demo.py` went from 1,583 lines to 166: the block vocabulary, the
nav, and the row selector — the parts that were never fiction. The deleted fixtures are
worth reading as a design record and are one command away:
`git show 14073d9:platform/web/rtf_platform/demo.py`.

**Live is not the same as finished, and the views say which.** A screen that reads a real
table it has no writer for is still telling the truth, provided it says so:

| View | What is real | What is absent |
|---|---|---|
| `/fleet` | `agent_manifest` cross-referenced against `agents.REGISTRY` | three declared agents have no implementation — they render `declared`, not `idle` |
| `/campaigns`, `/threads` | full state machine, created and driven from the console | no Scout, so an operator opens threads by hand |
| `/approvals` | the gate: approve writes the message and outbox rows in one transaction | no Sender claims the outbox, so nothing is sent |
| `/inbox` | `message` and its query | no inbound adapter, so the table is legitimately empty |

An empty `/inbox` that explains it has no adapter beats three invented replies. The
fixture version could not tell an operator the integration was missing; this one cannot
avoid telling them.

**Why the inspector is persistent rather than a drawer.** Every object in this product
has a *why* — a fact stands on evidence, a lead exists because another lead found it, a
draft cites lessons. The third pane is that surface, and it renders from one partial for
all thirteen views, so a fact, a lead, an agent, a budget and a failed run all get the
same treatment. Adding a view costs a builder and a route, not a screen — and the
inspector is now also where an operator *acts*, so a control is a `post` action in a
`Section` rather than a bespoke form.

| | |
|---|---|
| **Work** | `/` needs-you queue · `/approvals` the send gate · `/inbox` replies |
| **Knowledge** | `/artists` · `/tracks` · `/facts` · `/counterparties` |
| **Campaigns** | `/campaigns` · `/threads` |
| **System** | `/fleet` · `/queue` · `/runs` · `/budgets` |

**The console is also where outreach is created**, because a live view of a table nobody
can fill is a wireframe with a real query behind it, not a built screen. So `/campaigns`
creates a campaign, its inspector opens threads one counterparty at a time, a thread's
inspector writes the pitch, and `/approvals` gates it. That loop is the Scout, the
Drafter and the human gate with a person doing the first two — which is the right order:
the screen that governs an agent should work before the agent does, or there is nothing
to govern it with on the day it turns on.

One decision worth keeping visible: opening a thread is **one button per counterparty**,
not one for the batch. Opening a thread takes somebody off the market for every other
campaign — that is `§3c`'s unique index — and a bulk control would make that consequence
invisible at exactly the moment it is incurred.

## Running it

```bash
cd platform/web
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
./dev.sh            # reads DATABASE_URL from the repo-root .env; serves on :8099
```

`requirements.txt` is pinned and is the single source of truth for both installs —
`infra/build.sh` vendors the same file into the Lambda bundle, so a dependency cannot
resolve one way locally and another way in the deployment. `requirements-dev.txt` adds
uvicorn, which the deployed function never needs because Mangum invokes the ASGI app
directly.

There is no local database. CockroachDB Basic scales to zero and costs nothing idle,
so developing against the real cluster is cheaper than maintaining a second one that
drifts out of sync with it.

## Deploying it

Secrets go in `platform/infra/terraform.tfvars`, which is gitignored — not in `-var`
flags, because command-line arguments are visible in the process table to anything
running on the machine.

```hcl
# platform/infra/terraform.tfvars
database_url = "postgresql://…"   # from .env
admin_token  = "…"                # openssl rand -hex 24; this is how you sign in
```

```bash
./platform/infra/build.sh              # vendors arm64 wheels into infra/build
terraform -chdir=platform/infra init
terraform -chdir=platform/infra apply  # 5 resources: role, attachment, log group, fn, url
```

`terraform output console_url` is the demo URL `PLATFORM-SPEC §8` day 12 requires.

## What it costs

> **The rules live in [`COSTS.md`](COSTS.md), and `web/rtf_platform/spend.py` enforces
> them.** Nothing metered is enabled: `RTF_PAID_ENABLED` is unset, so every paid call is
> refused before it is made. This section covers the infrastructure; that one covers
> models, ceilings and the kill switch.

**$0.00/month idle.** Not "under a dollar" — zero. Each omission in `infra/main.tf` is
deliberate:

| Not used | Why |
|---|---|
| API Gateway | A Function URL routes a server-rendered app for free. |
| VPC / NAT gateway | A NAT is ~$32/month of idle floor, forever, visits or not. The Lambda reaches CockroachDB over public TLS, as CockroachDB Cloud expects. |
| Secrets Manager | $0.40/secret/month; Lambda env vars are already KMS-encrypted at rest for free. |
| ECR | A zip deployment has no per-GB image storage. |
| CloudFront / domain / ACM | The Function URL is working HTTPS. A domain is a decision for when something is worth pointing at. |

What can cost money: Lambda beyond the free tier (1M requests + 400k GB-seconds/month,
which this will not approach) and CloudWatch ingestion, capped by 7-day retention.

**Correction, 2026-08-07:** an earlier version of this paragraph claimed
`reserved_concurrent_executions = 10` was a hard ceiling. It is not set —
`infra/variables.tf` defaults `max_concurrency` to `-1`, because AWS refuses any
reservation that would leave fewer than 10 unreserved and this account's *total* is 10.
The ceiling is real but account-wide and shared with RemixKit, not per-function. Raising
the account quota removes it, so raise the quota and add a reservation in the same change.

**Every resource is tagged** `Project=rtf-platform`, `Env`, `ManagedBy=terraform`,
`Repo=respect-the-funk`, `Component=console`, via provider `default_tags`. Group by
`Project` in Cost Explorer to separate platform spend from RemixKit's.

## Design notes

**A band is an artist with a type.** One column, not a second table. The supported set
is `domain.ArtistType` — fourteen values in two groups, rendered as a grouped `<select>`
and enforced server-side, so the closed list is a rule rather than a UI convention.

The database column stays `STRING` rather than becoming an ENUM, which is the part
worth keeping straight now that the set is closed. An ENUM would make adding `mariachi`
an `ALTER TYPE` — a migration coordinated with a deploy, for what is really a copy
change — and, worse, a row written before a value was retired would no longer load.
So: closed where writes happen, permissive where history lives. `domain.unrecognised`
makes that concrete — an artist carrying a type this build no longer defines stays
editable, renders under a "No longer offered" group, and keeps its value unless
somebody deliberately changes it. Renaming an act never silently reclassifies it.

**The console is private. Four routes are public.** `/` serves a landing page to anyone
without a session and the needs-you queue to anyone with one — the same address either
way, so a bookmark survives getting a token. `/signin`, `POST /demo` and `/healthz` are
the other three; everything else 303s a stranger back to the landing page.

This reverses an earlier decision, and the earlier reasoning is worth keeping visible:
anonymous reads existed so a hackathon judge clicking the demo URL would land on the
product rather than a login box. **Overruled deliberately** — the roster, the counterparty
index and the campaign state are the label's own information, and judges are handed a
token instead.

The gate is a FastAPI dependency (`require_operator`), not a call at the top of each
handler, so a new console route is private by the act of annotating its principal
`Operator`. The failure mode of the other shape is the one route where somebody forgets.
The `Principal` shape is copied from `app/remixkit/auth/` (frozen, so copied not
imported), including its `tenant_id`. The shared token is not OTP and is not pretending
to be — `app/remixkit/auth/otp.py` is 61 lines and is where this goes when there is a
second operator.

**`demo_request` is the only table an unauthenticated visitor can write to**, and the
only one carrying no `tenant_id` — the whole point of the row is that the label filling
it in is not a customer yet. Field lengths are capped in `repo`, not trusted from the
form, because `maxlength` is a hint to a browser rather than a constraint on a POST.

**No seed file.** The label row is created by the first artist you save, so a fresh
cluster becomes a working one through the UI rather than a script somebody must
remember to run.

**The CSS is still structural on purpose.** The screens are live, the visual design is
not the point yet: hierarchy, state and
the light/dark split are settled; colour and character are deliberately left for a
design pass.

## Verified against the real cluster

Run against `respect-the-funk-31317` (aws-us-east-1, CockroachDB CCL **v26.2.5**).

1. **`feature.vector_index.enabled` is already `t`** — no `SET CLUSTER SETTING` needed,
   closing [`PLATFORM-SPEC §10` risk 3](../docs/PLATFORM-SPEC.md), the flagged go/no-go
   the whole retrieval design depended on.
2. **Vector-index prefix filtering works as §6's amendment predicts** — `EXPLAIN`
   resolves leading equality columns to `prefix spans` on a `vector search` node, for
   both R1 and R2 shapes. No `LIMIT k × n` over-fetch.
3. **A partial unique index enforces the §3c cross-channel collision**, and releases the
   counterparty when the thread closes.
4. `FOR UPDATE SKIP LOCKED` is supported, so §3a's lease claim works.
5. **The console works end to end**: anonymous read renders, anonymous write is refused
   with 403, an authenticated write creates the tenant and the artist, and search
   filters. `Hallow Youth` was created through the app during this test and is real.
6. **The Lambda bundle builds at 24MB** with genuine `cpython-313-aarch64` wheels
   matching the runtime and architecture Terraform declares. `terraform validate` passes.

Items 1–4 were established in a throwaway database that was dropped; a full-schema draft
covering all of `PLATFORM-SPEC §2` was written, verified and then cut back. It is not
deleted, only unshipped — `git show a6ba8bb:platform/schema/001_substrate.sql`, with its
deviations argued in `git show a6ba8bb:platform/README.md`. Reach for it when a piece
comes up, not as a plan.

## Still open

- ~~**The deployed function is running old code.**~~ **Resolved.** Checked 2026-08-09:
  the Function URL returns `200` and serves the current landing page with the auth gate,
  so the build was redeployed at some point after that note was written. The fleet code
  added since is not in the bundle, but nothing in the console depends on it yet.
- **Migrations 004 and 005 are applied.** `004` created the frontier, evidence and
  claims; `005_party_first.sql` replaced the artist-shaped root with a Party and added
  the industry's four layers — party, work, recording, release — per
  `docs/superpowers/specs/2026-08-08-party-first-identity-design.md`. `005` is
  destructive by design and `schema/apply.py` re-checks that the tables it drops are
  empty before running. `010_outreach.sql` added `campaign`, `thread`, `message` and
  `outbox` — `PLATFORM-SPEC §2d`, party-first — with `§3c`'s partial unique index, and
  seeded `agent_manifest` for the five agents in `agents.REGISTRY` plus the three that
  are declared and unwritten. **All thirteen console views now read the cluster; no
  fixtures remain.** The index was checked in both directions against the real cluster:
  a second campaign's thread on the same counterparty is refused, and closing the first
  releases them.
- **`party_fact.embedding` is still NULL.** `party_chunk.embedding` is not: 856 chunks
  across 17 documents carry real vectors, written by the fleet.

  **Correction, 2026-08-09:** an earlier version of this line said both columns were
  "indexed and empty". Only *empty* was true — there was no vector index anywhere in the
  shipped schema, and the earlier verification had been done in a throwaway database that
  was dropped. `007_vector_index.sql` creates them for real: `chunk_semantic` on
  `party_chunk (tenant_id, model, …)` and `fact_semantic` on
  `party_fact (tenant_id, model, status, …)`, both cosine. `EXPLAIN` on the retrieval in
  `agents.retrieve` resolves to a `vector search` node with `prefix spans` over the
  populated table — checked, not assumed.

  The index also forced a schema fix. `party_chunk` had an `embedding` and no way to say
  what produced it, and embeddings from two models are not comparable — the distance is a
  well-formed float and noise. `model`/`model_version` are now columns, a `CHECK` refuses
  an embedding that cannot name its model, and `model` is an equality predicate in every
  retrieval, which is also what the index prefix needs.
- **`presence` and `party_credit` have no foreign key on `subject_id`.** Both are
  polymorphic over party, recording and release, which is the price of one probe and
  one grid serving all three — so `ON DELETE CASCADE` cannot fire for either.
  `repo.delete_party` deletes presence rows itself. **Any future deleter of a party,
  recording or release must do the same**, and a deleter of a recording must also
  clear `party_credit`. This has already bitten once: an orphaned presence row
  survived a deleted party and was only found by counting. A periodic orphan sweep
  over both tables is worth writing before either grows.
- **Distributor statements are the only real stream counts.** `distributors/` reads
  exported files — DistroKid has no API, so a reader is the whole integration —
  and `statements.load` writes `recording` and `party_metric` rows from them.
  **No column map has been checked against a real export**, so `Format.verified` is
  false everywhere and the loader refuses to write until an operator confirms it per
  import. Confirming a format is a one-line change once a real file has been through
  it, and `statement_import.format_verified` records what was true at the time.
- **Bedrock is still unusable on this account, and no longer blocks anything.** On-demand
  quota is **0 requests per minute** for Titan Embeddings V2 — `ACTIVE` and access
  granted, but zero capacity, so every invoke returns `ThrottlingException`. Service
  Quotas reports the limit as `Adjustable: False`, so the self-service increase button
  does not apply and it needs a support case; one has been filed.

  It stopped being a blocker because embedding is now a **port**, not a Bedrock call.
  `embed.py` has two adapters behind one interface, selected by environment variable;
  OpenAI is the live one at 1024 dimensions (Matryoshka truncation, so it meets the
  schema rather than the schema meeting it) and Titan drops in unchanged when the case
  clears. The AWS requirement was never Bedrock's to carry anyway — Lambda already
  satisfies it and is deployed.
- **`POST /demo` has no rate limiting, no CAPTCHA and no email verification.** It is the
  one route a stranger can write through, and today nothing stops somebody filling the
  table with junk. Acceptable while the URL is unpublished and the Lambda is capped at
  `reserved_concurrent_executions = 10`; not acceptable once the address is public. The
  cheapest real fix is a per-IP limit at the edge, which the current no-API-Gateway
  topology does not have a place for — so it is a topology decision, not a code one.
- **Nothing sends, and the outbox is the proof.** `approve` writes the `message` row and
  the `outbox` row in one transaction per `§3b`, and no Sender claims from `outbox`
  because no mail provider is wired. A row sitting there in `pending` is a send that is
  fully prepared and has not happened. The gate is therefore genuinely load-bearing —
  and the button says "Approve & queue" rather than "Approve & send", because labelling
  it for what the product will eventually do is the difference between a gate and a lie.
  Deliverability was already called out as a time sink with low judging value in
  `PLATFORM-SPEC §10` risk 1; this is that decision, made visible on the screen.
- **The Drafter, Sender and Inbox agents are declared and unwritten.** They have
  `agent_manifest` rows with `enabled = false`, and `/fleet` renders an agent with a
  manifest and no implementation as `declared` rather than `idle` — one is a switch, the
  other is unwritten work, and they must not look alike. Meanwhile the operator does
  those jobs by hand through the console, which is deliberate: a governing screen should
  work before the thing it governs.
- **`/inbox` reads a real table nothing writes to.** `outreach.record_reply` is the
  writer and the tests drive it; what is missing is an inbound adapter to call it. The
  empty state says exactly that. Resisting a fixture here matters more than elsewhere —
  three invented replies would leave an operator with no way to discover the integration
  does not exist.
- **`agent_run` attributes work to the claiming worker, not the agent kind.** The CLI
  claims as one worker (`ingest-cli`) and dispatches to whichever agent the lead's kind
  selects, so all 68 runs on the cluster carry that name and every manifest row reads
  zero. `/fleet` lists unmanifested workers rather than dropping them, so the runs are
  visible somewhere — but per-agent rates are not measurable until `work_once` records
  the dispatched kind alongside the claimant.
- **Demo requests have no operator surface.** They land in `demo_request` and can only be
  read with SQL. A `/requests` view is a route and a fixture-free table read, but nothing
  reminds the operator a lead arrived, so a request could sit unseen indefinitely.
- **Dev runs Python 3.14, Lambda runs 3.13.** No 3.12/3.13 interpreter exists on the dev
  machine, so version-sensitive behaviour is only truly proven in the deployed function.
- **RU cost of a filtered vector scan** (`PLATFORM-SPEC §10` risk 2) — needs real row
  volume; a probe with no rows measures nothing.
- **Changefeed RU draw** — needs a webhook sink to exist first.
- **`ccloud` is installed and wired, but nobody has logged in yet.**
  `platform/bin/ccloud-mcp-setup.sh` uses it to resolve the cluster ID and writes the
  MCP client config from it. That is a real use rather than a command run once to be able
  to claim it — but the claim is only earned once the script has actually been run, and
  `ccloud auth login` is an interactive browser flow that no script can do for you.
- **The MCP server is configured but not yet connected.** Same blocker: the endpoint
  (`https://cockroachlabs.cloud/mcp`) answers `401` with an OAuth challenge, which is the
  correct response and confirms it is live. Read-only by default, which is the right
  posture — the console is the write path.
- **Licence chosen: Apache-2.0.** `LICENSE` and `NOTICE` are at the repository root. The
  `NOTICE` carries the pre-existing-code disclosure the hackathon rules require, naming
  `content/` and `app/` as out-of-period RemixKit work and `platform/` as the submission.
