# What can cost money, and what stops it

Every surface in this project that can generate a bill, what currently bounds it, and the
rules. `web/rtf_platform/spend.py` is the enforcement; this is the argument.

**Current state: nothing paid is enabled.** `RTF_PAID_ENABLED` is unset, which means
every metered call is refused before it is made. Turning it on is a deliberate act with a
number attached.

---

## The seven rules

**1. Default deny.** A missing environment variable means paid calls are off, not on. A
missing rate-card entry means the call is refused, not billed at an unknown price. A
ceiling that cannot be parsed is zero, not the default and never infinity. Every failure
mode points at not spending money.

**2. Price before you call, not after.** The usual shape — call, record, alert when a
dashboard crosses a line — finds the overspend after it happened, which for a token API
is a four-figure afternoon. `Gate.check()` computes the estimate first and the call does
not happen if it would breach what is left.

**3. Two ceilings, both in the environment.** `RTF_PER_CALL_CEILING_USD` stops one
pathological request; `RTF_DAILY_CEILING_USD` stops a thousand reasonable ones. Neither
lives in code, so raising one is a thing somebody does on purpose and can see in a diff.

**4. No override in code.** There is no `force=True`. The only way to spend more is to
change a number in the environment.

**5. Record every refusal.** A refused call goes into `agent_run.refused_json` with what
it would have cost. Silent skipping reads as "there was nothing to do" — the house rule
from `infra/MEMORY-WORKLOAD.md` and `content/bin/screen_clips.py`, pointed at our own
bill.

**6. Free is declared, not assumed.** `spend.FREE` lists the sources that cost nothing.
Adding a source is a decision about whether it is metered, rather than a silence that
defaults to free.

**7. No AWS resource that bills while idle.** Already the rule in `README.md` — no NAT,
no API Gateway, no Secrets Manager, no ECR. Extended: any new resource must state its
idle cost before it is added, and $0 is the only acceptable answer until there is revenue.

**Corollary — checking the bill costs money.** `ce:GetCostAndUsage` is **$0.01 per
request**. It is in the rate card for that reason. Never poll it, never put it in a loop,
and read the Billing console by hand instead.

---

## Every cost surface

| Surface | Bills for | What bounds it now | Risk |
|---|---|---|---|
| **Bedrock** | tokens | On-demand quota is **0 RPM account-wide** (verified 2026-08-07) | **None today** — unusable |
| **OpenAI** | tokens | `RTF_PAID_ENABLED` unset → every call refused | **None today** — gated |
| **CockroachDB Basic** | request units | Free allowance; **scales to zero, $0 idle** | Low — overage only |
| **Lambda** | requests + GB-seconds | Free tier 1M req / 400k GB-s; account concurrency **10** | Low |
| **CloudWatch Logs** | ingestion + storage | 7-day retention | Low |
| **Function URL** | nothing | — | None |
| **Cost Explorer API** | **$0.01/request** | Priced in the rate card | Low — never called in code |

### The two that are not fully bounded

**`POST /demo` has no rate limit.** It is the one route a stranger can write through. A
flood costs Lambda invocations, CockroachDB writes and storage. Bounded in practice by
the account's Lambda concurrency of 10 and by the URL being unpublished — neither of
which is a control, and both stop being true the moment the address is public. The real
fix is a per-IP limit at the edge, which the deliberately-no-API-Gateway topology has
nowhere to put. **It is a topology decision, not a code one.**

**Lambda concurrency is shared, not reserved.** `infra/variables.tf` sets
`max_concurrency = -1` because AWS refuses any reservation that would leave fewer than 10
unreserved, and this account's total is 10. So the ceiling is real but account-wide — the
console shares it with RemixKit. Raise the account quota before anything here gets busy,
and note that raising it removes the accidental ceiling.

---

## Turning paid calls on

Not yet. When it is time, the smallest useful step:

```bash
RTF_DRY_RUN=1                  # log what it would cost, call nothing
```

Run the whole pipeline that way first and read `agent_run.refused_json`. That produces a
real number for a real workload, which is the only honest input to choosing a ceiling.
Then:

```bash
RTF_PAID_ENABLED=1
RTF_DAILY_CEILING_USD=2.00     # a number you would not mind losing
RTF_PER_CALL_CEILING_USD=0.05
```

Ceilings go in `infra/terraform.tfvars` for the deployed function — gitignored, and never
in `-var` flags, because command-line arguments are visible in the process table.

**Raise the ceiling only after a dry run has produced the number.** A limit chosen before
measuring is either uselessly tight or uselessly loose, and the second one is the
expensive mistake.

---

## What has actually been spent

Nothing measurable, as of 2026-08-07:

- **No successful Bedrock call.** Every invoke returned `ThrottlingException` against a
  0 RPM quota. Zero tokens.
- **No deploy.** `terraform apply` has not been run against the current build.
- **CockroachDB**: three migrations and a handful of console reads, against a free
  allowance on a tier that costs $0 idle. Two test rows written to `demo_request` and
  deleted.
- **Cost Explorer**: not called, deliberately.

This section is a claim about the code and the commands run, **not a reading of the
bill** — checking the bill costs money, and confirming a $0 spend is not worth $0.01. Read
the Billing console by hand if you want the authoritative number.
