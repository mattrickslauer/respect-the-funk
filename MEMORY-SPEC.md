---
title: "Artist Memory (rtf.artist/v1, rtf.episode/v1)"
subtitle: "Onboarding an artist is expensive and happens once. Every video after that should be cheaper than the last, because the system remembers what worked. This is the memory layer that makes that true — and the second hackathon track it is submitted to."
status: "DECISION + DRAFT — adds a track to BUILD-SPEC §12. Reintroduces the database tier infra/README.md deliberately removed; that conflict is stated in §8, not hidden."
date: "2026-07-26"
deadline: "2026-08-18 17:00 EDT"
---

## 0. The decision

**Two hackathons, sequenced — not one submission sent twice.**

| | Backblaze Generative Media | CockroachDB × AWS: Build with Agentic Memory |
|---|---|---|
| Deadline | **2026-08-03** 17:00 EDT | **2026-08-18** 17:00 EDT |
| Criteria | Real-World Utility · Production Readiness · B2 + Data Orchestration · Use of Genblaze | Agentic Memory Design · Technical Implementation · Real-World Impact · Production Readiness · Creativity |
| Requires | B2 + Genblaze | ≥2 of 4 CockroachDB tools + ≥1 AWS service |
| This repo today | **on track** — [BUILD-SPEC §12](./BUILD-SPEC.md) | scores ~0 on criterion 1 |

The fifteen days between the deadlines are the whole opportunity. Ship Backblaze on Aug 3, tag it, then branch. Nothing on the CockroachDB track is allowed to touch the Aug 3 submission path before Aug 3.

**Rules position, checked:** neither hackathon forbids submitting a project to another. CockroachDB requires projects be *"newly created by the Entrant during the Submission Period"* (2026-06-30 → 2026-08-18); this repo's initial commit is **2026-07-26**, inside the window. Their rules also require disclosing pre-existing code incorporated into the work — §2 below is written to double as that disclosure.

---

## 1. Why this is the honest fit and not a bolt-on

[BUILD-SPEC §7b](./BUILD-SPEC.md) already stakes the company on one sentence:

> **Generate once, remix infinitely.** Expensive generative AI runs once per *release*. Cheap deterministic compositing runs once per *fan*.

[MEME-ENGINE](./content/MEME-ENGINE.md) makes the same move one layer down — *"a library is fetched once; after that a video is ffmpeg and a solver."* And [PRODUCT.md](./PRODUCT.md) step 2 makes it a third time, at the layer that actually matters commercially:

> Build that artist's identity once — structural facial features, reference frames across several lighting setups, wardrobe variants, anti-drift negatives, and signed likeness consent. **This is why the second video for an artist is cheap.**

That sentence is currently a *claim*. Nothing in the repo makes it true, measures it, or improves it over time. The identity is a YAML file that is read and never written back to; the second video is cheap only in the sense that somebody already typed the file.

**The memory layer is what converts that claim into a mechanism.** Onboarding an artist is a large, one-time, largely manual cost — reference photography across lighting setups, consent paperwork, a measured catalog, a screened clip library. That cost is only rational if it amortizes. It amortizes exactly to the degree that the system *accumulates* what it learns about the artist and *retrieves* it on the next generation.

> **The fixed cost is the mold. The memory is what makes the mold get better every time you pour into it.**

This is also the answer to the hackathon's headline criterion, arrived at from the product rather than at the product. We are not adding memory to win Agentic Memory Design; the product's central economic claim is unfalsifiable without it.

---

## 2. What is already memory (and is also the required disclosure)

Not starting from zero, and specifically not starting from zero on the hard part. What exists in `content/` today, as of this file:

| Artifact | What it already holds | Where it lives now |
|---|---|---|
| `*.clip.yaml` (`rtf.clip/v1`) | Per-clip *meaning*: `logline`, `audio.quote`, `beats[].means`, `cut_points[]`, `role.can_lead`, `rights.source` | Flat YAML in git, one sidecar per media file |
| `features` block on every clip | `energy`, `luma`, `contrast`, `warmth`, `saturation`, `detail`, `intensity_raw` — 7 computed scalars | Same sidecar |
| `axes` block on every clip | `light`, `motion`, `temp`, `scale`, each with value + categorical pole | Same sidecar |
| `character.yaml` | Cast identity: structural features, reference frames, `consent.people_release` | `lib/characters/` |
| Reference photography | 5 framings/lighting setups per person, plus face crops | `lib/characters/*.jpg` |
| `check_likeness.py` | Locates a face, crops it, scores the crop against reference crops on a fixed feature table | `bin/`, output to stdout |
| `screen_clips.py` | Per-pixel temporal variance + edge energy → overlay detection, with calibrated thresholds and an explicit abstain | `bin/`, output to stdout |

Corpus size today: **13 hook clips** (12 `dialogue`, 1 `stock`), a **15-clip `nocturnal`** library, **1 measured song**, **2 registered characters**.

**Read that table as the disclosure it is.** Everything above predates the CockroachDB track and will be declared as pre-existing work in the submission. What gets built for this track is §4–§6: the schema, the vector indexes, the write-back loop, the agent, and the retrieval that replaces `glob`.

---

## 3. What breaks — stated as a limit, not as a database sales pitch

The temptation is to claim the flat-file model is too slow. It is not. A linear scan over 13 sidecars is instantaneous and will still be instantaneous at 1,300. **Speed is not the problem, and saying it was would be the dishonest version of this document.**

Three things actually break, and none of them are fixed by a faster scan:

**1. Retrieval is by human eyeball.** `nate-test` chose its hook because a person read twelve loglines and picked one. That is a correct procedure for twelve. The query the format actually wants is *"a hook that promises a reveal, kinetic, mid-light, legally clean, with ≥2s of usable lead"* — half semantic, half numeric, half predicate. `grep` cannot do the first third, and no amount of directory structure makes it possible.

**2. The rights gate and the similarity search must be the same query.** [CLIP-SPEC](./content/CLIP-SPEC.md) rule 3 is unambiguous: `rights.source` is a hard gate, and *"a clip with no descriptor has no `rights.source` and is therefore unusable by construction."* A nearest-neighbour search that returns an unusable clip is not merely unhelpful — it is a rights trap that puts an unlicensed face in front of an editor who is now tempted. Filtered vector search (predicate and ANN resolved in one index, at interactive latency) is the requirement. A Parquet file in B2 fronted by Athena — [infra/README.md](./infra/README.md)'s current catalog answer — is an analytics path, not this.

**3. Nothing is ever written back.** This is the real one. `check_likeness.py`'s docstring records that its first version *"returned 5/5 'match' for every image a human had already rejected — including one that is plainly a different man,"* and explains precisely why: the face was too small, and the question invited a yes. That is a hard-won, artist-general lesson about generating this artist's face. **It currently lives in a Python docstring.** Every likeness score, every human rejection, every prompt that drifted is computed once, printed to a terminal, and lost. The next generation starts exactly as ignorant as the last.

A system whose central claim is *"the second video is cheap"* cannot discard everything it learned making the first one.

---

## 4. The data model

Four memory kinds. The taxonomy is standard; the mapping to this product is the part worth checking.

| Memory kind | Holds | Written | Read |
|---|---|---|---|
| **Identity** (semantic) | The mold: structural features, reference embeddings, wardrobe variants, anti-drift negatives, consent | Once at onboarding, amended rarely | Every generation |
| **Episodic** | One generation attempt: prompt, model, seed, params, likeness score, human verdict | After every attempt | By the distiller; by the dashboard |
| **Procedural** | What has worked: prompt fragments that held the face, cadences that landed, negatives that fixed a specific drift | Distilled from episodes | Every generation, as retrieved context |
| **Corpus** | The clip/hook library, vector-indexed, rights-predicated | At library fetch + screen | Every hook and payoff selection |

Schema sketch. All rows carry `tenant_id` — [BUILD-SPEC §2b rule 6](./BUILD-SPEC.md) is binding here and is the rule PRODUCT.md flagged as appearing **0 times** in `content/`. This is where that gets fixed.

```sql
artist(id, tenant_id, slug, name, status, created_at)                    -- the entity PRODUCT.md says does not exist yet

identity(id, artist_id, tenant_id, version, structural_json,             -- the mold
         consent_people_release, consent_signed_at, consent_doc_key,
         face_centroid VECTOR(512), created_at)                          -- centroid of accepted reference crops
identity_ref(id, identity_id, kind, b2_key, lighting, framing,           -- kind: reference|wardrobe|accepted_still
             face_embedding VECTOR(512), created_at)
identity_negative(id, identity_id, text, origin_episode_id,              -- anti-drift, learned not authored
                  hit_count, created_at)

clip(id, tenant_id, artist_id NULL, library, media_key,                  -- artist_id NULL = shared library
     descriptor_json, rights_source, people_release, minors_in_frame,
     can_lead, can_follow, min_useful_ms, duration_ms,
     energy, luma, contrast, warmth, saturation, detail,                 -- the 7 existing scalars, as columns
     axis_light, axis_motion, axis_temp, axis_scale,
     meaning_embedding VECTOR(1024), created_at)                         -- logline+quote+beats[].means

episode(id, tenant_id, artist_id, edit_id, kind, step_index,             -- kind: still|hook_pick|render|composite
        prompt, negatives_applied_json, provider, model, params_json,
        output_b2_key, manifest_b2_key,
        likeness_score, likeness_table_json,                             -- check_likeness.py output, kept
        human_verdict, verdict_at, verdict_by, cost_cents, created_at)   -- verdict: approved|rejected|unreviewed

lesson(id, tenant_id, artist_id NULL, scope, text, evidence_episode_ids, -- procedural memory, distilled
       confidence, supersedes_id, embedding VECTOR(1024), created_at)

approval(id, tenant_id, edit_id, state, actor, note, created_at)         -- draft|approved|published
```

Two notes on the vector columns, because the distinction is load-bearing:

- **`features` and `axes` stay as SQL columns, not as a vector.** They are seven hand-designed scalars with hand-calibrated meanings, not a learned embedding, and pretending otherwise would produce a 7-dimensional "embedding" whose nearest neighbours mean nothing. They are excellent *filter and rank* predicates. That is how they get used.
- **The embeddings are text and face.** `meaning_embedding` comes from the descriptor's prose — `title`, `logline`, `audio.quote`, `beats[].means` — which is the part a human actually reads when choosing a hook. `face_embedding` comes from the crops `check_likeness.py` already produces, so the drift check and the index share one representation rather than two that can disagree.

**`approval` closes a gap that predates this track.** [PRODUCT.md](./PRODUCT.md) "what we do not have yet" #2 is *no approval state*, and [infra/README.md](./infra/README.md) caveat 4 concedes it *"is the first thing that will want a real row somewhere."* It gets that row here.

---

## 5. The four queries that justify the index

Retrieval is the product surface. These are the queries; if they are not obviously better than a directory listing, this track is not worth building.

**Q1 — Hook selection.** *Find a hook that promises a reveal, kinetic, mid-light, legally publishable, with ≥2s of usable lead.* Semantic ANN over `meaning_embedding`, filtered in the same query by `rights_source IN (...) AND people_release AND can_lead AND min_useful_ms >= ?`, ranked with the `axes` scalars. This is the query that replaces reading twelve loglines, and it is the one that makes a 500-clip library usable at all.

**Q2 — Payoff congruence.** *Given this hook, find library clips that invert the ground while holding the figure.* [FORMAT-SPEC](./content/FORMAT-SPEC.md)'s geometry expressed as a vector operation: near on subject, far on `axis_light`/`axis_temp`. Today [MEME-ENGINE](./content/MEME-ENGINE.md)'s solver works over a 15-clip library it can hold entirely in memory. It cannot hold a catalog.

**Q3 — Likeness drift.** *Is this generated still within ε of this artist's identity centroid?* Vector distance from `face_embedding` to `identity.face_centroid`, plus the nearest accepted stills as few-shot evidence. This is `check_likeness.py`'s job, done against accumulated history instead of against five fixed reference photographs — and it directly attacks the failure that script documents, where a small face and an agreeable question produced 5/5 false matches.

**Q4 — Catalog operations.** *Which songs have no approved edit? Which artists' consent expires this quarter? What did this kit cost per approved still?* Ordinary SQL over `approval`, `episode`, and `artist`. This is the label's actual daily job, it is relational, and it is the half of the system that does not want a vector at all.

**Q1–Q3 need a vector index. Q4 needs joins. Needing both in one store, partitioned by tenant, is the honest reason for this database rather than a bucket.**

---

## 6. The agentic loop — where memory is a loop and not a table

A database that is only read is a catalog. The loop is what makes it memory:

```
  retrieve            generate           measure            judge            distil
  ────────            ────────           ───────            ─────            ──────
  identity +    ──▶   still /      ──▶   check_          ──▶ human      ──▶  new
  lessons +           hook pick /        likeness            approve /       identity_negative
  negatives           render             Q3 + screen_        reject          or lesson
  (Q1–Q3)                                clips                                    │
      ▲                                                                           │
      └───────────────────────────────────────────────────────────────────────────┘
```

Every arrow already exists as a script except the last one. `generate_stills.py` generates, `check_likeness.py` measures, a human judges in the terminal — and then the loop is cut, because nothing writes back. Closing it is roughly: persist an `episode` row per attempt, capture the verdict instead of printing it, and run a distiller that turns repeated rejections into an `identity_negative` or a `lesson` that Q1–Q3 retrieve next time.

**The demo metric, and it is a real one:** *attempts per approved still, per artist, over time.* If memory works, that number falls as an artist accumulates episodes — video 5 costs measurably less than video 1, for the same artist, at the same quality bar. That is [PRODUCT.md](./PRODUCT.md)'s "the second video is cheap" turned into a chart, and it is exactly what the Agentic Memory Design criterion is asking to see.

⚠️ **The risk on that metric is stated up front.** There is **one artist, one song, and 22 stills** in this repo. By Aug 18 there may not be enough episodes for the curve to be statistically meaningful. The mitigation is to instrument it honestly and show the mechanism with whatever N exists, labelled with its N — **not** to generate a flattering curve. This repo's own house style is `screen_clips.py` refusing to judge a locked-off shot rather than guessing; the same rule applies to our own demo.

---

## 7. Requirement mapping

**CockroachDB tools — need ≥2 of 4:**

| Tool | Use | Status |
|---|---|---|
| **Distributed Vector Indexing** | Q1–Q3: `meaning_embedding`, `face_embedding`, `lesson.embedding`, with rights predicates in the same query | **Core — required** |
| **Cloud Managed MCP Server** | The agent's and the label's read path into the catalog: Q4 in natural language over `approval`/`episode`/`artist` | **Core — required** |
| ccloud CLI (agent-ready) | Cluster + branch provisioning in the deploy path | Comes free; claim it |
| Agent Skills repo | Wrap `rtf.py` resolve / measure / render as skills | Optional, only if time |

**AWS — need ≥1, we have five:** Lambda (`web`), SQS (job queue + DLQ), Batch on Fargate Spot (the generator), SSM Parameter Store (secrets) are all already in [infra/README.md](./infra/README.md). **Add Bedrock** for embeddings and as the agent runtime — it is the piece that makes "agentic" a runtime fact rather than a description.

**Judging criteria:**

| Criterion | The claim |
|---|---|
| Agentic Memory Design | Four memory kinds, a closed write-back loop, and a falsifiable metric (attempts-per-approved-still) |
| Technical Implementation | Filtered vector search where the filter is a legal gate, not a nicety; one store serving both ANN and joins |
| Real-World Impact | A label's actual bottleneck. PRODUCT.md: *"at fifty songs that manual step, not compute, is what limits throughput"* |
| Production Readiness | Scale-to-zero AWS already designed; tenant as partition key; cost ledger per episode; idempotent keyed jobs |
| Creativity & Originality | Memory of a *person's appearance* as the retrieval target — vector search against a consented likeness, with the consent in the same row |

**Submission requirements:** public repo under MIT/Apache-2.0, a functional demo URL, a <3 min video, and documentation naming the tools used. The licence is an open decision (§10) — this repo has none today.

⚠️ **Rights caveat on the public demo URL.** All 12 `dialogue` hooks carry `rights.source: youtube` with `people_release: false`, and their own descriptors warn to *"check before publishing or before holding a likeness invariant in a payoff."* They are legitimate as a **retrieval corpus** in the demo — showing Q1 rank them is fine — but nothing built from them may be published from a public URL. The published half of the demo runs on owned masters and AI/consented assets only. This is [CLIP-SPEC](./content/CLIP-SPEC.md) rule 3 applied to ourselves.

---

## 8. What this costs us — the conflict with the no-database decision

Stated plainly because it contradicts a decision made three days ago and should not be discovered later.

[infra/README.md](./infra/README.md) says, as its proudest line: *"The move that made it simple: there is no database"* — and derives *"realistic idle cost: under $1/month"* from having no compute or database floor anywhere. **This track reintroduces the tier that decision removed.**

The argument that it is still the right call:

1. **The removal was conditional and the condition is named.** That file's own text: *"When to revisit: the moment role 2 comes back"* and *"if it turns out to need queryable state…"*. Approval state (§4) is queryable state, and it was already on the books as unresolved before this hackathon existed.
2. **The reasoning that removed the database does not cover this.** It removed Postgres because attribution joins, leaderboards, and reward ledgers — all fan-side, all deferred — were the only relational pressure. Vector retrieval over a clip corpus is a different requirement that the analysis simply did not consider, because at 13 clips it did not exist.
3. **It is a branch, not a rewrite.** Both architectures stay documented and generated by `diagram.py`, exactly as `deferred-marketplace.pdf` already sits beside `architecture.pdf`. The Aug 3 submission ships the no-database architecture unchanged.

⚠️ **Open, and deliberately unasserted:** what CockroachDB Cloud actually costs at idle for this workload. The $1/month claim does not survive an unverified assumption, so it is not being restated until the number is checked against the free/serverless tier. See §10.

---

## 9. Plan — Aug 3 → Aug 18

Fifteen days, and the first is spent not writing code.

**Aug 3 — Ship Backblaze. Tag `backblaze-submission`.** Branch after, never before.

**Days 1–2 — Provision and prove one round trip.** ccloud cluster, schema from §4, Bedrock embedding access. One clip descriptor → embedded → stored → retrieved by Q1 with a rights predicate applied. Nothing else counts until that works.

**Days 3–5 — Backfill and make Q1/Q2 real.** All 13 hooks and the 15-clip `nocturnal` library ingested with descriptors, features, and axes. `screen_clips.py` results stored rather than printed. Hook selection in the pipeline switches from filename to Q1.
⚠️ *Risk: embedding a 28-clip corpus proves nothing about ANN quality. Mitigation: synthesise descriptors for a larger corpus to exercise the index, and label them as synthetic wherever they appear.*

**Days 5–8 — Identity as memory, and close the loop.** `character.yaml` → `identity` + `identity_ref` with face embeddings. `check_likeness.py` writes `episode` rows instead of stdout. Human verdict captured. The distiller turns repeated rejections into `identity_negative`, and `generate_stills.py` retrieves them.
*This is the core. If everything after it slips, the submission still has its headline criterion.*

**Days 8–11 — The agent and MCP.** Bedrock agent over the CockroachDB MCP server: Q4 in natural language, plus a generation loop that retrieves memory before it prompts. Approval state wired to the label view.

**Days 11–13 — Amortization evidence + dogfood.** Generate a fresh edit for the existing artist with memory on, one with it off, and record attempts-per-approved-still for both. Whatever the number is, it is the number that gets shown.

**Days 13–14 — Package.** Licence, README, tools-used documentation, architecture diagram via `diagram.py`, <3 min video, public demo URL with the §7 rights caveat respected.

**Day 15 — Buffer. Submit Aug 17 EOD if possible.**

---

## 10. Open decisions

1. **Licence.** MIT or Apache-2.0 is required and the repo has neither. Apache-2.0 recommended (patent grant, and it is the safer default for anything with a commercial future). Needs a call before anything is made public.
2. **CockroachDB Cloud idle cost** — verify against the free/serverless tier before §8's economics are restated anywhere.
3. **Embedding models.** Bedrock Titan for text is the low-friction default; the face embedding is the real question, since `check_likeness.py` currently uses a vision model's judgement rather than a metric embedding, and a 512-d face vector is a new dependency rather than a refactor.
4. **Public repo timing.** The CockroachDB submission requires a public repo. Does that happen before or after Aug 3, given the Backblaze submission is in the same repo?
5. **Does the artist entity land on the Backblaze track too, or only here?** PRODUCT.md's proposed `lib/artists/<artist>/…` layout is good independent of this hackathon. Doing it once, before the branch, avoids doing it twice.
6. **Synthetic corpus** for exercising the index — acceptable, and how it gets labelled in the demo so it is never mistaken for real inventory.

---

## 11. What this deliberately is not

- **Not a second product.** It is the missing half of the one in [PRODUCT.md](./PRODUCT.md) — the half that makes "build the identity once" pay off more than once.
- **Not a recommender.** No model predicts which clip performs. [Non-goals in PRODUCT.md](./PRODUCT.md) kill "which archetype wins" as folklore, and retrieval is not a loophole around that. Q1 finds clips that *satisfy stated constraints*; it does not rank them by predicted reach.
- **Not a reason to publish third-party faces.** §7's rights caveat is a hard gate, applied to our own demo exactly as CLIP-SPEC rule 3 applies it to a kit.
- **Not a replacement for the human verdict.** The loop's judge step is a person. `check_likeness.py` exists precisely because an agreeable model said yes five times out of five; a memory layer that learns from its own unaudited scores would industrialise that error rather than fix it.
