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
  auth/                provider (Protocol) · anonymous  ← the seam; no auth today
  api/v1.py            JSON API
  ui/                  Jinja + htmx components
  deps.py              the composition root — the only file that names an adapter
```

Four axes, four environment variables:

| | dev default | production |
|---|---|---|
| `RK_STORAGE_BACKEND` | `local` | `b2` |
| `RK_GENERATOR_BACKEND` | `mock` | `genblaze` |
| `RK_QUEUE_BACKEND` | `inline` | `sqs` |
| `RK_AUTH_BACKEND` | `none` | *(none today)* |

---

## Authentication: absent, and shaped

There is **no authentication**, per the current instruction. `AnonymousAuth` is a
complete `AuthProvider` that admits everyone — not a stub that fails closed later.

What is already paid for, because it is the expensive half to retrofit:

1. Every request resolves a `Principal` (`deps.current_principal`). No handler reads a
   global or assumes a caller.
2. Every document, object key, and job payload carries `tenant_id`, taken from that
   principal. BUILD-SPEC §2b rule 6 calls multi-tenancy "near-impossible to retrofit".

Adding auth is: write `auth/oidc.py`, return it from `deps._build_auth`. No service,
route, template, or storage key changes shape. `Principal.can()` is the single place
scope checks become real, and the call sites already exist.

One guard: `RK_REQUIRE_AUTH=true` with `RK_AUTH_BACKEND=none` refuses to start.
Shipping "no auth" is a decision; shipping it by accident is an incident.

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
