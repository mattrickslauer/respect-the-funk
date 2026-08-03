# RemixKit — demo voice-over (target 3:00)

**Read on camera.** 478 words ≈ **3:05** at 155 wpm, 3:17 if you slow to 145 — measured, not
estimated. If you need to lose 15 seconds, cut the two sentences marked ✂ below. Cues in
brackets are for the edit, not to be read. Bold = land the stress there.

---

### 0:00 – 0:20 · COLD OPEN [ON CAM, no b-roll — let your face carry it]

Nine in ten tracks released this year will be commercially invisible.
Every label knows it. Almost none knows **why**.

We're Respect the Funk — a record label. Before we built anything, we ran the research.

### 0:20 – 0:40 · THE EVIDENCE [ON CAM → cut to plain stat cards on black]

You **cannot buy** streams. Twenty-five thousand dollars of ads returns a few hundred in
royalties. Fifty to one.

And fan videos — the thing everyone chases — are a lottery: the top ten percent of songs
take ninety-six percent.

So optimization is dead. When outcomes are lottery-shaped you don't need a better ticket.
You need **more tickets, cheaper** — with the plumbing correct, so a win actually pays.

### 0:40 – 0:52 · THE PRODUCT [SCREEN: 02-roster.png]

That's RemixKit. A label brings one artist and one song.

[SCREEN: 03-artist.png] Register the artist. Likeness consent goes on record before anything
generates — no consent, no generation. That gate is in the product, not a policy document.

### 0:52 – 1:12 · IDENTITY — WHY THE SECOND VIDEO IS CHEAP [SCREEN: 04-identity.png, slow push in]

Then you build their identity **once**. How they read on screen, reference frames across
angles and lighting, anti-drift negatives, and the consent that permits it.

✂ It's versioned, so an edit can't rewrite the provenance of kits already delivered.

Build it once, and every video after the first is cheap.

### 1:12 – 1:26 · THE SONG [SCREEN: 06-song.png]

Attach a song. It's measured **once** — tempo, hook window, sections.

Seventy-nine point nine seven BPM, and beside it the method that produced the number.
No tempo without a method behind it.

### 1:26 – 2:00 · GENERATE — THE GENBLAZE FAN-OUT [SCREEN: 07-generate.png — this is the money shot, hold it]

Then generate. Not finished ads — **templates**, designed to be copied, so a fan who sees one
knows what their own version looks like.

Here's what a kit costs before you spend it. Three shots, six calls, two dollars fifty-two.

Each clip is a **Genblaze pipeline**. Stage one settles the face — Bria's edit model, four
cents, conditioned on a reference frame. That frame becomes the first frame of a Sora 2 clip
for eighty. Two models, two vendors, chained into one run.

[SCREEN: budget ceiling field] And there's a ceiling: over it the run is refused before a
single call is submitted. A submitted step is a charged step.

### 2:00 – 2:20 · THE KIT [SCREEN: 08-kit.png, then B-ROLL: the real clips full-screen 9:16]

The kit comes back with a cost ledger, an approval gate — a label does not auto-publish AI
content about its own artists — and **provenance: verified**.

[B-ROLL: c0b5c9af.mp4 and 82745a41.mp4] These are real. Sora 2, nine-sixteen, cut to a
measured hook.

### 2:20 – 2:45 · B2 [SCREEN: 08-kit provenance panel → 09-verify.png]

Backblaze B2 does three jobs here, and it's why this system has **no database**.

It's the upload path — masters go browser straight to bucket, never touching our server.

It's the catalog: every run writes under tenant, date, run ID. ✂ Eleven services became five;
idle cost, under a dollar a month.

And it's the **receipt**. Every asset lands beside a manifest naming the provider, model and
prompt behind every step — verified before a kit is allowed to ship.

[SCREEN: 09-verify.png, drag a file in] Download an asset and that manifest travels inside
the file, while the stored object stays byte-identical — so the hash still means something.
Drop it into verify and it says exactly how it was made.

Nothing is looked up. The proof is in the bytes.

### 2:45 – 3:00 · CLOSE [ON CAM]

You cannot buy a hit. You can only lower the cost of trying — and be honest about what
came out.

Bring us one artist and one song.

---

## What changed from your draft, and why

1. **Genblaze is now named and shown.** It was absent from the original — and it's one of the
   four equally-weighted judging criteria ("Use of Genblaze"). The 1:26 block now shows the
   two-stage, two-vendor chain on screen while you say it.
2. **Cut the Athena/Parquet claim.** Your draft said the index "lands as Parquet in the same
   bucket, queryable by Athena." That's in `infra/diagram.py` only — there is no Parquet or
   Athena code in the shipped app. A judge who greps the repo finds nothing. B2's jobs went
   from four to three and the catalog line now claims only what's true.
3. **Reframed away from AWS.** "AWS handles the logic, B2 holds the bytes" led with the wrong
   sponsor. B2 is now the architectural protagonist — the reason there's no database.
4. **Demo starts at 0:40 instead of ~1:00.** Devpost asks for a video that "shows the Project
   functioning." The research is a real differentiator for Real-World Utility, so it stays —
   compressed from five stats to two.
5. **Added the production-readiness beats** you already built and weren't claiming: consent
   gate, budget ceiling that refuses before submitting, versioned identity, approval gate,
   cost ledger. That's a whole judging criterion sitting unspoken in your draft.

## Facts I verified against the code

| Claim | Status |
|---|---|
| Consent gate blocks generation | ✅ roster shows "No likeness consent — generation blocked" |
| 79.97 BPM + method string | ✅ rendered on the generate page |
| Two-stage chain, $0.04 + $0.80, 3 shots / 6 calls | ✅ `07-generate.png`, B2 mode |
| `manifest.verify()` gates a kit to `ready` | ✅ `adapters/generator_genblaze.py` |
| Manifest embedded on download, stored object byte-identical | ✅ `services/delivery.py` |
| `/verify` reads provenance out of the bytes, no lookup | ✅ `services/verify.py` |
| Budget ceiling refuses before submitting | ✅ `RK_MAX_RUN_CENTS` |
| Eleven services → five; under $1/month idle | ✅ `infra/README.md` |
| Presigned browser→B2 upload | ✅ `/ui/songs/{id}/master/upload-url` |
| **Parquet / Athena catalog** | ❌ **diagram only — cut from script** |

## Models to list on the Devpost form

Actually used in the runs shown: **OpenAI `sora-2`** (video), **Bria `bria-fibo-edit`** via GMI
Cloud (image, identity-lock), **ElevenLabs Scribe** (lyric transcription).
Also wired: `seedance-2-0-260128`, `seedream-5.0-lite`, `gpt-image-1`, `veo-3.0-generate-001`,
`imagen-4.0-generate-001`, `eleven_multilingual_v2`, `gpt-4o-mini-tts`.

## Shot list

| Cue | File |
|---|---|
| 0:40 roster | `screenshots/02-roster.png` |
| 0:45 artist + consent | `screenshots/03-artist.png` |
| 0:52 identity | `screenshots/04-identity.png` |
| 1:12 song / tempo | `screenshots/06-song.png` |
| 1:26 generate ← hold | `screenshots/07-generate.png` |
| 2:00 kit + provenance | `screenshots/08-kit.png` |
| 2:20 verify | `screenshots/09-verify.png` |
| B-roll clips | `broll/*.mp4` — 720×1280, 8s, real Sora 2 |
| B-roll stills | `broll/*.png` — real `bria-fibo-edit` output |

**Before you record the screen:** run against B2, not local storage
(`RK_STORAGE_BACKEND=b2`). On local storage the generate page refuses face conditioning and
the chain collapses to one call — the whole Genblaze beat disappears. Screenshots 02, 03, 04,
06 and 07 here are already B2; 01, 05, 08, 09, 10 are local, so their footer reads
`storage local`. Recapture those five on B2 if you want the footer consistent on screen.
