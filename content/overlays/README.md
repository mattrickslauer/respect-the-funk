# Overlay visuals

Everything that goes on top of the talking-head take. Two kinds of asset, and
they follow opposite rules:

| | **Scenes** (`scenes/`) | **Slides** (`slides/`) |
|---|---|---|
| What | Animations composited over the shot | Plates corner-pinned onto the monitor in frame |
| Background | Transparent | Opaque — a monitor showing transparency is a monitor that is off |
| Output | `.mov` (ProRes 4444, alpha) + `.webm` (VP9, alpha) | `.png` at 2560×1440 |
| Rendered by | `render.py` | `render_slides.py` |

The spoken script they are cut against is `docs/submission/SHOOT_VOICEOVER.txt`.
The shot-by-shot notes are in `docs/submission/SHOOT_SCRIPT.txt`.

```bash
python3 render.py              # all scenes → out/
python3 render.py 12           # just scene 12
python3 render.py --fps 60     # for a 60p timeline
python3 render_slides.py       # all slides → out/slides/
python3 preview.py 12 20 29    # single frames on a grey card, for looking at
```

`out/` is gitignored. A full render is a few GB and takes several minutes.

---

## The design, in one paragraph

The brand mark is a record read as a radial spectrum, so **every diagram is drawn
on a polar grid** — no rectangular flowcharts anywhere. One object recurs through
the whole video: a disc of counterparties arranged in concentric grooves. It is
established in the opening, filtered in the index beat, keeps filling as facts
land, and is what the playhead rewinds at the end. Thirteen scenes, one character
transforming, rather than thirteen unrelated illustrations.

**Purple is reserved.** `#7d4dff` appears in exactly two scenes — `04-tenant` and
`12-replay` — because those are the two moments CockroachDB is doing something
nothing else can. Everywhere else is copper. If purple leaks into a third scene it
stops meaning anything and the two that matter lose their signal.

**No numbers.** There is not a digit in the spoken script and there should not be
one here either. The voice carries the story; the *slides* carry whatever specific
values are needed. Counts and costs in an overlay are the only claim a viewer can
catch you on, and nobody remembers them anyway.

---

## The scenes

Durations are cut to the spoken paragraphs at ~155 wpm. Total ≈ 2:56.

| # | Scene | Dur | Over the line | Rank |
|---|---|---|---|---|
| 01 | `disappear` | 26s | "An independent label puts out a record…" → "Spindle does it." | 4 |
| 02 | `spam` | 8s | "you're not marketing, you're spam" | 5 |
| 03 | `subspace` | 10s | "the filters live inside the index itself" | **1** |
| 04 | `tenant` | 8s | "one column doing our security boundary and our partition key" | 3 |
| 05 | `pgvector` | 6s | "pgvector can do the similarity" | 6 |
| 06 | `filling` | 10s | "the index is never finished" | 5 |
| 07 | `fleet` | 16s | "there's no orchestrator" → "kill half the fleet" | 4 |
| 08 | `lease` | 9s | "the name can't tell them apart. The token can." | 4 |
| 09 | `token` | 8s | "the reply finds its own thread" | 6 |
| 10 | `residency` | 21s | "this row is in Virginia, this one physically in Ireland" | 3 |
| 11 | `money` | 10s | "it spends the label's money too, up to a ceiling" | 4 |
| 12 | `replay` | 32s | "four extra words of SQL… not a copy of them. Them." | **1** |
| 13 | `close` | 12s | "one database…" → the URL | 2 |

**If you only build three, build 03, 12 and 13.** 03 is the architecture claim,
12 is the thing nothing else can do, 13 is the call to action. Everything else is
texture and the film survives without it.

A few scenes worth knowing the intent of, because the intent is not recoverable
from watching them once:

- **03 `subspace`** — the dial closes *clockwise* rather than pinching symmetrically,
  so the three filter detents land in sequence around the rim. The symmetric
  version stranded them on the far side of the disc from the wedge they'd made.
- **07 `fleet`** — no line is ever drawn rim-to-rim. Every impulse visibly goes
  through the centre and back out, because the claim is that agents never call
  each other. One rim-to-rim line and the picture argues the opposite of the VO.
  The progress arc also never stalls at the `kill -9`; a flat spot there would
  invert the whole beat.
- **08 `lease`** — both workers are deliberately identical: same glyph, same size,
  same name. If the picture distinguishes them, the line has nothing to explain.
- **10 `residency`** — the table glyph never splits or duplicates. Only the rows
  travel. Two tables would be a different (and wrong) claim. A world map was the
  other option and it makes the beat about geography instead of about one table
  not caring about geography.
- **12 `replay`** — the valuation is drawn as the mark itself, so rewinding
  visibly reshapes *the same object* rather than showing two charts side by side.
  Two charts invite "so you stored a copy", which is the one reading this beat
  cannot leave available. The ghost bars behind show today's values, so what
  comes back is visibly *different*, not merely redrawn.

---

## Sound, level and colour

```bash
python3 sfx/synth.py     # -> out/sfx/*.wav   the palette, synthesised
python3 sfx/cues.py      # -> out/sfx/bed.wav one stereo bed, placed against the cut
python3 compose.py       # grade + level + bed, in the same pass as the graphics
```

**The audio arrived 12 dB too hot.** The source measures **−2.76 LUFS with a
+11.25 dBTP true peak** — guaranteed to clip the moment a player converts it to
16-bit. But `Flat factor: 0.0` says the waveform was never actually flat-topped,
so nothing had been destroyed and no declipper was needed. The fix is one number:
**−12.8 dB of pure gain**, which lands it at −15.61 LUFS / −1.71 dBTP. A gain is
arithmetic — it cannot degrade anything, which is why it was chosen over
`loudnorm`'s dynamic mode.

What could *not* be fixed, and is worth knowing before the next shoot: **LRA is
2.5**. The dynamics were squashed flat before this file existed. Nothing here
restores them. Record with peaks around −6 dBFS and leave the limiter off.

**The grade** corrects about two thirds of a warm orange cast (measured U 118.7 /
V 135.7, both off neutral the same way) and opens up a flat, dim image. It lands
at Y 107 · U 122.6 · V 132 · SAT 16.4. Deliberately not neutral: it is a warm
interior with a wood door in shot, and driving U/V to 128 makes a real room look
like a rendering. Graded in 10-bit so the contrast curve does not band the wall.

**The sound effects are synthesised, not sampled** — partly because there is no
library here, mostly because they have to be *matched*: the aperture in scene 03
closes over a duration the cue sheet knows, so its sweep is synthesised to
exactly that length. The palette is written to three rules — under 400ms, energy
below 6kHz (bright reads as "app" and collides with sibilance), and normalised
quiet per-sound so the cue sheet needs no mixing pass.

Cues are written in **scene time, never timeline time** (`"0.9s into scene 03"`).
The scenes are stretched to fit what was said, so a cue pinned to the timeline
drifts off its own visual the moment anyone re-reads a line. Pinned to scene time
it survives a re-shoot: re-run `align.py`, re-run `cues.py`, everything is still
on its frame. 88 cues, bed peaking −17 dBFS, about 15 dB under the dialogue.

Nothing is scored to *speech* — no cue on a word or a sentence end. Every sound
attaches to something that visibly happens, so it reads as the picture having
physical presence rather than as decoration under narration. `TRIM` at the top of
`cues.py` is the one knob if the bed sits wrong.

Everything above is applied **in the same pass that lays on the graphics,
straight from `BaseLayer.mov`**. Nothing in this pipeline ever reads its own
output — grading a finished h264 and re-encoding would put two generations of the
same lossy codec on the picture for nothing. `--master` swaps the h264/AAC
deliverable for ProRes 422 HQ and 24-bit PCM.

## Compositing notes

**Scenes.** Drop the `.mov` on a track above the talking head; it is straight
(unpremultiplied) alpha, so no matte adjustment. They are 1920×1080 with the
action centred and roughly 8% of safe margin, so they survive being scaled to
half and parked in a corner. Several — 01, 03, 06, 12 — put their action in the
upper two-thirds and leave the lower-left quiet, which is where a presenter
sitting camera-left tends to be.

Nothing loops and nothing is designed to be trimmed from the front. If a scene is
too long, slow the read or hold on the last frame; the last second of every scene
is a settled state that holds indefinitely.

**Slides.** Corner-pin the PNG onto the monitor. They render at 2560×1440 so
there is resolution left after the warp — do not re-render them at 1920 to "save
time", they go soft immediately. Add whatever screen grain, reflection and
slight blur the shot needs; a perfectly clean plate reads as a composite.

**The one thing to get right.** Type in the scenes carries a dark rim
(`paint-order: stroke fill`) so it holds over a bright desk. Check every scene
over the *actual* footage before committing to a colour grade — `preview.py --bg`
takes a hex, and the two worth testing are `--bg dddbd6` and `--bg 141312`.

---

## How the renderer works, and why it is like that

Scenes are **pure functions of time**. There are no CSS keyframes, no
`requestAnimationFrame`, no transitions anywhere in `lib/scene.js`. Each scene
exposes `window.seek(t)` which places every element exactly where it belongs at
`t` seconds, and `render.py` walks `t` in fixed steps taking a screenshot each
time.

That is not fussiness. A CSS animation captured by a screenshotter lands wherever
the compositor happened to be, so two renders of the same scene differ, and
re-rendering scene 12 after a tweak jitters against the take already in the
timeline. Driving the clock explicitly makes a frame reproducible forever — which
is what makes it safe to adjust a scene at 2am after the edit has started.

The dot scatter uses a seeded RNG for the same reason.

### Two traps, both already paid for

**CSS beats presentation attributes.** `node.setAttribute('stroke', …)` against
anything carrying `.wire` does nothing — the stylesheet rule keeps winning and the
element stays copper. This silently broke the purple time-travel moment in scene
12 and the dead-worker greying in scene 07. Recolour with `node.style.stroke`, or
swap the class. Same applies to `fill` and `opacity` on `.dot`, which is why
`.dot` is deliberately unstyled in `scene.css`.

**Classic scripts share one global lexical scope.** A bare `const lerp` in
`lib/scene.js` and `const { lerp } = SP` in a scene file are a redeclaration
error that kills the page before it renders — with no console output unless you
are listening for `pageerror`. `scene.js` is wrapped in an IIFE and everything
leaves through `window.SP`. Keep it that way.

`preview.py` and `render.py` both listen for `pageerror` and fail loudly. If a
scene renders blank, that is the first thing to check.
