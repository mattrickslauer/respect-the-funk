# The architecture document

`spindle-architecture.pdf` — five landscape pages, for a judge who wants the
whole system rather than the pitch.

| Page | What it answers |
|---|---|
| 1 | What is actually deployed, end to end — two Lambdas, one bucket, one mail identity, one database |
| 2 | The data plane: 54 tables, and the four vector indexes with their prefixes written out |
| 3 | The control plane: how work is claimed, how it survives a crash, and what stops a paid call |
| 4 | The decision ledger, and how "why did you do that, then?" is answered from the index itself |
| 5 | What is written and deliberately **not** switched on, with the command that proves each one |

```bash
python3 build.py        # architecture.html -> spindle-architecture.pdf
```

The PDF is committed, unlike the deck plates in `content/deck/out/`. It is 230 KB,
it is a thing a judge downloads rather than a build artefact, and `infra/` already
carries its diagrams the same way.

## Where the facts come from

Nothing on these pages was typed from memory:

- **AWS resources, sizes and names** — read out of the applied
  `infra/terraform/spindle/terraform.tfstate`, so the page describes what exists
  rather than what the plan would create. Only resource types present in that
  state are drawn; SQS, Batch and API Gateway appear in the module tree and are
  therefore *not* on page 1.
- **Tables and index prefixes** — read out of `apps/spindle/schema/*.sql`. The
  four prefixes on page 2 are transcribed exactly, in column order.
- **The agent list** — `agents.REGISTRY`, which is 17 entries, not the 15 an
  older document says.
- **The ledger and its seven kinds** — `035_decision_ledger.sql`, including the
  `CHECK` that enumerates them.

## The rules it inherits

**Purple is reserved**, the same as in `content/deck` and the film: `--crdb`
marks the filters living inside the index (page 2) and reading that index as of a
past instant (page 4). Nowhere else — not even on the box that *is* the database.
Spending it on the substrate would make the two irreplaceable claims look like
more of the same.

**Status is drawn, not captioned.** Dashed outline plus an amber chip means
written and not switched on, and page 5 pairs each one with the command that
returns nothing.

## Why `build.py` fails the build

The same reasoning as `content/deck/render.py`, with more to check because these
pages are denser. A caption clipped by its own box still looks deliberate, so the
build measures instead of trusting:

- every `<text>` against the `viewBox` it is drawn in,
- **every `<text>` against the box that owns it** — the commonest failure here,
  and invisible at page scale,
- every pair of boxes on a page, for overlap (nesting is allowed, touching is not),
- the foot rail, which is one line by design,
- `.fig` and `.page`, for content that spills without growing its parent,
- and any page error at all.

`box()` treats a passed `h` as a **minimum**: content is never clipped, the box
grows instead, and the overlap check turns that growth into a failure rather than
a silent collision. Between them those two rules caught every layout bug in this
document.

One thing worth knowing, because it cost a full round: an unterminated `<script>`
means the browser never executes it, so the checks pass against a page that drew
nothing at all. If a page renders empty and the build is green, look at the tags
before you look at the drawing code.
