# Demo overlays — founder to camera

Twenty transparent-background cards to composite over the founder speaking, one per beat
of [`docs/demo-voiceover.txt`](../../../docs/demo-voiceover.txt).

Generated, not hand-made. The source of truth is
[`content/lib/overlays/demo-founder.overlays.yaml`](../../lib/overlays/demo-founder.overlays.yaml)
— edit that and re-render, don't retouch the PNGs:

```sh
python3 content/bin/generate_overlays.py content/lib/overlays/demo-founder.overlays.yaml
```

## What's here

| File | What it is |
|---|---|
| `NN-slug.png` | RGBA still, the card fully built. Universal — drop it on a track and animate it yourself. |
| `NN-slug.webm` | VP9 with alpha. Animated: build-in, hold, fade. Plays in browsers and OBS. |
| `manifest.yaml` | Every card's running order, script line, spoken cue, timing, and sha256. |

The number prefix is the running order. `manifest.yaml` carries the `cue` — the words each
card lands on — so an editor places graphics by ear against the read, not by stopwatch
against a cut that will move.

## Which file to use

**Premiere, Resolve, Final Cut → render the ProRes.** These NLEs do not read alpha out of
a VP9 WebM. The `.mov` files are not committed because 4 seconds of ProRes 4444 at 1080p
is ~33 MB a card, which is 660 MB of binary for something reproducible in two minutes:

```sh
python3 content/bin/generate_overlays.py content/lib/overlays/demo-founder.overlays.yaml \
    --formats mov
```

**Browser, OBS, ffmpeg, After Effects → use the WebM.** ~120 KB each, alpha intact.

**Anything else → use the PNG.** A still always works, and for a card that just needs to
sit there for four seconds it is often the simpler edit.

Note that `ffmpeg`'s *native* VP9 decoder drops the alpha channel — a WebM that looks
opaque when you probe it is usually fine. Decode with `-c:v libvpx-vp9` to see the alpha:

```sh
ffmpeg -c:v libvpx-vp9 -i 06-ninety-six.webm -pix_fmt rgba frame%03d.png
```

## Compositing

```sh
ffmpeg -i founder.mp4 -c:v libvpx-vp9 -i 01-invisible.webm \
       -filter_complex "[0][1]overlay=0:0:enable='between(t,4,8)'" out.mp4
```

The overlay is full-frame with a transparent surround, so it composites at `0:0` — no
positioning maths. Placement is baked in by the spec's `placement` field, which keeps
every card in a lower third or a side panel and out of the middle of the frame where a
centred talking head lives.

## Other aspect ratios

The set is authored 1920×1080. Every layout is written in relative units, so a vertical
cut is one flag:

```sh
python3 content/bin/generate_overlays.py content/lib/overlays/demo-founder.overlays.yaml \
    --aspect 9:16 --out content/videos/demo-founder-9x16
```

`16:9`, `9:16`, `1:1` and `4:5` are supported.
