# Overlay visuals

Everything that goes on top of the talking-head take. Three kinds of asset, and
they follow different rules:

| | **Scenes** (`scenes/`) | **Screen** (`screen-layer.html`) | **Slides** (`slides/`) |
|---|---|---|---|
| What | Animations composited over the shot | Live SQL and product screenshots, in a framed panel | Plates corner-pinned onto the monitor in frame |
| Background | Transparent | Transparent outside the panel; the panel itself is opaque | Opaque — a monitor showing transparency is a monitor that is off |
| Says | what the system *is* | that it is *true* | whatever specific values are needed |
| Rendered by | `render.py`, `compose.py` | `compose.py` | `render_slides.py` |

The spoken script they are cut against is `docs/submission/SHOOT_VOICEOVER.txt`.
The shot-by-shot notes are in `docs/submission/SHOOT_SCRIPT.txt`.

```bash
python3 capture_sql.py         # run the proofs on the live cluster → screen/proofs.json
python3 capture_app.py         # screenshot the deployed site → out/app/
python3 compose.py             # the whole cut → out/edit/spindle-cut.mp4
python3 compose.py --preview 30 45     # just that slice, for looking at
python3 render.py 12           # one scene, standalone, as a .mov with alpha
python3 preview.py 12 20 29    # single frames on a grey card
```

`out/` is gitignored. A full render is a few GB and takes several minutes.
`screen/proofs.json` is **not** gitignored: it is a transcript of what production
answered, and re-rendering on a machine that cannot reach the cluster must still
draw the real numbers.

---

## The frame: him in the middle, the diagram left, the evidence right

He sits **centred** in this take. The note further down this file about leaving the
lower-left quiet for a presenter sitting camera-left describes a different shoot and
does not apply — frames sampled at 8s, 22s, 45s, 90s and 140s put his head and
shoulders inside x 620–1250 at the tightest framing. So the composition is three
columns on the thirds:

```
   x 140-560          x 620-1250          x 1260-1888
 ┌────────────┐                          ┌────────────┐
 │  ANIMATION │        ( him )           │  EVIDENCE  │
 │  on a bed  │                          │  SQL/shots │
 └────────────┘                          └────────────┘
              ____________subtitles____________
```

**Nothing is drawn over him unless it is earning its place.** Four of the thirteen
scenes were cut and a fifth lost its beat to a SQL result; the windows at 24.8–30.6,
48.7–52.8, 80.6–87.8 and 137.7–144.4 are now the presenter with a bare frame. The
last of those is deliberate: "Postgres cannot do that at any price" is the hardest
claim in the script and it is said to camera with nothing on top of it.

A scene is **full frame by default** and slides into the left column exactly when a
panel comes up — never on a schedule of its own. `screen_cues.py` is the single list
both halves read, because the one arrangement that ruins the frame is a panel landing
on top of a full-frame diagram, and that is invisible until a render finishes.

Two things make the shrunk scene survive, and both are needed. It sits on a **bed** —
the same dark plate the evidence panel uses, so the frame reads as two instruments
flanking a person rather than one panel and some debris — and its **dots are grown
back** by `DOT_BOOST`, because the groove field draws at r=3.4 for a 1920-wide canvas
and lands near one pixel when scaled. Strokes are already immune
(`vector-effect: non-scaling-stroke`); the dots are not. Labels are faded out rather
than shrunk: at 0.42 a 26px label renders around 11px, and the panel and the burned
subtitles are both already carrying text.

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

## The cut

Nine scenes and nine panels. `PLAN` in `compose.py` holds the first, `CUES` in
`screen_cues.py` the second. Where a row has both, the scene is in the left column.

| t | Scene | Panel | Over the line |
|---|---|---|---|
| 0–20.7 | `01-disappear` full | — | "An independent label puts out a record…" |
| 21.0–24.5 | — | **shot** hero | "Spindle does it. Every night, for every artist." |
| 24.8–30.6 | `02-spam` full | — | "you're not marketing, you're spam" |
| 30.9–40.9 | `03-subspace` left | **sql** `search` | "the filters live inside the index itself" |
| 41.0–47.9 | — | **sql** `prefix` | "that index starts with tenant_id" |
| 48.7–52.8 | `05-pgvector` full | — | "pgvector can do the similarity" |
| 52.9–61.5 | `06-filling` left | **sql** `memory` | "the index is never finished" |
| 61.6–72.9 | `07-fleet` left | **sql** `bus` | "a changefeed … wakes the next one" |
| 73.3–80.3 | `08-lease` left | **shot** send | "the name can't tell them apart. The token can." |
| 80.6–87.8 | `09-token` full | — | "the reply finds its own thread" |
| 88.4–105.7 | `10-residency` full | — | "this row is in Virginia, this one in Ireland" |
| 106.1–113.1 | `11-money` full | — | "it spends the label's money, up to a ceiling" |
| 113.3–137.1 | `12-replay` full → left | **sql** `replay` | "four extra words of SQL… not a copy of them." |
| 137.7–144.4 | — | — | "Postgres cannot do that at any price." |
| 145.3–154.6 | `13-close` left | **stack** | "one database doing the index, the scheduler…" |
| 155.0–161.0 | — | **shot** hero | the URL |
| 161.3–166.2 | — | **card** endcard | *(past the take — see below)* |

**The end card runs past the take.** `BaseLayer.mov` is 161.2s; `compose.py` appends
`TITLE_SECS` of black with `tpad` and the card plays on it, so it never covers the
face on the line that asks for the click. Three consequences, all of which broke
something the first time:

* the overlay sequences must be rendered to `dur + TITLE_SECS`, or ffmpeg's image2
  demuxer ends early and takes the card with it;
* the voice needs `apad`, or `amix=duration=first` ends the mix when the take does
  and the card plays in silence;
* `screen-layer.html` derives `DUR` from the cue sheet. `Scene.define` clamps
  `seek(t)` to `DUR`, so a hardcoded 161.2 silently froze the layer on the old last
  frame — the card rendered as the closing screenshot, for five seconds.

**Cut, and why.** `04-tenant` is the one scene dropped for an editorial reason: its
whole content is that a column name comes first, and the `prefix` proof prints
`1 | tenant_id` straight off the cluster. Where the database states the claim in its
own words, an animation restating it is the weaker of the two and both at once is
noise. `12-replay` lost its last two paragraphs so the "Postgres cannot do that"
line plays bare, and `13-close` lost the final paragraph for a mechanical reason
worth remembering: **a scene whose settled state is made only of type renders as an
empty box in the left column**, because type is faded out there. Carrying 13 to the
end put a bordered, empty plate on the last shot of the film. Check the tail of any
scene before pairing it with a panel.

**If you only keep three, keep 03, 12 and the `replay` panel.** 03 is the
architecture claim, 12 is the thing nothing else can do, and `replay` is the only
asset in the whole film that a competitor cannot also produce.

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

## The evidence panel

`capture_sql.py` runs each claim the voiceover makes as SQL against `$DATABASE_URL`
and records **what actually came back**. `screen-layer.html` draws it and never
touches the database. The seam is `screen/proofs.json`, so the panel can be
re-typeset all night without re-querying production.

| slug | shows | why it is the proof and not a picture of one |
|---|---|---|
| `memory` | 43,194 counterparties · 111,212 facts · 22,057 embedded chunks · 122,129 agent runs | the accumulation is the product |
| `prefix` | `chunk_semantic` = (tenant_id, model, embedding) | the security boundary *is* the partition key |
| `search` | `• vector search … prefix spans: [/tenant/model - …]` | the filters are served **inside** the index; if the planner ever degrades this to a filter over a scan the capture fails rather than filming a lie |
| `bus` | a sinkless changefeed emitting a row written while it listened | the mechanism, working, in one shot |
| `replay` | a value overwritten, then read back at the ledger's stored HLC | the one thing in the film nothing else can do |

**The typing is not decoration.** A still of a result proves a result exists.
Watching the statement land and the rows arrive after it is the difference between
"we ran this" and "we wrote this down", which is exactly what the judging guidance is
asking for when it says to show memory in action rather than narrate it. The clock is
still a pure function of `t`, for the same reason everything else here is.

### Three things this deliberately does not show

**Residency.** `SHOW REGIONS` on production returns **one** region. The three-region
demonstration ran on the throwaway cluster `docs/runbooks/multiregion.md` §9 deletes
on purpose, and `docs/evidence/` does not exist, so there is no transcript either.
That passage keeps its animation and gets no panel. A single-region `SHOW REGIONS`
under a line about Ireland would disprove the narration rather than support it.

**A resident changefeed job.** `SHOW CHANGEFEED JOBS` returns zero rows — feeds are
opened per run. `bus` opens a real one and writes into it instead.

**The console.** The screens behind the sign-in wall are the better footage and
`capture_app.py --cookie` exists to film them. Sign-in is currently down:
`POST /signin/code` returns 502 with `AccessDenied … SendEmail`, and an emailed code
is the only credential this product has by design. The two `shot` cues point at the
public landing page, which needs no account and is genuinely the running product.
When sign-in is restored, re-capture and swap the two refs in `screen_cues.py`.

### The GC window is load-bearing

`replay` cannot be filmed against decisions already in the ledger. The cluster runs
`gc.ttlseconds = 4500`, so any timestamp older than 75 minutes is past the GC
threshold and `AS OF SYSTEM TIME` fails outright:

```
batch timestamp … must be after replica GC threshold
```

That is why `replay` writes, records an instant, overwrites and reads back **inside
one run**. Splitting it across two invocations reintroduces exactly the failure it
exists to demonstrate the absence of. Re-run the whole proof, never half of it.

Everything it writes lands on production — a proof against a scratch database proves
nothing about the system of record — confined to `party_fact` and `decision`, marked
`written_by='capture_sql'`, and attached to **no party**, so no row can be read as a
claim about a real company. `--clean` removes exactly what the marker matches.

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
