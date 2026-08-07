# `content/` — the RemixKit asset pipeline

> **Status: subproject.** Per [`docs/SCOPE-RESET.md`](../docs/SCOPE-RESET.md), RemixKit is a
> component of the platform, not the product. Everything here is **reference** — accurate
> about the file formats `bin/` reads and writes, and not a statement of what is being built.
> The binding documents are [`SCOPE-RESET.md`](../docs/SCOPE-RESET.md) and
> [`PLATFORM-SPEC.md`](../docs/PLATFORM-SPEC.md).

A song and a library of clips go in; a beat-locked vertical video comes out. Fifteen tools,
a descriptor library, and five documents describing the formats they agree on.

## The five documents, by genre

They are not the same kind of thing, and reading them as though they were is the main way
this folder confuses people.

| | Genre | What it is | Read it when |
|---|---|---|---|
| **[`CLIP-SPEC.md`](./CLIP-SPEC.md)** | **Normative** | `rtf.clip/v1` — the sidecar that carries a clip's *meaning*: beats, cut points, rights, continuity. 7 rules. | Writing or parsing a `*.clip.yaml` |
| **[`FORMAT-SPEC.md`](./FORMAT-SPEC.md)** | **Normative** | The layer above — formats, songs, cadences, and the promise-payoff shape. The largest document here. | Authoring an edit, or adding a format |
| **[`MEME-ENGINE.md`](./MEME-ENGINE.md)** | Report | Proof of concept: a stock library plus a measured song, cut by a solver. No model calls. | Understanding `meme_engine.py` / `fetch_library.py` |
| **[`FOUND-HOOKS.md`](./FOUND-HOOKS.md)** | Report | Proof of concept: mining a caption track for a spoken hook, then generating a congruent payoff. | Understanding `fetch_hooks.py` / `scaffold_edit.py` |
| **[`RHYTHM-STUDY.md`](./RHYTHM-STUDY.md)** | Study | Measurements behind the cadence claims — where the drop is, what the bar line is, how two videos' rhythms compare. Proposes five rules; **none are enforced.** | Changing anything about timing |

**Normative documents constrain the files.** The reports and the study describe runs that
happened and what was learned; their findings are not gates. Where one proposes a rule, it
says so and says it is unenforced.

## Layout

```
content/
  bin/        the fifteen tools
  lib/        the descriptor library — inputs, versioned, reused across edits
  videos/     one directory per edit — outputs, self-contained
  demo/       the 2026-08-03 submission kit (script, screenshots, cut video)
  *.md        the five documents above
```

### `lib/` — inputs, written once, reused

| | Tracked | Holds |
|---|---|---|
| `lib/characters/` | 18 | `rtf.character/v1` — a person's reference frames and description. Attached to every generation prompt, because character drift is why AI cutaways don't cut. |
| `lib/stock/` | 16 | Fetched clip libraries with their `*.clip.yaml` sidecars |
| `lib/hooks/` | 13 | Mined dialogue hooks, one `*.clip.yaml` each |
| `lib/cadences/` | 4 | `rtf.cadence/v1` — the hook's rhythmic skeleton |
| `lib/briefs/` | 2 | `rtf.brief/v1` — what a library must contain, before it is fetched |
| `lib/formats/` | 1 | `rtf.format/v1` — promise-payoff |
| `lib/songs/` | 1 | `rtf.song/v1` — `losing-sleep`, measured. Every number in RHYTHM-STUDY is about this one track. |
| `lib/overlays/` | 1 | `rtf.overlay/v1` — overlay card decks |
| `lib/audio/` | 0 | Masters. Untracked by design; large, and not ours to redistribute. |

### `videos/` — outputs, one directory per edit

Five real edits, each with an `edit.yaml`, its hook descriptor, and 22–25 still descriptors:
`california-test`, `generated-test`, `nate-test`, `smart-test`, `storytime-test`.

Two directories here are **not** edits and have no `edit.yaml`:

- **`nocturnal/`** — three `*.plan.yaml` from the meme engine, which has no hook and so is
  not promise-payoff. See MEME-ENGINE.
- **`demo-founder/`** — the founder demo video's overlay cards, rendered from
  `lib/overlays/demo-founder.overlays.yaml`. It is submission material, not an edit.

Rendered `.mp4`, generated stills and fetched media are **untracked** — roughly 1.1 GB on
disk against 274 tracked files. `.cache/` and `.venv/` are likewise local only.

## The fifteen tools

Grouped by what they are for. All run from `content/` against `.venv/bin/python`.

**Measure** — turn media into numbers, once.

| | |
|---|---|
| `measure_beat.py` | A track's BPM, and the true downbeat near a stated drop. ⚠️ RHYTHM-STUDY §2 documents a case where its downbeat recommendation is wrong. |
| `measure_structure.py` | A song's own structure — drop, bar line, plateau length, and whether it can supply an intensity curve at all |
| `measure_pacing.py` | A video's cut rhythm on its own beat grid, and how far two videos' rhythms agree |

**Acquire** — bring in material and describe it.

| | |
|---|---|
| `fetch_library.py` | Brief → YouTube → normalised vertical shots plus sidecars |
| `fetch_hooks.py` | Mine YouTube captions for hook-shaped spoken lines, then cut around one |
| `screen_clips.py` | Flag clips carrying somebody else's burned-in text or logo. Abstains rather than guesses. |

**Generate** — the steps that cost money.

| | |
|---|---|
| `generate_stills.py` | The stills an edit references, content-addressed and cached |
| `generate_hook.py` | An edit's hook clip — a first frame, then Veo for motion |
| `generate_overlays.py` | Transparent-background overlay cards from an `rtf.overlay/v1` spec |
| `check_likeness.py` | Score generated stills against the cast's reference photographs. ⚠️ Triage, not authority — calibrated on one positive example. |

**Assemble** — resolve, plan, render.

| | |
|---|---|
| `rtf.py` | Resolve an `rtf.edit/v2` against its format and its song. The resolver everything else calls. |
| `scaffold_edit.py` | A found hook → a complete `rtf.edit/v2` with a congruent payoff |
| `meme_engine.py` | Library + measured song → beat-cut video. No model calls, no per-video cost. |
| `render_edit.py` | Render an `rtf.edit` timeline to mp4. `--check` validates without spending. |
| `vertex.py` | Vertex AI auth. Exists because express-mode API keys stopped working; see FOUND-HOOKS. |

## Running an edit, end to end

```bash
.venv/bin/python bin/fetch_hooks.py dialogue
.venv/bin/python bin/scaffold_edit.py lib/hooks/dialogue/storytime.clip.yaml --id storytime-test
.venv/bin/python bin/render_edit.py storytime-test --check     # free — validates the timeline
.venv/bin/python bin/generate_stills.py storytime-test         # ~$0.04 per still
.venv/bin/python bin/render_edit.py storytime-test
```

The meme-engine path, which calls no model at all:

```bash
.venv/bin/python bin/fetch_library.py nocturnal
.venv/bin/python bin/screen_clips.py lib/stock/nocturnal --reroll
.venv/bin/python bin/meme_engine.py --library lib/stock/nocturnal \
    --song losing-sleep --out videos/nocturnal --variants 3 --check
```

## What this pipeline does not check

Carried up from the documents' own limits sections, because each is easy to discover the
expensive way:

- **Nothing mechanical looks at pixels.** Beats tiling, cut points, axis coverage and
  escalation all passed while a payoff was a photorealistic room bolted onto a cartoon hook.
- **Line quality is the bottleneck.** Roughly one in three sources yields a hook worth
  building on, and no downstream machinery improves a line that was never a promise.
- **Three calibrations rest on one positive example each** — `screen_clips.py`,
  `check_likeness.py`, and RHYTHM-STUDY's cliff factor. Triage, not authority.
- **Every timing constant is measured on one song.** The methods should generalise; the
  numbers do not.
- **The rights gates were deliberately removed.** `render_edit.py` prints `rights.source`
  rather than refusing. Downloading is not publishing — the gate moved to a human. See
  FOUND-HOOKS, "Two gates removed."
