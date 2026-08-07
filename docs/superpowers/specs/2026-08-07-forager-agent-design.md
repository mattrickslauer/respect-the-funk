---
title: "The Forager — the first agent, and the artist context substrate it builds"
subtitle: "A frontier-queue research agent that assembles the internet's picture of an artist into a RAG-searchable, provenance-marked, self-invalidating store. Discovery and recurring measurement are one system. The agent's plan is a table, which is why it can be restarted, audited, and asked why it was ever looking somewhere."
status: "DESIGN — approved in brainstorming 2026-08-07. Extends SCOPE-RESET.md §3 (agentic by design) and PLATFORM-SPEC.md §3/§4/§5. Proposes a resolution path for SCOPE-RESET.md open decision 4 without requiring it up front."
date: "2026-08-07"
deadline: "2026-08-18 17:00 EDT"
---

> **On seven pages, for overview and audit: [`forager-vision.pdf`](./forager-vision.pdf)** —
> vision, engineering, architecture, why it scales, and the submission case. Generated from
> [`forager_vision.py`](./forager_vision.py); this document is its source of record, and where
> the two disagree this one wins.

## 0. What this is

The first agent in the fleet. Not the Scout.

`PLATFORM-SPEC.md §5` lists eight agents and puts **Scout** first in the table. Building Scout
first requires resolving `SCOPE-RESET.md` open decision 4 — counterparty acquisition — which is
marked *"the most urgent of these"* precisely because it is unresolved and blocking.

The Forager inverts that. It researches **our own artists**, from sources we are entitled to
read, and produces the artist context every other agent in §5 will query. Open decision 4 stops
being a gate and becomes a lead type inside a mechanism that already exists.

**One sentence:** the Forager expands a queryable frontier of leads outward from an artist's
known identifiers, turning each lead into evidence, claims, measurements — and more leads.

---

## 1. The vision this serves

An **agentic OS for a record label**. Not a tool a person triggers; a fleet that is always
running, always accumulating, and whose accumulated knowledge is what makes release *n+1*
cheaper and sharper than release *n*.

`SCOPE-RESET.md §1` fixed the artist as the spine on exactly that economic claim: what compounds
between releases is not the audio, it is the relationships, the audience model, and the lessons.
All three are artist-level, and all three have to come from somewhere.

**The Forager is where they come from.** It is the agent that makes the spine non-empty. Every
other agent in the fleet is a consumer of what it writes:

| Agent | Reads from the Forager |
|---|---|
| Scout | `entity` leads that resolved to people with a reachable contact |
| Researcher | the enrichment pattern, verbatim — same frontier, different lead kinds |
| Drafter | facts, quotable chunks, and the artist's own voice |
| Analyst | metric time series, and the audience hypotheses the Distiller wrote |
| RemixKit | artist identity, catalogue, brand voice |

An artist dossier that no human assembled, that cites its sources, that knows how old each of
its claims is, and that tells you when the world moved underneath it.

---

## 2. Why a frontier and not a crawler

Three approaches were considered.

**A — Scheduled crawler.** A per-artist list of enabled sources, walked on a cadence. Predictable
cost, two days of work, trivially correct. And it can only ever find what someone already pointed
it at. It never discovers the fan community nobody knew existed.

**B — Frontier-queue forager.** *Chosen.* The unit of work is a **lead**: a typed candidate with a
parent, a reason, and a score. Adapters turn leads into evidence *and into more leads*. The graph
expands on its own; scoring and budget bound it.

**C — LLM-directed gap-filling.** Hand a model the current dossier each run, ask what is missing,
let it search. Produces a good narrative and re-derives its plan from scratch every time. Its plan
lives in a context window rather than in the database — the opposite of the claim this project is
making.

**B was chosen for four reasons that are specific to this system, not general preference:**

1. **It needs no new coordination primitives.** `lead` is a work item with `owner_agent` and
   `lease_expires_at`, claimed by the query in `PLATFORM-SPEC §3a` with `thread` → `lead`. The
   same lease, the same `FOR UPDATE SKIP LOCKED`, already verified working against the real
   cluster (`platform/README.md`, item 4).
2. **The frontier is the memory.** The hackathon's headline criterion is that memory be integral
   to agent functionality. Here the agent's entire plan is rows. Kill it mid-crawl, restart, it
   resumes — `PLATFORM-SPEC §8`'s closing demo beat, available on day 3 instead of day 12.
3. **It becomes the Scout for free.** A lead that resolves to a person with a contact method *is*
   a counterparty candidate.
4. **It gives human judgment somewhere to attach.** Relevant / not-relevant is a verdict on a
   lead or a fact, and verdicts are training data for the scorer that gates the frontier. Without
   a frontier the button has nothing to teach.

**A is not discarded — it is absorbed.** See §4: one nullable column makes recurring measurement
(stream counts, view counts, follower counts) the same table, the same claim query, and the same
worker as discovery.

**A thin slice of C is folded in**, as one lead kind: when coverage of a dimension is thin, a model
proposes `gap_query` leads. The model's judgment enters as a lead *generator*, never as the
control loop.

---

## 3. Configuration — every artist is a different shape

Some artists have nine platforms. Some have two. Some have a TikTok presence made entirely of
other people's posts. The configuration has to make that ordinary rather than exceptional.

```sql
artist_profile(
  id, tenant_id, artist_id,
  platform,              -- instagram|tiktok|spotify|youtube|bandcamp|musicbrainz|web
  mode,                  -- owned | unowned | absent
  platform_user_id, handle, profile_url,
  credential_ref,        -- name of the secret holding an OAuth grant; NULL unless owned
  enabled,
  confirmed_by, confirmed_at,       -- a human asserted this is really them
  supersedes_id,
  created_at)
UNIQUE (tenant_id, artist_id, platform, handle)
```

| mode | means | what the Forager does |
|---|---|---|
| `owned` | we hold the OAuth grant | first-party reads: our media, comments on it, @mentions, tagged posts, real insights |
| `unowned` | the artist has an account, we have no grant | public reads only |
| `absent` | the artist has no account on this platform | **still sweeps the platform for fan activity** |

**`absent` is a positive assertion, not a missing row.** "This artist has no TikTok" is something
a human stated, with a timestamp and an author — asserted provenance, per `SCOPE-RESET §2a` rule 1.
A missing row means *we never checked*, which is a completely different fact and must not be
confused with it.

**Absence is a signal, not a gap.** An artist with measurable fan activity on a platform where
they have no presence is an audience being served by nobody. That is one of the most actionable
things a label OS can surface, and it falls out of the schema rather than being a special report.

`enabled` per row is the optional configuration. An artist with three platforms and an artist with
nine run identical code.

---

## 4. The frontier

```sql
lead(
  id, tenant_id, artist_id,
  kind, mode,            -- mode: auto | manual
  target, target_hash,   -- URL, handle, query, sound id — hash is of the normalised target
  platform,
  parent_lead_id, depth, reason,
  score,
  state,                 -- pending|claimed|done|failed|rejected|blocked_manual
  cadence_seconds,       -- NULL = one-shot forage; set = recurring refresh
  next_action_at,
  owner_agent, lease_expires_at,
  attempts, last_error,
  created_at, updated_at)
UNIQUE (tenant_id, artist_id, target_hash)
```

### 4a. One column absorbs the scheduled crawler

`cadence_seconds IS NULL` — a one-shot discovery lead. Fetch it, distil it, the lead is `done`.

`cadence_seconds IS NOT NULL` — a recurring measurement. Spotify monthly listeners at 86400,
YouTube view count at 86400, follower count at 86400. The lead never dies; on completion it
reschedules itself into `next_action_at`.

**Same table, same claim query, same worker.** Discovery and routine metric polling are not two
subsystems that need to agree with each other. That is the whole of approach A, in one nullable
column.

### 4b. Lead kinds

| kind | what it is |
|---|---|
| `profile` | resolve or refresh a platform profile |
| `catalogue` | releases and tracks from a metadata source |
| `metric` | recurring measurement — streams, views, followers, playlist position |
| `engagement` | comments, @mentions, tagged media on **owned** accounts |
| `mention_search` | public search for artist/track names on any platform, including `absent` ones |
| `fan_artifact` | a specific fan post, cover, edit, or playlist |
| `document` | a web page to fetch and read |
| `entity` | a person or handle worth resolving — **this is the Scout seam** |
| `gap_query` | a model-proposed query for a thin dimension |

### 4c. Dedup as a constraint

`UNIQUE (tenant_id, artist_id, target_hash)` makes "the same target is never queued twice" a
property of the database rather than a discipline the code has to remember. This is the same move
as `PLATFORM-SPEC §3c`'s partial unique index on open threads: contact discipline and crawl
discipline are both constraints, not conventions.

Two Foragers discovering the same handle from different parents in the same instant is the case
this settles. One insert wins, the other retries and merges its provenance onto the surviving row.

### 4d. Claiming

`PLATFORM-SPEC §3a`, verbatim, with `thread` → `lead`. A lease rather than a lock: a Forager that
dies mid-fetch releases its work by expiry, with no supervisor involved.

The frontier is mostly `done` rows within a week, so the claim path gets a partial index:

```sql
CREATE INDEX lead_claimable ON lead (tenant_id, artist_id, next_action_at)
  WHERE state = 'pending';
```

### 4e. `mode = 'manual'` — where pillar 10's constraint lands

`docs/research/10-creator-indexing.md §4` is a hard *"no scraper, ever,"* and §5 finds that manual
sound-page browsing is both compliant and the highest-signal path available. There is no compliant
API for "who used this TikTok sound."

In a frontier model that is not a dead end. It is a lead with `mode = 'manual'`: same queue, same
scoring, same provenance — dispatched to a human instead of a fetcher. *"Open this sound page, mark
who is relevant."*

The human's browsing becomes a step inside the agent loop rather than a process running beside it,
and their verdicts feed the same scorer that gates everything else. This is
`SCOPE-RESET.md` open decision 4's *"human-in-the-loop scout that surfaces candidates for bulk
human acceptance"* — arrived at structurally rather than as a compromise.

---

## 5. The store — three shapes, because there are three kinds of truth

### 5a. Evidence — has quotes, no truth value

```sql
artist_document(
  id, tenant_id, artist_id, lead_id,
  platform, url, title,
  content_hash, raw_key, mime, lang,
  published_at, fetched_at, http_status, gone_at)
UNIQUE (tenant_id, artist_id, content_hash)

artist_chunk(
  id, tenant_id, artist_id, document_id, ordinal,
  text, embedding VECTOR(1024), token_count, created_at)
```

### 5b. Claims — has truth value and provenance, no prose

```sql
artist_fact(
  id, tenant_id, artist_id,
  dimension,             -- origin_story|influences|collaborator|press_angle|
                         -- audience_geo|live_history|rights_note|brand_voice|…
  value_text, value_json,
  provenance,            -- measured | inferred | asserted
  status,                -- live | superseded | stale | retracted
  confidence, embedding VECTOR(1024),
  model, model_version, supersedes_id,
  observed_at, created_at)

fact_basis(fact_id, basis_kind, basis_id,
           PRIMARY KEY (fact_id, basis_kind, basis_id))
  -- basis_kind: chunk | fact | profile | metric
```

### 5c. Measurements — append-only time series

```sql
artist_metric(
  id, tenant_id, artist_id, platform,
  entity_kind, entity_id,           -- track | profile | video | playlist
  metric, value NUMERIC,
  provenance, source, observed_at)
```

**Metrics are a third shape and not facts, for a specific reason: Tuesday's stream count does not
supersede Monday's. Both are true.** Facts supersede; measurements accumulate. This is
`PLATFORM-SPEC §2c`'s `counterparty_observation`, one level up the hierarchy.

### 5d. Human judgment — schema now, UI later

```sql
verdict(id, tenant_id, artist_id,
        subject_kind,    -- lead | fact | document | entity | contradiction
        subject_id, relevant BOOL, note, judged_by, judged_at)
UNIQUE (subject_kind, subject_id, judged_by)
```

The first slice renders none of this. The table exists from the first migration so a human verdict
has somewhere to land without a later migration, and so the scorer can start consuming verdicts the
day a surface exists.

### 5e. Why not one table

Collapsing evidence and claims is the tempting simplification and it destroys both properties.
Chunks would acquire a truth value they do not have — a paragraph is not a claim, it is a thing
someone wrote. Facts would lose the ability to be quoted. And §9's change handling would become a
delete-cascade instead of a status transition.

The split is `PLATFORM-SPEC §2b`'s discipline — three provenance classes get three storage shapes,
so mixing them is a schema error rather than a discipline failure — applied one level up.

### 5f. Vector indexes

Following `PLATFORM-SPEC §6`'s amendment: CockroachDB accelerates a vector-index filter only on
prefix columns under equality or `IN`.

```sql
CREATE VECTOR INDEX artist_chunk_search
  ON artist_chunk (tenant_id, artist_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX artist_fact_search
  ON artist_fact (tenant_id, artist_id, embedding vector_cosine_ops);
```

Artist scoping is always equality and is the dominant filter. `platform` is deliberately **not** a
prefix column: adding it would break every query that does not specify a platform, and post-filtering
within a single artist's chunks is cheap.

---

## 6. The loop

1. **Seed.** `owned` and `unowned` profiles emit `profile`, `engagement` and `metric` leads. **Every**
   platform — including `absent` — emits `mention_search`.
2. **Claim.** The Forager claims a batch by lease.
3. **Fetch.** The adapter returns `(documents, metrics, leads)`.
4. **Write, in one transaction.** Documents, chunks, metrics, new leads scored and deduped, and the
   claimed lead marked `done` or rescheduled by its cadence. Embedding happens *before* the
   transaction opens, so no network call is held inside it.
5. **`artist_chunk` insert** → changefeed wakes the **Distiller** → writes `artist_fact` and
   `fact_basis`.
6. **`artist_fact` insert** → coverage evaluation → thin dimensions emit `gap_query` leads.
7. **Verdicts** arrive whenever a human looks, adjust scores, and can kill a subtree.

No agent names another agent. `PLATFORM-SPEC §0` holds unchanged.

---

## 7. Scoring, budget, and drift

### 7a. Scoring

Computed at insert. Below a floor, a lead is never enqueued at all.

| Factor | Effect |
|---|---|
| depth decay | `0.6 ^ depth` — four hops out starts at 0.13 |
| identifier match | does this touch a canonical ID (ISRC, Spotify ID, MBID, verified handle)? |
| kind prior | owned engagement > catalogue > press > fan artifact > gap query |
| source trust | per-platform multiplier |
| novelty | penalise domains already fetched *n* times |

v1 is deterministic and hand-tuned. Verdicts train it later: `relevant = false` demotes a lead's
entire subtree and downweights the features that produced it.

### 7b. Budget

```sql
artist_budget(artist_id, max_leads_per_run, max_documents_per_run,
              max_tokens_per_run, max_depth, updated_at)
```

The governor refuses to claim past the cap **and writes what it dropped to `agent_run`**. Silent
truncation reads as "we covered everything" when it did not — the house rule from
`infra/MEMORY-WORKLOAD.md` and `content/bin/screen_clips.py`, applied to our own crawl.

### 7c. Drift is the real failure mode, not volume

An artist name collides with unrelated text. "Hallow Youth" will match things that are not the band.

Two gates:

1. **Identifier anchoring.** A document that reaches no canonical identifier and did not arrive
   through an owned edge scores low, and past depth 2 is rejected outright.
2. **A cheap relevance check before the expensive step.** `fan_artifact` and `document` leads pass a
   small model gate *before* full fetch-and-embed, not after.

---

## 8. Adapters

```
fetch(lead, credentials) -> FetchResult(documents[], metrics[], leads[])
```

**No database access.** Input to output. That is what makes an adapter testable against recorded
fixtures with zero network, and it keeps every source small enough to read in one sitting.

The full adapter set is `spotify`, `musicbrainz`, `web_search`, `web_page`, `instagram_owned`,
`youtube`, `manual`. §13 fixes which of them land in the first slice: the first five. `youtube` and
`manual` follow, because both are about fan sweeps on platforms we do not own, and neither is needed
to prove the substrate.

**Instagram is first-wave.** An earlier draft of this design deferred it on the assumption that
Meta App Review would block for weeks. That is wrong for our case:
`docs/research/01-platform-apis.md §1` establishes that **App Review and Business Verification are
required only for Advanced Access** — publishing to or reading accounts you do *not* own or manage.
For accounts the business owns, Meta grants **Standard Access automatically, with no App Review.**
The remaining friction is account type (Business or Creator; personal accounts have no API access)
and, on the Facebook Login flow, a linked Facebook Page.

**TikTok stays read-only and partly manual.** Pillar 01 §2's audit wall governs *posting*, which the
Forager does not do; pillar 10 §5a governs *reading sound pages*, for which no compliant API exists.
TikTok therefore contributes `mention_search` where public search permits and `manual` leads
otherwise.

---

## 9. Change, staleness, and contradiction

The question this section answers: an artist has no TikTok, a human asserts `absent`, facts get
inferred from that — and then the artist makes a TikTok. What happens to everything downstream?

**Nothing is ever purged.** A purge destroys precisely the audit trail a label needs when a campaign
goes sideways. Staleness is a state, and it cascades.

### 9a. Evidence never goes stale; claims do

A 2026-06 article saying *"they're not on TikTok"* remains a perfectly accurate quote of that article,
forever. What changed is not the evidence — it is the claim standing on it.

This is the practical payoff of §5's split, and it is what makes change handling a status transition
instead of a delete cascade.

### 9b. Facts carry a status

| status | means |
|---|---|
| `live` | current head of its chain |
| `superseded` | a newer version of this same claim exists, via `supersedes_id` |
| `stale` | its basis changed, or its TTL expired — may still be true, no longer trusted, queued for re-derivation |
| `retracted` | a human said it was wrong |

Retrieval reads `live` by default. Everything else stays queryable. *"We believed X on 2026-06-12
because of source S, and that ended on 2026-08-07"* is a first-class query.

### 9c. Two causes of staleness, one handler

**The basis changed.** A changefeed on `artist_profile` and `artist_fact` wakes a **Reconciler**,
which walks `fact_basis` transitively and marks dependents `stale`.

**Time passed.** `SCOPE-RESET §2a` rule 1: facts have wildly different half-lives.

```sql
dimension_policy(dimension, ttl_seconds, recheck_cadence, provenance_floor)
```

BPM: unbounded. Follower count: days. Audience hypothesis: weeks, *and should be revised*. Past its
TTL a fact goes `stale` and a recheck lead is enqueued — the same code path, a different trigger.

### 9d. The TikTok case, end to end

| | |
|---|---|
| **Day 0** | Human asserts `artist_profile(tiktok, absent)`. The Forager emits `mention_search` leads on TikTok anyway. The Distiller writes an inferred fact — *"measurable TikTok fan activity, no artist presence: unserved audience"* — with `fact_basis` = {that profile row, the fan-artifact chunks}. |
| **Day 40** | The artist makes a TikTok. Someone sets the profile to `owned` with a credential ref. |
| → | New profile version appended; the old one superseded, not deleted. |
| → | The Reconciler follows `fact_basis` and marks the unserved-audience fact `stale`. |
| → | New leads seed: `engagement`, `metric`, `profile` refresh. |
| → | The existing `mention_search` leads **keep running** — still valid, and now more useful, because fan posts can be compared against owned posts. |
| → | Re-derivation writes a new `live` fact superseding the stale one. |
| **Result** | Documents and chunks untouched. Not one row deleted. |

### 9e. Precedence, and the case where the system refuses to decide

`asserted` and `measured` both outrank `inferred`. **An inferred fact may never supersede either** —
the most it can do is mark one `stale` and request review. That is what stops a model confidently
overwriting something a human stated.

Enforcement is in the single write path with a test, not a database constraint: the check needs a
join against the superseded row, which a `CHECK` cannot express.

`asserted` versus `measured` is deliberately **not** auto-resolved:

```sql
contradiction(id, tenant_id, artist_id, subject_kind, subject_id,
              claim_a_id, claim_b_id, detected_at,
              state, resolved_by, resolved_at)
```

The case that matters: a TikTok `mention_search` surfaces an account that looks like it *is* the
artist. That is measured evidence contradicting an asserted `absent`. The system must not silently
flip the configuration — and must not stay quiet either, because **the artist made a TikTok and did
not tell the label** is among the most useful things this system could ever report.

So it raises a contradiction, and the resolution surface is the boolean: *is this them — relevant or
not relevant?* The loop closes on itself.

### 9f. The other change classes

| Situation | Handling |
|---|---|
| **New account** | Profile transition and cascade, as §9d |
| **Rebrand or name change** | `artist_identifier(artist_id, kind, value, valid_from, valid_until)`. The old name stays a searchable alias with an end date, because press written before the rebrand still uses it. The §7c drift gate must accept historic identifiers or we go blind to our own back catalogue. |
| **New release** | A `catalogue` lead fires, seeds per-track `metric` leads, and reopens the dimensions that depend on catalogue — audience hypothesis, press angle |
| **Account lost or banned** | `owned` → `unowned`, the same cascade in reverse |
| **Human corrects a fact** | `retracted` plus a new `asserted` fact |
| **Source 404s on refetch** | Document marked `gone_at`; **chunks retained** — we still hold the text. Citations survive, confidence decays because we can no longer re-verify. Not invalidated. |

---

## 10. How this exercises CockroachDB

### 10a. The four jobs from `PLATFORM-SPEC §0`

**Memory.** Two vector surfaces — `artist_chunk.embedding` (evidence) and `artist_fact.embedding`
(claims) — plus the `fact_basis` edges between them, **all written in one transaction**. So *"find
claims like X and show me the exact source sentences"* is a single query against a single store. In
the Postgres-plus-vector-service default, the citation edge and the vectors live in different systems;
that query is two round trips with a staleness window between them. This is §1's "searchable at
commit" doing work rather than being asserted.

**State.** The plan is a table, so the plan is queryable:

```sql
WITH RECURSIVE trail AS (
  SELECT id, parent_lead_id, target, reason, 0 AS hop
    FROM lead WHERE id = $1
  UNION ALL
  SELECT l.id, l.parent_lead_id, l.target, l.reason, t.hop + 1
    FROM lead l JOIN trail t ON l.id = t.parent_lead_id)
SELECT hop, target, reason FROM trail ORDER BY hop DESC;
```

`fact_basis` answers *where did this claim come from*. This answers **why were we ever looking
there** — provenance of attention, walked with a recursive CTE. An agent whose plan lives in a
context window cannot answer it at all.

**Coordination.** Serializable isolation becomes a reproducible test rather than a talking point.
Two Foragers discover the same handle from different parents in the same instant. Under Read
Committed that is a duplicate row or a lost score update. Under serializable plus
`UNIQUE (tenant_id, artist_id, target_hash)`, one lead survives with merged provenance and the loser
retries. `PLATFORM-SPEC §1`'s claim that *"two agents writing lessons about the same counterparty
silently lose one"* becomes a test that fails on Postgres defaults and passes here.

**Event bus.** §9c's Reconciler is a **changefeed-driven transitive closure over `fact_basis`** — a
row change propagating invalidation through a dependency graph. That is the database behaving like a
build system rather than a CRUD application, and it has a demo you can watch: flip one
`artist_profile` row from `absent` to `owned` and watch staleness fan out through dependent facts
while re-derivation leads appear in the frontier, with zero deletes.

### 10b. What the Forager gives back to the spec

**It closes `PLATFORM-SPEC §10` risk 2.** `platform/README.md` records that the RU cost of a filtered
vector scan cannot be measured because *"a probe with no rows measures nothing."* The Forager is the
row generator. It is the first thing in the repository that makes that measurement possible.

**It makes the Managed MCP Server (requirement tool 2) demonstrable.** Natural-language queries over
a two-table schema demo poorly. *"What do we know about this artist's audience in Berlin, and where
did we learn it?"* over a real dossier with citations is a different thing.

**It moves `PLATFORM-SPEC §8`'s closing beat a week earlier.** The planned finale is killing the fleet
mid-campaign on day 12. The Forager gives the identical beat on day 3 — kill it mid-crawl, restart,
the frontier resumes from rows — off the critical path.

### 10c. Where it strains, honestly

**The Forager is a high-volume writer into a vector-indexed table, which is exactly the pattern
`PLATFORM-SPEC §10` risk 4 warns about.** The documentation states large batch inserts degrade the
index and `IMPORT INTO` is unsupported on tables carrying one. A single artist crawl can produce a
few thousand chunks in a burst. This is the sharpest tension between this design and this database,
and it is **currently unmeasured**.

The design response:

```sql
chunk_staging(id, artist_id, document_id, ordinal, text, embedding, created_at)
  -- deliberately no vector index
```

Chunks land in staging at whatever rate the crawl produces. A paced mover drains staging into
`artist_chunk` in small batches — and the pacing is itself driven by the lead queue, so it is the
same lease and the same worker shape. Write bursts are decoupled from index health by construction.
If measurement later shows direct insert is fine, the mover is deleted and nothing else changes.

**Two smaller strains.**

The Forager is the fleet's heaviest changefeed producer, which pushes on the unverified RU draw in
`PLATFORM-SPEC §10` risk 2. Mitigated structurally: the claim query polls `next_action_at`, so **the
Forager runs correctly with changefeeds switched off entirely.** They are a latency optimisation, not
a dependency.

The "ingest only, judge it in SQL" first slice would write the vector indexes without ever reading
them. The slice must therefore include at least one retrieval query with `EXPLAIN` confirming prefix
spans, or the headline CockroachDB claim goes unexercised for a week. That is an hour of work, not a
milestone.

### 10d. One Cockroach-specific operator tool

```sql
SELECT * FROM lead AS OF SYSTEM TIME '-4h' WHERE artist_id = $1;
```

*"What did the frontier look like before the drift?"* — free, with no versioning of our own. It does
**not** replace `supersedes_id`: the garbage-collection TTL on a Basic cluster bounds how far back it
reaches. As an operator tool during a twelve-day build it is genuinely useful; as a memory model it
is not one.

### 10e. Effect on `PLATFORM-SPEC §8` requirement coverage

| Requirement | Before | With the Forager |
|---|---|---|
| Distributed vector indexing | R1/R2 over counterparties — gated on open decision 4 | Also R-artist over chunks *and* facts, with citations, **unblocked today** |
| Cloud Managed MCP Server | Queries over a two-table schema | Queries over a real dossier with sources |
| Memory integral to agents | Threads resume after restart, day 12 | Frontier resumes after restart, day 3 |
| `ccloud` CLI | Not yet earned | Unchanged — still not earned |

---

## 11. Failure modes

| Failure | Handling |
|---|---|
| Rate limit / 429 | Exponential backoff into `next_action_at`, `attempts` counter, `failed` after *n* |
| Dead URL | Recorded with `http_status` and `gone_at`, not retried forever |
| Embedding cost runaway | Per-document chunk cap; near-duplicate chunks skipped by hash |
| Vector index write degradation | §10c's staging table and paced mover |
| Changefeed RU draw | Fall back to polling `next_action_at` — already the claim mechanism |
| Topical drift | §7c's identifier anchoring and pre-fetch relevance gate |
| Adapter breaks on a site redesign | Adapter returns zero documents and a `failed` lead with `last_error`; the crawl continues |
| Model hallucinates a fact | Every fact requires at least one `fact_basis` row; a fact with no basis is rejected at write |

---

## 12. Testing

| Area | Test |
|---|---|
| Adapters | Recorded fixtures, no network |
| Frontier contention | Two workers on one batch → disjoint claims |
| Lease expiry | Kill mid-lease → the lead is reclaimed after expiry |
| Dedup | The same target discovered from two parents → one row |
| Budget | Refusal at cap, and the drop recorded in `agent_run` |
| Provenance | `inferred` cannot supersede `measured` or `asserted` |
| Basis integrity | A fact written with no `fact_basis` row is rejected |
| Invalidation | Profile `absent` → `owned` marks dependents `stale` and enqueues re-derivation |
| Retrieval | Seeded chunks return, and `EXPLAIN` shows prefix spans |
| Cadence | A recurring lead reschedules rather than terminating |

---

## 13. First slice

Scope chosen against the Aug 18 deadline, and against the decision that v1 is **ingest only, judged
in SQL** — no console, no retrieval API, no rendered verdict buttons.

| | Deliverable |
|---|---|
| 1 | Migration: `artist_profile`, `artist_identifier`, `lead`, `artist_document`, `artist_chunk`, `chunk_staging`, `artist_fact`, `fact_basis`, `artist_metric`, `verdict`, `artist_budget`, `dimension_policy`, `contradiction`. Vector indexes. Partial index on `lead`. `artist_identifier` ships in slice one even though rebrands are rare, because §7c's drift gate anchors on it from the first crawl. |
| 2 | Frontier: seed, score, claim by lease, budget governor, dedup. Contention and expiry tests. |
| 3 | Adapters: `spotify`, `musicbrainz`, `web_search`, `web_page`, `instagram_owned`. Fixture tests. |
| 4 | Chunk and embed via Bedrock; staging table and paced mover. |
| 5 | Distiller: one pass, chunks → facts with `fact_basis`. |
| 6 | One retrieval query with `EXPLAIN` proving prefix spans, plus the recursive-CTE attention trail. |

Deferred to the second slice: the Reconciler cascade, `gap_query` leads, TikTok and YouTube fan
sweeps, `manual` lead dispatch, the console surface and the verdict buttons.

---

## 14. Open questions

1. **Chunking strategy is unspecified.** Fixed-token windows with overlap is the default; whether
   document structure (headings, comment boundaries, post boundaries) should drive it is untested.
2. **The dimension taxonomy in §5b is a first guess.** It should be revised once real documents have
   passed through the Distiller, not designed further in advance.
3. **Embedding model and dimensionality.** `VECTOR(1024)` is inherited from `PLATFORM-SPEC §2b` for
   consistency; the specific Bedrock model is unchosen.
4. **`chunk_staging` may be unnecessary.** It exists to mitigate an unmeasured risk. Measure first;
   delete it if direct insert holds up.
5. **Whether the Forager eventually subsumes Scout and Researcher.** All three are *expand a frontier,
   fetch, distil, write facts with provenance*. If this works, the fleet plausibly shrinks from eight
   agents to six. Not claimed — flagged, because the seam in §4b's `entity` kind is deliberate.
6. **Still open from `SCOPE-RESET.md`:** repository topology (2) and tenancy (6). Licence remains
   unchosen and is required for submission.

---

## 15. Relationship to the binding documents

This document **extends** and does not contradict:

- `SCOPE-RESET.md` — the artist remains the spine; the three provenance classes are carried through
  unchanged; rule 2 (processes write facts back, they do not only read them) is the Forager's entire
  purpose.
- `PLATFORM-SPEC.md` — §3a's lease claim, §3c's constraint-not-convention pattern, §4's changefeed
  topology and §6's vector-index amendment are all reused rather than re-derived.

It proposes a **path** for `SCOPE-RESET.md` open decision 4 rather than resolving it: `mode = 'manual'`
leads make human-in-the-loop acquisition a mechanism the system already has, so the decision can be
made later without blocking anything now.

Where this document and the two binding ones disagree, **the binding ones win and this file is the
bug.**
