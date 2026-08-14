"""The JSON API — a second surface on the same read layer, for a client that is not Jinja.

The console is server-rendered Jinja and htmx, and it stays that way. This package
exists because a React/TypeScript console is being built as a *new* surface beside it,
and a browser application cannot consume `HTMLResponse` full of `<tr>`. It is mounted as
its own application at `/api`, so nothing about the console changed to make room for it.

## The one design decision worth reading

`research.py` already did the read work for all thirteen console views, so the only
question that mattered was whether this package should call it or fork it. It calls it —
but that took a refactor first, and the reason is worth stating because "reuse the
existing layer" was not straightforwardly available.

`research.py`'s functions were **view-shaped, not domain-shaped**. Each returned a
`demo.View` carrying `Col`s, `Section`s, a ten-cell text bar, `"—"` for every absent
value, `"yes"` for every true one, scores pre-formatted as `"0.42"` and timestamps as
`"08-13 14:22"`. Those are correct answers to "what does an operator read". They are
unusable answers for a client: you cannot sort by a score that is a string, cannot
render a date that has no year or zone, and cannot distinguish a missing confidence
from a fact confidently scored em-dash.

The temptation was to write fresh queries here and leave `research.py` alone. That is
the failure mode the whole exercise exists to avoid — two front ends with two copies of
every statement, one of which learns that `status = 'live'` matters while the other goes
on quoting retracted facts at an operator approving a send.

So instead the queries were extracted: each statement now lives in one `research.rows_*`
function and both surfaces call it. The shaping stayed forked, because shaping *should*
be forked — `shapes.py` is this API's answer to the same rows, and its rules are the
opposite of Jinja's on purpose. Two genuinely different derivations (`fleet_agents`'s
four-way state, `thread_progress`) moved into `research.py` as domain logic, because
they are decisions rather than renderings and two surfaces deriving them independently
is two chances to get them backwards.

Dependency direction, held deliberately: `api/` imports `research`, `outreach`, `repo`,
`assets`, `agents`. None of them imports `api`. This package has no SQL in it at all.

## Mounted, not included

`main.py` mounts this as a sub-application rather than including its router into the
console's. That buys one thing and it is the thing that mattered: the two have genuinely
different HTTP contracts and neither should leak into the other.

`routes.require_operator` answers an unauthenticated request with a **303 to `/`**,
because a browser should land on the page that explains what this is rather than on a
wall. `deps.require_operator` answers one with a **401 and a code**, because an API
client following a 303 gets a 200 full of markup and reads it as success. Both are the
same gate; only the refusal differs. Sharing an exception handler between them would
mean discriminating by path prefix inside it, which is a worse-implemented version of
having two applications.

## Versioning

Everything is under `/api/v1`. The version is in the path rather than a header because
the client author reading `docs/reference/api-v1.md` should be able to paste a URL into
`curl` and have it work, and because a URL that appears in a log or a bug report should
say which contract it was speaking.

There is no deprecation policy yet and pretending otherwise would be decoration. When
`v2` exists, `v1` keeps working until somebody checks that nothing calls it.

## Streaming: assessed, and deliberately not built

The brief asked whether an SSE endpoint over `changefeed.py` was cheap to add. It is
not, and the honest answer is to say so rather than ship half of one.

Four things stop it, and the first alone is decisive:

  * **The deployment cannot stream.** This app runs under Mangum in Lambda, and
    `main.py`'s opening line is that nothing in the app knows whether it is under
    uvicorn or Mangum — "which is what keeps *works on my machine* and *works in the
    deployment* the same statement". Mangum buffers the whole ASGI response and returns
    it as one Lambda result; it does not implement Function URL response streaming. An
    SSE endpoint would work perfectly in local development and hang until timeout in
    production. That is precisely the invariant `main.py` exists to protect, and a
    single endpoint that violated it would be worse than no endpoint.
  * **`changefeed.follow` needs two dedicated connections**, and says so: a core
    changefeed occupies its connection in single-row mode for the life of the feed, no
    other statement can be issued on it, and the stream never ends. Its docstring
    states that neither connection may be `db.connect`'s — "because that one is the
    console's". `db.py` holds exactly one connection per container, on purpose, because
    Lambda gives each container one concurrent request and a pool would hold idle
    connections open against a cluster billed by resource unit. An SSE endpoint would
    need two more per connected browser tab.
  * **A core changefeed is a running job, per subscriber.** Two operators with the page
    open is two feeds. The RU cost scales with browser tabs, which is not a shape
    anybody wants to discover from a bill.
  * **The changefeed's contract is a wake, not a payload.** `Wake` deliberately carries
    a tenant and a set of lead kinds and no row, and there is a test asserting it stays
    that way, because the tempting optimisation is how a changefeed becomes an
    at-least-once work queue with no fence. An SSE feed of wakes would tell a client
    "something changed, go and look", which is a poll with extra infrastructure.

**Poll `/api/v1/summary` instead.** It is one round trip for eleven numbers — the same
statement `outreach.counts` runs for the console's nav badges, written as one query for
exactly this reason — and it is what a client needs to decide whether to refetch a
collection. At a few seconds' interval it costs less than a held changefeed and it works
in both execution models.

If streaming becomes worth the cost, the thing to change is the execution model, not
this package: a small always-on process (the fleet worker already is one) holding one
feed and fanning out over WebSockets. That is a real piece of work and it is not this
sub-project.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from rtf_platform.api import actions, errors, reads

#: The versioned application. Mounted at `/api` by `main.py`, so every path below is
#: reachable as `/api/v1/...`.
#:
#: `docs_url`/`redoc_url` are off for the same reason `main.py` turns them off: this is
#: a public URL, and interactive docs on it would be a browsable map of every endpoint
#: to an unauthenticated reader. The written reference is `docs/reference/api-v1.md`.
api = FastAPI(
    title="Respect the Funk — platform API v1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

api.add_exception_handler(errors.Refusal, errors.handle)
# So the API has exactly one error shape. Without this, a handler's refusal and the
# framework's own request-validation failure would arrive in two different envelopes,
# and a client would discover the second one in production.
api.add_exception_handler(RequestValidationError, errors.handle_validation)

api.include_router(reads.router, prefix="/v1")
api.include_router(actions.router, prefix="/v1")

#: Every route on the mounted application, for the surface test. Exported rather than
#: reached for through `api.routes` at the test site so that the test breaks loudly if
#: the assembly above changes shape, instead of quietly enumerating nothing.
ROUTERS = (reads.router, actions.router)
