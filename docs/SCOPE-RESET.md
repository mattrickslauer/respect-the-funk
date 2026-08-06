---
title: "Scope Reset — the platform is the product, RemixKit is a subproject"
subtitle: "This repository's specs describe a media generation suite. That is no longer the thing being built. This document declares the reset, fixes the artist as the spine, and states which existing documents are binding, which are reference, and which are void."
status: "DECISION — voids the plan status of PRODUCT.md, BUILD-SPEC.md, MEMORY-SPEC.md, MINDS-SPEC.md and the content/ specs. Research pillars 01–13 keep their status as findings. Until this document is extended, nothing in this repository describes the system being built."
date: "2026-08-06"
---

## 0. The decision

**We are greenfielding a superset.** The product is a platform for taking an artist's catalogue to an audience: register an artist, add their tracks, analyse each track once, and drive many outreach processes off that one analysis — UGC creator seeding, radio, playlist curators, press, sync, paid.

**RemixKit is now a subproject inside that platform** — the thing that generates a release's assets. It is one consumer of the spine, not the spine.

Read this document as a stop sign on the existing plan documents. They were written for a different product and several of them are internally excellent, which makes them more dangerous rather than less: they will pull design decisions toward a scope we are no longer building. §4 states exactly what status each one now holds.

---

## 1. The spine is the artist

Fixed here because it is a partition-key-class decision. [BUILD-SPEC §2b rule 6](./BUILD-SPEC.md) already flagged this category as *"near-impossible to retrofit"* about `tenant`, and it was right; the same applies to the root of the hierarchy.

```
tenant  →  artist  →  track  →  derived facts
                   ↘  counterparty relationships
                   ↘  audience model
                   ↘  accumulated lessons
```

**Why the artist and not the track.** The system's economic claim is that release *n+1* is cheaper and lands harder than release *n*. That is only true if something accumulates between them. What accumulates is not the audio — it is the counterparty relationships (which creators said yes, which programmers replied, who would work with this artist again), the audience model, and the lessons learned from previous campaigns. All three belong to the artist and are inherited by each new track.

A track-rooted schema restarts cold on every release and throws that compounding away.

**What this costs.** A genre-hopping artist whose tracks target genuinely different audiences is modelled worse this way — the artist-level audience model becomes an average of things that should not be averaged. Accepted deliberately: a track carries its own derived facts and can carry its own audience position, it simply *inherits* the artist's by default rather than starting empty. Revisit if the roster turns out to be wider than the model tolerates.

---

## 2. Analyse once, drive many processes

The backbone is persistent derived data. A track is analysed once and the analysis is durable; every downstream process is a **query against those facts**, never a re-analysis.

**What gets derived from one track, once:** musical measurement (BPM, key, drop, hook window, energy curve, sections), semantic character (mood, era, reference artists, an embedding), lyrical content where it exists, rights and splits, and an audience hypothesis.

**Why the processes unify.** A UGC creator, a radio programmer, a playlist curator, a sync supervisor and a music writer look like five different problems. They are the same shape: *a party with a taste profile, an audience, a contact method, a relationship history, and a state in a conversation.* Different playbooks, identical skeleton.

So the platform is **one counterparty index and one outreach engine**, queried with a track's derived facts — not six channel-specific tools that happen to share a database. This is the difference between building six things and building one.

### 2a. The two rules that make this real rather than aspirational

**Rule 1 — every fact carries how it was obtained.** Facts have wildly different half-lives. BPM is permanent, rights are semi-permanent, a creator's follower count is stale within weeks, an audience hypothesis is provisional and *should* be revised. Stored identically, the stale ones silently poison decisions made on the fresh ones.

Three provenance classes, on every fact in the system:

| Class | Means | Example |
|---|---|---|
| **measured** | Computed from the artefact itself | BPM from the audio; median views from public posts |
| **inferred** | A model's estimate | Audience position; vendor demographic shares |
| **asserted** | A human stated it | Rights, splits, likeness consent, a rate agreed on a call |

An inferred value may never overwrite a measured one, every fact carries a timestamp, and the interface must render the three classes distinguishably. This is [Pillar 10 §7](./research/10-creator-indexing.md)'s discipline — *"never render an estimated share next to a measured one"* — promoted from one table to a system-wide invariant.

**Rule 2 — processes contribute facts, they do not only consume them.** This is the rule the repo has already failed once, and [MEMORY-SPEC §1](./MEMORY-SPEC.md) diagnosed the failure precisely: *"the identity is a YAML file that is read and never written back to."* "Analyse once" is worth nothing if the analysis is write-once and read-only.

The version that works: when radio outreach hears "too long for daytime rotation" three times, that is a fact about the track, discovered by the radio process, that the UGC process and the asset generator both want. Every process is both a reader and a writer of the spine.

---

## 3. Agentic by design

The platform runs as a fleet of long-running agents with purposes, not a set of scripts a human triggers. Agents do not call each other; they read shared memory, act, write results back, and those writes wake the next agent. The persistent store is simultaneously the fleet's memory, its state, its coordination lock, and its event bus.

This is a claim about architecture, not a feature list, and it is stated here so the schema is designed for it from the start rather than retrofitted. The agent roster, the memory model and the coordination primitives are **not yet specified** — see §6.

---

## 4. Document status after this reset

The repository currently contains more written material than the new scope has decisions. Every existing document is assigned a status here so none of it is mistaken for a live plan.

| Document | Status | Why |
|---|---|---|
| **This document** | **BINDING** | The current statement of scope |
| **[`docs/PLATFORM-SPEC.md`](./PLATFORM-SPEC.md)** | **BINDING** | The architecture built on this scope — spine schema, agent fleet, coordination, changefeed topology, twelve-day plan. Resolves open decisions 1, 3 and 5 below. |
| `docs/research/01–13`, `SYNTHESIS.md`, `STRATEGY-PIVOT.md` | **REFERENCE — findings stand** | These describe the outside world (Meta's targeting deprecation, FTC penalties, the hiQ outcome, creator rates, the measurement wall). Those facts did not change because our scope did. Their *recommendations* are scoped to the old product and are not binding. |
| `docs/PRODUCT.md` | **VOID as plan** | Scopes the product to a media generation suite with one role. Superseded entirely. |
| `docs/BUILD-SPEC.md` | **VOID as plan** | Rule 6 (`tenant` as partition key everywhere) is carried forward into §1 above on its own merits. Nothing else survives. |
| `docs/MEMORY-SPEC.md` | **VOID as plan** | Its memory model is scoped to generation attempts on a 28-clip corpus. §1's diagnosis of the write-back failure is carried forward into §2a rule 2. |
| `docs/MINDS-SPEC.md` | **VOID as plan** | Email-as-approval-surface is a good idea and likely returns as the fleet's control plane. It returns as a new decision, not as this document. |
| `content/*-SPEC.md` | **REFERENCE — RemixKit subproject** | Accurate descriptions of the asset generator's file formats. They govern that subproject and nothing above it. |
| `infra/README.md`, `infra/MEMORY-WORKLOAD.md` | **REFERENCE — costs only** | The measured rates and volumes remain useful. The architectures they describe are void. |

**Take nothing in the VOID rows as fact or plan.** Where one of them contains reasoning worth keeping, it is quoted into this document explicitly rather than inherited by reference.

---

## 5. What survives the reset

Three things, kept because they are hard-won and scope-independent:

1. **The provenance discipline** (§2a rule 1) — from Pillar 10, now system-wide.
2. **The house rule on unverified claims** — mark what is not verified rather than guessing it. `MEMORY-WORKLOAD.md` stating RU headroom as an inversion instead of inventing a number, and `screen_clips.py` abstaining rather than judging a locked-off shot, are the two reference examples.
3. **The research findings themselves** — particularly that causal attribution is impossible, that bought engagement is ~50x EV-negative, that scraping survives the CFAA question and still ends companies, and that disclosure non-compliance is an active regulatory risk. These constrain the new scope exactly as they constrained the old one.

---

## 6. Open decisions

Numbered so they are decisions rather than drift.

1. ~~**Which processes ship first, and how many.**~~ **RESOLVED 2026-08-06 by [`PLATFORM-SPEC.md §7`](./PLATFORM-SPEC.md).** Channels are data, not code, so all of them are supported in parallel by construction. **Two are built by Aug 18 — UGC creators and radio.** One cannot demonstrate the substrate is generic; five multiplies integration surface that is not memory-layer work.
2. **Repository topology.** Whether the platform is built in this repository with RemixKit relocated beneath it, or in a new repository with this one vendored as a subproject. **Still open.**
3. ~~**The agent roster and the coordination model.**~~ **RESOLVED 2026-08-06 by [`PLATFORM-SPEC.md §3` and `§5`](./PLATFORM-SPEC.md).** Eight agents; work claimed by lease with `FOR UPDATE SKIP LOCKED`; double-contact prevented structurally by a partial unique index rather than by convention.
4. **Counterparty acquisition method.** Pillar 10 §4's verdict is *"no scraper, ever,"* and its §5 finding is that manual sound-page browsing is both compliant and the highest-signal path. A human-in-the-loop scout that surfaces candidates for bulk human acceptance is the middle option. **Still open — and it is now the most urgent of these**, because it gates the Scout agent.
5. ~~**The persistence layer, and whether the Aug 18 submission remains a goal.**~~ **RESOLVED 2026-08-06 by [`PLATFORM-SPEC.md §1` and `§8`](./PLATFORM-SPEC.md).** CockroachDB, on a consolidation-and-correctness argument explicitly *not* a scale one. The Aug 18 submission stays a goal and has a day-by-day plan.
6. **Tenancy.** Whether this is built for one label (Respect the Funk) with multi-tenancy designed in but unused, or as a product from the first commit. **Still open** — `tenant_id` is carried on every table regardless, so this decides policy, not schema.
