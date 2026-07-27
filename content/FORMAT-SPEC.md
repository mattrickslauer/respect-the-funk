---
title: "Format Spec (rtf.format/v1, rtf.song/v1, rtf.edit/v2)"
subtitle: "A video format is a shape you can retune, not a video you can copy. This is how the shape is written down, what a song is allowed to move, and what must never move."
status: "DRAFT v1 — promise-payoff is the only format; california-test (filmed hook) and generated-test (AI hook) are its two instances"
date: "2026-07-21"
---

## Why this exists

`california-test` works. The reason it works is not the joke — it is a *shape*, and the shape is reusable:

> **One clip makes a promise in one line and one image. The drop cuts it off. A carousel of pictures pays the promise back in an inverted world.**

Left as one edit file, that shape lives in a human's head and dies there — the same failure [CLIP-SPEC](CLIP-SPEC.md) exists to prevent one layer down. The second song would be a copy-paste of the first, the third would drift, and by the fifth nobody could say what made the first one land.

So the shape gets its own file, the song gets its own file, and the edit shrinks to the part that is actually about *this* video.

---

## Four files, four rates of change

```
lib/formats/promise-payoff.format.yaml      the shape    changes ~never
lib/cadences/question-reveal.cadence.yaml   the pacing   one per rhythm, any tempo
lib/songs/losing-sleep.song.yaml            the music    measured once per track
videos/california-test/edit.yaml            the joke     written per video
                    │
                    ▼   rtf.py resolve()
        the flat rtf.edit/v1 the renderers already consume
```

`rtf.resolve()` is a **compiler, not a runtime.** It merges the four into exactly the flat dict `render_edit.py` and `generate_stills.py` took before, so the format layer added zero new machinery downstream. A hand-written v1 edit with no `format:` key still passes straight through.

That the refactor is behaviour-preserving is checkable, not asserted: the resolved `california-test` renders **frame-for-frame identical** to the version built before the format existed (same 524 frames, same 17.466s, same SHA over the decoded video), and all 22 generation prompts hash to the same cache keys.

| | Format | Cadence | Song | Edit (instance) |
|---|---|---|---|---|
| Answers | *What shape is a video of this kind?* | *When does each thing happen?* | *Where are the beats, and how much drop can it carry?* | *What is the promise, and what pays it off?* |
| Owns | slots, invariants, defaults, craft doctrine | the hook's beats-before-the-drop | bpm, drop, pre-roll, carousel length | hook, congruence, the pictures and their prompts |
| Written by | whoever is defining a new format | whoever is stealing a rhythm that works | `measure_beat.py`, then a person listening | a person with a joke |
| Reused across | every video | every video at any tempo | every video using that track | nothing |

---

## Pacing — the cadence layer

Congruence between two videos is felt as **rhythm** before it is understood as meaning: *visual, beat, line, beat, drop.* That skeleton is the thing a viewer recognises across two videos that share nothing else, and it was the last part of this build still written as hand-typed milliseconds.

It isn't any more. A **cadence** (`cadences/*.cadence.yaml`) is the hook's pacing expressed as **beats before the drop** — so the hook runs on the same grid the carousel runs on, counted backward instead of forward. One grid for the whole video.

```
    13    12        11              8       6      4     2   0     ← beats before the drop
    ├─────┼─────────┼───────────────┼───────┼──────┼─────┼───┤
    establish │     THE LINE        commit  push  glimpse hold│
              turn                                           DROP ┃ carousel →
```

Change the song and every boundary moves with it; the shape doesn't. At 125 BPM `question-reveal` is a 6.24s hook, at 90 BPM it's 8.67s, and it is the same cadence both times.

**The numbers aren't invented — they're where `test01`'s cuts already were.** Measured against its 480ms beat, the whip-pan head landed 12 beats before the cut (+32ms), the bag landing at 6 (−38ms), the reveal at 4 (+22ms). Nobody planned that. The clip feels right partly because the picture was already cutting on the music, and the cadence file is that accident written down so the next one doesn't have to be lucky.

One relationship falls straight out of putting both on the same grid: the `line` slot **ends** on beat 8, and a 2-bar pre-roll **begins** on beat 8. The crescendo starts on the exact frame the talking stops. In `test01` that was a 202ms near-miss arranged by hand; on the grid it is exact and free.

Length is **binding for a generated hook and advisory for a filmed one.** A generated clip can be made any length, so it has no excuse to sit off its own grid — `generated-test` is now +0ms on all seven boundaries. A filmed clip is where the performance put it; re-cutting it to the beat after the fact would just clip the line, so `--check` *reports* its drift rather than rejecting it.

**The cadence also decides which generator makes each beat.** Every slot declares what it *needs* — `motion: performance | camera | static` — and that, not a guess, picks the tool:

| motion | means | rendered by |
|---|---|---|
| `performance` | someone speaks, or a body does something | a video model, **one take per slot** |
| `camera` | the frame moves but nothing in it does | a still + a zoompan move |
| `static` | a held composition | a still |

This came from getting it wrong. A video model is a **single-shot generator, not an editor**: handed a seven-beat shot list with timestamps it ignored every one of them and returned inert coverage paced to its own taste — which is why the first bowling hook "cut short and made no sense". Ask it for one shot of one thing and it is excellent. The cutting is ours, done in ffmpeg against the grid, which is why the rebuilt hook sits at +0ms on all six boundaries.

The economics follow the same line: `count-off` needs **one** video take (the line) and five stills. Paying a video model for a static composition buys nothing but drift and a bill.

A cadence also names its `reveal_slots` — the slots that are *about* the withheld subject. They stand or fall together. When the reveal subject can't be rendered by the video model at all, **every** reveal slot moves to stills, not just the last one: splitting them leaves the glimpse with nothing to glimpse, which silently deletes the two-stage turn and reads as a jump.

For a generated hook the cadence also composes the video prompt: the beat-by-beat timings come off the grid and the descriptor's own `frame` lines, never off a number someone typed. Retune the song and the prompt retimes itself.

The library so far, each with its own bet about how to buy the first second:

| Cadence | Shape | Inspired by |
|---|---|---|
| `question-reveal` | establish → turn → **line** → commit → push → glimpse → hold | The Office / Nathan Fielder deadpan |
| `hold-and-break` | a 7-beat unbroken stare → twitch → **line** → hold | Breaking Bad / Sopranos cold open |
| `count-off` | three symmetrical tableaux → **line** → snap zoom → hold | Wes Anderson, 60s heist titles |
| `slow-push-realize` | one continuous move, no cuts, **line** on the last beats | Jaws' dolly zoom, Hitchcock |

---

## Prompt hygiene

Every likeness failure in this project so far has had the same shape: **doctrine written
for one kind of beat applied to a beat with the opposite need.** It cost thirteen bad
frames in a finished reel before it was named, so it is named here.

**1. The craft suffix must match the beat's subject count.** The default `shared_suffix`
says *"faces clearly lit, sharply in focus and unobscured"* — exactly right for 22
carousel pictures that must read in 480ms, and an instruction to *put faces in the
picture* everywhere else. Applied to a `glimpse` it produced a second full reveal; applied
to three empty-room tableaux it invented strangers crouching behind the props. Three
variants, chosen by what the beat contains:

| beat contains | suffix | cast described | reference photos |
|---|---|---|---|
| faces that must read | `shared_suffix` | yes | yes |
| a deliberately unreadable subject | `obscured_suffix` | **no** | **no** |
| no people at all | `still_life_suffix` | no | no |

An obscured beat gets no cast, no references and no identity clause. You cannot drift a
face you never named — and describing a child into a frame that hides them is also the
fastest way to have the image model decline the job outright.

**2. Negatives belong to a character, never to the image.** They used to be concatenated
across the whole cast into one global `Avoid:` list, so each cast member's negative fought
the others' description. The clearest case: Anthony's description says **"Narrow face"**
while Cosima's negative says **"thin face"** — in the same prompt, applied to the same
pixels. His *"short hair, straight hair, clean-shaven"* were landing on a 7-month-old.
Each negative now travels inside that character's own labelled reference group, scoped
both by naming them and by sitting immediately before their photographs in the parts
array.

**3. Describing someone is what puts them in frame; negation does not take them out.**
`bowl-18` asked for an empty lane with *"NO PEOPLE ARE VISIBLE"* and returned both of them
standing in it. The first hook frame asked for Anthony alone and got Cosima too, because
the shared character block still described her. The fix is always to withhold the
description, never to add a louder "no".

---

## The geometry

The word doing the work is **congruence**, in its geometric sense. The payoff is the hook under a *reflection*:

- The **figure** is preserved exactly — same faces, same ages, same wardrobe, same props, same relationship between the two people. Anything in `congruence.invariant`.
- The **ground** is inverted along declared axes — cold→hot, flat light→hard light, one room→a whole state, nobody→somebody, stuck→moving. Anything in `congruence.inverted`.

Both halves are load-bearing, and they fail in opposite directions:

- Change the figure and it stops being a payoff. A different man in a different jacket in California is not the same joke; it is a stock-footage travel reel. This is what [CLIP-SPEC](CLIP-SPEC.md)'s `continuity` block and the `*.character.yaml` files exist to hold.
- Leave the ground alone and there was never a joke to pay off. Twenty-two warm pictures with no declared inversion is a mood board.

Writing the axes down turns "does the sequence feel varied" — a taste question nobody can settle at 2am — into two mechanical checks: **every declared axis must be hit by at least one picture**, and **no axis may lead more than twice in a row.** That is the whole reason the axes are a field instead of an intention.

Three to six axes. Under three the carousel is monotone however good the pictures are; over six no axis gets enough pictures to register as a flip.

**The two halves are not equally enforced, and you should know which is which.** `congruence.inverted` is fully machine-checked — coverage, undeclared axes, run length. `congruence.invariant` is checked only for *faces*, by `bin/check_likeness.py`; nothing yet verifies that the ball is in the picture, or that it is the same ball.

The likeness pass locates the face, crops to it, and makes a vision model fill in a fixed feature table — face length-to-width, jaw, nose, brow spacing, hairline, philtrum — marking each `same`/`different`/`unclear` *before* it may score. **Both of those matter.** The first version sent the whole 1080×1920 frame and asked "is this the same person": in a full-body shot the head is ~6% of the pixels, so it was scoring hair colour and vibe, and an agreeable model asked a yes/no question says yes. It returned **5/5 "match" for every image a human had already rejected.**

So the checker reports its own accuracy rather than asserting it — `--truth` takes the frames a human called bad and prints the confusion matrix. Against 12 judgeable human flags: **v1 caught 0, v2 caught 8.** That is useful triage and not an authority; five real misses remain, and the human contact sheet is still the last word.

---

## Two instances, one shape

The format earns its keep only if a second video shares none of the first one's content and all of its structure. `generated-test` is that test:

| | `california-test` | `generated-test` |
|---|---|---|
| Hook | camera-original iPhone, `rights.source: original` | **AI**, `rights.source: ai` + a required `hook.generate` block |
| The line | "It's too cold, I'm going to California." | "Have you ever been bowling?" |
| Convergence | a statement, and the bag opens on the baby | a question, and the floor answers it |
| Song | `losing-sleep` | `losing-sleep` — same file, measured once, reused unchanged |
| Invariant (figure) | the two people, **their wardrobe**, the duffel | the two people, the ball — **not** wardrobe |
| Inverted (ground) | temperature, light, scale, status, motion | scale, status, era, light |
| Everything in *Rules* | enforced | enforced |

The wardrobe row is the one worth staring at. `california-test` holds the clothes fixed *because* it flips the weather — the same jumper in the desert is the joke. `generated-test` flips the **era**, and wardrobe has to travel with it, so the ball inherits the job the duffel was doing. **The invariant set is a per-instance decision, not a format constant** — the format only insists that you declare it and then hold to it.

---

## Any cast, any world — and the "wow" problem

Nothing in this format is about a man and a baby. The figure that gets preserved is whatever the instance declares; `cast:` names it, and the character files carry the faces. Cast is **declared, not discovered** — the prompt machinery used to glob a shared `characters/` directory, which is correct only while the project contains exactly one cast and silently wrong the moment it contains two. A video can add its own people in `videos/<id>/characters/` without them leaking into anyone else's prompts.

The harder ask is *wow*. "Make it funnier" is not checkable and never will be. But the specific way these sequences fail is:

> **The pictures are fine and the running order is flat.**

Twenty-two individually good pictures at one level is a mood board. A carousel that opens at full volume has nowhere to go. So every picture declares an **`intensity` 1–5** — how far past what a viewer would have guessed it goes — and three things are enforced:

1. **The peak lands in the final third.** Deflating before the end card wastes the drop.
2. **The end card is strictly below the peak.** It is the release, not the climax — both worked examples end on someone asleep, and that is not an accident.
3. **The last third out-averages the first by ≥ 0.5.** It has to actually climb.

This was not written to bless what already existed. Turned on, it **failed both videos immediately** — `california-test` at lift +0.29 and `generated-test` at +0.29, against a floor of +0.50. Neither was building; both had been authored in the order the ideas arrived.

The fix cost nothing. Running order is free to change — same pictures, same prompts, same cache — so `bin/` grew a reorder pass that hill-climbs on pairwise swaps against a cost function: axis runs are a hard constraint, lift and a late peak are the objective. `california-test` went to **+1.57**, `generated-test` to **+2.00**.

Two things that fell out of doing it, both worth keeping:

- Sorting purely by intensity **groups the axes**, because the biggest pictures tend to be big in the same *way* — every `scale` shot lands together at the end. The `max_axis_run` rule is what forces the climb to stay varied, and it is why a sorted list reads as *sorted* while this one reads as *building*.
- A high lift is achievable **while still opening at full volume** — the average hides it. The first attempt put a 5 in slot one and scored well. The opening band has to be held down explicitly.

---

## Rules

Everything in this list is enforced by `rtf.check()` and reported by `render_edit.py --check`. None of it is taste — taste lives in the prompts and in whether the joke is funny. These are the things that make a video of this format *work as this format*, and they are the things a person retuning a new song gets wrong first.

1. **The hook is one clip, one line, one legal exit.** It must declare `role.can_lead: true`, carry a real `audio.quote`, and its `t_out` must be a `cut_point` of kind `hard_cut` in its own descriptor. The drop lands on that cut and nothing in the edit may move it.
2. **The hook may not run under its own `min_useful_ms` or over the format's `max_ms`.** Below the first, the setup breaks before the drop arrives. Above the second, a vertical feed has already decided about you.
3. **A generated hook must carry the prompt that made it.** If the hook's descriptor declares `rights.source: ai`, the instance must supply `hook.generate.prompt` — and `hook.generate.dialogue` too if the descriptor promises a spoken line, or the model will invent one. `ai` is a legal source only when the provenance exists ([CLIP-SPEC](CLIP-SPEC.md) rule 3), and for a hook the provenance *starts* as the prompt: without it the clip is unreproducible the moment the model version moves under it.
4. **The song was measured, not typed.** `measured.bpm`, `measured.drop_ms` and `measured.method` are all required. A BPM with no provenance is a guess, and a guess does not average out: a fixed offset makes every one of the cuts late by the same amount, for the whole sequence. (This is exactly how a 125 BPM track reads as 83.33 — the dotted-quarter alias — and why `measure_beat.py` prints its reasoning.)
5. **The pre-roll must fit inside the hook.** `bars × 4 × beat_ms ≤ hook duration`, or the swell would start before the video does. It should also start *after* the spoken line ends, or the crescendo fights the dialogue.
6. **The carousel's beat count must equal `song.tuning.carousel_beats`.** The song declares how much drop it can carry at full energy; the item list has to satisfy it. When they disagree the renderer believes the items, silently — so the check exists to make the disagreement loud.
7. **Minimum item count.** Under ~12 pictures the eye reads "slideshow", not "sequence".
8. **No two adjacent pictures may resolve to the same camera move.** The `animate` list cycles over the items; two identical moves back to back read as one long shot across the cut, which is the one thing a title sequence must never do.
9. **Every declared inverted axis must be hit, and no axis may lead more than twice in a row.** See *The geometry*. An item's first `hits` entry is its primary.
10. **An item may not hit an axis nobody declared.** Either add it to `congruence.inverted` — and then something else must hit it too — or stop claiming it.
11. **The carousel must escalate.** Peak in the final third, end card below the peak, last third out-averaging the first by ≥ 0.5. See *Any cast, any world*.
12. **A minor in frame plus an unlicensed track cannot be published.** Restated at the format boundary because both halves live in different files and neither one alone looks wrong. See [CLIP-SPEC](CLIP-SPEC.md) rule 3 and Pillar 06.

`generate_stills.py` refuses to spend money when any of these fail. A `--dry-run` still prints the plan, because you are usually mid-authoring when you see them.

---

## Schemas

### `rtf.format/v1`

```yaml
schema: rtf.format/v1
id: <slug>
premise: <what a video of this shape does, in a breath>

slots:
  hook:
    count: 1
    requires_can_lead: true       # → clip.role.can_lead
    requires_speech: true         # → clip.audio.quote must be non-empty
    must_end_on: hard_cut         # → the cut_point kind at t_out
    max_ms: <int>
  carousel:
    min_items: <int>
    max_axis_run: <int>           # consecutive pictures allowed on one primary axis
    escalation: { require, peak_within_last, min_lift }
    hold: beats

beat_grid: { origin: hook.t_out, quantize: strict }

defaults:                          # a song or an instance may override any of these
  output:   { dir, width, height, fps, aspect }
  tuning:   { speech_lufs, pre_drop_gain, pre_roll: {bars, curve, from_gain, to_gain} }
  carousel: { source, look, flash_frames, end_card_beats, animate: [<move>, ...] }

prompt:
  shared_suffix: <the craft doctrine every prompt inherits>
```

### `rtf.song/v1`

```yaml
schema: rtf.song/v1
id: <slug>
title / artist / file: <str>
rights: { license: owned | licensed | ..., notes }   # HARD GATE, same as CLIP-SPEC rule 3

measured:                          # ← measure_beat.py output. Never hand-typed.
  bpm: <num>
  beat_ms / bar_ms: <int>          # derived, restated for reading
  drop_ms: <int>
  method: <how it was measured, and what was ruled out>

tuning:                            # ← the entire per-song surface
  carousel_beats: <num>            # an item's `beats` may be fractional: 0.5 is a
                                   # double-time cut, 2 is half-time. No divisor knob.
  pre_roll: { bars, curve, from_gain, to_gain }

fits:                              # what this track wants from each format
  <format id>: { hook_ms_range: [lo, hi], energy: <str> }
```

### `rtf.edit/v2` (an instance)

```yaml
schema: rtf.edit/v2
id: <slug>
format: <format id>
cadence: <cadence id>
song: <song id>
cast: [<character id>, ...]      # declared; never globbed from a shared directory
title / intent: <str>

hook:
  clip: <clip id>                  # → ../<clip id>.clip.yaml. audio/lufs inherited.
  t_in: <int>
  t_out: <int>                     # must be a hard_cut cut_point on that clip
  generate:                        # REQUIRED when the clip is rights.source: ai
    provider / model: <str>
    duration_ms: <int>
    reference_characters: [<id>, ...]
    dialogue: <the line the model must say, verbatim>
    prompt: <the full shot-by-shot video prompt>

congruence:
  from: <the hook's world, in one line>
  to:   <the payoff's world, in one line>
  invariant: [<what must not change>, ...]
  inverted:  { <axis>: "<before> → <after>", ... }

prompt:
  characters_block: "{<id>.prompt_description} and {<id>.prompt_description}"
  identity: <the rule about who these people are and how old they are>

carousel:
  items:
    - still: <name>                # → stills/<name>.png
      hits: [<axis>, ...]          # first entry is the primary
      intensity: <1-5>             # how far past what a viewer would have guessed
      prompt: <the full body of the generation prompt for this picture>
      beats: <int>                 # optional; last item inherits end_card_beats
```

Note that a prompt lives **with the picture it makes.** The v1 file kept them as two parallel lists joined by a `prompt_ref` integer, which is one renumbering away from putting the wrong words on the wrong image.

---

## Tuning a new song

The format holds still; this is everything that moves. Roughly a day, most of it in step 5.

1. **Clear the master.** Fill `rights` in `songs/<id>.song.yaml`. No license, stop here — Pillar 06 is a gate, not a label.
2. **Measure it.** `python3 edits/measure_beat.py <file> --drop-ms <your guess> --cuts <n>`. Paste `bpm`, `drop_ms` and the tool's *reasoning* into `measured:`. Your guess at the drop will be 100–700ms off; that is normal and it is why the tool snaps to the transient.
3. **Set `carousel_beats`.** Listen from the drop forward and find where the arrangement starts breathing — cutting past that point fights the music. Round to a whole bar where you can. For `losing-sleep` that is 23 beats / 11.04s.
4. **Pick the hook.** It has to pass rules 1, 2 and 4: leads, has a line, ends on a `hard_cut`, sits inside `fits.hook_ms_range`, and its line ends before the pre-roll swell begins. If the line runs late, drop to `bars: 1` rather than moving the cut.
5. **Declare the congruence.** What is invariant — the figure. What inverts — the ground. This is the actual creative work and everything downstream is bookkeeping.
6. **Write the pictures.** `carousel_beats` worth, one legible idea each (a viewer gets ~480ms and will not parse two), `hits` assigned so no axis leads twice running. End card last.
7. **Watch the timing before you spend anything.** `render_edit.py <edit> --check`, fix what it says, then render for real — missing media comes out as labelled placeholder cards, stills *and* the hook, and the music is laid in either way so you can hear the drop land on the cut. If the shape is wrong you will see it in the placeholders, and placeholders are free.
8. **Generate.** `generate_stills.py <edit> --dry-run`, then without the flag. Content-addressed: re-running is free, changing one prompt costs one image. For a generated hook, `generate_hook.py <edit>` — one video take per `performance` slot, stills for the rest.
9. **Check the faces.** `check_likeness.py <edit>`. Re-roll anything at or below 3 with `generate_stills.py <edit> --only <name> --reroll 1`, which keeps the previous attempt rather than overwriting it. Treat the scores as triage, not verdict — see *The geometry* for how accurate this actually is.
10. **Render.** `render_edit.py <edit>`.

---

## Layout

```
content/
  FORMAT-SPEC.md  CLIP-SPEC.md
  bin/            rtf.py (resolver + invariants), render_edit.py,
                  generate_stills.py, generate_hook.py, measure_beat.py,
                  check_likeness.py
  lib/            everything reused ACROSS videos
    formats/    cadences/    songs/    audio/    characters/
  videos/
    <video-id>/               one folder per video, self-contained
      edit.yaml               the instance
      <hook-id>.clip.yaml     + its media, takes, and generated frames
      stills/                 the carousel + one sidecar each
      <video-id>.mp4          the finished reel
  .cache/         content-addressed; identical prompts dedupe across videos
```

Every tool takes a **video id**, a video directory, or a path to an `edit.yaml`:

```
python3 bin/render_edit.py california-test --check
```

A video folder is self-contained enough to delete, zip, or hand to someone. What sits in `lib/` is there *because* it is shared — a format, a cadence and a measured song are worth more the second time they are used, and a video that needed its own copy of one would be evidence the layering is wrong. Characters are the exception that proves it: `lib/characters/` holds recurring cast, and a video may override or add its own in `videos/<id>/characters/`.

---

## What this deliberately does not model

- **Chronology.** The carousel has none. Any two pictures may swap; only the end card is positional. A format where order carries meaning is a different format.
- **Text on screen.** No lyric burn-in, no captions. An item may request a `title`, and that is the whole of it.
- **A second reveal.** The hook already revealed. The carousel is consequence, not twist — if it has its own turn, the hook was too weak.
- **Transitions.** Hard cuts and one white flash frame. Dissolves smear across the beat and the beat is the point.
- **Multiple hooks.** `count: 1`. Two promises is a story, and this format cannot pay off a story in eleven seconds.
