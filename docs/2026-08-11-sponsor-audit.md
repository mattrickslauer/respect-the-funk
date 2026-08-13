---
title: "Why CockroachDB, and not Postgres"
subtitle: "An adversarial audit of the sponsor's load-bearingness. Every claim executed against the running cluster. The first section argues against the submission; the second is the answer, and it is a capability that was verified working today rather than a paragraph."
status: "FINDINGS — 2026-08-11. One configuration change was made during this audit and is recorded in §4. Two dated addenda were added on 2026-08-13, in §3 and §5.2; both record that something was built, and neither upgrades a claim, because neither thing is running."
date: "2026-08-11"
---

## The question

A hackathon submission fails when the sponsor is *tangential* — when a judge can read the
architecture and think "this would run on Postgres." RemixKit did not place at the
Backblaze hackathon for exactly this reason: B2 was where files happened to be stored, not
why the product worked. Sonar placed at the AWS hackathon because API Gateway WebSockets,
the Lambda authorizer and CloudFront were each doing something the product could not do
without them.

So: **is CockroachDB load-bearing here, or is it where the rows happen to be?**

---

## 1. The case against us, stated as strongly as it deserves

Every CockroachDB capability this project uses today, against its Postgres equivalent:

| What we use | Postgres equivalent | Verdict |
|---|---|---|
| 4 vector indexes, cosine, tenant-prefixed | `pgvector` HNSW + composite/partial indexes | **replaceable** |
| `SERIALIZABLE` by default | `SET default_transaction_isolation` | **replaceable** |
| `FOR UPDATE SKIP LOCKED` lease claiming | Postgres shipped it first | **replaceable** |
| `tenant_id` leading every index | ordinary composite indexes | **replaceable** |
| Scale-to-zero, $0.035 lifetime spend | Neon, Aurora Serverless v2 | **replaceable** |
| Changefeeds | Debezium + Kafka, or `LISTEN/NOTIFY` | replaceable, worse |
| `AS OF SYSTEM TIME` | **nothing** | **irreplaceable** |
| `REGIONAL BY ROW` domiciling | **nothing** in one Postgres | irreplaceable, *unavailable* — see §3 |

At 14,170 counterparties in one region, **Postgres with pgvector would serve this
workload.** `docs/reference/HACKATHON.md` already says so under *What would lose*:
"Claiming scale. At our volume Postgres would serve this workload."

That is the honest starting position, and any pitch built on "distributed scale" is a
pitch a judge will correctly dismiss. We have 14k rows. That is not a distributed-systems
problem.

---

## 2. The answer: time travel over a vector index

The brief's load-bearing clause is *"memory as integral to agent functionality — not
supplementary."* The hardest question any agentic memory faces is not *what does the agent
know* — every RAG chatbot answers that. It is:

> **Why did the agent decide that, then?**

This system's memory is not static. Lessons accumulate and rerank shortlists. Genres land
and change embeddings. `contact_state` moves as threads open. A station ranked 3rd on
Tuesday and 15th today, and the fleet **emailed a real person** on the strength of the
Tuesday ranking.

In Postgres, answering that means building an event-sourcing subsystem: temporal tables, a
versioned copy of every embedding, an audit log of every rerank — and it can only ever
answer questions you thought to log in advance.

In CockroachDB it is **the same query with four words added**, and it was verified working
on the live cluster during this audit:

```sql
SELECT name FROM party@party_shortlist AS OF SYSTEM TIME '-10m'
 WHERE tenant_id = $1 AND embedding_model = 'openai:text-embedding-3-small'
   AND party_class = 'counterparty' AND contact_state = 'contactable'
 ORDER BY profile_embedding <=> $2 LIMIT 3;
```

That is a **filtered vector search against the index as it existed at a past timestamp.**
Not a snapshot table. Not an audit trail. The actual index, actual embeddings, actual
`lesson` rows, as of then — with no schema written to support it and no write-path cost.

**pgvector cannot do this at any price.** There is no mechanism in Postgres to query an
index at a prior timestamp; you would have to have stored every historical vector yourself
and rebuilt the ranking offline.

### Why this is the right argument for *this* product

The fleet takes **irreversible real-world actions** — it sends pitches to named music
directors. "Why did you email this station?" is a question a label will actually be asked,
by a curator, by an artist, or by a regulator. This system can replay the exact memory
state that produced the decision, including the vector ranking, without having planned to.

That is memory as a **four-dimensional substrate**: not just what the agent knows, but what
it knew. It is the strongest available answer to the tie-breaker criterion, and it is
CockroachDB-only.

---

## 3. What we cannot claim, and should stop implying

**Multi-region data domiciling.** `REGIONAL BY ROW` with EU contact data pinned to EU
nodes would be the single strongest architectural argument available — `contact_route`
(migration 018) holds personal data for named individuals, the counterparty index is
already global-capable, and GDPR restricts where that data may live. One logical table,
row-level domiciling, enforced by the database.

**It is not available on this cluster.** Verified this session:

```
SHOW REGIONS                        -> aws-us-east-1 only
SHOW REGIONS FROM DATABASE defaultdb -> (empty)
SHOW SURVIVAL GOAL                   -> (empty)
```

BASIC is single-region. Demonstrating this needs a plan upgrade — a real cost and a real
migration, seven days out. **Recommended: do not claim it, and do not build toward it
before the 18th.** An architectural claim a judge can falsify with one `SHOW REGIONS` is
worse than no claim. It belongs in the roadmap section, named as future work.

> **Addendum, 2026-08-13 — half of that recommendation was overruled, and the important
> half was not.**
>
> The building happened: `platform/schema/024_regional_by_row.sql` makes `contact_route`
> `REGIONAL BY ROW`, and `infra/terraform/multiregion/` provisions the three-region
> Standard cluster the migration needs. `terraform validate` and `terraform fmt` pass
> against `cockroachdb/cockroach v1.22.0`. **Nothing has been applied. The three queries
> above still return exactly what they returned on the 11th**, and every document in this
> repository is required to say so.
>
> The audit's advice to hold off assumed the cost of building it was the cost of upgrading
> the live cluster. That turned out to be the wrong shape of cost, and the reason is worth
> recording because it is what keeps the option open at all: **CockroachDB Cloud does not
> support removing a region once it has been added.** The documented way back to
> single-region is backup, new cluster, restore. So converting `respect-the-funk` — the
> cluster holding the system of record, seven days from a deadline — would be a one-way
> door taken for a demo. The module therefore creates a *throwaway* cluster
> (`rtf-residency-demo`) and `import.tf.example` carries the conversion path disarmed,
> with the irreversibility written where whoever renames it will read it.
>
> That changes the schedule and not the standard. §3's rule stands unamended: **do not
> claim it.** A validated plan is not a region, and the sentence a judge can falsify is
> the same sentence either way.

---

## 4. The change made during this audit

The time-travel window was **75 minutes**, which is CockroachDB's default
`gc.ttlseconds = 4500`. That is long enough to diagnose an incident and far too short to
answer "why did you pitch them last Tuesday" — the question §2 rests on.

```sql
ALTER DATABASE defaultdb CONFIGURE ZONE USING gc.ttlseconds = 604800;   -- 7 days
```

Verified after the change: `SELECT count(*) FROM party AS OF SYSTEM TIME '-3h'` returned
14,178, a query that failed the GC threshold before it. The window now covers the entire
judging period.

**The cost is honest and worth stating:** a longer GC window keeps more MVCC garbage, so
storage grows. At this size it is negligible; on a large cluster it would be a real
trade-off, and the submission should say so rather than presenting it as free.

---

## 5. What to build in the remaining seven days

Ranked by how much each moves the "sponsor is load-bearing" judgement:

1. **Put "as of" in the console.** A timestamp control on the shortlist: *here is who we
   would have pitched last Tuesday, and here is what changed.* This is the demo. It is one
   query parameter threaded through `agents.shortlist`, and it converts §2 from a claim
   into thirty seconds of screen.
2. **Build the changefeed.** Rangefeeds are enabled and a sinkless feed was verified
   streaming during the 2026-08-10 audit. Memory writes waking agents with no broker is
   the sharpest statement of "the database is the coordination substrate". It also makes
   the video script's existing line true instead of falsifiable.

   > **Done 2026-08-13, and the line is still falsifiable.** `changefeed.py` composes the
   > `CREATE CHANGEFEED`, parses the webhook batches, maps a change to the lead kinds it
   > makes claimable, and ships a Lambda handler and a `--verify`. It does not run the
   > statement; it prints it. `SHOW CHANGEFEED JOBS` returns **zero rows**, because a feed
   > draws request units *continuously* — the one unmeasured cost `PLATFORM-SPEC §10`
   > risk 2 flagged as the likelier of the two to erode the free allowance — and spending
   > that is a human decision, not a deploy step. So this item is **built, not created**,
   > and the script line stays cut. Writing the code did not earn the sentence; creating
   > the job would.
   >
   > The build was still worth it for a reason this ranking undersold. The interesting
   > part is not the DDL, it is what the event is *allowed to carry*: a `Wake` holds a
   > tenant and a set of lead kinds, and deliberately no row id and no payload. A woken
   > worker's only move is to call `fleet.work_once` and claim, so two workers woken by
   > one at-least-once delivery race on the lease and the second gets nothing. The feed
   > cannot become a work queue with no fence on it, and a fleet with the feed switched
   > off is slower rather than broken. That is the argument; the job is the demo.
3. **Reframe the pitch around decision auditability**, not scale. The sentence to give a
   judge: *"Our agents email real people. CockroachDB is the only reason we can replay the
   exact memory state — including the vector ranking — that made them do it."*
4. **Keep saying what is replaceable.** Volunteering that pgvector could serve the
   retrieval, and then showing the thing it cannot do, is far more convincing than
   claiming everything is unique.

---

## Method

Executed against cluster `respect-the-funk` (`ae38b92e-c1ad-4a06-a247-489cd5ce9964`),
CockroachDB v26.2.5, on 2026-08-11. The `AS OF SYSTEM TIME` vector query, the zone
configuration before and after, the region enumeration, and the `-3h` read were each run
directly. Nothing in §2–§4 is read from documentation.
