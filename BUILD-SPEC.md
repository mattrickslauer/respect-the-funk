---
title: "RemixKit — Build Spec"
subtitle: "A provenance-clean UGC kit engine + creator marketplace. Backblaze Generative Media Hackathon submission and Respect the Funk's release-plumbing product, built as one thing."
status: "DRAFT for approval — no code yet, by design (same discipline as /research)"
date: "2026-07-20"
deadline: "2026-08-03 17:00 EDT"
---

> **Working name: RemixKit.** Placeholder — see [§13 Open decisions](#13-open-decisions). Product is generic (sold to any label/artist); **Respect the Funk is tenant #1** and *Losing Sleep* is the dogfood release.

> **There is now a second, later track.** [MEMORY-SPEC.md](./MEMORY-SPEC.md) adds the CockroachDB × AWS *Build with Agentic Memory* hackathon (deadline **2026-08-18**) as a branch taken **after** this submission ships. Nothing in it may touch the Aug 3 path before Aug 3. It amends §12 by appending a track, and it reverses §2's "no database" decision on that branch only — with the conflict stated in its §8.

---

## 0. Why one build serves both goals

The hackathon rewards a generative-media app that (a) orchestrates multiple AI providers via **Genblaze**, (b) stores assets/metadata/**provenance** in **B2**, and (c) solves a real problem with production readiness. Four **equally-weighted** criteria: Real-World Utility · Production Readiness · B2 Storage + Data Orchestration · Use of Genblaze.

Your own research ([`/research/_synthesis/SYNTHESIS.md`](./research/_synthesis/SYNTHESIS.md)) concludes the only defensible label product is: *"make each release cheap, correctly-plumbed, fast, and legally clean — more releases beat more spend."* It also concludes that the supported mechanism for **UGC spread specifically** is **templatability** (*"the song is the substrate, the template is the product"*).

> **Scope note (2026-08-03).** Those are two findings, and only the first is about the product. The second is about how sounds spread on TikTok, and it was for a while read as the definition of what RemixKit makes — which is how the app came to have one hardcoded asset shape. See [PRODUCT.md § What the product generates, precisely](./PRODUCT.md). Templatable backdrops are one format the suite ships; performance clips, press and cover stills, announcements to camera, lyric cards and voice-over are others.

**RemixKit is both.** It manufactures provenance-clean media for any release at near-zero marginal cost — in whichever format the release needs — tracks every dollar, and runs a creator marketplace that rewards fans for posting. Every feature does double duty:

| Feature | Hackathon criterion it maxes | RTF research constraint it satisfies |
|---|---|---|
| Genblaze fan-out across video+image+audio providers | Use of Genblaze | One release's whole media set at near-zero marginal cost — including the templatable backdrop, Pillar 13's one supported UGC lever |
| Genblaze manifest → B2, embedded in the media file | B2 + provenance; "provenance-tracking" is a named example category | **Disclosure-by-default** differentiator vs. an industry under live FTC investigation (Synthesis §6) |
| `genblaze index` → Parquet in B2 | B2 + Data Orchestration | *"Everything analytics-ready for ingestion"* |
| Per-`run` cost ledger from Genblaze steps | Production Readiness | *"The bill is a product feature"* — per-tenant cost attribution |
| Cloud Run min-instances=0 + Neon scale-to-zero | Production Readiness | *"Scale to zero is an absolute necessity"* (Pillar 09) |
| Owned masters + AI/stock/PD footage only | Clean demo, no rights drama | Sidesteps the film-clip death (Pillar 06) |
| Creator attribution links + leaderboard + rewards | Real-World Utility | Tracks the **one measurable event** (clickthrough, never streams); keeps you "legibly linked" |

**The honest guardrail (non-negotiable in copy and demo):** UGC breakout is a **sub-1% lottery** and seeding does not reliably cascade (Pillar 13). RemixKit does **not** claim to make a song go viral. It claims: *cheaper, cleaner, faster shots on goal, with the plumbing correct so a win pays.* This modesty is also a differentiator in a field of "our AI makes you go viral" demos.

---

## 1. Users & roles (the marketplace)

Three roles. Full-marketplace scope (your choice) means all three ship; §12 marks what can be simulated if time runs short.

1. **Label / Artist (tenant admin).** Uploads an owned master, sets the hook window, generates kits, funds a reward pool, sees the cost ledger + attribution dashboard. *(RTF, tenant #1.)*
2. **Creator (fan / UGC maker).** Browses a release's public kit, accepts the FTC-disclosure terms, gets a **personal tracked link + required disclosure caption**, downloads remix-ready assets (with provenance embedded), posts, and climbs a leaderboard for rewards.
3. **Ops / judge (read-only demo).** A public release page + a provenance verifier (`genblaze verify`) anyone can use to confirm an asset's origin — the "wow" for judges and the compliance proof for RTF.

---

## 2. Architecture

```
                    ┌─────────────────────────────────────────────┐
   Label/Creator ──▶│  Next-thin frontend (server-rendered HTML +  │   (all-Python option:
   (browser)        │  htmx/Jinja, or static React) on Cloud Run   │    Jinja templates; no
                    └───────────────┬─────────────────────────────┘    separate frontend)
                                    │ HTTPS
                    ┌───────────────▼─────────────────────────────┐
                    │  FastAPI app  (Cloud Run, min-instances=0)   │
                    │  - REST API (§10)                            │
                    │  - auth, tenant scoping                      │
                    │  - enqueues kit jobs → Cloud Tasks           │
                    │  - attribution redirect handler (/r/{code})  │
                    └───┬───────────────┬──────────────────┬───────┘
                        │               │                  │
              enqueue   │        read/write                │ read/write
                        ▼               ▼                  ▼
              ┌──────────────┐  ┌───────────────┐   ┌──────────────┐
              │ Cloud Tasks  │  │ Neon Postgres │   │ Backblaze B2 │
              │ (queue)      │  │ (scale-to-0)  │   │ (S3-compat)  │
              └──────┬───────┘  └───────────────┘   └──────▲───────┘
                     │ OIDC push                            │ Genblaze
                     ▼                                      │ ObjectStorageSink
              ┌───────────────────────────────────┐        │
              │  Worker endpoint (same FastAPI or  │────────┘
              │  Cloud Run Job): runs Genblaze     │
              │  Pipeline.abatch_run() fan-out,    │──▶ AI providers
              │  writes manifests+assets to B2,    │    (GMI Cloud / OpenAI /
              │  cost + rows to Neon               │     Google / ElevenLabs …)
              └───────────────────────────────────┘
```

**Stack (locked per your decision):**
- **Language/runtime:** Python 3.11+, FastAPI, Uvicorn. Genblaze is Python-native and "embeds into FastAPI, Lambda, Cloud Run" (its own docs).
- **Compute:** Cloud Run service `min-instances=0`, `max-instances` small. Long video jobs run in a **Cloud Run Job** (or a second `/worker` endpoint) triggered by **Cloud Tasks** — keeps the web service request-fast and lets generation take minutes without holding an HTTP request. (Cloud Run Jobs scale to zero and bill only while running — respects Pillar 09's "min-instances=0 isn't free under instance billing" caveat by isolating the long work.)
- **DB:** **Neon serverless Postgres** — genuinely scales to zero (the hard part Pillar 09 flags), branchable, cheap idle. Relational is required for the marketplace (attribution joins, ledger, leaderboard).
- **Storage:** Backblaze **B2** via `genblaze-s3` `S3StorageBackend.for_backblaze(bucket)`, `KeyStrategy.HIERARCHICAL`.
- **Gen media:** **Genblaze** `Pipeline`; providers via **GMI Cloud** (grab the free credits — first 270 participants) as primary, OpenAI/Google/ElevenLabs as secondary to demonstrate multi-provider orchestration.
- **Frontend:** default **server-rendered Jinja + htmx** inside the same FastAPI app (one deploy, fast to build, judge-credible). React only if a specific screen needs it.

---

## 2b. Scalability doctrine (the design constraint that outranks the others)

Stated first because you asked for it to outrank everything: **design decisions are resolved in favor of scale even at the cost of build speed.** The eight rules below are binding on every later section.

1. **Generate once, remix infinitely.** The expensive AI step is **per-release**; the cheap deterministic step is **per-fan**. Never let a per-fan action trigger a model call. This is the whole margin story (§7b).
2. **Nothing large ever transits the app server.** Uploads and downloads use **presigned B2 URLs**, browser↔B2 direct. The FastAPI service moves JSON only. This decouples request volume from bandwidth entirely and is what lets a 1-vCPU service serve a viral release.
3. **Everything long-running is a queued job, never a request.** Cloud Tasks → Cloud Run Job. HTTP handlers stay <200ms. A 10-minute video generation must never occupy a web instance.
4. **Every job is idempotent and keyed.** Jobs carry a deterministic key (`kit_id` / `session_id` + input hash); re-delivery is a no-op that returns the existing result. Cloud Tasks retries *will* double-deliver — design for it rather than hoping.
5. **Content-addressable reuse.** Genblaze assets carry SHA-256. Identical prompt+model+params → reuse the existing B2 object instead of regenerating. Directly cuts the dominant cost line (Pillar 07: AI video is 92% of variable cost).
6. **Tenant is a partition key everywhere** — Genblaze `tenant_id`, B2 key prefix, every Postgres row, every metric. Multi-tenancy is cheap on day one and near-impossible to retrofit.
7. **Serve from cache/CDN, not from origin.** Finished kits and composites are immutable → long-TTL, hashed keys, fronted by a CDN. A release going viral must be a *storage-read* event, not a compute event.
8. **Stateless services, state only in Neon + B2.** No local disk assumptions, no in-memory sessions. Any instance can serve any request; scale-out is horizontal and unbounded.

**The load profile this is designed for:** traffic is spiky and unpredictable by nature (Pillar 13 — virality is a lottery). The system must sit at **~$0 idle** and absorb a 1000× spike without a redeploy. Rules 2, 3, and 7 are what make that true: at peak, the hot path is B2 + CDN, and compute barely participates.

**Deliberately deferred (write down so we don't gold-plate):** sharding, multi-region, Kubernetes, a custom job scheduler, real-time websockets. None are needed before the architecture above saturates, and each would cost days we do not have.

---

## 3. Data model (Neon Postgres)

Minimal, warehouse-friendly (integer/uuid keys, `created_at` on everything, soft money in cents). All rows carry `tenant_id` → mirrors Genblaze's `tenant_id` so B2 layout, DB, and manifests share one tenant axis.

```sql
tenant(id, name, slug, reward_pool_cents, created_at)
release(id, tenant_id, title, artist, spotify_url, isrc,
        master_b2_key, hook_start_ms, hook_end_ms, status, created_at)     -- hook window = the free lever
kit(id, release_id, tenant_id, name, brief_json, status,                    -- status: queued|running|ready|failed
    run_id, parent_run_id, total_cost_cents, created_at)                    -- run_id ↔ Genblaze Run
asset(id, kit_id, tenant_id, modality, provider, model,                     -- modality: video|image|audio
      b2_key, thumb_b2_key, manifest_b2_key, sha256,
      cost_cents, params_json, created_at)                                  -- one row per Genblaze step output
provider_cost(id, asset_id, provider, model, units, unit_cost_cents,        -- the ledger; one row per billable call
      cost_cents, created_at)
creator(id, handle, email, platform, created_at)
disclosure_acceptance(id, creator_id, release_id, terms_version,            -- FTC audit trail
      required_caption, accepted_at, ip)
attribution_link(id, creator_id, release_id, code, dest_url,                -- code → /r/{code} redirect
      created_at)
click(id, attribution_link_id, ts, ua, referer, ip_hash)                    -- the one measurable event
post(id, creator_id, release_id, platform, url,                             -- creator-submitted or detected
     detected_sound_id, verified, created_at)
reward(id, creator_id, release_id, amount_cents, reason, status, created_at) -- status: pending|approved|paid
```

Views for the warehouse story: `v_kit_cost` (kit → total + per-provider), `v_creator_leaderboard` (creator → clicks, posts, rewards), `v_release_funnel` (release → kits → clicks → posts).

---

## 4. The Genblaze kit generator (the core)

A **kit** = one Genblaze `Run` whose steps fan out into a pack of provenance-clean assets for one release, in one **format**. The format is a stored record (`Recipe`) carrying the prompt, negatives, length policy, face policy and aspect ratio — so the sample below is one format's shape, not the only one the generator makes. Real API from `backblaze-labs/genblaze`:

```python
from genblaze_core import Pipeline, Modality, ObjectStorageSink, KeyStrategy
from genblaze_gmicloud import GMICloudVideoProvider, GMICloudImageProvider
from genblaze_elevenlabs import ElevenLabsProvider      # provider extras are opt-in
from genblaze_s3 import S3StorageBackend

def build_kit(release, brief, tenant_id) -> "Result":
    storage = ObjectStorageSink(
        S3StorageBackend.for_backblaze(BUCKET),
        key_strategy=KeyStrategy.HIERARCHICAL,   # runs/{tenant}/{date}/{run_id}/…
    )
    p = Pipeline(f"kit:{release.id}", tenant_id=tenant_id)

    # 1) AI video, one step per variant — here, the templatable-backdrop format
    for spec in brief.video_specs:               # e.g. 3–5 moods, 9:16
        p = p.step(GMICloudVideoProvider(), model="seedance-2-0-260128",
                   prompt=spec.prompt, modality=Modality.VIDEO,
                   duration=spec.seconds, aspect_ratio="9:16")

    # 2) Captioned hook-lyric cards (image) — the "use this sound" hook made visual
    for line in brief.hook_lines:
        p = p.step(GMICloudImageProvider(), model="seedream-5.0-lite",
                   prompt=f"Bold lyric card, vertical, text: “{line}”",
                   modality=Modality.IMAGE)

    # 3) Optional TTS / SFX stinger to seed a format (kept off by default)
    if brief.tts:
        p = p.step(ElevenLabsProvider(), model="tts", prompt=brief.tts_text,
                   modality=Modality.AUDIO)

    return p.arun(sink=storage, timeout=900)     # async fan-out; abatch_run for many kits
```

- **Hook window** (`release.hook_start_ms`) drives prompt + is written into every manifest → the free lever from Pillar 13 is a first-class input, not an afterthought.
- Each step's output → one `asset` row; `result.manifest` → `manifest_b2_key`; `manifest.verify()` gates `status=ready`.
- `chain=True` image→video (Genblaze supports it) lets us turn a still into motion when a provider lacks direct T2V — keep as a fallback path.
- **Footage is AI/stock/PD only. Owned master is the only copyrighted input.** This is enforced at upload, not by convention (Pillar 06).

---

## 5. B2 layout, provenance & disclosure

`KeyStrategy.HIERARCHICAL` gives: `remixkit/runs/{tenant}/{YYYY-MM-DD}/{run_id}/manifest.json` + `assets/…`. We add:

- `remixkit/masters/{tenant}/{release_id}.wav` — owned source (private).
- `remixkit/kits/{release_id}/{kit_id}/…` — public, served assets + thumbnails.
- `remixkit/analytics/{tenant}/{date}/manifests.parquet` — output of `genblaze index manifest.json -o` on a schedule → the **analytics-ready** artifact judges and your future warehouse both consume.

**Provenance = disclosure.** Each delivered creator asset has its Genblaze manifest **embedded in the file** (`Mp4Handler().embed(...)`, PNG/MP3 handlers likewise). So when a creator downloads a clip, the record of *which AI model made it, from which prompt, for which release* travels inside the media. The public `/verify` page runs `manifest.verify()` on any uploaded asset. This is simultaneously the hackathon's "provenance-tracking" wow and RTF's FTC differentiator ($53,088/violation; industry under live investigation per Synthesis §6).

---

## 6. Cost ledger

Every Genblaze step is a billable provider call. On worker completion we write one `provider_cost` row per step (provider, model, units, unit_cost, cost_cents) and roll up to `kit.total_cost_cents` and a per-tenant monthly total. Unit costs live in a small `pricing.yaml` (dated, editable — Pillar culture: *"price it, date it"*). The label dashboard shows **cost per kit and cost per delivered asset** — the "bill is a feature" made literal, and a clean Production-Readiness signal for judges.

> Hard-cap caveat carried from research: LiteLLM's per-tenant hard-cap path is compromised (Synthesis §8). We enforce budget **before enqueue** (estimate kit cost, block if it exceeds the tenant's remaining pool), not mid-run.

---

## 7. Attribution & incentive marketplace

- **Link:** creator accepts terms → we mint `attribution_link.code` → share URL `https://…/r/{code}`. The `/r/{code}` handler logs a `click` (the one event you *can* measure — Pillar 02/09) and 302s to the Spotify smart link.
- **Disclosure gate:** they cannot get a link without accepting the versioned FTC terms; we hand them the **required disclosure caption** (`#ad` / "paid partnership with Respect the Funk") and store `disclosure_acceptance` as an audit trail. Disclosure is *enforced by the funnel*, not requested politely.
- **Leaderboard:** `v_creator_leaderboard` ranks by clicks + verified posts.
- **Rewards / payouts (Layer 3):** label funds `reward_pool_cents`; rules convert clicks/posts → `reward` rows; payout via **Stripe Connect**. *Heavy — see §12; demo can run in "approved, not yet paid" state and still be complete.*

**What we deliberately do NOT build:** any "which archetype goes viral" recommender (Pillar 13: folklore), any stream-attribution claim (impossible — Synthesis §2), any bought-engagement or seeding-cascade feature (EV-negative — Pillar 05/13).

---

## 7b. Composite clips — fans creating together (the growth layer)

This resurrects **Idea 1** from the original brief ("collaborative post webapp") in the only form the evidence supports: not as a generic co-posting tool, but as **the cheapest possible way to turn one fan into several.**

### The mechanic

1. Fan A opens a release's kit and picks a **composite format** (e.g. 3-panel split screen, sequential relay, call-and-response, before/after).
2. They record or select their segment against the kit's templatable backdrop, synced to the **hook window**.
3. They get an **invite link** for the empty slots — sent to friends by text/DM. *The invite is the product's viral surface.*
4. Friend B (and C) open the link — **no install, no account needed to contribute** — record their slot in the browser, and submit.
5. When slots fill, the server **composites** the segments into one finished vertical clip, synced to the hook, with the provenance manifest embedded and **every contributor's attribution link** minted.
6. All contributors get the finished clip + their tracked link + the required disclosure caption. Each is now a creator in the marketplace.

### Why this is the right layer to add

- **It manufactures the one thing that actually predicts spread.** Pillar 13: creator *follower count* and network structure predict virality; audio does not. A composite clip is natively multi-network — it enters 3 follower graphs instead of 1, by construction.
- **It is a real viral loop, not a metaphor.** Every composite requires ≥1 invite. Invite → contribute → contributor becomes an inviter. This gives us a measurable **K-factor** (`invites_sent × accept_rate × onward_invite_rate`) — the growth metric investors underwrite. Track it from day one as a first-class table.
- **Social obligation beats incentive design.** A friend asking you to fill slot 2 of 3 converts far better than a label asking strangers to make content. It is also *organic by construction* — the fan is making something with friends, not performing a brand ask.
- **It does not violate any research finding.** No virality claim, no seeding cascade, no bought engagement. It lowers the effort and raises the social pull of making a post — squarely the templatability thesis.

### ⚠️ The unit-economics insight this unlocks (the core scalability argument)

> **Generate once, remix infinitely.**
> **Expensive generative AI runs once per *release*. Cheap deterministic compositing runs once per *fan*.**

Kit generation (Genblaze → multiple video/image/audio providers) is the costly step — dollars per kit. But it is amortized across every fan who ever remixes that release. Compositing a fan's clip is **ffmpeg: deterministic, CPU-only, seconds, fractions of a cent** — no model call, no GPU.

So marginal cost per additional fan is **storage + a few seconds of CPU**, not a generation bill. Cost per release is flat; revenue and reach scale with fans. That is the difference between a demo that gets expensive as it succeeds and a company whose margins *improve* with scale — and it is the single most important thing to say to an investor and to a hackathon judge grading Production Readiness.

### Implementation

- **Capture:** browser `MediaRecorder` → direct-to-B2 presigned PUT (never proxied through the app server — see §2b).
- **Compositing:** `ffmpeg` in a **Cloud Run Job**, driven by a declarative `format_spec` (grid/sequence, slot count, durations, audio bed = the hook window). Pure function of inputs → cacheable, retryable, idempotent.
- **Provenance:** the composite's manifest is a Genblaze run with `parent_run_id` = the kit's run → **lineage from finished fan clip back to the exact AI assets and models used.** Genblaze gives us this for free, and it is a genuinely impressive thing to show a judge.
- **Storage tiering:** raw contributor segments are transient (lifecycle-delete after N days); finished composites persist.

### Added tables

```sql
composite_format(id, key, name, slot_count, spec_json)                  -- 3-panel, relay, call-and-response…
composite_session(id, release_id, kit_id, format_id, initiator_creator_id,
                  status, run_id, parent_run_id, output_b2_key, created_at)
composite_slot(id, session_id, slot_index, creator_id, raw_b2_key,
               duration_ms, status, filled_at)
invite(id, session_id, slot_index, code, sent_by_creator_id,
       opened_at, accepted_at, accepted_by_creator_id, created_at)      -- K-factor source of truth
```

Metrics to expose on the dashboard from day one: **invites sent, open rate, fill rate, time-to-complete, contributors per composite, onward-invite rate, K-factor.**

---

## 8. Deployment & scalability

- Cloud Run service `--min-instances=0 --max-instances=3`, concurrency default.
- Cloud Run **Job** for kit generation, invoked via Cloud Tasks (OIDC push). Bills only while a kit runs; idle = $0 compute.
- Neon idle → scales to zero; wakes on first query (~sub-second).
- B2: pay-per-GB, no idle floor.
- **Idle cost target: ~$0/mo**, matching Pillar 09. The only fixed floors are any paid plan seats — avoid them.

---

## 9. Demo video (≤3 min) — scripted to the 4 equal criteria

1. **0:00–0:30 — Real-World Utility.** "Music breaks through fan remixes, but making usable, *legal*, disclosed assets for every release is manual and slow. RemixKit does it in one click." Show RTF tenant, *Losing Sleep*, set hook window.
2. **0:30–1:30 — Use of Genblaze.** Click *Generate Kit*; show the fan-out across GMI Cloud video + image + ElevenLabs audio; assets appear. Say the provider names (submission must document models used).
3. **1:30–2:15 — B2 + Provenance/Disclosure.** Open the B2 bucket layout; open a delivered .mp4; run `genblaze verify` live → manifest confirms model/prompt/release. "Disclosure travels inside the file." Show the Parquet analytics export.
4. **2:15–3:00 — Production Readiness + Marketplace.** Cost ledger (cost per kit), creator accepts terms → gets tracked link + required caption, clicks register on the leaderboard. Close on the honest line: *cheaper, cleaner, faster shots — not a virality promise.*

---

## 10. API surface (FastAPI)

```
POST /tenants                         create tenant
POST /releases                        upload master (→ B2), set spotify_url/isrc
PATCH /releases/{id}/hook             set hook_start_ms/hook_end_ms
POST /releases/{id}/kits              enqueue kit gen (body: brief) → 202 + kit_id
GET  /kits/{id}                       status + assets + cost
POST /internal/worker/run-kit         (Cloud Tasks target) executes Genblaze pipeline
GET  /releases/{id}                   public kit page (creator-facing)
POST /releases/{id}/join              creator accepts terms → attribution_link + caption
GET  /r/{code}                        log click → 302 to smart link
POST /posts                           creator submits post URL (verify sound_id)
GET  /releases/{id}/leaderboard       ranked creators
GET  /tenants/{id}/ledger             cost dashboard
POST /verify                          upload asset → manifest.verify() result
```

---

## 11. Env / secrets / accounts to line up (Day 1)

- Register team on Devpost; **claim GMI Cloud credits** (first 270).
- B2 bucket + `B2_KEY_ID`, `B2_APP_KEY`. GCP project + Cloud Run/Tasks + Artifact Registry. Neon project + `DATABASE_URL`.
- Provider keys: `GMI_API_KEY` (primary), `OPENAI_API_KEY`, `GOOGLE_*`, `ELEVENLABS_API_KEY` (as used).
- GitHub repo; if private, **grant access to `b2genblaze`** (submission rule).
- Spotify smart link for *Losing Sleep* as the attribution destination.

---

## 12. 14-day plan — MVP spine first, marketplace layered

The core is demo-able by ~Day 7 so a slipped payout layer never sinks the submission. Honest risk flags inline (research house style).

**Layer 0 — Spine (Days 1–3). Must exist.**
- Repo, FastAPI skeleton, Neon schema, B2 bucket, Genblaze installed with GMI Cloud.
- One release upload + hook window. **One** end-to-end: song → single Genblaze step → asset+manifest in B2 → row in Neon → `verify` passes.

**Layer 1 — Kit generator (Days 3–7). The hackathon core.**
- Multi-step, multi-provider fan-out (video+image+audio). Cloud Tasks + worker job. Cost ledger. Public kit page. Parquet export.
- ⚠️ *Risk: provider latency/quotas on video gen. Mitigation: cache generated assets; keep kit size small (3–5 videos); GMI credits reduce cost pressure.*

**Layer 2 — Creator attribution + disclosure (Days 7–10). High value, cheap.**
- Join flow, FTC terms + required caption, `/r/{code}` clicks, leaderboard.

**Layer 3 — Rewards/payouts (Days 10–11). STRETCH.**
- Reward rules + Stripe Connect. ⚠️ *Stripe Connect onboarding + KYC is slow and heavy for 14 days. Fallback: ship rewards in `approved` state with a "payout via Stripe" stub; the demo and utility story are complete without money actually moving. Do not let this block submission.*

**Days 11–13 — Dogfood + package.**
- Generate a **real** kit for *Losing Sleep*. Record demo. Write README + the required B2/Genblaze integration writeup + model/provider list. Deploy public URL.

**Day 14 — Buffer + submit** (deadline Aug 3, 17:00 EDT — submit Day 13 EOD if possible).

---

## 13. Open decisions (need your call before/at kickoff)

1. **Product name.** RemixKit (working) vs. something label-branded. Affects nothing technical; pick before the demo.
2. **Team size / who's building.** Solo or team? Changes how aggressive Layer 3 can be.
3. **GMI credits claimed?** Time-sensitive (first 270). If missed, budget ~$X of provider spend — needs a number.
4. **Spotify smart link** for *Losing Sleep* — exact URL for the attribution destination.
5. **Frontend taste:** Jinja+htmx (default, one deploy) vs. a small React app (nicer demo, more setup). Recommend Jinja for 14 days.
6. **Rewards realness:** simulated (approved-not-paid) vs. real Stripe payout in the demo. Recommend simulated given the timeline.

---

## 14. What this deliberately is not (so we don't drift)

- Not a virality engine. Not a stream-attribution tool. Not a seeding/bought-engagement product. Not a demographic-targeting "clock." Not film-clip creative.
  Each is dead for a sourced reason in `/research`. RemixKit is the part that survived: **cheap, clean, fast, disclosed shots on goal — and a marketplace that rewards the fans who take them.**
```
