# Pillar 7 — Infrastructure & AI Cost Model ("The Bill")

**Pricing verified as of 2026-07-15.** Every price below links to the vendor page it was read from. Prices decay fast — re-verify anything older than ~60 days. Items that could not be confirmed against a vendor's own page are marked **UNVERIFIED — needs check** rather than guessed.

---

## Bottom line

| Scenario | Fixed (subs) | Variable (usage) | **Total / mo** |
|---|---|---|---|
| **Scrappy** — 1 song, 20 movies, 100 posts/mo, ~20 cutscenes | **$20** | ~$35 | **≈ $55/mo** |
| **Growth** — 100 movies, 1,000 posts/mo, ~200 cutscenes | **$27** | ~$335 | **≈ $360/mo** |
| **Scale** — 1,000 movies, 10k posts/mo, ~2,000 cutscenes | **$62** | ~$3,350 | **≈ $3,400/mo** |

*Excludes ad spend (Pillar 2) — at Growth/Scale, ad spend dwarfs all infrastructure below.*

**The three things that matter:**

1. **AI video generation is the bill.** Everything else — hosting, DBs, LLM tokens, indexing, storage — rounds to noise. At Growth, ~92% of variable cost is AI footage generation. At Scale, ~93%. Choosing Veo 3.1 Standard ($0.40/s) over Veo 3.1 Lite ($0.05/s) turns the Scale bill from **$3,400 → $24,300/mo**. This single dropdown is worth more than every other optimization in this document combined.
2. **The fixed floor is ~$20/mo and it's almost all one Vercel seat.** Clerk (50k MAU), PostHog (1M events), Neon and Upstash free tiers cover the entire webapp through 1,000 users. At Scrappy volume, fixed cost is ~36% of the bill despite the product barely being used — normal, and fine, because the floor is $20 not $500.
3. **Everything else is ~free.** Self-hosted scene detection is ~$0.03/movie vs $18 on Google Video Intelligence (600x). Subtitles beat transcription. pgvector beats every dedicated vector DB at our scale. LLM cost per post is $0.006.

---

## Assumptions (stated up front)

| Assumption | Value | Why |
|---|---|---|
| Movie proxy format | 1080p H.264 @ 8 Mbps, 2 hr avg | → **7.2 GB/movie** |
| Generated clip | 30s vertical 9:16 @ 10 Mbps | → **37.5 MB/clip** |
| Frame sampling | **Keyframes only** (~1,000 shots/movie) | 7.2x cheaper than 1 fps; see §3 |
| Embedding dim | 768-dim float32 = 3,072 B/vector | → 1M vectors ≈ 3 GB raw |
| Post generation | 1,500 in + 300 out tokens | Brand voice + song context → copy |
| Cutscene reasoning | 8,000 in + 500 out tokens | Scene candidates + params → EDL |
| Render job | 90s active CPU, 3 GB mem | FFmpeg burn-in, 30s 1080p |
| Ad spend | **Excluded** | Owned by Pillar 2 |

**Context note:** this is now a **multi-tenant** engine for labels/artists on Meta + TikTok, and **scale-to-zero is a hard requirement**. Both shape the recommendations — see §12 and §13.

---

## 1. Video processing (FFmpeg render)

Unit: **render one 30-second 1080p vertical video with audio + burned-in subtitles.**

| Vendor | Rate | Math | **Cost/render** |
|---|---|---|---|
| **Vercel Fluid Compute** | [$0.128/Active-CPU-hr + $0.0106/GB-hr](https://vercel.com/docs/functions/usage-and-pricing) | (90/3600)×0.128 + (3×100/3600)×0.0106 | **$0.0041** |
| **AWS Lambda** | [$0.0000166667/GB-s](https://aws.amazon.com/lambda/pricing/) | 3 GB × 90 s = 270 GB-s | **$0.0045** |
| **Coconut.co** | [$0.015/min HD](https://www.coconut.co/pricing) | 0.5 min × 0.015 | **$0.0075** |
| **AWS MediaConvert** (Professional — Basic tier *cannot* do caption burn-in) | [$0.012/min base, 2x HD](https://aws.amazon.com/mediaconvert/pricing/) | 0.5 × 0.024 | **$0.012+** |
| **Mux** | [encoding/storage/delivery](https://www.mux.com/pricing) | — | **N/A** — ABR streaming pipeline, not an FFmpeg job runner |
| **Cloudflare Stream** | [free encode, $0.005/min storage](https://developers.cloudflare.com/stream/pricing/) | — | **N/A** — same shape mismatch |
| **Transloadit** | [$69/mo, $1.80/GB overage](https://transloadit.com/pricing/) | billed on GB in+out | **$0.05–$3.60** ⚠️ blows up if the job imports a full source movie |

**Verdict: Vercel Fluid or Lambda (~$0.004/render).** Both ~3x cheaper than Coconut, ~3–6x cheaper than MediaConvert. Pick whichever the app already lives on.

### Serverless limits are a real constraint

| Limit | Vercel | Lambda |
|---|---|---|
| Max duration | [Hobby 300s hard; Pro 800s GA / 1800s beta](https://vercel.com/docs/functions/limitations) | [900s hard cap](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html) — **default is 3s**, must be raised |
| Max memory | Hobby 2 GB / 1 vCPU; Pro 4 GB / 2 vCPU | 10,240 MB |
| Ephemeral disk | — | 512 MB default; [extra billed $0.0000000309/GB-s](https://aws.amazon.com/lambda/pricing/) — a 7.2 GB movie **will not fit** |

The ephemeral-disk ceiling is the real trap: you cannot pull a whole movie into a Lambda. Architecture must stream byte-ranges of the source, not download it. (Architecture is another agent's call — flagging the cost consequence only.)

---

## 2. Scene detection & indexing — **the 600x decision**

Unit: **index one 2-hour (120 min) movie.**

| Vendor | Rate | **Cost/movie** |
|---|---|---|
| **PySceneDetect (self-hosted)** | ~30 min CPU @ [$0.0472/hr Modal](https://modal.com/pricing) / [~$0.0416/hr t3.medium](https://instances.vantage.sh/aws/ec2/t3.medium)¹ | **~$0.02–0.04** |
| **TwelveLabs** | [$0.0435/min incl. infra](https://www.twelvelabs.io/pricing) | **$5.22** |
| **Google Video Intelligence** — shot only | [$0.05/min](https://cloud.google.com/video-intelligence/pricing) | **$6.00** |
| **AWS Rekognition Video** — segment only | [$0.05/min](https://aws.amazon.com/rekognition/pricing/) | **$6.00** |
| **Azure Video Indexer** — Standard | [$0.09/min](https://prices.azure.com/api/retail/prices)² | **$10.80** |
| **Google / AWS / Azure** — shot + label (full) | $0.15/min | **$18.00** |

¹ AWS EC2's own pricing page is JS-rendered; figure cross-checked via Vantage mirror. Processing time is an engineering estimate (~60–150 fps decode), not a vendor SLA.
² Azure's pricing page is JS-rendered; figure pulled from Microsoft's own public Retail Prices API (primary source).

### Scaling

| Vendor | 10 movies | 100 | 1,000 |
|---|---|---|---|
| **PySceneDetect** | **$0.30** | **$3** | **$30** |
| TwelveLabs | $52 | $522 | $5,220 |
| Google/AWS (shot+label) | $180 | $1,800 | $18,000 |
| Azure Standard | $108 | $1,080 | $10,800 |

**Verdict: PySceneDetect, unambiguously.** 600x cheaper than Google full-indexing at 1,000 movies ($30 vs $18,000). The catch: it gives shot boundaries only, so you run your own labels/embeddings on top — which §3 shows costs ~$3 for the whole 1,000-movie library. Total DIY: **~$33 vs $18,000.** There is no scale at which the managed APIs win here.

---

## 3. Embeddings & vector search

### Sampling assumption dominates everything

2-hour movie = 7,200 s. CLIP ViT-B/32 at ~100 frames/s sustained on an A10 (engineering estimate).

| Strategy | Frames | Self-hosted ([Modal A10 $1.10/hr](https://modal.com/pricing)) | [Replicate ($0.00036/run)](https://replicate.com/andreasjansson/clip-features) |
|---|---|---|---|
| 1 fps | 7,200 | $0.022 | **$2.59** |
| 1 frame / 5s | 1,440 | $0.0044 | $0.518 |
| **Keyframes only (~1,000)** | 1,000 | **$0.0031** | $0.36 |

**7.2x swing from the sampling choice alone.** On self-hosted GPU it's noise either way ($0.02 vs $0.003); on per-run-billed Replicate it's real money at 1,000 movies: **$2,590 (1 fps) vs $360 (keyframes)**. Use keyframes — scene detection already gives you the shot list for free.

### Vector DB at 100k / 1M / 10M vectors

| Vendor | 100k | 1M | 10M | Scale-to-zero? |
|---|---|---|---|---|
| **pgvector on [Neon](https://neon.com/pricing)** ($0.35/GB-mo) | **~$0.21** | **~$2.10** | **~$21** | ✅ autosuspend |
| **pgvector on [Supabase](https://supabase.com/pricing)** (8 GB incl. in Pro) | **$0** marginal | **$0** marginal | ~$6.50 marginal | ❌ $25/mo floor |
| [Turbopuffer](https://turbopuffer.com/pricing) ($0.02/GB-mo) | $16 (min) | $16 (min) | $16 (min) | ❌ **$16/mo min** |
| [Pinecone](https://www.pinecone.io/pricing/) | $20 (Builder) | ~$50 (Standard min) | $50–105 | ❌ **$20/mo min** |
| [Weaviate Cloud](https://weaviate.io/pricing) (Flex) | $45 (min) | $45 (min) | $45 (min) | ❌ **$45/mo min** |
| [Qdrant Cloud](https://qdrant.tech/pricing/) | ~$51 | ~$512 | ~$5,125 | ❌ RAM-resident |
| [LanceDB](https://lancedb.com/pricing) | **UNVERIFIED** — Cloud in free beta, no published pricing | — | — | — |

*Qdrant's ~$0.078/GB-RAM-hr is third-party-derived (their calculator is JS-only) — **UNVERIFIED against a primary source**. Quantization/on-disk mode would cut it 4–32x.*

### Is a vector DB even needed? **No.**

1,000 movies × ~1,000 shots = **~1M vectors**. pgvector HNSW handles that with sub-100ms ANN latency for an interactive workload. We need Postgres anyway for tenants/movies/scenes/licensing. Colocating means filtered search ("similar shots AND licensed AND tenant=X") is one SQL query, not a cross-service join. Marginal cost **~$2/mo** vs **$16–$512/mo** for a dedicated service — every one of which has a **monthly minimum that violates the scale-to-zero requirement.**

**Use pgvector on Neon.** Revisit only above ~10M vectors or if QPS outgrows a shared instance.

---

## 4. Transcription — **probably $0**

| Vendor | Rate | **$/hr audio** |
|---|---|---|
| **Existing subtitle files** | OpenSubtitles / bundled | **~$0** |
| [Deepgram Nova-3 (pre-recorded)](https://deepgram.com/pricing) | $0.0077/min | **$0.46** ($200 free credit) |
| Deepgram Nova-3 (streaming) | $0.0048/min | $0.29 |
| Groq / OpenAI Whisper / AssemblyAI | — | **UNVERIFIED — needs check** |
| Self-hosted Whisper | [$0.59/hr T4](https://modal.com/pricing) ÷ ~20x realtime | ~$0.03 |

**Movies already have subtitles.** For product (B)'s library, dialogue is a solved, near-free problem — a `.srt` gives you exact text *and* timecodes, which is precisely what "dialogue over the cut" needs. Even at full price, transcribing 1,000 movies (2,000 hrs) via Deepgram is a **one-time ~$920**, not recurring. This line item is a rounding error either way; treat as **$0 (subtitles)** in the bill.

⚠️ **The pivot changes this.** Moving to AI-generated / stock footage means there are no pre-existing subtitles for generated clips. Dialogue must then come from the generation prompt or TTS — cost unmodeled here. **Gap flagged.**

---

## 5. LLM costs

[All rates from Anthropic's pricing docs](https://platform.claude.com/docs/en/about-claude/pricing), verified 2026-07-15:

| Model | Input /MTok | Output /MTok | Cache write (5m) | Cache read | Batch in/out |
|---|---|---|---|---|---|
| **Claude Opus 4.8** | $5 | $25 | $6.25 | $0.50 | $2.50 / $12.50 |
| **Claude Sonnet 5** (intro, thru Aug 31 2026) | **$2** | **$10** | $2.50 | $0.20 | $1 / $5 |
| Claude Sonnet 5 (from Sep 1 2026) | $3 | $15 | $3.75 | $0.30 | $1.50 / $7.50 |
| **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) | $1 | $5 | $1.25 | $0.10 | $0.50 / $2.50 |

⚠️ **Sonnet 5 introductory pricing ends Aug 31, 2026 — a +50% price increase lands in ~6 weeks.** Model the bill at $3/$15.
⚠️ Opus 4.7+ / Sonnet 5 use a **newer tokenizer producing ~30% more tokens for the same text** — real cost is ~30% above naive estimates on those models.

**Levers:** Batch API = [flat 50% off in and out](https://platform.claude.com/docs/en/about-claude/pricing#batch-processing). Cache read = **0.1x input** (write 1.25x for 5m / 2x for 1h) — pays for itself after **one** read at 5m. **Discounts stack.**

Competitors ([Gemini](https://ai.google.dev/gemini-api/docs/pricing)): Gemini 3.1 Flash-Lite $0.25/$1.50; Gemini 2.5 Flash $0.30/$2.50; Gemini 3.1 Pro $2/$12. OpenAI: **UNVERIFIED — needs check.**

### Cost per generated post (1,500 in + 300 out)

| Config | Math | **Cost** |
|---|---|---|
| Sonnet 5, naive | (1500×$2 + 300×$10)/1M | **$0.0060** |
| Sonnet 5 + cache (1,200 cached) | (300×$2 + 1200×$0.20 + 300×$10)/1M | **$0.0038** (−36%) |
| Sonnet 5 + cache + batch | above × 0.5 | **$0.0019** (−68%) |
| Haiku 4.5, naive | (1500×$1 + 300×$5)/1M | **$0.0030** |
| **+ render** (§1) | +$0.0041 | **≈ $0.008–0.010/post** |

**10,000 posts/mo ≈ $60 of LLM + $41 of render ≈ $100.** LLM tokens are not the bill.

### Cost per generated cutscene (8,000 in + 500 out)

| Config | **Cost** |
|---|---|
| Sonnet 5 | **$0.021** |
| Opus 4.8 | **$0.053** |
| Sonnet 5 + batch | **$0.011** |
| 1,000 overnight cutscene-selection calls, Sonnet 5 batch | **$10.50** (vs $21 sync) |

The reasoning call is **~0.7% of a cutscene's cost.** Footage is ~99%. **Do not optimize the LLM here** — use Opus if it picks better scenes; the $0.03 delta is irrelevant against $1.50+ of footage.

---

## 6. AI video generation — **now the primary cost driver**

Since the product pivoted from copyrighted film to public-domain / licensed stock / AI-generated footage, this is no longer an alternative — it's the main input.

Unit: **30 seconds of AI video.**

| Vendor / model | Rate | **30s cost** | Source |
|---|---|---|---|
| **Veo 3.1 Lite** (720p) | $0.05/s | **$1.50** | [ai.google.dev](https://ai.google.dev/gemini-api/docs/pricing) |
| Veo 3.1 Lite (1080p) | $0.08/s | $2.40 | same |
| **Veo 3.1 Fast** (720p) | $0.10/s | **$3.00** | same |
| Veo 3.1 Fast (1080p) | $0.12/s | $3.60 | same |
| **Veo 3.1 Standard** (720p/1080p) | $0.40/s | **$12.00** | same |
| Veo 3.1 Standard (4K) | $0.60/s | $18.00 | same |
| **Runway Gen-4 Turbo** | 5 cr/s @ [$0.01/cr](https://docs.dev.runwayml.com/guides/pricing/) = $0.05/s | **$1.50** | [docs.dev.runwayml.com](https://docs.dev.runwayml.com/guides/pricing/) |
| **Runway Gen-4.5** | 12 cr/s = $0.12/s | **$3.60** | same |
| Runway Seedance 2.0 (1080p) | 40 cr/s = $0.40/s | $12.00 | same |
| Runway Seedance 2.0 (4K) | 150 cr/s = $1.50/s | $45.00 | same |
| Kling 3.0 | ~$0.10/s | ~$3.00 | **UNVERIFIED** — third-party only |
| Luma Ray 2 | ~$0.04/s | ~$1.20 | **UNVERIFIED** — third-party only |
| Sora / Pika | — | — | **UNVERIFIED — needs check** |

⚠️ **Veo generates max 8s per call** → 30s = **4 generations (32s billed)**. Costs above already reflect per-second billing; add ~7% for the rounding.

**Images** ([Imagen 4](https://ai.google.dev/gemini-api/docs/pricing)): Fast **$0.02**, Standard **$0.04**, Ultra **$0.06**/image. OpenAI/Midjourney/Stability: **UNVERIFIED — needs check.**

### The footage decision, priced

| Footage source | Cost/30s cutscene | Scale (2,000/mo) |
|---|---|---|
| **Licensed stock / public domain** (library amortized) | **~$0.03** | **~$60** |
| **Veo 3.1 Lite / Runway Gen-4 Turbo** | **~$1.55** | **~$3,100** |
| Veo 3.1 Fast 1080p / Runway Gen-4.5 | ~$3.65 | ~$7,300 |
| **Veo 3.1 Standard** | **~$12.05** | **~$24,100** |
| Seedance 2.0 4K | ~$45 | ~$90,000 |

**This table is the whole cost model.** A 400x spread on one dropdown. Public-domain/stock footage is ~50x cheaper than even the cheapest AI generation — the pivot's legal motivation and its cost motivation point the same direction, which is a rare gift. **Default to stock/PD; use Veo Lite/Gen-4 Turbo for gap-filling; gate Veo Standard behind an explicit per-tenant budget flag.**

---

## 7. Storage & bandwidth

| Vendor | Storage /GB-mo | Egress | Free tier |
|---|---|---|---|
| [**Cloudflare R2**](https://developers.cloudflare.com/r2/pricing/) | **$0.015** | **$0 — confirmed, uncapped** | 10 GB, 1M/10M ops |
| [**Backblaze B2**](https://www.backblaze.com/cloud-storage/pricing) | **$0.00695** | 3x storage free, then $0.01/GB; **free via Bandwidth Alliance CDN** | 10 GB |
| [AWS S3](https://aws.amazon.com/s3/pricing/) | $0.023 | 100 GB free, then **$0.09/GB** | — |
| [Vercel Blob](https://vercel.com/docs/vercel-blob/usage-and-pricing) | $0.023 | **$0.05/GB** | 1 GB / 10 GB transfer |

### 100-movie library (720 GB) + 1,000 clips (37.5 GB) = **758 GB**

| Vendor | Storage | Egress (11.1 TB/mo¹) | **Total** |
|---|---|---|---|
| **Cloudflare R2** | $11.37 | **$0** | **$11.37** |
| **B2 + Cloudflare (Bandwidth Alliance)** | $5.27 | **$0** | **$5.27** |
| B2 standalone | $5.27 | $88.26 | $93.53 |
| Vercel Blob | $17.43 | $550.00 | $567.43 |
| **AWS S3** | $17.43 | **$985.00** | **$1,002.43** |

¹ Assumes 100 movies re-pulled 5x/mo (3.6 TB) + 1,000 clips × 200 views × 37.5 MB (7.5 TB).

### Egress is the hidden killer

**S3 costs 88x more than R2 for the identical bytes** ($1,002 vs $11). S3's egress bill (**$985**) is **56x its own storage bill** ($17). Storage is never the problem; movement is. **Use R2** (or B2+Cloudflare if squeezing). Note this is a *per-tenant-linear* cost — 10 tenants × 100 movies = $114/mo on R2, or **$10,000/mo on S3.**

⚠️ **Storage does not scale to zero.** A 1,000-movie library is **~$110/mo on R2 whether or not anyone logs in.** This is the only meaningful always-on cost besides the Vercel seat.

---

## 8. App hosting & data (Product A)

| Vendor | Free tier | Paid | Scale-to-zero? |
|---|---|---|---|
| [**Vercel**](https://vercel.com/pricing) | Hobby: 1M reqs, 100 GB, **4 Active-CPU-hrs** (non-commercial only) | **$20/seat/mo** + [$0.128/Active-CPU-hr, $0.0106/GB-hr, $0.60/1M invocations](https://vercel.com/docs/functions/usage-and-pricing) | ❌ **$20 seat is always-on** |
| [**Neon**](https://neon.com/pricing) | 0.5 GB, 100 CU-hr | $0.106/CU-hr (Launch), $0.35/GB-mo, **no base fee** | ✅ **autosuspend, no minimum** |
| [Supabase](https://supabase.com/pricing) | 500 MB, 50k MAU | **$25/mo** Pro flat | ❌ $25 floor |
| [**Upstash Redis**](https://upstash.com/pricing) | 256 MB, **500k cmds/mo** | $0.20/100k cmds PAYG | ✅ true PAYG |
| [**Clerk**](https://clerk.com/pricing) | **50,000 MAU** | $0.02/MAU beyond | ✅ free tier covers us entirely |

Assumptions: ~600 API reqs/user/mo, ~50ms Active CPU/req, media in blob storage not Postgres.

| Line | 10 users | 100 users | 1,000 users |
|---|---|---|---|
| Vercel | $20 (seat; usage ~$0) | $20 | $20 + ~$1.70 = **$22** |
| Neon | **$0** (free tier) | ~$5.12 (1 GB storage breaks 0.5 GB free) | ~$21.55 |
| Upstash | **$0** (9k cmds) | ~$1.80 (900k cmds) | ~$18.00 |
| Clerk | **$0** | **$0** | **$0** |
| **Total** | **$20** | **~$27** | **~$62** |

**Findings:** (a) Clerk's 50k-MAU free tier covers all three scales — auth is $0 until we're 50x bigger. (b) Vercel *usage* is ~$1.70/mo even at 1,000 users; the **$20 seat, not scale, is the entire Vercel bill** — and it's a commercial-ToS requirement, not a usage cost. (c) DB **storage size**, not compute, forces the first paid upgrade. (d) **Choose Neon over Supabase**: Neon has no base fee and autosuspends → satisfies scale-to-zero; Supabase's $25 flat floor does not.

⚠️ Vercel Pro's "usage credit" amount is referenced in docs but **not quantified on any official page — UNVERIFIED.**

---

## 9. Analytics

| Vendor | Free tier | Paid |
|---|---|---|
| [**PostHog**](https://posthog.com/pricing) | **1M events/mo**, 5k replays | $0.00005/event (1–2M band) |
| [Mixpanel](https://mixpanel.com/pricing) | **1M events/mo** | $0.28/1k events |
| [Plausible](https://plausible.io/) | **none** (30-day trial) | $9–19/mo @ 10k pageviews |
| [Dub.co](https://dub.co/pricing) | **none** | **$90/mo** Business floor |
| [Bitly](https://bitly.com/pages/pricing) | 5 links/mo | $10 Core / $29 Growth / $199 Premium |
| **Self-built smart link** (Vercel Edge + Postgres) | — | **~$0–15/mo marginal** |

At ~100 events/user/mo:

| Vendor | 10 users (1k ev) | 100 (10k ev) | 1,000 (100k ev) |
|---|---|---|---|
| **PostHog** | **$0** | **$0** | **$0** |
| Mixpanel | $0 | $0 | $0 |
| Plausible | $9–19 | ~$14–39 | **UNVERIFIED** |
| Self-built links | ~$0 | ~$0–2 | ~$5–15 |

**Verdict: PostHog free + self-built smart links = $0.** PostHog's 1M-event ceiling isn't hit until ~10,000 users. Plausible is the *only* option that costs money at 10 users. Dub's $90/mo floor to track 200 clicks is absurd at our scale — a redirect + a Postgres insert on infra we already pay for is ~free.

⚠️ Dub.co's Free/Pro tiers cited by 2026 third-party sites **do not appear on the live pricing page** — treat $90 as the confirmed floor; cheaper tiers **UNVERIFIED / possibly discontinued.** Plausible's >10k-pageview tiers are unpublished — **UNVERIFIED.**

---

## 10. The bill

### Scrappy — 1 song, 20 movies, 100 posts/mo, ~20 cutscenes/mo

| Type | Line | Math | Cost |
|---|---|---|---|
| **FIXED** | Vercel Pro seat | 1 × $20 | **$20.00** |
| FIXED | Neon | free tier | $0 |
| FIXED | Upstash | free tier | $0 |
| FIXED | Clerk | 10 MAU ≪ 50k | $0 |
| FIXED | PostHog | 1k ev ≪ 1M | $0 |
| | **Fixed subtotal** | | **$20.00** |
| **VAR** | Index 20 movies (PySceneDetect) | 20 × $0.03 | $0.60 |
| VAR | Embed keyframes | 20 × $0.003 | $0.06 |
| VAR | Transcription | subtitles | $0 |
| VAR | 100 posts | 100 × ($0.006 + $0.0041) | $1.01 |
| VAR | **20 cutscenes (Veo Lite)** | 20 × $1.55 | **$31.00** |
| VAR | Storage (144 GB, R2) | 144 × $0.015 | $2.16 |
| | **Variable subtotal** | | **$34.83** |
| | **TOTAL** | | **≈ $55/mo** |

*With stock/PD footage instead: variable drops to **$3.83** → **total ≈ $24/mo.***

### Growth — 100 movies, 1,000 posts/mo, ~200 cutscenes/mo

| Type | Line | Math | Cost |
|---|---|---|---|
| **FIXED** | Vercel + Neon + Upstash + Clerk + PostHog | $20 + $5.12 + $1.80 + $0 + $0 | **$26.92** |
| **VAR** | Index 100 movies | 100 × $0.03 | $3.00 |
| VAR | Embed keyframes | 100 × $0.003 | $0.30 |
| VAR | 1,000 posts | 1,000 × $0.0101 | $10.10 |
| VAR | **200 cutscenes (Veo Lite)** | 200 × $1.55 | **$310.00** |
| VAR | Cutscene reasoning | 200 × $0.021 | $4.20 |
| VAR | Storage (758 GB, R2) | | $11.37 |
| VAR | Egress (11 TB, R2) | | **$0** |
| | **Variable subtotal** | | **$338.97** |
| | **TOTAL** | | **≈ $366/mo** |

*Veo Standard instead: **$2,410 cutscenes → total ≈ $2,466/mo.** Stock footage: **≈ $56/mo.***

### Scale — 1,000 movies, 10k posts/mo, ~2,000 cutscenes/mo

| Type | Line | Math | Cost |
|---|---|---|---|
| **FIXED** | Vercel + Neon + Upstash + Clerk + PostHog | $22 + $21.55 + $18 + $0 + $0 | **$61.55** |
| **VAR** | Index 1,000 movies | 1,000 × $0.03 | $30.00 |
| VAR | Embed keyframes | 1,000 × $0.003 | $3.00 |
| VAR | pgvector (1M vectors) | | $2.10 |
| VAR | 10,000 posts (batch+cache) | 10,000 × $0.0060 | $60.00 |
| VAR | **2,000 cutscenes (Veo Lite)** | 2,000 × $1.55 | **$3,100.00** |
| VAR | Cutscene reasoning (batch) | 2,000 × $0.011 | $22.00 |
| VAR | Storage (7.3 TB, R2) | 7,300 × $0.015 | $109.50 |
| VAR | Egress (R2) | | **$0** |
| | **Variable subtotal** | | **$3,326.60** |
| | **TOTAL** | | **≈ $3,388/mo** |

*Veo Standard instead: **$24,100 cutscenes → total ≈ $24,400/mo.** Stock footage: **≈ $350/mo.***

### Unit economics

| Metric | Scrappy | Growth | Scale |
|---|---|---|---|
| **Cost per post** (variable only) | $0.0101 | $0.0101 | $0.0060 |
| **Cost per post** (fully loaded) | $0.55 | $0.37 | $0.34 |
| **Cost per cutscene** (Veo Lite) | **$1.57** | **$1.57** | **$1.56** |
| **Cost per cutscene** (stock/PD) | **$0.03** | **$0.03** | **$0.03** |
| **Cost per cutscene** (Veo Standard) | $12.07 | $12.07 | $12.06 |
| **Cost per managed account/mo**¹ | $55 | $37 | $34 |
| Fixed as % of bill | **36%** | 7% | 2% |

¹ Assuming 1 / 10 / 100 tenant accounts respectively.

**Read:** variable unit costs are **flat** — this business has essentially no economies of scale on the marginal cutscene, because the AI-video vendor charges linearly per second. The only lever that moves the unit cost is *which footage source you pick*, not how much you buy. Fixed costs amortize nicely (36% → 2%), but they were never the problem.

---

## 11. What would actually blow up the bill

Ranked by expected damage:

1. **Defaulting to premium AI video.** Veo 3.1 Standard vs Lite at Scale: **$3,400 → $24,400/mo (7x)**. Seedance 2.0 4K: **~$90,000/mo**. One dropdown. **Hard-gate premium tiers behind a per-tenant budget cap.**
2. **Users regenerating cutscenes.** Every "try again" is a **full $1.55–$12 re-spend** — footage generation is not cacheable across prompts. 5 retries/cutscene silently 5x's the entire bill. **Cap retries; charge for them; make previews cheap (Lite) and finals expensive (Standard).**
3. **Putting media on S3 instead of R2.** 88x on the same bytes. At 10 tenants: **$114/mo → $10,000/mo.** Pure egress.
4. **Using managed video-intelligence APIs.** Google full-indexing at 1,000 movies = **$18,000** vs **$30** self-hosted (600x). A single "let's just use the API for now" decision.
5. **Embedding at 1 fps on a per-run-billed API.** Replicate at 1 fps × 1,000 movies = **$2,590** vs $360 keyframes vs **$3** self-hosted. 860x between worst and best.
6. **Sonnet 5's price increase on Sep 1, 2026** (+50%) **plus the ~30% tokenizer inflation** on Opus 4.7+/Sonnet 5 — a silent ~2x on LLM lines if modeled off old numbers. (Still small in absolute terms.)
7. **A dedicated vector DB.** $16–$512/mo of pure new spend for something pgvector does for $2 — and every one has a **monthly minimum that breaks scale-to-zero**. Qdrant at 10M vectors unquantized: **~$5,125/mo.**
8. **Transloadit importing full movies.** Billed per GB in+out → **$3.60 per render** instead of $0.004 (900x) if the job pulls the source file.
9. **Runaway render loops / no timeout.** Lambda at 3s default fails; Lambda at 900s max on a stuck job burns 10 GB × 900s repeatedly. **Set explicit timeouts and a dead-letter queue.**
10. **Per-tenant fixed costs.** If each tenant needs its own Neon project / Pinecone index / Vercel seat, the $20 floor becomes $20 × N. **Multi-tenancy must share infra by row, not by instance** (see §12).
11. **Dub.co-style SaaS floors.** $90/mo to track 200 clicks. Death by a dozen $20–90/mo subscriptions is how the *fixed* side quietly becomes $500/mo.

---

## 12. Multi-tenant: what amortizes vs what scales linearly

| Cost | Behavior | Note |
|---|---|---|
| Vercel seat ($20) | **Amortizes** | One team, N tenants — unless per-tenant seats |
| Clerk (50k MAU free) | **Amortizes** | Huge headroom; free to ~50k end users total |
| PostHog (1M events) | **Amortizes** | ~10k users before first dollar |
| Neon compute | **Mostly amortizes** | Shared instance, row-level tenant isolation |
| Postgres/vector storage | **Linear** | ~$2/mo per 1M vectors per tenant |
| **Movie/asset storage** | **Linear** | ~$110/mo per 1,000-movie library — **the always-on floor** |
| **AI footage generation** | **Strictly linear** | ~$1.55/cutscene, no volume discount found |
| Scene indexing | **Linear but trivial** | $0.03/movie |
| LLM tokens | **Linear**, batch-discountable | $0.006/post |

**Implication:** the fixed floor stays ~$20–60 no matter how many tenants — the engine genuinely is reusable. But **~93% of the bill is strictly per-tenant-linear AI footage**, which means **infra cost must be priced into per-tenant billing, not absorbed.** At $1.55/cutscene and 200 cutscenes/mo, a tenant costs ~$310/mo in footage alone. Any pricing below that loses money on every account, at any scale.

---

## 13. Scale-to-zero audit (hard requirement)

| Component | True idle floor | Verdict |
|---|---|---|
| **Vercel Pro seat** | **$20/mo** | ❌ Unavoidable (commercial ToS) — **this is the floor** |
| **R2 storage** | **~$11/mo per 100 movies; ~$110 per 1,000** | ❌ Bytes at rest cost money regardless of traffic |
| Neon | **$0** | ✅ Autosuspend, no base fee, usage-only |
| Upstash Redis | **$0** | ✅ True PAYG |
| Clerk | **$0** | ✅ Free to 50k MAU |
| PostHog | **$0** | ✅ Free to 1M events |
| Vercel Functions | **$0** | ✅ No invocations = no charge |
| Lambda / Modal / Replicate | **$0** | ✅ Per-second billing |
| Veo / Runway | **$0** | ✅ Pure per-use |
| **Pinecone / Turbopuffer / Weaviate** | **$16–$45/mo** | ❌ **Minimums — reject** |
| **Supabase Pro** | **$25/mo** | ❌ Flat floor — **prefer Neon** |
| **Dub.co** | **$90/mo** | ❌ **Reject — self-build** |
| Qdrant | RAM-resident | ❌ Reject |

**True idle floor: $20/mo (Vercel seat) + storage.** An idle Scrappy tenant costs **~$22/mo**; an idle 1,000-movie Scale library costs **~$130/mo**. Everything else genuinely goes to zero — *provided* we pick Neon over Supabase, pgvector over a vector DB, and self-built links over Dub. Those three choices are the difference between a **$20** floor and a **$150+** floor.

---

## 14. Cost-control levers, with breakevens

| Lever | Saving | Breakeven / note |
|---|---|---|
| **Stock/PD footage over AI gen** | **~50x per cutscene** ($0.03 vs $1.55) | Immediate. **Biggest lever in the document.** |
| **Veo Lite over Veo Standard** | **8x** ($1.50 vs $12/30s) | Immediate; use Lite for previews, Standard only for finals |
| **Self-host scene detection** | **600x** ($30 vs $18,000 @ 1k movies) | Breakeven at **~2 movies** ($0.06 DIY vs $36 API). Self-host from day one. |
| **Self-host CLIP over Replicate** | **~120x** ($3 vs $360 @ 1k movies) | Modal A10 $1.10/hr ÷ Replicate $0.00036/run → breakeven ≈ **3,000 frames ≈ 3 movies** |
| **Keyframes over 1 fps** | **7.2x** on embeddings | Free — scene detection already yields the shot list |
| **R2 over S3** | **88x** on the media bill | Immediate, no breakeven |
| **Subtitles over transcription** | ~$920 one-time @ 1k movies | Immediate (but see §4 pivot caveat) |
| **pgvector over Pinecone** | $2 vs $50/mo | Immediate; also preserves scale-to-zero |
| **Prompt caching** | −36% on posts | Pays back after **1 cache read** (5m write = 1.25x, read = 0.1x) |
| **Batch API** | **−50%** flat, stacks with caching | Free if latency-tolerant. Cutscene *selection* is inherently batchable overnight. |
| **Haiku 4.5 routing** | −50% vs Sonnet 5 | Use for templating/classification; keep Sonnet/Opus for scene reasoning where quality→footage-spend leverage is 70:1 |
| **Precompute + cache footage** | Up to 100% of a regeneration | A generated 30s clip costs $1.55 to make and **$0.0006/mo to store on R2**. **Never regenerate anything you could have stored — storage is ~2,600x cheaper than regeneration.** |

**The single highest-leverage line in this document:** storing a generated clip forever costs less than 0.04% of generating it once. Cache aggressively, permanently, and at every level.

---

## Known gaps

| Gap | Status |
|---|---|
| **OpenAI pricing** (GPT models, Whisper API, image gen) | **UNVERIFIED — needs check** |
| **Sora / Pika per-second pricing** | **UNVERIFIED — needs check.** Given the pivot, worth closing. |
| **Kling / Luma pricing** | Third-party only (~$0.10/s, ~$0.04/s) — **needs vendor confirmation.** Luma at ~$0.04/s would beat Veo Lite. |
| **Groq / AssemblyAI transcription** | **UNVERIFIED** — low priority (subtitles win) |
| **Licensed stock footage libraries** | **NOT PRICED** — now load-bearing post-pivot. The "$0.03/cutscene" stock figure assumes an amortized library whose acquisition cost is unmodeled. **Highest-value gap to close.** |
| **TTS / dialogue for AI-generated clips** | **NOT PRICED** — the pivot removes pre-existing subtitles (§4) |
| Vercel Pro usage credit amount | **UNVERIFIED** — not published |
| Plausible >10k pageviews | **UNVERIFIED** — not published |
| Dub.co Free/Pro tiers | **UNVERIFIED** — absent from live page |
| Qdrant $/GB-RAM-hr | **UNVERIFIED** — calculator-only |
| Mux 1080p encode rate | **UNVERIFIED** — 720p baseline only |
| Coconut.co subtitle burn-in support | **UNVERIFIED** — pricing confirmed, capability not |
| MediaConvert burn-in multiplier | **UNVERIFIED** — beyond the 2x HD multiplier |
| AWS EC2 / Azure list prices | Via Vantage mirror / Azure Retail Prices API (JS-rendered official pages) |
| PySceneDetect throughput, CLIP fps | Engineering estimates, not vendor SLAs |

---

## Sources

*All verified 2026-07-15.*

**Compute & video processing**
- [Vercel Functions usage & pricing](https://vercel.com/docs/functions/usage-and-pricing) · [Vercel Functions limitations](https://vercel.com/docs/functions/limitations) · [Vercel pricing](https://vercel.com/pricing) · [Vercel docs pricing](https://vercel.com/docs/pricing)
- [AWS Lambda pricing](https://aws.amazon.com/lambda/pricing/) · [Lambda limits](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)
- [AWS MediaConvert pricing](https://aws.amazon.com/mediaconvert/pricing/) · [Mux pricing](https://www.mux.com/pricing) · [Cloudflare Stream pricing](https://developers.cloudflare.com/stream/pricing/) · [Transloadit pricing](https://transloadit.com/pricing/) · [Coconut.co pricing](https://www.coconut.co/pricing)
- [Modal pricing](https://modal.com/pricing) · [RunPod pricing](https://www.runpod.io/pricing) · [Replicate pricing](https://replicate.com/pricing) · [Replicate clip-features](https://replicate.com/andreasjansson/clip-features) · [EC2 t3.medium (Vantage mirror)](https://instances.vantage.sh/aws/ec2/t3.medium)

**Video intelligence**
- [Google Video Intelligence pricing](https://cloud.google.com/video-intelligence/pricing) · [AWS Rekognition pricing](https://aws.amazon.com/rekognition/pricing/) · [Azure Retail Prices API](https://prices.azure.com/api/retail/prices) · [TwelveLabs pricing](https://www.twelvelabs.io/pricing) · [PySceneDetect benchmarks](https://github.com/Breakthrough/PySceneDetect/blob/main/benchmark/README.md)

**Vector & data**
- [Neon pricing](https://neon.com/pricing) · [Supabase pricing](https://supabase.com/pricing) · [Pinecone pricing](https://www.pinecone.io/pricing/) · [Turbopuffer pricing](https://turbopuffer.com/pricing) · [Turbopuffer pricing log](https://turbopuffer.com/docs/pricing-log) · [Qdrant pricing](https://qdrant.tech/pricing/) · [Weaviate pricing](https://weaviate.io/pricing) · [LanceDB](https://lancedb.com/pricing) · [Upstash pricing](https://upstash.com/pricing)

**AI models**
- [Anthropic pricing docs](https://platform.claude.com/docs/en/about-claude/pricing) · [Claude pricing](https://claude.com/pricing) · [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Runway API pricing](https://docs.dev.runwayml.com/guides/pricing/) · [Deepgram pricing](https://deepgram.com/pricing)

**Storage**
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/) · [AWS S3 pricing](https://aws.amazon.com/s3/pricing/) · [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing) · [Vercel Blob pricing](https://vercel.com/docs/vercel-blob/usage-and-pricing)

**Auth & analytics**
- [Clerk pricing](https://clerk.com/pricing) · [PostHog pricing](https://posthog.com/pricing) · [Mixpanel pricing](https://mixpanel.com/pricing) · [Plausible](https://plausible.io/) · [Dub.co pricing](https://dub.co/pricing) · [Bitly pricing](https://bitly.com/pages/pricing)
