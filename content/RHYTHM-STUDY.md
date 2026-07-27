---
title: "Rhythmic and pacing congruency (rtf.pacing study)"
subtitle: "Congruence between two videos is felt as rhythm before it is understood as meaning. That was the last load-bearing claim in either spec with no number under it. Here are the numbers, and the generating function they imply."
status: "STUDY — two new measurement tools, one measured song, five rendered videos, four cadences"
date: "2026-07-24"
---

## Why this exists

[FORMAT-SPEC](FORMAT-SPEC.md) opens its cadence section with a claim it never checks:

> Congruence between two videos is felt as **rhythm** before it is understood as meaning:
> *visual, beat, line, beat, drop.*

Everything else in this project got measured. `rtf.check()` enforces the congruence of the
*ground* — axis coverage, run length, escalation. `cadence_drift()` reports how far a
filmed clip sits off its grid. But the rhythm itself was never measured and two of them
were never compared, so "these feel like the same channel" stayed a taste argument.

Two tools were written for this study:

```
bin/measure_pacing.py      a video → its ONSET TRAIN: every cut in beats relative to the
                           drop. Density, regularity, seam ratio, bar position, and a
                           Jaccard congruency matrix over any set of videos.

bin/measure_structure.py   a song → its own structure: where the drop is, where the bar
                           line is, how long the post-drop section holds, and whether the
                           track can supply an intensity curve at all.
```

Both report their assumptions inline and both name the methods that **failed**, which is
half of what was learned.

---

## 1. Two methods that do not work on this material

Written down first because they each cost an afternoon.

**Foote checkerboard novelty does not find a drop.** The standard structural-segmentation
move — beat-synchronous features, cosine self-similarity, checkerboard kernel — ranked
`losing-sleep`'s *measured* drop **8th to 79th** depending on the feature set, never 1st.
Two reasons, and both are structural rather than tuning:

- A drop is not a section boundary in the Foote sense. It is a **re-entry**. The
  full-energy material before the breakdown closely resembles the full-energy material
  after it, which is exactly the configuration a checkerboard kernel scores *low*.
- The usual cosine SSM **L2-normalises every feature row**, which removes loudness — the
  one dimension a drop is actually made of.

**Beat-accent combs do not find the bar line.** Scoring a bar-period comb at each of the
four phases is standard, and it is what `measure_beat.downbeat()` does. On four-on-the-
floor it cannot work, because every beat carries a kick:

| phase | low-band accent, post-drop | onset flux, post-drop |
|---|---|---|
| 0 | 53.25 | 5382 |
| 1 | 54.90 | 5458 |
| 2 | **57.99** | 5405 |
| 3 | 56.76 | 5422 |

A 5–9% spread, and the two measures **pick different winners**. That is not a weak signal,
it is no signal.

What does work is the arrangement. A mix changes on bar lines and almost nowhere else, so
the structural events *are* the phase reference.

---

## 2. The drop is the downbeat, and `measure_beat.py` says otherwise

`measure_structure.py` finds the largest arrangement changes at **beat** resolution — not
bar-averaged first, which would be circular — and then asks what their spacing is:

| change | beats from drop | bars | residue mod 4 |
|---|---|---|---|
| section change | −64 | −16.00 | 0 |
| **filter sweep begins** | **−8** | **−2.00** | **0** |
| **the drop / kick re-entry** | **0** | **0.00** | **0** |
| **full-energy section ends** | **+28** | **+7.00** | **0** |
| section change | +48 | +12.00 | 0 |
| — | +63 | +15.75 | 3 |

**Five of six share residue 0.** This is a test the data could have failed and did not.
Since arrangements change on bar lines, that residue is the bar line — and it is the
drop's own. The drop at 124784ms **is** the downbeat.

`measure_beat.py` disagrees. It prints `...which is a beat, NOT the downbeat`, puts the
bar line at 124304ms, and then recommends:

```
→ music.drop_ms: 124304
  cutting on the stated 124784 would sit 480ms off the beat...
```

That recommendation is wrong, and not harmlessly. 124304ms is one beat *before* the drop,
inside the tail of the filter sweep:

| | beat −1 (124304ms) | beat 0 (124784ms) |
|---|---|---|
| low-band energy | **0.51** | **52.19** — a 102× step |
| loudness | −12.34 dB | −4.96 dB |

Following the tool would land the video's hardest cut on **the quietest beat in the
sequence**. The author overrode it by ear and recorded the reasoning in
`losing-sleep.song.yaml` — but the note explains the choice as *transient over bar line*,
when in fact the transient **is** the bar line, and the next track may not get an ear.

---

## 3. Three things land on one bar line, and only two of them were known

The track's filter sweep begins exactly **8 beats — 2 bars — before the drop** (loudness
−4.43 → −7.21 dB, low-band 54.4 → 34.3 across that one beat).

Now put the three grids side by side:

```
                         beat 8 before the drop
                                  │
   the spoken line ENDS ──────────┤   question-reveal's `line` slot, at: 11 → 8
   the gain ramp BEGINS ──────────┤   pre_roll.bars: 2  =  8 beats
   the TRACK's riser BEGINS ──────┤   measured, this study
                                  │
                                  └──── 8 beats ────▶ DROP
```

FORMAT-SPEC already claims the first two coincide — *"the crescendo starts on the exact
frame the talking stops... it falls out of putting both on the same grid."* The third was
not known. **The 2-bar pre-roll is not faking a crescendo; it is uncovering the track's
own.** The format's default and the song's arrangement agree to the beat, and nothing in
the system was checking that or would notice if a new song disagreed.

---

## 4. `carousel_beats: 23` is five beats short and ends mid-bar

FORMAT-SPEC §Tuning step 3 is explicitly a listening instruction: *"Listen from the drop
forward and find where the arrangement starts breathing."* That is a measurement.

Measured on the **kick**, not on loudness — a mastered drop is limited to within a couple
of dB, so RMS barely moves when a section ends while the low band falls off a cliff:

| bar | beats | rms dB | low-band |
|---|---|---|---|
| 0 | +0..+3 | −4.67 | 54.92 |
| 1 | +4..+7 | −4.68 | 54.16 |
| 2 | +8..+11 | −5.20 | 53.46 |
| 3 | +12..+15 | −5.29 | 50.69 |
| 4 | +16..+19 | −4.99 | 58.29 |
| 5 | +20..+23 | −4.95 | 59.48 |
| 6 | +24..+27 | −5.08 | 58.77 |
| **7** | **+28..+31** | **−6.17** | **41.61** ← the cliff |

The loudness step at the boundary is 1.09 dB — inside the noise of every other bar. The
low-band step is 58.8 → 41.6. **The full-energy plateau is 28 beats, 7 bars.**

The song file declares 23. Two consequences:

- **It leaves 5 beats of full-energy drop unused.**
- **23 ≡ 3 (mod 4), so the carousel ends three beats into a bar** — one beat short of the
  bar line at +24. Every other boundary in this system is bar-locked; the end of the video
  is not.

24 lands on the bar line at six bars. 28 uses the whole plateau. 23 is the one nearby
value that does neither.

---

## 5. The track cannot supply an intensity curve, and that is the answer

The obvious way to "make the song congruent with the video" is to take the intensity curve
off the audio: loud beat, big picture. On this track that is not available.

```
loudness across the 23 carousel beats, dB:
-5.0 -4.9 -4.5 -4.3 -4.7 -4.8 -4.5 -4.7 -5.4 -5.3 -5.1 -5.1
-5.9 -5.4 -4.9 -5.1 -5.3 -5.1 -4.7 -4.9 -5.1 -5.1 -4.7

dynamic range: 1.52 dB
```

A mastered four-on-the-floor drop is a **plateau**, by construction. So
`measure_structure.py` refuses to compute a correlation against the authored `intensity`
lists rather than printing a number about nothing — an earlier draft of this study *did*
print one (r = −0.497 against `california-test`), and that number was an artefact of a bad
energy proxy that was reading the spectral noise floor instead of loudness. It is
withdrawn.

This is not a dead end, it is the finding:

> **The song contributes STRUCTURE, not DYNAMICS.** It says where cuts may fall and how
> long a section runs. It has nothing to say about which picture is biggest.

Which means the existing design is right and now has a reason: escalation is hand-authored
per picture and hill-climbed into order *because the music is flat and cannot do it*.
`intensity` is the one axis that must stay a human judgement on this kind of track.

---

## 6. Rhythmic congruency between videos is 1.00 after the drop and 0.36 before it

Jaccard over cut positions, quantised to the beat:

| | pre-drop | post-drop | whole |
|---|---|---|---|
| `california-test` vs `generated-test` | **0.36** | **1.00** | 0.79 |
| `question-reveal` vs `hold-and-break` | 0.18 | — | 0.18 |
| `question-reveal` vs `count-off` | 0.36 | — | 0.36 |
| `question-reveal` vs `slow-push-realize` | 0.25 | — | 0.25 |
| `nocturnal-v1/2/3` against each other | 1.00 | 1.00 | 1.00 |
| `california-test` vs `nocturnal-v1` | 0.06 | 0.12 | 0.09 |

The whole-video figure of 0.79 is itself the point: it is high *because* the 23 shared
post-drop positions outnumber the ~9 disputed pre-drop ones. The number is dominated by
the half of the video that was never in question.

The two `promise-payoff` instances share **every** post-drop cut position and **about a
third** of their pre-drop ones. So the recognisable signature — the thing that reads as
"same channel" — is the **carousel pulse**, not the cadence.

That matters because the spec's claim names the cadence: *visual, beat, line, beat, drop*
is the hook's skeleton, and the hook is precisely the part that is not shared. Either

- the claim is really about the carousel, and cadences are free to differ — which they
  emphatically do, see below; or
- the cadence library needs a **shared rhythmic invariant**, and currently has none.

Worth noting the three `nocturnal` variants sit at 1.00/1.00 with each other. MEME-ENGINE
went to real trouble to make variants differ in their *inputs* rather than their search —
and they differ in clips, order and intensity while being **rhythmically identical**. That
is defensible (the grid is the format) but it is undeclared, and it caps how different
three variants can ever feel.

---

## 7. The seam: four cadences, four different bets, nothing knows it

The one rhythmic quantity a viewer meets head-on is the rate change *at* the drop —
everything either side of it is an average.

| cadence / video | cuts/beat before | cuts/beat after | last gap ÷ first gap | density lift |
|---|---|---|---|---|
| `question-reveal` | 0.538 | 0.957 | **2.00×** | 1.78× |
| `count-off` | 0.500 | 0.957 | **1.00×** | 1.91× |
| `hold-and-break` | 0.286 | 0.957 | **2.00×** | 3.35× |
| `slow-push-realize` | 0.077 | 0.957 | **∞** (single take) | ∞ |
| `nocturnal-v1/2/3` | 0.501 | 0.954 | **1.87×** | 1.91× |

A cadence has no carousel of its own, so the four cadence rows are scored against
`losing-sleep`'s standard 23-beat, one-cut-per-beat carousel — the only one either
`promise-payoff` instance has ever used.

`count-off` is the outlier and its own file says so — *"the pictures were already
switching at this rate"* — because its `hold` slot is one beat, already at carousel rate,
so the drop brings **no acceleration at all**. `slow-push-realize` goes from zero cuts to
one per beat. These are opposite bets about what the drop should feel like, made in
separate files, and no check anywhere is aware the quantity exists.

The meme engine, built by a solver with a 2-beat build grid, independently landed on
1.87× — within 7% of `question-reveal`'s 2.00×. Two systems that share no code agreeing on
a ratio is the strongest evidence in this study that the seam is a real design parameter.

**Regularity inverts across the drop too.** Coefficient of variation of the gaps:

| | before | after |
|---|---|---|
| `question-reveal` | 0.34 | 0.20 |
| `hold-and-break` | **0.59** | 0.20 |
| `nocturnal` | 0.02 | 0.20 |

So the drop is a *rhythmic* inversion as well as a visual one: **sparse and irregular
becomes dense and metronomic.** That is the same figure/ground geometry the format is
built on, running in the time axis, and it is currently undeclared.

---

## 8. Bar position, now that the phase is known

With the drop confirmed as the downbeat, each cadence's cuts fall in the bar like this
(1 = the bar line):

| cadence | bar positions of its cuts | on the bar line |
|---|---|---|
| `question-reveal` | 4, **1**, 2, **1**, 3, **1**, 3, **1** | 50% |
| `count-off` | **1**, 3, **1**, 3, 2, 4, **1** | 43% |
| `hold-and-break` | 3, 2, 4, 3, **1** | 20% |
| `slow-push-realize` | 4, **1** | 50% |

These are not arbitrary and they match what the files say about themselves:

- `question-reveal` was designed on bar logic — its cuts at 12, 8, 4 and 0 are all bar
  lines, and the `line` slot ending on beat 8 is the pre-roll alignment from §3.
- `count-off`'s three tableaux sit on 12/10/8 — bar line, half bar, bar line — and then
  the **snap at beat 3 and hold at beat 1 are deliberately off the bar.** The file calls
  the snap "the pattern breaking"; it is measurably a *phase* break.
- `hold-and-break` puts only the drop itself on a bar line. It is the most syncopated
  cadence in the library, which is a precise statement of what its own file calls
  "the riskier cadence, and it fails harder."

---

## 9. The generating function

What this study says can be computed rather than chosen. Given an audio file and a human
pointing roughly at a drop:

```
beat_ms        = comb fit over the whole track                      measure_beat.py
drop_ms        = largest low-band step near the estimate            ← NOT the nearest
                                                                      transient, and NOT
                                                                      the accent comb
downbeat_ms    = drop_ms, verified by the residue test of §2
riser_beats    = drop − first low-band collapse before it           → 8
pre_roll.bars  = riser_beats / 4                                    → 2
plateau_beats  = first bar whose kick falls > 3× the typical
                 bar-to-bar movement                                → 28
carousel_beats = largest multiple of 4 ≤ plateau_beats              → 28   (declared: 23)
intensity[i]   = NOT derivable — the track is flat to 1.52 dB.
                 Stays authored. See §5.
```

Everything above one line is arithmetic. Everything below it is the joke.

**Candidate rules**, in the form the specs already use — none of these is enforced today:

1. **The drop is a bar line.** `downbeat_ms == drop_ms`, verified by the residue test, not
   by an accent comb. A drop that is not a bar line means the measurement is wrong far
   more often than it means the track is unusual.
2. **`carousel_beats` is a whole number of bars and no longer than the measured plateau.**
   The video must end on a bar line; today it ends three beats into one.
3. **The gain ramp equals the track's own riser.** `pre_roll.bars × 4 == riser_beats`. It
   is currently true by luck and would silently stop being true on a new song.
4. **A cadence declares its seam ratio,** and it is checked against the carousel. 1.00×
   and ∞ are both legitimate and they are opposite videos; neither should be an accident.
5. **The `line` slot ends on a bar line.** True in `question-reveal`, and the reason its
   pre-roll alignment works.

---

## 10. Schema gaps this study ran into

- **`rtf.song/v1` has no `downbeat_ms`.** Without it nothing downstream can tell a bar
  line from any other beat. `measure_pacing.py` currently has to *assume* the drop is the
  downbeat and says so in every report it prints.
- **`rtf.cadence/v1` has no per-slot `transition:`** (`cut` | `move` | `none`). Cut
  density is therefore not derivable from the schema, and this study had to infer it —
  every slot boundary is a cut unless `requires.single_take`. That inference is printed
  with every result rather than hidden inside it, and it is the first thing the field
  would make unnecessary.
- **Nothing relates the hook grid to the carousel grid.** They are on one beat grid by
  construction and no invariant connects them — not the seam, not the phase, not the
  density.

---

## Known limits

- **One song.** Every number about `losing-sleep` is a number about one mastered
  four-on-the-floor track. The *methods* — residue test for phase, low-band cliff for
  section end — should generalise; the constants (2-bar riser, 7-bar plateau, 1.52 dB)
  certainly do not.
- **`LOW_HZ` and the 3× cliff factor are calibrated on one positive example**, the same
  standing `screen_clips.py` and `check_likeness.py` have. Triage, not authority.
- **The pre-drop trains are the cadences' *planned* positions, not the filmed ones.**
  `california-test`'s hook is 6442ms against a 13-beat cadence of 6240ms, and its real cuts
  sit up to 202ms off the grid. `cadence_drift()` already reports that and this study does
  not re-litigate it.
- **Jaccard is a hard-edged measure.** A cut one beat away scores the same as a cut ten
  beats away. It was chosen because it has no window width and no kernel — nothing to tune
  until it says what you wanted — but it understates near-misses.
- **Nothing here says a rhythm is good.** It says two rhythms are the same or different,
  and that a drop lands where the music changes. Whether the result is worth posting is
  the same question it was before.

---

## Running it

```bash
.venv/bin/python bin/measure_pacing.py --all
.venv/bin/python bin/measure_pacing.py california-test generated-test
.venv/bin/python bin/measure_structure.py losing-sleep --against california-test
```
