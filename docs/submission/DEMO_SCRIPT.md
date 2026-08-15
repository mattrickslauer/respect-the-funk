---
title: "Demo script — three minutes, one argument"
subtitle: "Every beat exists to answer one question: why is CockroachDB irreplaceable here? Nothing else is discussed. Superseded VIDEO_SCRIPT.md, which was written before the sponsor audit and was deleted on 2026-08-15."
status: "BINDING for the shoot. Runtime 2:59 against a 3:00 hard cap, with the §1 trim applied. The pre-shoot checklist lists what must be true before the camera rolls."
date: "2026-08-11"
amended: "2026-08-14 — §3 rewritten. Migrations 023 and 026–032 were applied and the first contact harvest ran, taking `contact_route` from 1 row to 2,351 across three channels. The beat that argued restraint from the schema now argues it from evidence, and gained 0:14 to do so. Telegram and WhatsApp are named as what the channel model is *for*; the rule that they may never be implied as integrations is in 'What must not be said'. Also corrects a claim that had been in this file since it was written: §3 told the shoot to film a `contact_route` row in `opted_out`, and that state has never held a row."
---

## The thesis, in one sentence

> **We map the music industry into a vector index, and then mail real human beings one at
> a time — and CockroachDB is the only reason we can do the second part safely and prove
> why we did it.**

Everything below serves that sentence. If a shot does not advance it, it is cut, no matter
how good it looks. No brand story. No founder story. No feature tour.

## The audience

Assume the judge never opens the app. Assume the repository is read by a model. That means
two things:

1. **Every claim in the video must be visible on screen as output**, not asserted by a
   voice. Show `EXPLAIN`. Show the refusal. Show the row count.
2. **The video and the repository must argue the same thing.** `docs/submission/TOOLS.md`
   and `docs/2026-08-11-sponsor-audit.md` already carry this thesis; they are what a model
   reads when the video ends.

---

## 1. Cold open — the problem is precision, not reach (0:00–0:15)

**Screen.** Terminal. A single query returning a count: `14170`. Then the shortlist for one
artist, top five, each with a genre beside it.

**VO.** *(52 words — 0:24 as written. Read the trimmed version below unless §3 is cut.)*
> A label with a new single has to find the few hundred people, out of tens of thousands,
> who might actually play it. Get that wrong and you are spam. We built the index that
> gets it right: fourteen thousand stations and curators, each one embedded with what
> they actually play.

**Marked trim, applied by default.** The original read *"fourteen thousand radio stations
and curators, **from public registers**, each one embedded…"*. Both cuts are free now that
§3 shows the harvest and names the sources on screen — saying it twice spends five seconds
this cut does not have. Restore the longer line only if §3 is dropped.

---

## 2. It is a vector search, and the filters are inside the index (0:15–0:50)

**Screen.** `EXPLAIN` output, full frame, with `vector search` and `prefix spans`
highlighted. Hold long enough to read.

```
• vector search
    table: party@party_shortlist
    target count: 20
    prefix spans: [/'1f9e6dd3…'/'openai:text-embedding-3-small'/'counterparty'/'contactable' - …]
```

**VO.**
> This is CockroachDB's distributed vector index. The filters — the tenant, the embedding
> model, whether they are a counterparty, whether they are contactable — are *inside* the
> index prefix, so the search happens in the filtered subspace. Not a scan with a filter
> bolted on afterwards. A test asserts this plan on every run, because a query that
> quietly degrades to a full scan still returns rows that look correct. And a trap single
> never reaches a classical broadcaster, because what they play is inside the text we
> embed.

**Why this beat.** It is the sponsor's own listed use case, and the prefix-span detail is
the part a technical judge recognises as real rather than decorative.

---

## 3. Three people, thousands of conversations — and the database is the discipline (0:50–1:43)

**Runtime — read this before recording.** This beat was 0:30 and is now **0:53**. Fifteen
seconds come from the 2:45/3:00 headroom, four from the duplicated line cut out of §4, and
**four are borrowed**: the cut runs **3:04** as written, which is over the cap.

Take the four seconds from §1, which is the one beat that is already over its own slot
(57 words is 0:26 in a 0:20 hole). The marked trim is in §1 — cutting *"from public
registers,"* and *"radio stations and curators"* → *"stations and curators"* returns 0:05
and costs nothing, because §3 now says where the data came from and shows it.

**Do not** take it out of §5. §5 is the only Postgres-impossible beat in the video and the
tie-breaker criterion rests on it.

This beat carries both arguments on purpose. It is not the old §3 replaced — it is the old
§3 (blast radius, `opted_out`, no guessed addresses, "a schema property, not a policy
document") with the contactability evidence that did not exist when it was written folded
in underneath. Nothing was dropped except one line that §2 now says better.

**Screen, in three holds.** Hold each long enough to read; this beat is evidence, not
montage.

1. `SELECT channel, count(*) FROM contact_route GROUP BY 1;` → `email 1622 · phone 501 ·
   form 228`. Then one counterparty expanded, carrying several routes at once.
2. The harvest log scrolling, stopping on a refusal:
   `robots.txt on https://khns.org/ disallows respect-the-funk/1.0`
3. `\d contact_route` — the `route_channel_known` CHECK visible
   (`email, phone, form, postal, social`) beside the partial unique index
   `one_open_thread_per_counterparty`.

**VO.** *(114 words — 0:53 at 130 wpm.)*
> A label is three people. They cannot hold thousands of conversations, so the database
> holds them. Each counterparty carries every route they publish — enquiries, a music
> director, a submissions form. Postal and social share that column, which is where
> Telegram and WhatsApp land when a label needs them: a new channel inherits every rule,
> because the rules are constraints, not code. We harvested twenty-three hundred routes
> from public pages. Seven robots files said no, and the agents stopped. Nothing was
> guessed — the sender refuses an address we inferred rather than read. And when someone
> tells us to stop, opted-out is a state no discovery stage can overwrite. One open
> conversation per counterparty, label-wide, is a partial unique index. Sustainable
> outreach is a schema property here, not a policy document.

**Why this beat, and why it is not a compliance slide.** Three claims, each visible as
output rather than asserted:

- **Many routes, one boundary.** `contact_route` is many-per-party by design — `018`
  argues that a single column would force a write-time choice about which address is
  "the" contact, and that choice belongs to the outreach stage. `channel` is closed
  (`email, phone, form, postal, social`) *because it selects the sender adapter*. That is
  the honest Telegram/WhatsApp story: the model is channel-agnostic and the consent
  boundary is one SQL predicate, so adding a DM channel is a column value and an adapter,
  not a second compliance regime.
- **Restraint you can see.** 7 robots refusals and 0 fabricated addresses across 1,622
  emails is stronger than any sentence about being responsible. Show the refusal.
- **Distributed, in the sense that matters here.** `sender._prepare` puts the route
  predicate in SQL "rather than in Python so that no caller can forget it" — an
  allowlist, `state IN ('unverified','verified')`, excluding `bounced`, `invalid` and
  `opted_out` by construction. Every worker in the fleet inherits it regardless of which
  agent or node is running. There is no rogue agent that forgot the rule, because the
  rule is not in the agent.

**Genre precision moved out of this beat** — it now sits in §2, where the vector index is
already on screen, in one line: *"a trap single does not reach a classical broadcaster,
because genre is inside the text we embed."*

---

## 4. The send is irreversible, so the database is the system of record (1:43–2:19)

**This is the centrepiece. Give it the most time.**

**Screen.** Two panes. Left: the sender draining the outbox, one message going out. Right:
`kill -9` on the worker mid-flight. Then the recovery: the row sits in `claimed`, and the
next drain does **not** pick it up. Then the test suite: `22 passed`.

**VO.**
> Sending is the one thing this system cannot take back. So the claim commits before the
> network call, and a worker that dies mid-send leaves a row that is never automatically
> retried. A missed pitch costs nothing. A second pitch to a curator who already got one
> costs us that curator. One outbox row per message is a constraint — not a convention,
> a thing the database will not let us get wrong.

> **Cut from this beat, deliberately.** The old read also said *"one open thread per
> counterparty is a partial unique index"* here. §3 now says it, and saying it twice
> spends four seconds the runtime does not have while making the second mention sound
> like a point the script could not find a home for.

**Then, immediately — the token.**

**Screen.** `platform/bin/lease_race_demo.py`, Act 2. Both workers named `ingest-cli`.

```
terminal 1 claims b844314c…   owner=ingest-cli  token=2bb498ed…
terminal 2 claims b844314c…   owner=ingest-cli  token=6f72beda…
terminal 1 tries to complete  -> REFUSED: lease lost
terminal 2 completes          -> OK
```

**VO.**
> Every agent action requires a capability token only the database mints, and only while
> its claim is current. Two workers with the same name, one lead — the name cannot tell
> them apart, the token can. Serializable isolation, `FOR UPDATE SKIP LOCKED`, and a fence
> that fails closed.

---

## 5. Why did you email them? (2:19–2:49)

**The irreplaceable beat. Nothing else in the video is Postgres-impossible; this is.**

**Screen.** The shortlist query run twice, side by side — once now, once with
`AS OF SYSTEM TIME` at the moment of the send. Different rankings. Same query.

**VO.**
> Our agents contact real people, so we have to be able to answer why. This is the same
> shortlist query, re-run against the memory as it existed when we sent — the same vector
> index, the same embeddings, the same lessons. Four extra words of SQL. No audit table,
> no event log, no versioned copy of anything. Postgres cannot do this at any price, and
> it is the reason an autonomous system that acts on the world can be held to account.

---

## 6. Close (2:49–2:59)

**Screen.** `SELECT sum(cost_micro_usd)/1e6 FROM agent_run;` → a number under four cents.
Then `node_count: 0`.

**VO.**
> One database is the index, the scheduler, the outbox and the audit log. It scales to
> zero between releases. Everything you just saw cost less than four cents.

---

## What must be true before the camera rolls

Ordered by how long each takes. **Nothing in this script may be filmed against a claim
that is not true on the day.**

- [ ] **The 7,163 queued embeddings finish**, or the shortlist in beats 1 and 3 still
      ranks on pre-genre text. ~8 hours, resumable, run it overnight.
- [ ] **One real send exists.** SES sandbox is sufficient — verify one address you own and
      send a genuine pitch to it. Production access is *not* required to film beat 4, and
      waiting on it is the main schedule risk if you want to mail real curators too.
- [ ] **One thread closed**, so `lesson` is non-empty and beat 5 has two genuinely
      different rankings to show. Today `lesson` holds zero rows.
- [ ] **Re-run `EXPLAIN`** on the day. The plan is asserted by a test, but film the real
      output, not this file's copy of it.
- [ ] **An `opted_out` route must exist before §3 can show one.** Measured 2026-08-14:
      `contact_route` holds 2,350 `unverified` and 1 `verified`, and **zero `opted_out`**.
      The previous version of this beat instructed the shoot to film a row in that state,
      which was true of the design and never of the table. Either mark one route
      `opted_out` by hand beforehand — it is a real state a real request would produce —
      or film the `route_state_known` CHECK and the sender's allowlist instead and say
      the state exists, which is what §3 above now does. **Do not point a camera at an
      empty filter and narrate it.**
- [ ] **Re-run the channel counts on the day.** §3 quotes `email 1622 · phone 501 ·
      form 228` and "two thousand three hundred routes", measured 2026-08-14. Another
      harvest pass will move them. The rounded spoken figure must not exceed the count on
      screen.

## What must not be said

- **No changefeed — and "we built it" is not the same sentence as "it runs".** As of
  2026-08-13 `changefeed.py` composes the statement, consumes the batches and ships a
  Lambda handler, and `SHOW CHANGEFEED JOBS` still returns **nothing**, because creating
  the feed spends RUs continuously and that is a human decision nobody has taken. The old
  script's line *"a changefeed wakes the next agent, no broker"* is therefore still false
  and still checkable in ten seconds. **The rule does not relax because code landed.** If
  the job exists on shoot day, say it and film `SHOW CHANGEFEED JOBS` returning a row. If
  it does not, the line stays out — and do not reach for a softer version like *"the
  database can wake an agent"*, which is a claim about the product wearing the grammar of
  a claim about SQL.
- **No multi-region, no data domiciling.** `SHOW REGIONS` returns one region on a BASIC
  cluster. Migration 024 and `infra/terraform/multiregion/` are written and validated and
  **nothing has been applied**, which changes nothing about what may be said on camera: a
  Terraform plan is not a region. It is the strongest argument we do not have; it belongs
  in the roadmap, not in the video.
- **No podcasts.** The adapter and migration 023 exist, the stage has no worker, and no
  podcast has been ingested. Radio is the second channel. Say radio.
- **Telegram and WhatsApp are a *model* claim, never an integration claim.** §3 names them
  deliberately and the wording is load-bearing: *"where Telegram and WhatsApp arrive the
  day a label needs them."* That is true — `channel` already admits `social`, the consent
  boundary is one SQL predicate, and a DM channel is a column value plus an adapter. What
  is **not** true, and may not be said or implied: that we message anyone on either
  platform, that any counterparty holds a `social` route, or that an adapter exists.
  Measured 2026-08-14: `contact_route` holds `email`, `phone` and `form` and **zero**
  `social` rows. Do not put a messenger logo on screen; a judge reads a logo as an
  integration. If the beat cannot be filmed without one, cut the sentence — the
  channel-agnostic point survives on `\d contact_route` alone, and an unbacked platform
  claim is exactly the failure §1 of the sponsor audit says loses.
- **"Compliant" is not a legal claim.** §3 says the database enforces our own rules and
  shows them. It does not say we are GDPR-, PECR- or CAN-SPAM-compliant, and neither
  should anyone on camera: `contact_country` landed on 2026-08-14 and nothing has yet made
  a jurisdiction decision with it. Show the constraint; do not name a statute.
- **No scale claims.** Fourteen thousand rows is not a distributed-systems problem, and
  saying so invites the one comparison we lose. Volunteer that pgvector could serve the
  retrieval — then show the thing it cannot do. Conceding the replaceable parts is what
  makes the irreplaceable one land.
- **No Bedrock.** The only embedding model that has ever produced a row here is
  `openai:text-embedding-3-small`. `bedrock.py` now implements both the on-demand and the
  batch path, and neither can run on this account: on-demand quota is 0 and
  non-adjustable, and batch inference is entitlement-gated behind a support case. Written
  is not running. AWS is satisfied by Lambda and S3; say those.
