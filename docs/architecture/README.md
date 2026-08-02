---
title: "RemixKit — system architecture poster"
subtitle: "Eight sheets covering the application end to end: who touches it, what every touch leaves behind, how a run is priced and executed, where it deploys, and every refusal in the product."
status: "GENERATED — `python3 render.py` rebuilds it from this directory."
date: "2026-08-02"
---

## The file

**[`system-architecture.pdf`](./system-architecture.pdf)** — eight sheets at 18.75 × 12.5 in.

```bash
cd docs/architecture
python3 render.py          # writes system-architecture.html, then the PDF
```

No dependencies beyond the standard library. The PDF step drives a headless
Chromium-family browser (Brave, Chrome, Chromium or Edge — whichever is installed);
without one, the HTML is still written and stands on its own.

## What is on each sheet

| | | |
|---|---|---|
| cover | Contents and the wire key | the five colours every arrow uses |
| 01 | **The whole system on one sheet** | actors → front doors → thirteen services → eight ports → seven adapter axes → one bucket, plus the asynchronous lane |
| 02 | **The journey · onboarding, and the master** | nine touchpoints, sign-in code to hook window |
| 03 | **The journey · buying a run, and what leaves** | nine more, priced dry run to deletion |
| 04 | **Residual actions — the ledger** | the whole key space, every durable record, and the exact limit of every undo |
| 05 | **The expensive path** | one resolution function with two callers, the two-stage identity lock, the still index |
| 06 | **Deployment** | Lambda · SQS · Batch · SSM · B2, against the same code on a laptop |
| 07 | **The trust argument** | the provenance loop, and the fifteen refusals |

Sheets 02 and 03 are the answer to "every touch point and its residual action": five lanes
per station — what the operator touches, what the server does, **what is left behind**, what
is recorded about *how*, and what undoing it costs. Read down a column for one action; read
across a lane to compare what the system keeps.

## Why it is generated

Same reason [`infra/diagram.py`](../../infra/diagram.py) is: the labels carry the real
routes, the real object keys and the real settings, so changing the architecture means
changing this file rather than remembering to redraw a picture. A node cannot move without
its arrows moving with it — the connectors are drawn from the same coordinates the boxes
were placed at.

```
kit.py            canvas, palette, boxes, orthogonal connector routing
pages_map.py      sheets 1–3 — the map and the two journey pages
pages_detail.py   sheets 4–7 — residue, pipeline, deployment, trust
render.py         page chrome, CSS, the cover, and the PDF step
```

Boxes are absolutely positioned HTML rather than SVG text, because SVG does not wrap and
most of these labels are sentences. The connectors are one SVG layer underneath them.

## Its relationship to the other diagrams

[`infra/architecture.pdf`](../../infra/README.md) is the **AWS-shaped** view: five services,
one durable store, and the cost argument for having no database tier. It is a separate
document and is not modified by this one — infra/README derives its "under $1/month idle"
claim from that drawing, and MEMORY-SPEC §8 commits to it shipping unchanged.

This poster is the **application-shaped** view of the same system: it is about what a person
does, what the code refuses, and what ends up in the bucket afterwards.

## Sources

Everything on these sheets is drawn from the code and the specs in this repository —
`app/README.md`, `PRODUCT.md`, `PIPELINE-SPEC.md`, `BUILD-SPEC.md`, `MEMORY-SPEC.md`, and
`app/remixkit/` itself. Where a sheet states a vendor fact (a payload key, a price, a model
slug), that fact arrived as a rejection or an invoice and is registered in
`adapters/model_families.py` or `adapters/pricing.py` rather than inferred.
