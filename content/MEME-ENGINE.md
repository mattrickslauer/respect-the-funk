---
title: "Meme Engine (rtf.brief/v1, rtf.memeplan/v1)"
subtitle: "A found-footage library and a measured song, cut to one beat grid by a solver instead of a person. The cheap half of generate-once-remix-infinitely, built end to end."
status: "PROOF OF CONCEPT — 15-clip `nocturnal` library, 3 rendered variants against `losing-sleep`"
date: "2026-07-24"
---

## Why this exists

[FORMAT-SPEC](FORMAT-SPEC.md) describes a format whose hook is a person saying a line and
whose payoff is 22 generated pictures. It works, and it costs a day of authoring and a
generation bill per video. That is the right shape for the flagship and the wrong shape
for volume — and [SYNTHESIS](../research/_synthesis/SYNTHESIS.md) is unambiguous that
volume is the only move: *outcomes are lottery-shaped, so the winning play is more
tickets at a lower cost per ticket, with the plumbing correct so a win pays.*

This is the low-cost ticket printer. A **library** is fetched once. After that a video is
ffmpeg and a solver: **no model call, no per-video cost, seconds of CPU.** That is
[BUILD-SPEC §7b](../BUILD-SPEC.md)'s unit-economics claim made literal one layer down —
the expensive step is per-library, the cheap step is per-post.

It is deliberately *not* a second creative format. It is the same geometry — hold the
figure, invert the ground — applied to footage nobody in this project shot.

---

## Three tools, three jobs

```
lib/briefs/nocturnal.brief.yaml        what the library has to contain
        │  fetch_library.py            resolve → download → pick one shot → normalise
        ▼
lib/stock/nocturnal/*.mp4 + *.clip.yaml     the library. Fetched once.
        │  screen_clips.py             reject somebody else's captions
        ▼
        │  meme_engine.py              + lib/songs/losing-sleep.song.yaml
        ▼
videos/nocturnal/nocturnal-v{1,2,3}.mp4 + .plan.yaml
```

| | Brief | Library clip | Song | Plan |
|---|---|---|---|---|
| Answers | *what should this library contain?* | *what is in this shot, measured?* | *where are the beats?* | *what did the engine decide, and why?* |
| Written by | a person with a mood | `fetch_library.py`, off the pixels | `measure_beat.py` | `meme_engine.py`, per variant |
| Changes | per library | never after fetch | once per track | every render |

---

## What the fetcher actually does

Downloading is the easy part and not the interesting one. Three decisions are:

**1. It picks one continuous shot.** A 150-second download is not a clip, it is a reel
containing several. Cutting a 480ms beat across somebody else's cut reads as broken no
matter how good the grade is. So a motion curve is sampled at 8fps, segmented at its own
discontinuities — those spikes *are* the source's cuts — and the chosen window never
crosses one. The threshold is adaptive, because a locked-off night plate and a handheld
crowd shot have motion floors an order of magnitude apart.

**2. It reframes on the motion, not on the centre.** A 16:9 source centre-cropped to 9:16
throws away 66% of the width, and the subject is regularly not in the third that survives.
The crop x is the centroid of column-wise motion energy over the chosen window — free,
because those frames are already decoded.

**3. It measures the ground.** Every clip gets `light`, `motion`, `temp`, `scale` off the
pixels, plus an `intensity_raw`. This is what makes the running order solvable later
instead of a taste argument at 2am.

The source is **recorded, not gated** — `provenance` carries the query, video id, channel
and the exact window taken. Downloading is not publishing. Before any of this becomes
*paid* creative, note that `research/06` found TikTok's ad policy contains a written rule
barring "clips from any unauthorized media sources to promote your product." That is a
publishing decision, and this file does not make it for you.

---

## Screening: somebody else's lower-third

The first render put a frame reading **"FAN SETTINGS / TURNING CLOCKWISE"** in the middle
of the music video — the top search hit for "ceiling fan spinning" was an explainer. That
is the single most visible way a found-footage library fails, and no amount of grading
recovers it.

OCR is the obvious fix and the wrong one: it needs a model, it misses logos and UI, and it
fires on legitimate signage — a neon sign reading CLOSED is *exactly* the shot we want.

The signal that actually works is temporal: **an overlay is pixel-locked while the scene
moves.** Per-pixel temporal variance ∧ per-pixel edge energy → the mask of things that are
strongly edged and completely still.

That alone was not enough. Its first false positive was `ocean-night` — a full moon over
moving water, locked and edged and entirely legitimate. So a second test asks whether the
masked pixels are *drawn*: type has horizontal strokes, vertical stems and rectangular
plates, so its gradients pile onto the two axes, while a moon is a circle and spreads
across every direction.

| clip | locked-and-edged | axis-aligned | verdict |
|---|---|---|---|
| `ceiling-fan` (explainer) | 0.0300 | **0.83** | overlay |
| `ocean-night` (a moon) | 0.0157 | 0.65 | clean |
| `rain-window` | 0.0076 | 0.60 | clean |
| eleven others | ≤ 0.0001 | 0.00 | clean |

**Both thresholds are calibrated against one positive example, so this is triage and not
an authority** — the same standing `check_likeness.py` has one layer up. It also abstains
rather than guesses: in a locked-off shot every pixel is still, so an overlay is
indistinguishable from a wall, and three clips are marked `unscreenable` and left to the
eye. Flagged clips are dropped and their video ids blacklisted, so the refill cannot
re-download the tutorial it just rejected.

---

## The geometry, mechanically

Same two halves as [FORMAT-SPEC](FORMAT-SPEC.md), with different things holding them.

**The figure — what never varies inside a video:** one grade, one aspect, one cut grid,
hard cuts only, one white flash. Plus one thing the hand-built format never needed: a
**per-clip exposure match**. Fifteen sources graded identically still cut like fifteen
videos when one plate sits at 0.07 luma and the next at 0.45. Solving eq's gamma per clip
toward a common target is what lets a *shared* look mean anything at all. The match is
normalisation; the look is the figure.

**The ground — what is made to invert:** the four measured axes. And because they are
measured, they are enforceable:

- **Every axis must be hit.** The first version failed this silently: raw axis values are
  not comparable, and across a night library `light` and `motion` use the whole 0–1 range
  while `temp` lives inside 0.43–0.69. Ranking by raw distance from centre let light and
  motion lead every slot, and `temp` was declared, measured, and structurally impossible
  to use. Standardising each axis against the library's own spread fixed it: **3/4 → 4/4.**
- **No axis may lead more than twice running**, or the carousel is a mood board.

---

## The grid

One grid for the whole video, counted from the drop. Positions are absolute and durations
are their differences, so rounding never accumulates.

```
 build: 4 bars, a cut every 2 beats        drop           post: 23 beats, a cut every beat
├──┬──┬──┬──┬──┬──┬──┬──┤                   ┃  ├─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┤──┤
0                    7.67s                  ⚡                                          end card
```

For `losing-sleep` (125 BPM, 480ms beat, drop measured at 124784ms): 30 slots, 562 frames,
**18.73s**, audio taken from 117.104s of the master. The drop lands 7.680s into the video
and the flash sits at frame 230 = 7.6667s — **13ms early, under half a frame at 30fps**,
and entirely an artefact of quantising to the frame grid rather than a timing error.

---

## Running order is solved, not authored

Both hand-built videos failed the escalation check the moment it was turned on, at +0.29
against a floor of +0.50, because each had been authored in the order the ideas arrived.
Order is free to change, so there is no reason to accept that.

Each slot carries an intensity target that climbs through the build, climbs harder after
the drop, peaks at ~85% of the post-drop section, and steps down for the end card — the
end card is the release, not the climax. Clips are fitted greedily, then hill-climbed on a
cost where axis runs and adjacent repeats are priced high enough to act as hard
constraints.

**Variants have to differ in their inputs, not their search.** Re-seeding the hill-climb
alone produced three near-identical edits — with five intensity levels over fifteen clips
the target curve nearly determines each slot, so every seed walked to the same optimum.
Each variant now casts from a different 80% of the library and chases a slightly jittered
curve, with a guard that never drops the only clip leading an axis.

Measured over three variants of `nocturnal`:

| | v1 | v2 | v3 |
|---|---|---|---|
| axes hit | 4/4 | 4/4 | 4/4 |
| max axis run | 2 | 2 | 2 |
| lift (floor +0.50) | **+2.10** | **+2.10** | **+1.90** |
| peak slot | 28/30 | 27/30 | 27/30 |

---

## Rules

Reported by `meme_engine.py` on every render, and by `--check` before spending anything.

1. **The drop lands on a cut.** The grid is counted from it; nothing may move it.
2. **Every declared axis is hit, and none leads more than twice running.**
3. **The carousel escalates** — last third out-averages the first by ≥ 0.5, peak occurs in
   the final third, end card below the peak.
4. **No two adjacent slots share a clip or a move.** Either reads as one long take across
   the cut, which is the one thing a title sequence must never do.
5. **A slot is exactly as long as the grid says.** The frame-count trim is the last filter
   in every chain, whatever `zoompan` did to timing before it.
6. **A clip used more than once never shows the same window twice.**
7. **`screen.usable: false` clips do not enter an edit.**

---

## Known limits

- **The screener is calibrated on one positive.** See above. Look at the contact sheet.
- **Three clips are `unscreenable`** and were cleared by eye, not by the tool.
- **Search quality is not controlled.** `club-crowd` asked for bodies on the beat and
  returned floodlights; `walking-alone` returned a parked van. Both are visually fine and
  neither is what the brief asked for. There is no check that a clip matches its `wants`.
- **`intensity` is a proxy.** It is motion, saturation, contrast and detail — not comedy,
  not meaning. It orders a sequence; it cannot tell you the sequence is worth posting.
- **No hook.** This format has no spoken promise, so it is not `promise-payoff` and does
  not pretend to be. It is a mood cut that lands on a drop.

---

## Running it

```bash
.venv/bin/python bin/fetch_library.py nocturnal              # once
.venv/bin/python bin/screen_clips.py lib/stock/nocturnal --reroll
.venv/bin/python bin/fetch_library.py nocturnal              # refill what was dropped
.venv/bin/python bin/meme_engine.py --library lib/stock/nocturnal \
    --song losing-sleep --out videos/nocturnal --variants 3 --check
```

`bin/yt-dlp` is a pinned standalone binary on purpose: YouTube extraction breaks every few
months, the homebrew build was 16 months stale and failed outright, and the newest release
needs a Python this venv does not have.

---

## What this deliberately does not model

- **A hook.** One promise in one line is [FORMAT-SPEC](FORMAT-SPEC.md)'s job.
- **Chronology.** Any two slots may swap; only the end card is positional.
- **Captions or lyric burn-in.** One end card, and that is the whole of it.
- **Transitions.** Hard cuts and one white flash. Dissolves smear across the beat.
- **A virality claim.** More tickets, cheaper. Nothing in here makes one win.
