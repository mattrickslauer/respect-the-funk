# `platform/` — the substrate

The platform described by [`docs/SCOPE-RESET.md`](../docs/SCOPE-RESET.md) and
[`docs/PLATFORM-SPEC.md`](../docs/PLATFORM-SPEC.md). Everything new lives here.

> ## Nothing outside `platform/` may be touched.
>
> `app/`, `content/` and `infra/` are **frozen**. RemixKit is live in judging for the
> Backblaze generative-media hackathon, and the deployed console is what judges are
> looking at. This resolves `SCOPE-RESET.md` open decision 2 (repository topology) as
> *defer* — a new top-level directory here, with the platform-takes-over-the-repo move
> reconsidered after both deadlines pass.

| | |
|---|---|
| `schema/001_substrate.sql` | The spine, coordination and memory. Applies as one file. |
| `tests/seed_minimal.sql` | Fixture exercising the §3c cross-channel collision. |

## Applying it

```bash
set -a; . .env; set +a
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f platform/schema/001_substrate.sql
```

## What has been verified, and what has not

Everything in this section was run against the real cluster
(`respect-the-funk-31317`, aws-us-east-1, CockroachDB CCL **v26.2.5**) in a throwaway
`_schema_probe` database, which was dropped afterwards. `defaultdb` is untouched and
still empty.

**Verified:**

1. **`feature.vector_index.enabled` is already `t`.** No `SET CLUSTER SETTING` needed,
   so the restricted-settings worry does not arise. This closes
   [`PLATFORM-SPEC §10` risk 3](../docs/PLATFORM-SPEC.md), which was flagged as
   *"the first thing to check on day 1, before anything else is built."*
2. **R1 accelerates.** `EXPLAIN` on the shortlist query resolves
   `(tenant_id, kind, contact_state)` to **`prefix spans`** on a `vector search` node.
   The §6 amendment does what it was written to do; there is no `LIMIT k × n`
   over-fetch. The `contact_state` denormalisation is load-bearing and earns its place.
3. **R2 accelerates**, on the same evidence, using the polymorphic-scope index.
4. **§3c holds.** With one counterparty reachable as both a creator and a curator, the
   second fleet's `INSERT` fails with
   `duplicate key value violates unique constraint "one_open_thread_per_counterparty"`
   — and closing the first thread releases the person, verified by retry. Contact
   discipline is a constraint, not a convention.
5. **§3a claims work.** The lease `UPDATE … FOR UPDATE SKIP LOCKED` runs and returns
   the claimed row; `SKIP LOCKED` is supported.
6. The whole file applies cleanly, including the computed `provenance_rank STORED`
   column, `ON UPDATE now()`, and both vector indexes.

**Not verified — still open:**

- **RU cost of a filtered vector scan** (`PLATFORM-SPEC §10` risk 2). Needs real row
  volume; a probe with no rows measures nothing. This is the day-1-with-data task.
- **Changefeed RU draw.** No changefeed has been created yet — it needs a webhook sink
  (Lambda) to point at. The likelier of the two to erode the free allowance, because it
  draws continuously rather than per query.
- **`ccloud` is not installed and was not used.** The cluster was created in the
  console. `PLATFORM-SPEC §8` claims `ccloud` as CockroachDB tool 3 "in the day-1
  provisioning path" — either it gets used for something real, or that claim comes out.

## Deviations from PLATFORM-SPEC, and why

Per the house rule, these are argued rather than made silently.

**1. `is_current` alongside `supersedes_id`.** §2b specifies supersession chains with
"the current value is the head of the chain". Finding that head means
`NOT EXISTS (SELECT 1 … WHERE supersedes_id = t.id)` — an anti-join on the hottest read
path in the system. `is_current` is a denormalisation written in the same serializable
transaction as the supersession, which is precisely the argument §6's `contact_state`
amendment already makes and accepts. A partial unique index
(`… ON track_character (track_id) WHERE is_current`) makes "exactly one current row"
structural, in the same style as §3c.

**2. R2 has R1's problem, and §6 does not address it.** §6 amends R1 for the
vector-index prefix constraint but leaves R2 as written. R2 wants lessons scoped to
*this artist AND this counterparty kind AND this channel*, and `lesson`'s scope is
polymorphic (`scope_kind`, `scope_id`) — so those three cannot form one equality
prefix. **R2 is therefore N scoped ANN queries, one per scope level, merged by the
caller** (suggested ordering: confidence × recency, with `hit_count` as a tiebreak).
Each is individually accelerated, as verified above. The alternative — denormalising
`channel` and `counterparty_kind` onto `lesson` as nullable columns — does not work,
because a nullable column is not usable as an equality prefix. **This needs your sign-off;
it changes the Drafter's retrieval code from one query to a merge.**

**3. `channel` and counterparty `kind` are `STRING`, not enums.** §7's claim is that
"a channel is data, not code". An enum would make adding a channel an `ALTER TYPE` —
i.e. code — which quietly falsifies the claim the architecture rests on. They stay
`STRING`, and `campaign.channel` carries a composite FK to
`channel_playbook (tenant_id, channel)`, so "adding a channel is a row" is *enforced*
rather than asserted. Enums are used only for sets the system genuinely owns
(`provenance`, `thread_state`, `contact_state`, `outbox_state`, …).

Note the resulting division of labour: `thread_state` is channel-agnostic per §2d;
`channel_playbook.state_machine` describes which transitions and cadence a given channel
permits *over those same states*.

**4. `provenance_rank` as a stored computed column.** §2c says "source rank decides what
is read" without saying where rank lives. Left to each caller's `ORDER BY`, it is a
discipline that will be forgotten. As a computed column it is indexable, and
`obs_read_path` makes "best observation for this dimension" a single index scan. The
ordering is measured (3) > asserted (2) > inferred (1), which is
`SCOPE-RESET §2a` rule 1's rule: an inferred value may never win over a measured one.

**5. `tenant_id` carried on child tables** the spec's sketch omits it from
(`track_measurement`, `track_character`, `message`, …). Required for tenant-scoped
queries and vector-index prefixes without a join, and consistent with
`BUILD-SPEC` rule 6 as carried into `SCOPE-RESET §1`.

**6. `outbox.lease_expires_at` added.** §2d gives `outbox` `claimed_by` and `claimed_at`
but no expiry, so a Sender that dies mid-claim strands its row forever — the exact
failure `thread`'s lease exists to prevent. Same discipline, same reason.

**7. `message.tenant_id`, `counterparty` uniqueness, and a few CHECKs** are additive
tightenings, not design changes.

## Open questions before the next layer

1. **Embedding dimensions are set to 1024.** That matches Bedrock Titan Text Embeddings
   V2 (default 1024) and Cohere Embed v3. Confirm which you're using — changing it later
   means rebuilding both vector indexes and re-embedding everything.
2. **Tenancy policy** — `SCOPE-RESET` open decision 6. `tenant_id` is on every table
   regardless, so this decides policy, not schema. Not blocking.
3. **Counterparty acquisition** — `SCOPE-RESET` open decision 4, and the most urgent of
   them, because it gates the Scout agent and therefore the first real rows in
   `counterparty`.
