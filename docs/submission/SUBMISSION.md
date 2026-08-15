---
title: "Spindle — CockroachDB × AWS Hackathon submission"
subtitle: "Who, what, where, when, why CockroachDB, and the one thing it does that nothing else can. Every figure in this document was executed against the running cluster on 2026-08-14 and is labelled with its N."
status: "SUBMISSION NARRATIVE. The argument of record for judges and for a model reading the repository. Where this disagrees with `docs/2026-08-11-sponsor-audit.md`, the audit wins — it is the adversarial version and this is the readable one."
date: "2026-08-14"
---

## The one sentence

> **We map the music industry into a vector index, mail real human beings one at a time,
> and CockroachDB is the only reason we can prove why we did it.**

---

## WHO

**A record label with three people on it.**

That is not a simplification for the pitch, it is the design constraint. An independent
label is a founder, someone doing marketing, and someone doing everything else. They have a
roster, a catalogue, and a release next month. The job that eats them is not creative — it
is *correspondence*: finding the few hundred people in the world who might play this
particular record, writing to each of them individually, and remembering what happened.

**The user is the label, not the artist and not the fan.** One role. Everything in
`docs/SCOPE-RESET.md` follows from fixing that.

**And the agents work for them, not instead of them.** Every message this system prepares
waits for a person. That gate is the product.

## WHAT

**An agentic OS for music distribution.** A track is analysed **once** — tempo, brightness,
genre off the audio itself — and every outreach process afterwards is a *query against those
facts*, never a re-analysis. What accumulates between releases is not the audio. It is who
replied, who said yes, who asked for a shorter edit, and who would work with this artist
again.

That accumulation is the memory, and it is why release *n+1* is cheaper than release *n*.

**Measured on the cluster, 2026-08-14:**

| | |
|---|---|
| counterparties indexed | **14,170** |
| live facts about them | **66,103** |
| of those, roles classified | **14,169 of 14,170** |
| contact routes held | **2,351** (email 1,622 · phone 501 · form 228) |
| counterparties actually reachable | **439** |
| agent runs, all time | **55,569** |
| total spend, all time | **$0.12** |
| lessons learned | **1** |
| threads opened | **1** |
| messages sent | **1** |

**Read those last three honestly, because they are the point.** This system has held
exactly one conversation. The argument below is not that we ran thousands — it is that
every guarantee which makes thousands *safe* is a database constraint, and a constraint
holds identically at one conversation and at a hundred thousand. That is why the N is
small and the argument is not.

The repository says this about itself before a judge has to: `HACKATHON.md` → *What would
lose* → "Claiming scale. At our volume Postgres would serve this workload."

## WHERE

**AWS**, and only what is genuinely used:

- **Lambda** — the console and the JSON API, behind a Function URL, via Mangum. One
  container, one connection, scale-to-zero on both halves.
- **S3** — masters and derived artefacts, uploaded straight from the browser by presigned
  PUT so a 60 MB WAV never passes through a 15-minute function.
- **CockroachDB Basic** on AWS `us-east-1`.

**Not Bedrock.** `bedrock.py` implements both the on-demand and batch paths and neither can
run on this account — on-demand quota is 0 and non-adjustable, batch inference is
entitlement-gated. Written is not running, and the submission claims Lambda and S3.

**One region.** `SHOW REGIONS` returns 1. Migration `024_regional_by_row.sql` and
`infra/terraform/multiregion/` are written and validated and **not applied**, because adding
a region to a Basic cluster is irreversible and costs money. A Terraform plan is not a
region. It is the strongest argument we do not have, and it lives in the roadmap.

## WHEN

Initial commit **2026-07-26**, inside the 2026-06-30 → 2026-08-18 submission window.
RemixKit (`content/`, `app/`) predates the track, is not part of this submission's claim,
and is disclosed in `NOTICE` and `SCOPE-RESET.md`.

## WHY COCKROACHDB

The brief's load-bearing clause is *"memory as integral to agent functionality — not
supplementary."* A RAG chatbot that stores embeddings in CockroachDB scores near zero on
that. So the question has to be asked adversarially, and `docs/2026-08-11-sponsor-audit.md`
asks it: **is CockroachDB load-bearing here, or is it where the rows happen to be?**

### The case against us, stated as strongly as it deserves

| What we use | Postgres equivalent | Verdict |
|---|---|---|
| 4 vector indexes, cosine, tenant-prefixed | pgvector HNSW + composite indexes | **replaceable** |
| `SERIALIZABLE` by default | one `SET` statement | **replaceable** |
| `FOR UPDATE SKIP LOCKED` lease claiming | Postgres shipped it first | **replaceable** |
| `tenant_id` leading every index | ordinary composite indexes | **replaceable** |
| Scale to zero, $0.12 lifetime | Neon, Aurora Serverless v2 | **replaceable** |
| Changefeeds | Debezium + Kafka | replaceable, worse |
| `AS OF SYSTEM TIME` over a vector index | **nothing** | **irreplaceable** |
| `REGIONAL BY ROW` domiciling | nothing in one Postgres | irreplaceable, *unavailable* |

**At 14,170 rows in one region, Postgres with pgvector would serve this workload.**
Conceding that is what makes the remaining row land.

### The answer: *why did the agent decide that, then?*

Every agentic memory can answer *what does the agent know*. The hard question — the one
that matters the moment an agent takes an irreversible action against a real person — is
**why did it decide that, at that moment?**

This memory is not static. Lessons accumulate and reorder shortlists. Genres land and change
embeddings. `contact_state` moves as threads open and close. A station ranked 3rd on Tuesday
and 15th today, and the fleet **wrote to a real human being** on the strength of Tuesday's
ranking.

In Postgres, answering that means building an event-sourcing subsystem: temporal tables, a
versioned copy of every embedding, an audit log of every rerank — and it can only ever
answer questions you knew to instrument for.

In CockroachDB it is four words:

```sql
SELECT id, name, profile_embedding <=> $1::VECTOR(1024) AS distance
  FROM party@party_shortlist AS OF SYSTEM TIME '-2h'
 WHERE tenant_id = $2
   AND embedding_model = $3
   AND party_class = 'counterparty'
   AND contact_state = 'contactable'
 ORDER BY profile_embedding <=> $1::VECTOR(1024)
 LIMIT 20;
```

That is `agents.shortlist_as_of` verbatim in shape — the same statement the fleet runs to
decide, with three words added. Same index, same embeddings, same lessons, same four
predicates living inside the index prefix. **The memory as it actually stood.** No audit
table, no snapshot, no versioned copy of anything.

### The refusal that proves it is real

Time travel is bounded by `gc.ttlseconds`. Past that boundary the MVCC versions are
collected and the answer is genuinely gone — and the function **raises** rather than
retrying without the clause.

Retrying would "work": it returns rows, the shape is right, the page renders. It would also
be *the current ranking presented as the historical one* — a true answer to a question
nobody asked, offered as the justification for having emailed somebody. The code says it
plainly: there is no failure mode in this codebase worse than that one, which is why it is
a raise and not a fallback, and why the message says the history is gone rather than that
something went wrong.

A system that can say *"I cannot tell you why, and here is exactly why I cannot"* is more
accountable than one that always has an answer.

That is the whole submission. An autonomous system that acts on the world can be held to
account, and the accountability is a property of the storage engine rather than a subsystem
somebody remembered to build.

## HOW IT'S IRREPLACEABLE — for a label holding many conversations

The product's ambition is a label running **thousands of conversations across many
countries**. Everything below is what makes that safe, and every item is a constraint in the
database rather than a rule in an agent — which is precisely why it scales from the one
conversation we have held to the thousands we have not.

**1. Two campaigns cannot work the same person.**
`one_open_thread_per_counterparty` is a *partial* unique index on `(tenant_id,
counterparty_id)` where the thread is not closed. Not a convention, not a check somebody
remembered. Closing the thread releases them the same instant. A label with three people and
thirty campaigns cannot collide with itself.

**2. Nothing sends twice.**
`UNIQUE (message_id)` on the outbox. A double approval is a *failed insert*, not a second
copy in flight. Verified live: two approvals → `409 already_queued`, `outbox` still holds
one row.

**3. Nothing sends to someone who said stop.**
`opted_out` is a terminal state no discovery stage can overwrite — the loaders use
`ON CONFLICT DO NOTHING` so a harvester that re-finds a public address cannot resurrect it.
The sender's route predicate is an **allowlist in SQL** — `state IN ('unverified',
'verified')` — placed there, in `sender._prepare`, *"rather than in Python so that no caller
can forget it."*

**4. Nothing sends to an address we guessed.**
An `inferred` route is refused outright, not deprioritised. That distinction was a real bug:
the first version expressed it in `ORDER BY`, which merely ranked a guess lower, so a
counterparty whose only route was guessed still got mailed. Ordering is a preference; this
needed a predicate.

**5. Two workers with the same name cannot corrupt each other.**
Every agent action is fenced on a `lease_token` the database mints per row. `owner_agent`
alone cannot serve — the CLI default is the constant `ingest-cli`, and two concurrent drains
under that name would match each other's leases. The name cannot tell them apart; the token
can. This is what lets a small team run many workers without a scheduler.

**6. A dead worker never double-sends.**
The claim commits *before* the network call. A worker killed mid-send leaves a row that is
never automatically retried. A missed pitch costs nothing; a second pitch costs the curator.

**7. The channel model is ready for the conversations we are not having yet.**
`contact_route` holds many routes per counterparty — `channel IN ('email','phone','form',
'postal','social')` — and the channel selects the sender adapter. Adding Telegram or
WhatsApp is a column value plus an adapter, **not a second compliance regime**, because
every guarantee above is a predicate those channels inherit rather than reimplement.
*Today there are zero `social` routes and no such adapter; this is a claim about the model.*

**8. Restraint that is visible rather than asserted.**
The first harvest ran 2026-08-14. 2,351 routes from public pages. **Seven sites' robots.txt
refused us and the agents stopped.** **Zero fabricated addresses** across 1,622 emails —
audited for the `example.com` and vendor-address patterns that a naive scraper produces.

**9. And when a station asks why you wrote to them, you can answer.**
Which is item 1 through 8's reason for existing, and the only one on this list Postgres
cannot do at any price.

## Against the judging criteria

| Criterion | The claim | The evidence |
|---|---|---|
| **Agentic Memory Design** *(tie-breaker)* | Memory is the coordination substrate. Agents never call each other; work is claimed from a table under a lease. Time travel makes past decisions inspectable. | `AS OF SYSTEM TIME` over the vector index; `lease_token`; 55,569 agent runs |
| **Technical Implementation** | Filtered vector retrieval inside the index prefix; transactional outbox; partial unique index making double-contact impossible | live `EXPLAIN` showing `vector search` + `prefix spans`; 605 tests |
| **Real-World Impact** | A label's actual bottleneck, with real public data | 14,170 counterparties from the FCC register and Radio Browser; 2,351 real contact routes |
| **Production Readiness** | Tenant as partition key, per-run cost, idempotent sends, guarded migrations | $0.12 across 55,569 runs; `apply.py` refuses a destructive migration with no emptiness guard |
| **Creativity & Originality** | A fleet that takes irreversible real-world actions, in a domain that is not another dev tool | one real send, human-gated |

## What this submission does not claim

Stated here so a judge does not have to find it:

- **Not global infrastructure.** One region. `REGIONAL BY ROW` is written and unapplied.
- **Not global counterparties.** All 3,180 country facts read `US`. Radio Browser holds
  53,297 non-US stations and we have not seeded them; `docs/research/14-counterparty-sources.md`
  measures exactly what that would yield.
- **Not thousands of conversations.** One thread, one send. See WHAT.
- **Not scale.** 14,170 rows is not a distributed-systems problem.
- **Not legal compliance.** The database enforces *our* rules and shows them.
  `contact_country` landed 2026-08-14 and no jurisdiction decision has been made with it.
  No statute is named anywhere in this submission.
- **Not a changefeed.** `changefeed.py` composes the statement and consumes batches;
  `SHOW CHANGEFEED JOBS` returns nothing, because creating one draws RUs continuously and
  nobody authorised that spend. Built is not running.
- **Not Bedrock.** See WHERE.
- **Not a paid product.** `apps/spindle/schema/033_account_billing.sql` makes the tenant column
  real, `POST /claim` creates a bounded free tenant with no Stripe configuration present,
  and `apps/spindle/web/spindle/billing.py` speaks Stripe in **test mode only** — it
  refuses a live key on purpose. No checkout session has been created against Stripe by
  this code, no customer exists, and no payment has been taken. The prices in `plans.py`
  are prices that have been written down.

Every one of these was true and unstated at some point in this project's life, and each is
written down here because a submission that claims everything is one a judge stops
believing.

## Reading order for a judge with four minutes

1. This document.
2. `docs/2026-08-11-sponsor-audit.md` — the same argument, made adversarially, with the
   case *against* the sponsor stated first.
3. `docs/submission/TOOLS.md` — every tool claim, each executed against the cluster.
4. `apps/spindle/schema/018_contact_route.sql` — one migration, to see how decisions get
   recorded here.
