#!/usr/bin/env python3
"""Render the architecture AS BUILT, with the multi-region topology deployed.

    python3 as_built_architecture.py        # -> as-built-architecture.pdf

`platform_architecture.py` renders `docs/PLATFORM-SPEC.md` — the design, as of
2026-08-06, including tables that were never created and agents that do not exist. It
says so on its own first page. This file is the other document: what the code actually
does, with the numbers read off the running cluster on AS_OF.

The one thing it draws that is not deployed today is the three-region topology, because
that is what was asked for — "assuming I deploy multi-region". Everything region-shaped
is therefore drawn as **provisioned-from-committed-IaC**, and page 7 is the ledger that
says which parts of this picture are running right now and which are one command away.
That page is not an appendix. A diagram of a system that does not exist yet is a
prospectus, and the honest way to publish one is to print the gap beside it.

Colour carries region, everywhere, on every page:

    indigo   aws-us-east-1      primary; the console, the fleet, the masters bucket
    teal     aws-eu-west-1      EU personal data, by law rather than by preference
    amber    ap-southeast-1     APAC personal data, and the third region that makes
                                SURVIVE REGION FAILURE possible at all

Two regions give you residency. Three give you residency *and* survival, which is why
the runbook provisions three for a demo that could have been made with two.

Requires weasyprint. Hand-authored SVG, no graphviz, so this runs wherever weasyprint
does — the same choice `platform_architecture.py` made and for the same reason.
"""

from pathlib import Path

from weasyprint import HTML

OUT = Path(__file__).parent / "as-built-architecture.pdf"

#: Read off the running cluster. Status with no date on it is an assertion.
AS_OF = "2026-08-13"
CLUSTER = "respect-the-funk · ae38b92e-c1ad-4a06-a247-489cd5ce9964"
VERSION = "CockroachDB CCL v26.2.5"

# ────────────────────────────────────────────────────────── measured, not asserted

MEASURED = {
    "counterparties": "14,170",
    "embedded": "14,136",
    "chunks": "22,057",
    "chunk_parties": "14,018",
    "facts": "45,125",
    "documents": "36,229",
    "leads": "36,495",
    "runs": "54,183",
    "spend": "$0.12",
}

#: (index, table, rows, reads, verdict). Reads are lifetime, from
#: crdb_internal.index_usage_statistics on AS_OF.
INDEXES = [
    ("party_shortlist", "party", "14,136 vectors", "23,891", "live",
     "R1. Four prefix columns — the ANN search runs in the filtered subspace."),
    ("chunk_semantic", "party_chunk", "22,057 chunks", "45,238", "live",
     "R2 over evidence. Second most-read index in the whole cluster."),
    ("lesson_semantic", "lesson", "1 row", "691", "thin",
     "The memory loop. Works, and holds one lesson — one thread has closed."),
    ("fact_semantic", "party_fact", "—", "—", "dropped",
     "Indexed a column nothing wrote, for a query nobody ran. Removed."),
]

#: What each region holds once the topology is deployed.
REGIONS = [
    ("aws-us-east-1", "us", "PRIMARY",
     ["console · Lambda + Mangum", "the fleet, all ten stages",
      "masters bucket · S3", "contact_route rows for US data subjects"]),
    ("aws-eu-west-1", "eu", "GDPR",
     ["contact_route rows for EU data subjects",
      "replicas of every global table", "voting replica for region survival"]),
    ("ap-southeast-1", "ap", "APAC",
     ["contact_route rows for APAC data subjects",
      "replicas of every global table", "the third region survival requires"]),
]

#: The ten stages actually in agents.REGISTRY.
STAGES = [
    "embed_document", "map_source", "find_counterparties", "profile_party",
    "embed_party", "distil_lesson", "analyse_recording", "index_stations",
    "index_streams", "enrich_genre", "index_podcasts",
]

#: (what, state, evidence). Page 7. The whole point of the page is that this is
#: measured rather than intended, so every row carries how it was checked.
LEDGER = [
    ("Filtered vector search (R1)", "running",
     "EXPLAIN shows a vector search node with four prefix spans. 23,891 reads."),
    ("Evidence retrieval (R2)", "running",
     "22,057 chunks over 14,018 parties. 45,238 reads."),
    ("Time travel on the production path", "running",
     "shortlist_as_of takes an absolute HLC; the scrubber returns a different first "
     "result at -1h than at now."),
    ("Decision provenance", "running · no data",
     "Schema, writer, replay and panel all land. 0 threads carry one — 1 thread exists "
     "and it predates the change."),
    ("Serializable claiming with a lease token", "running",
     "lease_race_demo Act 2: two workers, one name, only the token tells them apart."),
    ("Tenant isolation proved statically", "running",
     "test_tenant_scoping resolves every statement reaching execute() and fails the "
     "build on any that is unscoped."),
    ("Suite isolated from production", "running",
     "A marker table the cluster must carry. Production has none, so the suite refuses."),
    ("Managed MCP server in the console", "running",
     "Six pinned SQL literals; the model classifies onto them and never writes SQL."),
    ("Changefeed wakes the agents", "built, not created",
     "Statement, consumer, Lambda sink and --verify all exist. SHOW CHANGEFEED JOBS "
     "returns 0 — it draws RUs until cancelled, so creating it stays a human step."),
    ("REGIONAL BY ROW residency", "built, not provisioned",
     "Terraform, migration 024 and the runbook exist. SHOW REGIONS returns one row. "
     "About $0.65 for a three-hour window on a throwaway cluster."),
    ("Bedrock embeddings", "blocked at the account",
     "On-demand quota 0 and non-adjustable. Batch is not authorised pending a support "
     "case. Embeddings are OpenAI; AWS is Lambda and S3, which are real."),
    ("Podcast corpus", "built, no credential",
     "Adapter, migration, ingest stage and worker are tested. Needs a free API key."),
]

C = {
    "us": "#4F46E5", "usbg": "#EEF0FE",
    "eu": "#0F766E", "eubg": "#E3F1EF",
    "ap": "#B45309", "apbg": "#FBEEE0",
    "ink": "#14161D", "mid": "#464B5A", "soft": "#8A90A0",
    "rule": "#D8DAE2", "paper": "#FFFFFF", "sunk": "#F5F6F9",
    "good": "#0F766E", "warn": "#B45309", "gone": "#9AA0AE", "stop": "#B4232B",
}


#: Content width of an A4 landscape page inside the margins declared in CSS.
#: weasyprint collapses an inline <svg> that carries a viewBox and `width="100%"` but no
#: height — it does not derive the height from the aspect ratio — so every diagram here
#: states both in millimetres, computed from its own viewBox so the two cannot drift.
CONTENT_MM = 273.0

#: Vertical room a diagram may take once the kicker, heading and lede above it have had
#: theirs. A4 landscape is 210mm tall and the CSS margins leave 185mm; the block of type
#: that opens every page costs about 38mm, and page 1 spends another 18mm on the stat
#: row. Scaling to width alone produced a 169mm-tall picture that did fit the page it was
#: measured against and did not fit the one it shared with a heading — so every diagram
#: is fitted to BOTH bounds and takes whichever is tighter.
ROOM_MM = 140.0
ROOM_MM_P1 = 122.0


def svg_open(vw: float, vh: float, room: float = ROOM_MM) -> str:
    scale = min(CONTENT_MM / vw, room / vh)
    return (f'<svg viewBox="0 0 {vw:g} {vh:g}" width="{vw * scale:.2f}mm" '
            f'height="{vh * scale:.2f}mm" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'preserveAspectRatio="xMidYMid meet">')


def arrow_defs() -> str:
    return "".join(
        f'<marker id="a{k}" markerWidth="10" markerHeight="10" refX="9" refY="3.2" '
        f'orient="auto" markerUnits="strokeWidth">'
        f'<path d="M0,0 L0,6.4 L9,3.2 z" fill="{v}"/></marker>'
        for k, v in (("us", C["us"]), ("eu", C["eu"]), ("ap", C["ap"]),
                     ("m", C["mid"]), ("g", C["gone"])))


# ───────────────────────────────────────────────────────────────────── page 1

def svg_topology() -> str:
    """The whole system on one picture: three regions, one logical database."""
    out = [svg_open(760, 470, ROOM_MM_P1),
           f'<defs>{arrow_defs()}</defs>']

    # one logical database, spanning
    out.append(f'<rect x="18" y="150" width="724" height="150" rx="10" fill="{C["sunk"]}" '
               f'stroke="{C["rule"]}" stroke-dasharray="5 4"/>')
    out.append(f'<text x="30" y="172" font-size="11" font-weight="700" fill="{C["mid"]}" '
               f'letter-spacing="1.4">ONE LOGICAL DATABASE — defaultdb</text>')
    out.append(f'<text x="30" y="188" font-size="9.5" fill="{C["soft"]}">'
               f'SURVIVE REGION FAILURE · every table replicated · contact_route domiciled per row</text>')

    for i, (name, key, badge, holds) in enumerate(REGIONS):
        x = 34 + i * 240
        col, bg = C[key], C[key + "bg"]
        out.append(f'<rect x="{x}" y="200" width="212" height="86" rx="8" fill="{bg}" stroke="{col}"/>')
        out.append(f'<text x="{x+12}" y="222" font-size="12.5" font-weight="700" fill="{col}">{name}</text>')
        out.append(f'<rect x="{x+150}" y="209" width="52" height="16" rx="8" fill="{col}"/>')
        out.append(f'<text x="{x+176}" y="220.5" font-size="8.5" font-weight="700" fill="#fff" '
                   f'text-anchor="middle">{badge}</text>')
        for j, line in enumerate(holds[:3]):
            out.append(f'<text x="{x+12}" y="{240 + j*15}" font-size="9" fill="{C["mid"]}">· {line[:44]}</text>')

    # the app tier, above, only in us-east-1
    out.append(f'<rect x="34" y="40" width="212" height="88" rx="8" fill="{C["paper"]}" stroke="{C["us"]}"/>')
    out.append(f'<text x="140" y="60" font-size="12" font-weight="700" fill="{C["us"]}" '
               f'text-anchor="middle">console · Lambda</text>')
    for j, t in enumerate(("FastAPI behind Mangum", "the operator's screens", "/ask · managed MCP")):
        out.append(f'<text x="140" y="{78 + j*15}" font-size="9" fill="{C["mid"]}" text-anchor="middle">{t}</text>')

    out.append(f'<rect x="274" y="40" width="212" height="88" rx="8" fill="{C["paper"]}" stroke="{C["us"]}"/>')
    out.append(f'<text x="380" y="60" font-size="12" font-weight="700" fill="{C["us"]}" '
               f'text-anchor="middle">the fleet · 11 stages</text>')
    for j, t in enumerate(("claim · lease · token", "no orchestrator", "kill any of it, work finishes")):
        out.append(f'<text x="380" y="{78 + j*15}" font-size="9" fill="{C["mid"]}" text-anchor="middle">{t}</text>')

    out.append(f'<rect x="514" y="40" width="212" height="88" rx="8" fill="{C["paper"]}" stroke="{C["us"]}"/>')
    out.append(f'<text x="620" y="60" font-size="12" font-weight="700" fill="{C["us"]}" '
               f'text-anchor="middle">edges</text>')
    for j, t in enumerate(("S3 masters · SES sends", "OpenAI embeddings", "changefeed → Lambda")):
        out.append(f'<text x="620" y="{78 + j*15}" font-size="9" fill="{C["mid"]}" text-anchor="middle">{t}</text>')

    for x in (140, 380, 620):
        out.append(f'<path d="M{x} 128 L{x} 150" stroke="{C["us"]}" stroke-width="1.8" marker-end="url(#aus)"/>')

    # what the database is, underneath
    out.append(f'<text x="380" y="330" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'text-anchor="middle" letter-spacing="1.2">ONE SUBSTRATE, FIVE JOBS</text>')
    for i, (t, s) in enumerate((("index", "R1 + R2 vector search"), ("scheduler", "lease + SKIP LOCKED"),
                                ("outbox", "at-most-once sends"), ("event bus", "changefeed"),
                                ("audit log", "AS OF SYSTEM TIME"))):
        x = 30 + i * 143
        out.append(f'<rect x="{x}" y="344" width="130" height="46" rx="6" fill="{C["paper"]}" stroke="{C["rule"]}"/>')
        out.append(f'<text x="{x+65}" y="364" font-size="11" font-weight="700" fill="{C["ink"]}" '
                   f'text-anchor="middle">{t}</text>')
        out.append(f'<text x="{x+65}" y="379" font-size="8.5" fill="{C["soft"]}" text-anchor="middle">{s}</text>')

    out.append(f'<text x="380" y="424" font-size="10.5" fill="{C["mid"]}" text-anchor="middle">'
               f'Not Postgres plus a vector store plus Redis plus a queue plus an audit pipeline.</text>')
    out.append(f'<text x="380" y="442" font-size="10.5" font-weight="600" fill="{C["ink"]}" '
               f'text-anchor="middle">One system. Five jobs. Zero nodes at rest.</text>')
    out.append('</svg>')
    return "".join(out)


# ───────────────────────────────────────────────────────────────────── page 2

def svg_residency() -> str:
    """One logical table, rows on three continents, enforced by the database."""
    out = [svg_open(760, 410),
           f'<defs>{arrow_defs()}</defs>']
    out.append(f'<text x="18" y="24" font-size="12" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">contact_route — ONE TABLE</text>')
    out.append(f'<text x="18" y="42" font-size="9.5" fill="{C["soft"]}">'
               f'REGIONAL BY ROW AS residency_region · a STORED computed column derived from contact_country</text>')

    rows = [("hello@wfmu.org", "US", "us", "aws-us-east-1"),
            ("studio@nts.live", "GB", "eu", "aws-eu-west-1"),
            ("music@rrr.org.au", "AU", "ap", "ap-southeast-1")]
    for i, (val, cc, key, region) in enumerate(rows):
        y = 62 + i * 46
        col, bg = C[key], C[key + "bg"]
        out.append(f'<rect x="18" y="{y}" width="330" height="38" rx="6" fill="{bg}" stroke="{col}"/>')
        out.append(f'<text x="30" y="{y+16}" font-size="10.5" font-weight="600" fill="{C["ink"]}">{val}</text>')
        out.append(f'<text x="30" y="{y+30}" font-size="9" fill="{C["mid"]}">contact_country = {cc}</text>')
        out.append(f'<path d="M348 {y+19} L410 {y+19}" stroke="{col}" stroke-width="2" '
                   f'marker-end="url(#a{key})"/>')
        out.append(f'<rect x="412" y="{y}" width="200" height="38" rx="6" fill="{C["paper"]}" stroke="{col}"/>')
        out.append(f'<text x="512" y="{y+23}" font-size="10.5" font-weight="700" fill="{col}" '
                   f'text-anchor="middle">{region}</text>')

    out.append(f'<text x="18" y="222" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">WHAT THE DATABASE ENFORCES, NOT THE APPLICATION</text>')
    notes = [
        "Same table. Same query. The row for an EU data subject has its replicas physically in Ireland.",
        "No ELSE branch in the region mapping — an unmapped country fails NOT NULL rather than being",
        "domiciled somewhere plausible. CN and RU are unmapped deliberately: Singapore does not satisfy",
        "PIPL and nothing here satisfies 152-FZ.",
        "In Postgres this is three databases, an application-level router, and a synchronisation problem",
        "you own forever. Here it is a table property.",
    ]
    for i, n in enumerate(notes):
        out.append(f'<text x="18" y="{242 + i*16}" font-size="9.5" fill="{C["mid"]}">{n}</text>')

    out.append(f'<rect x="18" y="344" width="724" height="52" rx="6" fill="{C["sunk"]}" stroke="{C["rule"]}"/>')
    out.append(f'<text x="30" y="365" font-size="9.5" font-weight="700" fill="{C["stop"]}">'
               f'A REGION CANNOT BE REMOVED ONCE ADDED — on Basic or Standard.</text>')
    out.append(f'<text x="30" y="376" font-size="9" fill="{C["mid"]}">'
               f'So the runbook provisions a throwaway cluster — converting the live one would be permanent,</text>')
    out.append(f'<text x="30" y="389" font-size="9" fill="{C["mid"]}">'
               f'and its teardown would mean a backup, a new cluster, a restore, and repointing every URL.</text>')
    out.append('</svg>')
    return "".join(out)


# ───────────────────────────────────────────────────────────────────── page 3

def svg_retrieval() -> str:
    """R1 and R2, and the fate of all four vector indexes."""
    out = [svg_open(760, 430),
           f'<defs>{arrow_defs()}</defs>']

    out.append(f'<rect x="18" y="26" width="352" height="132" rx="8" fill="{C["usbg"]}" stroke="{C["us"]}"/>')
    out.append(f'<text x="34" y="48" font-size="12.5" font-weight="700" fill="{C["us"]}">R1 — who should we pitch?</text>')
    for i, t in enumerate(("party@party_shortlist", "tenant_id · embedding_model · party_class · contact_state",
                           "→ vector search with prefix spans", "14,136 vectors · 23,891 reads")):
        w = "700" if i == 0 else "400"
        s = "10" if i == 0 else "9"
        out.append(f'<text x="34" y="{70 + i*20}" font-size="{s}" font-weight="{w}" '
                   f'fill="{C["mid"]}" font-family="monospace">{t}</text>')

    out.append(f'<rect x="390" y="26" width="352" height="132" rx="8" fill="{C["eubg"]}" stroke="{C["eu"]}"/>')
    out.append(f'<text x="406" y="48" font-size="12.5" font-weight="700" fill="{C["eu"]}">R2 — what do we already know?</text>')
    for i, t in enumerate(("party_chunk@chunk_semantic", "tenant_id · model",
                           "→ vector search with prefix spans", "22,057 chunks · 45,238 reads")):
        w = "700" if i == 0 else "400"
        s = "10" if i == 0 else "9"
        out.append(f'<text x="406" y="{70 + i*20}" font-size="{s}" font-weight="{w}" '
                   f'fill="{C["mid"]}" font-family="monospace">{t}</text>')

    out.append(f'<text x="18" y="186" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">THE FILTERS ARE INSIDE THE INDEX</text>')
    out.append(f'<rect x="18" y="196" width="724" height="62" rx="6" fill="{C["ink"]}"/>')
    for i, t in enumerate(("• vector search",
                           "    table: party@party_shortlist",
                           "    prefix spans: [/'1f9e6dd3…'/'openai:text-embedding-3-small'/'counterparty'/'contactable' - …]")):
        out.append(f'<text x="32" y="{216 + i*17}" font-size="9.5" fill="#B9F5D8" '
                   f'font-family="monospace">{t}</text>')

    out.append(f'<text x="18" y="284" font-size="10" fill="{C["mid"]}">'
               f'The search runs in the filtered subspace — not a scan with a WHERE clause bolted on after. '
               f'And that prefix starts with tenant_id,</text>')
    out.append(f'<text x="18" y="300" font-size="10" fill="{C["mid"]}">'
               f'the same column a static test proves every statement in the codebase carries. '
               f'One column: security boundary and partition key at once.</text>')

    out.append(f'<text x="18" y="332" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">ALL FOUR VECTOR INDEXES, HONESTLY</text>')
    for i, (name, table, rows, reads, verdict, why) in enumerate(INDEXES):
        y = 344 + i * 22
        col = {"live": C["good"], "thin": C["warn"], "dropped": C["gone"]}[verdict]
        out.append(f'<rect x="18" y="{y}" width="6" height="16" rx="2" fill="{col}"/>')
        out.append(f'<text x="32" y="{y+12}" font-size="9.5" font-weight="700" fill="{C["ink"]}" '
                   f'font-family="monospace">{name}</text>')
        out.append(f'<text x="168" y="{y+12}" font-size="9" fill="{C["mid"]}">{rows}</text>')
        out.append(f'<text x="272" y="{y+12}" font-size="9" fill="{col}" font-weight="700">{verdict}</text>')
        out.append(f'<text x="348" y="{y+12}" font-size="9" fill="{C["soft"]}">{why}</text>')
    out.append('</svg>')
    return "".join(out)


# ───────────────────────────────────────────────────────────────────── page 4

def svg_fleet() -> str:
    """Coordination: a row changes, an agent wakes, and only one of them wins."""
    out = [svg_open(760, 440),
           f'<defs>{arrow_defs()}</defs>']

    out.append(f'<rect x="18" y="26" width="180" height="52" rx="6" fill="{C["eubg"]}" stroke="{C["eu"]}"/>')
    out.append(f'<text x="108" y="47" font-size="11" font-weight="700" fill="{C["eu"]}" text-anchor="middle">a row changes</text>')
    out.append(f'<text x="108" y="64" font-size="9" fill="{C["mid"]}" text-anchor="middle">thread · outbox · message</text>')

    out.append(f'<path d="M198 52 L262 52" stroke="{C["eu"]}" stroke-width="2" marker-end="url(#aeu)"/>')
    out.append(f'<rect x="264" y="26" width="180" height="52" rx="6" fill="{C["eubg"]}" stroke="{C["eu"]}"/>')
    out.append(f'<text x="354" y="47" font-size="11" font-weight="700" fill="{C["eu"]}" text-anchor="middle">CHANGEFEED</text>')
    out.append(f'<text x="354" y="64" font-size="9" fill="{C["mid"]}" text-anchor="middle">initial_scan = &#39;no&#39;</text>')

    out.append(f'<path d="M444 52 L508 52" stroke="{C["eu"]}" stroke-width="2" marker-end="url(#aeu)"/>')
    out.append(f'<rect x="510" y="26" width="180" height="52" rx="6" fill="{C["usbg"]}" stroke="{C["us"]}"/>')
    out.append(f'<text x="600" y="47" font-size="11" font-weight="700" fill="{C["us"]}" text-anchor="middle">Lambda webhook</text>')
    out.append(f'<text x="600" y="64" font-size="9" fill="{C["mid"]}" text-anchor="middle">wakes the next stage</text>')

    out.append(f'<text x="18" y="104" font-size="9.5" fill="{C["mid"]}">'
               f'No broker. No queue. No scheduler. Waking is not claiming — two workers woken by the same event still '
               f'produce exactly one winner.</text>')

    # the fence
    out.append(f'<text x="18" y="140" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">AND ONLY ONE OF THEM WINS</text>')
    for i, (who, tok, res) in enumerate((("worker A", "token 2bb498ed…", "REFUSED — lease lost"),
                                         ("worker B", "token 6f72beda…", "OK"))):
        y = 152 + i * 40
        col = C["stop"] if i == 0 else C["good"]
        out.append(f'<rect x="18" y="{y}" width="724" height="32" rx="6" fill="{C["paper"]}" stroke="{C["rule"]}"/>')
        out.append(f'<text x="34" y="{y+20}" font-size="10" font-weight="700" fill="{C["ink"]}">{who}</text>')
        out.append(f'<text x="120" y="{y+20}" font-size="9.5" fill="{C["mid"]}" font-family="monospace">'
                   f'owner=ingest-cli · {tok}</text>')
        out.append(f'<text x="726" y="{y+20}" font-size="9.5" font-weight="700" fill="{col}" '
                   f'text-anchor="end">{res}</text>')
    out.append(f'<text x="18" y="244" font-size="9.5" fill="{C["mid"]}">'
               f'Same name. Same lead. The name cannot tell them apart; the token can — and only the '
               f'database mints it.</text>')
    out.append(f'<text x="18" y="260" font-size="9.5" fill="{C["mid"]}">'
               f'Serializable by default, FOR UPDATE SKIP LOCKED, and a fence that fails closed.</text>')

    out.append(f'<text x="18" y="292" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">THE ELEVEN STAGES IN agents.REGISTRY</text>')
    for i, s in enumerate(STAGES):
        x = 18 + (i % 4) * 182
        y = 300 + (i // 4) * 34
        out.append(f'<rect x="{x}" y="{y}" width="170" height="26" rx="5" fill="{C["usbg"]}" stroke="{C["us"]}"/>')
        out.append(f'<text x="{x+85}" y="{y+17}" font-size="9.5" font-weight="600" fill="{C["us"]}" '
                   f'text-anchor="middle" font-family="monospace">{s}</text>')
    out.append(f'<text x="18" y="412" font-size="9" fill="{C["soft"]}">'
               f'Every one claims its own work. Agents never call each other — {MEASURED["runs"]} runs recorded, '
               f'{MEASURED["leads"]} leads in the frontier.</text>')
    out.append('</svg>')
    return "".join(out)


# ───────────────────────────────────────────────────────────────────── page 5

def svg_timetravel() -> str:
    """Why did the agent decide that, then?"""
    out = [svg_open(760, 400),
           f'<defs>{arrow_defs()}</defs>']

    out.append(f'<rect x="18" y="26" width="724" height="46" rx="6" fill="{C["ink"]}"/>')
    out.append(f'<text x="34" y="46" font-size="10" fill="#B9F5D8" font-family="monospace">'
               f'thread.decided_at_hlc = 1786644836824776765.0000000000</text>')
    out.append(f'<text x="34" y="62" font-size="9" fill="#8FBFA8" font-family="monospace">'
               f'a hybrid logical clock reading — not a timestamp, and the difference is the whole design</text>')

    out.append(f'<text x="18" y="98" font-size="9.5" fill="{C["mid"]}">'
               f'AS OF SYSTEM TIME with a wall clock resolves to the most recent version at or before that instant, and '
               f'several writes share a millisecond.</text>')
    out.append(f'<text x="18" y="114" font-size="9.5" fill="{C["mid"]}">'
               f'Landing on the wrong side of one reconstructs a ranking nobody ever saw — a rounding error in a chart, '
               f'a fabrication in the record that</text>')
    out.append(f'<text x="18" y="130" font-size="9.5" fill="{C["mid"]}">'
               f'justifies emailing a stranger, and indistinguishable from the truth either way.</text>')

    out.append(f'<text x="18" y="166" font-size="11" font-weight="700" fill="{C["ink"]}" '
               f'letter-spacing="1.2">THREE OUTCOMES, KEPT APART ON PURPOSE</text>')
    outcomes = [
        ("a ranking", C["good"], "the shortlist as it stood, plus whether the replay still agrees with what was recorded"),
        ("NotJustified", C["warn"], "nobody ranked this — a human opened the thread, and an absent reason must look absent"),
        ("HistoryExpired", C["stop"], "a reason was recorded and the GC window has passed. Never falls back to today's ranking"),
    ]
    for i, (name, col, why) in enumerate(outcomes):
        y = 180 + i * 46
        out.append(f'<rect x="18" y="{y}" width="724" height="38" rx="6" fill="{C["paper"]}" stroke="{col}"/>')
        out.append(f'<rect x="18" y="{y}" width="5" height="38" rx="2" fill="{col}"/>')
        out.append(f'<text x="36" y="{y+17}" font-size="10.5" font-weight="700" fill="{col}" '
                   f'font-family="monospace">{name}</text>')
        out.append(f'<text x="36" y="{y+31}" font-size="9" fill="{C["mid"]}">{why}</text>')

    out.append(f'<rect x="18" y="326" width="724" height="56" rx="6" fill="{C["sunk"]}" stroke="{C["rule"]}"/>')
    out.append(f'<text x="34" y="346" font-size="10" font-weight="700" fill="{C["ink"]}">'
               f'Every agentic system can tell you what it knows. This one tells you what it believed, when it acted.</text>')
    out.append(f'<text x="34" y="363" font-size="9" fill="{C["mid"]}">'
               f'Four extra words of SQL. No audit table, no event log, no versioned copy of anything — and no way to '
               f'have chosen in advance what to record,</text>')
    out.append(f'<text x="34" y="376" font-size="9" fill="{C["mid"]}">'
               f'because AS OF SYSTEM TIME replays state nobody chose to record, including the vector ranking itself.</text>')
    out.append('</svg>')
    return "".join(out)


# ───────────────────────────────────────────────────────────────────── page 6

def svg_ledger() -> str:
    """Running, built, or blocked — and how each was checked."""
    out = [svg_open(760, 40 + len(LEDGER) * 34, 136.0)]
    palette = {"running": C["good"], "running · no data": C["warn"],
               "built, not created": C["warn"], "built, not provisioned": C["warn"],
               "blocked at the account": C["stop"], "built, no credential": C["warn"]}
    for i, (what, state, evidence) in enumerate(LEDGER):
        y = 14 + i * 34
        col = palette.get(state, C["mid"])
        out.append(f'<rect x="18" y="{y}" width="724" height="28" rx="5" fill="{C["paper"]}" stroke="{C["rule"]}"/>')
        out.append(f'<rect x="18" y="{y}" width="4" height="28" rx="2" fill="{col}"/>')
        out.append(f'<text x="34" y="{y+12}" font-size="10" font-weight="700" fill="{C["ink"]}">{what}</text>')
        out.append(f'<text x="34" y="{y+24}" font-size="8.2" fill="{C["soft"]}">{evidence}</text>')
        out.append(f'<text x="726" y="{y+12}" font-size="8.5" font-weight="700" fill="{col}" '
                   f'text-anchor="end">{state}</text>')
    out.append('</svg>')
    return "".join(out)


CSS = f"""
@page {{ size: A4 landscape; margin: 13mm 12mm 12mm 12mm;
  @bottom-left {{ content: "respect-the-funk · as built {AS_OF}"; font-size: 7.5pt; color: {C['soft']}; }}
  @bottom-right {{ content: counter(page) " / " counter(pages); font-size: 7.5pt; color: {C['soft']}; }} }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif; color: {C['ink']};
        margin: 0; font-size: 10pt; line-height: 1.45; }}
h1 {{ font-family: Georgia, "Times New Roman", serif; font-size: 21pt; margin: 0 0 2mm 0;
      letter-spacing: -0.3pt; }}
h2 {{ font-family: Georgia, serif; font-size: 15pt; margin: 0 0 1mm 0; }}
.kicker {{ font-size: 7.5pt; letter-spacing: 1.8pt; text-transform: uppercase;
           color: {C['soft']}; font-weight: 700; margin-bottom: 1.5mm; }}
.lede {{ color: {C['mid']}; font-size: 9.5pt; max-width: 185mm; margin: 0 0 4mm 0; }}
.page {{ break-after: page; }}
.page:last-child {{ break-after: auto; }}
svg {{ display: block; margin: 0 auto; }}
.stats {{ display: flex; gap: 4mm; margin: 3mm 0 0 0; }}
.stat {{ flex: 1; border: 0.4pt solid {C['rule']}; border-radius: 3pt; padding: 2mm 2.5mm; }}
.stat b {{ display: block; font-size: 14pt; font-family: Georgia, serif; }}
.stat span {{ font-size: 7.5pt; color: {C['soft']}; letter-spacing: 0.6pt; text-transform: uppercase; }}
.note {{ font-size: 8.5pt; color: {C['mid']}; border-top: 0.4pt solid {C['rule']};
         padding-top: 2mm; margin-top: 3mm; }}
"""


def stats_row() -> str:
    cells = [("counterparties", MEASURED["counterparties"]), ("embedded", MEASURED["embedded"]),
             ("evidence chunks", MEASURED["chunks"]), ("facts", MEASURED["facts"]),
             ("agent runs", MEASURED["runs"]), ("lifetime spend", MEASURED["spend"])]
    return '<div class="stats">' + "".join(
        f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for k, v in cells) + '</div>'


def main() -> None:
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>

<div class="page">
  <div class="kicker">As built · {AS_OF} · with the multi-region topology deployed</div>
  <h1>Respect the Funk — the system, as it actually works</h1>
  <p class="lede">Every number on these pages was read off {CLUSTER.split(' · ')[0]} ({VERSION}) on {AS_OF}.
  The three-region topology is drawn as deployed because that is what this document assumes; page 6 is the
  ledger of what is running right now and what is one command away. A diagram of a system that does not
  exist yet is a prospectus, and the honest way to publish one is to print the gap beside it.</p>
  {svg_topology()}
  {stats_row()}
</div>

<div class="page">
  <div class="kicker">Page 2 · residency</div>
  <h2>One table, three continents, enforced by the database</h2>
  <p class="lede">The agents email real people, so the system holds real personal data about them, and European
  law says where that data may live. <code>contact_route</code> is one logical table whose rows are domiciled
  individually — this is the capability no single Postgres has.</p>
  {svg_residency()}
</div>

<div class="page">
  <div class="kicker">Page 3 · retrieval</div>
  <h2>Filtered vector search, and what each index is actually worth</h2>
  <p class="lede">Two retrievals do real work and are measured here. A third has one row in it. A fourth was
  deleted rather than backfilled, because it indexed a column nothing wrote for a query nobody ran — and an
  index you removed is more honest than one that indexes nothing.</p>
  {svg_retrieval()}
</div>

<div class="page">
  <div class="kicker">Page 4 · coordination</div>
  <h2>The database is the runtime</h2>
  <p class="lede">Agents never call each other. A row changing is the scheduling primitive, and the fence that
  decides who may act on it is a capability token only the database mints.</p>
  {svg_fleet()}
</div>

<div class="page">
  <div class="kicker">Page 5 · accountability</div>
  <h2>What did it believe, when it acted?</h2>
  <p class="lede">The hardest question any agentic memory faces is not what the agent knows — every RAG system
  answers that. It is why it decided that, <em>then</em>.</p>
  {svg_timetravel()}
</div>

<div class="page">
  <div class="kicker">Page 6 · the ledger</div>
  <h2>Running, built, or blocked</h2>
  <p class="lede">The page that makes the rest of this document honest. Everything above is drawn as though
  deployed; this is what is true on {AS_OF}, and how each line was checked.</p>
  {svg_ledger()}
  <div class="note">Three of these are one action away and none has been taken: paste the changefeed statement
  into <code>cockroach sql</code>; run <code>docs/runbooks/multiregion.md</code> (~$0.65, three hours, throwaway
  cluster); request a free Podcast Index key. The fourth — Bedrock — is blocked at AWS and needs a support case.</div>
</div>

</body></html>"""
    HTML(string=html).write_pdf(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
