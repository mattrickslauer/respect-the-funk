---
title: "RemixKit — AWS Infrastructure Proposal"
subtitle: "The BUILD-SPEC §2 architecture re-platformed onto AWS scale-to-zero, with Backblaze B2 kept as the data plane and Genblaze as the generation layer."
status: "PROPOSAL — no Terraform yet, by design. Approve the shape before anyone writes HCL."
date: "2026-07-26"
---

## The deliverable

**[`architecture.pdf`](./architecture.pdf)** — two pages:

1. **The system.** Who calls what, which boxes cost money when nobody is using them (none of them), and where the bytes actually live.
2. **The argument.** *Generate once, remix infinitely* drawn as a flow, so the unit economics and the provenance lineage are visibly the same picture.

Both pages are generated from [`diagram.py`](./diagram.py), not drawn by hand, so the picture cannot drift from the words. Node labels carry the actual configuration (`min 0 ACU`, `Function URL`, `Fargate Spot`); changing the architecture means changing that file.

```bash
brew install graphviz
pip install -r requirements.txt
python3 make_icons.py && python3 diagram.py     # -> architecture.pdf
```

---

## The one-sentence version

> **AWS runs the logic; B2 holds the bytes and the provenance.**

This split is the whole proposal. AWS contributes a control plane that costs nothing at rest, and B2 contributes storage that is cheap, S3-compatible, and — because Genblaze writes its manifests there — the system of record for *how every asset was made*. Neither half is decorative: strip AWS out and you have no orchestration; strip B2 out and you lose two of the four things the hackathon actually scores.

---

## Why this differs from BUILD-SPEC §2

BUILD-SPEC locked a **GCP** stack: Cloud Run (`min-instances=0`), Cloud Tasks, Cloud Run Jobs, Neon Postgres. That was a sound choice, and this document does not claim otherwise. It re-platforms it because the target moved to AWS.

The mapping is close to one-for-one, which is itself the useful finding — **§2b's eight scalability rules survive the move unchanged**, because they are about *shape* (queue the long work, keep bytes off the app server, serve from cache) rather than about vendor.

| BUILD-SPEC §2 (GCP) | This proposal (AWS) | Why |
|---|---|---|
| Cloud Run service, `min-instances=0` | **Lambda** (container) + **Lambda Web Adapter** + **Function URL** | Runs uvicorn/FastAPI unmodified. True zero — no instance to bill, no request-concurrency floor. |
| Cloud Tasks | **SQS** + DLQ | Pay-per-request, $0 idle, 1M requests/month free. |
| Cloud Run Job (kit generation) | **AWS Batch on Fargate Spot** | See the 15-minute caveat below — this is the one place the naive mapping is *wrong*. |
| Cloud Run Job (compositing) | **AWS Batch on Fargate Spot** | ffmpeg is CPU-bound and interruptible; Spot is ~70% cheaper and restart is free because the job is idempotent. |
| Neon serverless Postgres | **Aurora Serverless v2, min 0 ACU** | Aurora now scales to zero ACUs and auto-pauses. Storage still bills; compute does not. |
| Cloud CDN | **CloudFront** | Long-TTL immutable keys in front of B2. |
| — | **CloudFront Function + KeyValueStore** | `/r/{code}` → 302 *at the edge*, with clicks read off access logs. Zero compute on the attribution hot path. |
| Cloud Scheduler | **EventBridge Scheduler** → Lambda | Nightly `genblaze index` → Parquet. |
| Secret Manager | **SSM Parameter Store** (standard tier) | Free. Secrets Manager is $0.40/secret/month, which is a real fixed floor against a "$0 idle" target. |
| **Backblaze B2** | **Backblaze B2 — unchanged** | Non-negotiable: it is 1 of the 4 scoring criteria and the whole provenance story. |
| **Genblaze** | **Genblaze — unchanged** | Likewise. Genblaze's own docs advertise Lambda as a supported host. |

---

## Where the four scoring criteria land

The hackathon weights these **equally**, so the architecture is deliberately built to put a visible artifact under each one.

| Criterion | What in this diagram serves it |
|---|---|
| **Use of Genblaze** | `kit-worker` runs one `Pipeline` that fans out across GMI Cloud video, GMI Cloud image, and ElevenLabs audio. Multi-provider orchestration is the node, not a slide. |
| **B2 Storage + Data Orchestration** | B2 is the *only* durable store for media. `KeyStrategy.HIERARCHICAL` gives the run layout; the nightly indexer emits `manifests.parquet` — the "analytics-ready for ingestion" artifact, queryable by Athena directly from B2. |
| **Production Readiness** | Idle cost ~$0 compute; DLQ + idempotency keys on every job; cost ledger per run/asset/tenant; CloudWatch alarms; Spot with free restart. |
| **Real-World Utility** | The `/verify` path: manifest embedded *inside* the delivered mp4, so disclosure travels with the file. That is an FTC posture, not a demo trick. |

---

## Component inventory

Everything below is chosen so that **the bill at 3am with zero traffic is storage and nothing else.**

| Service | Role | The setting that makes it scale to zero | Idle cost |
|---|---|---|---|
| Lambda (`web`) | FastAPI, JSON only, <200ms | No provisioned concurrency | $0 |
| Lambda (`indexer`) | `genblaze index` → Parquet | Invoked by schedule only | $0 |
| SQS (`kit-jobs` + DLQ) | Durable job handoff | Pay-per-request | $0 (1M free) |
| AWS Batch on Fargate Spot | `kit-worker`, `composite-worker` | `minvCpus: 0` on the compute environment | $0 |
| EventBridge Scheduler | Nightly trigger | Pay-per-invocation | ~$0 |
| CloudFront | Cached asset reads, edge redirect | Pay-per-request | $0 |
| Aurora Serverless v2 | Marketplace, ledger, K-factor | `min_capacity = 0` ACU + auto-pause | storage only (~$0.10/GB-mo) |
| SSM Parameter Store | B2 + provider credentials | Standard tier | $0 |
| ECR | Container images | — | ~$0.10/GB-mo |
| CloudWatch | Logs, metrics, alarms | Short retention (14d) | cents |
| **Backblaze B2** | **All media + manifests + Parquet** | — | **$6/TB-mo** |

**Realistic idle total: roughly $1–3/month**, dominated by B2 storage and ECR — *not* the ~$0 BUILD-SPEC §8 aspires to, but the gap is storage you are choosing to keep, not compute you forgot to turn off. Worth stating precisely rather than rounding to zero, in the same spirit as the repo's *"price it, date it."*

---

## The caveats, stated up front

A proposal that only lists strengths is a sales document. These are the four things most likely to bite.

**1. The 15-minute Lambda cliff — and why `kit-worker` is not a Lambda.**
BUILD-SPEC §4 calls `p.arun(sink=storage, timeout=900)`. 900 seconds is *exactly* Lambda's maximum. A kit that fans out to several video models would sit flush against the ceiling with zero headroom for cold start, provider retries, or the upload afterwards — and it would fail by timing out, silently, on the most expensive path in the system. So kit generation runs on **AWS Batch (Fargate)**, which has no such limit and still scales to zero via `minvCpus: 0`. The SQS→Batch hop is either EventBridge Pipes or a thin dispatcher Lambda. *This is the one place the obvious GCP→AWS mapping is actively wrong, which is why it is first.*

**2. Aurora's cold start is user-visible.**
Scaling to 0 ACU means the first query after a pause pays a resume penalty (roughly 10–15s). That is fine for a nightly job and bad for a judge clicking a link. Mitigations, in order of preference: keep the demo window warm with a cheap scheduled ping; set the auto-pause threshold generously; or accept it and make the first screen static. If the resume latency proves unacceptable, **Neon stays a legitimate answer on AWS** — it is the incumbent from BUILD-SPEC and wakes faster.
*Aurora DSQL is tempting here and probably not right yet:* it is genuinely serverless, but it does not support foreign keys, and the §3 schema leans on them throughout. Revisit only if the schema is denormalised first.

**3. B2 egress through CloudFront is a real line item.**
Backblaze's free-egress arrangement is with **Cloudflare**, not AWS. CloudFront origin-fetching from B2 pays B2 egress (free up to 3× stored bytes, then ~$0.01/GB). At demo scale this is pennies. If a release actually breaks out, **putting Cloudflare in front of B2 instead of CloudFront makes that line $0** — worth doing before it matters, and worth knowing now rather than discovering on a bill.

**4. Spot interruption is safe here only because the jobs are idempotent.**
§2b rule 4 is load-bearing: jobs are keyed by `kit_id`/`session_id` + input hash, so a Spot reclaim mid-render costs wall-clock and nothing else. If that rule is ever relaxed, Fargate Spot has to become Fargate on-demand.

---

## Deliberately not in this proposal

Named so nobody thinks they were forgotten: EKS/Kubernetes, multi-region, a VPC with NAT gateways (a NAT is ~$32/month of pure idle floor and would defeat the entire premise — Lambdas here need no VPC because Aurora is reached over its public endpoint with IAM auth, or via VPC Lattice), API Gateway (Function URLs are sufficient and free), Step Functions (Batch covers the one long workflow), and any warm pool anywhere.

## Next step

This is a shape to approve, not code to run. Once the shape is agreed, `infra/terraform/` gets: ECR + two image builds, the Lambda/Function-URL pair, the SQS+DLQ pair, a Batch compute environment at `minvCpus: 0`, the Aurora cluster at `min_capacity = 0`, the CloudFront distribution with a B2 custom origin, and the KeyValueStore for `/r/{code}`.

## Files

| File | What |
|---|---|
| `architecture.pdf` | **The deliverable** — 2 pages, vector |
| `diagram.py` | Source of the PDF |
| `make_icons.py` | Generates the non-AWS node tiles (B2, Genblaze, providers) |
| `assets/` | Generated icon tiles |
| `requirements.txt` | `diagrams`, `pillow`, `pypdf` |
