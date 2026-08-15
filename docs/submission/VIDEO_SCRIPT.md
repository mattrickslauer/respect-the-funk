# Respect the Funk — demo video script v3 (≤ 3:00)

> ## Superseded for the shoot by [`DEMO_SCRIPT.md`](./DEMO_SCRIPT.md), 2026-08-11.
>
> That script was written after the sponsor audit and argues one thing — why CockroachDB
> is irreplaceable here — where this one tours the product. **`DEMO_SCRIPT.md` is what
> gets filmed.** This file is kept for the claim ledger below, which is a live document
> and is maintained: it is the record of what may and may not be said on camera, and it
> is checked against the cluster rather than against either script.

> **Hard limit: under 3 minutes.** Judges are not required to watch past 3:00. This cut is
> **370 words — 2:50 of speech at 130 wpm, ~2:57 with the marked silences.** Full
> read-through in [`VOICEOVER.txt`](./VOICEOVER.txt), which is the authority; the timeline
> below quotes it verbatim.
>
> **Audience: CockroachDB × AWS judges**, watching one of ~3,000 submissions. The scored
> criteria are Agentic Memory Design, Technical Implementation, Real-World Impact,
> Production Readiness, Originality — and the first is the tie-breaker.
>
> **What changed from v2, and why.** v2 was well-built and made one structural mistake: it
> spent 1:44 before reaching the thing no other submission can say. A judge on their
> fortieth video decides in fifteen seconds. So v3 **opens on the kill-the-fleet demo**,
> earns the problem afterwards, and adds the two beats v2 was missing — the **transaction
> boundary** that is the literal answer to the brief, and **time travel over data we
> actually destroyed**. Every claim below was re-checked against the live cluster on
> 2026-08-10; see [`../2026-08-10-hackathon-audit.md`](../2026-08-10-hackathon-audit.md).

---

## Production approach

**Founder to camera, intercut with screen capture and flat diagrams.** Three registers:

- `[CAM]` — founder, talking head, mid-shot. Carries the problem and the pivot lines.
  Warm, room-lit, not a studio. This is a label owner explaining their own job.
- `[APP]` — real screen recording of the console at the deployed Lambda Function URL.
  Never a mockup: every row on screen is a row in the cluster.
- `[MG]` — flat 2D motion graphics on dark, for the architecture third.

**Why open on the terminal and not the founder.** The problem is felt, and the founder
carries it — but the *reason to keep watching* is that the first thing you see is a
system surviving something that should break it. Hook, then earn it. The founder arrives
at 0:13 and lands harder for having been held back.

**Never say "AI".** Say what it does. **Never say "Bedrock"** — the embeddings on this
cluster are OpenAI's, and Bedrock is not merely unused but unreachable on this account
(on-demand quota 0 and non-adjustable, batch inference entitlement-gated). The AWS surface
is Lambda and S3, and those are what the submission claims.

---

## Claim ledger — verified against the cluster 2026-08-10

The script may describe what the product *will* do; it may not show what does not exist.

| | Claim | State |
|---|---|---|
| ✅ | Kill a worker mid-run, its lease expires, another finishes the work | **built** — `test_fleet.py`, real concurrency |
| ✅ | Lease claiming, `FOR UPDATE SKIP LOCKED` | **built** |
| ✅ | Agent write + `agent_run` + lead completion in **one transaction** | **built** — `194b972` |
| ✅ | Serializable by default | **verified** — `SHOW default_transaction_isolation` |
| ✅ | Vector similarity + business filters in **one index traversal** | **verified** — live `EXPLAIN`: `vector search`, `prefix spans` on all four filter columns |
| ✅ | Paste a link → catalogue, ISRCs, genre, releases | **built** — Deezer live |
| ✅ | Provenance on every fact: measured / inferred / asserted | **built** — enforced in adapters and schema |
| ✅ | A name match becomes a question, never a contact | **built and live** — 8 real `suggestion` rows, accepted/rejected/superseded |
| ✅ | `AS OF SYSTEM TIME` — ask what we believed an hour ago | **verified live**, and now genuinely dramatic — see the 2:38 beat |
| ✅ | Scales to zero, `$0` idle | **verified** — BASIC, `node_count: 0`; $0.0053 spent across all 52 runs ever |
| ✅ | 239 tests, against the real cluster | **verified** — 5m58s, green |
| 🔴 | **The shortlist returns Deezer's Dance & EDM editor** | **NOT FILMABLE TODAY.** All 18 counterparties were deleted 2026-08-10 ~04:40 UTC. Snapshot at `/home/mattricks/rtf-snapshot-2026-08-10/`. **Restore the five real editors before shooting** — audit §7.1. |
| 🔴 | **A lesson reordered the shortlist** | **`lesson` = 0 rows.** The index exists and has never held one. **Seed this before shooting** — it is the tie-breaker criterion. |
| ⛔ | ~~"A changefeed wakes the next agent"~~ | **STILL CUT — 2026-08-13.** `changefeed.py` now composes the statement and consumes the feed, and `SHOW CHANGEFEED JOBS` still returns zero, because creating it draws RUs continuously and nobody has authorised that spend. Built is not running. Restore the line only if the *job* exists on shoot day. |

### Addendum — 2026-08-14, the contactability beat

Migrations 023 and 026–032 were applied to the cluster on 2026-08-14 and the first contact
harvest ran. `DEMO_SCRIPT.md` §3 was rewritten against the result. New claims:

| | Claim | State |
|---|---|---|
| ✅ | **2,351 contact routes**, `email 1622 · phone 501 · form 228` | **live** — was 1 row (a self-test) the day before |
| ✅ | 439 counterparties reachable, of 14,170 | **live** — say "hundreds", not "thousands" |
| ✅ | **Zero fabricated addresses** across 1,622 emails | **audited 2026-08-14** — no `example.com`, no vendor addresses |
| ✅ | **7 harvests refused by `robots.txt`**; agents stopped | **live** — in `lead.last_error`, filmable |
| ✅ | Many routes per counterparty, several channels at once | **built** — `UNIQUE (tenant_id, party_id, channel, value)` |
| ✅ | Route predicate is an allowlist in SQL, not a Python branch | **built** — `sender._prepare`; excludes `bounced`/`invalid`/`opted_out` by construction |
| ✅ | An `inferred` route is refused, not merely deprioritised | **built** — the ordering-is-not-a-predicate fix |
| 🟡 | `channel` admits `postal` and `social`, so a DM channel inherits the boundary | **true as a model claim only.** Zero `social` rows, no adapter. See the Telegram/WhatsApp rule in `DEMO_SCRIPT.md`. |
| 🔴 | **A `contact_route` row in `opted_out`** | **NOT FILMABLE.** 2,350 `unverified`, 1 `verified`, **0 `opted_out`**. The old §3 told the shoot to film this and it was never in the table. Mark one by hand or film the CHECK instead. |
| ⛔ | ~~"We message curators on WhatsApp / Telegram"~~ | **never say it.** No adapter, no route, no message. The model claim is the only permitted one. |
| ⛔ | ~~"GDPR/PECR/CAN-SPAM compliant"~~ | **never say it.** `contact_country` exists as of 2026-08-14; no jurisdiction decision has been made with it. |

**Five things gate shooting: restore the five editors, seed one real lesson, leave the
changefeed line out unless the job — not the module — exists, produce an `opted_out` row
or film the constraint instead, and re-run the channel counts on the day.**

---

## Timeline

Timings are computed from [`VOICEOVER.txt`](./VOICEOVER.txt) at **130 words per minute**.
Speech totals **2:50**; the remaining **9 seconds** are the silences marked below. The two
files must be kept in step — if you change a line here, change it there.

| Time | On screen | Voiceover |
|---|---|---|
| **0:00–0:10** · Cold open | `[APP]` Terminal, no titles, **two seconds of silence** while two workers claim disjoint leads and rows scroll. Then **kill one** — `^C`, its pane dies. A countdown ring on its held row expires; the surviving worker picks the row up and finishes. Nothing flashes red. Small mono caption: `FOR UPDATE SKIP LOCKED`. | "Kill half this fleet mid-run and the work still finishes. No orchestrator. No queue, no broker, no scheduler. The database is the runtime." |
| **0:10–0:26** · The job | `[CAM]` Founder, mid-shot. **Lower-third title card carries the project name here** — it is not spoken until the close, so it has to be read. Cut on "spreadsheets and DMs" to a two-second `[APP]` flash of an actual messy spreadsheet / DM thread. | "I run a record label. Signing an artist is the easy part — getting the record heard is the job, and most labels do it in spreadsheets and DMs. So every release starts from zero." |
| **0:26–0:37** · The thesis | `[MG]` Five release sleeves, each with an identical flat cost bar beneath. The bars then step *down* left to right while a line labelled **relationships · audience · lessons** rises beneath them. | "Release n plus one should be cheaper and land harder, because something accumulated. Not the music — the relationships, the audience, the lessons." |
| **0:37–0:48** · Paste a link | `[APP]` Artist inspector → paste a Deezer URL into *Add a surface* → save. Cut to `/tracks`: two recordings appear with real ISRCs. Cut to the artist record: releases with UPCs, genre chip. Speed-ramp the wait; do not fake it. | "You paste a link. It maps the catalogue, pulls the ISRCs, reads the genre, and goes looking for who to take it to." |
| **0:48–1:08** · Provenance | `[APP]` Hold on a fact row and its provenance chip. `[MG]` overlay: three stacked labels — **measured** · **inferred** · **asserted** — with a struck-through arrow from inferred → measured. Cut to `[APP]`: the real pending suggestion, *"deezer matched on name — needs a human to confirm"*, confidence `0.7`. **Accept** clicked; the row promotes. | "And it never guesses at you. Every fact carries how we learned it — measured, inferred, or asserted. An inferred fact may never overwrite a measured one. So a name match never becomes a contact — it becomes one question, answered in a click." |
| **1:08–1:34** · The shortlist, and the lesson **← the tie-breaker beat** | `[APP]` The shortlist for Hallow Youth. Let the real list land: **Laeti — Deezer Dance & EDM Editor** at the top, distance visible. Then **the reorder**: a row lifts, and the inspector shows `applied:` naming the lesson that moved it — hold long enough to read it. `[MG]` inset: two filter chips, `party_class = counterparty` and `contact_state = contactable`, sliding *inside* the index glyph rather than sitting after it. | "So who do we take this record to? For a dance act, the top answer is Deezer's Dance and EDM editor — nearest neighbour in a vector index, filtered to people we aren't already talking to. And the order isn't fixed. It's ranked by what we learned last time, and every row says which lesson moved it." |
| **1:34–1:37** · Pivot | `[CAM]` Founder, direct to camera. **A beat of silence before the line, and after it.** Hard cut to black. | "Now the part we're proud of." |
| **1:37–1:49** · One store | `[MG]` Four labelled boxes — Postgres · vector store · Redis · queue — collapse into **one** box marked **CockroachDB**, which splits into four *roles*: memory · state · lock · event bus. | "One database. Not Postgres plus a vector store plus Redis plus a queue. CockroachDB is the memory, the state, the lock and the event bus at once." |
| **1:49–2:08** · The transaction boundary **← the strongest line** | `[MG]` Three glyphs — **the agent's write** · **its run record** · **the lead marked done** — drift apart, then snap inside a single bracket labelled `BEGIN … COMMIT`. One of the three fails; the whole bracket rolls back and the lead returns to `pending`. | "Every agent's memory write, its run record, and its work being marked done all commit together. If the memory write fails, the work is not done. That's memory being integral and not supplementary — a transaction boundary, not a slogan." |
| **2:08–2:23** · The lease, paid off | `[MG]` Callback to the cold open, now labelled: two workers, disjoint rows, `FOR UPDATE SKIP LOCKED` in mono; the killed worker's lease expires and the row moves. | "It's also why that fleet survives being killed. Agents never call each other. They claim work with a lease on the memory row, and a dead worker's lease simply expires. No supervisor." |
| **2:23–2:42** · The index, and time travel | `[MG]` The live `EXPLAIN`, typed on: `• vector search` / `table: party@party_shortlist` / `prefix spans: […/'counterparty'/'contactable']`. Then `[APP]`: the same page an hour ago — **21 counterparties** — cut to now — **3**. Mono caption: `AS OF SYSTEM TIME '-60m'`. | "The filters live inside the vector index, not after it — similarity and business rules in one traversal. And an hour ago I deleted a bad harvest of counterparties. I can still ask what this database believed beforehand. No audit table." |
| **2:42–2:50** · Close | `[MG]` A queue icon, a Redis icon and a vector-DB cylinder struck through in turn; caption **the memory is the coordination**. `[CAM]` Founder, Function URL on the laptop behind them. | "The memory is the coordination. Respect the Funk — release n plus one should cost less than release one." |

**Runtime: 2:50 of speech, ~2:57 with the marked silences.** That is deliberately close to
the wire. If a read comes in slow, cut the struck-through arrow from the provenance beat
(0:48–1:08) — it reaches ~2:48. Do **not** cut 1:49–2:08 or the shortlist reorder at
1:25 — those two *are* the tie-breaker criterion on screen.

---

## Narration notes

- **Say the names out loud.** "CockroachDB". "AWS Lambda". "Vector index". "Serializable".
  "As of system time." Judges are listening for them; a diagram they read is worth less
  than a phrase they hear.
- **The single most important line:** *"If the memory write fails, the work is not done."*
  It is the literal answer to the brief's one load-bearing clause — *memory as integral to
  agent functionality, not supplementary* — and unlike a slogan it is checkable in the
  source. Do not rush it; leave air after.
- **Second:** *"The database is the runtime."* It opens the film and pays off at 2:26.
  Say it the same way both times.
- **Third:** *"The memory is the coordination."* Last technical thing said.
- **Land "measured, inferred, or asserted" cleanly.** Three words, three beats. It is the
  most distinctive product idea and the one a judge is most likely to repeat.
- **The time-travel beat is a confession, and that is why it works.** "I deleted a bad
  harvest" is a real operator doing real cleanup, and the recovery is the feature. Do not
  soften it into a hypothetical — the numbers 21 and 3 are true.
- Read **calm and slow**, ~130 words a minute, with air between lines. The cold open is
  the one place to let the picture run ahead of the voice: say nothing for the first two
  seconds while the workers are just working.
- Never say "AI", "leverage", "seamless", or "powerful".

## Screen-capture shot list

Capture against the deployed Function URL, signed in, with the real roster.

- [ ] **Terminal: two workers claiming disjoint leads, one killed, the work resumed** — the
      cold open, and the single most important capture in the film
- [ ] A genuinely messy spreadsheet / DM thread (2s)
- [ ] Artist inspector — paste a Deezer URL into *Add a surface*, save
- [ ] `/tracks` — two recordings with ISRCs appearing
- [ ] Artist record — releases with UPCs and the genre chip
- [ ] A fact row with its provenance chip visible
- [ ] The needs-you queue — the real pending suggestion, then **Accept**, then the promote
- [ ] **The shortlist with Laeti — Deezer Dance & EDM Editor at the top** *(blocked: restore)*
- [ ] **The shortlist reordering, inspector showing `applied:`** *(blocked: seed a lesson)*
- [ ] Terminal: live `EXPLAIN` showing `vector search` + `prefix spans`
- [ ] `[APP]` the same page at `AS OF SYSTEM TIME '-60m'` — 21 rows — cut to now — 3 rows
- [ ] `/runs` — durations, token counts, cost *(clear or explain the 14 failed runs first)*

## Submission checklist this video is part of

- [x] Public repository, Apache-2.0, licence auto-detected
- [x] Pre-existing code disclosed in `NOTICE`
- [x] Functional demo URL — Lambda Function URL
- [ ] **This video, under 3:00, on YouTube, public**
- [x] **One page naming the CockroachDB and AWS tools used and how** —
      [`TOOLS.md`](./TOOLS.md), written 2026-08-11 and revised 2026-08-13. No longer
      scattered across `PLATFORM-SPEC.md` and `reference/`, both of which now defer to it.
- [x] Architectural diagram (optional) — generated
