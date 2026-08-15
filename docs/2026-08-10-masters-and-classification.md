---
title: "Masters on the spine, and classifying a record instead of guessing at it"
subtitle: "Why counterparty discovery for a new artist is cold-start blocked, what was built today, the two approaches that failed validation, and the design for storing masters once so that analysis, delivery, radio servicing and RemixKit all read the same file."
status: "BUILT, not fully deployed. §1–§3 are measured findings; §3a's conclusion is now confirmed against six reference tracks. §4 is built and the bucket is applied. The classifier image passes validation locally and has not been pushed to ECR."
date: "2026-08-10"
---

## 0. Why this document exists

Counterparty discovery for Hallow Youth returns nothing, and the reason is not tuning.
Chasing it produced a chain of findings that ends at a missing capability — the platform
has nowhere to put a master — and that capability turns out to be load-bearing for far
more than discovery.

Everything in §1–§3 was measured against live services on 2026-08-10. Everything from §4
is design.

---

## 1. The cold start, measured

`GET https://api.deezer.com/artist/395684471/related` returns an **empty array**. Deezer's
relatedness is collaborative filtering over listening data, and Hallow Youth has **0 fans
and 2 albums**, so it has no neighbours. Deezer's own `bpm` field reads **0** for both
tracks.

`sources.deezer.discover_counterparties` searches playlists by *style terms*, and falls
back to the artist's name when it has none. That fallback is now deleted (`77722c6`): of
the eighteen counterparties this harvest put on the live cluster, **thirteen were people
called Amanda**, matched against the roster artist "Amanda Kurt". A fallback that fires
exactly when the system knows least is not a safety net.

Which leaves style terms as the only working input, and the roster had **two** of the six
the agent accepts — `Dance` and `Electro` — both `inferred` from Deezer's top-level genre
labels. Deezer has nothing more specific: both albums carry the same two.

**So the record has to be listened to.** That is the whole reason the rest of this exists.

## 2. What was built, and what it can and cannot say

`spindle/audio.py` (`92a33cc`) measures a Deezer 30-second preview: tempo by the same
spectral-flux comb `content/bin/measure_beat.py` uses, plus spectral centroid. BPM and
centroid are `measured`; style terms derived from tempo bands are `inferred`, per
`SCOPE-RESET §2a` rule 1.

Two defects were found by its own tests and fixed:

- **Confidence was a z-score, which cannot discriminate.** The maximum of several hundred
  comb candidates sits three-plus standard deviations above their mean for *any* input, so
  white noise scored 1.0 and the abstention floor never engaged. Now a ratio to the mean:
  measured, noise **1.80** against a click train's **16.7–19.0**.
- **A clean 140 BPM train measured 70.** A comb at half tempo lands on every other
  transient and scored **5.297** against the true grid's **5.282** — a tie by construction.
  Near-exact octave ties now resolve upward.

Live results: `Losing Sleep` measures **125 BPM** unambiguously → `house, melodic house,
progressive house`. `Denial (Radio Edit)` measures **84.0 with a rival at 126.0** — the
dotted-quarter alias — and **abstains**, because 84 says trip hop and 126 says techno and
emitting either is a coin flip wearing a measurement's clothes.

**The limit is the preview, not the method.** Thirty seconds, usually lifted from the
middle. Tempo survives that; an energy curve, a section map and a drop location do not,
because most of the arrangement is not in the file. `SCOPE-RESET §2` lists all of those as
things derived once per track, and none of them is derivable from what we currently hold.

## 3. Two routes to genre classification, both tested, one dead

`essentia.upf.edu` publishes Discogs-trained classifiers. The relevant facts, checked:

| | |
|---|---|
| `discogs-effnet-bsdynamic-1.onnx` | 18 MB. Input `[n,128,96]` mel spectrogram, **outputs `[n,400]` Discogs styles directly** plus 1280-d embeddings. No separate head needed. |
| `genre_discogs400-discogs-effnet-1` | `.pb` and `.json` only — **no ONNX**. |
| `genre_discogs400-discogs-maest-*` | ONNX exists, but it is a *head*: input is `[batch,1685,96]` embeddings from a MAEST extractor that is itself `.pb`. |
| `essentia-tensorflow` on Python 3.14 | **No matching distribution.** The platform venv cannot install it. |
| `onnxruntime` on Python 3.14 | Available (1.28.0). |

### 3a. The lightweight route failed validation — do not retry it without a reference

The attractive path was: effnet ONNX + `onnxruntime` + a mel spectrogram computed in
numpy, no TensorFlow, no container, testable locally. The mel front end was implemented
(Slaney mel, 96 bands, 0–8000 Hz, `log10(1 + 10000·mel)`, 128-frame patches) and validated
against reference tracks with unambiguous genres.

**It failed.** Everything classified as Electronic:

| Track | Expected | Top prediction |
|---|---|---|
| Daft Punk — Around The World | house | `Electronic---New Beat` 0.267 ✅ |
| Metallica — Master of Puppets | metal | `Electronic---Hardcore` 0.129 ❌ |
| John Coltrane — Giant Steps | jazz | `Electronic---Speedcore` 0.147 ❌ |
| Bob Marley — Jamming | reggae | `Electronic---Electro` 0.130 ❌ |

The Daft Punk result is a coincidence, not a pass — *every* input collapsed to Electronic.
That is the signature of a mel front end that does not match what the network was trained
on, and the model keeps emitting confident labels regardless. **This is the exact failure
class this codebase exists to prevent**, and it is why the reference tracks were run before
anything was wired.

Essentia's preprocessing has several conventions that must all match — `unit_tri` band
normalisation, window convention, magnitude versus power, framing and centring. Tuning
them by trial against four reference tracks would produce something that passes four
tests and is still wrong.

**Conclusion: use Essentia's own preprocessing.** `TensorflowPredictEffnetDiscogs` is the
correct front end because it is the one the weights were trained against. That forces
`essentia-tensorflow`, which forces Python ≤3.11, which forces a container — and the
container was needed for AWS anyway, so nothing is lost.

**Confirmed.** With Essentia's front end and the *same weights*, run inside the AWS
Lambda base image against six records spanning six Discogs parent genres:

| Track | Expected | Top prediction | p |
|---|---|---|---|
| Daft Punk — Around The World | Electronic | `Electronic---House` | 0.433 ✅ |
| Metallica — Master of Puppets | Rock | `Rock---Heavy Metal` | 0.844 ✅ |
| John Coltrane — Giant Steps | Jazz | `Jazz---Hard Bop` | 0.621 ✅ |
| Bob Marley — Jamming | Reggae | `Reggae---Roots Reggae` | 0.790 ✅ |
| Dr. Dre — Still D.R.E. | Hip Hop | `Hip Hop---Horrorcore` | 0.215 ✅ parent only |
| Johnny Cash — Folsom Prison Blues | Folk, World, & Country | `…---Country` | 0.609 ✅ |

Six of six on the parent genre, and the sub-genres are specific enough to be useful —
Giant Steps really is hard bop. **The model was never the problem.** The diagnosis in
this section was right, and the cost of having tested it before wiring anything was one
afternoon against a classifier that would otherwise have been confidently wrong forever.

The Dr. Dre row is the one to keep. Still D.R.E. is Hip Hop and is not Horrorcore: the
model is reliably better at the genre than at the style within it, so `handler`
reports the style only above a confidence floor and the parent alone below it. That is a
third state — `none` / `parent` / `style` — rather than a single answer that is partly
wrong.

### 3b. On the runtime: a container-image Lambda, not a spot instance

The workload is **seconds of CPU per track, sporadically, triggered by a lead**. Against
that:

- A **spot instance** idles between leads. The submission's scored production-readiness
  claim is *"scales to zero, `$0` idle"* — measured, and true today. Standing up an
  always-on instance to classify a handful of tracks contradicts a claim we make on
  camera, and adds interruption handling for a workload that never needed it.
- **AWS Batch** scales to zero but brings a control plane, job queues and compute
  environments — a lot of Terraform for a few invocations a week.
- A **container-image Lambda** takes images up to 10 GB (essentia-tensorflow plus an
  18 MB model is nowhere near it), runs to 15 minutes, scales to zero, and is *already*
  how `aws_lambda_function.console` is deployed. No new deployment concept.

Spot would be right for bulk-classifying a hundred thousand tracks. For this roster it is
the wrong tool and it costs a scored claim.

The fleet makes this easy: a worker drains `--kinds analyse_recording` and claims leases
on the same table as every other agent. `requirements-worker.txt` already exists (`92a33cc`)
to keep numpy out of the web bundle for exactly this reason.

---

## 4. The actual gap: the spine has nowhere to put a master

**Proposal from here. Nothing below is built.**

`recording` carries `title`, `isrc`, `duration_ms`, `released_on` — and no audio. There is
no storage port, no bucket, no object key anywhere in `platform/`. Every measurement above
reads a Deezer preview because that is the only audio the platform can reach.

`app/` already solved this for RemixKit: `ports/storage.py` is a Protocol, with
`adapters/storage_b2.py` (Backblaze via its S3 API) and `storage_local.py` behind it, and
uploads go **browser↔bucket directly through a presigned URL** — the function never moves
bytes. That pattern should be reused rather than reinvented; masters are 50–100 MB and a
Lambda should not be in their path.

### 4a. Why this is worth doing properly rather than adding a column

A master is not one file and is read by more than one consumer:

| Consumer | Wants |
|---|---|
| Genre classification | any full-length render |
| Tempo, key, sections, hook window | the master, full length — `SCOPE-RESET §2`'s list |
| Radio servicing | broadcast-quality, often a specific edit |
| Sync licensing | the master, and often an instrumental |
| RemixKit | master plus stems |
| Distributor delivery | a specified format per distributor |

So: a `recording_asset` table, not a column. Sketch, deliberately not a migration yet:

```
recording_asset
  id, tenant_id, recording_id
  kind            master | instrumental | stem | radio_edit | preview
  bucket          -- S3 only. B2 stays in `app/`; see below.
  object_key      -- opaque to the platform
  content_hash    -- idempotent upload and integrity, as statement_import already does
  mime, bytes, sample_rate, channels, duration_ms
  provenance      asserted   -- a human uploaded this and said what it is
  uploaded_by, uploaded_at
```

Two constraints worth fixing now rather than retrofitting, in the spirit of
`SCOPE-RESET §1` on decisions that cannot be retrofitted:

1. **`content_hash` is unique per tenant per kind.** Re-uploading the same file is
   ordinary; storing it twice and analysing it twice is not.
2. **Every derived fact names the asset it came from.** `audio.measure` already returns
   `"basis": "deezer_preview_30s"` precisely so a measurement can say what it listened to.
   With assets that becomes an id, and a BPM measured from a preview stops being
   indistinguishable from one measured off the master.

### 4b. S3, and a second qualifying AWS service

`app/` uses B2 because the Backblaze track required it. **That is a fact about `app/`'s
history, not a pattern to inherit** — the platform runs on AWS and stores masters in
**S3**. What carries across from `app/` is the *shape*: a `ports.Storage` Protocol with the
adapter behind it, and presigned URLs so bytes never pass through a function. The
provider does not.

This also earns something. **Amazon S3 is on the CockroachDB × AWS hackathon's list of
qualifying services**, and the submission currently qualifies on Lambda alone. Masters in
S3 make it two, without inventing anything for the sake of it.

Presigned `PUT` for upload and presigned `GET` for the worker, so neither the console
Lambda nor the classifier Lambda ever proxies a master.

### 4c. Sequence

1. `recording_asset` migration and the S3 bucket in Terraform, with lifecycle rules.
2. Presigned upload from the console, and a masters panel on the recording inspector.
3. `analyse_recording` reads the asset rather than a Deezer preview, and its facts name
   the asset.
4. The classifier container, per §3b, with reference tracks in its test suite — a genre
   classifier without them is unfalsifiable, which §3a is the proof of.
5. Only then, the full `SCOPE-RESET §2` analysis: key, sections, hook window, energy
   curve. All of it needs the whole file.

---

## 6. What was built, and the two things §4 got wrong

Added after §4 was implemented, so the record shows what survived contact.

**Built** (`4ace501`, `7b9371e`, and the commit carrying this section):

| | |
|---|---|
| Migration 016 | `recording_asset`, applied and verified against the live cluster — all thirteen constraints refuse what they claim to |
| Migration 017 | `analyse_recording` and `distil_lesson` in `agent_manifest` |
| `storage.py` | the `Storage` Protocol and the S3 adapter, presigned both ways |
| `assets.py` | claim / confirm / current / delete, and `queue_analysis` |
| `routes.py` | three masters routes, and the uploader in `_inspector.html` |
| `platform/infra` | bucket, CORS, versioning, lifecycle, IAM — **written, not applied** |
| `agents.analyse_recording` | downloads the master, verifies its hash, measures it, writes facts with `fact_basis` edges |

**Two things §4 got wrong**, both found by tests rather than by review:

1. **`assets.delete`'s reference count was dead code.** It counted rows sharing
   `(kind, content_hash)` — which `asset_one_row_per_file` already makes unique, so it
   could only ever count zero. It reads like a safety check and was not one. It counts
   `object_key` now, which has no unique index and which a backfill or an importer
   adopting existing objects genuinely can point two rows at. Caught by the test that
   tried to construct the state it guarded and could not.
2. **§4a's "every derived fact names the asset" needed no new column and no new
   plumbing, but it did need somebody to actually run it.** `fact_basis` had zero rows
   on this cluster before `analyse_recording`; the design asserted the table was
   sufficient without exercising it. It is, and it renders in the console's provenance
   walk with no template change — but that was a claim, not a finding, until the test
   above.

**The classifier is built** (`platform/classifier/`): the image, the Lambda and the ECR
repository in Terraform, and the reference-track suite that §3a says is not optional. It
passes 7/7 locally — six genres plus the two floors, silence refused and a too-short clip
raising. What has not happened is `./push.sh` and the second `terraform apply`; until
then `PLATFORM_CLASSIFIER_FUNCTION` is unset and `analyse_recording` writes a tempo and
**no genre at all**, saying so in its run summary rather than promoting tempo-derived
style terms into the genre dimension.

Three things the build itself taught, none of which were in this design:

1. **numpy had to be pinned to 1.26.4**, and the reason is not the reason anyone would
   guess. The AWS Lambda `python:3.11` base image is Amazon Linux 2 — glibc **2.26** —
   and numpy ≥2.1 ships `manylinux_2_28` wheels only, so pip falls through to the sdist
   and dies on `Unknown compiler(s)`. Resolving unpinned in a Debian container gives
   2.4.6 and works perfectly. The same requirements file fails on the target. *Validate
   in the runtime image, not a convenient one* — this is the same class of mistake as
   §3a, one layer down.
2. **The classifier is x86_64 while the console is arm64.** `essentia-tensorflow`
   publishes no aarch64 wheel, and forcing Graviton would mean compiling Essentia and
   TensorFlow from source for a function that runs seconds per month.
3. **A skipped test reads as a pass.** The two floor tests generated their own fixtures
   with ffmpeg, which is deliberately absent from the runtime image, so they skipped
   silently — leaving the abstention floor untested in a run that reported OK. The
   fixtures are made on the host now and the tests fail rather than skip if they are
   missing.

---

## 5. What is true today

- Discovery refuses rather than searching by name (`77722c6`), so the Amanda failure
  cannot recur — and until style terms exist for an artist, it produces nothing at all.
  **That is the current state for Hallow Youth: `find_counterparties` will refuse.**
- `audio.py` can supply those terms from a preview for unambiguous tempi, and abstains
  otherwise. **It is now wired to an agent** — `analyse_recording`, which reads the
  master rather than the preview and labels every fact with the asset it listened to.
  Nothing has run it against a real record, because no master has been uploaded: the
  bucket is written in Terraform and not applied.
- The memory loop closed separately (`fde8688`): a thread reaching a terminal state queues
  a `distil_lesson` lead, and the lesson commits with the run record and the lead. `lesson`
  is still **0 rows** on the cluster because no thread has been worked end to end.
- 270 tests, green against the live cluster.
