# The agentic console

A React/TypeScript client for the platform API. **Additive, not a replacement**:
the thirteen Jinja views in `platform/web` keep working and are not ported here.

## Running it

The console needs the Python app running, because it proxies `/api` to it.

```bash
# terminal 1 — the API, against the real cluster (read-mostly, watched)
cd platform/web && ./dev.sh            # http://127.0.0.1:8099

# terminal 2 — the console
cd platform/console && npm install && npm run dev   # http://127.0.0.1:5180
```

You need an operator session for anything to load. Sign in at
`http://127.0.0.1:8099/signin` first; the cookie is same-origin and the dev proxy
keeps it.

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server, proxying `/api` to `:8099` |
| `npm run build` | Type-check and build to `dist/` |
| `npm test` | Vitest, once |
| `npm run check-tokens` | Fails if the generated stylesheet is stale |

## The three surfaces

- **Now** — the proposal stream. Everything asking something of a person, each
  item carrying its reasoning one keystroke away and its controls in reach.
- **Campaigns** — a campaign drawn as the signal chain it is: shortlist, open,
  draft, approve, send. Pin, veto, re-run from a stage.
- **New** — state a goal and see what would run, where it stops for you, and which
  stages cannot currently do anything, before the campaign exists.

Everything else — Facts, Queue, Statements, Fleet, Budgets — is in the classic
console, linked from the rail.

## Two rules worth knowing before you edit

**Do not add a colour.** The palette lives in
`platform/web/rtf_platform/templates/_design.html`, which the landing page and the
operator manual also include. `npm run sync-tokens` generates
`src/styles/design.generated.css` from it before every `dev` and `build`, and the
generated file is gitignored so it cannot drift across a checkout. If a rule needs
a hue that is not already a token, the token is missing from the system and belongs
there. Four palettes had already diverged in this repo before that file existed;
that is the failure this arrangement exists to prevent.

**Do not fall back.** `src/api/client.ts` sorts failure into five kinds — offline,
signedOut, missing, refused, broken — and each gets its own sentence and its own
next step. Nothing returns an empty array when it could not get data. The reason is
specific: a surface that renders "nothing needs you" because an endpoint 404'd tells
an operator their queue is clear when it is unknown, and this console exists so they
do not have to check the other screens. There is a test for exactly that.

## Current state

The client is built against `/api/v1`. Two endpoints it wants do not exist yet:

- `GET /v1/today` — the proposal stream. Until it lands, **Now** renders "that part
  of the API is not built yet", which is the correct thing for it to say.
- `POST /v1/campaigns` — campaign creation, which **New** needs to commit a plan.

Nothing serves the built assets yet; `vite.config.ts` sets `base: "/console/"` in
anticipation of a static mount at that path.

`src/api/types.ts` is where every assumption about the wire lives, deliberately, so
reconciling with the API is one file rather than a search.
