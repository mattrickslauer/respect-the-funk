# PIPELINE-SPEC — previewing a run, and the nodes it is assembled from

Companion to BUILD-SPEC (how it is built), PRODUCT.md (what it is for), and MEMORY-SPEC
(what an identity is). This one answers two questions that the 2026-07-31 "Un Poquito Más"
kit made urgent:

1. **How do I see what I am about to buy, before I buy it?**
2. **How do I stop every kit being a stock clip?**

Both have the same root. Today a kit is assembled from four hardcoded mood strings and an
identity that never reaches the provider. There is one lever (`video_count`) and no way to
inspect the result of pulling it. This spec describes the preview plane — the part now
built — and the node model that replaces the hardcoded plan.

---

## 0. What went wrong, because the design follows from it

The kit for *Un Poquito Más* came back as a night-drive clip with a lofi instrumental the
model composed itself. Reconstructed, the prompt that bought it was:

```
Vertical 9:16 loop, moody night drive, city lights streaking past, shallow
depth of field. Templatable backdrop for a fan video, clean centre frame
left empty for a subject. [night-drive]
```

Three distinct failures, and it is worth keeping them separate because they have three
different fixes:

| # | Failure | Fix | Status |
|---|---------|-----|--------|
| 1 | The prompt named no song, artist, tempo, or language. `MOODS` is four fixed strings shared by every artist in the catalogue. | Song-aware defaults + **editable prompts** | built |
| 2 | Nothing told the model not to generate audio. Veo, Sora 2 and Seedance all return a native audio track. | Negatives + prompt clause; deterministic mux-out on delivery | partly built (§4) |
| 3 | `Identity.reference_frames` — Amanda Kurt's face — is uploadable, storable, rendered in the console, and **read by nothing**. `_compose_prompt` is text-only. | Character plates as image conditioning (§3) | built |

The fourth failure is the one that made the other three expensive: **the screen showed a
90-character truncation of the mood fragment, not the composed prompt.** There was no
surface anywhere in the product on which the actual wire payload could be read. A defect
you cannot see is a defect you buy repeatedly.

---

## 1. The preview plane (built)

`ports.generator.PlannedShot` is the unit. It is a shot resolved all the way to what the
provider would receive — model, composed prompt, negatives, snapped duration, params,
price — with no socket opened and nothing charged.

The load-bearing property is that there is **one resolution path**.
`GenblazeGenerator._resolve()` produces `PlannedShot`s; `plan()` shows them and
`generate()` submits them. Not two implementations that agree today — one implementation.
This is the same argument `services.kits.estimate_cents` already makes about price,
extended from the number to the payload the number is for.

```
brief  ──▶ default_shot_plan ──▶ [ShotSpec]  ──▶ apply_overrides ──▶ [ShotSpec]
                                                                        │
                                                        _resolve() ─────┤
                                                                        │
                                    plan()  ◀── [PlannedShot] ──▶  generate()
                                   (free)                          (charged)
```

`Kit.brief["shot_overrides"]` carries the edits by position, as a **list** — `Kit.brief`
is persisted as JSON and JSON has no integer keys, so a `{0: …}` map written at request
time returns as `{"0": …}` and silently matches nothing.

### Free preview tiers

| Tier | Cost | Fidelity | Status |
|------|------|----------|--------|
| 0 — Plan | zero | exact payload, exact price, exact model | **built** |
| 1 — Mock render | zero | real files, real manifest, synthetic pixels (`RK_GENERATOR_BACKEND=mock`) | exists globally; wants a per-kit toggle |
| 2 — Proxy frame | ~$0.04 vs ~$0.61 | one still image per video shot | designed (§5) |
| 3 — Step cache | zero on repeat | identical | available, unwired (§6) |

Tier 0 alone would have caught the *Un Poquito Más* kit: the composed prompt is on the
screen now, and it visibly names no song.

---

## 2. Nodes — the model

A node is **a reusable, precomputed input to a prompt**. It is saved once, processed once,
and attached to as many shots as you like. This is the generalisation of what `Identity`
already is for one artist's face, and the reason the second kit for an artist should be
cheap: the expensive part is deciding how something reads on screen, and that decision
should be made once and then referenced.

```
NodeKind
├── character   a person — face, build, the way they carry themselves   (Amanda Kurt)
├── wardrobe    a look, separable from the person wearing it
├── location    a place, real or invented
├── motion      how the camera behaves — a dolly, a locked-off, a handheld drift
└── palette     grade and light — what the footage is graded to
```

Every node carries the same four things, and the shape is deliberately the shape
`Identity` already has, because `Identity` is a character node that predates the word:

```python
class Node(Base):
    id: str
    kind: NodeKind
    name: str                              # "Amanda Kurt", "the Wynwood rooftop"
    version: int = 1                       # bumped, never edited in place

    # --- what a person put in -------------------------------------------------
    sources: list[SourceRef]               # uploaded stills, clips, or text
    notes: str | None                      # what a person wants held invariant

    # --- what preprocessing produced (§3) -------------------------------------
    fragment: str | None                   # the text this node contributes
    negatives: list[str]                   # what it must never look like
    plates: list[Plate]                    # canonical conditioning images
    digest: NodeDigest | None              # how the plates were derived, and from what

    approval: ApprovalState = DRAFT
```

Three rules, all inherited from the existing model rather than invented:

- **Versioned, not edited.** `IdentityService.save` already bumps a version rather than
  mutating; a kit records `identity_version` in its brief. A delivered asset's record must
  not move under it, so a node that changes is a new version and old kits keep pointing at
  the old one.
- **Provenance or nothing.** `NodeDigest` records which sources produced which plate, with
  what method and what hashes — the same discipline `SongAnalysis` and `SongLyrics` carry.
  A plate whose origin is unrecorded is a plate nobody can check.
- **Consent is a gate, not a field.** A character node inherits
  `LikenessConsent.blocks_generation`. This is already enforced in `services.kits`; nodes
  do not get a second, weaker path to the same providers.

### Attachment

Shots reference nodes; they do not embed them.

```python
@dataclass
class ShotSpec:
    ...
    node_ids: list[str] = field(default_factory=list)
```

At resolution time `_resolve()` does two things with them, and the split is the whole
design:

- **Text nodes** (`wardrobe`, `motion`, `palette`) contribute their `fragment` to the
  composed prompt and their `negatives` to the negative prompt. Cheap, no extra step.
- **Visual nodes** (`character`, `location`) contribute their `plates` as *image inputs*.
  Prompt text describing a face is a lossy encoding of a face; an image of the face is not.

---

## 3. Precompute — the Amanda Kurt case

> *"Amanda Kurt can have just her face where we can collect many many face shots and do
> preprocessing to make the video generation more effective."*

This is exactly right, and the reason it works is worth being precise about: **you are not
fine-tuning a model, you are manufacturing a conditioning image.** The output of
preprocessing is not weights. It is a small set of canonical plates plus the record of how
they were made.

```
  many uploads                 preprocessing                    plates
  ┌──────────────┐                                     ┌────────────────────┐
  │ 40 face shots│──▶ dedupe by perceptual hash ──▶    │ neutral / front    │
  │ mixed light  │    group by lighting setup          │ neutral / 3-4      │
  │ mixed crop   │    reject blur, occlusion, crops    │ warm / front       │
  │ mixed source │    pick the sharpest per group      │ hard-flash / front │
  └──────────────┘    normalise crop + resolution      └────────────────────┘
                      hash and store                      4–6 images, forever
```

Why "many many" shots help even though only a handful survive: **variety across lighting
is the thing that makes a plate generalise.** A single reference forces every generated
frame toward that one exposure. `ReferenceFrame.lighting` already exists on the model for
this reason — MEMORY-SPEC's argument for why the second video is cheap is that the identity
was built across several setups *once*. The upload set is the raw material; the plate set
is the product.

None of the selection steps above require a model. Perceptual hashing, blur detection
(variance of Laplacian), and face-box crop normalisation are ordinary image processing —
which means **preprocessing a character node costs nothing**, and can run on upload.

### How a plate reaches the provider

This is already supported by the SDK and unused by the app. Two mechanisms, both real:

```python
# genblaze_core/pipeline/pipeline.py — step()
external_inputs: list[Asset] | None   # caller-held assets seed Step.inputs
input_from: list[int] | int | None    # a previous step's output seeds this one
```

and the GMI video registry routes those inputs into the model's native slots:

```python
# genblaze_gmicloud/models/video.py
_GMI_VIDEO_SEEDANCE_FAMILY = ModelFamily(
    pattern=re.compile(r"^seedance-"),
    input_mapping=route_images(slots=("first_frame", "last_frame")),
    ...
)
_FALLBACK = ModelSpec(input_mapping=route_images(slots=("image",)), ...)
```

So a character plate becomes `first_frame` on a Seedance shot with no new provider code —
the plumbing is `external_inputs=[Asset(url=…, sha256=…)]` on the step.

**`sha256` is not optional here.** `pipeline.step()` warns when an input asset lacks one,
because both the step cache key and the manifest's canonical hash fall back to the asset
*URL* — and on B2 that URL is presigned and rotates. Without the hash, the cache never hits
and the manifest's provenance claim drifts every render. `ReferenceFrame.sha256` already
exists on the model; it needs to be populated on upload and carried through.

### The two-stage character shot

The strongest version, and the one that answers "make video generation more effective":

```
  stage 1  IMAGE  ~$0.04   plate + shot prompt ──▶ a still of Amanda in this scene
                                    │
                                    │  a human approves it (or regenerates for pennies)
                                    ▼
  stage 2  VIDEO  ~$0.61   approved still as first_frame ──▶ the clip
```

The first stage is both the cheap preview *and* the thing that makes the second stage
accurate — a video conditioned on an approved frame cannot drift away from a face that was
already signed off. You stop paying $0.61 to discover a composition you would have rejected
at $0.04, and the frame you approve is literally the frame the clip starts from.

---

## 4. Silence (partly built)

A kit loop is a backdrop the master plays over. Audio in it is a defect, not a bonus.

Built: `briefs.VIDEO_NEGATIVES` sends `music, soundtrack, singing, speech, …` as
`negative_prompt`, and the composed prompt carries a `silent footage` clause.

**This is best-effort and should not be described as more.** `negative_prompt` is a
conditioning signal; no provider here documents it as binding on the audio track. The
deterministic fix is to drop the track on delivery:

```
ffmpeg -i in.mp4 -c copy -an out.mp4
```

Stream-copy, so it is a container rewrite rather than a re-encode — fast, lossless, and
ffmpeg is already a dependency (`adapters/mock_media`). The work is the download/re-upload
round trip through storage, which is why it is not in this change. Until it lands, a
generated clip may still arrive scored, and the console should say so rather than implying
the negatives settled it.

---

## 5. Proxy frames (designed)

A video shot costs roughly fifteen times its image equivalent (`adapters/pricing`, checked:
`seedance-2` is 61¢ for 6s, `seedream-5.0-lite` is 4¢). So the cheapest useful preview of a
video plan is **the same plan rendered as stills**.

`ShotSpec` needs one field:

```python
proxy: bool = False   # render this video shot as a still instead
```

`_resolve()` maps a proxied VIDEO shot onto the IMAGE provider, keeps the prompt and the
aspect ratio, drops `duration`, and prices it as an image. The plan screen shows both
numbers — "storyboard $0.12 / full run $2.44" — and a storyboard that reads correctly can
be promoted shot-by-shot, with each approved frame becoming that shot's `first_frame`.

This is the same mechanism as §3's two-stage shot, applied to composition rather than to a
face. They should share an implementation.

---

## 6. Step cache (available, unwired)

`genblaze_core.pipeline.cache.StepCache` exists and `Pipeline.cache(...)` accepts it. The
app never calls it, so **re-running an identical shot is billed again every time** — which
is the normal case while somebody iterates on a prompt.

The key is a canonical hash over provider, model, prompt, negatives, params, seed,
modality, and input content ids. Wiring it is three lines.

**It is not wired here, on purpose.** A cached step replays its stored asset URLs, and on
B2 those are presigned and expire. A cache hit after expiry produces a kit that reports
`ready` with assets nothing can fetch — the exact failure mode `ui.routes.asset_url` was
written to avoid by deriving URLs from keys at render time. The cache should land together
with a rehydration step that maps a cached asset back to its object key before the kit
records it. Until then, the honest state is: available, and knowingly not used.

---

## 7. Build order

Each of these is independently useful and independently shippable.

1. ~~Preview plane — `plan()`, exact payloads on screen, per-shot editing~~ **done**
2. ~~Song-aware prompt defaults + anti-soundtrack negatives~~ **done**
3. ~~`sha256` and content type on reference frames at upload~~ **done**
4. ~~Character plates as `first_frame` — Amanda Kurt's face in a kit~~ **done**
5. Silent delivery — the ffmpeg `-an` pass (§4)
6. Proxy frames and shot-by-shot promotion (§5)
7. `Node` as a first-class document; migrate `Identity` onto it as `kind=character`
8. Step cache with key rehydration (§6)

Steps 3 and 4 were the ones that change what the videos actually look like. Everything
before them is about being able to see what you are buying; everything after is about
buying less of it.

### What plates turned out to need

Three constraints surfaced in the build that the design above did not anticipate, and all
three are the kind that pass on a laptop:

- **Providers disagree about image inputs, and disagreement is fatal.**
  `GMICloudVideoProvider` and `SoraProvider` declare `accepts_chain_input=True` with
  `image` among their inputs; `VeoProvider` and `ImagenProvider` declare neither.
  `Pipeline._check_step_capabilities` raises `GenblazeError` when a step carries
  `external_inputs` and the provider has not declared it accepts them — *before any step
  runs*, so the whole kit fails, not the one shot. Since provider selection happens at
  runtime from whatever is keyed, the same brief conditions correctly on GMI and refuses
  outright on Google. `_plates_for` gates on the declared capability and drops the plate
  with a reason rather than letting the run die.
- **One plate per video shot, and it is a correctness limit.** Seedance routes a second
  image into `last_frame`, which the model reads as "end the clip here" and interpolates
  towards. A second reference frame requests a morph, not a stronger likeness.
- **Plates need storage that serves public URLs.** The provider does the fetching, from its
  own network. `LocalStorage.presign_get` returns `/files/…`, an app route that means
  nothing off-host — so `Storage.serves_public_urls` now exists and a live backend on local
  storage refuses to send plates instead of sending dead links. The mock generator fetches
  nothing and is deliberately exempt, which is what keeps the laptop exercising this path.

The preprocessing itself came in cheaper than specced: dedupe by content hash, one frame
per lighting setup, hashed frames preferred. No perceptual hashing, no blur detection, no
new dependency — and `Identity.plates()` is pure, so it costs nothing to call on every
render of the plan screen.
