# The picture track

The demo video is in two halves. The first is filmed — the founder, on camera, reading
the cold open. **This directory builds the second half**: the generated read of the rest
of [`docs/demo-voiceover-v2.txt`](../../../docs/demo-voiceover-v2.txt), cut to the
console's own screenshots and to the real assets a real run produced.

**v2 is the script that ships.** v1 claimed a catalog index landing "as Parquet in the
same bucket, queryable by Athena", and there is no Parquet and no Athena anywhere in the
app. [`demo-voiceover-v2-audit.md`](../../../docs/demo-voiceover-v2-audit.md) checks every
claim in the script against the code, one line at a time, and is what v2 was written
from. v1 is kept beside it as the record of what the first cut said.

```bash
python content/demo/video/build.py                 # everything
python content/demo/video/build.py --preview       # 1 frame/second, ~2 min, for looking
python content/demo/video/build.py --from 40 --to 52 --force   # recut one stretch
```

Output is `out/remixkit-demo-visuals.mp4` — 1920×1080, 30fps, 146.9s, narration muxed in.
Put the on-camera intro in front of it.

## Why it is built rather than edited

The same reason the wireframe next door is generated rather than drawn: a hand-cut
timeline goes stale silently. Re-record one sentence of the voice-over and every cut
after it is wrong, and nothing tells you — the video just drifts out of sync with itself.

So the edit has no timecodes in it. `vo.py` asks ElevenLabs for the audio **and** the
character-level alignment in one request, and every cue in `render.html` is a phrase
looked up against that alignment:

```js
const tBack = CUE("Or a backdrop");     // fires the frame that word is spoken
```

Re-cut the script, re-run `vo.py`, and the whole edit re-times itself. A phrase that no
longer matches is a loud `CUE MISS` on stderr and a non-zero exit, not a highlight that
quietly renders at t=0.

## The three steps

| Step | File | Notes |
|---|---|---|
| Voice | `vo.py` | One request. Writes `out/narration.mp3` + `out/alignment.json`. Skips itself if the script has not changed. |
| Picture | `render.html` + `capture.js` | `SEEK(t)` puts the stage at time `t`; capture screenshots 1/30s steps. |
| Encode | `build.py` | ffmpeg, CRF 16, `yuv420p`, audio muxed. |

**The renderer has no clock.** No CSS animation, no `requestAnimationFrame` — every
property is computed from `t` inside `SEEK`, which also waits for every b-roll frame it
swapped to finish decoding before it resolves. That is what makes the output reproducible:
the same commit produces the same frames on a busy laptop and an idle one, and an edit to
one scene only changes the frames in that scene.

Frames are kept on disk rather than piped into ffmpeg, so a crashed run resumes and a
one-scene fix costs seconds instead of the whole 147.

## The character budget is the real constraint

The ElevenLabs account is the **free tier: 10,000 characters, lifetime**. One read of the
script is 2,212 of them. `vo.py` prints the balance every run and refuses to spend
anything when `out/narration.mp3` already matches the current script — pass `--force`
only when you mean it. After the v2 read, **4,339 are spent and 5,661 remain** — two more
full reads. Budget for that before editing the script casually.

That is why `out/narration.mp3` and `out/alignment.json` are committed against the
repo's "media is derivable" rule. They are not derivable; they are purchased.

## What is on screen

Every screenshot is the real console and every clip is a real asset with a manifest
behind it — nothing here is a mock-up of a feature that does not exist.

| Beat | Source |
|---|---|
| register · consent gate | `screenshots/02-roster.png` — the green chip and the blocked artist beside it |
| identity, built once | `screenshots/04-identity.png` — the reference frame, the character budget, the anti-drift negatives |
| measurement | `screenshots/06-song.png` — 79.97 BPM and the method string that produced it |
| two vendors, one run | drawn — `bria-fibo-edit` 4¢ → first frame → `sora-2` 80¢, priced at $2.52, and the ceiling that refuses |
| the four formats | `screenshots/07-generate.png` — each card highlighted as it is named |
| what each format made | `broll/*.mp4`, `broll/*.png` — sora-2 and bria-fibo-edit output |
| the receipt | `screenshots/08-kit.png` provenance panel |
| the disclosure | `screenshots/09-verify.png` |

Highlight boxes are in the screenshot's own pixel coordinates, so they survive any camera
move. The format-card grid was measured off the gold selection ring that is already in
`07-generate.png` rather than estimated — see the comment above `CARDS`.

## Brand assets

`assets/backblaze-wordmark.svg` is Backblaze's own header logo, lifted unmodified from
their site. `assets/backblaze-wordmark-on-dark.svg` is the same file with the letterforms
switched from `black` to `#ffffff` — the flame keeps its `#E20626`. That is the variant
the site itself renders on dark backgrounds; on our near-black stage the unmodified file
is invisible.

## The claim that was cut

v1's catalog line said the index lands "as Parquet in the same bucket, queryable by
Athena". It does not — that appears in `infra/diagram.py` and `BUILD-SPEC.md`, which are
a drawing and a plan, and nowhere in `app/`. v2 claims only what the run prefix actually
does, and the catalog scene says *"the index is the prefix itself"* instead of showing a
warehouse that was never built. The full pass is in
[`demo-voiceover-v2-audit.md`](../../../docs/demo-voiceover-v2-audit.md).
