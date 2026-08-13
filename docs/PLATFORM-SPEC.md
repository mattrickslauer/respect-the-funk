---
title: "Platform Architecture — the spine, the fleet, and the substrate"
subtitle: "How one artist's catalogue drives many outreach channels in parallel, and why the persistence layer is simultaneously the fleet's memory, its state, its coordination lock, and its event bus."
status: "DECISION + DRAFT — extends SCOPE-RESET.md, which remains the binding scope statement. Resolves its open decisions 1, 3 and 5. Leaves 2, 4 and 6 open."
date: "2026-08-06"
deadline: "2026-08-18 17:00 EDT"
---

## 0. The thesis

**Agents do not call each other. They read shared memory, act, write the result back, and that write wakes the next agent.**

Everything below follows from taking that literally. There is no orchestrator, no agent-to-agent RPC, and no message broker. A campaign advances because a row changed and a changefeed noticed.

This makes the persistence layer do four jobs at once:

| Job | What it holds |
|---|---|
| **Memory** | What we know about a track, an artist, a counterparty, and what we have learned |
| **State** | Where every conversation with every counterparty currently stands |
| **Coordination** | Which agent owns which piece of work right now, and for how long |
| **Event bus** | Row changes, streamed, as the signal that wakes the next agent |

The conventional stack for that is Postgres + a vector database + Redis + a queue. Four systems, three seams, and every seam is a place where two agents can disagree about reality. One store removes the seams.

---

## 1. Why one store, honestly

**This is not a scale argument and must not be presented as one.** At one label, a small roster, thousands of counterparties and hundreds of sends a day, a single Postgres instance would serve this workload. `infra/MEMORY-WORKLOAD.md` already measured the volumes and they are small.

The argument is consolidation and correctness defaults. What CockroachDB gives over the default stack, stated narrowly enough to be defensible:

| | Default stack | Here |
|---|---|---|
| Isolation | Postgres defaults to Read Committed. Two agents writing lessons about the same counterparty silently lose one. | **Serializable by default.** The footgun cannot be forgotten. |
| Vectors | Separate service. A memory is written, then embedded, then indexed — a staleness window where the agent cannot retrieve what it just learned. | **Same transaction.** Searchable at commit. |
| Coordination | Redis lock, distinct from the data it protects. Lock says claimed, database says otherwise. | `SELECT … FOR UPDATE SKIP LOCKED` **on the row itself.** |
| Events | Queue fed by application code after the write — the classic dual-write. | **Changefeed on the table.** Cannot diverge from the data. |
| Idle cost | Instance floor | **Scales to zero, $0** — measured in `infra/MEMORY-WORKLOAD.md` |

Multi-region is **not** claimed as a current requirement. It becomes one if counterparty PII acquires residency obligations (see §10, open).

> ⚠️ **Amended 2026-08-13.** The trigger has fired and the capability has not. `contact_route` (migration 018) now holds email addresses, phone numbers and named job titles for identifiable individuals — Article 4(1) personal data, and the only table in this schema that is. Migration `024_regional_by_row.sql` makes it `REGIONAL BY ROW` and `infra/terraform/multiregion/` provisions the three-region cluster it needs; both are written, `terraform validate` passes, and **neither has been applied**. The live cluster is still Basic, single-region `aws-us-east-1`, and `SHOW REGIONS` will say so. A region cannot be removed from a CockroachDB Cloud cluster once added, so the plan is a throwaway cluster and never a conversion of the one holding the system of record. Nothing in the submission claims this, and nothing should until a `SHOW REGIONS` backs it.

---

## 2. The spine

`SCOPE-RESET.md §1` fixes the artist as the root. `SCOPE-RESET.md §2a` fixes two rules the schema has to encode structurally rather than by convention: every fact carries how it was obtained, and processes write facts back as well as reading them.

### 2a. Roots

```sql
tenant(id, slug, name, created_at)
artist(id, tenant_id, slug, name, status, created_at)
track (id, tenant_id, artist_id, title, isrc, master_key, status, created_at)
```

### 2b. Derived facts — analysed once

The three provenance classes get three storage shapes, so mixing them is a schema error rather than a discipline failure.

```sql
-- MEASURED — computed from the audio. Deterministic, permanent, one row.
track_measurement(track_id PK, bpm, musical_key, duration_ms,
                  drop_ms, hook_start_ms, hook_end_ms, energy_curve_json,
                  method, tool_version, measured_at)

-- INFERRED — a model's estimate. Versioned, never overwritten in place.
track_character(id, track_id, mood, era, reference_artists TEXT[],
                embedding VECTOR(1024), model, model_version,
                confidence, inferred_at, supersedes_id)

-- ASSERTED — a human stated it, and is accountable for it.
track_rights(id, track_id, master_owner, publishing_json, splits_json,
             clearance_state, asserted_by, asserted_at, doc_key)

-- The artist-level audience model. Inferred, versioned, inherited by tracks.
artist_audience(id, artist_id, model_version, position_json,
                confidence, inferred_at, supersedes_id)
```

`supersedes_id` rather than `UPDATE` is the mechanism behind "never overwrite a measured value with an estimate." Revisions are appended; the current value is the head of the chain.

### 2c. Counterparty — one shape, many kinds

The unification claim from `SCOPE-RESET.md §2`: a creator, a radio programmer, a playlist curator, a sync supervisor and a music writer differ in playbook, not in structure.

```sql
counterparty(id, tenant_id, kind,          -- creator|radio|curator|press|sync
             platform, platform_user_id, handle, display_name, profile_url, bio,
             profile_embedding VECTOR(1024),
             first_seen_at, last_refreshed_at)

counterparty_contact(id, counterparty_id, channel, address, role, verified, added_at)
             -- channel: email|dm|manager_email   role: self|manager|programmer

counterparty_observation(id, counterparty_id, dimension, bucket, share,
             provenance,                    -- measured|inferred|asserted
             source, vendor, confidence, error_bar_pp, sample_size,
             observed_at, observed_by, notes)
             -- APPEND ONLY. Source rank decides what is read. Never overwritten.
```

`counterparty_observation` is `docs/research/10-creator-indexing.md §7`'s `audience_demographic` generalised past creators. Its rule survives intact: an estimate may never overwrite a measurement, and the interface must render the two distinguishably.

### 2d. Outreach — state machine and lock in one row

```sql
campaign(id, tenant_id, artist_id, track_id, channel, goal, state, started_at, ended_at)

thread(id, tenant_id, campaign_id, counterparty_id,
       state,                            -- see below
       next_action_at,
       owner_agent, lease_expires_at,    -- the lock lives on the work item
       created_at, updated_at)
  UNIQUE (campaign_id, counterparty_id)

message(id, thread_id, direction, channel, subject, body,
        provider_message_id, idempotency_key UNIQUE, sent_at, received_at)

outbox(id, thread_id, kind, payload_json, state, attempts,
       claimed_by, claimed_at, not_before, created_at)
```

The thread state machine is channel-agnostic:

```
discovered → shortlisted → approved → drafted → awaiting_human → queued → sent
          → awaiting_reply → replied → negotiating → agreed → delivered → verified
          → closed_won | closed_lost | closed_no_reply
```

### 2e. Memory and fleet

```sql
lesson(id, tenant_id, scope_kind, scope_id,   -- artist|counterparty_kind|channel|global
       text, evidence_json, confidence,
       embedding VECTOR(1024), supersedes_id, hit_count, created_at)

agent_run(id, tenant_id, agent_kind, thread_id, input_json, output_json,
          state, model, tokens_in, tokens_out, cost_cents,
          started_at, ended_at, error)
```

`agent_run` is not telemetry. It is the record that makes a fleet restartable and a decision explainable.

---

## 3. Coordination

### 3a. Claiming work

One primitive, used by every agent in the fleet:

```sql
UPDATE thread
   SET owner_agent = $agent, lease_expires_at = now() + INTERVAL '5 minutes'
 WHERE id IN (
       SELECT id FROM thread
        WHERE tenant_id = $tenant
          AND state = $state
          AND next_action_at <= now()
          AND (owner_agent IS NULL OR lease_expires_at < now())
        ORDER BY next_action_at
        LIMIT $batch
        FOR UPDATE SKIP LOCKED)
RETURNING *;
```

A lease rather than a lock: an agent that dies mid-task releases its work by expiry, without a supervisor noticing. This is what makes the fleet restartable.

### 3b. Irreversible actions

Sending is the only thing here that cannot be undone. The agent never sends directly. It writes a `message` row and an `outbox` row **in one transaction**; the Sender claims from `outbox`, calls the provider, and records `provider_message_id` against the `idempotency_key` it already holds. A crash between claim and send retries against the same key and the provider deduplicates.

Without the transaction, a crash between "email sent" and "recorded as sent" double-emails a counterparty — a relationship you burn exactly once.

### 3c. Cross-channel collision

This is the case that makes running channels in parallel dangerous, and the database settles it structurally:

```sql
CREATE UNIQUE INDEX one_open_thread_per_counterparty
    ON thread (tenant_id, counterparty_id)
 WHERE state NOT IN ('closed_won', 'closed_lost', 'closed_no_reply');
```

A creator who is also a curator cannot be worked by the UGC fleet and the curator fleet at the same time. The second fleet's insert fails; it does not need to know the first fleet exists. **Contact discipline becomes a constraint rather than a convention.**

---

## 4. Changefeed topology

```sql
CREATE CHANGEFEED FOR TABLE thread, outbox, message
  INTO 'webhook-https://…'
  WITH initial_scan = 'no', updated, resolved = '5m';
```

| Change | Wakes | Which then |
|---|---|---|
| `thread.state → shortlisted` | Researcher | enriches the counterparty, writes observations |
| `thread.state → approved` | Drafter | retrieves lessons, writes a draft, sets `awaiting_human` |
| `thread.state → queued` | Drafter | writes the `outbox` row (human has approved) |
| `outbox` insert | Sender | sends, records `provider_message_id` |
| `message` insert, `direction=inbound` | Inbox | classifies intent, sets next state |
| `counterparty_observation` insert | Analyst | updates the audience model, may write a `lesson` |

No agent names another agent. The topology is a property of the data.

> ⚠️ **Amended 2026-08-13, on two counts, and the first one would have cost money.**
>
> **`initial_scan` defaults to *on* for a feed with a sink.** As first written, this
> statement does not begin at the present — it replays every existing row of every named
> table through the webhook before it reports anything that has actually happened. On this
> cluster that is every thread ever closed, each arriving as a change, each waking a
> `distil_lesson` claim, each of those a paid embedding call. The feed's job is to say
> *something happened*, and history did not just happen. One option, added above.
>
> **`counterparty_observation` was never built.** §2c's table did not survive migration
> 005's party-first rename, and a `CREATE CHANGEFEED` naming a table that does not exist
> fails at creation. The feed watches the three tables that do. The Analyst row in the
> table above is consequently wired to nothing; it is left standing as the design it was,
> rather than quietly deleted as though it had never been planned.
>
> **Status: built, not created.** `platform/web/rtf_platform/changefeed.py` composes this
> exact statement, parses the webhook batches, maps a change to the set of lead kinds it
> can make claimable, and ships a Lambda handler and a `--verify`. `SHOW CHANGEFEED JOBS`
> still returns **zero rows**, because creating the feed draws RUs continuously against a
> free allowance (§10 risk 2) and that is a spend a human authorises, not a step in a
> deploy. Nothing in that module runs the statement; it prints it. The fleet meanwhile
> wakes on `next_action_at` and a lease — precisely the fallback risk 2 named, running as
> the primary rather than as a contingency.

---

## 5. The fleet

| Agent | Purpose | Reads | Writes |
|---|---|---|---|
| **Scout** | Find counterparties worth approaching | `track_character`, `counterparty` | `counterparty`, `thread(discovered)` |
| **Researcher** | Enrich one counterparty | `counterparty` | `counterparty_observation` |
| **Drafter** | Write the outreach | `lesson`, `track_*`, `counterparty_*` | `message(outbound)`, `thread` |
| **Sender** | Perform the irreversible act | `outbox` | `message.sent_at`, `outbox.state` |
| **Inbox** | Classify replies, decide next action | `message(inbound)` | `thread.state`, `lesson` |
| **Negotiator** | Drive rate and terms to agreement | `thread`, `message`, `lesson` | `message(outbound)`, `thread` |
| **Analyst** | Update the audience model, kill dead hypotheses | `counterparty_observation`, results | `artist_audience`, `lesson` |
| **RemixKit** | Generate the assets a brief needs | `track_*`, artist identity | assets, `agent_run` |

RemixKit is a tool the Drafter calls. It is not a peer.

---

## 6. Retrieval — what justifies the vector index

**R1 — Shortlist.** *Given this track, who should we approach on this channel?* ANN between `track_character.embedding` and `counterparty.profile_embedding`, gated by kind, tenant, freshness, and the absence of an open thread. The filter is a business gate, not a nicety — a shortlist that returns someone already mid-conversation is worse than useless.

> ⚠️ **Amended 2026-08-06.** As first written this query does not work as intended. CockroachDB accelerates a vector-index filter **only on prefix columns, with equality or `IN`** — range comparisons and subqueries fall back to post-filtering. Freshness (a range), the open-thread test (a subquery) and any provenance join therefore would not be accelerated, forcing a `LIMIT k × n` over-fetch that degrades as the index grows.
>
> The fix is a denormalised `counterparty.contact_state` (`contactable | in_thread | stale | declined | unusable`), maintained in the transaction that already opens and closes a thread, so every predicate becomes equality:
>
> ```sql
> CREATE VECTOR INDEX counterparty_shortlist
>     ON counterparty (tenant_id, kind, contact_state,
>                      profile_embedding vector_cosine_ops);
>
> SELECT id, handle FROM counterparty
>  WHERE tenant_id = $1 AND kind = 'creator' AND contact_state = 'contactable'
>  ORDER BY profile_embedding <=> $2 LIMIT 50;
> ```
>
> The denormalisation is safe because it is written in the same serializable transaction as the fact it mirrors. Full detail and the source quotes in [`docs/reference/COCKROACHDB-AI.md`](./reference/COCKROACHDB-AI.md).

**R2 — Lessons for drafting.** *What have we learned that applies here?* ANN over `lesson.embedding`, scoped to this artist, this counterparty kind, and this channel. This is the query that makes campaign *n+1* cheaper than campaign *n*, which is `SCOPE-RESET.md §1`'s entire justification for the artist being the root.

**R3 — Cross-platform identity.** *Is this TikTok handle the same person as that email?* ANN over `profile_embedding`. Useful, not load-bearing; deferred if time is short.

R1 and R2 need ANN and predicates resolved together. Everything the label actually does daily — which tracks have no campaign, which threads are stalled, what a channel costs per agreement — is ordinary SQL over the same tables. **Needing both in one store is the honest reason for this database.**

---

## 7. Channels — parallel by construction, two at launch

Running every channel in parallel is the goal, and the substrate supports it, because **a channel is data, not code**:

```sql
channel_playbook(id, tenant_id, channel, state_machine_json, cadence_json,
                 draft_system_prompt, success_metric, created_at)
```

Adding press or sync is a `channel_playbook` row plus a contact adapter. The spine, the fleet, the coordination and the retrieval are untouched.

**Two channels ship by Aug 18: UGC creators and radio.** Not five, and not one. One channel cannot demonstrate that the substrate is generic; five multiplies integration surface — deliverability, sourcing, station databases — none of which is memory-layer work, against a twelve-day clock. Two proves the claim and produces the cross-channel collision in §3c, which is the sharpest demonstration the architecture has.

---

## 8. Twelve-day plan

| Day | | Deliverable |
|---|---|---|
| 1 | Aug 7 | Cluster via `ccloud`, schema from §2, one round trip: embed → store → retrieve with a predicate. **Measure the RU cost of a filtered vector scan** — closes the open risk in `infra/MEMORY-WORKLOAD.md`. |
| 2–3 | Aug 8–9 | Spine and ingest. One artist, their tracks, analysed once. Measurement, character embedding via Bedrock, rights asserted. |
| 3–5 | Aug 9–11 | Counterparty index and R1. Seeded with real UGC candidates plus labelled synthetic rows to exercise the index at size. |
| 5–7 | Aug 11–13 | **The core.** Thread state machine, lease claiming, transactional outbox, the §3c unique index. Two fleets contending, proven under test. |
| 7–9 | Aug 13–15 | Agents on Bedrock — Scout, Researcher, Drafter, Sender, Inbox — wired entirely through changefeeds. |
| 9–10 | Aug 15–16 | Radio as the second channel: a `channel_playbook` row and one adapter. |
| 10–11 | Aug 16–17 | Human approval by email. MCP server for natural-language catalog queries. |
| 12 | Aug 17 | Package: licence, README, tools-used, diagram, demo URL, <3 min video. **Submit.** |
| — | Aug 18 | Buffer only. |

**If days 7–12 slip, the submission still has its headline criterion**, because days 5–7 are what "memory is integral to agent functionality" actually means here.

**The demo's closing beat:** kill the entire fleet mid-campaign, restart it, and watch every thread resume from its row. Work claims, agent state, memory and the event bus are all rows, so the fleet is stateless and the database is the runtime.

### Requirement coverage

| Requirement | Met by |
|---|---|
| CockroachDB tool 1 | Distributed vector indexing — R1, R2 |
| CockroachDB tool 2 | Cloud Managed MCP Server — natural-language catalog queries |
| CockroachDB tool 3 *(free)* | `ccloud` CLI in the day-1 provisioning path |
| AWS | Bedrock as agent runtime and embedding provider; Lambda for changefeed webhooks |

> ⚠️ **Amended 2026-08-13. Two of these four rows did not survive contact with the
> accounts they were written against, and the submission page says so instead.**
>
> **Bedrock is unreachable on this AWS account, not merely unused.** On-demand inference
> for Titan Text Embeddings V2 is quota `L-26C560CE` at an applied value of **0**, marked
> `Adjustable: false` — there is no increase to request. Batch inference has published
> quotas and no entitlement: `CreateModelInvocationJob` returns *"Your account is not
> authorized to perform this action. Please create a support case"*, reproduced in
> `us-east-1` and `us-west-2` with a real IAM role and a real bucket. Both paths are
> written (`rtf_platform/bedrock.py`) because the entitlement is a switch AWS throws
> rather than work anyone here can do — but the only embedding model that has ever
> produced a row on this cluster is `openai:text-embedding-3-small`. **The AWS requirement
> is carried by Lambda and S3**, which are genuinely deployed. Days 2–3 and 7–9 above
> should be read as the plan they were.
>
> **The Managed MCP Server is development wiring, not an application component.**
> `.mcp.json` points a developer's MCP client at `https://cockroachlabs.cloud/mcp` for
> natural-language inspection of the cluster. Nothing the fleet or the console does goes
> through it, and no user-facing feature depends on it. It is honestly claimable as a tool
> *used*; claiming it as one of the two core CockroachDB capabilities was a category
> error, corrected in `docs/reference/HACKATHON.md` and `docs/submission/TOOLS.md`.
>
> What actually shipped, verified against the cluster and the account rather than read
> from this file, is [`docs/submission/TOOLS.md`](./submission/TOOLS.md).

---

## 9. Deferred

Fan and creator-facing surfaces, attribution links, rewards and payouts. Press, sync and playlist channels — supported by §7, not built. R3 identity resolution. Multi-region topology.

---

## 10. Risks and open decisions

1. **Email deliverability is a time sink with low judging value.** Warmup and domain reputation take longer than twelve days. **Mitigation:** the demo sends only to owned and consented addresses; the outbox proves the mechanism without needing volume.
2. **RU cost of a filtered vector scan is still unverified** — carried forward from `infra/MEMORY-WORKLOAD.md`. Day 1 measures it. Changefeeds are now **confirmed available on Basic and confirmed to consume RUs**; the rate is unpublished, and because a changefeed draws continuously rather than per-query it is the likelier of the two to erode the free allowance. Fallback if it does: poll `thread.next_action_at`, which is adequate at this volume and costs the architecture its elegance, not its function. **Update 2026-08-13:** the rate is still unpublished and still unmeasured, because the fallback is what runs. The feed itself is built — see §4 — and creating it remains a deliberate human act for exactly the reason stated here.
3. **⚠️ Go/no-go: `SET CLUSTER SETTING feature.vector_index.enabled = true` on a Basic cluster.** Vector indexes require that setting, and cluster settings are generally restricted on serverless tiers. If it cannot be set on Basic, either it is on by default there or the tier is wrong — and the entire retrieval design depends on the answer. **This is the first thing to check on day 1, before anything else is built.**
4. **No batching of vector writes.** The documentation states large batch inserts degrade the index, and `IMPORT INTO` is unsupported on tables carrying one. The day 3–5 synthetic backfill must use small `INSERT` batches.
5. **Counterparty acquisition is unresolved** — `SCOPE-RESET.md` open decision 4. `10-creator-indexing.md §4` is a hard "no scraper, ever"; §5 finds manual sound-page browsing both compliant and higher-signal. A human-in-the-loop Scout that surfaces candidates for bulk acceptance is the presumed middle, and is not yet a decision.
6. **The improvement metric will have small N by Aug 18.** Label it with its N. Do not draw a flattering curve — the house rule in `MEMORY-WORKLOAD.md` and `screen_clips.py` applies to our own demo.
7. **Still open from `SCOPE-RESET.md`:** repository topology (2), acquisition method (4), tenancy (6). Licence remains unchosen and is required for submission — Apache-2.0 recommended.
