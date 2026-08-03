# Brand assets

The mark, and the commands that make it.

| File | What it is |
|---|---|
| `thumbnail.svg` | The source. Hand-authored, no build step — the same rule `docs/deck/` and the landing page follow. |
| `thumbnail.png` | 1280×640, rendered from the SVG. Committed because GitHub's social preview, Devpost and the deck all want a raster. |
| `mark.py` | Emits the 64-bar radial group inside the SVG. The one part that is a formula rather than a drawing. |

## Re-rendering

```bash
rsvg-convert -w 1280 -h 640 thumbnail.svg -o thumbnail.png
```

`brew install librsvg` if `rsvg-convert` is missing. **Edit the SVG, never the PNG** — the
PNG is a build output that happens to be checked in.

## The mark

A record read as a spectrum: grooves inside, waveform radiating out, gold label at the
centre. It is the whole image on purpose — the thumbnail is a logo, not a slide.

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

Both inherited from `docs/deck/remixkit-deck.html` so the deck, the site and this read as
one brand. Tokens are documented in [`docs/web.md`](../web.md) § *Design system* — display
type Futura → Avenir Next, accent `#f5b940`, background `#14121a` → `#0e0c14`.

The fonts are named, not embedded. On a machine without Futura the SVG falls back to
Avenir Next and then Helvetica Neue, which is why the committed PNG is the artifact anyone
consuming this should use.

## What it says, and what it deliberately does not

Two words of text: the wordmark and `RESPECT THE FUNK`. An earlier version carried the
tagline, the five formats and a provenance line, which made it a slide rather than a mark
and left it unreadable at the size a repo listing actually shows.

That version also claimed **"every asset ships its manifest"**, and it is worth recording
why nothing replaced it here rather than simply shortening it. `manifest.verify()` checks
a canonical hash and that each declared output carries a well-formed `sha256`; the
schema's `signature` field is reserved and unused, and there is no C2PA. So the strongest
honest claim is that the record of which model made what travels *inside the bytes* —
true, and what `/verify` demonstrates, but it needs a sentence of qualification to not
overstate. A logo has no room for the qualification, so the claim belongs in the README
and the deck where it can be made properly, and the mark stays a mark.
