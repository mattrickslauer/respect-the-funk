# Platform API v1

The JSON surface of the platform. Everything is under `/api/v1`.

It was built for the React console at `platform/console`, which was removed on
2026-08-15. The API was deliberately kept: it is independently tested, it is the only way
anything scripts this system, and nothing about it depended on that client.

This document exists so the client author does not read `spindle/api/*.py` to guess
shapes. Where it says a field can be `null`, it can be `null` and the client has to mean
something by it — the API never substitutes an em-dash, a zero or an empty string for a
value that is absent.

**Source of truth.** The endpoint list and every refusal code are checked against the
code by `platform/web/tests/test_api_surface.py`. The behaviour is checked against a
cluster by `platform/web/tests/test_api_endpoints.py`. If this file and those disagree,
those are right and this is stale.

---

## Authentication

Every route is behind the signed-in gate. There are no public API routes — `/healthz`
and the landing page stay on the console.

The credential is your **account token**, and there is exactly one kind now. The shared
`PLATFORM_ADMIN_TOKEN` was removed on 2026-08-15 along with the operator role; a token is
issued when you sign in at `/signin` with a code emailed to your address, and it resolves
to your tenant by a hash lookup in `account`. Every authenticated request is scoped to
that tenant and there is no principal that sees across tenants.

Two ways to present it, and they are the *same* token resolved the same way
(`accounts.account_for_token`, via `auth.principal_from_cookie`):

```
Cookie: rtf_session=<token>
Authorization: Bearer <token>
```

The cookie wins if both are sent. Prefer it: it is `httpOnly`, so script cannot read it,
and a bearer token has to live somewhere script *can* read — which puts it within reach
of an XSS. The bearer path exists so a non-browser client and `curl` can reach the API at
all. For a same-origin console, use the cookie and leave the header to scripts.

**No CORS headers are sent.** Deliberate, not an oversight: permissive CORS on a
cookie-authenticated API is how CSRF gets built by accident, and the right allow-list
depends on where the console is served from. Same-origin needs nothing. If the console
ends up on another origin, that needs a decision — ask, do not add a wildcard.

Signed out ⇒ **401** with `code: "not_authenticated"`. Not a 303. The console redirects a
browser to a page that explains what the product is; an API client following that would
get a 200 full of HTML and read it as success.

---

## Refusals

One envelope for every non-2xx this API produces:

```json
{
  "error": { "code": "already_queued", "message": "Already queued — the first approval prepared the send, and the database refused a second copy." },
  "message": "Already queued — the first approval prepared the send, and the database refused a second copy."
}
```

* **`error.code`** is the contract. Closed set, listed below. Branch on this.
* **`error.message`** is for a human. **Not a contract** — it may be rewritten in any
  commit. Show it; never pattern-match it.
* **`message`** is the same string mirrored at the top level, for clients whose error
  reader looks there. Derived from the same field, so the two cannot drift.

Framework-level request-validation failures (missing body field, wrong type) use this
envelope too, at **422** with `code: "malformed_request"` and an extra `error.fields`
carrying the raw validator output — the only part of any refusal that names *which*
field was wrong. There is exactly one error shape on this API; you do not need a second
parser.

Driver text is never passed through. A refusal will not name a constraint.

| code | status | means |
|---|---|---|
| `not_authenticated` | 401 | No valid token. |
| `read_only` | 403 | Signed in, may not write. |
| `not_configured` | 503 | `DATABASE_URL` unset on the server. Misconfiguration; retrying will not help. |
| `not_found` | 404 | No such object in this tenant. Also what you get for another tenant's object — the two are indistinguishable on purpose. |
| `not_allowed_value` | 400 | A value outside a closed set. The message lists the set. |
| `malformed_request` | 422 | The request did not parse. `error.fields` names the field. |
| `transition_refused` | 409 | A thread state change the machine does not allow. The message names both ends and what *is* legal from here. |
| `already_queued` | 409 | A second approve. `UNIQUE (message_id)` on `outbox` refused it. **Not an error** — the first one worked. |
| `thread_occupied` | 409 | Somebody already has an open thread with this counterparty, across every campaign. **Not an error.** |
| `plan_limit_reached` | 409 | The tenant's plan allows no more open conversations this calendar month. The message names the plan, the allowance and the count. |
| `no_draft_waiting` | 409 | That message is not an unsent outbound draft in this tenant. |
| `nothing_queued` | 409 | Analysis not queued — already in the frontier, or nobody is credited on the track. |
| `no_master` | 409 | No current stored master to analyse. |
| `suggestion_unacceptable` | 400 | The suggestion payload cannot be promoted. The message says why. |
| `not_justified` | 409 | This thread records no shortlist behind it — nobody ever recorded a reason. |
| `history_expired` | 409 | A reason *was* recorded and the cluster no longer retains the memory to replay it. |

`not_justified` and `history_expired` are deliberately separate. "We never had a reason"
and "we had one and it aged out" are different answers to somebody asking why they were
contacted, and a console that rendered them identically would be lying about one.

`thread_occupied` and `plan_limit_reached` are separate for the same class of reason.
Both are 409s on the same endpoint and they mean opposite things: the first says *this
counterparty is taken, pick another*, the second says *the month is used up, no
counterparty will work*. Only the second has an upgrade as its remedy.

Two further codes exist in the closed set — `billing_not_configured` and
`billing_signature_invalid` — and **no endpoint in this API raises them.** They belong to
the two `POST /billing/*` endpoints, which live on the console application rather than
here (the webhook cannot present a cookie). They share this envelope because there is one
refusal shape for the whole deployment, not because they are part of this contract.

**Two codes were removed from the set on 2026-08-15**, and their absence is deliberate
rather than an omission:

- `no_tenant` meant "authenticated, but we cannot work out whose rows these are". It was
  reachable only through the operator principal, which carried no tenant. With one
  credential there is no way to be authenticated without an `account` row naming a
  tenant, so nothing can raise it.
- `claim_refused` belonged to `POST /claim`, which no longer exists. Sign-in is a
  two-step browser flow with no machine-readable form; a script that wants a credential
  holds the token it was issued.

A declared code no path can produce is worse than no code — a client author writes a
branch for it and can never test the branch. If you had one, delete it.

---

## Collections

Every list endpoint returns the same envelope:

```json
{
  "rows": [ … ],
  "limit": 200,
  "returned": 12,
  "total": 47,
  "truncated": false,
  "truncated_is_exact": true
}
```

* `limit` — always 200 (`research.LIMIT`). **There is no pagination and no cursor.**
* `total` — `null` unless the endpoint computes one.
* `truncated` — exact when `truncated_is_exact` is `true`. Otherwise it is
  `returned >= limit`, an **upper bound**: a table holding exactly 200 rows reports
  `true` while having been sent whole.

Sort order is fixed per collection and is part of what the view means (the queue is
pending-first then by score; threads are awaiting-human first). Do not sort a truncated
page and call it a ranking.

### Types

* Timestamps — ISO 8601 with offset (`"2026-08-13T14:22:00+00:00"`).
* Dates — `"2026-08-13"`. `released_on` is a day; it has no time.
* Money — **integer micro-dollars**, always suffixed `_micro_usd`. Divide by 1,000,000
  to display. The schema stores micro so summing never drifts; do not round-trip through
  a float before you have to.
* Scores and confidences — numbers in 0–1, or `null`. Never strings.
* `null` means absent. It never means zero.

---

## Read endpoints

### `GET /api/v1/summary`

Eleven numbers in one round trip — the same statement the console's nav badges use.
**This is what to poll.** See "Streaming" below.

```json
{ "awaiting_human": 0, "open_threads": 1, "inbound": 0, "queued_unsent": 0,
  "running_campaigns": 3, "leads_pending": 2626, "leads_failed": 0, "leads_due": 2626,
  "suggestions_pending": 8, "sender_wired": false, "inbound_adapter_wired": false }
```

`sender_wired` and `inbound_adapter_wired` are both `false` and will stay false until a
provider is wired. They are stated rather than implied so a client showing "3 queued" can
say why none of them has left.

### `GET /api/v1/today`

The needs-you queue: everything asking something of a person, in one list. Composed by
`research.today_items` — including the grouping rule, which is why this endpoint exists
rather than the client assembling it from `/suggestions` and `/queue`. Five candidate
Deezer pages for one artist is **one decision made five times**; a client that listed
five entries would make an operator feel behind when they are not.

```json
{
  "rows": [{
    "id": "sug-334ccb2a-…",
    "kind": "suggestion_group",
    "tone": "act",
    "head": "3 candidate surfaces for Hallow Youth",
    "sub": "deezer · best match 0.70 · found by search, not asserted",
    "subject": { "kind": "party", "id": "334ccb2a-…", "name": "Hallow Youth" },
    "why": [
      { "label": "how it was found", "value": "an agent searched a source by name and found these", "provenance": "inferred" },
      { "label": "candidates", "value": "3" },
      { "label": "platforms", "value": "deezer" },
      { "label": "best match", "value": "0.70" }
    ],
    "candidates": [ { "id": "eefed9c8-…", "confidence": 0.7, "payload": {…}, "rationale": "…", "refused_because": null } ],
    "actions": [
      { "key": "accept", "label": "Accept", "style": "primary", "endpoint": "/api/v1/suggestions/{id}/accept", "per": "candidate" },
      { "key": "reject", "label": "Reject", "style": "danger",  "endpoint": "/api/v1/suggestions/{id}/reject", "per": "candidate" }
    ]
  }],
  "returned": 2,
  "quiet": { "leads_pending": 2626, "facts_live": 45110, "chunks_indexed": 22057, "runs_24h": 40866 }
}
```

`kind` is `"suggestion_group"` or `"parked_lead"`. `tone` is `"act"` or `"warn"` — only
those two are produced today; `"info"` is not, so do not build a branch waiting for it.
`"act"` means confidence ≥ 0.70.

`per: "candidate"` means the action applies to each entry in `candidates`, not to the
item — substitute the candidate's `id` into `endpoint`. An artist has one page on a
service, so accepting is per row.

**`parked_lead` items carry `"actions": []`.** That is honest, not missing: "run it now"
would mean `fleet.expedite`, and no endpoint exposes it. The item still carries its full
`why`. If you want the item actionable in place, ask — it is a small addition on an
existing function, and it was left out because it was not requested.

**`refused_because`** on a candidate is the server saying *in advance* that Accept would
be declined, computed by the same predicate the write path branches on
(`repo.why_unacceptable`). `null` means the accept path is clear. Disable the control and
show the string when it is non-null.

> **On the duplicate-approval case you asked about:** it cannot appear here, and this is
> structural rather than an omission. `/today` contains no drafts. A draft only reaches
> the gate at `awaiting_human`, and reaching `awaiting_human` means no `outbox` row
> exists for it — the first approve is what writes one, and it simultaneously moves the
> thread to `queued`, at which point the draft is off `/approvals` entirely. So there is
> no state in which a visible draft's approve is known-in-advance to be refused. A
> pre-disabled control for it would be a control that is never disabled. Retry the
> approve and handle `already_queued`; that is what the constraint is for, and the
> endpoint is safe to retry.

### `GET /api/v1/artists`

Roster. Row: `id`, `name`, `type`, `slug`, `kind`, `status`, `created_at`, and counts
`tracks`, `facts`, `pending`, `profiles`, `docs`.

### `GET /api/v1/artists/{artist_id}/profiles`

Presence rows for one artist: `id`, `platform`, `mode`, `handle`, `profile_url`, `state`,
`match_basis`. An unknown artist returns an empty list, not a 404 — see `not_found` on
existence oracles.

### `GET /api/v1/recordings`

Recordings with masters and credits attached. Row: `id`, `title`, `slug`, `isrc`,
`isrc_raw`, `released_on`, `status`, `created_at`, `facts`, `leads`, `places`,
`artist_name` (**`null` when uncredited** — the console's em-dash is translated back),
plus `assets[]` (`id`, `kind`, `label`, `bytes`, `mime`, `state`, `uploaded_at`,
`uploaded_by`, `duration_ms`, `sample_rate`) and `credits[]` (`id`, `party_id`,
`party_name`, `role`, `provenance`).

### `GET /api/v1/counterparties`

**The only filtered collection**, because it is the only one large enough to need it —
14,170 rows on the cluster today, so an unfiltered call shows 1.4% of them alphabetically.

| param | type | meaning |
|---|---|---|
| `q` | string | Case-insensitive substring of `name`. Name only — not roles, not profile text. |
| `contact_state` | string | Exact match. |
| `searchable` | bool or omitted | `true`: only those the shortlist can see. `false`: only those it cannot. Omit for both. |

`searchable` is **tri-state**; omitting it is not the same as `false`. `searchable=false`
is the useful query — a counterparty with no embedding is invisible to the shortlist no
matter how good a match they would be, and this is how you get that list.

Row: `id`, `name`, `contact_state`, `embedding_model`, `searchable` (boolean),
`platform`, `url`, `roles` (count), `role_list` (array), `profile` (full text or `null` —
not truncated; take a prefix if you want a preview).

The response echoes `filters` so a client can confirm what was applied.

### `GET /api/v1/facts`

Row: `id`, `dimension`, `value_text`, `provenance`, `status`, `confidence` (or `null`),
`source`, `written_by`, `observed_at`, `model`, `supersedes_id`, `artist_name`.
Envelope carries an exact `total` and `by_status` (`live`, `stale`, `retracted`).

### `GET /api/v1/suggestions`

Pending inferred matches. Row: `id`, `party_id`, `party_name`, `party_slug`, `kind`,
`payload`, `confidence`, `rationale`, `refused_because`.

### `GET /api/v1/campaigns`

Row: `id`, `name`, `channel`, `state`, `goal`, `started_at`, `created_at`, `artist`,
`track`, and `funnel` — `{threads, open_threads, awaiting, queued, sent, replied, agreed,
delivered}`. Every funnel number is derived at read time; none is stored.

`sent` counts messages a provider accepted, and **nothing has a provider**. A campaign
can be running with drafts approved and queued and still read `sent: 0`. That is the send
gate working, not a stalled campaign.

### `GET /api/v1/threads`

Row: `id`, `state`, `reason`, `created_at`, `updated_at`, `closed_at`, `owner_agent`,
`lease_expires_at`, `attempts`, `last_error`, `who`, `contact_state`, `campaign`,
`channel`, `artist`, `track`, `messages`, `last_message`, `queued`, plus two derived:

* `holds_counterparty` — whether this row is inside `one_open_thread_per_counterparty`,
  and therefore whether the counterparty is unavailable to every other campaign.
* `progress` — 0–100 along the walk to a close. `closed_won` is 100; the other two closed
  states are 0.

### `GET /api/v1/threads/{thread_id}/justification`

Why this counterparty was contacted — the shortlist replayed as it stood at the moment
the thread was opened.

```json
{ "as_of": "…", "recorded_rank": 3, "recorded_distance": 0.41,
  "replayed_rank": 3, "matched": true, "ranking": [ … ] }
```

**Read `matched`.** The replay alone is unfalsifiable — whatever it returns looks like an
answer. Compared against the rank and distance the thread stored at decision time, it
becomes checkable: if the embedding model changed underneath, the numbers diverge and
`matched` goes `false`. Show a `false` as loudly as you show the ranking.

Refuses with `not_justified` or `history_expired` — see above on why those are separate.

### `GET /api/v1/approvals`

Drafts at the send gate. Row: `id` (the **message** id — this is what the approve and
reject endpoints take), `thread_id`, `state`, `updated_at`, `subject`, `body`,
`created_at`, `channel`, `idempotency_key`, `who`, `campaign`, `campaign_channel`,
`artist`, `track`, `drafts`, and `could_stand_on[]`.

`could_stand_on` is named awkwardly on purpose. Nothing records what the drafter actually
read; these are the live facts that exist for this artist. Do not render it as "this
pitch is based on…" — that would be a claim the server cannot support.

Envelope also carries `queued_unsent` and `sender_wired: false`.

### `GET /api/v1/inbox`

Row: `id`, `thread_id`, `subject`, `body`, `intent`, `confidence`, `received_at`,
`channel`, `thread_state`, `who`, `artist`, `campaign`.

`intent` is `""` when nothing classified it — the raw column value, not a label. Envelope
carries `inbound_adapter_wired: false` and `threads_awaiting_reply`. **Nothing writes
inbound messages**, so an empty list here is correct and not a failure; say so in the UI
rather than implying the label has no replies.

### `GET /api/v1/queue`

Row: `id`, `kind`, `adapter`, `target`, `depth`, `score` (number), `state`,
`owner_agent`, `lease_expires_at`, `next_action_at`, `attempts`, `last_error`,
`cadence_seconds`, `scope_kind`, `reason`, `parent_lead_id`, `artist_name`. Envelope
carries an exact `total` and `by_state`.

### `GET /api/v1/runs`

Row: `id`, `agent_kind`, `state`, `summary`, `error`, `documents`, `facts`, `metrics`,
`leads`, `dropped`, `tokens_in`, `tokens_out`, `cost_micro_usd`, `duration_ms`,
`started_at`, `artist_name`, `refused` (the spend gate's object, or `null`).

Envelope carries `last_24h` — `{total, errors, refused, lease_lost, cost_micro_usd}`.
Note the rows are *not* limited to 24 hours; the counts are. `total` is deliberately not
set on the envelope so `truncated` cannot be misread.

### `GET /api/v1/fleet`

Row: `kind`, `state`, `has_implementation`, `leases_held`, `work_waiting`, `manifest`,
`runs`.

`state` is one of:

| state | means |
|---|---|
| `working` | holding a lease right now |
| `idle` | enabled, nothing claimed |
| `off` | disabled — an `UPDATE`, not a deploy |
| `declared` | a manifest with no implementation behind it |
| `unmanifested` | has run, and nothing declares it. `manifest` is `null`. |

`off` and `declared` look similar and mean opposite things. The API computes this so two
front ends cannot derive it differently.

`runs` is `{total, last_hour, failed, refused, last_run, cost_micro_usd}`. Runs are
attributed to the *worker that claimed the lead*, not the agent function it dispatched
to — `ingest-cli` claims for everything — so a manifest can show zero runs while its code
has run many times. That is why `unmanifested` rows exist rather than being dropped.

### `GET /api/v1/budgets`

Row: `id`, `name`, `cap`, `paused`, `max_depth`, `max_leads`, `spent`, `pending`,
`cost_micro_usd_24h`. The spent/cap ratio is left to the client — `cap` can legally be 0.

Spend is summed from `agent_run`, not decremented from a counter: a counter row is a
serialization point under SERIALIZABLE, and ten workers on one launching artist would
retry against each other on exactly the row meant to protect them. Do not cache it as if
it were a balance.

---

## Actions

All are `POST`, all require write permission, all return JSON.

### `POST /api/v1/campaigns`

```json
{ "artist_id": "…", "name": "Spring push", "channel": "curator",
  "goal": "playlist adds", "recording_id": null }
```

`channel` ∈ `curator`, `ugc`, `press`, `radio`, `sync` (default `curator`).
`recording_id` is genuinely optional — an artist-level push is a real campaign with no
single recording behind it.

`name` is **required**. No default is invented: `campaign.name` is `NOT NULL` and a name
derived from the artist and the date would be a label nobody chose appearing in every
list.

**There is no cap parameter and cannot be one.** Caps are per-artist, in `party_budget`,
and are read through `GET /api/v1/budgets`. `campaign` has no cap column. Accepting a
`cap` field and ignoring it would be exactly the silent default this codebase forbids.

Returns `{id, name, state: "draft", channel, note}`. **Created as a draft that opens
nothing** — `running` is the state in which the fleet may open threads, and there is no
`state` parameter here to reach it. Running a campaign stays a separate, deliberate act,
and today that control exists only on the console.

Refuses: `not_allowed_value` (channel, empty name), `not_found` (artist or recording).

### `POST /api/v1/campaigns/{campaign_id}/threads`

```json
{ "counterparty_id": "…" }
```

Opens a conversation and takes the counterparty off the shortlist
(`contact_state → in_thread`).

Returns `{id, state: "discovered", campaign_id, counterparty_id, decided}`, where
`decided` is `{as_of, rank, distance}` or **`null`**.

**The ranking is recomputed server-side and there is no parameter to supply it.** That
record is what answers *why was this person contacted*, and a client-supplied rank would
be forgeable by the one party with a motive to forge it, in the one place the record
exists to answer for an irreversible act. `decided: null` means the counterparty was not
on the shortlist — render that as "opened by hand", never as "rank unknown". An absent
reason is left absent rather than given a plausible default.

Refuses: `thread_occupied` (409 — somebody already has an open thread with them, across
every campaign), `plan_limit_reached` (409 — the tenant's plan allows no more open
conversations this calendar month; closing one does not give the allowance back, because
the meter counts conversations started), `not_found` (campaign or counterparty).

### `POST /api/v1/threads/{thread_id}/close`

```json
{ "outcome": "closed_won", "reason": "optional" }
```

`outcome` ∈ `closed_won`, `closed_lost`, `closed_no_reply`.

Returns `{id, was, state, counterparty_released: true}`.

Closing releases the counterparty in the same transaction: the thread leaves the partial
unique index and `contact_state` follows it (`declined` after `closed_lost`, otherwise
`contactable` — a no is worth remembering). This is the **only** way a client returns
somebody to the shortlist. It also queues a `distil_lesson` lead, because a closed thread
is the only moment the system knows how an approach actually went.

Refuses: `not_allowed_value`, `transition_refused`. Note the state machine is real — a
thread in `sent` cannot go straight to `closed_no_reply`; it must reach `awaiting_reply`
first. The refusal message names what *is* legal from where the thread actually is.

### `POST /api/v1/drafts/{message_id}/approve`

Takes the **message** id from `/approvals` rows (`row.id`). No body.

Returns `{thread_id, message_id, thread_state: "queued", sent: false, note}`.

Stamps the message, writes the `outbox` row and moves the thread — **all in one
transaction**. It does **not** send. Nothing claims `outbox`; no provider is wired.
`sent` is always `false`. Do not render this as "sent".

**Safe to retry.** A second approve is refused by `UNIQUE (message_id)` on `outbox` and
returns `already_queued` — the first one worked, and there is no second copy in flight.
Verified against the cluster: after a double approve, `outbox` holds exactly one row.

Refuses: `no_draft_waiting`, `already_queued`, `transition_refused`.

### `POST /api/v1/drafts/{message_id}/reject`

```json
{ "reason": "optional" }
```

Returns `{thread_id, message_id, thread_state: "drafted"}`. The message row **stays** —
what the drafter wrote and what an operator refused is the only training signal this part
of the product produces. A rejected draft is redrafted, not discarded, so rejecting costs
the thread nothing.

### `POST /api/v1/recordings/{recording_id}/analyse`

No body. Returns `{recording_id, asset_id, queued: true}`.

Queues a lead against the **current** master — stored, of kind `master`, and not
superseded. It does not analyse; a worker claims it on its next pass.

Refuses `no_master`, or `nothing_queued` when either this exact file is already waiting
in the frontier (one analysis per distinct file, forever) or nobody is credited on the
track (a recording-scoped lead has to carry a party). The endpoint cannot distinguish
those two without a second query, so the message names both.

### `POST /api/v1/suggestions/{suggestion_id}/accept` · `…/reject`

No body. Return `{id, state}`.

Accept promotes an inferred match to an asserted surface and queues the mapping, in one
transaction. It is also the only place in the system where a guess becomes a fact — a
name match is inference, and a wrong accept quietly attaches somebody else's catalogue to
your artist. Check `refused_because` first and disable the control when it is non-null.

Refuses `suggestion_unacceptable` with the reason.

---

## Streaming

**There is no SSE or WebSocket endpoint, and this was assessed rather than skipped.**

* The app runs under Mangum in Lambda. Mangum buffers the whole ASGI response and does
  not implement Function URL response streaming. An SSE endpoint would work in local
  development and hang until timeout in production — breaking the invariant that "works
  on my machine" and "works in the deployment" are the same statement.
* `changefeed.follow` needs **two dedicated connections per subscriber** — a core
  changefeed occupies its connection in single-row mode forever — and neither may be the
  shared console connection. Two browser tabs is two feeds.
* The changefeed's `Wake` deliberately carries no payload, only "something changed of
  this kind". A stream of those is a poll with extra infrastructure.

**Poll `GET /api/v1/summary`.** One round trip, eleven numbers, and enough to decide
whether to refetch a collection. A few seconds' interval costs less than a held
changefeed and works in both execution models.

If streaming becomes worth the cost, the change is to the execution model — a small
always-on process holding one feed and fanning out — not to this API.

---

## Known limits

Stated so they are decisions rather than surprises.

* **No pagination.** `LIMIT 200`, no cursor. Offsets skip and duplicate rows under
  concurrent writes; the right answer is a keyset cursor per collection, which is real
  work and should be shaped against a real client rather than guessed at.
  `/counterparties` is the collection that feels this, which is why it got filters.
* **No per-object GET.** The collections carry every field a detail view would need.
  `/threads/{id}/justification` is the exception because it is a different question.
* **No campaign state change, no queue expedite, no fleet toggle, no draft-by-hand.**
  Each is one call to an existing function; none was requested. Ask rather than assume
  they are hard.
* **Nothing sends.** No mail provider, nothing claims `outbox`, no inbound adapter. The
  API reports this in `/summary`, `/approvals` and `/inbox` rather than letting a client
  infer that the label simply has no mail.
