# UGC capture — the fan half of the world

*Design spec. 2026-08-09. Follows `2026-08-09-outreach-loop-design.md` and
`2026-08-08-party-first-identity-design.md`. Adds no new loop: this is the existing
outreach machinery pointed at a second population.*

## 1. The decision

**A fan clip is a thread.** The story tag is an inbound message, the ask is an outbound
message, the rights grant is an inbound reply, and the file is an asset hanging off the
message that licensed it. Nothing about user-generated content gets its own pipeline.

That is the whole design, and it is why `campaign.channel` already permitting `'ugc'`
turns out to be load-bearing rather than aspirational — `010_outreach.sql` wrote the
constraint before anything needed it, and the constraint was right.

The product being copied is Laylo's UGC Agent: it watches an artist's Instagram Story
tags, and asks the fan for the original file before the Story expires in 24 hours. The
reason that shape exists is not laziness about scraping. Meta's story-mention webhook
delivers a **privacy-aware CDN URL that expires in 24 hours**, and the platform terms
permit storing the URL rather than the bytes behind it. The DM is therefore not a
courtesy — it is the only mechanism that converts an expiring, unlicensed, re-compressed
reference into a durable, licensed, full-resolution asset. Any implementation that
downloads the story media instead of asking has built an asset library it cannot use.

```
fan tags artist in a Story
          │
   Meta webhook (messages / story_mention)
          │
  POST /webhooks/instagram ──HMAC──► lead(kind='ugc_mention')      [~40 lines, returns 200 fast]
          │
     fleet.claim (exists)
          │
   ugc_capture agent ──► party(+role 'fan') ──► thread ──► message(outbound) + outbox
          │                                                              │
          │                                                     sender (Instagram)
          │                                                              │
          │                                                        fan replies
          ▼                                                              │
   asset(S3, content-hashed) ◄──── outreach.record_reply (exists, never called) ◄┘
                    │
          granted_by_message_id ──► the licence is the message row
```

The only genuinely new capabilities are an inbound adapter, a sender, and bytes on disk.
`apps/spindle/README.md` has been honest for weeks that the first two are missing; this is
the change that supplies them, and building them for Instagram gives the curator/email
path a shape to copy rather than inventing one twice.

## 2. What is already true

Verified against `respect-the-funk-31317` / `defaultdb` on 2026-08-09, and against
Meta's Graph API on the same day. Not read from documentation.

| | |
|---|---|
| `party` | 21 rows — 3 roster, 18 counterparty (17 contactable, 1 in_thread) |
| `party_role` | live: `curator` × 18, `roster_artist` × 3 |
| `campaign` | 1 row — `playlist-campaign-1`, channel `curator`, state `paused` |
| `thread` | 1 row, `discovered` |
| `message` / `outbox` | **empty** — nothing has ever been drafted or queued |
| `presence` | 19 Deezer, 2 Spotify. **No Instagram rows at all** |
| `party_budget` | **0 rows** — the per-artist ceilings are presently unenforced |
| `agent_manifest` | 9 kinds; `drafter`, `sender`, `inbox` declared and disabled |
| `META_APP_ID` / `META_SECRET_APP_KEY` | present in root `.env`, **valid** — Meta issued an app token |
| Meta app `respect-the-funk` | exists, **no product configured, no webhook subscription** |

Two of these are load-bearing and easy to miss. **There is no Instagram presence
anywhere**, so the connect flow is writing the first such row rather than reading one.
And **`party_budget` is empty**, so any ceiling this spec adds must state what an absent
row means before it can be relied on.

## 3. Identity — one graph, roles as truth

Every human in the artist's world is a `party`: fan, curator, promoter, journalist. The
role is what they are to us, and `party_role` already carries it, dated and additive. A
fan who turns out to run a blog **gains a role**; no row migrates and no history is lost.
This is what makes the fan population reusable rather than a side system.

**`party_class` gains `'fan'`, and this is not a contradiction of the above.**
`009_counterparty_index.sql` states in its own comments that `party_class` is
"denormalised from `party_role` deliberately … written in the same serializable
transaction as the role it mirrors." Role is the truth; class is the equality column the
vector index prefix requires, because `party_shortlist` can only accelerate equality
predicates on the party table and a role join is not one. With fans in the graph and only
two class values, the curator shortlist starts returning fans — which is precisely the
failure `009` says the column exists to prevent, one pool over. So the third value is the
existing mechanism extended, not a new concept.

**Fans are embedded on a decaying budget.** Fan 1 is a person worth understanding. Fan
100,000 is a row in a count. `party_budget` gains `max_embedded_fans`, per-artist because
a 500-fan act and a 100k-fan act want different answers from the same code. A fan earns an
embedding by **arrival ordinal** below the artist's threshold *or* by **re-engagement at
any ordinal** — tagging a third time, or actually sending a clip. Engagement outranks
arrival, or the rule merely rewards being early and goes stale the moment an act breaks.
Unembedded fans remain fully in the graph with presence, threads and history; what is lost
is similarity search over them, not the mapping.

Because `party_budget` is empty today, **an absent row means the schema defaults apply**
and the console must show that the ceiling is a default rather than a choice. A silent
default that looks like a decision is the failure mode worth avoiding here.

**The collision index is rescoped, not removed.**
`one_open_thread_per_counterparty` currently means one open thread per person across all
campaigns. At fan scale that is wrong in a specific way: a UGC thread would lock that
person out of every curator campaign for as long as it is open. The predicate becomes
scoped by pool, so a person may hold one open UGC thread and one open curator thread but
never two of either. `§3c`'s intent — two fleets must not work the same human at once —
survives; the cross-purpose false positive does not. UGC threads also auto-close on window
expiry, so the lock is short-lived by construction.

## 4. Migration 011

| | |
|---|---|
| `party_class` CHECK | add `'fan'` — drop and re-add the constraint; no table rewrite |
| `party_shortlist` | **unchanged** — see below |
| `thread.pool` | new column, denormalised from `campaign.channel` |
| `one_open_thread_per_counterparty` | rescope the partial predicate onto `pool` |
| `party_budget.max_embedded_fans` | INT, per-artist |
| `asset` | new — files, hashes, provenance, rights pointer |
| `ig_connection` | new — per-artist OAuth tokens and their expiry |
| `campaign_policy` | new — the pre-approved send rule (§6) |
| `agent_manifest` | seed `ugc_capture`; enable nothing |

**`party_shortlist` needs no change, and an earlier draft of this spec said it did.**
The index prefix is `(tenant_id, embedding_model, party_class, contact_state, …)` and the
shortlist filters `party_class = 'counterparty'` by equality. Rows written with
`party_class = 'fan'` land under a different prefix span and are simply never scanned by
that query. A third value costs nothing here — which is a further argument for the class
column carrying the pool, since the alternative designs all did require touching the index.

**The collision index cannot be rescoped without a new column.** A partial unique index
cannot join, and the pool lives on `campaign.channel` while the index is on `thread`. So
`thread` gains `pool`, denormalised from the campaign at `open_thread` and written in the
same transaction — the same doctrine `009` applied to `party_class` and `010` applied to
`party.contact_state`. The index becomes
`UNIQUE (tenant_id, counterparty_id, pool) WHERE state NOT IN (…closed…)`.
Without this column the rescope in §3 is not implementable, and the spec previously
asserted it as though it were.

**`asset` uses concrete foreign keys, deliberately breaking with `presence` and
`party_credit`.** Both are polymorphic over subject kind, and `apps/spindle/README.md` already
records the cost: an orphaned presence row survived a deleted party and was found only by
counting, and every future deleter must now sweep by hand. Inheriting that for a table
holding files means orphaned **S3 objects** — which cost money forever and which nothing
points at. So `party_id` is `NOT NULL REFERENCES party(id) ON DELETE CASCADE`, with a
nullable `recording_id`, and the deleter's only extra duty is the bucket.

`asset` is not a UGC table. A fan clip, a press photo and a live recording are one shape,
and naming it for the first caller would guarantee a second table later.

**Content hash is the key of meaning.** `party_document` already uses
`UNIQUE (tenant_id, content_hash)` so re-ingesting an unchanged document is a no-op. The
same rule applies here, and the S3 key derives from the hash — so the same clip sent
twice is one object, and a fan re-sending after a failed upload costs nothing.

## 5. Ingest

`POST /webhooks/instagram` joins `/`, `/signin`, `POST /demo` and `/healthz` as a public
route. **Public means unauthenticated by a session, not unauthenticated.** Every delivery
is verified against `X-Hub-Signature-256` — HMAC-SHA256 of the raw body under the app
secret — and rejected otherwise. That check needs the *raw* bytes before FastAPI parses
JSON, which is the implementation trap most likely to be discovered late.

`GET /webhooks/instagram` serves the subscription handshake, echoing `hub.challenge` when
`hub.verify_token` matches `META_WEBHOOK_VERIFY_TOKEN`.

**The receiver does almost nothing**: verify, write a `lead`, return 200. Meta retries any
delivery it does not get a prompt 200 for, and a handler doing S3 writes inline will
eventually be slow enough to manufacture its own duplicates. Everything downstream is the
fleet's existing claim/lease/backoff machinery, which already survives restarts and poison
rows.

Duplicates are therefore **normal traffic, not an error path**, and are absorbed
structurally: `UNIQUE (tenant_id, idempotency_key)` on `message` and the content hash on
`asset` mean a replayed delivery loses a race and writes nothing.

## 6. The send gate moves from message to policy

The operator approves a **template and a rule** once, when the UGC campaign goes running:
audience, cap, per-fan cooldown, and the ask text. The agent then sends inside it
unattended. The gate does not disappear; it relocates one level up, which is what the
24-hour window forces — an operator asleep for eight hours loses every clip that expired
meanwhile, and a feature that only works during office hours is not the feature.

`agent_manifest` already carries `per_artist_cap` and `requires_human`, and `campaign`
already has `draft → running`, so the vocabulary exists.

**This requires one real change to `outreach.ALLOWED`.** The machine currently forces
`drafted → awaiting_human → queued`, which is correct for a curator pitch and wrong for a
policy-approved send. A `drafted → queued` edge is added, **permitted only when the
campaign carries a policy row**. Without that condition the send gate quietly stops
existing for everything, which is the highest-risk line in this spec and gets its own
test.

**v1 asks everyone who tags.** No triage — the cap and the cooldown are the only brakes,
which is why they belong in the policy rather than in an agent's judgement. A scorer can
arrive later as a second adapter behind `spend.Gate`, exactly as `embed.py` handles
Bedrock-versus-OpenAI, and would improve triage rather than enable it.

## 7. Rate limits

Four ceilings; one is ours.

**Meta's, outbound.** Instagram messaging uses business-use-case rate limiting and every
response carries `X-Business-Use-Case-Usage`, reporting consumption as a percentage and,
when throttled, an estimated time to regain access. **The ceiling is read from that header,
never hardcoded** — Meta changes the numbers, and a constant in our source is a constant
that is silently wrong later. The sender records the reported percentage per call and
stops climbing well short of 100.

**The 24-hour messaging window.** We may only DM a fan inside the window their interaction
opened. A queued DM whose window has closed is **cancelled, not sent** — `outbox` already
has a `cancelled` state, so this is a predicate rather than new machinery.

**Ours, on volume.** `outbox.not_before` is already a scheduled-send column, so a token
bucket is free: the sender stamps staggered `not_before` values rather than sleeping.
Pacing survives a cold start and is visible as rows instead of hidden in a loop.

**Ours, on politeness.** One ask per fan per clip; a fan who ignores us twice is not asked
a third time. With no triage in v1 this cooldown is the only thing between the feature and
looking like spam.

Throttles reuse the fleet's exponential backoff: `last_error` is recorded and `not_before`
pushed out. Nothing is dropped; it is deferred and stays visible in `/queue`.

## 8. Rights, and the asset that is worth having

**Story media is never downloaded.** Only the fan-sent file reaches S3. This is both the
platform terms and the entire point of the DM step.

**The licence is already append-only, so it is a query rather than a table.**
`outreach.draft` never rewrites an outbound body. The grant is therefore the tuple already
in the schema: the exact ask we sent, immutable; the fan's reply, verbatim; both
timestamps; the Instagram identity; and the hash of the file that arrived. What must
change is the *content* of the ask — it states the terms plainly and requests an explicit
affirmative, because "reply YES and send the clip" is a record and a bare attachment is an
argument. `asset.granted_by_message_id` points at the reply.

**Two risks stated rather than discovered.** Since v1 asks everyone who tags, **some
senders will be minors** — so an asset lands `unreviewed`, and nothing in the system treats
stored as cleared for use; an operator promotes it. And S3 lifecycle moves objects to
infrequent access after 90 days, because an asset library is the one thing here whose cost
only ever rises.

Downloads are size- and content-type-capped. These are files from strangers.

## 9. Storage

S3, new bucket, on the Lambda role that already exists — two statements, no new vendor,
no new credentials in the environment. Backblaze B2 was the alternative and was rejected
for one specific reason recorded in `app/.env.example`: **B2's transaction caps are per
account, not per bucket**, and RemixKit is live against that account during judging. A few
hundred clips is cents per month either way; B2's price advantage is a terabyte-scale
argument that does not apply yet.

The console Lambda is tuned for HTML requests. Downloading video is a different workload
and wants its own function or a raised timeout — decided before it bites, not after.

## 10. Console

No new views. UGC campaigns render through `/campaigns` and `/threads` as they stand;
assets appear in the persistent inspector, which is exactly what that pane argues for —
every object has a *why*, and an asset whose why is the DM that licensed it is the
cleanest instance the product has.

## 11. Failure modes

| | |
|---|---|
| Duplicate webhook delivery | Absorbed by the unique key. Normal traffic. |
| Bad signature | 403, never processed. A burst is an attack signal, not a bug. |
| Token expired mid-run | Row fails with `last_error`; refresh-on-use fixes it on retry. |
| 24h window closed | `cancelled`. Nothing went wrong; the opportunity expired. |
| Meta throttle | `failed` + backoff. Something went wrong and will retry. |
| Fan deletes the story | CDN URL 404s. Expected. Thread closes `closed_no_reply`. |
| S3 write fails after the DM sent | Rights record exists, asset missing. Retry is safe — the hash dedupes. |

The fourth and fifth rows look identical on a dashboard and mean opposite things.
Collapsing them into one state would make the queue unreadable exactly when it is busiest.

## 12. Testing

`sources.py` already establishes the pattern — a `Source` Protocol with `build()`
selecting an adapter — so the Instagram adapter gets a fake twin and every agent test runs
offline. Signature verification is tested with fixed body, fixed secret and known digest,
no network. Story-mention payloads are fixtures in Meta's documented shape.

The `drafted → queued` edge gets dedicated tests in both directions: permitted with a
policy, refused without one.

## 13. Build order

Business verification and App Review start on day one and run alongside everything, since
they are calendar time rather than work.

1. Migration 011
2. `POST/GET /webhooks/instagram`
3. **Deploy** — Meta verifies the callback URL synchronously, so the route must be live
   *before* the dashboard can be configured. This ordering is the opposite of the obvious
   one and was found by checking the app rather than assuming.
4. Configure the Meta app: add the Instagram product, subscribe the webhook
5. S3 adapter
6. `ugc_capture` agent
7. The sender — the first thing ever to claim `outbox`
8. Inbound adapter, calling the `outreach.record_reply` that exists and is tested and has
   never had a caller

Steps 1, 2, 5 and 6 are testable against fixtures immediately. Step 7 is the only one
useless until App Review clears.

**This is more than one implementation plan.** Steps 1–3 are a substrate change that
stands on its own and can ship without any Meta dependency; 4–6 are the capture path;
7–8 are the send and return path and are gated on App Review. Treating all eight as a
single plan would put a fixture-testable migration behind a weeks-long external review.
They should be planned as three, in that order.

## 14. What is needed that is not code

- `META_WEBHOOK_VERIFY_TOKEN` — we invent it; `openssl rand -hex 24`, the same idiom as
  the admin token. **Not yet in `.env`.**
- Business verification on the Meta Business account — document review, days.
- App Review for the Instagram messaging permission. Until it passes, Standard Access
  limits messaging to app testers: enough to prove the pipeline, useless for real fans.
- An Instagram **Business** account per roster artist, each completing an OAuth grant.
  Tokens are ~60 days and refreshable, so they are per-artist rows, not environment
  variables. **Refresh on use**, not on a schedule — the $0 topology has no scheduler, and
  an artist untouched for 60 days had no clips to collect.
- Prefer *Instagram API with Instagram Login* over *with Facebook Login*: it drops the
  linked-Facebook-Page requirement, which is real friction for an artist. Confirm the
  permission naming against current Meta docs — these were renamed recently and this spec
  does not restate the names it is unsure of.

## 15. Open

- **`campaign.channel` is not editable after creation.** The existing campaign is
  `curator`; a UGC test needs a new one. Whether channel becomes editable is a separate
  decision, and making it so has consequences for threads already open under it.
- **No Instagram `presence` rows exist.** The connect flow writes the first.
- **`party_budget` is empty**, so every ceiling in this spec currently resolves to a
  schema default. Whether an absent row should be a default or a refusal is unresolved,
  and the honest reading of the house rule is that a ceiling nobody chose should be
  visible as such.
- **Meta permission names** are stated by role, not by literal string, wherever this spec
  was not able to verify the current spelling.
