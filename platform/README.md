# `platform/` — the substrate

The platform described by [`docs/SCOPE-RESET.md`](../docs/SCOPE-RESET.md) and
[`docs/PLATFORM-SPEC.md`](../docs/PLATFORM-SPEC.md). Built one piece at a time.

> ## Nothing outside `platform/` may be touched.
>
> `app/`, `content/` and `infra/` are **frozen**. RemixKit is live in judging for the
> Backblaze generative-media hackathon, and the deployed console is what judges are
> looking at. This resolves `SCOPE-RESET.md` open decision 2 (repository topology) as
> *defer* — reconsider the platform-takes-over-the-repo move after both deadlines pass.

## Where we are

| Piece | State |
|---|---|
| `schema/001_tenant_artist.sql` — the roots | **current** |
| Tracks, derived facts, counterparties, threads, memory | not started |

One migration at a time. A table arrives when something needs it, not because
`PLATFORM-SPEC §2` lists it.

```bash
set -a; . .env; set +a
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f platform/schema/001_tenant_artist.sql
```

## Cluster facts already established

Run against `respect-the-funk-31317` (aws-us-east-1, CockroachDB CCL **v26.2.5**) in a
throwaway database that was dropped afterwards. Recorded here so nobody pays for them
twice:

1. **`feature.vector_index.enabled` is already `t`.** No `SET CLUSTER SETTING` needed,
   so the restricted-settings worry does not arise. This closes
   [`PLATFORM-SPEC §10` risk 3](../docs/PLATFORM-SPEC.md) — the flagged go/no-go that
   the whole retrieval design depended on.
2. **Vector-index prefix filtering works as §6's amendment predicts.** `EXPLAIN`
   resolves leading equality columns to `prefix spans` on a `vector search` node, for
   both the R1 shortlist and R2 lesson shapes. No `LIMIT k × n` over-fetch.
3. **A partial unique index enforces the §3c cross-channel collision**, and releases the
   counterparty when the thread closes.
4. `FOR UPDATE SKIP LOCKED` is supported, so §3a's lease claim works.

A full-schema draft covering all of `PLATFORM-SPEC §2` was written and verified before
being cut back to these two tables. It is not deleted, only unshipped — see
`git show a6ba8bb:platform/schema/001_substrate.sql`, and the deviations argued in
`git show a6ba8bb:platform/README.md`. Reach for it as a reference when a piece comes
up, not as a plan.

## Still unmeasured

- **RU cost of a filtered vector scan** (`PLATFORM-SPEC §10` risk 2). Needs real row
  volume; a probe with no rows measures nothing.
- **Changefeed RU draw.** Needs a webhook sink to exist first.
- **`ccloud` has not been used.** The cluster was made in the console, so
  `PLATFORM-SPEC §8`'s claim of it as tool 3 "in the day-1 provisioning path" is not yet
  earned — use it for something real or drop the claim.
