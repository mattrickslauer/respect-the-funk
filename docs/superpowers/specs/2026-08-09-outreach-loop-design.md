# The outreach loop — the memory that compounds

*Design spec. 2026-08-09. Follows `2026-08-08-party-first-identity-design.md`,
`2026-08-07-agent-contract-design.md` and `2026-08-07-forager-agent-design.md`.
Resolves `SCOPE-RESET.md` open decision 4. Nine days before the hackathon deadline
(2026-08-18 17:00 EDT).*

## 1. The decision

**Build one cycle, not four tools.** Discovery, deduplication, outreach and measurement
are four bottlenecks the label named, and in this schema they are four stations on a
single loop. The connective tissue is one table that does not exist yet — `lesson` —
and one query that does not exist yet: a shortlist rerank that reads it.

Everything else in this spec is plumbing to make that arrow real:

```
scout_source ──► suggestion(party) ──accept──► party
                                                 │
                                          embed_party (exists)
                                                 │
                    ┌────────────────────────────┤
                    ▼                            ▼
             dedup_party (R3)              shortlist (R1, exists)
                    │                            │
            suggestion(merge)                    ▼
                                              thread ──► draft_pitch (R2)
                                                 │            │
                                                 │        outbox ──► send_pitch
                                                 │                       │
                                          classify_reply ◄──── message(inbound)
                                                 │
                                              lesson ──────────┐
                                                 ▲             │
                                          learn_outcome        │
                                                 ▲             │
                                          party_metric         │
                                                               ▼
                                                     re-ranks shortlist
```

The bottom arrow is the claim. Without it the diagram is a workflow; with it the system
gets better at its job without anybody writing more code, which is the only thing that
makes campaign *n+1* cheaper than campaign *n* — `SCOPE-RESET §1`'s entire justification
for the party being the root.

## 2. What is already true

Verified against `respect-the-funk-31317` / `defaultdb` on 2026-08-09, not read from
documentation:

| | |
|---|---|
| `party` | 21 rows — 3 roster, 18 counterparty |
| `party.profile_embedding` | 18 of 18 counterparties populated |
| `party_shortlist` vector index | exists, cosine, four equality prefix columns |
| `lead` | 31 rows — 23 done, 6 pending, 2 failed |
| `agent_run` | 68 rows — 53 ok, 12 failed, 3 refused |
| `suggestion` | 8 rows — 5 pending, 1 accepted, 2 superseded |
| `party_document` / `party_chunk` | 20 / **0** |
| `party_fact` | 4 |
| Tests | 77 passed, 16 skipped |
| Lambda console | `200` on `/` and `/healthz` |

So R1 works today and has real data behind it. `fleet.py` supplies lease claiming,
exponential backoff, poisoned-row parking and `agent_run` recording, and every station
below is a function taking a claimed row — **no new coordination code is written by this
spec.**

### 2a. Three claims in `platform/README.md` are false and this spec obliges their correction

Stated here so the correction is a task rather than an embarrassment:

1. *"856 chunks across 17 documents carry real vectors, written by the fleet."*
   `party_chunk` has **0 rows**. Migration `005` dropped `artist_chunk` and nothing
   re-ingested into its replacement. R2 semantic search over the document corpus
   currently searches an empty table.
2. *"Vector indexes on `party_chunk` and `party_fact` — **live**."* The indexes exist.
   The tables hold 0 and 4 rows. An index over nothing is not a live capability.
3. *"A partial unique index enforces the §3c cross-channel collision."* It was verified
   in a throwaway database that was then dropped. **It is in no shipped migration.**
   §4 below ships it.

## 3. Three new tables

### 3a. `lesson` — the compounding memory

```sql
CREATE TABLE lesson (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    scope_kind    STRING NOT NULL,        -- party | party_kind | channel | global
    scope_id      STRING NOT NULL,        -- a party UUID, a kind, a channel, or ''
    text          STRING NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '{}',
    confidence    FLOAT NOT NULL DEFAULT 0.5,
    embedding     VECTOR(1024),
    model         STRING NOT NULL DEFAULT '',
    model_version STRING NOT NULL DEFAULT '',
    supersedes_id UUID REFERENCES lesson(id) ON DELETE SET NULL,
    hit_count     INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT lesson_scope_known
        CHECK (scope_kind IN ('party','party_kind','channel','global')),
    CONSTRAINT lesson_embedding_has_a_model
        CHECK (embedding IS NULL OR model != '')
);

CREATE VECTOR INDEX lesson_semantic
    ON lesson (tenant_id, model, scope_kind, embedding vector_cosine_ops);
```

Three decisions, each carried forward from something already settled rather than
invented here:

**`model` is an equality prefix column and a `CHECK` refuses an embedding that cannot
name its model.** Migration `007` established this for `party_chunk` after the same
mistake: cosine distance between vectors from two different models is a well-formed
float and pure noise, and the only structural defence is to make the model a predicate
every retrieval must carry. The vector index prefix needs it anyway.

**`supersedes_id` rather than `UPDATE`.** `SCOPE-RESET §2a` rule 1 — revisions are
appended, the current value is the head of the chain, and an inferred value never
overwrites anything in place. A lesson that turns out to be wrong is superseded and
stays readable, because *why we used to believe this* is itself evidence.

**`hit_count` is not telemetry.** It is how an operator sees which lessons are earning
their place and which are noise the Analyst should retire. A lesson that has never been
retrieved in fifty drafts is a candidate for supersession.

**`scope_id` is a `STRING`, not a `UUID`, and carries no foreign key.** It holds a party
UUID when `scope_kind = 'party'` and a bare token (`'curator'`, `'radio'`) otherwise.
This is the same polymorphism `presence` and `party_credit` already carry, and it has
the same cost: `ON DELETE CASCADE` cannot fire, so **any deleter of a party must clear
its lessons.** `repo.delete_party` already does this for presence and gains one more
statement. The orphan sweep the README says is worth writing now covers three tables
rather than two.

### 3b. `thread`, `message`, `outbox` — state, and the one irreversible act

`PLATFORM-SPEC §2d` specified these against the pre-`005` schema, which was
artist-shaped. Restated party-first:

```sql
CREATE TABLE thread (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    party_id         UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    canonical_party_id UUID NOT NULL REFERENCES party(id) ON DELETE CASCADE,
    recording_id     UUID REFERENCES recording(id) ON DELETE SET NULL,
    channel          STRING NOT NULL,
    state            STRING NOT NULL DEFAULT 'discovered',
    next_action_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_agent      STRING,
    lease_expires_at TIMESTAMPTZ,
    attempts         INT NOT NULL DEFAULT 0,
    last_error       STRING NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE message (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    thread_id           UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    direction           STRING NOT NULL,        -- outbound | inbound
    channel             STRING NOT NULL,
    subject             STRING NOT NULL DEFAULT '',
    body                STRING NOT NULL,
    cites_lesson_ids    UUID[] NOT NULL DEFAULT '{}',
    provider_message_id STRING,
    sent_at             TIMESTAMPTZ,
    received_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outbox (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    thread_id       UUID NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    message_id      UUID NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    idempotency_key STRING NOT NULL UNIQUE,
    payload_json    JSONB NOT NULL,
    state           STRING NOT NULL DEFAULT 'pending',
    attempts        INT NOT NULL DEFAULT 0,
    claimed_by      STRING,
    claimed_at      TIMESTAMPTZ,
    not_before      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The state machine, channel-agnostic per `PLATFORM-SPEC §2d`:

```
discovered → shortlisted → approved → drafted → awaiting_human → queued → sent
          → awaiting_reply → replied → negotiating → agreed
          → closed_won | closed_lost | closed_no_reply
```

**`message.cites_lesson_ids` is the column that makes a draft auditable.** A pitch that
cannot say which lessons produced it is a black box, and the console's persistent
inspector exists precisely to answer *why*. This is also how `hit_count` is incremented
without a second write path.

**The §3c collision index, shipped this time:**

```sql
CREATE UNIQUE INDEX one_open_thread_per_party
    ON thread (tenant_id, canonical_party_id)
 WHERE state NOT IN ('closed_won','closed_lost','closed_no_reply');
```

On `canonical_party_id` rather than `party_id`, for the reason argued in §4a-i: an alias
and the row it aliases are two ids for one person, and an index on the raw id would let
both hold an open thread. `thread` therefore carries both columns — `party_id` for who
the thread is literally with, `canonical_party_id` for who that turns out to be.

Four lines, and they are the sharpest demonstration the architecture has: a curator who
is also a UGC creator cannot be worked by two fleets at once, and the second fleet's
insert fails without needing to know the first fleet exists. Contact discipline becomes
a constraint rather than a convention.

## 4. The six stations

Each is a function taking a claimed `lead` row, registered in `agents.REGISTRY`.

| Lead kind | Does | Reads | Writes |
|---|---|---|---|
| `scout_source` | fetch one public page, extract candidates | `source_manifest` | `suggestion(party)` |
| `dedup_party` | R3 — ANN over `profile_embedding`, propose merges | `party.profile_embedding` | `suggestion(merge)` |
| `draft_pitch` | R2 — retrieve lessons, write a draft | `lesson`, `party_fact`, `recording` | `message(outbound)`, `thread` |
| `send_pitch` | claim outbox, send, record the provider id | `outbox` | `message.sent_at`, `outbox.state` |
| `classify_reply` | inbound → next state, and what we learned | `message(inbound)`, `thread` | `thread.state`, `lesson` |
| `learn_outcome` | statement deltas + thread outcomes → lessons | `party_metric`, `thread` | `lesson` |

### 4a. `dedup_party` — R3, and the five Amandas

The live index already contains `Amanda`, `Amanda` again, `Amanda Goncalves`,
`Amanda Gonçalves`, `Amanda Rocha da Silva` and `Petra Liina Amanda Suokorpi`. Five to
six rows that are probably two or three people. This is not hypothetical dirt.

R3 was deferred in `PLATFORM-SPEC §6` as *"useful, not load-bearing."* Acquisition by
scraper (§5) makes it load-bearing: a discovery process that adds rows faster than a
human can reconcile them degrades the shortlist it was built to improve.

The agent runs ANN over `profile_embedding` within the tenant, gated by
`embedding_model` equality so the index prefix is satisfied, and proposes a merge above
a distance threshold. **It never merges.** It writes `suggestion(kind='merge')` and a
human accepts — the same queue, state machine and console surface the `presence`
suggestions already use. Automatic identity merges are irreversible in the way that
matters: two curators collapsed into one is a relationship you cannot un-collapse
because you no longer know there were two.

Name similarity is a tiebreaker on the evidence, never the trigger. `Amanda Goncalves`
and `Amanda Rocha da Silva` share a token and are probably different people; the
embedding of what they curate is the stronger signal.

### 4a-i. Merging is reversible, and rewrites nothing

**Decision, 2026-08-09, resolving §10 open item 2: accepting a merge keeps both rows and
flags one as an alias.** No references are rewritten.

The mechanism costs one column and one enum value:

```sql
ALTER TABLE party ADD COLUMN alias_of UUID REFERENCES party(id) ON DELETE SET NULL;

ALTER TABLE party DROP CONSTRAINT party_class_known;
ALTER TABLE party ADD CONSTRAINT party_class_known
    CHECK (party_class IN ('roster', 'counterparty', 'alias'));

ALTER TABLE party ADD CONSTRAINT party_alias_is_classed
    CHECK ((party_class = 'alias') = (alias_of IS NOT NULL));
```

`party_class` is already an equality prefix column on `party_shortlist`, and R1 filters
`party_class = 'counterparty'`. So **an alias falls out of the shortlist for free** —
no new predicate, no index change, and none of the acceleration lost. This is the same
reason `contact_state` exists, reused rather than reinvented.

Accepting a merge is two column writes. Reversing it is two column writes back. Nothing
is destroyed, so there is nothing to restore.

**The cost, stated plainly:** references keep pointing at the alias row. A read that
wants everything known about a person must resolve the chain — `presence`,
`party_credit`, `lesson` and `party_fact` for the canonical row are the union over its
aliases. That is one join in `repo`, and it is a better cost than an irreversible
rewrite. Alias chains are one level deep: merging an alias resolves to its canonical
first, enforced in `repo` and covered by a test.

**One hole the flag alone does not close.** The §3c unique index is on
`thread (tenant_id, party_id)`, so an alias and its canonical are two different
`party_id` values and both could hold an open thread — which is exactly the
double-contact the index exists to prevent, arriving by the back door.

The fix follows the pattern `009` already established for `contact_state`: `thread`
carries a denormalised **`canonical_party_id`**, written in the same serializable
transaction as the insert, and the partial unique index is on that column rather than
`party_id`:

```sql
CREATE UNIQUE INDEX one_open_thread_per_party
    ON thread (tenant_id, canonical_party_id)
 WHERE state NOT IN ('closed_won','closed_lost','closed_no_reply');
```

Accepting a merge therefore does touch one thing besides the two columns: it repoints
`thread.canonical_party_id` for the aliased party's open threads. That write can
*collide* — if both rows already have open threads, the merge is telling us they were
always the same person and we have been contacting them twice. The accept fails with
that as the message, which is the correct outcome: it is a fact the operator needs to
know before the rows are joined, not an error to swallow.

### 4b. `draft_pitch` — R2, and the only place a model writes prose

Retrieves lessons by ANN scoped to this party, this party kind and this channel, plus
the recording's facts, and writes one draft. The draft is a `message` row with
`cites_lesson_ids` populated and the thread set to `awaiting_human` — **it is never
queued for sending by the agent that wrote it.** A human moves `awaiting_human → queued`
in the console, and only that transition writes the `outbox` row.

Spend passes through `spend.Gate` exactly as the existing agents do. `RTF_PAID_ENABLED`
is unset, so this refuses to run until somebody deliberately enables it, and the refusal
is recorded in `agent_run` as `refused` — three such rows already exist, which is the
gate working.

### 4c. `send_pitch` — the transactional outbox

The only irreversible act in the system.

`PLATFORM-SPEC §3b` puts the `message` and the `outbox` row in one transaction. Human
approval moves where that transaction sits, and it is worth being exact about where,
because the atomicity is the whole point:

| Actor | Transaction |
|---|---|
| `draft_pitch` | `message(outbound, sent_at NULL)` + `thread → awaiting_human` |
| **The operator's approve action** | `outbox` row + `thread → queued`, **in one transaction** |
| `send_pitch` | claim, send, `message.sent_at` + `outbox.state = 'sent'` |

The invariant that matters is that an `outbox` row and the state change authorising it
commit together. A thread that says `queued` with no outbox row never sends; an outbox
row with the thread still `awaiting_human` sends something nobody approved. Both are
impossible if they are one transaction, and both are reachable if they are two.

`send_pitch` claims from `outbox` with the same `FOR UPDATE SKIP LOCKED` primitive the
fleet already uses, calls the provider, and records `provider_message_id` against the
`idempotency_key` it already holds. A crash between claim and send retries against that
key and the provider deduplicates.

Without that key, a crash between "email sent" and "recorded as sent" double-emails a
counterparty — a relationship you burn exactly once.

**Deliverability is explicitly not solved here**, per `PLATFORM-SPEC §10` risk 1. Warmup
and domain reputation take longer than nine days. Sends go only to owned and consented
addresses; the outbox proves the mechanism without needing volume.

### 4d. `classify_reply` and `learn_outcome` — where lessons come from

`classify_reply` reads an inbound message, sets the next thread state, and writes a
lesson scoped to that party when the reply says something durable — a rate, a format
preference, a hard no, a "come back after the album."

`learn_outcome` is the slower one. It reads `party_metric` deltas around a send and
thread outcomes in aggregate, and writes lessons scoped to a kind or a channel:
*Deezer mood editors reply to pitches naming the playlist; Deezer pop editors do not.*
These are the lessons that transfer to a party we have never contacted, which is the
only kind that makes the index better rather than just the file on one curator.

Every lesson carries `evidence_json` naming the threads and messages it was drawn from,
and `confidence`. A lesson with one supporting thread says so.

## 5. Acquisition — `SCOPE-RESET` open decision 4, resolved

**Decision: build the scraper.** Public pages, into the suggestion queue.

This overrides `docs/research/10-creator-indexing.md §4`, whose verdict is *"no scraper,
ever."* The override is deliberate and is the operator's call, made 2026-08-09.

**The risk the research actually identified is worth restating accurately, because the
mitigations follow from it.** `SCOPE-RESET §5` records the finding as *"scraping survives
the CFAA question and still ends companies."* The exposure is not criminal liability; it
is the platform relationship. A label whose distribution and pitching run through
Spotify and Deezer loses more from an account termination than a fast index gains.

So the scraper is built as the version that does not trip that:

| Constraint | Why |
|---|---|
| Public, unauthenticated pages only | An authenticated fetch is the fact pattern that turns a ToS matter into a serious one |
| No logged-in session or cookie reuse | Same, and it is what links collection to the label's own accounts |
| `robots.txt` honoured, checked per host and cached | The cheapest possible evidence of good faith |
| Rate from `source_manifest.rate_per_sec`, never faster | The manifest already carries per-platform rates; the scraper reads them rather than inventing its own |
| Identifying `User-Agent` with a contact address | Anonymous collection is what gets blocked; identified collection gets an email first |
| Never writes `party` — only `suggestion(kind='party')` | A human accepts. Keeps `SCOPE-RESET §2a` rule 1 honest: a scraped claim is *inferred*, not *measured* |
| One page per lead, follow-on pages as new leads | Backoff, spend gating and parking come free from `fleet.py`; a crawl loop inside one agent has none of them |

`source_manifest.enabled` remains the switch. A platform whose terms are read and found
prohibitive is disabled with a `disabled_reason`, which is a row, not a code change.

## 6. How the loop closes

`agents.shortlist()` today returns 20 rows ordered by cosine distance. It becomes two
steps, and the second one is the point of this spec.

**Step 1 — R1, unchanged.** Vector search over `party_shortlist`, every predicate an
equality on a prefix column, resolving to a `vector search` node with `prefix spans`.
Widened from 20 to 50 candidates to give the rerank something to do.

**Step 2 — lesson-informed rerank.** One R2 ANN over `lesson_semantic`, scoped to this
channel and the candidate kind, and each candidate's score adjusted by the lessons that
apply to it. A curator who ghosted twice sinks. A pitch angle that landed on three
Deezer mood editors lifts the fourth, including one never contacted.

**The rerank is explainable by construction.** Every adjustment carries the `lesson.id`
that caused it and the inspector renders it, so `platform/README.md`'s claim that every
object in the product has a *why* becomes true for the object where it matters most.
Incrementing `hit_count` happens here.

**Scoring stays arithmetic, not a model call.** The distance is a float, the adjustment
is a float, and the combination is a documented formula. A model deciding the ranking
would be unexplainable, unmeasurable, and would put a paid call in the middle of the
system's hottest query.

### 6a. The measurement, labelled with its N

Shortlist precision before lessons and after, over the real roster and the real 18
counterparties. **N will be small — 18 counterparties and single-digit threads by
Aug 18.** It is reported as `N=18`, not drawn as a curve. `PLATFORM-SPEC §10` risk 6 and
the house rule in `MEMORY-WORKLOAD.md` both apply to our own demo, and this is the
sentence that binds them.

## 7. Order of work

Sequenced so that stopping early still leaves something that works.

| | Deliverable | Value if it stops here |
|---|---|---|
| 1 | `lesson` table, R2 retrieval, shortlist rerank | Semantic memory works and the shortlist reads it |
| 2 | `dedup_party` | The index is clean; five Amandas resolved |
| 3 | `thread`, `message`, `outbox`, `§3c` index, `draft_pitch`, `send_pitch` | Outreach exists end to end, human-gated |
| 4 | `classify_reply`, `learn_outcome` | **The loop closes.** The whole claim is demonstrable |
| 5 | `scout_source` | The index grows on its own |
| 6 | Repopulate `party_chunk`; correct the three README claims; MCP as an operator query surface; video | Submission-ready |

Steps 1–2 are useful the day they land regardless of the deadline, which is what
"real-world usefulness first" means in practice. Step 4 is where the hackathon's headline
criterion is met. Steps 5–6 are upside.

## 8. Testing

Following `tests/test_fleet.py`'s existing shape — a fake connection, no cluster, no
network. New coverage:

- **Lease contention on `thread`** — two workers, one claimable row, exactly one wins.
- **The §3c collision** — a second open thread for the same party must fail, and must
  become insertable again once the first thread closes.
- **Outbox idempotency** — a crash between claim and send retries against the same key
  and does not produce a second message row.
- **`supersedes_id` chains** — a chain of three lessons resolves to one head, and a
  superseded lesson never appears in retrieval.
- **`model` equality** — retrieval with a mismatched model returns nothing rather than
  noise, and an embedding without a model is refused by the `CHECK`.
- **`dedup_party` proposes and never merges** — the agent writes a suggestion and leaves
  `party` untouched.
- **A merge round-trips** — accept, then reverse, and every row reads as it did before.
- **An alias is invisible to R1** — a shortlist run after a merge returns the canonical
  row and never the alias, without the query gaining a predicate.
- **Merging two parties that both have open threads is refused**, with the collision as
  the message rather than a swallowed constraint violation.
- **Alias chains stay one level deep** — merging into an alias resolves to its canonical
  first.
- **Scraper** — a fixture page parses to the expected candidates; a `robots.txt`
  disallow refuses the fetch; the rate limit is read from `source_manifest`. No test
  touches a network.

The vector-index behaviour that cannot be faked — that `EXPLAIN` still resolves to
`vector search` with `prefix spans` after the rerank lands — is checked against the real
cluster and recorded in `platform/README.md`'s verification section, as `007` and `009`
already were.

## 9. What this does not do

Named so they are omissions rather than oversights.

- **No changefeed.** The fleet polls `next_action_at`, which is `PLATFORM-SPEC §10`
  risk 2's documented fallback and adequate at this volume. It costs the architecture
  its elegance, not its function — but `platform/README.md` and the architecture poster
  must stop implying otherwise.
- **No Bedrock.** On-demand quota is 0 rpm for Titan Embeddings V2 on this account and
  the increase is not self-service. Embedding is a port with two adapters; OpenAI is
  live at 1024 dimensions. The AWS requirement is carried by Lambda, which is deployed.
- **One channel actually sent.** `PLATFORM-SPEC §7` wants two, on the argument that one
  cannot show the substrate is generic. Email is the one built here; a second is a
  `channel_playbook` row and a contact adapter, and it is not among the six steps in §7
  of this spec. The
  §3c collision — §7's stated reason for wanting two — is demonstrable with one channel
  plus a party holding two roles, which is what the live data already looks like. If
  time remains after step 6, a second channel is the first thing to add.
- **No negotiation agent.** `classify_reply` sets `negotiating` and stops there.
- **No rate limiting on `POST /demo`.** Unchanged, and still a topology decision.

## 10. Open

1. **Two sets of constants that cannot be chosen before there is data.** How much a
   ghosting lesson should sink a candidate (§6 step 2), and the cosine distance below
   which `dedup_party` proposes a merge (§4a). Neither is knowable before there are
   lessons and before the index is bigger than eighteen rows. Both ship as **named
   module-level constants with the reasoning in a comment**, never as literals inline,
   so tuning them is a visible change rather than a silent one. The merge threshold
   starts deliberately conservative: a missed duplicate costs a human one glance at a
   list, and a proposed merge between two real people costs the operator's trust in the
   whole queue.
2. ~~**Merge acceptance is destructive and has no undo.**~~ **RESOLVED 2026-08-09 by
   §4a-i.** Both rows are kept and one is flagged `party_class = 'alias'` with
   `alias_of` set. Nothing is rewritten, so a merge is two column writes and reversing
   it is two column writes back. The alias falls out of R1 for free because
   `party_class` is already an equality prefix column on `party_shortlist`. The cost is
   a resolve-the-chain join in `repo`, and one genuine new obligation:
   `thread.canonical_party_id`, without which the §3c index would let an alias and its
   canonical both hold an open thread.
3. **RU cost of a filtered vector scan** remains unmeasured, carried from
   `PLATFORM-SPEC §10` risk 2. The rerank adds a second ANN per shortlist, which
   roughly doubles whatever that number turns out to be. Measure once there are enough
   lessons for the measurement to mean anything.
