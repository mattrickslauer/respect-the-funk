# web — the B2B landing page

Next.js 15 App Router, TypeScript, Tailwind v4. Static export.

```bash
npm install
npm run dev       # http://localhost:3000
npm run build     # -> out/   (static, no server)
```

## Who it is for

Labels and artist managers, per [PRODUCT.md](../PRODUCT.md). One audience, one action:
bring a roster, generate content for the catalogue. There is no fan-facing surface here —
that is BUILD-SPEC §1 role 2 and it is deferred.

## Two rules the copy follows

**1. Every figure carries its source.** The numbers on this page — 96% of creations to the
top 10% of songs, ~50× loss buying streams, 663 Facebook RCTs, $53,088 per FTC violation —
all trace to [`research/_synthesis/SYNTHESIS.md`](../research/_synthesis/SYNTHESIS.md),
which carries the primary-source link for each. This is not fastidiousness for its own
sake: §9 of that synthesis records three widely-repeated industry figures that **do not
exist where they are attributed**, one of which ("$0.02–0.05 per stream", traced to an AI
content farm, real figure 10–40× worse) would have set the entire budget. A page selling
rigour to labels is the last place to repeat that pattern.

**2. The guardrail is on the page, not buried.** We say in the hero that we do not claim
to make a song go viral, and there is a whole section listing what we will not sell. In a
field of "our AI makes you go viral" pitches, the modesty *is* the differentiator — and
it is the only claim the evidence actually supports.

## Design

Palette is lifted verbatim from `deck/remixkit-deck.html` so the site and the investor
deck read as one brand; the `s1`–`s5` accents are that deck's already-validated
categorical series rather than a second, unvalidated set invented here.

No external fonts, no external scripts, no analytics — the page has no network
dependencies at all, which is also why it exports statically.

## Deploying

`output: "export"` in `next.config.ts` produces a plain directory in `out/`. It has no
server-side work to do, so it wants a bucket and a CDN rather than a Node process.

Consistent with [`infra/README.md`](../infra/README.md): put it in B2 behind
**Cloudflare**, not CloudFront — Backblaze's free-egress arrangement is with Cloudflare,
so that path costs $0 in bandwidth where CloudFront would bill B2 egress.

Drop `output: "export"` when the authenticated app (artists, generation, review) lands
alongside the landing page and needs a server.

## Not built yet

The `Add your artists` / `Request access` CTAs are a `mailto:` today. The authenticated
product — artist CRUD, identity builder, catalogue, generation queue, review and approve —
is the next branch, and it is what `infra/architecture.pdf` describes.
