---
title: "Found Hooks (rtf.brief/v1 → rtf.clip/v1 → rtf.edit/v2)"
subtitle: "Mine somebody else's caption track for a line that makes a promise, cut the clip on the millisecond the sentence ends, and generate a congruent payoff for it."
status: "PROOF OF CONCEPT — 12-source `dialogue` brief, `storytime-test` built end to end"
date: "2026-07-24"
---

## Why this exists

[MEME-ENGINE](MEME-ENGINE.md) builds mood cuts out of silent b-roll. It is cheap and it is
not `promise-payoff`, because a shot of a neon sign cannot make a promise.
[FORMAT-SPEC](FORMAT-SPEC.md) rule 1 is unambiguous about what a hook has to be:

> **The hook is one clip, one line, one legal exit.** It must declare `role.can_lead: true`,
> carry a real `audio.quote`, and its `t_out` must be a `cut_point` of kind `hard_cut`.

So the hook has to *talk*. Both worked instances were filmed or generated to order. This
finds one instead — and then builds the congruent payoff that pays it off.

The result is the full geometry running on footage nobody here shot: a stranger makes the
promise, the drop cuts them off, and a generated carousel answers them in an inverted
world.

---

## The caption track is the whole trick

A transcript says three things, and the third is the one that matters:

| it says | which gives you |
|---|---|
| where the speech is | `t_in` |
| what was said | `audio.quote` |
| **when the sentence stops** | **`t_out` — the only legal place to cut** |

That last one is why this works at all. Cutting a talking clip anywhere except the end of
a sentence reads as broken, and a caption track hands you that boundary to the
millisecond.

It also inverts the search. **The query does not look for the line; the transcript does.**
Captions are a few kilobytes, so every candidate's *entire* transcript is downloaded and
mined, and only the ten seconds around the winning line is ever fetched as video. The
brief's queries only have to return people talking to a camera in English.

### De-rolling

YouTube's auto-captions roll *inside* a cue:

```
00:00:04.560 --> 00:00:07.190
So, I uh I figured it out why hot dogs          <- the previous cue, restated
come<00:00:04.720><c> in</c><00:00:04.960><c> packages</c>   <- the only new words
```

The bare line is the repeat; the inline-timestamped line is the payload, and *its* head
word is new and untimed. Parsing line-by-line re-emits every repeat at the wrong
timestamp, which is how a quote ends up containing the same sentence twice. Two bugs found
by reading the output against the raw file:

- Splitting cues on `\n\s*\n` tears a cue in half, because YouTube writes a **single
  space** as a cue's first body line. That silently dropped the opening sentence of every
  video.
- Entities arrive raw. `&gt;&gt;` is a speaker-change marker, not something anybody said,
  and it landed verbatim in `audio.quote`.

Utterances are then split on **sentence punctuation first, silence second**. Splitting on
pauses alone produced `"So, I uh I figured it"` / `"out"` / `"why hot dogs come in
packages of 10 and"` — and a fragment cannot make a promise.

---

## Choosing the line: regex shortlists, model picks

A regex scorer is good at throwing out junk (sponsor reads, filler, calls to action) and
bad at telling a promise from a sentence that merely looks like one. Its top pick on the
first video was:

> *"where were we oh I remember"* — score **3.88**

which scores well only because it opens with an interrogative. So the mechanical score
shortlists the top eight and a model chooses among them, with the format's own definition
of a hook and explicit permission to refuse. It rejected the line above, and on a
control list of three fragments it correctly returned *none*.

**One bug here was worth more than the model.** The final utterance of every transcript
was being handed a fabricated 5.0s `gap_after`, and the silence term is the heaviest in
the score — so *the last line of every video* won almost automatically, whether or not
anything followed it. Unmeasured now means unrewarded.

And because a caption gap only means *nobody typed a word there*, the tail is measured
acoustically after the cut and recorded, not assumed:

```
storytime      "are you gonna scream?"     tail +4.8 dB quieter   ← real silence to cut into
ever-wondered  "let me see if I can …"     tail −3.5 dB           ← louder after the line
```

That number is reported, not gated on: a hook over a music bed is fine; a hook with
somebody still talking through the drop is not, and the difference is now visible.

---

## What comes out

Each hook is written as a `rtf.clip/v1` descriptor that satisfies rule 1 by construction —
`can_lead: true`, a real quote, gapless beats, and a `hard_cut` at `t_out` — plus a
`hook_recommended` block carrying the pre-roll bar count that actually fits, because rule 5
requires the swell to start *after* the dialogue stops. Where 2 bars will not fit it drops
to 1 rather than moving the cut: **the cut belongs to the line, not to the arrangement.**

All 12 sources produced a clip. **Six produced a line actually worth building on**, which
is the number that matters:

| hook | line | tail | verdict |
|---|---|---|---|
| `interview-answer` | *"Are you smart?"* | −1.0 dB | the shape the format wants, exactly |
| `storytime` | *"are you gonna scream?"* | **+4.8 dB** | a question with real silence after it — built below |
| `morning-vlog` | *"What is this?"* | +2.7 dB | short, direct, cuts clean |
| `explainer-turn` | *"Like, have you ever gotten to the checkout page when you're…"* | +0.9 dB | the *have you ever* shape |
| `never-told` | *"Seriously, what is the rule with the producers here?"* | +1.1 dB | usable |
| `unpopular` | *"What is your opinion on the current state of R&B?"* | +3.6 dB | usable, and on-topic for a label |
| `crowd-work` | *"SCH helps you write some songs"* | +3.2 dB | not a promise |
| `about-to`, `podcast-clip`, `ever-wondered` | — | | fragments; they need the previous sentence |
| `worst-day`, `confession` | — | | grammatical, tonally unusable |

**Finding a good line is the bottleneck, not the plumbing.** That is the honest headline of
this whole pipeline.

Four quotes arrived carrying caption furniture — `&gt;&gt; Are you smart?`, a stray line
number on `71 Seriously, what is the rule…`, `&nbsp;` — which is exactly the kind of thing
that reaches `audio.quote`, the one field the format treats as the spoken line, and is
never noticed because nothing downstream reads English.

---

## The congruent payoff

`scaffold_edit.py` takes one hook descriptor and writes a complete `rtf.edit/v2`: it copies
the clip into the video folder (a video directory is meant to be self-contained), drafts
the congruence and one prompt per beat, then fixes the one thing that is mechanically
fixable.

### You cannot preserve a figure you have never seen

The first version passed the model the spoken line plus two adjectives measured off the
pixels — *"a bright, static shot"*. For `"are you gonna scream?"` it drafted, in confident
detail, a photorealistic living room: an armchair, a coffee table with books, a standing
lamp, a patterned rug, held invariant across 22 images. Eight of them were generated
before anyone looked at the hook.

**The hook is a 2D cartoon on a stark white background.** There is no living room. There
is no photograph. The payoff had nothing whatever in common with the thing it was paying
off, and every mechanical check passed the whole way — because none of them look at
pixels.

The fix is to stop describing the hook and start showing it: three frames are extracted
and attached to the drafting request, with instructions to match the *medium* first. Same
line, same tool, second run:

| | drafted blind | drafted looking |
|---|---|---|
| medium | photorealistic | **2D cartoon, matching line weight and flat colour** |
| invariant | an invented living room | the pink striped shirt, the brown hair, the egg character with its black mask and orange beak, the close-up framing |
| inverted | 5 axes of a room that does not exist | `empty`: stark white → bustling; `threat` → comfort; `still` → dynamic; `flat` → textured; `monochrome background` → richly coloured |

The white void being inverted into a crowded world *is* the joke, and it is only available
to a tool that looked.

### Describing a style is not enough — the style anchor

Drafting while looking at the hook fixed the *plan*: the edit correctly declared "2D
cartoon, simple line art, flat colour" as invariant. The generated pictures still came back
as polished, softly-shaded, semi-realistic illustration. Structurally congruent, visibly a
different medium — the hook and its own payoff did not look like they came from the same
video.

Words describing a style lose to the image model's defaults. The format already solved
this one layer up, for faces: **put the actual frames in front of the model on every
prompt.** So `scaffold_edit.py` now writes the hook's own frames out as an
`rtf.character/v1` file and declares it as the cast, which means `generate_stills.py`
attaches them to all 22 prompts — the same machinery that exists because *"character drift
is the #1 reason AI cutaways don't cut."*

`generate_stills` grew one thing to allow it: the reference label is overridable. Its
default says *"Match this face exactly"*, which is the wrong instruction to give a model
looking at three frames of a cartoon.

| | described | anchored |
|---|---|---|
| medium | polished rendered illustration | **the hook's own flat linework** |
| characters | a striped shirt on several different people | the same two characters throughout |
| refusals | 1 of 22 | 0 of 22 |

That is the difference between a payoff and a mood board, and it cost three reference
images per prompt.

### The reorder pass, and a bug in its scoring

FORMAT-SPEC's escalation rules are enforced by hill-climbing on the running order. Drafted
blind, the model's order failed outright — **lift +0.14 against a floor of +0.50, peak not
in the final third** — and reordering fixed it to +0.86. Exactly the failure the rule
exists to catch: authored in the order the ideas arrived.

But on the second draft the pass made things *worse*, taking a drafted **+1.57 down to
+1.14**. The cost function penalised lift below +0.50 and then stopped caring, so every
order clearing the floor scored identically on that term and the search happily traded lift
away for a fractional gain elsewhere. A floor is not an objective. With a continuous
reward added, the same 22 pictures reorder to **+2.43**.

`render_edit.py storytime-test --check`: **23 segments, drop cut at 4500ms, 22 cuts / 331
frames (11.03s), beat-locked.**

### On likeness — a decision, not a limitation

The speaker in a found clip is usually a real, identifiable person who agreed to nothing.
So the default invariant is deliberately **not** their face: it holds the room, the
objects, the wardrobe and the framing, and people appear only as silhouettes or from
behind. That is enough to read as the same world. `--invariant-person` overrides it; using
it is a choice about a third party and should be a deliberate one.

This particular hook is animation, so the question does not arise for it — the "figure" is
a drawn character and nobody's likeness is being synthesised. It arises immediately for the
others. Looking at three frames from the library:

- `explainer-turn` is a man talking straight to camera. A real, identifiable adult.
- `interview-answer` — *"Are you smart?"*, the best-shaped line in the whole library — is a
  street interview with someone who **looks like they may be under 18.**
- `morning-vlog` is a hand holding a product box, no face at all.

CLIP-SPEC has `people_release` and `minors_in_frame` for exactly this, and the first
version of this tool wrote neither. They are now recorded as `false` and `"unknown"` rather
than omitted, because an absent field reads as *"no minors"* to anyone skimming, and this
tool genuinely cannot tell a 17-year-old from a 19-year-old. Do not hold a likeness
invariant on `interview-answer` without checking who that is.

---

## A second instance: `smart-test`

`"Are you smart?"` — a street interview — built the same way, and it is the case the
likeness question was written for. The subject is a real member of the public who could
plausibly be 16 or 20, and the style anchor works by feeding the hook's own frames into an
image generator 22 times.

So it was built with `--no-anchor`: **his image is never sent to the image model at all.**
An instruction not to reproduce someone is weaker than not showing the model their face,
and the asymmetry of getting it wrong is not close.

The cost of dropping the anchor turned out to be near zero here, for a reason worth
keeping: **the anchor earns its keep when the hook's medium is not the model's default.**
`storytime` is flat 2D cartoon, which the model will not produce unprompted, so it needed
the frames. `smart-test` is a photograph, which is exactly what an image model does by
default — so words were enough, and the medium held.

The invariant becomes the world instead of the person: the beige graphic t-shirt, a
microphone held by a hand at the edge of frame, medium close-up framing, the street
architecture, the photographic medium. Every generated figure is from behind, in
silhouette, or in obscured profile, and none of them is him.

| | `storytime-test` | `smart-test` |
|---|---|---|
| line | *"are you gonna scream?"* | *"Are you smart?"* |
| medium | 2D cartoon | photographic |
| anchor | hook frames on every prompt | **none — deliberately** |
| invariant | the two characters, the drawing style | the t-shirt, the mic, the street, the framing |
| inverted | 5 axes: empty, threat, still, flat, monochrome | 5 axes: intellect, night/cosmic, dynamic, crowded, dramatic |
| lift | +2.57 | +2.43 |
| refusals | 0/22 | 0/22 |

Both: 23 segments, drop cut at 4500ms, 22 cuts / 331 frames (11.03s), beat-locked, 15.53s.

---

## Two gates removed, and what that costs

At your instruction the hard rights gates are gone.

- `render_edit.validate_clip` no longer refuses a clip whose `rights.source` is outside
  `{original, ai, stock_licensed, public_domain}`. It **prints** the source on every run
  instead. A renderer refusing to render was a distribution decision being taken by a
  build step.
- `fetch_hooks.py` / `fetch_library.py` record provenance — query, video id, channel,
  exact window — rather than gating on it.

What that does **not** change: `research/06` found that TikTok's ad policy contains a
written rule barring *"clips from any unauthorized media sources to promote your
product,"* and that Meta pre-screens for infringing IP. Downloading is not publishing, and
this pipeline is now happy to build things it should not run as paid creative. The gate
moved to a human; it did not disappear.

---

## Running it

```bash
.venv/bin/python bin/fetch_hooks.py dialogue
.venv/bin/python bin/scaffold_edit.py lib/hooks/dialogue/storytime.clip.yaml --id storytime-test
.venv/bin/python bin/render_edit.py storytime-test --check
.venv/bin/python bin/generate_stills.py storytime-test          # ~$0.04 per still
.venv/bin/python bin/render_edit.py storytime-test
```

### Auth, which was broken

Every generation tool was dead before any of this could run. They authenticated as Vertex
**express mode** — `AGENT_GCP_KEY` as `x-goog-api-key` against
`aiplatform.googleapis.com/v1/publishers/...` — and that now returns:

```
401 CREDENTIALS_MISSING — "API keys are not supported by this API"
```

The credentials to fix it were already on the machine: an application-default login, plus
`GCP_PROJECT` / `GCP_LOCATION` in `.env` that express mode never used. `bin/vertex.py` uses
the **project-scoped regional** endpoint with an OAuth bearer token, and
`generate_stills.py` and `check_likeness.py` prefer it, falling back to the key path for
anyone whose key still works. Verified on 2026-07-24: `gemini-2.5-flash` (text) and
`gemini-2.5-flash-image` (image). `gemini-2.0-flash` and `gemini-3-pro-image-preview` 404
on this project — do not assume a model exists because it exists elsewhere.

---

## Known limits

- **Line quality is the bottleneck.** ~1 in 3 sources yields a line worth building on, and
  no amount of downstream machinery improves a hook that was never a promise.
- **The model picks; nothing checks it.** There is no ground truth for "is this a good
  hook", and there is no confusion matrix here the way `check_likeness.py` has one.
- **Utterance ends are inferred.** Auto-captions give no explicit word end, so the last
  word of each group gets a nominal 0.35s. `t_out` is therefore accurate to roughly a
  syllable, not to a frame.
- **No face check on the hook.** Nothing verifies a person is even visible in the clip the
  line came from — and one of the twelve turned out to be a cartoon, which nothing in the
  pipeline noticed until a human looked at a frame.
- **Nothing mechanical looks at pixels.** Every check here — beats tiling, cut points,
  axis coverage, escalation — passed while the payoff was a photorealistic room bolted
  onto a cartoon. Structural validity says nothing about whether two halves belong
  together.
- **The payoff is unwatched.** `--check` proves the timeline is legal and beat-locked. It
  cannot tell you the twenty-two pictures are any good.
