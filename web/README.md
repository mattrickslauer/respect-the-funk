# web/ — B2B landing page

One self-contained HTML file. No build step, no framework, no dependencies.

## Why not Next.js

It was considered and dropped. The landing page is static marketing content with no
client-side state, so a React framework buys nothing here and costs a second deploy
target, a second dependency tree, and — per `research/07-cost-model` — a **$20/mo Vercel
seat that is a commercial-ToS requirement, not a usage cost**. That is the entire
always-on floor of the stack, spent on a page that never re-renders.

This follows the precedent already in the repo: `deck/remixkit-deck.html` is a
hand-rolled self-contained HTML file too.

## How it deploys

Today it is a static file — open it, or serve it from anywhere.

When the FastAPI app from `BUILD-SPEC.md` §2 exists, this drops in as a Jinja template
with **zero rework** (it is already valid Jinja — there is no `{{ }}` or `{% %}` syntax to
escape):

```python
templates = Jinja2Templates(directory="web")

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
```

That keeps the whole product on **one deploy**, which is the stack decision recorded in
`BUILD-SPEC.md` §2 ("Frontend: default server-rendered Jinja + htmx inside the same
FastAPI app") and resolves open decision §13.5.

## Design system

Inherited from `deck/remixkit-deck.html` so the deck and the site read as one brand:

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
`research/13-ugc-adoption`:

> UGC breakout is a lottery, not a mechanism you can operate.

So the page deliberately does **not** claim to cause virality. It claims what the research
supports: templatability is the mechanism, network position predicts spread, and when
outcomes are lottery-shaped the edge is *more attempts at a lower cost per attempt* — plus
honest measurement of what is genuinely countable.

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
