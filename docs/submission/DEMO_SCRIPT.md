---
title: "Demo script — three minutes, one argument"
subtitle: "Every beat exists to answer one question: why is CockroachDB irreplaceable here? Nothing else is discussed. Supersedes VIDEO_SCRIPT.md, which was written before the sponsor audit."
status: "BINDING for the shoot. Runtime budget 2:45 against a 3:00 hard cap. §5 lists what must be true before the camera rolls."
date: "2026-08-11"
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

## 1. Cold open — the problem is precision, not reach (0:00–0:20)

**Screen.** Terminal. A single query returning a count: `14170`. Then the shortlist for one
artist, top five, each with a genre beside it.

**VO.**
> A label with a new single has to find the few hundred people, out of tens of thousands,
> who might actually play it. Get that wrong and you are spam. We built the index that
> gets it right: fourteen thousand radio stations and curators, from public registers,
> each one embedded with what they actually play.

---

## 2. It is a vector search, and the filters are inside the index (0:20–0:55)

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
> quietly degrades to a full scan still returns rows that look correct.

**Why this beat.** It is the sponsor's own listed use case, and the prefix-span detail is
the part a technical judge recognises as real rather than decorative.

---

## 3. Tailored, not blasted — and the database enforces it (0:55–1:25)

**Screen.** Split: a station row showing `genre: classical` next to an artist tagged
`trap`, and the shortlist *not* containing it. Then the `contact_route` table showing a row
in `opted_out`.

**VO.**
> Genre comes from three sources into the text we embed, so a trap single does not get
> pitched to a classical broadcaster. That is the difference between outreach and blast
> radius. And when someone tells us to stop, `opted_out` is a terminal state no discovery
> stage can overwrite — not a rule we remember, a constraint the database keeps. The
> sender also refuses any address we merely guessed at. Sustainable outreach is a schema
> property here, not a policy document.

---

## 4. The send is irreversible, so the database is the system of record (1:25–2:05)

**This is the centrepiece. Give it the most time.**

**Screen.** Two panes. Left: the sender draining the outbox, one message going out. Right:
`kill -9` on the worker mid-flight. Then the recovery: the row sits in `claimed`, and the
next drain does **not** pick it up. Then the test suite: `22 passed`.

**VO.**
> Sending is the one thing this system cannot take back. So the claim commits before the
> network call, and a worker that dies mid-send leaves a row that is never automatically
> retried. A missed pitch costs nothing. A second pitch to a curator who already got one
> costs us that curator. One open thread per counterparty is a partial unique index. One
> outbox row per message is a constraint. Not conventions — things the database will not
> let us get wrong.

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

## 5. Why did you email them? (2:05–2:35)

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

## 6. Close (2:35–2:45)

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

## What must not be said

- **No changefeed.** `SHOW CHANGEFEED JOBS` returns nothing. The old script's line *"a
  changefeed wakes the next agent, no broker"* is false and a judge can check it in ten
  seconds. Cut it unless it gets built.
- **No multi-region, no data domiciling.** `SHOW REGIONS` returns one region on a BASIC
  cluster. It is the strongest argument we do not have; it belongs in the README's future
  work, not in the video.
- **No scale claims.** Fourteen thousand rows is not a distributed-systems problem, and
  saying so invites the one comparison we lose. Volunteer that pgvector could serve the
  retrieval — then show the thing it cannot do. Conceding the replaceable parts is what
  makes the irreplaceable one land.
- **No Bedrock.** The only embedding model that has ever produced a row here is
  `openai:text-embedding-3-small`. AWS is satisfied by Lambda and S3; say those.
