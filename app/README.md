---
title: "RemixKit — the artist console"
subtitle: "The application PRODUCT.md describes: register an artist, build their identity once, attach songs, generate content designed to be imitated, and verify what came out."
status: "BUILT — runs end-to-end with zero credentials. AWS deploy is written and validated but unapplied."
date: "2026-07-27"
---

## Run it

No credentials, no bucket, no API keys, no database.

```bash
cd app
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn remixkit.main:app --reload
```

Open <http://localhost:8000>. Register an artist, record likeness consent, save an
identity, attach a song, set its hook window, generate a kit. Real videos appear.

`ffmpeg` is the one non-Python dependency (`brew install ffmpeg`). Without it the mock
generator reports the modality unavailable rather than producing something misleading.

```bash
.venv/bin/python -m pytest        # 24 tests, ~2s
```

---

## What "mock" does and does not mean

This matters more than it usually would, because a demo that looks live while it is
mocked misleads by accident.

**Real:** the Genblaze `Pipeline`, the `ObjectStorageSink`, the `HIERARCHICAL` key
layout, content hashing, the manifest, `manifest.verify()`, the cost ledger, and the
embed-on-delivery path. **Synthetic:** the pixels, which ffmpeg generates, and the unit
costs, which come from a table.

The consequence worth stating: *the manifest verified on a laptop is produced by the
same code that produces the one in B2.* Switching to real providers changes
`RK_GENERATOR_BACKEND` and nothing else.

Every page carries a banner naming which backends are live, and `/healthz` returns the
same thing as JSON.

---

## Shape

Hexagonal, so that "componentized, and we add auth later" is a configuration change
rather than a refactor.

```
remixkit/
  domain/models.py     Artist · Identity · Song · Kit · Asset — pure, no I/O
  ports/               Protocols: Storage · DocumentRepository · Generator · JobQueue
  adapters/            storage_local · storage_b2 · repo_documents
                       generator_genblaze · providers (mock | live) · mock_media
                       queue_inline · queue_sqs
  services/            artists · identities · songs · briefs · kits · delivery · verify
                       accounts  ← sign-in codes and sessions
  auth/                provider (Protocol) · anonymous · otp
  api/v1.py            JSON API
  ui/                  Jinja + htmx components
  deps.py              the composition root — the only file that names an adapter
```

Five axes, five environment variables:

| | dev default | production |
|---|---|---|
| `RK_STORAGE_BACKEND` | `local` | `b2` |
| `RK_GENERATOR_BACKEND` | `mock` | `genblaze` |
| `RK_QUEUE_BACKEND` | `inline` | `sqs` |
| `RK_MAIL_BACKEND` | `console` | `zeptomail` |
| `RK_AUTH_BACKEND` | `none` | `otp` |

---

## Authentication: passwordless email codes

`RK_AUTH_BACKEND` selects a provider. `none` (the default) is `AnonymousAuth` — a
complete implementation that admits everyone, so a fresh checkout has no login to get
past. `otp` is the real one: a six-digit code by email, exchanged for a signed session.

```
POST /api/v1/auth/request-code   {"email": "…"}          → emails a code
POST /api/v1/auth/verify-code    {"email": …, "code": …} → {"token": "…"}
GET  /api/v1/auth/me             Authorization: Bearer … → the principal
```

The console uses the same tokens through an httpOnly `rk_session` cookie set at
`/auth/login`. One `AuthError` handler in `main.py` decides what a failure looks like:
a browser is redirected to the login page with `?next=` preserved, an htmx request gets
`HX-Redirect`, and a script gets 401 JSON. No route contains an auth branch.

Turning it on:

```bash
RK_AUTH_BACKEND=otp
RK_MAIL_BACKEND=zeptomail
RK_ALLOWED_EMAILS=you@agfarms.dev,colleague@agfarms.dev   # this IS the user table
RK_SESSION_SECRET=$(python -c 'import secrets;print(secrets.token_urlsafe(32))')
RK_ZEPTOMAIL_TOKEN=…                                      # = the SMTP password
RK_MAIL_FROM=rtp@agfarms.dev                              # a verified ZeptoMail sender
```

Four choices worth knowing about, each with a defensible opposite — the reasoning is in
`services/accounts.py`:

* **Allowlist, no registration.** `RK_ALLOWED_EMAILS` decides who exists. It is checked
  when a code is requested, when it is verified, *and* on every session read, so
  removing someone ends their session at their next request rather than at its expiry.
* **A refused address is told so**, rather than silently accepting. This leaks
  membership, which is the right trade when the population is a handful of known
  colleagues and the real failure is someone mistyping their own address.
* **Sessions are stateless** — a signed token, not a row. So there is no revocation
  before expiry; the TTL is a week, and the panic button is rotating
  `RK_SESSION_SECRET`, which signs everyone out at once.
* **Codes are stored hashed** (HMAC, keyed by email), single-use, ten-minute window,
  five attempts, thirty-second resend floor.

With no mailer configured the code goes to the server log and — *only* in `RK_ENV=dev` —
back to the caller, so the whole flow is completable on a laptop with no credentials.
Both conditions are required: either alone would be an authentication bypass on a
misconfigured deployment.

Two guards, both refusing to start rather than warning: `RK_REQUIRE_AUTH=true` with
`RK_AUTH_BACKEND=none`, and `RK_REQUIRE_AUTH=true` with an empty `RK_SESSION_SECRET`
(which would otherwise sign sessions with a key that changes every restart).

What made this cheap: every request already resolved a `Principal`, and every document,
object key, and job payload already carried its `tenant_id`. Adding auth was one
provider, one service, one page, and one exception handler — no service, no existing
template, and no storage key changed shape.

---

## The two refusals

Both are product features rather than validation, so both are in the service layer and
both are tested.

**No likeness consent, no kit.** A kit holds the artist's face invariant across every
shot, and that needs recorded, auditable rights. PRODUCT.md gap #3 inverted for this
use case: `nate-test` kept a face *out* of all 22 frames because rights were unknown; a
label generating content about its own signed artists wants the opposite. Withdrawal is
as easy as granting and takes effect on the next kit, not retroactively.

**No BPM without a method.** FORMAT-SPEC requires the provenance of a measurement.
PRODUCT.md names catalogue onboarding — not compute — as the real bottleneck; a field
that silently accepts an unsourced `128` hides that instead of measuring it.

---

## Provenance

The claim is "disclosure travels inside the file", and it is tested as a loop
(`tests/test_provenance.py`):

1. Generate a kit → assets + manifest land in the bucket, `manifest.verify()` gates
   the kit to `ready`. An unverified manifest fails the kit rather than shipping a
   claim we cannot back.
2. `GET /api/v1/kits/{id}/assets/{id}/download` → the delivered copy has the run's
   manifest **embedded in the media**, and says so in `X-RemixKit-Provenance`.
3. `POST /api/v1/verify` with that file → `verified: true`, `source: "embedded"`, plus
   the provider, model, and prompt of every step. Nothing is looked up.

The stored object stays byte-identical to what the provider returned — that is what
makes the content hash mean anything — so embedding happens on delivery, not before.

---

## What is deliberately not here

Everything PRODUCT.md defers: attribution links, `/r/{code}`, the disclosure gate,
leaderboards, rewards, composite sessions, K-factor. Those are role 2, and they are
what would force a relational database. Their absence is why there is no database tier.

Also absent by design: a websocket (kit status polls one row every 3s), a CDN, and a
"which archetype goes viral" recommender — research calls that folklore.
