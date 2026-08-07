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
| `schema/` | Migrations, applied in order. Two so far. |
| `web/` | The console — FastAPI + Jinja + htmx, the same shape as `app/remixkit/ui/`. |
| `infra/` | Terraform. Lambda + Function URL, and nothing else that costs money. |

## Where we are

| Piece | State |
|---|---|
| `tenant`, `artist` | **live** in `defaultdb` |
| `artist.type` — band, dj, singer, orchestra, composer, … | **live** |
| Console: list, search, add, edit, delete artists | **works locally**, not yet deployed |
| Tracks, derived facts, counterparties, threads, memory | not started |

A table arrives when something needs it, not because `PLATFORM-SPEC §2` lists it.

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
`reserved_concurrent_executions = 10` is a hard ceiling so neither AWS nor CockroachDB
can be billed by a runaway loop.

**Every resource is tagged** `Project=rtf-platform`, `Env`, `ManagedBy=terraform`,
`Repo=respect-the-funk`, `Component=console`, via provider `default_tags`. Group by
`Project` in Cost Explorer to separate platform spend from RemixKit's.

## Design notes

**A band is an artist with a type.** One column, not a second table. `type` is a free
STRING rather than an ENUM because "anything that has a music release" cannot be
enumerated in advance, and an ENUM would make adding `orchestra` a deploy. The form
offers suggestions and accepts anything. No lookup table until types need to be strict
or carry their own outreach playbooks.

**Anonymous reads, authenticated writes.** A judge clicking the demo URL sees the real
console rather than a login box; only the operator cookie can change anything. The
`Principal` shape is copied from `app/remixkit/auth/` (frozen, so copied not imported),
including its `tenant_id`. The shared token is not OTP and is not pretending to be —
`app/remixkit/auth/otp.py` is 61 lines and is where this goes when there is a second
operator.

**No seed file.** The label row is created by the first artist you save, so a fresh
cluster becomes a working one through the UI rather than a script somebody must
remember to run.

**The CSS is structural on purpose.** This is the wireframe stage: hierarchy, state and
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

- **Not deployed yet.** Everything above is local; `terraform apply` has not been run.
- **Dev runs Python 3.14, Lambda runs 3.13.** No 3.12/3.13 interpreter exists on the dev
  machine, so version-sensitive behaviour is only truly proven in the deployed function.
- **RU cost of a filtered vector scan** (`PLATFORM-SPEC §10` risk 2) — needs real row
  volume; a probe with no rows measures nothing.
- **Changefeed RU draw** — needs a webhook sink to exist first.
- **`ccloud` has not been used.** The cluster was made in the console, so
  `PLATFORM-SPEC §8`'s claim of it as tool 3 "in the day-1 provisioning path" is not yet
  earned — use it for something real or drop the claim.
- **Licence is still unchosen** and is required for submission; Apache-2.0 recommended.
