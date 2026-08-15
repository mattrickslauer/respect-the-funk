# Brand assets

The mark, and the commands that make it.

| File | What it is |
|---|---|
| `thumbnail.svg` | The source. Hand-authored, no build step — the same rule the landing page follows. |
| `thumbnail.png` | 1280×640, rendered from the SVG. Committed because GitHub's social preview, Devpost and the deck all want a raster. |
| `mark.py` | Emits the 64-bar radial group inside the SVG. The one part that is a formula rather than a drawing. |

## Re-rendering

```bash
rsvg-convert -w 1280 -h 640 thumbnail.svg -o thumbnail.png
```

`brew install librsvg` if `rsvg-convert` is missing; on Fedora it is `librsvg2-tools`.
**Edit the SVG, never the PNG** — the PNG is a build output that happens to be checked in.

Two renderer notes, both learned the hard way on 2026-08-15:

- **Do not render this with `cairosvg`.** It resolves every `font-family` to the same
  fallback regardless of what the chain asks for, so the wordmark comes out in whatever
  sans it feels like and the PNG silently stops being the mark. librsvg honours the chain.
  If `rsvg-convert` is unavailable but the library is, `Rsvg.Handle` through GObject
  introspection is the same engine.
- **`font-family` is set on each `<text>`, not on the wrapping `<g>`.** librsvg does not
  inherit it from the group here. Moving it back up to the `<g>` will render, and will
  render wrong.

## The mark

A record read as a spectrum: grooves inside, waveform radiating out, copper label at the
centre. It is the whole image on purpose — the thumbnail is a logo, not a slide.

The centre hole carries a light rim, which is the one piece of the drawing that exists
because of the name: a spindle is the post the record turns on, and without the rim the
hole reads as a dot rather than as something a post goes through.

The 64 bars are the one part nobody should hand-edit. They come from three summed cosine
harmonics, and the symmetry is the reason for the choice: `cos` is even, so a bar's height
depends only on its angular distance from vertical and the left half mirrors the right.
Nudging individual rects would only make that drift. To change the geometry, edit the
constants in `mark.py`, run it, and paste the group back:

```bash
python3 mark.py            # prints the <g transform="translate(640, 274)"> group
```

Everything else in the SVG is hand-authored and the script does not touch it.

## Why 1280×640

GitHub's repository social preview is 1280×640. It also survives being cropped to 1200×630
for Open Graph, because the mark and the wordmark sit well inside the centre.

## Type and colour

The tokens are of record in
[`platform/web/spindle/templates/_design.html`](../../platform/web/spindle/templates/_design.html),
which is the single source for the running product; this mark tracks it rather than the
other way round. Display type Futura → Avenir Next, accent copper `#d97f4a`, ground
`#14120f` → `#0b0a09`.

Copper is not decoration and not a taste call. `_design.html` runs a two-pole colour
system in which warm is a thing a hand has touched and cold is a thing that has settled
into evidence, and the two hues are copper and verdigris because a stamper is
electroplated in copper and copper left alone becomes verdigris. The mark uses only the
warm pole. One hue on a logo, two in the instrument.

The fonts are named, not embedded. On a machine without Futura the SVG falls back to
Avenir Next, then URW Gothic, then Helvetica Neue — URW Gothic is in the chain because it
is the geometric face that is actually present on Linux, and without it a Linux render
silently drops to a humanist sans and changes the mark's character. The committed PNG
remains the artifact anyone consuming this should use.

## What it says, and what it deliberately does not

Two lines of text: the wordmark `Spindle` and the tagline
`THE TOOL THAT MAKES YOUR MUSIC GO ROUND`. An earlier version carried a tagline, the five
formats and a provenance line, which made it a slide rather than a mark and left it
unreadable at the size a repo listing actually shows. One tagline is the ceiling.

Until 2026-08-15 the wordmark read `RemixKit` over the line `RESPECT THE FUNK`, which was
wrong in two directions at once after [`docs/SCOPE-RESET.md`](../SCOPE-RESET.md): RemixKit
had been demoted to a subproject, so the logo branded a component as the whole product,
and *Respect the Funk* is the name of the label — the first tenant — so the product's mark
was wearing a customer's name. The rebrand separates the three: **Spindle** is the
platform, **Respect the Funk** is the label that runs on it, **RemixKit** is the asset
generator inside it. That separation is also why the tagline is not a second name.

That version also claimed **"every asset ships its manifest"**, and it is worth recording
why nothing replaced it here rather than simply shortening it. `manifest.verify()` checks
a canonical hash and that each declared output carries a well-formed `sha256`; the
schema's `signature` field is reserved and unused, and there is no C2PA. So the strongest
honest claim is that the record of which model made what travels *inside the bytes* —
true, and what `/verify` demonstrates, but it needs a sentence of qualification to not
overstate. A logo has no room for the qualification, so the claim belongs in the README
and the deck where it can be made properly, and the mark stays a mark.
