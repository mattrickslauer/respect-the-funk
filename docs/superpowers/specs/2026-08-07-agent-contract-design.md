---
title: "The agent contract — how a fleet of agents shares one backbone"
subtitle: "The Forager was the first agent. This is what makes it the first of many rather than the only one: three purity layers, five contracts, one polymorphic edge table that carries both invalidation and credit, and three scope tiers so label-wide work has somewhere to live."
status: "DESIGN — extends docs/PLATFORM-SPEC.md and docs/superpowers/specs/2026-08-07-forager-agent-design.md, both of which remain binding. Amends PLATFORM-SPEC §2, §5 and §8; §11 lists every amendment. Resolves SCOPE-RESET.md open decision 4."
date: "2026-08-07"
deadline: "2026-08-18 17:00 EDT"
---

## 0. What this is

`PLATFORM-SPEC.md` names eight agents in a table and specifies none of them. The Forager
design specifies one agent completely. Between those two documents there is a gap: **nothing
says what an agent *is*** — how it gets work, how it contributes to the spine, how its output
becomes someone else's input, and what a new one costs to add.

This document closes that gap. It is a contract, not an agent. Every agent named in
`PLATFORM-SPEC §5` is built against it, and the Forager is its reference implementation.

The test it has to pass: **adding the ninth agent should be a row and a function**, not a new
subsystem.

---

## 1. The repo has already invented the same agent twice

`thread` (`PLATFORM-SPEC §2d`) and `lead` (Forager §4) were designed a day apart, for
unrelated purposes, by way of unrelated reasoning. They came out the same shape.

| | `thread` | `lead` |
|---|---|---|
| What it is | one conversation with one counterparty | one thing worth fetching |
| Claimed by | `owner_agent`, `lease_expires_at` | same |
| Scheduled by | `next_action_at` | same, plus `cadence_seconds` |
| Advanced by | a state machine | a state machine |
| Woken by | a changefeed on the row | a changefeed on the row |
| Retried by | lease expiry | lease expiry |

That convergence is the whole modularity story, and it is worth naming before it becomes
three near-identical implementations that drift. Written down as a contract, the shared part
is written once and every future agent inherits it.

**The consequence for schedule:** this contract exists to make `PLATFORM-SPEC §8`'s day 7–9
block *cheaper*, not to add a layer that block has to learn. If it does not make the fifth
agent obviously faster to build than the second, it is wrong.

---

## 2. What an agent is — three layers, each pure with respect to a different thing

| Layer | Touches the network | Touches the database | Makes decisions |
|---|---|---|---|
| **Adapter** | yes | **no** | no |
| **Handler** | **no** | **no** | yes |
| **Runtime** | no | yes | no |

The Forager already established the first row — `fetch(lead, credentials) -> FetchResult`,
no database access, which is what makes an adapter testable against recorded fixtures with
zero network. This applies the same discipline one level up.

### 2a. A handler never writes to the database

```
handle(work_row, ctx) -> Effects(
    facts[],        # typed fact rows, each with the basis edges under it
    metrics[],
    work[],         # new work rows, in any table
    transitions[],  # state changes on the row that was claimed
)
```

The handler returns what it *wants* to happen. The runtime applies it, in **one transaction**,
for every agent in the fleet.

Two things fall out of that, and they are the reason for the shape:

1. **A handler is testable with no database and no network.** Hand it a row, assert on the
   `Effects` it returns. No fixtures beyond the row itself, no cleanup, no ordering.
2. **The transaction boundary is written once and cannot be got wrong per-agent.** The failure
   mode this prevents is the one `PLATFORM-SPEC §3b` already identified for sending — a crash
   between the act and the record of the act. Every agent gets that discipline for free rather
   than by remembering.

### 2b. Everything else belongs to the runtime

Claiming, leasing, lease expiry, the retry ladder, budget enforcement, `agent_run` accounting,
dedup, and the changefeed wiring. One module. Written once.

### 2c. An agent is therefore a row plus a function

```sql
agent_manifest(
  kind                      TEXT PRIMARY KEY,  -- forager|distiller|scout|drafter|…
  work_table                TEXT   NOT NULL,   -- lead | thread | outbox | chunk_staging
  claim_state               TEXT   NOT NULL,   -- the state it claims
  scope_kinds               TEXT[] NOT NULL,   -- tenant | artist | track  (§7)
  batch_size                INT    NOT NULL,
  concurrency               INT    NOT NULL,   -- workers of this kind
  max_concurrent_per_artist INT    NOT NULL,   -- the fairness cap (§3b)
  lease_seconds             INT    NOT NULL,
  max_attempts              INT    NOT NULL,
  backoff_seconds           INT[]  NOT NULL,
  adapters                  TEXT[],            -- capabilities it may call
  writes                    TEXT[],            -- subject kinds it may write facts about
  requires_human            BOOL   NOT NULL,   -- gates §3b irreversible acts
  enabled                   BOOL   NOT NULL,
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

This is `PLATFORM-SPEC §7`'s own argument applied one level up. §7 says **"a channel is data,
not code"** — adding press is a `channel_playbook` row plus a contact adapter. The same is true
of an agent, and for the same reason.

Two of those columns do more than configuration work:

**`writes` is a declared blast radius.** The runtime rejects a handler writing outside it. The
Drafter cannot quietly start emitting track measurements. And when something wrong turns up in
the store, the manifest already narrows which handlers could possibly have put it there — which
is the question you actually ask at 2am.

**`enabled` means an agent is switched off with an `UPDATE`, not a deploy.** Combined with the
lease, that is a clean drain: it stops claiming, finishes what it holds, and goes quiet.

### 2d. Abstract over the artist

No handler ever names an artist. Every work row carries `(tenant_id, artist_id, track_id)`, the
runtime passes them down, and per-artist behaviour lives in `artist_budget`, `artist_profile`
and `dimension_policy` — data, not code, not deployments.

One Forager implementation, N artists. This is the property that makes the roster a
configuration rather than a fleet size.

---

## 3. The work contract

### 3a. What runs in parallel, and what contends

| | Mechanism | Contention |
|---|---|---|
| Different artists | disjoint rows, disjoint index ranges | none |
| Same artist, different work | `FOR UPDATE SKIP LOCKED` | none — the second worker takes different rows |
| Same track, different agents | different work tables entirely | none |

`FOR UPDATE SKIP LOCKED` is verified on our cluster (`apps/spindle/README.md`, item 4).

Two places where naive parallelism breaks. Both are specified here rather than discovered
later.

### 3b. One artist must not starve the roster

The Forager's claim query is `ORDER BY next_action_at LIMIT $batch` with no per-artist bound.
An artist mid-launch with ten thousand pending leads takes every worker and the rest of the
roster gets nothing.

**Two-step claim.** Pick the bucket, then claim inside it:

```sql
-- step 1: which scopes have ready work, longest-waiting first
SELECT scope_kind, artist_id
  FROM lead
 WHERE tenant_id = $1 AND state = 'pending' AND next_action_at <= now()
 GROUP BY scope_kind, artist_id
 ORDER BY min(next_action_at)
 LIMIT $buckets_per_round;

-- step 2: the existing claim, scoped — hits lead_claimable directly
UPDATE lead
   SET owner_agent = $agent, lease_expires_at = now() + $lease
 WHERE id IN (SELECT id FROM lead
               WHERE tenant_id = $1 AND artist_id = $2
                 AND state = 'pending' AND next_action_at <= now()
               ORDER BY score DESC, next_action_at
               LIMIT $per_artist
                 FOR UPDATE SKIP LOCKED)
RETURNING *;
```

Two trivial queries rather than one clever one. A single-query version using a window function
or `LATERAL` may work, but a locking clause alongside window functions is restricted in
Postgres and **has not been verified on our cluster** (§13). The two-step needs no exotic SQL
and makes the fairness knob explicit, tunable, and visible in the manifest.

### 3c. The budget row is the hot spot, per artist

If every worker reads-and-decrements a counter in `artist_budget`, that row is a serialization
point. Under `SERIALIZABLE` — which is the default here and is the reason `PLATFORM-SPEC §1`
gives for this database existing — concurrent workers on the same artist retry against each
other. Ten workers on one launching track is exactly when it bites, which is exactly when you
do not want it to.

**Do not decrement. Compute spend from `agent_run`**, which is append-only and already written:

```sql
SELECT coalesce(sum(tokens_in + tokens_out), 0)
  FROM agent_run
 WHERE artist_id = $1 AND started_at > now() - INTERVAL '1 hour';
```

Read-only, no write conflict, no hot row. The cost is that a worker can overshoot by one batch.
For a crawl budget that is the right trade — and per the Forager's §7b, the overshoot is
**recorded in `agent_run`, not hidden.**

`artist_budget` stays as the table holding the *limits*. It is read, never written, on the hot
path.

---

## 4. The capability contract

Unchanged from the Forager design, generalised to the fleet:

```
fetch(input, credentials) -> result
```

**No database access.** Every adapter in the system, for every agent. A contact adapter that
sends an email, a Spotify adapter that reads a catalogue, and a search adapter are the same
shape.

The registry is named in `agent_manifest.adapters`, and the runtime refuses a handler that
reaches for one not on its list. Same argument as `writes`: the capability set is declared, so
it can be audited without reading the handler.

---

## 5. The write contract

`SCOPE-RESET §2a` states the two rules. Rule 1 — every fact carries how it was obtained — is
already structural: `PLATFORM-SPEC §2b` gives the three provenance classes three different
storage shapes *"so mixing them is a schema error rather than a discipline failure."*

**That stays.** A measured BPM lives in a table with no `confidence` column, because a measured
BPM does not have one. A demographic estimate lives in a table with `error_bar_pp` and
`sample_size`, because it needs them. A single polymorphic fact table would have to hold the
union of every column any fact needs, leave most of them NULL, and demote `provenance` to a
string that convention has to police. Considered and rejected.

What is missing is rule 2's other half — **what a fact stands on.**

### 5a. One polymorphic edge table

The Forager's `fact_basis` keys only on `artist_fact`. Generalised:

```sql
fact_basis(
  subject_kind TEXT NOT NULL,   -- artist_fact|counterparty_observation|track_character
                                -- |artist_audience|lesson|outcome
  subject_id   UUID NOT NULL,
  basis_kind   TEXT NOT NULL,   -- chunk|document|metric|message|verdict|profile
                                -- |artist_fact|counterparty_observation|lesson
  basis_id     UUID NOT NULL,
  weight       REAL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (subject_kind, subject_id, basis_kind, basis_id),
  INDEX fact_basis_down (basis_kind, basis_id)
)
```

The primary key serves the **up** walk — *what does this rest on*. The secondary index serves
the **down** walk — *what rests on this*. Two indexes, two directions, one table.

No foreign keys, deliberately: the subject and basis span a dozen tables and a polymorphic FK
does not exist. The referential discipline is the runtime's, which is the same trade
`counterparty_observation` already makes.

### 5b. Invalidation walks down

Woken by a changefeed when any fact's status flips to `stale` or `retracted`:

```sql
WITH RECURSIVE affected(subject_kind, subject_id, depth) AS (
  SELECT subject_kind, subject_id, 1
    FROM fact_basis
   WHERE basis_kind = $1 AND basis_id = $2
  UNION
  SELECT b.subject_kind, b.subject_id, a.depth + 1
    FROM fact_basis b
    JOIN affected a ON b.basis_kind = a.subject_kind
                   AND b.basis_id   = a.subject_id
   WHERE a.depth < 8
)
SELECT * FROM affected;
```

`UNION` rather than `UNION ALL`, and a depth cap, because **cycles are real**: a lesson learned
from an outcome that the lesson helped produce is a legitimate shape, not a bug.

It marks `stale`. **It never deletes.** The Forager's rule holds for the whole fleet — a purge
destroys exactly the audit trail a label needs when a campaign goes sideways.

And it does not know which agent wrote anything. It walks edges. `PLATFORM-SPEC §0` holds
unchanged.

### 5c. The rule with teeth

**The runtime rejects any `inferred` fact whose handler returned no basis edges.** `measured`
facts cite their document or metric; `asserted` facts cite the human who said it. Nothing gets
to be believed without saying what it stands on.

This is the mechanical form of `SCOPE-RESET §2a` rule 2. The repo has already failed this rule
once — `MEMORY-SPEC §1`'s diagnosis was *"the identity is a YAML file that is read and never
written back to."* A rule enforced by the runtime cannot be forgotten by the fifth agent.

---

## 6. The wake contract

Unchanged from `PLATFORM-SPEC §0` and §4: an agent's write inserts work into a table it does
not name, and a changefeed carries it. No agent names another agent, there is no orchestrator,
and there is no message broker.

The manifest makes the topology inspectable rather than implicit — `work_table` and
`claim_state` on one side, `writes` on the other, so the graph can be rendered from the
database instead of from a diagram somebody maintains by hand.

**The fleet runs correctly with changefeeds off.** The claim query already polls
`next_action_at`. This is the fallback `PLATFORM-SPEC §10` risk 2 names, and it costs the
architecture its elegance, not its function.

---

## 7. Three scope tiers

A label-wide creator sweep has no artist. `counterparty` in `PLATFORM-SPEC §2c` is already
tenant-scoped with no `artist_id`, so the spine supports this — but `lead` requires an
`artist_id` and therefore cannot express it.

```sql
scope_kind TEXT NOT NULL,          -- tenant | artist | track
artist_id  UUID REFERENCES artist(id),
track_id   UUID REFERENCES track(id),
CHECK ( (scope_kind = 'tenant' AND artist_id IS NULL     AND track_id IS NULL)
     OR (scope_kind = 'artist' AND artist_id IS NOT NULL AND track_id IS NULL)
     OR (scope_kind = 'track'  AND artist_id IS NOT NULL AND track_id IS NOT NULL) )
```

Carried on every work table and every fact. The database enforces the tier rather than the
application remembering it.

§3b's claim then picks a **bucket**, not an artist, so a launch week cannot starve the
label-wide sweep and the sweep cannot starve a launch.

---

## 8. Memory is artist-rooted. Attention is track-rooted.

`SCOPE-RESET §1` fixed the artist as the spine, and the argument holds: what *accumulates*
between releases is counterparty relationships, the audience model and lessons, and all three
survive the track. But that is an argument about **where memory lives**, not about **where
effort goes**. `PLATFORM-SPEC` already agrees — `campaign(id, tenant_id, artist_id, track_id,
channel, goal, …)` is per-track.

The Forager's `lead` has `artist_id` and no `track_id`. Under a goal of *making tracks perform*
that is a gap, and it is a nullable column:

```sql
ALTER TABLE lead ADD COLUMN track_id UUID REFERENCES track(id);  -- NULL = artist-level
```

With it the Forager can forage for a release rather than only for an act: who is covering this
single, who reviewed it, per-track stream and view polling on a cadence, who used this sound.
And budget can follow the release — a track in its launch window gets crawl budget that
back-catalogue does not.

**The artist is where knowledge compounds. The track is where the fleet spends.**

---

## 9. The feedback contract

The system's outputs are emails — to UGC creators, radio programmers, playlist curators,
blogs. Its response is supposed to be the track performing. That is a real loop, and it is the
one place where this design can go quietly wrong in a way that looks like it is working.

`SCOPE-RESET §5` keeps **"causal attribution is impossible"** as a surviving research finding.
`PLATFORM-SPEC §10` risk 6 says do not draw a flattering curve. A scorer trained on stream
count learns whatever *correlates* with stream bumps, and pillar research already measured
where that road ends: bought engagement, ~50× EV-negative.

So the loop is a **ladder**, and the fast rungs are the ones that train it.

### 9a. Four rungs

| Rung | Signal | Latency | Attributable | Used for |
|---|---|---|---|---|
| **1 Response** | replied, said yes, said no, and why | hours–days | yes, cleanly | **training** — pitch angle, targeting, playbook |
| **2 Delivery** | they actually posted, spun, published, added | days–weeks | yes, verifiable | **training** — was the yes real |
| **3 Local effect** | *their* post did 40k; market Shazams moved | weeks | partially | **training, discounted** |
| **4 Streams** | the track's stream count | weeks–months | no, confounded | **reporting only** |

Rung 4 is the goal and is the worst possible trainer: low frequency, high variance, confounded
by playlist adds, algorithmic push, seasonality and our own five channels firing at once.

### 9b. Rung 2 closes the loop through the Forager

The thread state machine already ends `delivered → verified`. The thing that *verifies* is a
crawl: did the post go up, did the station log the spin, did the blog publish. That is a `lead`.

So the Forager is not only the intake organ — **it is the sensor on the output.** Same agent,
same contract, opposite end of the loop. No new machinery.

### 9c. The edge table runs both directions

```
recursive CTE walking UP    ->  "why do we believe this?"      (provenance)
recursive CTE walking DOWN  ->  "what produced this outcome?"  (credit)
```

An outcome fact — *this creator posted, and the post did 40k* — carries basis edges to the
thread, the playbook, the pitch angle and the counterparty selection that got it there. Walking
down from the outcome returns everything that was involved.

**The honest caveat: "involved" is not "caused."** On rungs 1 and 2 the action and the outcome
are adjacent enough that the distinction rarely matters. On rung 4 it matters entirely.

### 9d. The system declines to draw the edge it cannot defend

Stream counts land in `artist_metric` as an append-only time series **with no basis edges to
any thread.** Not an oversight — a statement. It is the same move the Forager already makes
when it raises a `contradiction` rather than auto-resolving an asserted-vs-measured conflict:
where the data does not support a conclusion, the schema declines to encode one.

### 9e. Holdouts are the only real causality available

```sql
ALTER TABLE thread ADD COLUMN holdout BOOL NOT NULL DEFAULT false;
```

Shortlist 100, contact 85, deliberately do not contact 15, compare. It costs nothing but
discipline and it is the difference between *"streams rose during the campaign"* and something
that can be put in front of a label.

### 9f. The slow rung audits the loop; it does not drive it

A months-long outer rung against a small roster means rung 4 has tiny N for a long time — the
same problem `PLATFORM-SPEC §10` risk 6 already names. So: **learn on rungs 1–3, and use rung 4
as a drift check.** If the fast rungs say we are winning and streams flatly disagree across
several releases, the fast rungs are measuring the wrong thing. That is a real signal. It is
just a slow one.

---

## 10. The counterparty index and the conversation of record

The label needs a standing database of everyone who can help get the music out — UGC creators,
people who run playlists, blogs, programmers — with the state of the relationship, held in one
place.

`counterparty` is already tenant-scoped with no `artist_id`. It is a label asset by
construction. Two things it is missing.

### 10a. How we got them, per contact

```sql
counterparty_contact ADD
  source_kind       TEXT,   -- bio_business_email|press_page|api|label_roster|inbound|asserted
  source_url        TEXT,
  source_lead_id    UUID REFERENCES lead(id),
  captured_at       TIMESTAMPTZ,
  suppressed_at     TIMESTAMPTZ,
  suppressed_reason TEXT     -- unsubscribe / do-not-contact; permanent, survives everything
```

`source_lead_id` is the load-bearing one. The Forager's attention trail — the recursive CTE up
`parent_lead_id` — already answers *"why were we even looking here?"* So for any person in the
index the system can produce the exact chain of hops that surfaced them.

**That is the provenance record, and it is simultaneously the compliance artifact** — the thing
that answers *"where did you get my address"* with a record rather than a shrug.

### 10b. This resolves `SCOPE-RESET` open decision 4

Open decision 4 — counterparty acquisition method — has been the most urgent open decision
since the reset, because it gates the Scout.

The risk that ends companies is not reading a public page. It is storing personal data with no
lawful basis and contacting people who never opted in. So:

- **Documented APIs where they exist** — Spotify, YouTube Data, Instagram Graph for owned
  accounts. Not scraping.
- **Public search** for discovery. Not scraping.
- **Business-inquiry contact from public professional surfaces only** — the "for business"
  address in a bio, a press page, a label roster — never a personal address inferred or
  guessed.
- **`mode = 'manual'` for the places with no compliant API.** Pillar 10 §5 already found manual
  sound-page browsing both compliant and the highest-signal path, so this is not a concession.
- **Every contact carries its source, and suppression is permanent.**

Pillar 10 §4's *"no scraper, ever"* survives intact.

### 10c. The conversation of record, and where it leaks

The rule: **if it is not a `message` row, it did not happen.**

Outbound already works — `PLATFORM-SPEC §3b`, a `message` row and an `outbox` row in one
transaction. Inbound needs **per-thread reply addressing** (plus-addressing or VERP) so replies
route back deterministically rather than being parsed out of a shared mailbox.

The leak is human. Someone DMs a creator from their phone and the record rots. So logging
after-the-fact contact must be **one action, not a form** — a `message` row with
`direction='outbound'`, `provenance='asserted'`, `logged_by=<human>`. If that is harder than
sending the DM, people route around the system and the asset being built is lost.

### 10d. One consequence of §3c worth knowing now

`one_open_thread_per_counterparty` spans the whole tenant. So two artists on the roster cannot
work the same creator at the same time — the second campaign's insert fails, structurally.

That is correct and it is the right default. It also makes the creator pool a **scarce shared
resource across the roster**, which needs a precedence rule. Default: the campaign with the
nearer release date, ties broken toward the artist with no prior relationship to that
counterparty. It belongs in `channel_playbook` as a knob, not in code.

---

## 11. Amendments this forces on the binding documents

| Document | Change |
|---|---|
| `PLATFORM-SPEC §2` | Add `agent_manifest` (§2c above) and polymorphic `fact_basis` (§5a). Add `scope_kind`/`track_id` with their `CHECK` to work tables (§7). Add `thread.holdout` (§9e) and the `counterparty_contact` source columns (§10a). |
| `PLATFORM-SPEC §5` | The fleet table gains the Forager, the Distiller and the Invalidator — ten agents, not eight. Each row gains its work table, scope tiers and manifest state. |
| `PLATFORM-SPEC §8` | **Replace the twelve-day calendar with §12's dependency graph.** It prices work in human-team days and has no room for three of the ten agents. Keep only the Aug 18 deadline and the submission packaging checklist. |
| `PLATFORM-SPEC §10` risk 3 | **Closed.** `apps/spindle/README.md` verified `feature.vector_index.enabled` is already `t` on `respect-the-funk-31317`. |
| `SCOPE-RESET §6` decision 4 | **Resolved** by §10b. |
| Forager design §4 | `lead` gains `track_id`, `scope_kind`, and the two-step claim replaces the single-query claim. |

`SCOPE-RESET §6` decisions 2 (repository topology) and 6 (tenancy) stay open. `apps/spindle/README.md`
defers 2 explicitly until both deadlines pass, which stands.

---

## 12. The plan

Not a day grid. The only things that genuinely consume wall-clock are unknowns that need
measuring, humans doing account admin, and the outside world replying at its own speed.

### 12a. Gates — checks, not builds, and two of them can invalidate design

| Gate | What | If it fails |
|---|---|---|
| **A** | ~~vector index enabled on Basic~~ | **Closed.** Already `t` on our cluster. |
| **B** | RU cost of a filtered vector scan; RU draw of an idle changefeed | Poll `next_action_at`. §6 notes the fleet runs correctly without changefeeds. |
| **C** | Parallel insert into a vector-indexed table | `chunk_staging` and its paced mover stay. **If it is clean, delete `chunk_staging`** — it exists only to hedge an unmeasured risk. |
| **D** | Instagram account is Business or Creator with a linked Page | Human account admin. Blocks one adapter, nothing else. |
| **E** | SPF/DKIM/DMARC on the sending domain | DNS propagation. Blocks live send, not the outbox mechanism. |

B and C are the real ones and both need row volume, so they are measured against the first
real crawl rather than against an empty table. D and E are not build-blocked and should start
immediately for exactly that reason.

### 12b. The trunk — narrow, serial, and it is the whole backbone

1. **One migration.** `PLATFORM-SPEC §2`, the Forager's §5, and this document's additions.
2. **One runtime module:**

```
claim(manifest)          two-step fairness claim, lease, SKIP LOCKED
reap()                   expire dead leases
invoke(handler, rows)    handler returns Effects, touches nothing
apply(effects)           ONE transaction; enforces the `writes` blast radius,
                         rejects inferred facts carrying no basis edges
spend(artist_id)         sum over agent_run — no hot counter row
adapters                 registry; fetch(input, creds), no DB access
```

Nothing else is serial.

### 12c. The fan-out — each is a handler plus adapters

| Agent | Shape | Proves |
|---|---|---|
| **Forager** | handler + `spotify`, `musicbrainz`, `web_search`, `web_page`, `instagram_owned` | work contract, capability contract, scope tiers |
| **Distiller** | handler, no adapters — chunk → facts + basis edges | write contract, changefeed wake, the basis graph |
| **Invalidator** | handler, no adapters — the §5b recursive CTE | that it is one system and not ten |
| **Scout** | handler, tenant-scoped | label-wide tier, counterparty index, acquisition provenance |
| **Drafter → Sender → Inbox** | three handlers, one contact adapter | thread machine, §3c collision, transactional outbox, conversation of record |

Plus the **MCP server** (submission requirement — CockroachDB tool 2) and the **retrieval
queries**, whose `EXPLAIN` output is kept as evidence.

**Seven of the ten. Researcher, Negotiator and Analyst are deferred before the build starts** —
each needs a counterparty relationship that has already run somewhere, and none of them is
needed to show that the substrate is generic.

### 12d. What stays hardcoded, deliberately

| Hardcode | Why it is fine |
|---|---|
| Radio station list | A static seed CSV *is* the adapter. §7's "a channel is data" claim does not need a station-database integration to be true. |
| Spotify, MusicBrainz, YouTube fetches | Documented APIs. Request, parse, done. No model in the loop. |
| Metric polling | `cadence_seconds` on a lead, an HTTP call, an append to `artist_metric`. |
| The lead scorer | Deterministic and hand-tuned by design (Forager §7a). Verdicts train it later. |
| Chunking | Deterministic. |

**What must not be hardcoded, because it is the product:** the Distiller's fact extraction, the
Drafter's pitch, the Inbox's reply classification.

### 12e. The one thing that cannot be compressed

Rung 1 of the ladder is *someone replies*, and that takes days no matter how fast anything is
built. Rungs 2–4 take weeks to months. **The loop cannot be demonstrated live by Aug 18.**

So the demo replays a recorded conversation through the *real* state machine, real outbox and
real invalidator — mechanism real, timeline compressed — alongside one or two genuinely live
threads to owned and consented addresses. **Labelled as exactly that.** `PLATFORM-SPEC §10`
risk 6 applies to our own demo.

### 12f. Cut order

Researcher, Negotiator and Analyst are already out (§12c). Among what is *in*, cut in this
order: **holdout cohorts → radio as the second channel → Scout.**

Scout is last because losing it costs the label-wide tier a demonstration, and the tier is the
part of this contract that `PLATFORM-SPEC` had no way to express. Radio goes before it because
`PLATFORM-SPEC §7`'s "a channel is data" claim is provable from a `channel_playbook` row even
if nothing sends through it.

**Never cut:** the migration, the runtime, Forager, Distiller, Drafter/Sender/Inbox, the
Invalidator, the MCP server, and the kill-the-fleet-and-restart beat. Those are the headline
criterion — *memory is integral to agent functionality* — and everything else sits on top of it.

---

## 13. What is not verified

Marked rather than guessed, per `SCOPE-RESET §5` rule 2.

1. **A single-query fair claim** using a window function or `LATERAL` alongside `FOR UPDATE
   SKIP LOCKED`. Restricted in Postgres; untested on CockroachDB v26.2.5. §3b's two-step needs
   no exotic SQL and is the specified path regardless.
2. **Recursive CTE performance over `fact_basis`** at real edge counts. The depth cap bounds
   it, but the constant is unmeasured.
3. **RU cost of a filtered vector scan, and changefeed RU draw** — carried forward from
   `PLATFORM-SPEC §10` risk 2 and `apps/spindle/README.md`. Both need row volume.
4. **Vector-index degradation under parallel insert** — `PLATFORM-SPEC §10` risk 4. Determines
   whether `chunk_staging` survives.
5. **Per-thread reply addressing** (§10c) has not been tested against any provider.

---

## 14. Open questions

1. **Does `agent_manifest` hold prompts?** Currently no — operational envelope only, handlers
   stay code. A prompt in a row is a prompt nobody diffs, reviews or tests. Revisit when there
   is a second operator who needs to tune one without a deploy.
2. **Counterparty precedence across the roster** (§10d) — the default is stated; it has not been
   tested against a real scheduling conflict.
3. **`weight` on `fact_basis`** is specified and unused. v1 walks edges unweighted. It is there
   so adding it later is not a migration.
4. **Where the Invalidator's depth cap should sit.** 8 is a guess bounded by the cycle argument,
   not a measurement.

---

## 15. Relationship to the binding documents

| Document | Status |
|---|---|
| `docs/SCOPE-RESET.md` | **BINDING.** This resolves its open decision 4 and honours §2a, §3 and §5 unchanged. |
| `docs/PLATFORM-SPEC.md` | **BINDING.** This amends §2, §5 and §8 per §11 and honours §0 exactly — no agent names another. |
| `docs/superpowers/specs/2026-08-07-forager-agent-design.md` | **BINDING.** This generalises its adapter rule, its `fact_basis`, its budget governor and its claim query to the fleet, and amends its `lead` table per §11. |
| `apps/spindle/README.md` | **REFERENCE — verified facts.** Its item 4 and its vector-index finding are load-bearing here. Its freeze on `app/`, `content/` and `infra/` stands. |
| `docs/research/01–13` | **REFERENCE — findings stand.** §9 and §10b are built on pillars 01 and 10 rather than around them. |
