# The submission deck

Fifteen 3:2 plates for the Devpost project gallery. They are looked at as stills,
in a browser, at whatever size the gallery gives them, with no voice explaining
anything — which is a different job from either of the other two picture surfaces
in this repo:

| | `content/overlays/scenes/` | `content/overlays/slides/` | **here** |
|---|---|---|---|
| What | animations over the take | plates pinned onto a monitor in shot | standalone diagrams |
| Background | transparent | opaque, terminal-shaped | opaque, 2400×1600 |
| Read by | a viewer, with narration | a viewer, at a glance | a judge, in silence |

```bash
python3 render.py            # all fifteen -> out/
python3 render.py 06 10      # just those, by filename prefix
```

`out/` is gitignored, in line with the rule at the top of `.gitignore` — the HTML
is the source of truth and a full render takes about twenty seconds.

## The rules this deck inherits

**Purple is reserved.** `--crdb` appears on exactly two slides — **06** (the filters
living inside the index) and **10** (asking the index what it looked like at the
moment of a decision). Those are the two things nothing else can do. Every other
slide is copper, *including* the slides that are also about the database. If purple
leaks onto a third, the two that matter stop reading as special and the argument of
the deck goes with it.

**Every diagram is polar.** The brand mark is a record read as a radial spectrum, so
there are no rectangular flowcharts. One object recurs — a disc of counterparties in
concentric grooves — established on 02, filtered on 06, orbited on 04 and 08, and
shown twice side by side on 10. That repetition is what lets slide 10 say "the same
thing, earlier" without a caption.

**Status is drawn, not just written.** Anything written-but-not-running is dashed and
unlit: the creator sector on 03, the residency card on 15. A roadmap item that looks
identical to a shipped one is how a deck ends up claiming something a judge can
falsify in one command.

## Why `render.py` fails the build

A caption two characters too long is simply clipped by the viewBox edge, and the
slide still looks deliberate — which is exactly the failure a human reviewer skims
past. So the renderer measures instead:

- every `<text>` node against the `viewBox` it is drawn in,
- the bottom rail, which is one line by design and wraps silently when a caption runs long,
- `.fig`, the one flex child with a height it can exceed (a `min-height: 0` child spills without growing the plate, so the plate-level check misses it entirely),
- and any page error, because a slide whose script threw renders as a half-drawn diagram.

It caught fourteen real overruns across the first pass of these fifteen slides.

**What it cannot check** is text against a box drawn inside the same SVG — a caption
can sit inside the viewBox and still run out of its own card. Those are budgeted by
hand instead: IBM Plex Mono advances at 0.6em, so `t-mono` is 18px a character,
`t-val` is 24px, and `t-lbl` is about 20.5px with its tracking. A card of width *W*
holds `(W - padding) / 18` characters of caption. Slides 05, 07 and 13 carry that
arithmetic in comments beside the strings it constrains.

Only Regular and Medium weights of the face are installed, so nothing here sets a
weight above 500 — 600 gets synthesised and smears at these sizes. Hierarchy comes
from scale and colour.
