# B2B landing page

> **The page lives in the app**, at
> [`app/remixkit/ui/templates/pages/landing.html`](../app/remixkit/ui/templates/pages/landing.html),
> served at `/` by `ui/routes.py:landing`. This file is its documentation — the
> positioning, the design tokens, and the rule about the numbers all still bind. The
> wireframe that preceded it is [`docs/wireframe/`](./wireframe/).

One self-contained HTML file. No build step, no framework, no dependencies.

## Why not Next.js

It was considered and dropped. The landing page is static marketing content with no
client-side state, so a React framework buys nothing here and costs a second deploy
target, a second dependency tree, and — per `docs/research/07-cost-model` — a **$20/mo Vercel
seat that is a commercial-ToS requirement, not a usage cost**. That is the entire
always-on floor of the stack, spent on a page that never re-renders.

This follows the precedent already in the repo: `docs/deck/remixkit-deck.html` is a
hand-rolled self-contained HTML file too.

## How it deploys — done

It was a static file; it is now a Jinja template inside the FastAPI app, which is what
this section predicted and cost. The move needed **zero rework**: the page contained no
`{{ }}`, `{% %}`, or `{# #}` to escape, so it was already a valid template.

```python
@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return _render(request, "pages/landing.html")
```

It had to move *into* `app/remixkit/` rather than be served as a separate static site, because the
Dockerfile copies `remixkit/` and `worker.py` and nothing else — a template outside the
package is not in the image.

The one thing that is no longer static: the nav renders **Open console** instead of
**Sign in** when the `rk_session` cookie verifies. That is the page's only Jinja, and it
exists so a signed-in operator is not sent to a login form that would bounce them back.

That keeps the whole product on **one deploy**, which is the stack decision recorded in
`BUILD-SPEC.md` §2 ("Frontend: default server-rendered Jinja + htmx inside the same
FastAPI app") and resolves open decision §13.5.

## How someone gets in

`/` is public and is the only route that renders without a session. The nav's **Sign in**
points at `/auth/login`; everything under `/console` requires `CurrentPrincipal` and so
refuses without one. There is no sign-up — `RK_ALLOWED_EMAILS` is the user table. See
`app/README.md` § *Two front doors*.

## Design system

Inherited from `docs/deck/remixkit-deck.html` so the deck and the site read as one brand:

| Token | Value |
|---|---|
| Background | `#14121a` / `#0e0c14` |
| Panel | `#1c1926` / `#232030` |
| Accent | `#f5b940` (gold) |
| Display type | Futura → Avenir Next |
| Body type | Avenir Next → Helvetica Neue |

Dark-committed by design — it matches the deck, which is the other thing labels see.

## Positioning

The page is aimed at **independent labels who want organic (non-paid) fan UGC**, and it is
built directly on `/research`. The load-bearing constraint, from
`docs/research/13-ugc-adoption`:

> UGC breakout is a lottery, not a mechanism you can operate.

So the page deliberately does **not** claim to cause virality. It claims what the research
supports: for UGC spread specifically, templatability is the mechanism; network position
predicts spread; and when outcomes are lottery-shaped the edge is *more attempts at a lower
cost per attempt* — plus honest measurement of what is genuinely countable.

> **⚠️ The page's framing is narrower than the product (2026-08-03).** RemixKit generates
> whatever media a release needs — performance clips, press and cover stills, announcements
> to camera, lyric cards, voice-over, and templatable backdrops. This page is written almost
> entirely around the last of those, because "the song is the substrate, the template is the
> product" was for a while read as the definition of the product rather than as a finding
> about how sounds spread ([PRODUCT.md](./PRODUCT.md) § *What the product generates,
> precisely*). The factual claims about what the product *makes* have been corrected; the
> hero, the narrative arc and the evidence section are still UGC-first. **Whether to
> reposition the page is a go-to-market decision, not a docs fix — it has not been made
> here.**

Every statistic on the page traces to a sourced claim in `/research` or the deck's source
table. **If you edit a number, re-check its pillar first.** The "we quote the range, not
the flattering end" standard is a differentiator on this page; breaking it costs more than
the copy is worth.

## Known placeholders

- Both CTA `mailto:` links point at `anthonybtedesco@gmail.com`. Swap for a
  label-branded inbox before this is shown to labels at any scale — a personal
  Gmail address undercuts the page's own credibility pitch.
- "RemixKit" is still the working name (`BUILD-SPEC.md` §13.1, unresolved).
- Figures inside the two illustrative figures (variant test, ledger) are structural
  examples, labelled as such in the page copy.
