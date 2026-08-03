---
title: "RemixKit — Product Scope"
subtitle: "Who this is for and what they do, in one page. Written because the repo's most detailed documents describe a larger product than the one being built, and every design decision was quietly being pulled toward it."
status: "DECISION — narrows BUILD-SPEC §1. Supersedes its role scope; everything else in BUILD-SPEC stands."
date: "2026-07-26"
amended: "2026-08-03 — 'What the product generates, precisely' rewritten. The product is a media generation suite for a label's catalogue, not a manufacturer of templatable UGC bait. Templatability is a research finding about how sounds spread, and it had been promoted into the definition of the product; it is one format among several. Supersedes the section it replaces and nothing else."
---

## Why this document exists

Before this file, the goal below appeared **nowhere** in the repository. That is measurable, not rhetorical:

- `tenant` appears **0 times** in `content/`, despite BUILD-SPEC §2b rule 6 calling it "a partition key everywhere… near-impossible to retrofit."
- `artist` appears **5 times**, every one of them cosmetic: a string on `losing-sleep.song.yaml`, a passthrough in `rtf.py`, a `print()` in `measure_structure.py`, a caption in `meme_engine.py`, one schema line in FORMAT-SPEC.
- `videos/` is flat and named after tests — `california-test`, `smart-test`, `nate-test`. Nothing is grouped by artist.
- Every spec's stated subject is the *video form* — promise-payoff, congruence, rhythm, found hooks. None describes a label, a catalog, or a customer.

So the repo contained a superb **craft engine** and a pitch for a **three-sided marketplace**, with nothing in between saying which one was being built. This file is that missing middle.

---

## The user

**One role: the label.** A record label or artist-services company with a roster and a catalog. Respect the Funk is tenant #1 and dogfoods everything; the product is generic and sold to any label.

There is no second user in this scope. No fan, no creator, no remixer, no payee.

---

## What they do

1. **Register an artist.** The artist is a first-class entity, not a string on a song.
2. **Build that artist's identity once.** A stored, reusable description of how the artist looks and reads on screen — structural facial features, reference frames across several lighting setups, wardrobe variants, anti-drift negatives, and signed likeness consent. This is the thing that gets reused across every song and every video for that artist, and it is why the second video for an artist is cheap.
3. **Attach songs.** Each song is measured once — BPM, drop, hook window — and that measurement is reused forever.
4. **Generate the release's media** from a song plus the artist's identity — in whichever format the release needs.
5. **Review and approve.** A human decides what is publishable before anything leaves the system.
6. **Publish or hand off** — download the assets, with provenance embedded in the file.

> **An assumption worth correcting if it is wrong.** "Remaps of their artists" is read here as *a stored, reusable representation of an artist that drives generation* — step 2 above. The alternative reading is "remixes of their artists' songs," which is step 4. The scope below covers both, but they imply different primary screens, so if the emphasis is wrong, say so and this document changes rather than the code.

---

## What the product generates, precisely

This is the load-bearing distinction, and an earlier version of this section got it wrong in a way that reached the code.

RemixKit is a **media generation suite for a label's catalogue**. Given a song and an artist's stored identity, it makes the assets a release actually needs: performance clips, press and cover stills, announcements delivered to camera, lyric cards, voice-over — and backdrops designed to be imitated, which a fan can copy with their own camera.

That last one used to be the *only* one. This section used to say the label's output is "**not** the finished marketing post" but content designed to be imitated, on the grounds that *"the song is the substrate, the template is the product."* That sentence is a finding from [`research/13-ugc-adoption`](./research/13-ugc-adoption/README.md) §3 about **how sounds spread on TikTok**, and it holds there — templatability is the mechanism of sound spread, qualitative academic consensus, sourced. Promoting a finding about one distribution channel into the definition of the whole product is the error.

It was not a harmless one. It welded `clean centre frame left empty for a subject` into every prompt the app could write and `speech` into every negative list, so the first label that wanted their artist *talking to camera* would have paid a video model to suppress the thing they asked for. Formats are rows now — see `app/remixkit/services/recipes.py` — and the templatable backdrop is one of five that ship.

**What this does not change.** The craft specs are still part of the product, and the research still constrains what may be claimed (see Non-goals). FORMAT-SPEC describes the promise-payoff shape — *a* format, not *the* product. The promise-payoff format in `content/` is one such shape and `nate-test` is one instance of it; the product manufactures instances of a repeatable shape per song at low marginal cost, for that shape and for the others.

---

## Explicitly deferred

BUILD-SPEC §1 defines three roles. **Roles 2 and 3 are deferred**, with one deliberate exception.

| BUILD-SPEC role | Status | What deferring it removes |
|---|---|---|
| 1. Label / Artist (tenant admin) | **In scope** | — |
| 2. Creator / fan (UGC maker) | **Deferred** | Attribution links + `/r/{code}` redirect + clicks, disclosure-acceptance gate, leaderboard, rewards + Stripe Connect, composite sessions + slots + invites, K-factor tracking. **8 of the ~14 tables in §3 and §7b.** |
| 3. Ops / judge (public verify) | **Deferred, except `/verify`** | See below. |

**Keep `/verify`.** It is a single read-only endpoint that runs `manifest.verify()` on an uploaded asset, and it buys a hackathon scoring criterion ("provenance-tracking" is a named example category under B2 + Data Orchestration) for a few hours of work. Deferring the *rest* of role 3 — the public release page, the ops dashboard — costs nothing. With the deadline on **2026-08-03**, this is the one piece of the deferred scope that still pays for itself.

**What deferring role 2 buys.** Nearly all of the architectural complexity. The marketplace is what forces a relational database: attribution joins, leaderboard views, and reward ledgers are the only things in the schema that need SQL. Remove them and the entire database tier becomes optional — see `infra/README.md`.

**This is a deferral, not a deletion.** Role 2 is the growth layer and the investor story (the deck's spine is "Three users. One flywheel."). The design for it is preserved in `infra/deferred-marketplace.pdf` and BUILD-SPEC §7/§7b. Nothing is being thrown away; it is being sequenced.

---

## Vocabulary

So that code, specs, and conversation use one set of words.

| Term | Means | Exists today? |
|---|---|---|
| **tenant** | The label. Partition key for everything. | ❌ nowhere in `content/` |
| **artist** | A roster member. Owns an identity, songs, and rights. | ❌ a string on a song |
| **identity** | The reusable "remap" — how this artist looks on screen. | ⚠️ `character.yaml` exists and is excellent, but models *cast members in a video*, not artists in a catalog |
| **song** | One measured master: BPM, drop, hook window. | ✅ `rtf.song/v1`, one instance |
| **format** | A kind of asset the label makes, as data: prompt, negatives, length policy, face policy, aspect ratio, whether the master goes under it. `Recipe` in the app. | ✅ five built-ins; `rtf.format/v1` promise-payoff is one shape |
| **edit** | One video: this song, this format, this artist. | ✅ `rtf.edit/v2` |
| **kit** | The pack of assets generated for one release, in one format. | ❌ BUILD-SPEC only |

---

## What we do not have yet

Named so they are decisions rather than surprises.

1. **No artist entity, and no per-artist partitioning.** A layout that fits how `rtf.find_root()` already works:

   ```
   lib/artists/<artist>/artist.yaml        # rights, likeness consent, brand
   lib/artists/<artist>/identity/          # the remap: character.yaml + frames
   lib/artists/<artist>/songs/<song>.song.yaml
   videos/<artist>/<song>/<edit>/
   ```

   `song.artist` becomes a reference; rights inherit artist → song → clip.

2. **No approval state.** Nothing carries `draft | approved | published`. `render_edit --check` is a *technical* gate — beats tile, cuts land on the grid — not an editorial one. A label cannot auto-publish AI content about its own artists.

3. **Likeness consent is currently inverted for this use case.** The `nate-test` build is the live example: source rights were unknown, so the payoff deliberately held the park and the pigeons invariant and kept the speaker's face out of all 22 frames. For a label generating content about *its own signed artists* you want the opposite — explicit, auditable likeness rights so the artist's face **can** be held invariant. `character.yaml`'s `consent.people_release` is the right hook; it needs to be artist-level.

4. **Catalog onboarding is the real bottleneck — not infrastructure.** There is one song. Every new song needs `measure_beat.py` run and a written `method` justifying the BPM (FORMAT-SPEC requires the provenance, correctly). At fifty songs that manual step, not compute, is what limits throughput. Worth measuring before optimising anything else.

---

## Non-goals

Carried from research so they do not creep back: no virality claim (breakout is a sub-1% lottery), no stream attribution (impossible), no "which archetype wins" recommender (folklore), no bought engagement or seeding cascades (EV-negative), and no film-clip footage (rights death — AI, stock, public-domain, or owned masters only).

---

## How this relates to the other documents

| Document | Answers |
|---|---|
| **PRODUCT.md** (this) | *Who is it for and what do they do?* |
| BUILD-SPEC.md | *What gets built, in what order, at what cost?* — §1 role scope narrowed by this file |
| MEMORY-SPEC.md | *How does "build the identity once" actually make the second video cheap?* — turns step 2 above into a mechanism, and gives gap #2 (approval state) and gap #1 (the artist entity) a home |
| MINDS-SPEC.md | *Where does the human in step 5 actually approve?* — moves the judge out of the terminal and into email, which is what closes MEMORY-SPEC's write-back loop |
| FORMAT-SPEC.md | *What shape does a video of this kind take?* — one format among several, not the product's definition |
| CLIP-SPEC.md | *What does one clip mean, and where may it be cut?* |
| infra/README.md | *What runs it?* |
