# Brand assets

One asset, and the command that makes it.

| File | What it is |
|---|---|
| `thumbnail.svg` | The source. Hand-authored, no build step — the same rule `docs/deck/` and the landing page follow. |
| `thumbnail.png` | 1280×640, rendered from the SVG. Committed because GitHub's social preview, Devpost and the deck all want a raster. |

## Re-rendering

```bash
rsvg-convert -w 1280 -h 640 thumbnail.svg -o thumbnail.png
```

`brew install librsvg` if `rsvg-convert` is missing. **Edit the SVG, never the PNG** — the
PNG is a build output that happens to be checked in.

## Why 1280×640

GitHub's repository social preview is 1280×640. It also survives being cropped to 1200×630
for Open Graph, because everything load-bearing sits inside the hairline frame.

## Type and colour

Both inherited from `docs/deck/remixkit-deck.html` so the deck, the site and this read as
one brand. Tokens are documented in [`docs/web.md`](../web.md) § *Design system* — display
type Futura → Avenir Next, accent `#f5b940`, background `#14121a` → `#0e0c14`.

The fonts are named, not embedded. On a machine without Futura the SVG falls back to
Avenir Next and then Helvetica Neue, which is why the committed PNG is the artifact anyone
consuming this should use.

## What the wording is allowed to claim

The footer reads **"every asset ships its manifest"** rather than anything about signing,
and that is deliberate. `manifest.verify()` checks a canonical hash and that each declared
output carries a well-formed `sha256`; the `signature` field in the schema is reserved and
unused, and there is no C2PA. So the honest claim is that the record of which model made
what travels *inside the bytes* — which is true, and is the thing `/verify` demonstrates.
"Tamper-proof" or "cryptographically signed" would not be.
