# The voice-over, line by line, against the code

Every factual claim in `demo-voiceover.txt`, checked against the repository rather than
against memory or against `SCRIPT.md`. Verdicts are one of:

- ✅ **true** — the code does this, cite included
- ⚠️ **overclaimed** — something real is behind it, but the sentence says more than the code does
- ❌ **false** — no implementation; the claim is a plan
- 🫥 **empty** — not false, but carries no information a listener can use

`demo-voiceover-v2.txt` is what this audit produced.

---

## The on-camera half

| Line | Verdict | Evidence |
|---|---|---|
| "Nine in ten tracks released this year will be commercially invisible" | ⚠️ overclaimed | The nearest sourced fact is *"the long tail is where ~90% of songs live"* — and `13-ugc-adoption.md:300` marks it **unmeasured**. "Commercially invisible" is undefined. There is a much harder number available: Spotify pays **nothing at all** below 1,000 streams in 12 months ([`04-spotify-economics.md:238`](research/04-spotify-economics.md), Spotify's own support docs). v2 uses the threshold, because it is a rule rather than an estimate. |
| "Five of six core questions came back brutal" | 🫥 empty | There is no set of six questions anywhere in `docs/research/`. There are **thirteen pillars**. The number is unverifiable and the word "brutal" is a mood, not a finding. Cut. |
| "$25,000 of ads returns a few hundred in royalties. Fifty to one." | ✅ true | `02-meta-ads.md:182` puts the planning midpoint for 100k streams at **$20–25k**; `SYNTHESIS.md:9` states the **~50x loss**. $25,000 ÷ 50 = $500, so "a few hundred" is right. |
| "Facebook ran 663 trials and still cannot say an ad caused a stream" | ✅ true | Gordon, Moakler & Zettelmeyer (2023), *Marketing Science* 42(4) — **663 large-scale Facebook experiments**, 5,000+ user-level features, "unable to reliably estimate an ad campaign's causal effect." `03-audience-models.md:215`, `STRATEGY-PIVOT.md:24`. |
| "the top ten percent of songs take ninety-six percent" | ✅ true | [arXiv 2405.14999](https://arxiv.org/abs/2405.14999), the strongest design found — top 10% of songs = **96% of TikTok creations**. `13-ugc-adoption.md:290`. |
| "optimization is dead… more tickets, cheaper" | ✅ true | This is `SYNTHESIS.md:9`'s own conclusion, near-verbatim: *"this is a portfolio business, not an optimization business."* |

## The generated half

| Line | Verdict | Evidence |
|---|---|---|
| "likeness consent on record" | ⚠️ overclaimed | Understates it. Consent is not a record, it is a **gate** — the roster renders "No likeness consent — generation blocked". v2 says so. |
| "measured once: tempo, drop, hook windows" | ✅ true | Song carries `bpm`, `hook`, `sections`, all measured. |
| "no tempo without a method behind it" | ✅ true | **Enforced**, not aspirational: `services/songs.py:104` refuses a `bpm` that arrives without a non-empty `bpm_method`. `models.py:1001` records why. |
| the four named formats | ✅ true | Five builtins ship: `backdrop-loop`, `direct-address`, `performance`, `portrait-still`, `voice-over` — `services/recipes.py:88–195`. |
| "a format is a row **a label can edit**" | ⚠️ overclaimed | `Recipe` is genuinely a document, and `RecipeService` has `save()` and `delete()` — but **no route exposes them**. `grep` for a recipe POST/PUT/PATCH in `ui/routes.py` and `api/v1.py` returns nothing; the console only lists and selects. A label cannot edit a format in the product today. The *true* claim is the one the model's own docstring makes: *"The shape of the asset was not a setting; it was a commit… Adding a format is adding a row."* v2 uses that. |
| "AWS handles the logic. B2 holds the bytes" | ✅ true | Matches `infra/README.md`. |
| "this system has no database" | ✅ true | Documents in a bucket; `infra/README.md:44` derives the idle-cost claim from exactly this. |
| "Eleven services became five" | ✅ true | `infra/README.md:68`, verbatim. |
| "Idle cost: under a dollar a month" | ✅ true | `infra/README.md:44` — "Realistic idle cost: under $1/month". Survives the memory branch (`:60`). |
| "masters go browser straight to bucket, never touching our server" | ✅ true | `ui/routes.py:1343` `POST /ui/songs/{id}/master/upload-url` issues a presigned PUT; `routes.py:2092` notes the server route "is never reached" on B2. |
| **"the index landing as Parquet… queryable by Athena. No warehouse."** | ❌ **false** | **There is no Parquet and no Athena anywhere in `app/`.** Every hit is in `infra/diagram.py` (a drawing), `infra/README.md` and `BUILD-SPEC.md` (a plan). The run prefix *is* real and *is* the index — `runs/{tenant}/{date}/{run_id}/` — so v2 claims the prefix and drops the warehouse that does not exist. **This is the one outright false sentence in the script.** |
| "a manifest naming the provider, model, and prompt behind every step" | ✅ true | Written per run; `08-kit.png` shows it. |
| "verified before a kit ships. Unverified fails." | ✅ true | `adapters/generator_genblaze.py:18` — *"The manifest is verified before a kit is allowed to be `ready`. `manifest.verify()` is the gate, not a decoration"*; the call is at `:1046`. |
| "the manifest is embedded inside the file… stored object stays byte-identical" | ✅ true | `services/delivery.py` docstring states both, and why the seam is at download. ⚠️ One nuance: a media type with **no embedding handler** (an MP3 without `mutagen`) is delivered unmodified and the caller is told — so "every asset" would be too strong. v2 says "download a clip". |
| "Drop it into verify and it says exactly how it was made. Nothing is looked up." | ✅ true | `services/verify.py` reads the manifest **out of the bytes**; "nothing has to be looked up" is the docstring's own phrasing. |

## Length, and where the 30 seconds are if you need them

Measured against the v1 read, which delivered 329 words in exactly 120.0s — **164 wpm**,
not the 155 `SCRIPT.md` estimated.

| | intro (on camera) | generated | total |
|---|---|---|---|
| v1 | 128 w · 0:47 | 329 w · 2:00 | **457 w · 2:47** |
| v2 | 147 w · 0:54 | 432 w · 2:38 | **579 w · 3:31** |

v2 is longer because the cuts were small and the additions are real: the consent gate, the
two-vendor chain, the budget ceiling. If there is a three-minute cap, these four are the
ones to lose — kept out of the script file itself so what remains is still readable
straight down. Removing all four lands at **3:03**.

1. ✂ **"Not a little — nothing."** (4 w) — rhetorical reinforcement; the number does the work.
2. ✂ **"You cannot measure it. Facebook ran six hundred sixty-three trials on its own data and still could not say an ad caused a stream."** (23 w) — the hardest stat in the deck, but the *"optimization is dead"* conclusion survives on the other two.
3. ✂ **"It's versioned, so an edit can't rewrite kits already delivered."** (11 w) — the identity screen shows this on its own.
4. ✂ **"The prefix is the index — there's no second copy to disagree with it."** (14 w) — keeps the true catalog claim, drops the gloss on it.

Do **not** cut the two-vendor sentence to save time. "Use of Genblaze" is a scored
criterion and that sentence is the only place the chain is stated.

## Meaningful things the script never said

All verified, all absent from v1. v2 spends the space freed by the cuts on these.

| Thing | Evidence |
|---|---|
| **The budget ceiling refuses before spending** | `RK_MAX_RUN_CENTS`. Over the ceiling the run is refused *before a single call is submitted* — and a submitted step is a charged step. This is the strongest production-readiness beat in the product and it was not in the script. |
| **Two models, two vendors, one run** | Stage one settles the face with `bria-fibo-edit` (4¢); that frame becomes the first frame of a `sora-2` clip (80¢). Visible on `07-generate.png`. Also the "use of Genblaze" judging criterion. |
| **The price is shown before you buy** | The generate page prices the exact plan the button submits — 3 shots, 6 calls, $2.52. |
| **Identity is versioned** | Kits record the identity version they were built from, so an edit cannot rewrite the provenance of kits already delivered (`04-identity.png`). |
| **The delivered clip carries the master** | `services/delivery.py`: what leaves is the **scored** cut — the song's own audio under the picture, not whatever the model composed. |
| **Nothing regenerates twice** | Generation is content-addressed; an identical prompt + model + params reuses the cached object instead of paying again (`.gitignore` preamble, BUILD-SPEC §2b rule 5). This *is* the cost-per-attempt argument, made concrete. |
