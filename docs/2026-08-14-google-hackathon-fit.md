---
title: "All Things Agentic — fit, eligibility, and the seventeen days"
subtitle: "The Google hackathon judged against what is actually deployed, not what is written. Three mandatory requirements are currently unmet and none of them is hard. One judging criterion carries 40% of the score and we have an N of 1 against it — that is the real problem, and it is not a porting problem."
status: "FINDINGS — 2026-08-14. Deadline 2026-08-31 17:00 PDT, seventeen days out, thirteen of them after the CockroachDB submission ships. Four decisions outstanding, listed in §9."
date: "2026-08-14"
---

## The one-sentence result

**We are ineligible today and a credible contender by the 31st** — the three mandatory
Google requirements are all satisfied by swaps this architecture was already built to
absorb, and the thing that would actually cost us the money is not the port, it is that
the 40%-weighted criterion asks how much friction the agent removes *on its own* and our
answer is currently one conversation.

---

## 1. The three hard gates — all three unmet

These are stated in the rules as requirements, not as preferences. A submission missing
any one of them is not scored badly, it is disqualified.

| Requirement | Rule text | State today | Cost to close |
|---|---|---|---|
| **Gemini** | "Gemini 3.5 or newer accessed through Gemini API or Vertex AI" | **unmet** — the only embedded party carries `openai:text-embedding-3-small`; `bedrock.py` cannot run on this account | **low** |
| **Google agent framework** | at least one of ADK, GenAI SDK, Antigravity SDK, GenKit 3 | **unmet** — no Google SDK in the tree | **low–medium** |
| **Google Cloud service** | "Cloud Run, Cloud SQL, Firestore, GKE, Pub/Sub" or similar | **unmet** — Lambda + S3, CockroachDB Basic on AWS `us-east-1` | **medium** |

There is no partial credit here and no argument to make. `docs/submission/TOOLS.md` is a
page about AWS.

### Why the first one is nearly free, and worth saying out loud to a judge

`embed.py` is one port with three adapters, and `embedding_model` is the **second prefix
column** of `party_shortlist` — a design decision made in migration 009 for a different
reason, which happens to make provider migration mechanical:

> "CockroachDB will not walk across a prefix it is filtering by equality, so an OpenAI
> vector is not merely *unlikely* to be compared against a Titan one, it is unreachable
> from a query that asked for the other model."

A fourth adapter and a re-embed of 14,170 parties under `google:…` gives us both models
live in the same index simultaneously, with no backfill lock, no downtime, and no window
where a shortlist can silently mix vector spaces. **That is a story for the video**, not
just a migration: the architectural-discipline criterion is 30% and this is the cheapest
evidence for it we own.

> **Verify before relying on it:** `party_fact.embedding` and `party_chunk.embedding` are
> `VECTOR(1024)`, fixed by schema and chosen when Titan was the assumed provider. The
> Gemini embedding model must be confirmed to emit 1024 dimensions (via its output
> dimensionality parameter) **before** this is scheduled as cheap. If it cannot, this
> becomes a schema migration across four vector indexes and moves from a half-day to two
> days. Do not plan the week on the assumption.

---

## 2. The eligibility question that could void everything

> "Projects must be newly created during the Submission Period. Participants may use
> standard development tools… but must disclose any other pre-existing code or work
> incorporated into the Project."

**Submission period: 2026-08-03 → 2026-08-31.**

| | Date | Inside the window? |
|---|---|---|
| Repository initial commit | 2026-07-26 | **no** |
| `docs/SCOPE-RESET.md` — the greenfield decision | 2026-08-06 | yes |
| First commit touching `platform/` | 2026-08-06 (`a6ba8bb`) | **yes** |
| Commits since 2026-08-03 | 141 of 238 | — |

**The platform — the thing being submitted — was created entirely inside the window.**
`app/` and `content/` (RemixKit) predate it and are already disclosed in `NOTICE` and
`SCOPE-RESET.md`. This is the identical position we took for CockroachDB and it held.

It must be stated *in the submission text*, not left for a judge to reconstruct from a git
log. One paragraph: what was built in-window, what predates it, where the disclosure lives.
We have written that paragraph once already; it gets rewritten, not reused, because the
window is different.

---

## 3. Track fit, ranked

Four of the nine prize categories are worth entering. The submission picks one.

### Fortified Enterprise Fleet — $20,000 — **the strongest fit in the field**

The track asks for "a scalable network of institutional agents" across four pillars. The
mapping is close to point-for-point, which is unusual enough to be worth being suspicious
of — so here it is with the gaps named:

| Pillar | What we have | Gap |
|---|---|---|
| **Discovery & Lifecycle** (registry) | Leads become work when `next_action_at` passes; agents claim by lease; `agent_run` records every one | **No agent registry.** The fleet is 8 agents known by convention, not a table. This is the one real gap and it is cheap. |
| **Core Execution** (runtime/memory) | Memory *is* the coordination substrate. Agents never call each other. Memory write + `agent_run` + lead completion commit in one transaction | none — this is the best thing we own |
| **Security** (identity, gateway, guardrails) | `opted_out` terminal and unresurrectable; route allowlist as a SQL predicate "so no caller can forget it"; `inferred` routes refused outright rather than deprioritised; spend gate fails closed; `tenant_id` leading every index; seven `robots.txt` refusals honoured | identity/gateway is thin — `auth.py` only |
| **Telemetry** (observability) | 55,569 agent runs, cost per run, $0.12 lifetime, 33%-era failure rates visible rather than hidden | no Cloud Logging / Trace integration — worth adding *because* it is the Google-native answer |

### Best Architectural Design — $5,000 ×2 — **high probability**

Two winners, and the criterion rewards exactly the discipline this repository already
practises to an unusual degree: the adversarial sponsor audit, the "what this submission
does not claim" section, `changefeed.py` refusing to start a continuous RU draw without a
human decision. Judges remember the project that argued against itself.

### The Taskmaster — $20,000 — **good fit, one framing risk**

"Build a complete workflow, not just a chatbot… sends the right info to the right places"
describes outreach precisely. The risk is §4 below.

### Startup Excellence — $20,000 — **decision required**

Requires incorporation and a corporate email address. If Respect the Funk is incorporated
this is a $20,000 category with a probably-thinner field than the open tracks. **This is
the cheapest unanswered question on the page** — see §9.

**Grand Prize ($50,000)** is not a category you enter, it is the best submission overall.
Worth building toward, not worth planning around.

---

## 4. Scored against the three criteria

### Innovation & Operational Utility — 40% — **this is where we lose**

> "How much real-world friction does the agent remove **on its own**? We reward
> autonomous, high-value action over simple chat."

`SUBMISSION.md` states our position honestly and it is the right position for CockroachDB,
whose tie-breaker was memory *design*:

> "This system has held exactly one conversation… the argument below is not that we ran
> thousands — it is that every guarantee which makes thousands *safe* is a database
> constraint."

**That argument does not score against this criterion.** "Every guarantee is a constraint"
is an architecture claim, and architecture is the *other* 30%. This criterion is asking for
friction removed, measured, and it is weighted heaviest of the three. One thread and one
send is not an answer to it.

Two things fix it, and only one of them is writing:

**a. Run the fleet for real, at volume, between the 18th and the 26th.** This is the
single highest-value work in the entire seventeen days and it is worth more than any of
the porting. We hold 14,170 counterparties, 2,351 contact routes, 439 reachable. A
sanctioned campaign that actually opens hundreds of conversations converts our weakest
criterion into our strongest, and it needs no new code — the constraints that make it safe
are already in the database and already audited.

**b. Reframe the human gate before a judge reframes it for us.** "Every message waits for
a person" reads as *less autonomous* to a criterion that rewards autonomous action, unless
we state the boundary. The gate is on **sending**. Everything before it — discovery,
role classification, enrichment, route extraction, provenance assignment, shortlist
ranking, re-ranking as lessons land, scheduling — is unattended. The number to produce and
say on camera is of this shape:

> *The fleet took N counterparties from unknown to approved-ready with nobody watching.
> A human touched only the send decision — M of them, in T minutes.*

That is an operational-utility number. "One conversation, held safely" is not.

### Architectural Discipline & Tech Stack — 30% — **strongest, once it is Google's stack**

"Engineering rigor, system decoupling, state management, security, failure handling" is a
list of things we have receipts for. Keeping CockroachDB and moving it to a GCP region is
also a *differentiator* here rather than a liability — the field will be thick with
Firestore and Cloud SQL, and `AS OF SYSTEM TIME` over a vector index answers "why did the
agent decide that" in a way none of them can. The accountability argument survives the port
intact. It was never an AWS argument.

### Demo & Production Readiness — 30% — **mostly mechanical, one hard dependency**

Video clarity, repo quality, architecture diagram, setup reproducibility, **and Google
Cloud deployment proof**. We have the first four as a matter of routine. The fifth requires
the backend to actually be on Cloud Run with a dashboard we can film, which makes the port
a *video* dependency and not just a code one — and therefore something that must be done
by ~the 27th, not the 30th.

---

## 5. What the port actually costs

Ranked by score-per-hour, which is not the same order as difficulty.

1. **Gemini as the inference provider.** Replaces the Bedrock path that has never run on
   this account — we are not removing a working component, we are filling a hole that
   `embed.py` and `bedrock.py` both document at length. Satisfies the Gemini requirement,
   and via the **GenAI SDK** satisfies the framework requirement at the same time.
2. **Gemini embeddings as a fourth adapter** (§1). Re-embed 14,170 under the new model
   value; both live in one index. Gated on the 1024-dimension check.
3. **Cloud Run.** `apps/spindle/web` is FastAPI behind Mangum. Cloud Run wants the container
   and drops the adapter — this is a simplification, not a migration. Satisfies the Google
   Cloud requirement on its own and produces the dashboard the video must show.
4. **CockroachDB Cloud on a GCP region.** Recommended, not required. Leaving the cluster on
   AWS makes "the backend runs on Google Cloud" a partial claim in a submission whose whole
   credibility rests on not making partial claims. The harvest is reproducible (FCC register,
   Radio Browser), so this is a fresh cluster plus migrations plus a reload, not a data
   rescue. **Budget two days and start it early** — it is the item most likely to surprise us.
5. **ADK, and the one thing not to do with it.** ADK would score better than GenAI SDK alone
   under "architectural discipline". The trap: our entire thesis is that agents do not call
   each other and the database is the coordination substrate. Adopting ADK as an
   *orchestrator* contradicts the submission. Adopt it as the **in-turn runtime** — ADK is
   what happens inside one agent's turn (reasoning, tool calls); CockroachDB is what happens
   between turns (waking, claiming, committing). Said in one sentence on camera, that
   distinction is a point in our favour rather than a compromise.
6. **Pub/Sub + Cloud Scheduler** to wake workers. Named explicitly in the rules. Optional,
   and it partially re-opens the changefeed question we deliberately closed.
7. **The agent registry table** (§3). Cheap, and it is the Fortified Fleet track's first
   pillar.

### The bonus points are unusually well-aligned, and one of them is strategic

The rules give bonus credit for **Gemma, Veo, or Lyria**. We are a record label with a
dormant media-generation subproject:

- **Veo** — `content/` and `app/` already generate clips through a provider adapter
  (`provider_sora.py`, `providers.py`). Veo is another adapter.
- **Lyria** — music generation, in a music company.
- **Gemma** — `apps/spindle/classifier/` classifies 14,170 parties. A small model is the right
  size for that job on the merits, not just for the bonus.

The strategic part: wiring Veo re-activates **RemixKit as the payoff stage of the
pipeline**. The CockroachDB submission ends at "we mailed a real human being." The Google
submission can end at *"…they said yes, and the assets for that placement generated
themselves"* — which is a materially better answer to "build a complete workflow, not just
a chatbot," and it is the product as `SCOPE-RESET.md` actually defines it: analyse once,
drive many processes, chainable budgeted stages.

---

## 6. Tomorrow's shoot — how to record once and ship twice

The founder is on camera tomorrow, 2026-08-15, for a deadline on the 18th. A second
submission is due on the 31st. **Footage of a person is the one asset that cannot be
regenerated later** — lighting, wardrobe, energy and framing will not match a pickup shoot
two weeks out, and mismatched intercuts read as amateur in a criterion that scores video
clarity at 30%.

**The length constraint differs and it is not a rounding error.** CockroachDB requires
**under 3:00**. Google evaluates **only the first 4:00**. These are different cuts of the
same shoot.

Six rules for the day:

1. **Shoot in segments, not one take.** Clean head and tail on every beat, two seconds of
   silence either side. A 2:50 cut and a 3:55 cut assemble from segments; they cannot be
   trimmed out of a continuous read.
2. **Record every stack line twice.** One take saying *AWS Lambda, S3*; one take saying
   *Cloud Run, Vertex AI, Gemini*. Also OpenAI-embeddings vs Gemini-embeddings. It costs
   ninety seconds tomorrow and saves a reshoot that would not cut together.
3. **Record the problem statement and the close sponsor-free.** Those beats are reusable
   across both submissions and every pitch after them. No sponsor name in either.
4. **Record the scale beat.** The framing is right and it is the Google cut's opening
   move — *any business where a small team must hold thousands of independent
   conversations*: recruiting, BD, grant outreach, clinical trial recruitment, franchise
   operations, artist relations. Twenty seconds, shot standalone. It is a **horizontal**
   claim, which is what the Google judges reward and what the CockroachDB cut deliberately
   did not lead with.
5. **Record the autonomy line as a blank.** §4b's number does not exist yet. Shoot the
   sentence with the figure left for a lower-third graphic rather than spoken, or shoot it
   the day the campaign runs. Do not speak a number tomorrow that the cluster will
   contradict on the 26th.
6. **Log the setup.** Camera position, lens, height, lighting, wardrobe, mic. Photograph
   it. The pickup shoot around the 28th has to match.

**What cannot be shot tomorrow:** the Cloud Run dashboard and Google Cloud logs. That is
b-roll, captured after the port, and it is a hard requirement of the Google submission.

---

## 7. The thirteen days after the 18th

| Dates | Work |
|---|---|
| Aug 15 | **Shoot.** Both stacks, segmented, per §6 |
| Aug 16–17 | CockroachDB cut (<3:00), submission polish |
| **Aug 18** | **CockroachDB submission, 17:00 EDT** |
| Aug 19 | 1024-dimension check (§1). Gemini inference via GenAI SDK |
| Aug 20 | Gemini embedding adapter; re-embed 14,170; both models live in one index |
| Aug 21–22 | CockroachDB Cloud on GCP: cluster, migrations, reload, re-verify `EXPLAIN` plans |
| Aug 23 | Cloud Run deploy; drop Mangum; Cloud Logging |
| Aug 24 | ADK as in-turn runtime; agent registry table |
| **Aug 25–26** | **Run the fleet at volume.** The 40% criterion's evidence. Highest value in the plan |
| Aug 27 | Veo adapter (RemixKit payoff stage); Gemma on the classifier as far as it honestly goes |
| Aug 28 | Pickup shoot + Cloud Run / logs b-roll |
| Aug 29 | Architecture diagram regenerated for GCP; README setup guide; `TOOLS.md` equivalent for Google |
| Aug 30 | Cut to 4:00, submission narrative, disclosure paragraph (§2) |
| **Aug 31** | **Submit, 17:00 PDT** — with a day of buffer already spent |

The plan has no slack. The two items most likely to consume it are the GCP cluster move and
the volume campaign, which is why they are scheduled early and given two days each.

---

## 8. What would lose

Written in the house style, because the version of this document that only lists strengths
is the one we would regret.

- **Porting and not running.** A perfect Google stack with one conversation behind it loses
  40% of the score to a rougher project that demonstrably removed real work. If the week
  compresses, cut the Veo bonus and the GCP cluster move before cutting the campaign.
- **Adopting ADK as an orchestrator** and contradicting our own architecture on camera.
- **Claiming scale.** The temptation is worse here than it was for CockroachDB, because the
  track is called Fortified *Enterprise* Fleet and the user's instinct — correctly — is to
  make the scale obvious. Make the *shape* obvious; keep stating the N. `HACKATHON.md`'s
  "what would lose" line survives the change of sponsor unaltered.
- **Reusing the CockroachDB narrative.** Its tie-breaker was memory design. This one's
  heaviest criterion is operational utility. Same system, different argument, and the
  document has to be rewritten rather than search-and-replaced.
- **Missing the disclosure paragraph** (§2) and having a judge find the July commits.

---

## 9. Decisions outstanding

1. **Is Respect the Funk incorporated, with a corporate email?** Gates the $20,000 Startup
   Excellence category. Cheapest question on this page.
2. **Which single track do we submit to?** Recommended: **Fortified Enterprise Fleet**, on
   the §3 mapping, unless decision 1 resolves yes and the Startup field looks thinner.
3. **Does the cluster move to GCP?** Recommended yes, scheduled Aug 21–22, first thing cut
   if the campaign slips.
4. **What is the sanctioned volume for the Aug 25–26 campaign?** A human decision about
   contacting real people at a scale we have not previously operated at, and it is not the
   fleet's to make. It needs answering before the 24th or the schedule fails silently.

---

## Method

Hackathon requirements read from the Devpost overview and rules pages on 2026-08-14 and
quoted verbatim where they are load-bearing. Repository claims checked against the tree and
the git history in this session: `platform/`'s first commit is `a6ba8bb`, 2026-08-06; 141
of 238 commits fall after 2026-08-03. Cluster figures are carried from
`docs/submission/SUBMISSION.md` dated today and were not independently re-executed here.
The 1024-dimension constraint is flagged as unverified rather than assumed, per the house
rule.
