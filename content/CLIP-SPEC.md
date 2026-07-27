---
title: "Clip Descriptor Spec (rtf.clip/v1)"
subtitle: "A standard sidecar file that carries a clip's *meaning* so an editor — human or machine — can place it, cut it, and generate matching footage around it."
status: "DRAFT v1 — in use for content/test01.MOV"
date: "2026-07-21"
---

## Why this exists

A raw `.MOV` tells you pixels, codec, duration. It does not tell you *what happens in it*, *where you're allowed to cut*, *who is in frame*, *whether you own it*, or *what an AI-generated shot would have to look like to cut cleanly after it*. All of that lives in a human's head and dies there.

The clip descriptor is a plain-text sidecar that holds exactly that, in a form both a person and a pipeline can read.

**Two files, two jobs — do not conflate them:**

| | Clip descriptor (`*.clip.yaml`) | Genblaze manifest (embedded, [BUILD-SPEC §5](../BUILD-SPEC.md)) |
|---|---|---|
| Answers | *What does this clip mean?* | *How was this file made?* |
| Authored by | a human (or a vision pass) | the generation pipeline |
| Lives | next to the media, in git | inside the media file + B2 |
| Trusted for | editorial decisions | provenance / FTC disclosure |

The descriptor is the input to generation; the manifest is the receipt from it. For an AI-generated clip, both exist and `provenance.manifest_b2_key` in the descriptor points at the manifest.

---

## Rules

1. **Sidecar, same basename.** `test01.MOV` → `test01.clip.yaml`. One media file, one descriptor. Never rename one without the other.
2. **All times are integer milliseconds from the clip's own frame 0.** No timecode strings, no floats, no "about 3 seconds in."
3. **`rights.source` is a hard gate, not a label.** Only `original`, `ai`, `stock_licensed`, or `public_domain` may enter a kit. This is the Pillar 06 enforcement point — a clip with no descriptor has no `rights.source` and is therefore unusable by construction. That is the intended failure mode.
4. **`beats[]` must tile the whole clip** — no gaps, no overlaps, `beats[0].t_in == 0`, last `t_out == duration_ms`. A gap means somebody stopped watching before they finished writing.
5. **`means` is the load-bearing field.** `frame` says what a camera recorded; `means` says why it's in the video. If you can't write `means` for a beat, the beat probably isn't earning its runtime.
6. **`cut_points[]` are *permissions*, not suggestions.** An editor may only cut on a listed point. This is what lets a music-sync pass align a clip to a drop without a human re-watching it.
7. **`continuity` is written for the machine downstream.** It describes what a *following* shot must inherit — subjects, wardrobe, light, lens — so an AI still generated to sit after this clip actually cuts. Under-specify it and you get a stranger in a different jacket.

---

## Schema — `rtf.clip/v1`

```yaml
schema: rtf.clip/v1              # required, versioned; breaking changes bump to v2
id: <slug>                       # stable, unique within content/; never reused
media: <filename>                # sibling file
title: <short human name>
logline: <one sentence: the whole clip in a breath>

source_meta:                     # mechanical — regenerate with ffprobe, don't hand-edit
  duration_ms: <int>
  width: <int>                   # display dimensions, post-rotation
  height: <int>
  fps: <num>
  video_codec / audio_codec: <str>
  aspect: "9:16" | "16:9" | "1:1"

rights:
  source: original | ai | stock_licensed | public_domain    # HARD GATE — see rule 3
  owner: <who>
  people_release: true | false | n/a   # everyone identifiable has consented
  minors_in_frame: true | false        # if true, guardian consent is required to publish
  notes: <str>

subjects: [<character id>, ...]  # → content/characters/<id>.character.yaml
setting: { place, interior_exterior, time_of_day, season, weather }
mood: [<adjective>, ...]
tags: [<free>, ...]

audio:
  has_speech: true|false
  quote: "<verbatim line, if any>"
  language: <code>
  diegetic: true|false           # sound belongs to the scene
  music_bed_ok: true|false       # safe to duck/replace audio under a music track
  keep_audio_on_cut: true|false  # the line must survive the edit

beats:                           # ordered, gapless, covers [0, duration_ms]
  - t_in: <int>
    t_out: <int>
    shot: <static|pan|whip_pan|dolly_in|handheld|...>
    framing: <wide|medium|close|insert|pov>
    frame: <what the camera sees>
    means: <why it's here — the story job of this beat>
    subjects: [<character id>, ...]

cut_points:                      # the only legal cut positions
  - t: <int>
    kind: in | out | hard_cut | beat_change | reveal
    note: <str>

role:                            # how this clip may be used in an edit
  primary: cold_open | setup | punchline | reveal | b_roll | outro
  can_lead: true|false           # may open a video
  can_follow: true|false         # may sit mid-timeline
  min_useful_ms: <int>           # shortest excerpt that still reads

continuity:                      # what a FOLLOWING shot must inherit — feeds AI prompts
  characters: [<id>, ...]
  wardrobe: { <id>: <description> }
  props: [<str>, ...]
  light: <str>
  lens_feel: <str>
  emotional_carry: <str>         # the feeling the next shot pays off

provenance:                      # AI clips only; null for camera-original
  manifest_b2_key: <str>
  model / prompt / run_id: <str>
```

Optional blocks are omitted, never left empty-with-a-guess.

---

## Companion files

**`content/characters/<id>.character.yaml`** — one per recurring person. Holds the description used verbatim in every generation prompt so the same face, hair, and wardrobe come back across clips. Character drift is the #1 reason AI cutaways don't cut.

**`content/edits/<id>.edit.yaml`** — an edit instance: which clip is the hook, which song, and the pictures that pay it off. References clips by `id`, never by filename. It does *not* carry the timeline or the beat grid — those are compiled from the format and the song it names. See [FORMAT-SPEC](FORMAT-SPEC.md).

**`content/formats/<id>.format.yaml`** and **`content/songs/<id>.song.yaml`** — the shape a video of this kind takes, and where one track's beats and drop actually are. A clip descriptor is the input to an edit; the format is the input to *every* edit.

---

## Layout

```
content/
  CLIP-SPEC.md              this file
  FORMAT-SPEC.md            the layer above: formats, songs, edit instances
  test01.MOV                media
  test01.clip.yaml          its descriptor
  characters/*.character.yaml
  formats/*.format.yaml
  songs/*.song.yaml
  edits/*.edit.yaml         + rtf.py, the resolver
  generated/                AI stills/clips (each with its own .clip.yaml)
```

## Validating

`beats` tiling, `cut_points` in range, `rights.source` presence, and character-id resolution are all mechanically checkable. Until there's a repo to hang it on, the check is: read the file top to bottom and confirm the last beat's `t_out` equals `source_meta.duration_ms`.
