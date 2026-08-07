---
title: "Reference — CockroachDB × AWS Hackathon: Build with Agentic Memory"
subtitle: "The rules, requirements and judging criteria, captured locally so the submission is not built against a half-remembered version of them."
status: "REFERENCE — captured 2026-08-06 from cockroachdb-ai.devpost.com. Not a plan. Where this disagrees with the live listing, the live listing wins."
date: "2026-08-06"
---

## The shape of it

| | |
|---|---|
| **Name** | CockroachDB × AWS Hackathon — Build with Agentic Memory |
| **Deadline** | **2026-08-18, 17:00 EDT** |
| **Themes** | Machine Learning/AI · Databases · DevOps |
| **Format** | Online, public |
| **Registrants** | ~3,020 as of capture |
| **Prize pool** | $8,750 |

**Prizes:** 1st **$5,000** + blog feature + swag · 2nd **$2,500** + swag · 3rd **$1,250** + swag.

## The brief

> Build an agentic application leveraging CockroachDB as the persistent memory layer on AWS
> infrastructure. The submission must demonstrate **memory as integral to agent
> functionality — not supplementary.**

That last clause is the whole competition. A retrieval-augmented chatbot that happens to
store its embeddings in CockroachDB is supplementary memory and scores near zero on the
headline criterion.

## Hard requirements

**CockroachDB — at least 2 of these 4:**

| Tool | Our use | Status |
|---|---|---|
| Distributed Vector Indexing | R1 counterparty shortlist, R2 lesson retrieval | **core** |
| Cloud Managed MCP Server | natural-language catalog queries over the spine | **core** |
| `ccloud` CLI (agent-ready) | cluster provisioning in the day-1 path | free, claim it |
| Agent Skills repo (open source) | wrap platform operations as skills | optional |

**AWS — at least 1 of these:** Amazon Bedrock · AWS Lambda · Amazon ECS/EKS · Amazon S3 ·
Amazon SageMaker · Bedrock Agents.

*Ours: Bedrock as agent runtime and embedding provider, Lambda for changefeed webhooks.*

## Submission checklist

- [ ] **Public open-source repository** under **MIT or Apache-2.0** — this repo currently has
      **neither**. Blocking.
- [ ] **Functional demo application URL**
- [ ] **Video demonstration, under 3 minutes**, on YouTube or Vimeo
- [ ] **Documentation naming** which CockroachDB and AWS tools were used
- [ ] *Optional:* architectural diagram — we generate one, see
      [`infra/platform_architecture.py`](../../infra/platform_architecture.py)
- [ ] *Optional:* tool feedback

## Judging criteria — unweighted

| Criterion | What we intend to show |
|---|---|
| **Agentic Memory Design** | Memory is the coordination substrate, not a lookup table. Agents never call each other; a row change wakes the next one. |
| **Technical Implementation** | Filtered vector retrieval, lease-based work claiming, transactional outbox, and a partial unique index that makes double-contact impossible. |
| **Real-World Impact** | A label's actual bottleneck — finding and working the right counterparties for a release. |
| **Production Readiness** | Terraform, priced workload model, tenant as partition key, cost per agent run, idempotent sends. |
| **Creativity & Originality** | A fleet that takes irreversible real-world actions, in a domain that is not another dev tool. |

## Eligibility notes

- The project must be **newly created by the Entrant during the Submission Period**
  (2026-06-30 → 2026-08-18). This repository's initial commit is **2026-07-26**, inside the
  window.
- Pre-existing code incorporated into the work **must be disclosed**. RemixKit (`content/`,
  `app/`) predates the CockroachDB track and is disclosed as such; see
  [`SCOPE-RESET.md`](../SCOPE-RESET.md) for what it is and what standing it has.
- Neither this hackathon nor the earlier Backblaze one forbids submitting a project to both.

## What would lose

Stated so it is a decision rather than a drift:

1. **Claiming scale.** At our volume Postgres would serve this workload. The argument is
   consolidation and correctness defaults. See [`PLATFORM-SPEC.md §1`](../PLATFORM-SPEC.md).
2. **A flattering improvement curve.** Small N by Aug 18. Label it with its N.
3. **Publishing anything built on unlicensed third-party faces.** The `dialogue` hooks carry
   `people_release: false`; they are a retrieval corpus, not publishable output.

## Source

Captured from <https://cockroachdb-ai.devpost.com/> on 2026-08-06.
