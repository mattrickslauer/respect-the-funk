# Respect the Funk — demo video script (≤ 3:00)

> **Hard limit: under 3 minutes.** Judges are not required to watch past 3:00. This cut
> lands at **~2:40** at a calm read. Full read-through in [`VOICEOVER.txt`](./VOICEOVER.txt).
>
> **Audience: CockroachDB × AWS judges.** The scored criteria are Agentic Memory Design,
> Technical Implementation, Real-World Impact, Production Readiness, Originality — and
> the first is the tie-breaker. So the middle third is the product and the **last third is
> the database**, not the UI.

---

## Production approach

**Founder to camera, intercut with screen capture and flat diagrams.** Three registers,
deliberately:

- `[CAM]` — founder, talking head, mid-shot. Carries the problem and the pivot lines.
  Warm, room-lit, not a studio. This is a label owner explaining their own job.
- `[APP]` — real screen recording of the console at the deployed Function URL. Never a
  mockup: every row on screen is a row in the cluster.
- `[MG]` — flat 2D motion graphics on dark, for the architecture third. The one
  deliberate stylistic break, because the judged section has to be legible.

**Why founder-to-camera rather than all screencast:** the problem is a *felt* one and the
credibility is that we run a label. A voice describing spreadsheets over a screen
recording of a dashboard is a product video; a person saying "this is my job and it's
done in DMs" is a reason to keep watching.

**Never say "AI".** Say what it does. The word is free and means nothing to these judges.

---

## Claim ledger — build to match before shooting

The script is allowed to describe what the product *will* do; it is not allowed to show
something that does not exist. Every line is one of:

| | Claim | State |
|---|---|---|
| ✅ | Paste a link → catalogue, ISRCs, genre, releases mapped | **built** — Deezer live; Spotify adapter waiting on credentials |
| ✅ | Provenance on every fact: measured / inferred / asserted | **built** — enforced in adapters and schema |
| ✅ | A name match becomes a question, never a contact | **built** — `suggestion`, accept/reject in the queue and inspector |
| ✅ | Shortlist returns Deezer's Dance & EDM editor for a dance act | **built and verified** — real output, real curators |
| ✅ | Lease claiming, `FOR UPDATE SKIP LOCKED` | **built** — proven under real concurrency in `test_fleet.py` |
| ✅ | Kill the fleet mid-run, work resumes | **built** — lease expiry tested |
| ✅ | Vector similarity + business filters in one query | **built** — `EXPLAIN` shows `vector search` with `prefix spans` |
| ✅ | Serializable by default | **built** — CockroachDB default, relied on by the transactions |
| ✅ | Scales to zero, `$0` idle | **built** — measured |
| ⚠️ | **A changefeed wakes the next agent** | **NOT BUILT — build this before shooting.** `SHOW CHANGEFEED JOBS` returns 0. It is the single most load-bearing line in the technical third and the one thing a judge could check. |
| ⚠️ | **"Ask what we believed an hour ago" — `AS OF SYSTEM TIME`** | **available, not surfaced.** Verified working on the cluster (75-minute GC window). Needs one console control to be showable. |
| ⚠️ | Track-level analysis driving marketing strategy | **planned** — not in this cut. Do not imply it on screen. |

**Two things to build before this is shootable: the changefeed, and a time-travel toggle
in the console.** Everything else can be filmed today.

---

## Timeline

| Time | On screen | Voiceover |
|---|---|---|
| **0:00–0:14** · The job | `[CAM]` Founder, mid-shot. Cut on "spreadsheets and DMs" to a two-second `[APP]` flash of an actual messy spreadsheet / DM thread. | "Signing an artist is the easy part. Getting the record heard is the job — and most labels do it in spreadsheets and DMs." |
| **0:14–0:30** · Why it costs | `[MG]` Five release sleeves in a row, each with an identical cost bar beneath it — flat, not declining. The bars pulse once together. | "So every release starts from zero. The curator who said yes last time, the station that replied, what actually earned — none of it carries forward." |
| **0:30–0:44** · The thesis | `[MG]` The same five bars, now stepping *down* left to right, while a line labelled **relationships · audience · lessons** rises beneath them. | "It shouldn't be that way. Release n plus one ought to be cheaper and land harder, because something accumulated. Not the music — the relationships, the audience, the lessons." |
| **0:44–0:48** · Name | `[CAM]` Founder. Logo settles beside them. | "Respect the Funk is an operating system for that." |
| **0:48–1:05** · Paste a link | `[APP]` **The money shot of the first half.** Artist inspector → paste a Deezer URL into *Add a surface* → save. Cut to `/tracks`: two recordings appear with real ISRCs. Cut to the artist record: releases with UPCs, genre chip. Speed-ramp the wait; do not fake it. | "You paste a link. The system maps the catalogue, pulls the ISRCs, reads the genre off the releases, and goes looking for who to take it to." |
| **1:05–1:24** · Provenance | `[APP]` Hold on a fact row showing its provenance chip. `[MG]` overlay: three stacked labels — **measured** (a platform's own number), **inferred** (a model or a match), **asserted** (a human said so) — with a struck-through arrow from inferred → measured. Cut back to `[APP]`: a pending suggestion in the needs-you queue, **Accept** clicked, the row promotes. | "And it never guesses at you. Every fact carries how we learned it: measured, inferred, or asserted. A name match is inferred, so it never becomes a contact — it becomes one question you answer in a click." |
| **1:24–1:40** · The shortlist | `[APP]` The shortlist for Hallow Youth. Let the real list land: **Deezer Dance & EDM Editor** at the top, distance `0.5649` visible. Highlight the top row. `[MG]` small inset: a vector field with one point nearest the query, and two filter chips — `class = counterparty`, `contact_state = contactable`. | "So who do we take this record to? For a dance act, the top answer is Deezer's Dance and EDM editor. Not a keyword match — nearest neighbour in a vector index, filtered to people we aren't already talking to." |
| **1:40–1:44** · Pivot | `[CAM]` Founder, direct to camera. Beat of silence before the line. Then a hard cut to black for the graphics third. | "Now the part we're proud of." |
| **1:44–1:58** · One store | `[MG]` Four labelled boxes — Postgres · vector store · Redis · queue — collapse into **one** box marked **CockroachDB**, which then splits into four *roles*: memory · state · lock · event bus. | "This runs on one database. Not Postgres plus a vector store plus Redis plus a queue. One. CockroachDB is the memory, the state, the lock and the event bus at once." |
| **1:58–2:18** · The lease | `[MG]` Two worker glyphs reach for a row of leads; each takes a disjoint set — `FOR UPDATE SKIP LOCKED` printed in mono. Then **kill one worker** (it greys out mid-task), a countdown ring on its held row expires, and the other worker picks that row up. Nothing flashes red. | "Agents never call each other. An agent claims work with a lease — select for update, skip locked, on the work row itself. Kill the fleet mid-run: nothing is lost, nothing is stuck. The lease expires and another worker takes it. No supervisor. The database is the runtime." |
| **2:18–2:24** · Changefeed | `[MG]` A row in `lead` changes; a green line leaves the table and lands on an **AWS Lambda** glyph, which wakes the next agent. Mono caption: `CREATE CHANGEFEED FOR TABLE lead`. | "A changefeed on that table wakes the next agent. No broker." |
| **2:24–2:34** · Vector index | `[MG]` The `EXPLAIN` output itself, typed on: `• vector search` / `table: party@party_shortlist` / `prefix spans: [...]`. Let judges read it. | "Distributed vector indexing answers the shortlist — similarity and the business filters in one query. That is the honest reason this is one store and not four." |
| **2:34–2:46** · The rest | `[MG]` Three quick cards: **SERIALIZABLE** (two writes, neither lost); **`AS OF SYSTEM TIME`** (the same artist page, an hour ago, no audit table); **`$0.00`** idle. | "Serializable by default. And we can ask what we believed an hour ago — as of system time, no audit table. It scales to zero: idle, this costs nothing." |
| **2:46–2:52** · The anti-claim | `[MG]` A queue icon, a Redis icon and a vector-DB cylinder, each struck through in turn. Caption: **the memory is the coordination**. | "No queue. No Redis. No vector database bolted on the side. The memory is the coordination." |
| **2:52–3:00** · Close | `[CAM]` Founder. Function URL visible on the laptop behind them. Logo and tagline settle. | "Respect the Funk. Release n plus one should cost less than release one." |

**Runtime: ~2:40–2:55.** If it runs long, cut the provenance beat (1:05–1:24) to ~8s by
dropping the struck-through arrow — it reaches ~2:30. Do **not** cut the lease beat; it is
the tie-breaker criterion on screen.

---

## Narration notes

- **Say the names out loud.** "CockroachDB". "AWS Lambda". "Distributed vector indexing".
  "Changefeed". "Serializable". "As of system time". Judges are listening for them and a
  diagram they read is worth less than a phrase they hear.
- **The single most important line:** *"No supervisor. The database is the runtime."* It
  lands on the worker dying and its lease expiring. Do not rush it; leave air after.
- **Second most important:** *"The memory is the coordination."* It is the answer to the
  brief's one load-bearing clause — memory integral, not supplementary — and it should be
  the last technical thing said.
- **Land "measured, inferred, or asserted" cleanly.** Three words, three beats. It is the
  most distinctive product idea and the one a judge is most likely to repeat.
- Read **calm and slow**. The reference cut this is modelled on reads at ~130 words a
  minute with air between lines. Cramming to fit more in is how a 2:40 script becomes an
  unwatchable 2:40.
- Never say "AI", "leverage", "seamless", or "powerful".

## Screen-capture shot list

Capture against the deployed Function URL, signed in, with the real roster.

- [ ] A genuinely messy spreadsheet / DM thread (2s, the cold-open cut)
- [ ] Artist inspector — paste a Deezer URL into *Add a surface*, save
- [ ] `/tracks` — two recordings with ISRCs appearing
- [ ] Artist record — releases with UPCs and the genre chip
- [ ] A fact row with its provenance chip visible
- [ ] The needs-you queue — a pending suggestion, then **Accept**, then the row promoting
- [ ] The shortlist — Deezer Dance & EDM Editor at the top with its distance
- [ ] `/runs` — the run table with durations, token counts and cost
- [ ] Terminal: `EXPLAIN` showing `vector search` + `prefix spans`
- [ ] Terminal: two workers claiming disjoint leads, one killed, work resumed

## Submission checklist this video is part of

- [x] Public repository, Apache-2.0, licence auto-detected
- [x] Pre-existing code disclosed in `NOTICE`
- [x] Functional demo URL
- [ ] **This video, under 3:00, on YouTube, public**
- [ ] Documentation naming the CockroachDB and AWS tools used and how
- [x] Architectural diagram (optional) — generated
