---
title: "Artist console — structural wireframe"
subtitle: "Nine screens, no behaviour. Screens are data, the component vocabulary is data, and the field lists are read from the application's own domain model. Adding a screen is a JSON file; adding a field is an edit to models.py."
status: "STRUCTURE ONLY — no functionality, no visual design decisions, no copy that survives"
date: "2026-07-27"
---

## Build it

```bash
python3 web/wireframe/build.py
open web/wireframe/index.html
```

Standard library only. No `pip install`, no node, no build tool — deliberately, so the
wireframe builds on any machine that can run Python even when the application's
dependencies are not installed.

## Why it is generated rather than drawn

A wireframe drawn by hand goes stale the moment the model moves, and the staleness is
invisible: nothing fails, the picture just quietly stops describing the system. So
nothing here is hand-drawn.

Three inputs, one output:

| Input | What it is | Where |
|---|---|---|
| **Vocabulary** | The 19 block types a screen may use, and the props each accepts | `spec/primitives.json` |
| **Screens** | Nine layout trees, each naming blocks from the vocabulary | `spec/screens/*.json` |
| **Model** | Entities and fields, parsed out of the real domain model | `app/remixkit/domain/models.py` |

`build.py` reads the model with `ast` rather than importing it. That keeps the build
dependency-free and means no import-time side effect in the application can break it,
while the field lists still come from the one place they are actually defined.

## What is enforced

The build fails rather than rendering something misleading. Every one of these is a
hard error with the offending path named:

```
roster.body > carousel[0]: unknown primitive 'carousel'. Declared: actions, banner, …
roster.body > region[2] > table[1]: Artist has no field(s) follower_count. Available: …
roster.body > region[2] > table[1]: table does not take colour. It takes: entity, …
roster.json: nav group 'marketing' is not declared in meta.json (roster, catalogue, …)
```

That is the whole reason the vocabulary is data instead of markup: a typo becomes a
build failure instead of a block that silently renders as nothing.

## What the screens are

Navigation is derived from each screen's own `nav` block against the groups in
`meta.json`. There is no menu defined anywhere.

| Group | Screen | Route |
|---|---|---|
| Roster | Roster · Artist · Identity builder | `/`, `/artists/{id}`, `/artists/{id}/identity` |
| Catalogue | Song · Kit · Catalogue · Generate a kit | `/songs/{id}`, `/kits/{id}`, `/catalogue`, `…/generate` |
| Provenance | Verifier | `/verify` |
| System | Deployment | `/settings` |

Two reference views are generated from the same data: **Vocabulary** (every primitive,
its props, and how many times it is used) and **Model** (every entity and field, with
required and inherited marked).

## Reading it

Two voices carry the drawing. **Mono** is the machine layer — primitive names, field
names, bindings, routes; everything that came from a spec file. **Sans** is the content
layer — the bars and hatching standing in for what a person will eventually read.
Nothing is lorem, because placeholder prose invites copy review at a stage where there
is no copy.

**Annotations** (toggle in the rail) show each block's primitive type and entity
binding, plus the `note` fields from the specs. Turn them off to read the page as pure
structure; leave them on to read it as a spec sheet. JSON has no comments, so `note` is
both the comment and the annotation — one string, rendered where it applies.

## What it deliberately does not do

No behaviour. Buttons are labels, inputs are rectangles, and no control accepts input —
screen switching, the annotation toggle and the theme toggle are viewer affordances,
not application features.

No visual design. The palette is one accent plus semantic state on a cool neutral, and
it is chosen to be legible rather than to be the product's. Type is system stacks, so
there is no webfont to load and nothing to mistake for a brand decision.

No data. Counts like `rows: 6` and `count: 5` are placeholder quantities that set the
shape of a block. They are not claims about how much of anything exists.

## Three things the specs record on purpose

Each is a gap in the running application that the structure has to account for, written
into the screen that would close it rather than left as a note somewhere else.

1. **Reference frames have no surface.** The domain model carries them and nothing can
   create one, so the identity is text-only in practice. `identity.json` specifies the
   upload and gallery that closes that.
2. **There is no catalogue view.** "Which songs have no approved kit" is the label's
   actual daily question and it spans collections. `catalogue.json` gives it a screen.
3. **Cost is visible only after the fact.** `generate.json` puts the estimate, the
   remaining budget, and the count of shots reusable from cache *before* the control
   that queues the job.

## Files

| Path | What |
|---|---|
| `build.py` | Introspect · validate · emit. Stdlib only. |
| `spec/meta.json` | Product name, nav groups, model source |
| `spec/primitives.json` | The block vocabulary and the props each accepts |
| `spec/screens/*.json` | One file per screen |
| `src/wireframe.css` | Tokens and block styles, both themes |
| `src/renderer.js` | One recursive walk. No per-screen code. |
| `index.html` | **Generated** — self-contained, opens from disk |
| `artifact.html` | **Generated** — same page for hosts that supply the document skeleton |
| `wireframe.json` | **Generated** — the resolved model, for anything else that wants it |

Generated files are committed so the wireframe can be opened without running anything.
